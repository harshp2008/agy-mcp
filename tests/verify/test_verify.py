"""Unit tests for verification allowlist, command execution, and fix prompts."""

from pathlib import Path

from agy_mcp.verify import format_fix_prompt, is_verify_allowed, run_verify

DEFAULT_ALLOWLIST = ["pytest", "ruff", "npm test", "cargo test", "go test"]


def test_is_verify_allowed_accepts_allowlisted_commands() -> None:
    assert is_verify_allowed("pytest", DEFAULT_ALLOWLIST)
    assert is_verify_allowed("pytest -v tests/test_core.py", DEFAULT_ALLOWLIST)
    assert is_verify_allowed("ruff check .", DEFAULT_ALLOWLIST)
    assert is_verify_allowed("npm test", DEFAULT_ALLOWLIST)
    assert is_verify_allowed("cargo test --release", DEFAULT_ALLOWLIST)
    assert is_verify_allowed("go test ./...", DEFAULT_ALLOWLIST)
    # Canonical interpreter form for the allowlisted pytest tool (smoke-fix item 3).
    assert is_verify_allowed("python -m pytest", DEFAULT_ALLOWLIST)
    assert is_verify_allowed("python3 -m pytest -q", DEFAULT_ALLOWLIST)


def test_is_verify_allowed_rejects_unlisted_and_malicious_commands() -> None:
    assert not is_verify_allowed("cat /etc/passwd", DEFAULT_ALLOWLIST)
    assert not is_verify_allowed("rm -rf /", DEFAULT_ALLOWLIST)
    assert not is_verify_allowed("bash -c 'pytest'", DEFAULT_ALLOWLIST)
    assert not is_verify_allowed("sh -c 'pytest'", DEFAULT_ALLOWLIST)
    assert not is_verify_allowed("python -m http.server", DEFAULT_ALLOWLIST)
    assert not is_verify_allowed("python -c 'print(1)'", DEFAULT_ALLOWLIST)
    assert not is_verify_allowed("npm test -- --watch=false", DEFAULT_ALLOWLIST)
    assert not is_verify_allowed("", DEFAULT_ALLOWLIST)
    assert not is_verify_allowed("   ", DEFAULT_ALLOWLIST)


def test_run_verify_clean_success(tmp_path: Path) -> None:
    # Create a small python script that exits 0
    script = tmp_path / "check.py"
    script.write_text("print('all tests passed')\n")

    rc, output = run_verify(
        command_str=f"python3 {script}",
        worktree_path=tmp_path,
        timeout_seconds=10,
    )
    assert rc == 0
    assert "all tests passed" in output


def test_run_verify_failure_output_capture(tmp_path: Path) -> None:
    script = tmp_path / "failing.py"
    script.write_text(
        "import sys\nsys.stderr.write('AssertionError: expected 1 got 2\\n')\nsys.exit(1)\n"
    )

    rc, output = run_verify(
        command_str=f"python3 {script}",
        worktree_path=tmp_path,
        timeout_seconds=10,
    )
    assert rc == 1
    assert "AssertionError: expected 1 got 2" in output


def test_run_verify_timeout_enforcement(tmp_path: Path) -> None:
    script = tmp_path / "hang.py"
    script.write_text("import time\ntime.sleep(10)\n")

    rc, output = run_verify(
        command_str=f"python3 {script}",
        worktree_path=tmp_path,
        timeout_seconds=1,
    )
    assert rc == 124
    assert "timed out after 1s" in output


def test_run_verify_output_tail_truncation(tmp_path: Path) -> None:
    script = tmp_path / "huge_output.py"
    # Output 30 KB of text
    script.write_text("import sys\nprint('A' * 30000)\nsys.exit(1)\n")

    rc, output = run_verify(
        command_str=f"python3 {script}",
        worktree_path=tmp_path,
        timeout_seconds=10,
    )
    assert rc == 1
    assert len(output.encode("utf-8")) <= 8192
    assert "[truncated ...]" in output


def test_format_fix_prompt_structure(tmp_path: Path) -> None:
    prompt = format_fix_prompt("Test failed: assert 1 == 2")
    assert "Verification failed with the following output:" in prompt
    assert "<untrusted_text" in prompt
    assert "Test failed: assert 1 == 2" in prompt
    assert "Change code, not tests, unless the test itself is wrong" in prompt
    assert "Please analyze the error, fix the issues" in prompt
    assert "You are an autonomous coding assistant" in prompt


def test_is_verify_allowed_interpreter_commands() -> None:
    """Validate allowlist matches interpreter commands per D53 and rejects arbitrary shell."""
    # 1. Config allowlist with token list and interpreter strings
    token_allowlist = [
        ["/home/user/venv/bin/python", "-m", "pytest"],
        "/home/user/venv/bin/python -m ruff",
        "pytest",
    ]
    assert is_verify_allowed("/home/user/venv/bin/python -m pytest", token_allowlist) is True
    assert (
        is_verify_allowed(
            "/home/user/venv/bin/python -m pytest tests/test_calc.py", token_allowlist
        )
        is True
    )
    assert is_verify_allowed("/home/user/venv/bin/python -m ruff check .", token_allowlist) is True
    assert is_verify_allowed("pytest -v", token_allowlist) is True

    # 2. Arbitrary shell strictly rejected
    assert is_verify_allowed("/bin/bash -c pytest", token_allowlist) is False
    assert is_verify_allowed("sh -c 'pytest'", token_allowlist) is False
    assert is_verify_allowed("bash -c 'echo evil'", token_allowlist) is False
    assert is_verify_allowed("zsh -c pytest", token_allowlist) is False

    # 3. Python -c or raw script rejected (arbitrary code execution)
    assert (
        is_verify_allowed(
            "/home/user/venv/bin/python -c 'import os; os.system(\"ls\")'", token_allowlist
        )
        is False
    )
    assert is_verify_allowed("/home/user/venv/bin/python script.py", token_allowlist) is False

    # 4. Non-allowlisted command or module rejected
    assert is_verify_allowed("/home/user/venv/bin/python -m evil_module", token_allowlist) is False
    assert is_verify_allowed("npm test", token_allowlist) is False


def test_verify_command_not_found(tmp_path: Path) -> None:
    """Verify that a non-existent verify command returns rc 127 without crashing."""
    rc, output = run_verify(
        command_str="nonexistent_binary_xyz_12345",
        worktree_path=tmp_path,
        timeout_seconds=5,
    )
    assert rc == 127
    assert "Executable not found" in output


def test_verify_output_ansi_and_binary_junk(tmp_path: Path) -> None:
    """Verify runner handles ANSI escape sequences and binary characters cleanly."""
    script = tmp_path / "junk_output.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.write('\\x1b[31mRed Error\\x1b[0m\\n')\n"
        "sys.stdout.write('Some binary bytes: \\x00\\x01\\x02\\xfe\\xff\\n')\n"
        "sys.exit(1)\n"
    )
    rc, output = run_verify(
        command_str=f"python3 {script}",
        worktree_path=tmp_path,
        timeout_seconds=5,
    )
    assert rc == 1
    assert "Red Error" in output

    prompt = format_fix_prompt(output)
    assert "<untrusted_text" in prompt
    assert "</untrusted_text" in prompt
    assert "Red Error" in prompt


def test_format_fix_prompt_failing_tests_and_guidance() -> None:
    """format_fix_prompt extracts failing test names and enforces prompt guidance per D53."""
    pytest_tail = (
        "============================= FAILURES =============================\n"
        "_____________________________ test_mul _____________________________\n"
        "def test_mul():\n"
        ">       assert mul(3, 4) == 12\n"
        "E       assert 7 == 12\n"
        "FAILED tests/test_calc.py::test_mul - assert 7 == 12\n"
        "FAILED tests/test_other.py::test_foo - assert False\n"
        "======================== 2 failed in 0.05s =========================\n"
    )
    prompt = format_fix_prompt(pytest_tail)
    assert "Failing tests: tests/test_calc.py::test_mul, tests/test_other.py::test_foo" in prompt
    expected_instruction = (
        "Change code, not tests, unless the test itself is wrong "
        "(and then say so in the commit message)."
    )
    assert expected_instruction in prompt
    assert "<untrusted_text" in prompt
    assert "</untrusted_text" in prompt


def test_prompt_guidance_exact_text() -> None:
    """Exact text requirement checks for agy_start docstring and preamble rule 6 per D53."""
    from agy_mcp.server import agy_start
    from agy_mcp.util import load_preamble

    expected_clause = (
        "the wrapper runs the verify command after your work and will send you the results; "
        "fix what fails"
    )

    # 1. agy_start docstring
    doc = agy_start.__doc__ or ""
    assert "agy must NOT run tests;" in doc
    assert expected_clause in doc.replace("\n", " ")

    # 2. Preamble rule 6
    preamble = load_preamble()
    assert "agy must NOT run tests;" in preamble
    assert expected_clause in preamble.replace("\n", " ")


def test_verify_process_group_kills_grandchild(tmp_path: Path, monkeypatch) -> None:
    """Verify that run_verify uses its own process group and kills grandchildren on timeout."""
    import os
    import time

    pids_file = tmp_path / "pids.txt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    fake_pytest = bin_dir / "pytest"
    # The script spawns a grandchild process and sleeps
    script_content = f"""#!/usr/bin/env python3
import os, sys, subprocess, time
from pathlib import Path

# Spawn background grandchild
grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(100)"])
pids_path = Path({repr(str(pids_file))})
pids_path.write_text(f"{{os.getpid()}}:{{grandchild.pid}}")

# Sleep until killed
time.sleep(100)
"""
    fake_pytest.write_text(script_content)
    fake_pytest.chmod(0o755)

    orig_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"{bin_dir}:{orig_path}")

    rc, output = run_verify(
        command_str="pytest",
        worktree_path=tmp_path,
        timeout_seconds=1,
    )

    assert rc == 124
    assert "timed out after 1s" in output

    assert pids_file.is_file(), "Fake pytest did not write PIDs"
    parent_pid_str, grandchild_pid_str = pids_file.read_text().strip().split(":")
    parent_pid = int(parent_pid_str)
    grandchild_pid = int(grandchild_pid_str)

    # Allow slight OS scheduling window for SIGTERM/SIGKILL reaping
    time.sleep(0.5)

    def is_pid_alive(pid: int) -> bool:
        if os.path.exists(f"/proc/{pid}"):
            try:
                # Check if it's a zombie or completely gone
                stat_content = Path(f"/proc/{pid}/stat").read_text()
                # Status state character is 3rd field
                state = stat_content.split()[2]
                return state != "Z"
            except Exception:
                return False
        return False

    assert not is_pid_alive(parent_pid), f"Parent process {parent_pid} still alive"
    assert not is_pid_alive(grandchild_pid), f"Grandchild process {grandchild_pid} still alive"


def test_verify_env_dontwritebytecode_and_pytest_addopts(tmp_path: Path) -> None:
    """Verify run sets PYTHONDONTWRITEBYTECODE=1 and PYTEST_ADDOPTS per Part A2."""
    script = tmp_path / "dump_env.py"
    script.write_text(
        "import os\n"
        "print('PYTHONDONTWRITEBYTECODE=' + os.environ.get('PYTHONDONTWRITEBYTECODE', 'missing'))\n"
        "print('PYTEST_ADDOPTS=' + os.environ.get('PYTEST_ADDOPTS', 'missing'))\n"
    )
    rc, output = run_verify(
        command_str=f"python3 {script}",
        worktree_path=tmp_path,
        timeout_seconds=5,
    )
    assert rc == 0
    assert "PYTHONDONTWRITEBYTECODE=1" in output
    assert "-p no:cacheprovider" in output


def test_verify_run_in_fake_repo_leaves_no_committed_artifacts(
    tmp_path: Path, safe_git_env
) -> None:
    """Verify run creating __pycache__ and .pytest_cache results in none committed per A5(a)."""
    from agy_mcp.gitops import run_git, safety_net_commit
    from tests.helpers.make_repo import make_repo

    repo = make_repo(tmp_path / "repo_verify_clean", env=safe_git_env)
    # Simulate verify running by creating test script and executing it, which generates artifacts
    test_file = repo / "test_sample.py"
    test_file.write_text("def test_ok(): assert 1 == 1\n")
    run_git(["add", "test_sample.py"], repo, env=safe_git_env)
    run_git(["commit", "-m", "Add sample test"], repo, env=safe_git_env)
    head_before = run_git(["rev-parse", "HEAD"], repo).stdout.strip()

    # Create __pycache__ and .pytest_cache as verify execution would
    cache_dir = repo / "__pycache__"
    cache_dir.mkdir(exist_ok=True)
    (cache_dir / "test_sample.cpython-312.pyc").write_bytes(b"\x00\x01\x02")
    pytest_cache = repo / ".pytest_cache"
    pytest_cache.mkdir(exist_ok=True)
    (pytest_cache / "nodeids").write_text("[]")

    # Safety-net commit after verify
    job_dir = tmp_path / "job_dir"
    job_dir.mkdir(parents=True, exist_ok=True)
    new_sha = safety_net_commit(repo, title="Verify Clean", audit_passed=True, job_dir=job_dir)
    # Since only verify junk was created, safety_net_commit must NOT commit anything
    assert new_sha is None
    head_after = run_git(["rev-parse", "HEAD"], repo).stdout.strip()
    assert head_before == head_after

