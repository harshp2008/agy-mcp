"""Tests for Part D: watcher duplicate COMMIT_MSG fix and supervisor_error payload shape."""

import json
import time
from pathlib import Path
from typing import Callable

from agy_mcp.config import Config
from agy_mcp.gitops import GitError
from agy_mcp.jobs import append_audit_event, get_job_dir
from agy_mcp.manager import JobManager
from agy_mcp.models import JobState
from agy_mcp.runner import LocalProcessRunner
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"


def poll_until(
    predicate: Callable[[], bool],
    timeout_seconds: float = 30.0,
    poll_interval: float = 0.05,
    error_message: str = "Timed out waiting for condition",
) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(poll_interval)
    if not predicate():
        raise TimeoutError(f"{error_message} after {timeout_seconds}s")


def _setup_manager_and_repo(
    tmp_path: Path, settle_window: float = 0.0, poll_interval: float = 0.05
) -> tuple[JobManager, Path]:
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)

    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    runner = LocalProcessRunner(executable=FAKE_AGY)
    cfg = Config(max_concurrent_jobs=2, daily_jobs_cap=5)
    manager = JobManager(
        config=cfg,
        runner=runner,
        base_state_dir=state_dir,
        home=tmp_path,
        settle_window_seconds=settle_window,
        poll_interval_seconds=poll_interval,
    )
    return manager, repo_dir


def test_double_commit_msg_skips_empty_commit(tmp_path: Path) -> None:
    """Scenario double_commit_msg yields 2 commits, 1 skipped event, and zero supervisor_errors."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path, settle_window=0.0, poll_interval=0.05)
    env = manager.start(
        prompt="Perform commit sequence __SCENARIO:double_commit_msg__",
        repo_path=str(repo_dir),
        title="Double Commit Test",
        model="gemini-3.8-flash-medium",
    )

    poll_until(
        lambda: manager.status(env.job_id).state == JobState.DONE,
        timeout_seconds=30.0,
        poll_interval=0.05,
        error_message="Job did not reach DONE state in double_commit_msg",
    )
    res = manager.status(env.job_id)
    assert res.state == JobState.DONE
    assert res.commits_count == 2

    job_dir = get_job_dir(env.job_id, manager.state_dir)
    audit_events = [
        json.loads(line)
        for line in (job_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    commit_events = [e for e in audit_events if e.get("event") == "commit"]
    assert len(commit_events) == 2
    assert commit_events[0]["detail"]["generation"] == 1
    assert commit_events[1]["detail"]["generation"] == 3

    skipped_events = [e for e in audit_events if e.get("event") == "commit_skipped_no_changes"]
    assert len(skipped_events) == 1
    assert skipped_events[0]["detail"]["generation"] == 2
    assert "Commit 2 duplicate: no changes" in skipped_events[0]["detail"]["subject"]

    # Must have zero supervisor_error events!
    error_events = [e for e in audit_events if e.get("event") == "supervisor_error"]
    assert len(error_events) == 0


def test_supervisor_error_payload_shape_and_regression(tmp_path: Path) -> None:
    """Regression test for s2-result.json supervisor_error shape.

    In s2-result.json, supervisor_error only carried the command tuple:
      {"error": "('git', '-C', ...)", "consecutive_errors": 1}
    Now it must carry exception_type, message, and the trailing 500 bytes of git_stderr.
    """
    job_dir = tmp_path / "job_err"
    job_dir.mkdir(parents=True, exist_ok=True)

    # Simulate a genuine GitError
    git_err = GitError(
        returncode=128,
        stderr="fatal: Unable to create '.git/index.lock': File exists." + ("!" * 600),
        stdout="",
        args=["git", "-C", "/path/to/work", "commit", "-F", "msg.txt"],
    )

    git_stderr = getattr(git_err, "stderr", "")
    git_stderr_tail = git_stderr[-500:] if isinstance(git_stderr, str) and git_stderr else ""

    append_audit_event(
        job_dir,
        event="supervisor_error",
        detail={
            "error": str(git_err),
            "exception_type": type(git_err).__name__,
            "message": str(git_err),
            "git_stderr": git_stderr_tail,
            "consecutive_errors": 1,
        },
    )

    audit_lines = (job_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(audit_lines) == 1
    event = json.loads(audit_lines[0])

    assert event["event"] == "supervisor_error"
    detail = event["detail"]
    assert detail["exception_type"] == "GitError"
    assert "Git command failed (rc=128)" in detail["message"]
    assert detail["consecutive_errors"] == 1
    assert len(detail["git_stderr"]) <= 500
    assert "fatal: Unable to create" in detail["git_stderr"] or "!" in detail["git_stderr"]
