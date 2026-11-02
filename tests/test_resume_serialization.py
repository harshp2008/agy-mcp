"""Tests for finding F4: Resume/launch serialization and post-launch exception handling."""

import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

from agy_mcp.config import Config
from agy_mcp.jobs import get_job
from agy_mcp.manager import JobManager, ManagerError
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


def test_f4_concurrent_resumes_one_launch_one_rejected(tmp_path: Path) -> None:
    """Two concurrent resumes must result in exactly one launch and one clean rejection."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)

    # Start a job that pauses in needs_decision
    env = manager.start(
        prompt="Decision task __SCENARIO:needs_decision__",
        repo_path=str(repo_dir),
        title="F4 Decision Job",
    )
    wait_env = manager.wait(env.job_id, seconds=5)
    assert wait_env.state == JobState.NEEDS_DECISION

    results = []
    errors = []

    def _do_resume(ans: str) -> None:
        try:
            r = manager.resume(env.job_id, ans)
            results.append(r)
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=_do_resume, args=("Option A",))
    t2 = threading.Thread(target=_do_resume, args=("Option B",))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Exactly one resume should succeed
    assert len(results) == 1, f"Expected 1 successful resume, got {len(results)}"
    # Exactly one should fail with invalid_state_for_resume
    assert len(errors) == 1, f"Expected 1 error, got {len(errors)}"
    assert isinstance(errors[0], ManagerError), f"Expected ManagerError, got {type(errors[0])}"
    assert errors[0].code == "invalid_state_for_resume", (
        f"Expected invalid_state_for_resume, got {errors[0].code}"
    )


def test_f4_provision_and_launch_failure_kills_process(tmp_path: Path) -> None:
    """Failure injected after launch in _provision_and_launch must kill the spawned process."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)

    spawned_pids = []
    original_launch = manager.runner.launch

    def tracking_launch(*args, **kwargs):
        handle = original_launch(*args, **kwargs)
        spawned_pids.append(handle.pid)
        return handle

    manager.runner.launch = tracking_launch

    # Inject failure right after launch (e.g. disk full during save_snapshot)
    with patch("agy_mcp.manager.save_snapshot", side_effect=OSError("Disk full simulation")):
        env = manager.start(
            prompt="Simple task __SCENARIO:hang_after_partial_commit__",
            repo_path=str(repo_dir),
            title="F4 Setup Failure Job",
        )

        deadline = time.time() + 5.0
        while time.time() < deadline:
            st = manager.status(env.job_id)
            if st.state == JobState.FAILED:
                break
            time.sleep(0.1)

        st = manager.status(env.job_id)
        assert st.state == JobState.FAILED
        job = get_job(env.job_id, manager.state_dir)
        assert job.review_reason == "setup_failed"

        # The spawned process must have been killed!
        assert len(spawned_pids) == 1
        spawned_pid = spawned_pids[0]

        # Give it a brief moment to finish reaping
        kill_deadline = time.time() + 2.0
        while time.time() < kill_deadline and _is_alive(spawned_pid):
            time.sleep(0.05)

        assert not _is_alive(spawned_pid), f"PID {spawned_pid} was orphaned after setup failure"
