"""Tests for finding F23: Stale index.lock deletion liveness callback verification."""

import os
import time
from pathlib import Path

from agy_mcp import gitops
from agy_mcp.config import Config
from agy_mcp.manager import JobManager
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


def test_f23_unknown_liveness_does_not_delete_stale_lock(tmp_path: Path) -> None:
    """When liveness is unknown, stale index.lock must NOT be deleted."""
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True, env=get_safe_git_env(tmp_path))

    # Create stale index.lock (age > 300s)
    lock_file = repo_dir / ".git" / "index.lock"
    lock_file.write_text("dummy lock\n")
    old_mtime = time.time() - 600
    os.utime(lock_file, (old_mtime, old_mtime))

    def dummy_action():
        return gitops.GitResult(1, "", "fatal: Unable to create '.git/index.lock': File exists", [])

    # Action fails on index.lock; execute with unknown liveness (None)
    gitops.execute_with_index_lock_retry(
        action=dummy_action,
        worktree_path=repo_dir,
        max_attempts=1,
        sleep_func=lambda _: None,
        is_process_alive_func=None,
    )

    assert lock_file.exists(), "Unknown liveness must treat process as alive and keep index.lock"


def test_f23_live_process_does_not_delete_stale_lock(tmp_path: Path) -> None:
    """When process is alive, stale index.lock must NOT be deleted."""
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True, env=get_safe_git_env(tmp_path))

    lock_file = repo_dir / ".git" / "index.lock"
    lock_file.write_text("dummy lock\n")
    old_mtime = time.time() - 600
    os.utime(lock_file, (old_mtime, old_mtime))

    def dummy_action():
        return gitops.GitResult(1, "", "fatal: Unable to create '.git/index.lock': File exists", [])

    gitops.execute_with_index_lock_retry(
        action=dummy_action,
        worktree_path=repo_dir,
        max_attempts=1,
        sleep_func=lambda _: None,
        is_process_alive_func=lambda: True,
    )

    assert lock_file.exists(), "Live process must NOT have its index.lock deleted"


def test_f23_dead_process_deletes_stale_lock(tmp_path: Path) -> None:
    """When process is confirmed dead and lock > 300s old, index.lock IS deleted."""
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True, env=get_safe_git_env(tmp_path))

    lock_file = repo_dir / ".git" / "index.lock"
    lock_file.write_text("dummy lock\n")
    old_mtime = time.time() - 600
    os.utime(lock_file, (old_mtime, old_mtime))

    def dummy_action():
        return gitops.GitResult(1, "", "fatal: Unable to create '.git/index.lock': File exists", [])

    gitops.execute_with_index_lock_retry(
        action=dummy_action,
        worktree_path=repo_dir,
        max_attempts=1,
        sleep_func=lambda _: None,
        is_process_alive_func=lambda: False,
    )

    assert not lock_file.exists(), "Dead process stale index.lock must be deleted"


def test_f23_watcher_has_real_liveness_callback(tmp_path: Path) -> None:
    """Supervisor watcher must be configured with a real liveness callback."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)

    env = manager.start(
        prompt="Simple task __SCENARIO:hang__",
        repo_path=str(repo_dir),
        title="Liveness Callback Job",
    )

    for _ in range(100):
        if env.job_id in manager._supervisors:
            break
        time.sleep(0.05)

    supervisor = manager._supervisors.get(env.job_id)
    assert supervisor is not None

    # Watcher must have an is_process_alive callback
    cb = getattr(supervisor.watcher, "is_process_alive", None)
    assert cb is not None and callable(cb), "Watcher must have a callable is_process_alive callback"

    # While process is running, callback returns True
    assert cb() is True, "Callback must return True while process is alive"

    # Terminate process and verify callback returns False
    manager.cancel(env.job_id)
    assert cb() is False, "Callback must return False after process is terminated"
