"""Tests for the standalone agy-mcp installer chapter.

Every test drives the real ``installer/install.sh`` through ``bash`` with a
temporary HOME, a fake ``agy`` and a stub ``systemctl`` on PATH.  The suite
never touches the real ``~/.dsh``, ``~/.config`` or ``~/.local``: all paths are
derived from the temporary HOME, and every DSH_* variable is removed from the
child environment (except in the explicit "inside dsh" refusal test).

The offline wheel below installs a tiny ``agy_mcp`` package with no runtime
dependencies.  ``AGY_MCP_PIP_ARGS="--no-deps --no-index"`` keeps every install
hermetic: no network, no build backend, no real agy.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "installer" / "install.sh"
AGENTS_MD = REPO_ROOT / "installer" / "agents_block.md"
TOOLS_SCHEMA = REPO_ROOT / "tests" / "fixtures" / "tools_schema.json"
MANAGER_PY = REPO_ROOT / "src" / "agy_mcp" / "manager.py"
PATCH_BEGIN = "# >>> ai-stack agymcp >>>"
PATCH_END = "# <<< ai-stack agymcp <<<"
AGENTS_BEGIN = "<!-- >>> ai-stack agymcp >>> -->"
AGENTS_END = "<!-- <<< ai-stack agymcp <<< -->"


def _sha256_b64(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _build_fake_wheel(
    destination: Path, version: str = "0.0.0", requires: tuple[str, ...] = ()
) -> None:
    """Write a wheel that provides an importable ``agy_mcp``.

    ``requires`` adds ``Requires-Dist`` lines so a test can prove that an
    upgrade re-resolves the package's dependencies instead of passing
    ``--no-deps``.
    """
    files = {
        "agy_mcp/__init__.py": f'__version__ = "{version}"\n',
        "agy_mcp/__main__.py": (
            "def main() -> int:\n"
            "    return 0\n"
            "\n"
            "\n"
            'if __name__ == "__main__":\n'
            "    raise SystemExit(main())\n"
        ),
    }
    dist_info = f"agy_mcp-{version}.dist-info"
    requires_lines = "".join(f"Requires-Dist: {req}\n" for req in requires)
    files[f"{dist_info}/METADATA"] = (
        f"Metadata-Version: 2.1\nName: agy-mcp\nVersion: {version}\n" + requires_lines
    )
    files[f"{dist_info}/WHEEL"] = (
        "Wheel-Version: 1.0\nGenerator: manual\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    )
    record: list[str] = []
    for name, content in files.items():
        data = content.encode("utf-8")
        record.append(f"{name},sha256={_sha256_b64(data)},{len(data)}")
    record.append(f"{dist_info}/RECORD,,")
    files[f"{dist_info}/RECORD"] = "\n".join(record) + "\n"

    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def _build_fake_dependency_wheel(destination: Path, version: str = "1.0") -> None:
    """Write a tiny dependency wheel named ``fakedep``."""
    files = {
        "fakedep/__init__.py": f'__version__ = "{version}"\n',
    }
    dist_info = f"fakedep-{version}.dist-info"
    files[f"{dist_info}/METADATA"] = (
        f"Metadata-Version: 2.1\nName: fakedep\nVersion: {version}\n"
    )
    files[f"{dist_info}/WHEEL"] = (
        "Wheel-Version: 1.0\nGenerator: manual\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    )
    record: list[str] = []
    for name, content in files.items():
        data = content.encode("utf-8")
        record.append(f"{name},sha256={_sha256_b64(data)},{len(data)}")
    record.append(f"{dist_info}/RECORD,,")
    files[f"{dist_info}/RECORD"] = "\n".join(record) + "\n"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)


@pytest.fixture(scope="session")
def fake_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the offline wheel once per test session."""
    wheel = tmp_path_factory.mktemp("wheel") / "agy_mcp-0.0.0-py3-none-any.whl"
    _build_fake_wheel(wheel)
    return wheel


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _installer_env(
    home: Path,
    tmp_path: Path,
    wheel: Path,
    *,
    with_agy: bool = True,
    dsh_session: bool = False,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build a hermetic child environment for one installer invocation."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    _write_executable(
        bin_dir / "agy",
        "#!/usr/bin/env bash\n"
        "echo 'fake agy must never be executed by the installer' >&2\n"
        "exit 91\n",
    )
    _write_executable(
        bin_dir / "systemctl",
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {str(home / 'systemctl.log')!r}\n"
        "exit 0\n",
    )

    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key.startswith("DSH_"):
            continue
        if key.startswith("AGY_MCP_"):
            continue
        if key in {
            "CONF_DIR",
            "PYTHONPATH",
            "VIRTUAL_ENV",
            "XDG_CONFIG_HOME",
            "XDG_STATE_HOME",
        }:
            continue
        env[key] = value
    if dsh_session:
        env["DSH_SESSION_ID"] = "installer-test-session"
    env["HOME"] = str(home)
    env["AGY_MCP_NO_RESTART"] = "1"
    env["AGY_MCP_PIP_ARGS"] = "--no-deps --no-index"
    env["AGY_MCP_SRC"] = str(wheel)
    candidates = [str(bin_dir)] if with_agy else []
    candidates.extend(["/usr/bin", "/bin"])
    env["PATH"] = ":".join(candidates)
    if extra:
        env.update(extra)
    return env


def _run_installer(
    home: Path,
    tmp_path: Path,
    wheel: Path,
    *args: str,
    with_agy: bool = True,
    dsh_session: bool = False,
    extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = _installer_env(
        home,
        tmp_path,
        wheel,
        with_agy=with_agy,
        dsh_session=dsh_session,
        extra=extra,
    )
    return subprocess.run(
        [str(INSTALLER), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _output(proc: subprocess.CompletedProcess[str]) -> str:
    return proc.stdout + proc.stderr


def _install_dir(home: Path) -> Path:
    return home / ".config" / "ai-stack" / "agymcp"


def _config_path(home: Path) -> Path:
    return home / ".config" / "agy-mcp" / "config.toml"


def _patch_path(home: Path) -> Path:
    return home / ".dsh" / "profiles" / "web" / "cordis.patch.yml"


def _agents_path(home: Path) -> Path:
    return home / ".dsh" / "AGENTS.md"


def _generated_text_files(home: Path) -> dict[str, bytes]:
    """Snapshot every non-venv generated file, keyed by path relative to HOME."""
    paths = [
        _install_dir(home) / "run.sh",
        _install_dir(home) / "env",
        _install_dir(home) / "mcp-client-config.json",
        _install_dir(home) / "orchestrator-guidance.md",
        _config_path(home),
        _patch_path(home),
        _agents_path(home),
    ]
    return {
        str(path.relative_to(home)): path.read_bytes()
        for path in paths
        if path.is_file()
    }


def _block_between(text: str, begin: str, end: str) -> str:
    lines = text.splitlines()
    start = lines.index(begin)
    stop = lines.index(end, start + 1)
    return "\n".join(lines[start + 1 : stop]) + "\n"


def test_fresh_dsh_install_writes_every_file(
    tmp_path: Path, fake_wheel: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    proc = _run_installer(home, tmp_path, fake_wheel, "--client", "dsh")
    assert proc.returncode == 0, _output(proc)

    install_dir = _install_dir(home)
    run_sh = install_dir / "run.sh"
    env_file = install_dir / "env"
    assert run_sh.is_file()
    assert run_sh.stat().st_mode & stat.S_IXUSR
    assert env_file.is_file()
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert (install_dir / "mcp-client-config.json").is_file()
    assert (install_dir / "orchestrator-guidance.md").is_file()
    assert _config_path(home).is_file()
    assert (home / ".local" / "state" / "agy-mcp").is_dir()

    patch_text = _patch_path(home).read_text(encoding="utf-8")
    assert PATCH_BEGIN in patch_text
    assert PATCH_END in patch_text
    assert "- id: mcp-agy" in patch_text
    assert "serverName: agy" in patch_text
    assert "toolCallTimeoutMs: 360000" in patch_text

    agents_text = _agents_path(home).read_text(encoding="utf-8")
    assert AGENTS_BEGIN in agents_text
    assert AGENTS_END in agents_text


def test_fresh_default_install_is_generic_and_touches_no_dsh(
    tmp_path: Path, fake_wheel: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    proc = _run_installer(home, tmp_path, fake_wheel)
    assert proc.returncode == 0, _output(proc)
    assert not (home / ".dsh").exists()

    install_dir = _install_dir(home)
    assert (install_dir / "run.sh").is_file()
    assert (install_dir / "mcp-client-config.json").is_file()
    assert (install_dir / "orchestrator-guidance.md").is_file()
    assert _config_path(home).is_file()
    assert "mcpServers" in proc.stdout
    assert "orchestrator-guidance.md" in proc.stdout
    assert "360000" in proc.stdout


@pytest.mark.parametrize("client", ["generic", "dsh"])
def test_second_install_is_byte_identical(
    tmp_path: Path, fake_wheel: Path, client: str
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    args = ("--client", client)
    first = _run_installer(home, tmp_path, fake_wheel, *args)
    assert first.returncode == 0, _output(first)
    before = _generated_text_files(home)

    second = _run_installer(home, tmp_path, fake_wheel, *args)
    assert second.returncode == 0, _output(second)
    after = _generated_text_files(home)

    assert sorted(before) == sorted(after)
    for name in before:
        assert before[name] == after[name], f"{name} changed on the second run"


def test_dsh_install_preserves_mcp_search_block_and_user_text(
    tmp_path: Path, fake_wheel: Path
) -> None:
    home = tmp_path / "home"
    patch = _patch_path(home)
    patch.parent.mkdir(parents=True)
    search_block = (
        "# >>> ai-stack searchmcp >>>\n"
        "- insert:\n"
        "    - id: mcp-search\n"
        "      name: '@deepseek-ai/dsh-mcp-client'\n"
        "      config:\n"
        "        serverName: search\n"
        "        transport: stdio\n"
        "        command: /opt/search/run.sh\n"
        "        toolCallTimeoutMs: 120000\n"
        "# <<< ai-stack searchmcp <<<\n"
    )
    patch.write_text("user_patch: keep-me\n" + search_block, encoding="utf-8")

    agents = _agents_path(home)
    agents.parent.mkdir(parents=True, exist_ok=True)
    user_text = "# my own instructions\n\nKeep this line.\n"
    search_guidance = (
        "<!-- >>> ai-stack search >>> -->\n"
        "search guidance stays\n"
        "<!-- <<< ai-stack search <<< -->\n"
    )
    agents.write_text(user_text + search_guidance + "tail stays\n", encoding="utf-8")

    proc = _run_installer(home, tmp_path, fake_wheel, "--client", "dsh")
    assert proc.returncode == 0, _output(proc)

    new_patch = patch.read_text(encoding="utf-8")
    assert "user_patch: keep-me" in new_patch
    assert search_block in new_patch
    assert PATCH_BEGIN in new_patch
    backup = _patch_path(home).with_suffix(".yml.bak")
    assert backup.is_file()
    assert backup.read_text(encoding="utf-8") == "user_patch: keep-me\n" + search_block

    new_agents = agents.read_text(encoding="utf-8")
    assert user_text in new_agents
    assert search_guidance in new_agents
    assert "tail stays" in new_agents
    assert AGENTS_BEGIN in new_agents


def test_flow_style_patch_is_refused_without_corruption(
    tmp_path: Path, fake_wheel: Path
) -> None:
    home = tmp_path / "home"
    patch = _patch_path(home)
    patch.parent.mkdir(parents=True)
    original = "insert: [{id: mcp-search, serverName: search}]\n"
    patch.write_text(original, encoding="utf-8")

    proc = _run_installer(home, tmp_path, fake_wheel, "--client", "dsh")
    assert proc.returncode != 0
    output = _output(proc)
    assert "flow style" in output
    assert PATCH_BEGIN in output
    assert patch.read_text(encoding="utf-8") == original
    assert not (_install_dir(home) / "run.sh").exists()


def test_agy_mcp_src_with_spaces_installs(
    tmp_path: Path, fake_wheel: Path
) -> None:
    spaced = tmp_path / "source tree with spaces"
    spaced.mkdir()
    wheel = spaced / fake_wheel.name
    shutil.copy2(fake_wheel, wheel)

    home = tmp_path / "home"
    home.mkdir()
    proc = _run_installer(
        home,
        tmp_path,
        wheel,
        extra={"AGY_MCP_SRC": str(wheel)},
    )
    assert proc.returncode == 0, _output(proc)
    assert (_install_dir(home) / "run.sh").is_file()


@pytest.mark.parametrize("client", ["generic", "dsh"])
def test_dry_run_changes_nothing(
    tmp_path: Path, fake_wheel: Path, client: str
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    before = sorted(str(path.relative_to(home)) for path in home.rglob("*"))
    proc = _run_installer(
        home, tmp_path, fake_wheel, "--client", client, "--dry-run"
    )
    assert proc.returncode == 0, _output(proc)
    after = sorted(str(path.relative_to(home)) for path in home.rglob("*"))
    assert before == after
    assert not (home / ".dsh").exists()
    assert not (home / ".config").exists()
    assert "would write" in proc.stdout
    assert "would run" in proc.stdout


@pytest.mark.parametrize("client", ["generic", "dsh"])
def test_uninstall_removes_only_its_own_files(
    tmp_path: Path, fake_wheel: Path, client: str
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    proc = _run_installer(home, tmp_path, fake_wheel, "--client", client)
    assert proc.returncode == 0, _output(proc)

    user_agents = "# user instructions, do not delete\n"
    user_patch = "user_patch: keep-me\n"
    if client == "dsh":
        # Prepend our own user text to the generated block, so uninstall must
        # remove only the marker block.
        patch_text = _patch_path(home).read_text(encoding="utf-8")
        _patch_path(home).write_text(user_patch + patch_text, encoding="utf-8")
        agents_text = _agents_path(home).read_text(encoding="utf-8")
        _agents_path(home).write_text(
            user_agents + agents_text, encoding="utf-8"
        )

    config_before = _config_path(home).read_bytes()
    proc = _run_installer(
        home, tmp_path, fake_wheel, "--client", client, "--uninstall"
    )
    assert proc.returncode == 0, _output(proc)

    assert not (_install_dir(home) / "run.sh").exists()
    assert not (_install_dir(home) / "env").exists()
    assert not (_install_dir(home) / "mcp-client-config.json").exists()
    assert not (_install_dir(home) / "orchestrator-guidance.md").exists()
    assert _config_path(home).read_bytes() == config_before
    assert (home / ".local" / "state" / "agy-mcp").is_dir()

    if client == "dsh":
        patch_text = _patch_path(home).read_text(encoding="utf-8")
        agents_text = _agents_path(home).read_text(encoding="utf-8")
        assert user_patch in patch_text
        assert PATCH_BEGIN not in patch_text
        assert PATCH_END not in patch_text
        assert user_agents in agents_text
        assert AGENTS_BEGIN not in agents_text
        assert AGENTS_END not in agents_text
        assert _patch_path(home).with_suffix(".yml.bak").is_file()
        assert _agents_path(home).with_suffix(".md.bak").is_file()
    else:
        assert not (home / ".dsh").exists()


def test_dsh_install_refuses_inside_dsh(
    tmp_path: Path, fake_wheel: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    proc = _run_installer(
        home, tmp_path, fake_wheel, "--client", "dsh", dsh_session=True
    )
    assert proc.returncode != 0
    assert "inside dsh" in _output(proc)
    assert not (home / ".dsh").exists()
    assert not (home / ".config").exists()


def test_unknown_client_fails_clearly(
    tmp_path: Path, fake_wheel: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    proc = _run_installer(home, tmp_path, fake_wheel, "--client", "vscode")
    assert proc.returncode != 0
    output = _output(proc)
    assert "unknown client" in output
    assert "dsh" in output
    assert "generic" in output
    assert not (home / ".config").exists()


def test_missing_agy_fails_clearly(tmp_path: Path, fake_wheel: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    proc = _run_installer(home, tmp_path, fake_wheel, with_agy=False)
    assert proc.returncode != 0
    assert "agy" in _output(proc)
    assert not (home / ".config").exists()


def test_existing_config_is_not_overwritten(
    tmp_path: Path, fake_wheel: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    config = _config_path(home)
    config.parent.mkdir(parents=True)
    original = '# user config\nusage_guard = "block"\n'
    config.write_text(original, encoding="utf-8")

    proc = _run_installer(home, tmp_path, fake_wheel)
    assert proc.returncode == 0, _output(proc)
    assert config.read_text(encoding="utf-8") == original
    output = _output(proc).lower()
    assert "not overwriting" in output
    assert "suggested" in output


def test_sourcing_defines_functions_and_runs_nothing(
    tmp_path: Path, fake_wheel: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env = _installer_env(home, tmp_path, fake_wheel)
    script = (
        'source "$1"\n'
        "for fn in ch_agymcp doctor_agymcp uninstall_agymcp; do\n"
        '  declare -F "$fn" >/dev/null || { echo "missing $fn"; exit 3; }\n'
        "done\n"
        "printf 'SOURCED_OK\\n'\n"
    )
    proc = subprocess.run(
        ["bash", "-c", script, "bash", str(INSTALLER)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, _output(proc)
    assert proc.stdout == "SOURCED_OK\n"
    assert proc.stderr == ""
    assert not (home / ".config").exists()
    assert not (home / ".dsh").exists()


def test_generic_json_is_valid_and_run_sh_is_executable(
    tmp_path: Path, fake_wheel: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    proc = _run_installer(home, tmp_path, fake_wheel)
    assert proc.returncode == 0, _output(proc)

    payload = json.loads(
        (_install_dir(home) / "mcp-client-config.json").read_text(encoding="utf-8")
    )
    server = payload["mcpServers"]["agy"]
    command = Path(server["command"])
    assert command.is_file()
    assert os.access(command, os.X_OK)
    # B4: only the standard mcpServers keys belong in the generated JSON; the
    # 360000 ms requirement is a printed note (and documented), not a key.
    assert set(server) == {"command", "args", "env"}
    assert server["args"] == []
    assert server["env"] == {}
    assert "360000" in proc.stdout


def test_doctor_reports_healthy_install(
    tmp_path: Path, fake_wheel: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    proc = _run_installer(home, tmp_path, fake_wheel, "--client", "dsh")
    assert proc.returncode == 0, _output(proc)

    doctor = _run_installer(
        home, tmp_path, fake_wheel, "--client", "dsh", "--doctor"
    )
    assert doctor.returncode == 0, _output(doctor)
    output = doctor.stdout
    assert "run.sh" in output
    assert "imports agy_mcp" in output
    assert "agy found" in output
    assert "patch block present" in output
    assert "AGENTS.md block present" in output


# ---------------------------------------------------------------------------
# ITEM 2: orchestrator instructions
# ---------------------------------------------------------------------------


def test_agents_block_file_exists_and_is_short() -> None:
    assert AGENTS_MD.is_file(), f"missing {AGENTS_MD}"
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert len(text.splitlines()) < 120


def test_agents_block_names_the_four_workflows() -> None:
    """The orchestrator must be able to pick a workflow from the block alone."""
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert "## Choosing a workflow" in text
    for name in ("GATED", "MICRO", "RESEARCH", "DIRECT"):
        assert f"- {name} (" in text, f"workflow {name} is not named in the block"
    assert "docs/orchestrator/PLAN.md" in text
    assert "first line names the chosen workflow and why" in text


def test_agents_block_protects_acceptance_tests() -> None:
    """The gated workflow only works if the job cannot edit the gate."""
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert "tests/acceptance/" in text
    assert "never modify tests/acceptance" in text
    assert "git diff <base> -- tests/acceptance" in text
    assert "never trust the job's claim of success" in text


def test_agents_block_states_the_commit_subject_limit() -> None:
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert "commit subject <= 72 characters" in text
    assert "3-10 step checklist" in text


def test_agents_block_fits_the_workflow_budget() -> None:
    """The block is replayed on every step, so it has a hard size cap."""
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 62, "the orchestrator block grew past 62 lines"


def test_agents_block_matches_dsh_install(
    tmp_path: Path, fake_wheel: Path
) -> None:
    expected = AGENTS_MD.read_text(encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    proc = _run_installer(home, tmp_path, fake_wheel, "--client", "dsh")
    assert proc.returncode == 0, _output(proc)

    agents_text = _agents_path(home).read_text(encoding="utf-8")
    generated = _block_between(agents_text, AGENTS_BEGIN, AGENTS_END)
    assert generated == expected


def test_agents_block_matches_generic_install(
    tmp_path: Path, fake_wheel: Path
) -> None:
    expected = AGENTS_MD.read_text(encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    proc = _run_installer(home, tmp_path, fake_wheel)
    assert proc.returncode == 0, _output(proc)
    generated = (_install_dir(home) / "orchestrator-guidance.md").read_text(
        encoding="utf-8"
    )
    assert generated == expected


def test_agents_block_mentions_only_real_tools() -> None:
    text = AGENTS_MD.read_text(encoding="utf-8")
    schema = json.loads(TOOLS_SCHEMA.read_text(encoding="utf-8"))
    real_tools = {tool["name"] for tool in schema}
    mentioned = set(re.findall(r"\bagy_[a-z_]+\b", text))
    assert mentioned, "the block mentions no agy_* tool names"
    assert mentioned <= real_tools, (
        f"unknown tool names in agents_block.md: {sorted(mentioned - real_tools)}"
    )


def test_agents_block_mentions_real_job_finalizing_code() -> None:
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert "job_finalizing" in text
    assert "job_finalizing" in MANAGER_PY.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# ITEM B1: upgrade on rerun (--upgrade / AGY_MCP_UPGRADE=1)
# ---------------------------------------------------------------------------


def _venv_python(home: Path) -> Path:
    return _install_dir(home) / "venv" / "bin" / "python"


def _site_packages(home: Path) -> Path:
    matches = sorted((_install_dir(home) / "venv" / "lib").glob("python*/site-packages"))
    assert matches, "the fake venv has no site-packages directory"
    return matches[0]


def _write_editable_marker(home: Path) -> Path:
    """Write the PEP 610 marker pip leaves for a PEP 660 editable install."""
    dist_info = next(_site_packages(home).glob("agy_mcp-*.dist-info"), None)
    assert dist_info is not None, "installed agy_mcp dist-info not found"
    marker = dist_info / "direct_url.json"
    marker.write_text(
        '{"url": "file:///tmp/agy-mcp-src", "dir_info": {"editable": true}}',
        encoding="utf-8",
    )
    return marker


def _module_importable(home: Path, module: str) -> bool:
    proc = subprocess.run(
        [str(_venv_python(home)), "-c", f"import {module}"],
        env={"PATH": "/usr/bin:/bin", "HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc.returncode == 0


def _installed_agy_mcp_version(home: Path) -> str:
    proc = subprocess.run(
        [
            str(_venv_python(home)),
            "-c",
            "import agy_mcp; print(agy_mcp.__version__)",
        ],
        env={"PATH": "/usr/bin:/bin", "HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout.strip()


def test_rerun_prints_upgrade_hint(tmp_path: Path, fake_wheel: Path) -> None:
    """A second run must say how to reinstall instead of silently skipping pip."""
    home = tmp_path / "home"
    home.mkdir()
    first = _run_installer(home, tmp_path, fake_wheel)
    assert first.returncode == 0, _output(first)

    second = _run_installer(home, tmp_path, fake_wheel)
    assert second.returncode == 0, _output(second)
    output = _output(second)
    assert "agy-mcp already installed" in output
    assert "rerun with --upgrade to reinstall from" in output
    assert str(fake_wheel) in output


def test_upgrade_reinstalls_the_package_from_source(
    tmp_path: Path, fake_wheel: Path
) -> None:
    """--upgrade must really replace the installed package with AGY_MCP_SRC."""
    home = tmp_path / "home"
    home.mkdir()
    first = _run_installer(home, tmp_path, fake_wheel)
    assert first.returncode == 0, _output(first)
    assert _installed_agy_mcp_version(home) == "0.0.0"

    newer = tmp_path / "agy_mcp-0.0.1-py3-none-any.whl"
    _build_fake_wheel(newer, version="0.0.1")
    proc = _run_installer(
        home,
        tmp_path,
        newer,
        "--upgrade",
        extra={"AGY_MCP_SRC": str(newer)},
    )
    assert proc.returncode == 0, _output(proc)
    assert "reinstalling agy-mcp from" in _output(proc)
    assert _installed_agy_mcp_version(home) == "0.0.1"


def test_upgrade_does_not_copify_an_editable_install(
    tmp_path: Path, fake_wheel: Path
) -> None:
    """--upgrade on an editable install must not turn it into a normal copy."""
    home = tmp_path / "home"
    home.mkdir()
    first = _run_installer(home, tmp_path, fake_wheel)
    assert first.returncode == 0, _output(first)
    marker = _write_editable_marker(home)

    newer = tmp_path / "agy_mcp-0.0.1-py3-none-any.whl"
    _build_fake_wheel(newer, version="0.0.1")
    proc = _run_installer(
        home,
        tmp_path,
        newer,
        "--upgrade",
        extra={"AGY_MCP_SRC": str(newer)},
    )
    output = _output(proc)
    assert proc.returncode == 0, output
    assert "editable install is already live; nothing to reinstall" in output
    assert "reinstalling agy-mcp from" not in output
    assert _installed_agy_mcp_version(home) == "0.0.0"
    assert marker.is_file()
    assert '"editable": true' in marker.read_text(encoding="utf-8")


def test_upgrade_with_agymcp_editable_reinstalls_editable(
    tmp_path: Path, fake_wheel: Path
) -> None:
    """AGY_MCP_EDITABLE=1 forces an editable reinstall instead of the live note."""
    home = tmp_path / "home"
    home.mkdir()
    first = _run_installer(home, tmp_path, fake_wheel)
    assert first.returncode == 0, _output(first)
    _write_editable_marker(home)

    proc = _run_installer(
        home,
        tmp_path,
        fake_wheel,
        "--upgrade",
        "--dry-run",
        extra={"AGY_MCP_EDITABLE": "1"},
    )
    output = _output(proc)
    assert proc.returncode == 0, output
    assert "editable install is already live" not in output
    pip_lines = [line for line in output.splitlines() if "[dry-run] would run:" in line
                 and "pip install" in line]
    assert pip_lines, output
    command = pip_lines[0]
    assert " -e " in command, command
    assert "--force-reinstall" not in command, command
    assert str(fake_wheel) in command


def test_upgrade_resolves_the_new_dependency_list(
    tmp_path: Path, fake_wheel: Path
) -> None:
    """A normal --upgrade must let pip resolve the package's dependencies."""
    home = tmp_path / "home"
    home.mkdir()
    first = _run_installer(home, tmp_path, fake_wheel)
    assert first.returncode == 0, _output(first)
    assert _module_importable(home, "fakedep") is False

    dep_dir = tmp_path / "deps"
    _build_fake_dependency_wheel(dep_dir / "fakedep-1.0-py3-none-any.whl")
    newer = tmp_path / "agy_mcp-0.0.2-py3-none-any.whl"
    _build_fake_wheel(newer, version="0.0.2", requires=("fakedep==1.0",))

    proc = _run_installer(
        home,
        tmp_path,
        newer,
        "--upgrade",
        extra={
            "AGY_MCP_SRC": str(newer),
            "AGY_MCP_PIP_ARGS": f"--no-index --find-links {dep_dir}",
        },
    )
    output = _output(proc)
    assert proc.returncode == 0, output
    assert _installed_agy_mcp_version(home) == "0.0.2"
    assert _module_importable(home, "fakedep") is True, output


def test_upgrade_is_documented_in_help_and_dry_run(
    tmp_path: Path, fake_wheel: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    help_proc = _run_installer(home, tmp_path, fake_wheel, "--help")
    assert help_proc.returncode == 0, _output(help_proc)
    assert "--upgrade" in help_proc.stdout

    dry = _run_installer(home, tmp_path, fake_wheel, "--upgrade", "--dry-run")
    assert dry.returncode == 0, _output(dry)
    output = _output(dry)
    assert "--upgrade" in output
    assert "--force-reinstall" in output
    assert "--no-deps" in output


# ---------------------------------------------------------------------------
# ITEM B2: uninstall safety
# ---------------------------------------------------------------------------


def test_uninstall_without_client_removes_dsh_blocks(
    tmp_path: Path, fake_wheel: Path
) -> None:
    """A bare --uninstall must not leave dsh blocks pointing at a deleted run.sh."""
    home = tmp_path / "home"
    home.mkdir()
    proc = _run_installer(home, tmp_path, fake_wheel, "--client", "dsh")
    assert proc.returncode == 0, _output(proc)

    proc = _run_installer(home, tmp_path, fake_wheel, "--uninstall")
    assert proc.returncode == 0, _output(proc)
    output = _output(proc)

    patch_text = _patch_path(home).read_text(encoding="utf-8")
    agents_text = _agents_path(home).read_text(encoding="utf-8")
    assert PATCH_BEGIN not in patch_text
    assert PATCH_END not in patch_text
    assert AGENTS_BEGIN not in agents_text
    assert AGENTS_END not in agents_text
    assert f"removed the agy-mcp block from {_patch_path(home)}" in output
    assert f"removed the agy-mcp block from {_agents_path(home)}" in output
    assert "restart dsh from a normal terminal to unload the server" in output
    assert not (_install_dir(home) / "run.sh").exists()


def test_uninstall_generic_keeps_dsh_blocks_but_warns(
    tmp_path: Path, fake_wheel: Path
) -> None:
    """Explicit --client generic must not touch dsh, and must warn about the now-dead block."""
    home = tmp_path / "home"
    home.mkdir()
    proc = _run_installer(home, tmp_path, fake_wheel, "--client", "dsh")
    assert proc.returncode == 0, _output(proc)

    proc = _run_installer(home, tmp_path, fake_wheel, "--client", "generic", "--uninstall")
    assert proc.returncode == 0, _output(proc)
    output = _output(proc)

    assert PATCH_BEGIN in _patch_path(home).read_text(encoding="utf-8")
    assert AGENTS_BEGIN in _agents_path(home).read_text(encoding="utf-8")
    assert "still register the agy-mcp server" in output
    assert "point at a missing run.sh" in output
    assert "restart dsh from a normal terminal" in output
    assert "removed the agy-mcp block" not in output
    assert not (_install_dir(home) / "run.sh").exists()


def test_uninstall_dsh_reports_removed_blocks_and_restart_hint(
    tmp_path: Path, fake_wheel: Path
) -> None:
    """Explicit --client dsh removes both blocks and prints what it removed."""
    home = tmp_path / "home"
    home.mkdir()
    proc = _run_installer(home, tmp_path, fake_wheel, "--client", "dsh")
    assert proc.returncode == 0, _output(proc)

    proc = _run_installer(home, tmp_path, fake_wheel, "--client", "dsh", "--uninstall")
    assert proc.returncode == 0, _output(proc)
    output = _output(proc)
    assert f"removed the agy-mcp block from {_patch_path(home)}" in output
    assert f"removed the agy-mcp block from {_agents_path(home)}" in output
    assert "restart dsh from a normal terminal to unload the server" in output


def test_uninstall_flow_style_patch_prints_no_restart_hint(
    tmp_path: Path, fake_wheel: Path
) -> None:
    """Nothing removed on a flow-style patch must not claim a restart is needed."""
    home = tmp_path / "home"
    home.mkdir()
    patch = _patch_path(home)
    patch.parent.mkdir(parents=True)
    patch.write_text(
        "user_style: [a, b]\n"
        + PATCH_BEGIN
        + "\n"
        + "- insert:\n"
        + "    - id: mcp-agy\n"
        + PATCH_END
        + "\n",
        encoding="utf-8",
    )

    proc = _run_installer(home, tmp_path, fake_wheel, "--client", "dsh", "--uninstall")
    output = _output(proc)
    assert proc.returncode == 0, output
    assert "it uses flow style" in output
    assert "restart dsh from a normal terminal to unload the server" not in output
    assert "removed the agy-mcp block" not in output
    assert PATCH_BEGIN in patch.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# ITEM B (this round): integration-branch rule, no force cleanup, no model
# ---------------------------------------------------------------------------


def test_agents_block_states_the_integration_branch_rule() -> None:
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert "dedicated integration branch" in text
    assert "integration/<topic>" in text
    assert "Never merge, fast-forward or push into main/master" in text
    assert 'merge integration/<topic> into main when ready' in text
    assert "commit docs/orchestrator/ on the integration branch, not on main" in text


def test_agents_block_never_uses_force_cleanup() -> None:
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert "Never pass force=true to agy_cleanup" in text
    assert "remove the worktree only (delete_branch false)" in text
    assert "NEEDS_HUMAN" in text


def test_agents_block_does_not_offer_main_as_a_merge_target() -> None:
    """Every line naming main must say never/not: main is the human's call."""
    text = AGENTS_MD.read_text(encoding="utf-8")
    for line in text.splitlines():
        if re.search(r"\bmain\b", line):
            assert re.search(r"\b(never|not)\b", line, re.IGNORECASE), (
                f"the guidance offers main as a target: {line!r}"
            )


def test_agents_block_uses_the_server_default_model() -> None:
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert "Do not pass the model parameter unless the human named one" in text
    assert "use the server default" in text


# ---------------------------------------------------------------------------
# ITEM B3: flow-style guard ignores comments and the installer's own block
# ---------------------------------------------------------------------------


def test_comment_with_brackets_does_not_block_install(
    tmp_path: Path, fake_wheel: Path
) -> None:
    """A '#' comment containing [x] is not flow style and must not stop the install."""
    home = tmp_path / "home"
    patch = _patch_path(home)
    patch.parent.mkdir(parents=True)
    comment = "# keep this list [x] intact; it is a comment, not YAML flow style\n"
    patch.write_text(comment + "user_patch: keep-me\n", encoding="utf-8")

    proc = _run_installer(home, tmp_path, fake_wheel, "--client", "dsh")
    assert proc.returncode == 0, _output(proc)
    text = patch.read_text(encoding="utf-8")
    assert comment in text
    assert "user_patch: keep-me" in text
    assert PATCH_BEGIN in text


def test_brackets_inside_the_installer_block_do_not_block_a_rerun(
    tmp_path: Path, fake_wheel: Path
) -> None:
    """The installer's own marker block is ignored by the flow-style guard."""
    home = tmp_path / "home"
    patch = _patch_path(home)
    patch.parent.mkdir(parents=True)
    patch.write_text(
        "- insert:\n"
        "    - id: mcp-search\n"
        f"{PATCH_BEGIN}\n"
        "- insert: [{id: mcp-agy, serverName: agy}]\n"
        f"{PATCH_END}\n",
        encoding="utf-8",
    )

    proc = _run_installer(home, tmp_path, fake_wheel, "--client", "dsh")
    assert proc.returncode == 0, _output(proc)
    text = patch.read_text(encoding="utf-8")
    assert "- id: mcp-search" in text
    assert "serverName: agy" in text
    assert "[{id: mcp-agy" not in text


def test_real_flow_style_still_refuses_and_reports_the_line(
    tmp_path: Path, fake_wheel: Path
) -> None:
    """A real 'key: [a, b]' line must still be refused and named in the error."""
    home = tmp_path / "home"
    patch = _patch_path(home)
    patch.parent.mkdir(parents=True)
    original = "# a comment [ok]\nuser_patch: [one, two]\n"
    patch.write_text(original, encoding="utf-8")

    proc = _run_installer(home, tmp_path, fake_wheel, "--client", "dsh")
    assert proc.returncode != 0
    output = _output(proc)
    assert "flow style" in output
    assert "user_patch: [one, two]" in output
    assert patch.read_text(encoding="utf-8") == original
    assert not (_install_dir(home) / "run.sh").exists()
