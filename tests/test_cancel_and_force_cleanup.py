"""Tests for finding F12: agy_cancel on queued jobs and cleanup(force=True) on running jobs."""

import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

from agy_mcp.config import Config
from agy_mcp.jobs import get_job, transition_job_state
from agy_mcp.manager import JobManager
from agy_mcp.models import JobState
from agy_mcp.runner import LocalProcessRunner
from tests.helpers.make_repo import get_safe_git_env, make_repo


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _setup_manager_and_repo(
    tmp_path: Path, config: Config | None = None
) -> tuple[JobManager, Path]:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = home / "repo"
    make_repo(repo_dir, initial_commit=True, env=get_safe_git_env(home))

    fake_agy_path = Path(__file__).parent / "fake_agy" / "agy"
    runner = LocalProcessRunner(executable=str(fake_agy_path))

    cfg = config or Config()
    mgr = JobManager(
        runner=runner,
        config=cfg,
        base_state_dir=state_dir,
        home=home,
        settle_window_seconds=0.1,
    )
    return mgr, repo_dir


def test_f12_cancel_queued_job_aborts_launch(tmp_path: Path) -> None:
    """agy_cancel on a queued job transitions to cancelled and aborts process launch."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)

    # First verify queued -> cancelled transition is legal
    job = get_job(
        manager.start("Test task", str(repo_dir), "Queued Job").job_id,
        manager.state_dir,
    )
    job.state = JobState.QUEUED

    # This transition must be legal per SPEC section 7
    updated = transition_job_state(
        job,
        JobState.CANCELLED,
        event_trigger="Explicit call to agy_cancel",
        base_state_dir=manager.state_dir,
    )
    assert updated.state == JobState.CANCELLED

    # Now test full manager cancel on queued job
    provision_gate = threading.Event()
    original_launch = manager.runner.launch
    launch_called = []

    def gated_launch(*args, **kwargs):
        launch_called.append(True)
        return original_launch(*args, **kwargs)

    manager.runner.launch = gated_launch

    with patch("agy_mcp.manager.create_worktree") as mock_wt:
        # Delay provisioning so job stays in QUEUED state
        def delayed_wt(*args, **kwargs):
            provision_gate.wait(timeout=5.0)
            raise RuntimeError("Should have aborted before creating worktree")

        mock_wt.side_effect = delayed_wt

        env = manager.start("Test task", str(repo_dir), "Queued Job 2")
        assert env.state == JobState.QUEUED

        # Cancel while queued
        cancel_res = manager.cancel(env.job_id)
        assert cancel_res.state == JobState.CANCELLED

        # Release gate and wait
        provision_gate.set()
        time.sleep(0.5)

        # Process launch must never have occurred
        assert len(launch_called) == 0, "Runner launch was called despite queued cancellation"

        refreshed = get_job(env.job_id, manager.state_dir)
        assert refreshed.state == JobState.CANCELLED


def test_f12_cleanup_force_on_running_job_kills_process_first(tmp_path: Path) -> None:
    """cleanup(force=True) on a running job must kill process and finalize before removing."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)

    env = manager.start(
        prompt="Work on task __SCENARIO:hang_after_partial_commit__",
        repo_path=str(repo_dir),
        title="F12 Cleanup Running Job",
    )

    # Wait until running with pid
    deadline = time.time() + 5.0
    job = None
    while time.time() < deadline:
        st = manager.status(env.job_id)
        if st.state == JobState.RUNNING:
            job = get_job(env.job_id, manager.state_dir)
            if job.pid:
                break
        time.sleep(0.05)

    assert job is not None
    assert job.pid is not None
    pid = job.pid
    assert _is_alive(pid)

    # Cleanup with force=True
    cleanup_res = manager.cleanup(env.job_id, force=True)
    assert cleanup_res.worktree_removed

    # Process MUST be dead!
    kill_deadline = time.time() + 2.0
    while time.time() < kill_deadline and _is_alive(pid):
        time.sleep(0.05)

    assert not _is_alive(pid), f"PID {pid} was left running after cleanup(force=True)"

    refreshed = get_job(env.job_id, manager.state_dir)
    assert refreshed.state == JobState.CANCELLED
