"""Tests for finding F15: Pre-kill checkpoint commits_count increment."""

import time
from pathlib import Path

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


def test_f15_sensitive_dirty_cancel_commits_count_zero(tmp_path: Path) -> None:
    """Worktree with sensitive untracked files must not increment commits_count on cancel."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    state_dir = tmp_path / "state"

    env = manager.start(
        prompt="Long job __SCENARIO:hang__",
        repo_path=str(repo_dir),
        title="Sensitive Dirty Cancel Job",
    )

    # Wait until running
    for _ in range(50):
        j = get_job(env.job_id, state_dir)
        if j.state == JobState.RUNNING and j.worktree_path and j.worktree_path.is_dir():
            break
        time.sleep(0.05)

    assert j.state == JobState.RUNNING
    assert j.commits_count == 0
    orig_base_commit = j.base_commit

    # Create only a sensitive file (.env) in worktree
    env_file = j.worktree_path / ".env"
    env_file.write_text("SUPER_SECRET_KEY=abcdef123456\n")

    # Cancel the running job
    manager.cancel(env.job_id)

    refreshed = get_job(env.job_id, state_dir)
    assert refreshed.state == JobState.CANCELLED
    # commits_count must stay 0 because checkpoint_commit returned None
    assert refreshed.commits_count == 0, (
        f"Expected commits_count 0 when checkpoint None, got {refreshed.commits_count}"
    )
    # head_commit must not be corrupted or changed to None
    assert refreshed.head_commit == orig_base_commit or refreshed.head_commit is not None
