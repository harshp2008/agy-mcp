"""Tests for F17: verify tail in audit log must be redacted and preserved in verify_tail.txt."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from agy_mcp.audit import save_snapshot, take_snapshot
from agy_mcp.jobs import JobState, create_job, get_job_dir, transition_job_state
from agy_mcp.runner import ProcessHandle
from agy_mcp.supervisor import JobSupervisor
from agy_mcp.worktree import create_worktree
from tests.helpers.make_repo import make_repo


def test_verify_tail_in_audit_is_redacted(tmp_path: Path) -> None:
    """When verify fails, audit.jsonl must record redacted verify_tail without leaking secrets."""
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    job = create_job(
        repo_path=repo_dir,
        title="F17 Verify Test",
        prompt="hello",
        model="gemini-3.8-flash-medium",
        max_minutes=10,
        verify="pytest",
        base_state_dir=state_dir,
    )

    runner = MagicMock()
    runner.poll.return_value = 0
    job_dir = get_job_dir(job.job_id, state_dir)
    stdout_path = job_dir / "stdout.txt"
    stderr_path = job_dir / "stderr.txt"
    stdout_path.write_text(json.dumps({"status": "SUCCESS", "response": "ok"}))
    stderr_path.touch()
    handle = ProcessHandle(
        pid=1234,
        pgid=1234,
        start_time=time.time(),
        job_id=job.job_id,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    wt_path, branch, head = create_worktree(
        repo_path=repo_dir,
        job_dir=job_dir,
        title=job.title,
        base_state_dir=state_dir,
    )
    job.worktree_path = wt_path
    job.branch = branch
    job.head_commit = head

    transition_job_state(
        job,
        JobState.RUNNING,
        event_trigger="Process launched",
        base_state_dir=state_dir,
    )
    baseline = take_snapshot(job, home=tmp_path)
    save_snapshot(baseline, job_dir / "snapshot.json")

    # Secret-leaking verify failure output
    secret_token = "api_key = 'sk-abcdef1234567890abcdef1234567890'"
    raw_verify_tail = f"FAILED test_auth.py - Exception: {secret_token}\n"

    with patch("agy_mcp.verify.run_verify", return_value=(1, raw_verify_tail)):
        supervisor = JobSupervisor(
            job=job,
            handle=handle,
            runner=runner,
            job_dir=job_dir,
            max_minutes=job.max_minutes,
            baseline_snapshot=baseline,
            base_state_dir=state_dir,
            home=tmp_path,
        )
        supervisor.run_loop()

    # verify_tail.txt preserves the full raw output inside the D80 diagnostic
    # block (command, timeout, result, then the last ~40 lines).
    tail_file = job_dir / "verify_tail.txt"
    assert tail_file.is_file()
    tail_text = tail_file.read_text(encoding="utf-8")
    assert raw_verify_tail.strip() in tail_text
    assert "[wrapper] verify command: pytest" in tail_text
    assert "[wrapper] timeout: 900s" in tail_text
    assert "[wrapper] result: command exited on its own with rc=1" in tail_text

    # audit.jsonl must contain verify_tail, but REDACTED
    audit_file = job_dir / "audit.jsonl"
    assert audit_file.is_file()
    events = [
        json.loads(line)
        for line in audit_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    # Check state_transition or verify events
    transition_events = [e for e in events if e.get("event") == "state_transition"]
    assert len(transition_events) > 0
    transition_detail = transition_events[-1].get("detail", {})

    # verify_tail in transition detail must NOT contain the unredacted secret
    assert "sk-abcdef" not in str(transition_detail), "Secret leaked in audit.jsonl!"
    assert "[REDACTED" in str(transition_detail.get("verify_tail", "")), (
        "verify_tail was not redacted in audit.jsonl"
    )
