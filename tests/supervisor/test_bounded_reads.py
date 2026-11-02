"""Tests for finding F16: explicit size caps and bounded file reads across the server."""

import json
from pathlib import Path

from agy_mcp.audit import load_snapshot
from agy_mcp.jobs import get_job
from agy_mcp.recovery import get_liveness_summary
from agy_mcp.verify import run_verify
from agy_mcp.watcher import validate_commit_message


def test_job_json_oversized_fails_closed(tmp_path: Path) -> None:
    """Oversized job.json (> 1 MB) must fail closed with ValueError rather than loading."""
    state_dir = tmp_path / "state"
    job_dir = state_dir / "jobs" / "huge-job-123"
    job_dir.mkdir(parents=True, exist_ok=True)
    job_file = job_dir / "job.json"

    # Write a 2 MB valid job file
    padding = " " * (2 * 1024 * 1024)
    data = {
        "job_id": "huge-job-123",
        "title": "Huge Job",
        "repo_path": "/tmp/repo",
        "branch": "main",
        "base_commit": "abc",
        "state": "running",
        "padding": padding,
    }
    job_file.write_text(json.dumps(data))

    try:
        get_job("huge-job-123", base_state_dir=state_dir)
        assert False, "Expected ValueError for oversized job.json"
    except ValueError as e:
        assert "exceeds" in str(e).lower() or "limit" in str(e).lower()


def test_snapshot_oversized_fails_closed(tmp_path: Path) -> None:
    """Oversized snapshot.json (> 2 MB) must fail closed and return None."""
    snapshot_path = tmp_path / "snapshot.json"
    padding = " " * (3 * 1024 * 1024)
    data = {"dot_git_content": "gitdir: /tmp", "padding": padding}
    snapshot_path.write_text(json.dumps(data))

    res = load_snapshot(snapshot_path)
    assert res is None


def test_commit_msg_oversized_fails_closed(tmp_path: Path) -> None:
    """Oversized .agy/COMMIT_MSG (> 64 KB) with valid subject must fail validation."""
    msg_path = tmp_path / "COMMIT_MSG"
    msg_path.write_text("feat: valid subject line\n\n" + ("A" * (70 * 1024)))

    valid, err = validate_commit_message(msg_path)
    assert not valid
    assert err is not None
    assert "exceeds" in err.lower() or "limit" in err.lower()


def test_verify_run_verify_bounded_memory_on_huge_output(tmp_path: Path) -> None:
    """run_verify must cap captured output tail and include truncation marker on huge output."""
    cmd = 'python3 -c "import sys; sys.stdout.write(\'X\' * (5 * 1024 * 1024))"'
    rc, tail = run_verify(cmd, worktree_path=tmp_path, timeout_seconds=10)
    assert rc == 0
    assert len(tail.encode("utf-8")) <= 16 * 1024
    assert "[TRUNCATED" in tail


def test_liveness_summary_huge_cli_log_bounded_memory(tmp_path: Path) -> None:
    """get_liveness_summary must not read entire huge cli.log (> 10 MB)."""
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    cli_log = job_dir / "cli.log"

    # Create 15 MB file
    with open(cli_log, "wb") as f:
        f.write(b"entry\n" * (2 * 1024 * 1024))
        f.write(b"FINAL_LINE_IN_LOG\n")

    summary = get_liveness_summary(job_dir, tmp_path)
    recent_tail = summary["recent_log_tail"]
    assert isinstance(recent_tail, list)
    assert len(recent_tail) <= 50
    assert any("FINAL_LINE_IN_LOG" in line for line in recent_tail)

