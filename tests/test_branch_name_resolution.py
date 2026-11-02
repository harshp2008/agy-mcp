"""A3: the branch named by agy_start must be the branch that is actually created.

`jobs.create_job` used to guess ``agy/<slug>-<first 8 hex of the job id>`` and
agy_start returned that guess, while ``create_worktree`` later allocated
``agy/<slug>-<secrets.token_hex(4)>``. Every later tool and git showed the
second name, so a branch copied from the agy_start envelope did not exist.
"""

from pathlib import Path

from agy_mcp.config import Config
from agy_mcp.gitops import run_git
from agy_mcp.jobs import get_job
from agy_mcp.manager import JobManager
from agy_mcp.models import JobState
from agy_mcp.runner import LocalProcessRunner
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"


def _manager(tmp_path: Path) -> tuple[JobManager, Path]:
    repo_dir = make_repo(tmp_path / "repo", initial_commit=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    manager = JobManager(
        config=Config(max_concurrent_jobs=2, daily_jobs_cap=5),
        runner=LocalProcessRunner(executable=FAKE_AGY),
        base_state_dir=state_dir,
        home=tmp_path,
        settle_window_seconds=0.1,
        poll_interval_seconds=0.05,
    )
    return manager, repo_dir


def _git_branches(repo: Path) -> list[str]:
    res = run_git(["for-each-ref", "--format=%(refname:short)", "refs/heads"], repo)
    assert res.returncode == 0, res.stderr
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def test_branch_name_is_computed_once_and_agrees_everywhere(
    tmp_path: Path, safe_git_env: dict[str, str]
) -> None:
    manager, repo_dir = _manager(tmp_path)
    env = manager.start(
        prompt="Perform task __SCENARIO:success_with_commits__",
        repo_path=str(repo_dir),
        title="A3 Branch Name Consistency",
    )
    started_branch = env.branch
    assert started_branch.startswith("agy/")

    # Persisted before agy_start returns, not patched later by the provisioning
    # thread: a crash between start and provisioning must not leave a job whose
    # envelope branch names a ref that was never created.
    persisted = get_job(env.job_id, manager.state_dir)
    assert persisted.branch == started_branch

    waited = manager.wait(env.job_id, seconds=10)
    assert waited.state == JobState.DONE
    assert waited.branch == started_branch

    status = manager.status(env.job_id)
    review = manager.review(env.job_id)
    result = manager.result(env.job_id)
    assert status.branch == started_branch
    assert review.branch == started_branch
    assert result.branch == started_branch
    assert get_job(env.job_id, manager.state_dir).branch == started_branch

    # git agrees: the ref exists with exactly that name and the worktree is on it.
    assert started_branch in _git_branches(repo_dir)
    worktree = get_job(env.job_id, manager.state_dir).worktree_path
    head = run_git(["rev-parse", "--abbrev-ref", "HEAD"], worktree, check=True)
    assert head.stdout.strip() == started_branch

    # Merging and cleaning up the branch named by agy_start must work.
    run_git(["merge", "--ff-only", started_branch], repo_dir, check=True)
    cleanup = manager.cleanup(env.job_id, remove_worktree=True, delete_branch=True)
    assert cleanup.branch_deleted is True
    assert started_branch not in _git_branches(repo_dir)


def test_two_jobs_with_the_same_title_get_distinct_persisted_branches(
    tmp_path: Path, safe_git_env: dict[str, str]
) -> None:
    manager, repo_dir = _manager(tmp_path)
    first = manager.start(
        prompt="Perform task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Same Title Twice",
    )
    second = manager.start(
        prompt="Perform task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Same Title Twice",
    )
    assert first.branch != second.branch
    assert get_job(first.job_id, manager.state_dir).branch == first.branch
    assert get_job(second.job_id, manager.state_dir).branch == second.branch

    manager.wait(first.job_id, seconds=10)
    manager.wait(second.job_id, seconds=10)
    branches = _git_branches(repo_dir)
    assert first.branch in branches
    assert second.branch in branches
