"""Unit tests for crash recovery, process liveness checking, and startup reconciliation."""

import json
import os
import signal
import time
from pathlib import Path
from typing import Callable

from agy_mcp.audit import save_snapshot, take_snapshot
from agy_mcp.gitops import is_worktree_dirty
from agy_mcp.jobs import create_job, get_job, get_job_dir, save_job, transition_job_state
from agy_mcp.manager import JobManager
from agy_mcp.models import Job
from agy_mcp.recovery import (
    check_process_liveness,
    get_liveness_summary,
    reconcile_on_startup,
)
from agy_mcp.runner import LocalProcessRunner, read_proc_start_time
from agy_mcp.states import JobState
from agy_mcp.worktree import create_worktree
from tests.helpers.make_repo import make_repo


def poll_until(
    predicate: Callable[[], bool],
    timeout_seconds: float = 10.0,
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


def _setup_repo_and_job(tmp_path: Path) -> tuple[Job, Path, Path]:
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)

    state_dir = tmp_path / "state"
    state_dir.mkdir()

    job = create_job(
        repo_path=repo_dir,
        title="Recovery Test Job",
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


def test_check_process_liveness_real_and_fake() -> None:
    my_pid = os.getpid()
    start_time = read_proc_start_time(my_pid)
    assert start_time > 0

    # True for active process with matching start time
    assert check_process_liveness(my_pid, start_time)

    # False for mismatched start time (PID reuse simulation)
    assert not check_process_liveness(my_pid, start_time + 99999)

    # False for non-existent PID
    assert not check_process_liveness(99999999, 100)

    # False for invalid inputs
    assert not check_process_liveness(0, 100)
    assert not check_process_liveness(1, 0)


def test_reconcile_startup_dead_process_finalization(tmp_path: Path) -> None:
    job, job_dir, state_dir = _setup_repo_and_job(tmp_path)
    transition_job_state(
        job,
        JobState.RUNNING,
        event_trigger="Launched",
        base_state_dir=state_dir,
    )

    # Assign a dead PID
    job.pid = 9999999
    job.start_time = 12345
    save_job(job, state_dir)

    # Simulate completed stdout.txt left behind before crash
    stdout_file = job_dir / "stdout.txt"
    stdout_payload = {
        "conversation_id": "11111111-1111-4111-8111-111111111111",
        "status": "SUCCESS",
        "response": "Work finished before sudden crash",
        "usage": {"input_tokens": 120, "output_tokens": 40, "total_tokens": 160},
    }
    stdout_file.write_text(json.dumps(stdout_payload))

    # Dirty the worktree to verify safety net commit
    (job.worktree_path / "work_file.txt").write_text("Recovered content\n")
    assert is_worktree_dirty(job.worktree_path)

    reconciled = reconcile_on_startup(state_dir=state_dir, home=tmp_path)
    assert len(reconciled) == 1

    updated_job = get_job(job.job_id, state_dir)
    assert updated_job.state == JobState.DONE
    assert updated_job.recovered is True
    assert updated_job.conversation_id == "11111111-1111-4111-8111-111111111111"
    assert updated_job.tokens.total_tokens == 160
    # Safety net commit was executed
    assert not is_worktree_dirty(job.worktree_path)


def test_reconcile_startup_stale_queued_job(tmp_path: Path) -> None:
    job, job_dir, state_dir = _setup_repo_and_job(tmp_path)
    assert job.state == JobState.QUEUED

    reconciled = reconcile_on_startup(state_dir=state_dir, home=tmp_path)
    assert len(reconciled) == 1

    updated_job = get_job(job.job_id, state_dir)
    assert updated_job.state == JobState.FAILED
    assert updated_job.recovered is True


def test_reconcile_startup_preserves_alive_process(tmp_path: Path) -> None:
    job, job_dir, state_dir = _setup_repo_and_job(tmp_path)
    transition_job_state(
        job,
        JobState.RUNNING,
        event_trigger="Launched",
        base_state_dir=state_dir,
    )

    # Point to current test process
    my_pid = os.getpid()
    job.pid = my_pid
    job.start_time = read_proc_start_time(my_pid)
    save_job(job, state_dir)

    reconciled = reconcile_on_startup(state_dir=state_dir, home=tmp_path)
    assert len(reconciled) == 1

    updated_job = get_job(job.job_id, state_dir)
    # Process is alive, remains RUNNING
    assert updated_job.state == JobState.RUNNING


def test_get_liveness_summary(tmp_path: Path) -> None:
    job, job_dir, state_dir = _setup_repo_and_job(tmp_path)
    cli_log = job_dir / "cli.log"
    cli_log.write_text(
        "Step 1: processing...\nAPI_KEY = AIza12345678901234567890123456789012345\nStep 2: done\n"
    )

    summary = get_liveness_summary(job_dir, job.worktree_path)
    assert summary["last_cli_log_mtime"] > 0
    assert summary["worktree_dirty"] is False
    assert isinstance(summary["recent_log_tail"], list)
    assert len(summary["recent_log_tail"]) > 0

    # Sensitive key was redacted
    tail_joined = " ".join(summary["recent_log_tail"])
    assert "[REDACTED" in tail_joined
    assert "AIza1234567890" not in tail_joined


def test_recovery_adoption_slow_progress_end_to_end(tmp_path: Path) -> None:
    """Simulate server crash with running slow_progress agy process; adopt and complete."""
    fake_agy = Path(__file__).parent / "fake_agy" / "agy"
    runner = LocalProcessRunner(executable=fake_agy)
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    state_dir = tmp_path / "state"

    mgr = JobManager(runner=runner, base_state_dir=state_dir, home=tmp_path)
    env = mgr.start(
        prompt="Slow progress task __SCENARIO:slow_progress__",
        repo_path=str(repo_dir),
        title="Slow Progress Adoption",
    )

    # Wait until process is spawned and running
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if mgr.status(env.job_id).state == JobState.RUNNING:
            break
        time.sleep(0.05)

    assert mgr.status(env.job_id).state == JobState.RUNNING

    # Simulate server crash: stop supervisor loop without killing child process, and discard mgr
    with mgr._lock:
        for s in mgr._supervisors.values():
            s.stop()
    del mgr

    # Create new JobManager on the same state directory
    new_mgr = JobManager(runner=runner, base_state_dir=state_dir, home=tmp_path)
    reconciled = new_mgr.reconcile()
    assert any(j.job_id == env.job_id for j in reconciled)

    # Wait for completion via new manager
    final_env = new_mgr.wait(env.job_id, seconds=10)
    assert final_env.state == JobState.DONE
    assert final_env.commits_count >= 1


def test_recovery_pid_reuse_rejected_end_to_end(tmp_path: Path) -> None:
    """PID reuse (start_time mismatch) must reject adoption and finalize from disk streams."""
    fake_agy = Path(__file__).parent / "fake_agy" / "agy"
    runner = LocalProcessRunner(executable=fake_agy)
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    state_dir = tmp_path / "state"

    mgr = JobManager(runner=runner, base_state_dir=state_dir, home=tmp_path)
    env = mgr.start(
        prompt="Hang task __SCENARIO:hang__",
        repo_path=str(repo_dir),
        title="PID Reuse Test",
    )

    # Wait until running
    poll_until(
        lambda m=mgr: m.status(env.job_id).state == JobState.RUNNING,
        timeout_seconds=10.0,
        error_message="Job did not reach RUNNING state before crash simulation",
    )

    job_data = get_job(env.job_id, state_dir)
    assert job_data.pid is not None
    real_pid = job_data.pid

    # Simulate server crash: discard mgr
    del mgr

    # Tamper with recorded start_time to simulate PID reuse by different process
    job_data.start_time = 123456789
    save_job(job_data, state_dir)

    new_mgr = JobManager(runner=runner, base_state_dir=state_dir, home=tmp_path)
    reconciled = new_mgr.reconcile()
    assert any(j.job_id == env.job_id for j in reconciled)

    # Clean up the lingering hang process
    try:
        os.kill(real_pid, signal.SIGKILL)
    except Exception:
        pass

    recovered_job = get_job(env.job_id, state_dir)
    # Must NOT have remained running (not adopted)
    assert recovered_job.state != JobState.RUNNING
    assert recovered_job.recovered is True


def test_recovery_process_already_finished_while_server_down(tmp_path: Path) -> None:
    """Process that finished while server was down is finalized from files with safety-net."""
    fake_agy = Path(__file__).parent / "fake_agy" / "agy"
    runner = LocalProcessRunner(executable=fake_agy)
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    state_dir = tmp_path / "state"

    mgr = JobManager(runner=runner, base_state_dir=state_dir, home=tmp_path)
    env = mgr.start(
        prompt="Simple task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Finished While Down Test",
    )

    # Wait until running
    poll_until(
        lambda m=mgr: m.status(env.job_id).state == JobState.RUNNING,
        timeout_seconds=10.0,
        error_message="Job did not reach RUNNING state before crash simulation",
    )

    job_data = get_job(env.job_id, state_dir)
    # Stop manager immediately
    del mgr

    # Allow fake agy to finish and write stdout
    time.sleep(0.5)

    # Create uncommitted file in worktree to test F05 safety net commit
    (job_data.worktree_path / "uncommitted.txt").write_text("modified worktree content\n")
    assert is_worktree_dirty(job_data.worktree_path)

    new_mgr = JobManager(runner=runner, base_state_dir=state_dir, home=tmp_path)
    new_mgr.reconcile()

    finalized_job = get_job(env.job_id, state_dir)
    assert finalized_job.state == JobState.DONE
    assert finalized_job.recovered is True
    assert not is_worktree_dirty(finalized_job.worktree_path)
    assert finalized_job.commits_count >= 1
