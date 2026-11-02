"""Tests for finding F21: Watcher commit and cancel pre-kill checkpoint serialization."""

import threading
from pathlib import Path
from unittest.mock import patch

from agy_mcp import gitops
from agy_mcp.config import Config
from agy_mcp.jobs import get_job
from agy_mcp.manager import JobManager
from agy_mcp.models import JobState
from agy_mcp.runner import LocalProcessRunner
from tests.helpers.make_repo import get_safe_git_env, make_repo


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


def test_f21_cancel_pre_kill_checkpoint_races_watcher_commit_serialized(tmp_path: Path) -> None:
    """Watcher commit and cancel pre-kill checkpoint serialize on commit_lock without failure."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    state_dir = tmp_path / "state"

    env = manager.start(
        prompt="Commit sequence __SCENARIO:hang_after_partial_commit__",
        repo_path=str(repo_dir),
        title="Concurrent Commit Job",
    )

    # Wait until supervisor is active and running
    for _ in range(100):
        j = get_job(env.job_id, state_dir)
        if env.job_id in manager._supervisors and j.state == JobState.RUNNING:
            break
        threading.Event().wait(0.05)

    wt = j.worktree_path
    assert wt is not None and wt.is_dir()

    # Create changes and a COMMIT_MSG for watcher
    (wt / "change.txt").write_text("content\n")
    agy_dir = wt / ".agy"
    agy_dir.mkdir(parents=True, exist_ok=True)
    (agy_dir / "COMMIT_MSG").write_text("feat: valid commit message\n")

    b_inside_commit = threading.Barrier(2)
    orig_commit_with_retry = gitops.commit_with_retry
    cancel_thread_started = threading.Event()

    def wrapped_commit_with_retry(worktree_path: Path, msg_file: Path | None = None, **kwargs):
        # We are inside watcher's commit under commit_lock
        b_inside_commit.wait()
        cancel_thread_started.wait()
        return orig_commit_with_retry(worktree_path, msg_file=msg_file, **kwargs)

    cancel_envelope = []

    def _cancel_worker():
        cancel_thread_started.set()
        # Cancel will try to acquire commit_lock for pre-kill checkpoint
        res = manager.cancel(env.job_id)
        cancel_envelope.append(res)

    with patch("agy_mcp.watcher.commit_with_retry", side_effect=wrapped_commit_with_retry):
        # Trigger watcher tick
        supervisor = manager._supervisors.get(env.job_id)
        assert supervisor is not None

        def _watcher_worker():
            supervisor.watcher.tick()

        w_thread = threading.Thread(target=_watcher_worker)
        w_thread.start()

        # Wait until watcher enters commit_with_retry
        b_inside_commit.wait()

        # commit_lock must be held by the watcher thread
        assert supervisor.commit_lock.locked(), "commit_lock must be held during watcher commit"
        assert supervisor.commit_lock is supervisor.watcher.commit_lock

        # Now launch concurrent cancel
        c_thread = threading.Thread(target=_cancel_worker)
        c_thread.start()

        w_thread.join()
        c_thread.join()

    # The job must end CANCELLED, not FAILED
    refreshed = get_job(env.job_id, state_dir)
    assert refreshed.state == JobState.CANCELLED, f"Expected CANCELLED state, got {refreshed.state}"
    assert refreshed.commits_count == 1, (
        f"Expected commits_count 1 from watcher commit, got {refreshed.commits_count}"
    )
