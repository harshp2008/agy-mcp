"""Tests for finding F14: crash recovery must not trust stale stdout and must preserve deadline."""

import json
import os
import time
from pathlib import Path

from agy_mcp.audit import save_snapshot, take_snapshot
from agy_mcp.jobs import create_job, get_job, get_job_dir, save_job, transition_job_state
from agy_mcp.manager import JobManager
from agy_mcp.models import Job, JobState
from agy_mcp.recovery import reconcile_on_startup
from agy_mcp.runner import LocalProcessRunner, read_proc_start_time
from agy_mcp.worktree import create_worktree
from tests.helpers.make_repo import make_repo


def _setup_repo_and_job(tmp_path: Path) -> tuple[Job, Path, Path]:
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)

    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    job = create_job(
        repo_path=repo_dir,
        title="F14 Recovery Job",
        prompt="Execute task",
        model="gemini-3.8-flash-medium",
        max_minutes=60,
        base_state_dir=state_dir,
    )

    job_dir = get_job_dir(job.job_id, state_dir)
    wt_path, branch, head = create_worktree(
        repo_path=repo_dir,
        job_dir=job_dir,
        title=job.title,
        base_state_dir=state_dir,
    )
    job.worktree_path = wt_path
    job.branch = branch
    job.head_commit = head
    save_job(job, state_dir)
    snapshot = take_snapshot(job, home=tmp_path)
    save_snapshot(snapshot, job_dir / "snapshot.json")
    return job, job_dir, state_dir


def test_recovery_does_not_trust_stale_stdout_after_resume(tmp_path: Path) -> None:
    """Crash recovery must fail closed to needs_review when stdout is stale from previous turn."""
    job, job_dir, state_dir = _setup_repo_and_job(tmp_path)

    # Turn 1 stdout was written and job had completed turn 1
    stdout_file = job_dir / "stdout.txt"
    turn1_payload = {
        "conversation_id": "11111111-1111-4111-8111-111111111111",
        "status": "SUCCESS",
        "response": "Turn 1 completed successfully",
        "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
    }
    turn1_text = json.dumps(turn1_payload) + "\n"
    stdout_file.write_text(turn1_text)

    # Now turn 2 starts: job resumed, state is RUNNING, but process dies before writing anything
    transition_job_state(
        job,
        JobState.RUNNING,
        event_trigger="Resumed for turn 2",
        base_state_dir=state_dir,
    )
    job.pid = 9999998  # Dead PID
    job.start_time = 12345
    job.stdout_offset = len(turn1_text.encode("utf-8"))
    job.run_generation = 2
    job.launch_wall_clock = time.time()
    save_job(job, state_dir)

    # Reconcile on startup
    reconciled = reconcile_on_startup(state_dir=state_dir, home=tmp_path)
    assert len(reconciled) == 1

    updated_job = get_job(job.job_id, state_dir)
    # Stale stdout must NOT be trusted as turn 2 outcome (must NOT be DONE)
    assert updated_job.state == JobState.NEEDS_REVIEW
    assert updated_job.review_reason == "unconfirmed_recovery"


def test_recovery_preserves_wall_clock_start(tmp_path: Path) -> None:
    """Supervisor initialized during recovery must preserve original launch_wall_clock."""
    job, job_dir, state_dir = _setup_repo_and_job(tmp_path)

    transition_job_state(
        job,
        JobState.RUNNING,
        event_trigger="Launched",
        base_state_dir=state_dir,
    )
    my_pid = os.getpid()
    job.pid = my_pid
    job.start_time = read_proc_start_time(my_pid)
    # Job was launched 1800 seconds (30 minutes) ago
    one_hour_ago = time.time() - 1800.0
    job.launch_wall_clock = one_hour_ago
    save_job(job, state_dir)

    runner = LocalProcessRunner()
    from agy_mcp.config import Config
    mgr = JobManager(runner=runner, config=Config(), base_state_dir=state_dir, home=tmp_path)
    mgr.reconcile()

    supervisor = mgr._supervisors.get(job.job_id)
    assert supervisor is not None
    # started_at must reflect launch_wall_clock, not time.time()
    assert abs(supervisor.started_at - one_hour_ago) < 5.0
