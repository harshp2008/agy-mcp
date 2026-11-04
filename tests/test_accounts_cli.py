"""Tests for the human account CLI (D117).

Hermetic: HOME, config and state are under tmp_path, and the only agy is a fake
script. No test uses a pty, sleeps, or touches ~/.gemini outside the fake HOME.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from agy_mcp import server
from agy_mcp.accounts import cli, config_edit
from agy_mcp.accounts.identity import email_sha256
from agy_mcp.states import JobState
from tests.helpers.account_cli import (
    FAKE_EMAIL,
    OTHER_EMAIL,
    make_job,
    tree_snapshot,
    write_config,
    write_fake_agy,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class CliEnv:
    """Temporary paths and a base config shared by one CLI test."""

    home: Path
    config: Path
    state: Path
    accounts: Path
    fake: Path
    work: Path

    def base_args(self) -> list[str]:
        return [
            "--config",
            str(self.config),
            "--state-dir",
            str(self.state),
            "--agy-binary",
            str(self.fake),
        ]


def _config_text(env: CliEnv, extra: str = "") -> str:
    return (
        "# keep this comment\n"
        "max_accounts = 5\n"
        f'allowed_workdir_roots = ["{env.work}"]\n'
        f"{extra}"
    )


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CliEnv:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gemini" / "config").mkdir(parents=True)
    (home / ".gemini" / "config" / "config.json").write_text(
        json.dumps({"permissions": {"allow": ["command(ls)"]}}), encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    config = tmp_path / "config" / "config.toml"
    accounts = tmp_path / "accounts"
    state = tmp_path / "state"
    work = tmp_path / "work"
    env = CliEnv(
        home=home,
        config=config,
        state=state,
        accounts=accounts,
        fake=tmp_path / "fake_agy",
        work=work,
    )
    write_config(config, _config_text(env))
    write_fake_agy(env.fake)
    return env


def _seams(
    *,
    email: str = FAKE_EMAIL,
    login=None,
    isatty: bool = True,
    input_value: str = "",
) -> tuple[cli.CliSeams, list[tuple[str, Path, str]]]:
    calls: list[tuple[str, Path, str]] = []

    def default_login(account, home: Path, binary: str) -> int:
        calls.append((account.name, home, binary))
        (home / ".fake_logged_in").write_text("1", encoding="utf-8")
        return 0

    def input_func(prompt: str) -> str:
        del prompt
        return input_value

    seams = cli.CliSeams(
        login_runner=login or default_login,
        isatty=lambda: isatty,
        prompt_email=lambda: email,
        input_func=input_func,
    )
    return seams, calls


def _add_block(env: CliEnv, name: str, *, enabled: bool = True, **kwargs) -> str:
    home = env.accounts / name
    return config_edit.render_account_block(
        name, str(home), enabled=enabled, **kwargs
    )


def _write_config_with_accounts(env: CliEnv, *blocks: str, extra: str = "") -> str:
    text = _config_text(env, extra)
    for block in blocks:
        text = config_edit.append_account_block(text, block)
    write_config(env.config, text)
    return text


def _make_account_home(env: CliEnv, name: str) -> Path:
    home = env.accounts / name
    home.mkdir(parents=True, mode=0o700)
    home.chmod(0o700)
    return home


def _output(capsys: pytest.CaptureFixture[str]) -> str:
    captured = capsys.readouterr()
    return captured.out + captured.err


def test_add_happy_path_provisions_isolated_account(
    cli_env: CliEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    env = cli_env
    seams, calls = _seams()
    rc = cli.main(
        [
            "add",
            "a",
            "--yes",
            *(env.base_args()),
            "--home",
            str(env.accounts / "a"),
            "--pool",
            "shared",
            "--max-concurrent",
            "1",
        ],
        seams=seams,
    )
    output = _output(capsys)

    assert rc == 0
    assert calls == [("a", env.accounts / "a", str(env.fake))]
    text = env.config.read_text(encoding="utf-8")
    assert "# keep this comment" in text
    assert "# agy-mcp:account a begin" in text
    assert f'expected_email_sha256 = "{email_sha256(FAKE_EMAIL)}"' in text
    assert 'pool = "shared"' in text
    assert "max_concurrent_jobs = 1" in text
    assert FAKE_EMAIL not in output
    assert "restart dsh: systemctl --user restart dsh.service" in output

    account_home = env.accounts / "a"
    assert account_home.stat().st_mode & 0o777 == 0o700
    policy = account_home / ".gemini" / "config" / "config.json"
    assert policy.stat().st_mode & 0o777 == 0o600
    assert policy.read_text(encoding="utf-8") == (
        env.home / ".gemini" / "config" / "config.json"
    ).read_text(encoding="utf-8")
    settings = account_home / ".gemini" / "antigravity-cli" / "settings.json"
    assert settings.stat().st_mode & 0o777 == 0o600
    assert json.loads(settings.read_text(encoding="utf-8")) == {
        "trustedWorkspaces": [str(env.work)]
    }
    assert (account_home / ".fake_logged_in").is_file()
    backups = list((env.config.parent).glob("config.toml.*.bak"))
    assert len(backups) == 1


def test_add_plan_is_the_default_and_writes_nothing(
    cli_env: CliEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    env = cli_env
    marker = env.accounts.parent / "probe-called"
    write_fake_agy(env.fake, invocation_marker=marker)
    before = env.config.read_text(encoding="utf-8")
    rc = cli.main(
        ["add", "a", *(env.base_args()), "--home", str(env.accounts / "a")],
        seams=_seams()[0],
    )
    output = _output(capsys)

    assert rc == 0
    assert "dry run" in output
    assert "# agy-mcp:account a begin" in output
    assert env.config.read_text(encoding="utf-8") == before
    assert not (env.accounts / "a").exists()
    assert not marker.exists()


def test_add_refuses_a_non_tty(
    cli_env: CliEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    env = cli_env
    before = env.config.read_text(encoding="utf-8")
    rc = cli.main(
        ["add", "a", "--yes", *(env.base_args()), "--home", str(env.accounts / "a")],
        seams=_seams(isatty=False)[0],
    )
    output = _output(capsys)

    assert rc == 3
    assert "account_cli_non_tty" in output
    assert env.config.read_text(encoding="utf-8") == before
    assert not (env.accounts / "a").exists()


def test_add_aborts_when_a_fresh_home_sees_quota(
    cli_env: CliEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    env = cli_env
    write_fake_agy(env.fake, leak=True)
    seams, calls = _seams()
    before = env.config.read_text(encoding="utf-8")
    rc = cli.main(
        ["add", "a", "--yes", *(env.base_args()), "--home", str(env.accounts / "a")],
        seams=seams,
    )
    output = _output(capsys)

    assert rc == 5
    assert "account_cli_isolation_leak" in output
    assert calls == []
    assert env.config.read_text(encoding="utf-8") == before
    assert (env.accounts / "a").is_dir()
    assert not (env.accounts / "a" / ".fake_logged_in").exists()


def test_add_with_a_wrong_expected_email_stores_nothing(
    cli_env: CliEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    env = cli_env
    before = env.config.read_text(encoding="utf-8")
    rc = cli.main(
        ["add", "a", "--yes", *(env.base_args()), "--home", str(env.accounts / "a")],
        seams=_seams(email=OTHER_EMAIL)[0],
    )
    output = _output(capsys)

    assert rc == 5
    assert "account_identity_mismatch" in output
    assert env.config.read_text(encoding="utf-8") == before
    assert "expected_email_sha256" not in env.config.read_text(encoding="utf-8")
    account_home = env.accounts / "a"
    for needle in (FAKE_EMAIL, OTHER_EMAIL, email_sha256(OTHER_EMAIL)):
        assert needle not in env.config.read_text(encoding="utf-8")
        for root in (env.home, account_home):
            for path in root.rglob("*"):
                if path.is_file():
                    assert needle not in path.read_text(errors="ignore")
        assert needle not in output


def test_add_is_idempotent(cli_env: CliEnv, capsys: pytest.CaptureFixture[str]) -> None:
    env = cli_env
    seams, calls = _seams()
    args = ["add", "a", "--yes", *(env.base_args()), "--home", str(env.accounts / "a")]
    assert cli.main(args, seams=seams) == 0
    capsys.readouterr()
    first_text = env.config.read_text(encoding="utf-8")
    backups = list(env.config.parent.glob("config.toml.*.bak"))
    assert len(backups) == 1

    assert cli.main(args, seams=seams) == 0
    second_output = _output(capsys)
    assert env.config.read_text(encoding="utf-8") == first_text
    assert env.config.read_text(encoding="utf-8").count("[[accounts]]") == 1
    assert len(list(env.config.parent.glob("config.toml.*.bak"))) == 1
    assert "already has this exact block" in second_output
    assert len(calls) == 2


def test_add_respects_max_accounts(
    cli_env: CliEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    env = cli_env
    write_config(env.config, _config_text(env).replace("max_accounts = 5", "max_accounts = 2"))
    seams, calls = _seams()
    assert (
        cli.main(
            ["add", "a", "--yes", *(env.base_args()), "--home", str(env.accounts / "a")],
            seams=seams,
        )
        == 0
    )
    capsys.readouterr()
    rc = cli.main(
        ["add", "b", "--yes", *(env.base_args()), "--home", str(env.accounts / "b")],
        seams=seams,
    )
    output = _output(capsys)
    assert rc == 2
    assert "account_limit_exceeded" in output
    assert len(calls) == 1
    assert 'name = "b"' not in env.config.read_text(encoding="utf-8")


def test_add_refuses_a_duplicate_hand_written_account(
    cli_env: CliEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    env = cli_env
    home = _make_account_home(env, "a")
    text = _config_text(env) + f'\n[[accounts]]\nname = "a"\nhome = "{home}"\n'
    write_config(env.config, text)
    seams, calls = _seams()
    rc = cli.main(
        ["add", "a", "--yes", *(env.base_args()), "--home", str(env.accounts / "a")],
        seams=seams,
    )
    output = _output(capsys)
    assert rc == 4
    assert "account_name_duplicate" in output
    assert calls == []
    assert env.config.read_text(encoding="utf-8") == text


def test_add_does_not_overwrite_existing_policy_or_settings(
    cli_env: CliEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    env = cli_env
    home = _make_account_home(env, "a")
    (home / ".gemini" / "config").mkdir(parents=True)
    policy = home / ".gemini" / "config" / "config.json"
    policy.write_text(
        (env.home / ".gemini" / "config" / "config.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    policy.chmod(0o600)
    settings = home / ".gemini" / "antigravity-cli" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"trustedWorkspaces": ["/custom"]}\n', encoding="utf-8")
    settings.chmod(0o600)

    rc = cli.main(
        ["add", "a", "--yes", *(env.base_args()), "--home", str(home)],
        seams=_seams()[0],
    )
    _output(capsys)
    assert rc == 0
    assert policy.read_text(encoding="utf-8") == (
        env.home / ".gemini" / "config" / "config.json"
    ).read_text(encoding="utf-8")
    assert policy.stat().st_mode & 0o777 == 0o600
    assert settings.read_text(encoding="utf-8") == '{"trustedWorkspaces": ["/custom"]}\n'
    assert settings.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("state", [JobState.QUEUED, JobState.RUNNING])
def test_remove_refuses_an_active_job(
    cli_env: CliEnv, capsys: pytest.CaptureFixture[str], state: JobState
) -> None:
    env = cli_env
    _make_account_home(env, "a")
    write_config(env.config, _config_text(env) + "\n" + _add_block(env, "a"))
    job = make_job(env.state, account="a", state=state, title="Running work")
    rc = cli.main(["remove", "a", "--yes", *(env.base_args())], seams=_seams()[0])
    output = _output(capsys)
    assert rc == 3
    assert "A task is currently running on this account" in output
    assert job.job_id in output
    assert "Running work" in output
    assert 'name = "a"' in env.config.read_text(encoding="utf-8")


def test_remove_paused_jobs_warns_and_needs_the_phrase(
    cli_env: CliEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    env = cli_env
    home = _make_account_home(env, "a")
    write_config(env.config, _config_text(env) + "\n" + _add_block(env, "a"))
    paused = make_job(
        env.state,
        account="a",
        state=JobState.NEEDS_REVIEW,
        title="Quota paused",
        job_id="job-quota",
        review_reason="quota_exhausted",
    )
    decision = make_job(
        env.state,
        account="a",
        state=JobState.NEEDS_DECISION,
        title="Needs answer",
        job_id="job-decision",
    )

    rc = cli.main(
        ["remove", "a", "--yes", *(env.base_args())],
        seams=_seams(input_value="wrong")[0],
    )
    output = _output(capsys)
    assert rc == 3
    assert "unresumable" in output
    assert paused.job_id in output and decision.job_id in output
    assert "account_cli_confirmation_required" in output
    assert 'name = "a"' in env.config.read_text(encoding="utf-8")

    rc = cli.main(
        ["remove", "a", "--yes", *(env.base_args())],
        seams=_seams(input_value="remove account a")[0],
    )
    _output(capsys)
    assert rc == 0
    assert 'name = "a"' not in env.config.read_text(encoding="utf-8")
    assert home.is_dir()


def test_remove_hand_written_block_keeps_every_other_byte(
    cli_env: CliEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    env = cli_env
    text = (
        "# top comment\n"
        "max_concurrent_jobs = 2\n"
        "\n"
        "[[accounts]]\n"
        'name = "a"\n'
        'home = "/tmp/a"\n'
        "\n"
        "# keep me\n"
        "[[accounts]]\n"
        'name = "b"\n'
        'home = "/tmp/b"\n'
        'pool = "shared"  # inline\n'
        "\n"
        "[default_account]\n"
        "enabled = true  # keep\n"
    )
    write_config(env.config, text)
    rc = cli.main(["remove", "b", "--yes", *(env.base_args())], seams=_seams()[0])
    _output(capsys)
    assert rc == 0
    assert env.config.read_text(encoding="utf-8") == (
        "# top comment\n"
        "max_concurrent_jobs = 2\n"
        "\n"
        "[[accounts]]\n"
        'name = "a"\n'
        'home = "/tmp/a"\n'
        "\n"
        "# keep me\n"
        "[default_account]\n"
        "enabled = true  # keep\n"
    )


def test_remove_refuses_an_ambiguous_marker_block(
    cli_env: CliEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    env = cli_env
    text = (
        "[[accounts]]\n"
        'name = "b"\n'
        'home = "/tmp/b"\n'
        "\n"
        "# agy-mcp:account b begin\n"
        "[[accounts]]\n"
        'name = "a"\n'
        'home = "/tmp/a"\n'
        "# agy-mcp:account b end\n"
    )
    write_config(env.config, text)
    rc = cli.main(["remove", "b", "--yes", *(env.base_args())], seams=_seams()[0])
    output = _output(capsys)
    assert rc == 3
    assert "account_cli_block_ambiguous" in output
    assert env.config.read_text(encoding="utf-8") == text


def test_remove_default_is_refused(
    cli_env: CliEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    env = cli_env
    before = env.config.read_text(encoding="utf-8")
    rc = cli.main(["remove", "default", "--yes", *(env.base_args())], seams=_seams()[0])
    output = _output(capsys)
    assert rc == 3
    assert "use `account disable default`" in output
    assert env.config.read_text(encoding="utf-8") == before


def test_delete_home_refuses_a_symlink(
    cli_env: CliEnv, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = cli_env
    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    (root / "a").symlink_to(target)
    write_config(
        env.config,
        _config_text(env) + f'\n[[accounts]]\nname = "a"\nhome = "{root / "a"}"\n',
    )
    rc = cli.main(
        [
            "remove",
            "a",
            "--yes",
            "--delete-home",
            "--accounts-root",
            str(root),
            *(env.base_args()),
        ],
        seams=_seams(input_value="delete account home a")[0],
    )
    output = _output(capsys)
    assert rc == 3
    assert "account_cli_delete_home_unsafe" in output
    assert (root / "a").is_symlink()
    assert target.is_dir()
    assert 'name = "a"' in env.config.read_text(encoding="utf-8")


def test_delete_home_refuses_outside_the_root(
    cli_env: CliEnv, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = cli_env
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    write_config(
        env.config,
        _config_text(env) + f'\n[[accounts]]\nname = "a"\nhome = "{outside}"\n',
    )
    rc = cli.main(
        [
            "remove",
            "a",
            "--yes",
            "--delete-home",
            "--accounts-root",
            str(root),
            *(env.base_args()),
        ],
        seams=_seams(input_value="delete account home a")[0],
    )
    output = _output(capsys)
    assert rc == 3
    assert "account_cli_delete_home_unsafe" in output
    assert outside.is_dir()
    assert 'name = "a"' in env.config.read_text(encoding="utf-8")


def test_delete_home_refuses_with_a_nonterminal_job(
    cli_env: CliEnv, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = cli_env
    root = tmp_path / "root"
    root.mkdir()
    home = root / "a"
    home.mkdir(mode=0o700)
    write_config(
        env.config,
        _config_text(env) + f'\n[[accounts]]\nname = "a"\nhome = "{home}"\n',
    )
    make_job(
        env.state,
        account="a",
        state=JobState.NEEDS_REVIEW,
        title="Paused work",
        job_id="job-paused",
    )
    rc = cli.main(
        [
            "remove",
            "a",
            "--yes",
            "--delete-home",
            "--accounts-root",
            str(root),
            *(env.base_args()),
        ],
        seams=_seams(input_value="remove account a")[0],
    )
    output = _output(capsys)
    assert rc == 3
    assert "account_cli_delete_home_unsafe" in output
    assert home.is_dir()
    assert 'name = "a"' in env.config.read_text(encoding="utf-8")


def test_delete_home_removes_only_the_resolved_home(
    cli_env: CliEnv, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = cli_env
    root = tmp_path / "root"
    root.mkdir()
    home = root / "a"
    home.mkdir(mode=0o700)
    write_config(
        env.config,
        _config_text(env) + f'\n[[accounts]]\nname = "a"\nhome = "{home}"\n',
    )
    rc = cli.main(
        [
            "remove",
            "a",
            "--yes",
            "--delete-home",
            "--accounts-root",
            str(root),
            *(env.base_args()),
        ],
        seams=_seams(input_value="delete account home a")[0],
    )
    output = _output(capsys)
    assert rc == 0
    assert not home.exists()
    assert root.is_dir()
    assert "Google login may still be valid server-side" in output
    assert 'name = "a"' not in env.config.read_text(encoding="utf-8")


def test_enable_disable_and_the_active_job_warning(
    cli_env: CliEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    env = cli_env
    _make_account_home(env, "a")
    write_config(env.config, _config_text(env) + "\n" + _add_block(env, "a"))
    before = env.config.read_text(encoding="utf-8")
    rc = cli.main(["disable", "a", *(env.base_args())], seams=_seams()[0])
    assert rc == 0
    assert "enabled = false" in _output(capsys)
    assert env.config.read_text(encoding="utf-8") == before

    make_job(env.state, account="a", state=JobState.RUNNING, title="Still running")
    rc = cli.main(["disable", "a", "--yes", *(env.base_args())], seams=_seams()[0])
    output = _output(capsys)
    assert rc == 0
    assert "will finish on this account" in output
    assert "enabled = false" in env.config.read_text(encoding="utf-8")
    assert "# keep this comment" in env.config.read_text(encoding="utf-8")
    backups_after_disable = len(list(env.config.parent.glob("config.toml.*.bak")))
    assert backups_after_disable == 1

    rc = cli.main(["enable", "a", "--yes", *(env.base_args())], seams=_seams()[0])
    assert rc == 0
    assert "enabled = true" in env.config.read_text(encoding="utf-8")
    capsys.readouterr()

    rc = cli.main(["enable", "a", "--yes", *(env.base_args())], seams=_seams()[0])
    assert rc == 0
    assert "already enabled" in _output(capsys)
    assert len(list(env.config.parent.glob("config.toml.*.bak"))) == backups_after_disable + 1


def test_enable_and_disable_the_implicit_default(
    cli_env: CliEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    env = cli_env
    rc = cli.main(["disable", "default", "--yes", *(env.base_args())], seams=_seams()[0])
    assert rc == 0
    text = env.config.read_text(encoding="utf-8")
    assert text.endswith("[default_account]\nenabled = false\n")
    capsys.readouterr()

    rc = cli.main(["enable", "default", "--yes", *(env.base_args())], seams=_seams()[0])
    assert rc == 0
    assert "enabled = true" in env.config.read_text(encoding="utf-8")


def test_list_offline_and_with_probe(
    cli_env: CliEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    env = cli_env
    home_a = _make_account_home(env, "a")
    home_b = _make_account_home(env, "b")
    home_b.chmod(0o755)
    for home in (env.home, home_a, home_b):
        (home / ".fake_logged_in").write_text("1", encoding="utf-8")
    text = _config_text(env)
    text += "\n" + config_edit.render_account_block(
        "a", str(home_a), enabled=True, expected_email_sha256="a" * 64
    )
    text += (
        "\n[[accounts]]\n"
        'name = "b"\n'
        f'home = "{home_b}"\n'
        "enabled = false\n"
        f'expected_email = "{FAKE_EMAIL}"\n'
    )
    write_config(env.config, text)
    make_job(env.state, account="a", state=JobState.QUEUED, title="Queued")
    make_job(
        env.state,
        account="a",
        state=JobState.NEEDS_REVIEW,
        title="Paused",
        job_id="job-paused",
    )
    make_job(env.state, account="a", state=JobState.DONE, title="Done", job_id="job-done")
    make_job(env.state, account="b", state=JobState.RUNNING, title="Running", job_id="job-b")

    marker = env.accounts.parent / "probe-called"
    write_fake_agy(env.fake, invocation_marker=marker)
    rc = cli.main(["list", *(env.base_args())], seams=_seams()[0])
    offline = _output(capsys)
    assert rc == 0
    assert "account a: enabled=yes" in offline
    assert "active=1 paused=1" in offline
    assert "account b: enabled=no" in offline
    assert "active=1 paused=0" in offline
    assert "mode 0700" in offline
    assert "mode 0755" in offline
    assert "sha256-present" in offline
    assert "plaintext-field(pla***" in offline
    assert FAKE_EMAIL not in offline
    assert not marker.exists()

    rc = cli.main(["list", "--probe", *(env.base_args())], seams=_seams()[0])
    probed = _output(capsys)
    assert rc == 0
    assert marker.exists()
    assert "probe: observed=" in probed
    assert "quota=Gemini Models weekly=80% 5h=90%" in probed
    assert FAKE_EMAIL not in probed


def test_list_and_remove_do_not_touch_homes(
    cli_env: CliEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    env = cli_env
    home = _make_account_home(env, "a")
    (home / ".gemini" / "config").mkdir(parents=True)
    (home / ".gemini" / "config" / "config.json").write_text("{}", encoding="utf-8")
    (home / ".fake_logged_in").write_text("1", encoding="utf-8")
    write_config(env.config, _config_text(env) + "\n" + _add_block(env, "a"))
    before = tree_snapshot(env.home, home)

    assert cli.main(["list", "--probe", *(env.base_args())], seams=_seams()[0]) == 0
    capsys.readouterr()
    assert tree_snapshot(env.home, home) == before

    assert cli.main(["remove", "a", "--yes", *(env.base_args())], seams=_seams()[0]) == 0
    capsys.readouterr()
    assert tree_snapshot(env.home, home) == before
    assert home.is_dir()


def test_planted_email_never_reaches_cli_output(
    cli_env: CliEnv, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = cli_env
    home = tmp_path / "homes" / FAKE_EMAIL / "b"
    home.mkdir(parents=True, mode=0o700)
    (home / ".fake_logged_in").write_text("1", encoding="utf-8")
    text = _config_text(env) + (
        "\n[[accounts]]\n"
        'name = "b"\n'
        f'home = "{home}"\n'
        f'expected_email = "{FAKE_EMAIL}"\n'
    )
    write_config(env.config, text)
    outputs: list[str] = []

    assert cli.main(["list", *(env.base_args())], seams=_seams()[0]) == 0
    outputs.append(_output(capsys))
    assert cli.main(["list", "--probe", *(env.base_args())], seams=_seams()[0]) == 0
    outputs.append(_output(capsys))
    assert cli.main(["remove", "b", *(env.base_args())], seams=_seams()[0]) == 0
    outputs.append(_output(capsys))
    assert cli.main(["disable", "b", *(env.base_args())], seams=_seams()[0]) == 0
    outputs.append(_output(capsys))
    assert (
        cli.main(
            ["add", "c", *(env.base_args()), "--home", str(env.accounts / "c")],
            seams=_seams()[0],
        )
        == 0
    )
    outputs.append(_output(capsys))

    for output in outputs:
        assert FAKE_EMAIL not in output


def test_python_dash_m_without_a_subcommand_starts_the_server(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    state = tmp_path / "state"
    fake = tmp_path / "fake_agy"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env.pop("AGY_MCP_STATE_DIR", None)
    env.pop("XDG_STATE_HOME", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agy_mcp",
            "--agy-binary",
            str(fake),
            "--state-dir",
            str(state),
            "--config",
            str(tmp_path / "missing.toml"),
        ],
        input=b"",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0
    log = state / "server.log"
    assert log.is_file()
    assert "Server exiting" in log.read_text(encoding="utf-8")


def test_server_main_routes_the_account_subcommand(
    cli_env: CliEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    env = cli_env
    with pytest.raises(SystemExit) as excinfo:
        server.main(["account", "list", *(env.base_args())])
    output = _output(capsys)
    assert excinfo.value.code == 0
    assert "account default:" in output
    assert not (env.state / "server.log").exists()
