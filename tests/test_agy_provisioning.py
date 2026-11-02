"""ITEM 1 (audit A4/F24): the production launch path must provision .agy/.

These tests drive the real ``JobManager.start`` -> ``_provision_and_launch`` path
with the fake agy. They fail on the pre-fix tree because nothing calls
``worktree.provision_metadata_dir`` outside tests.
"""

import stat
from pathlib import Path
from unittest.mock import patch

from agy_mcp.config import Config
from agy_mcp.gitops import run_git
from agy_mcp.jobs import get_job
from agy_mcp.manager import JobManager
from agy_mcp.models import JobState
from agy_mcp.runner import LocalProcessRunner
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"


def _make_manager(tmp_path: Path, config: Config | None = None) -> JobManager:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    return JobManager(
        config=config or Config(max_concurrent_jobs=2, daily_jobs_cap=5),
        runner=LocalProcessRunner(executable=FAKE_AGY),
        base_state_dir=state_dir,
        home=tmp_path,
        settle_window_seconds=0.1,
        poll_interval_seconds=0.05,
    )


def _setup_manager(tmp_path: Path, config: Config | None = None) -> tuple[JobManager, Path]:
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    return _make_manager(tmp_path, config), repo_dir


def test_launch_path_provisions_agy_dir(tmp_path: Path) -> None:
    """A real start -> launch cycle leaves <worktree>/.agy/ at mode 0700 with PLAN.md."""
    manager, repo_dir = _setup_manager(tmp_path)
    env = manager.start(
        prompt="Perform task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Provision Me",
    )
    manager.wait(env.job_id, seconds=10)
    job = get_job(env.job_id, tmp_path / "state")
    assert job.state == JobState.DONE, f"expected DONE, got {job.state} ({job.review_reason})"
    assert job.worktree_path is not None
    agy_dir = job.worktree_path / ".agy"
    assert agy_dir.is_dir(), "launch path did not create <worktree>/.agy"
    assert not agy_dir.is_symlink()
    assert stat.S_IMODE(agy_dir.stat().st_mode) == 0o700
    assert (agy_dir / "PLAN.md").is_file(), "launch path did not seed .agy/PLAN.md"

    # Excluded through the repository-local exclude file, not a tracked ignore.
    check = run_git(["check-ignore", "-q", ".agy/PLAN.md"], job.worktree_path)
    assert check.returncode == 0, "provisioned .agy is not ignored by git"
    tracked = run_git(["ls-files", ".agy"], job.worktree_path)
    assert tracked.stdout.strip() == "", f".agy leaked into the index: {tracked.stdout!r}"


def test_provision_never_edits_tracked_gitignore(tmp_path: Path) -> None:
    """The exclude is written to the local info/exclude, never the tracked .gitignore."""
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    tracked_ignore = repo_dir / ".gitignore"
    tracked_ignore.write_text("*.log\n", encoding="utf-8")
    run_git(["add", ".gitignore"], repo_dir, check=True)
    run_git(["commit", "-m", "add gitignore"], repo_dir, check=True)
    before = tracked_ignore.read_text(encoding="utf-8")

    manager = _make_manager(tmp_path)
    env = manager.start(
        prompt="Perform task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Ignore Check",
    )
    manager.wait(env.job_id, seconds=10)
    job = get_job(env.job_id, tmp_path / "state")
    assert job.worktree_path is not None

    assert tracked_ignore.read_text(encoding="utf-8") == before, "tracked .gitignore was edited"
    status = run_git(["status", "--porcelain", ":(top).gitignore"], job.worktree_path)
    assert status.stdout.strip() == "", "tracked .gitignore shows as modified"
    # The local exclude is untracked by definition.
    exclude_res = run_git(["rev-parse", "--git-path", "info/exclude"], job.worktree_path)
    exclude_path = Path(exclude_res.stdout.strip())
    if not exclude_path.is_absolute():
        exclude_path = job.worktree_path / exclude_path
    assert exclude_path.is_file()
    assert ".agy/" in exclude_path.read_text(encoding="utf-8")


def test_provision_failure_fails_closed_without_launch(tmp_path: Path) -> None:
    """If provisioning raises, the job is FAILED/setup_failed, cleaned up, agy never runs."""
    manager, repo_dir = _setup_manager(tmp_path)

    with patch(
        "agy_mcp.manager.provision_metadata_dir",
        side_effect=RuntimeError("provision exploded"),
    ):
        env = manager.start(
            prompt="Perform task __SCENARIO:success_simple__",
            repo_path=str(repo_dir),
            title="Provision Failure",
        )
        manager.wait(env.job_id, seconds=10)

    job = get_job(env.job_id, tmp_path / "state")
    assert job.state == JobState.FAILED
    assert job.review_reason == "setup_failed"
    assert job.pid is None
    assert job.worktree_path is None or not job.worktree_path.exists(), "worktree not cleaned up"
    branches = run_git(["branch", "--list", "agy/*"], repo_dir)
    assert branches.stdout.strip() == "", f"branch leaked: {branches.stdout!r}"
    # The fake agy writes this line to cli.log; its absence proves no launch.
    job_dir = tmp_path / "state" / "jobs" / env.job_id
    cli_log = job_dir / "cli.log"
    assert not cli_log.is_file() or "Starting fake agy session" not in cli_log.read_text(
        encoding="utf-8", errors="replace"
    )
