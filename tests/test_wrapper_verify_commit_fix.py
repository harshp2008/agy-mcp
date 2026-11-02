"""Regression tests for the wrapper verify/commit fixes (D80).

Covers the H27 failure history:
- wrapper-generated checkpoint/safety-net subjects always satisfy the 72-char
  ASCII rule and repository commit-msg hooks run instead of being bypassed;
- verify timeouts kill the whole process group and are distinguishable from a
  command that failed by itself;
- the result names the command, timeout and last 40 lines, and surfaces the
  pytest basename-collision diagnosis;
- checkpoint/verify needs_review paths carry one-line cause + next action.

All git work happens in throwaway repositories under ``tmp_path``; no real agy
job and no clipsync-lab worktree is touched.
"""

import os
import subprocess
import time
from pathlib import Path

import pytest

from agy_mcp.config import Config, ConfigError
from agy_mcp.gitops import (
    build_wrapper_commit_text,
    checkpoint_commit,
    commit_message_problem,
    commit_with_retry,
    run_git,
    safety_net_commit,
    sanitize_commit_subject,
)
from agy_mcp.jobs import get_job
from agy_mcp.manager import JobManager
from agy_mcp.models import JobState
from agy_mcp.runner import LocalProcessRunner
from agy_mcp.verify import (
    VerifyResult,
    build_verify_excerpt,
    diagnose_verify_output,
    run_verify,
)
from agy_mcp.watcher import validate_commit_message
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"


def _manager(tmp_path: Path, config: Config | None = None) -> tuple[JobManager, Path]:
    repo_dir = make_repo(tmp_path / "repo", initial_commit=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    manager = JobManager(
        config=config
        or Config(
            max_concurrent_jobs=2,
            daily_jobs_cap=5,
            verify_allowlist=["python3"],
        ),
        runner=LocalProcessRunner(executable=FAKE_AGY),
        base_state_dir=state_dir,
        home=tmp_path,
        settle_window_seconds=0.1,
        poll_interval_seconds=0.05,
    )
    return manager, repo_dir


def _pid_alive(pid: int) -> bool:
    """True only for a live (non-zombie) process."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    stat = Path(f"/proc/{pid}/stat")
    try:
        content = stat.read_text(encoding="utf-8")
    except OSError:
        return False
    idx = content.rfind(")")
    if idx != -1 and idx + 2 < len(content) and content[idx + 2] in ("Z", "X"):
        return False
    return True


def _subject(repo: Path, env: dict[str, str]) -> str:
    res = run_git(["show", "-s", "--format=%B", "HEAD"], repo, env=env, check=True)
    return res.stdout.splitlines()[0]


def _install_commit_msg_hook(repo: Path, script: str) -> Path:
    hook = repo / ".git" / "hooks" / "commit-msg"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(script, encoding="utf-8")
    hook.chmod(0o755)
    return hook


# ---------------------------------------------------------------------------
# 1. Wrapper-generated commit subjects always pass the rule.
# ---------------------------------------------------------------------------


def test_sanitize_commit_subject_is_always_safe(tmp_path: Path) -> None:
    """ASCII, one line, no trailing period, <= 72 chars, non-empty."""
    raw = "  Feat: add café support for the relay. \nSecond line dropped\t\x07\n"
    subject = sanitize_commit_subject(raw)
    assert subject == "Feat: add cafe support for the relay"
    assert subject.isascii()
    assert "\n" not in subject
    assert not subject.endswith(".")
    assert len(subject) <= 72

    long_subject = sanitize_commit_subject("x" * 200)
    assert len(long_subject) <= 72
    assert long_subject

    # Non-Latin-only input cannot be transliterated; the fallback is still valid.
    fallback = sanitize_commit_subject("日本語のコミット")
    assert fallback
    assert fallback.isascii()
    assert len(fallback) <= 72

    text = build_wrapper_commit_text(raw, "Changed files: a.py, b.py")
    assert commit_message_problem(text) is None
    msg_file = tmp_path / "msg.txt"
    msg_file.write_text(text, encoding="utf-8")
    valid, err = validate_commit_message(msg_file)
    assert valid, err


def test_checkpoint_subject_passes_watcher_validation(
    tmp_path: Path, safe_git_env: dict[str, str]
) -> None:
    """The real checkpoint commit subject is short, ASCII and validator-clean."""
    repo = make_repo(tmp_path / "repo", env=safe_git_env)
    (repo / "work.txt").write_text("dirty\n", encoding="utf-8")

    sha = checkpoint_commit(repo)
    assert sha is not None

    subject = _subject(repo, safe_git_env)
    assert subject.startswith("agy: checkpoint ")
    assert subject.isascii()
    assert "\n" not in subject
    assert not subject.endswith(".")
    assert len(subject) <= 72

    msg_file = tmp_path / "checkpoint_subject.txt"
    msg_file.write_text(subject + "\n", encoding="utf-8")
    valid, err = validate_commit_message(msg_file)
    assert valid, err


def test_safety_net_subject_is_sanitized(tmp_path: Path, safe_git_env: dict[str, str]) -> None:
    """A hostile title (long, non-ASCII, control chars, trailing period) cannot leak."""
    repo = make_repo(tmp_path / "repo", env=safe_git_env)
    (repo / "work.txt").write_text("dirty\n", encoding="utf-8")
    title = "Fix café relay " + "x" * 120 + "\x07."

    sha = safety_net_commit(repo, title=title, audit_passed=True)
    assert sha is not None
    subject = _subject(repo, safe_git_env)
    assert subject.isascii()
    assert len(subject) <= 72
    assert "\n" not in subject
    assert not subject.endswith(".")


def test_commit_msg_hook_runs_and_checkpoint_complies(
    tmp_path: Path, safe_git_env: dict[str, str]
) -> None:
    """A repo hook rejects the raw bad subject, then accepts the sanitized checkpoint."""
    repo = make_repo(tmp_path / "repo", env=safe_git_env)
    marker = repo / "hook-ran.txt"
    _install_commit_msg_hook(
        repo,
        "#!/bin/sh\n"
        "subject=$(head -n1 \"$1\")\n"
        # Reject trailing period, non-ASCII bytes, over-long subjects.
        "case \"$subject\" in *.) echo 'REJECT subject-full-stop' >&2; exit 1;; esac\n"
        "if printf '%s' \"$subject\" | LC_ALL=C grep -q '[^ -~]'; then\n"
        "  echo 'REJECT non-ascii subject' >&2; exit 1\n"
        "fi\n"
        "if [ \"${#subject}\" -gt 72 ]; then\n"
        "  echo 'REJECT subject too long' >&2; exit 1\n"
        "fi\n"
        f"echo \"$subject\" >> '{marker}'\n"
        "exit 0\n",
    )

    # Before: the raw subject a wrapper used to hand to git is rejected by the hook.
    (repo / "bad.txt").write_text("x\n", encoding="utf-8")
    run_git(["add", "bad.txt"], repo, env=safe_git_env, check=True)
    bad_msg = tmp_path / "bad_msg.txt"
    bad_msg.write_text("Feat: add café support.\n", encoding="utf-8")
    rejected = subprocess.run(
        ["git", "-C", str(repo), "commit", "-F", str(bad_msg)],
        env=safe_git_env,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode != 0
    assert "REJECT" in rejected.stderr

    # Clean the rejected attempt, then let the wrapper checkpoint commit.
    run_git(["reset"], repo, env=safe_git_env, check=True)
    (repo / "bad.txt").unlink()
    (repo / "good.txt").write_text("dirty\n", encoding="utf-8")
    sha = checkpoint_commit(repo)
    assert sha is not None, "checkpoint commit must succeed through the repo hook"
    assert marker.is_file(), "the commit-msg hook was not run (hooks bypassed)"

    subject = _subject(repo, safe_git_env)
    assert subject.isascii()
    assert len(subject) <= 72
    assert not subject.endswith(".")
    assert "hook-ran" not in subject


def test_commit_with_retry_rejects_when_hook_rejects(
    tmp_path: Path, safe_git_env: dict[str, str]
) -> None:
    """A failing hook surfaces as GitError instead of silently bypassing the hook."""
    from agy_mcp.gitops import GitError

    repo = make_repo(tmp_path / "repo", env=safe_git_env)
    marker = repo / "hook-ran.txt"
    _install_commit_msg_hook(
        repo,
        "#!/bin/sh\n"
        f"echo ran >> '{marker}'\n"
        "echo 'REJECT custom-policy' >&2\n"
        "exit 1\n",
    )
    (repo / "x.txt").write_text("x\n", encoding="utf-8")
    run_git(["add", "x.txt"], repo, env=safe_git_env, check=True)
    msg = repo / ".agy" / "MSG"
    msg.parent.mkdir(parents=True, exist_ok=True)
    msg.write_text("chore: valid message\n", encoding="utf-8")

    with pytest.raises(GitError) as excinfo:
        commit_with_retry(repo, msg)
    assert "REJECT custom-policy" in (excinfo.value.stderr or "")
    assert marker.is_file(), "hooks were bypassed instead of run"


# ---------------------------------------------------------------------------
# 2. Verify timeout: process-group kill and distinguishable status.
# ---------------------------------------------------------------------------


def test_run_verify_timeout_kills_process_group_and_reports_metadata(
    tmp_path: Path,
) -> None:
    """A hanging command and its child are both gone; timeout is not a failure."""
    pid_file = tmp_path / "child.pid"
    script = tmp_path / "hang_with_child.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "open(sys.argv[1], 'w').write(str(child.pid))\n"
        "print('spawned', child.pid, flush=True)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )

    result = run_verify(
        f"python3 {script} {pid_file}",
        tmp_path,
        timeout_seconds=1,
    )

    assert isinstance(result, VerifyResult)
    assert len(result) == 2  # old unpacking contract
    rc, output = result
    assert rc == 124
    assert result.timed_out is True
    assert result.killed_by_timeout is True
    assert result.status == "timeout"
    assert "Verification command timed out after 1s" in output
    assert "python3" in output and str(script) in output
    assert result.timeout_seconds == 1

    child_pid = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.time() + 5.0
    while time.time() < deadline and _pid_alive(child_pid):
        time.sleep(0.05)
    assert not _pid_alive(child_pid), f"orphan child {child_pid} survived the timeout"


def test_run_verify_failure_is_not_a_timeout(tmp_path: Path) -> None:
    """A command that exits by itself is reported as failed, not timed out."""
    script = tmp_path / "fail.py"
    script.write_text("print('boom')\nraise SystemExit(3)\n", encoding="utf-8")

    result = run_verify(f"python3 {script}", tmp_path, timeout_seconds=10)
    assert result.rc == 3
    assert result.timed_out is False
    assert result.killed_by_timeout is False
    assert result.status == "failed"
    assert "boom" in result.output


def test_verify_excerpt_surfaces_pytest_import_mismatch(tmp_path: Path) -> None:
    """The basename-collision diagnosis appears even when it is not in the tail."""
    script = tmp_path / "mismatch.py"
    script.write_text(
        "import sys\n"
        "print(\"ERROR collecting tests/acceptance/test_x.py\")\n"
        "print(\"import file mismatch: imported module 'test_x' has this __file__ \"\n"
        "      \"attribute: tests/test_x.py\")\n"
        "print('HINT: remove __pycache__ / .pyc files and/or use a unique basename')\n"
        "for i in range(100):\n"
        "    print(f'filler line {i}')\n"
        "sys.exit(2)\n",
        encoding="utf-8",
    )

    result = run_verify(f"python3 {script}", tmp_path, timeout_seconds=10)
    assert result.rc == 2
    excerpt = build_verify_excerpt(result)

    assert result.status == "failed"
    assert "import file mismatch" in excerpt
    assert "HINT: remove __pycache__" in excerpt
    assert "[wrapper] cause:" in excerpt
    assert "[wrapper] next:" in excerpt
    assert "filler line 99" in excerpt  # the actual tail is preserved
    cause, next_action = diagnose_verify_output(result.output)
    assert cause and "basename" in cause
    assert next_action and "__init__.py" in next_action


# ---------------------------------------------------------------------------
# 3. Per-job timeout, config defaults and envelope fields.
# ---------------------------------------------------------------------------


def test_config_verify_timeout_default_and_validation(tmp_path: Path) -> None:
    from agy_mcp.config import load_config

    assert Config().verify_timeout_seconds == 900
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("verify_timeout_seconds = 45\n", encoding="utf-8")
    assert load_config(cfg_file).verify_timeout_seconds == 45

    for bad in (0, 86401, "ten"):
        cfg_file.write_text(f"verify_timeout_seconds = {bad!r}\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(cfg_file)


def test_manager_verify_timeout_is_per_job_and_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-job timeout reaches run_verify; the envelope distinguishes timeout."""
    manager, repo_dir = _manager(tmp_path)
    captured: dict[str, object] = {}

    def fake_run_verify(command, worktree, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return VerifyResult(
            124,
            "collected 3 items\nDEADLOCK\n" + "\n".join(f"line {i}" for i in range(60)),
            command=command,
            timeout_seconds=int(kwargs.get("timeout_seconds", 900)),
            timed_out=True,
            killed_by_timeout=True,
            duration_seconds=3.0,
        )

    monkeypatch.setattr("agy_mcp.verify.run_verify", fake_run_verify)
    env = manager.start(
        prompt="Simple task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Per Job Verify Timeout",
        verify="python3 check.py",
        verify_timeout_seconds=3,
    )
    res = manager.wait(env.job_id, seconds=15)

    assert captured["timeout_seconds"] == 3
    assert captured["command"] == "python3 check.py"
    job = get_job(env.job_id, manager.state_dir)
    assert job.verify_timeout_seconds == 3
    assert job.review_reason == "verify_timeout"

    res_dict = res.to_dict()
    verify = res_dict["verify"]
    # Old fields remain.
    assert verify["requested"] is True
    assert verify["ran"] is True
    assert verify["passed"] is False
    assert verify["rc"] == 124
    # New fields distinguish a wrapper-timeout from a command failure.
    assert verify["status"] == "timeout"
    assert verify["timed_out"] is True
    assert verify["killed_by_timeout"] is True
    assert verify["command"] == "python3 check.py"
    assert verify["timeout_seconds"] == 3
    assert "timed out" in (verify["cause"] or "").lower()
    assert "line 59" in (verify["last_tail"] or "")
    assert "[wrapper] verify command: python3 check.py" in (verify["last_tail"] or "")

    assert res_dict["error"]["code"] == "verify_timeout"
    assert "Cause:" in res_dict["error"]["message"]
    assert "Next:" in res_dict["error"]["message"]
    assert "Verification tests timed out" in res_dict["next_actions"][0]
    assert "python3 check.py" in res_dict["next_actions"][0]


def test_manager_verify_timeout_default_is_900(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without an override the historical 900 s default is used and stored."""
    manager, repo_dir = _manager(tmp_path)
    captured: dict[str, object] = {}

    def fake_run_verify(command, worktree, **kwargs):
        captured.update(kwargs)

        return 0, "ok"

    monkeypatch.setattr("agy_mcp.verify.run_verify", fake_run_verify)
    env = manager.start(
        prompt="Simple task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Default Verify Timeout",
        verify="python3 check.py",
    )
    res = manager.wait(env.job_id, seconds=15)
    assert captured["timeout_seconds"] == 900
    assert get_job(env.job_id, manager.state_dir).verify_timeout_seconds == 900
    verify = res.to_dict()["verify"]
    assert verify["status"] == "passed"
    assert verify["timeout_seconds"] == 900


def test_manager_rejects_bad_verify_timeout(tmp_path: Path) -> None:
    manager, repo_dir = _manager(tmp_path)
    with pytest.raises(Exception) as excinfo:
        manager.start(
            prompt="Simple task",
            repo_path=str(repo_dir),
            title="Bad Timeout",
            verify_timeout_seconds=0,
        )
    assert getattr(excinfo.value, "code", "") == "invalid_verify_timeout"


# ---------------------------------------------------------------------------
# 4. Actionable needs_review text.
# ---------------------------------------------------------------------------


def test_invalid_commit_message_is_actionable(tmp_path: Path) -> None:
    """The watcher keeps the validator reason and a cause/next pair on the job."""
    manager, repo_dir = _manager(tmp_path)
    env = manager.start(
        prompt="Work on task __SCENARIO:hang_after_invalid_commit_msg__",
        repo_path=str(repo_dir),
        title="Actionable Invalid Commit Msg",
    )
    res = manager.wait(env.job_id, seconds=15)
    assert res.state == JobState.NEEDS_REVIEW

    job = get_job(env.job_id, manager.state_dir)
    assert job.review_reason == "invalid_commit_message"
    assert job.error, "invalid_commit_message must carry an actionable error"
    assert "NUL byte" in job.error["message"]
    assert job.error["message"].startswith("Cause:")
    assert "Next:" in job.error["message"]
    assert "72" in job.error["next_action"]


def test_hook_rejection_is_actionable(tmp_path: Path) -> None:
    """A hook that rejects the agent message ends needs_review with the hook text."""
    repo_dir = make_repo(tmp_path / "repo", initial_commit=True)
    _install_commit_msg_hook(
        repo_dir,
        "#!/bin/sh\n"
        "if grep -q 'src_feature' \"$1\"; then\n"
        "  echo 'REJECT subject policy: src_feature commits need a ticket id' >&2\n"
        "  exit 1\n"
        "fi\n"
        "exit 0\n",
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    manager = JobManager(
        config=Config(max_concurrent_jobs=2, daily_jobs_cap=5),
        runner=LocalProcessRunner(executable=FAKE_AGY),
        base_state_dir=state_dir,
        home=tmp_path,
        settle_window_seconds=0.1,
        poll_interval_seconds=0.05,
    )
    env = manager.start(
        prompt="Do work __SCENARIO:success_with_commits__",
        repo_path=str(repo_dir),
        title="Hook Rejects Commit",
    )
    res = manager.wait(env.job_id, seconds=15)
    assert res.state == JobState.NEEDS_REVIEW

    job = get_job(env.job_id, manager.state_dir)
    assert job.review_reason == "invalid_commit_message"
    assert job.error is not None
    assert "REJECT subject policy" in job.error["message"]
    assert job.error["message"].startswith("Cause:")
    assert "Next:" in job.error["message"]
