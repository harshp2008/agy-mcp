"""A1: agy_cleanup must treat a job branch as merged when its commits are
reachable from the repository HEAD or from any other local branch.

The old gate compared ``base_commit..<branch>`` and therefore refused a branch
that had already been merged into ``integration`` (or fast-forwarded into
``main`` from a different ref) even though no commit would be lost.
"""

import uuid
from pathlib import Path

import pytest

from agy_mcp.config import Config
from agy_mcp.jobs import get_job, save_job
from agy_mcp.manager import JobManager, ManagerError
from agy_mcp.models import Job
from agy_mcp.runner import LocalProcessRunner
from agy_mcp.states import JobState
from agy_mcp.worktree import cleanup_worktree, create_worktree
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"


def _git(args: list[str], cwd: Path, env: dict[str, str], check: bool = True):
    from agy_mcp.gitops import run_git

    return run_git(args, cwd, env=env, check=check)


def _make_job(tmp_path: Path, title: str, env: dict[str, str]) -> tuple[Job, Path, Path]:
    """Create a real repo + worktree + persisted job. Returns (job, repo, wt)."""
    repo = make_repo(tmp_path / "repo", initial_commit=True, env=env)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    job_id = str(uuid.uuid4())
    job_dir = state_dir / "jobs" / job_id
    job_dir.mkdir(parents=True)
    wt_path, branch, base_sha = create_worktree(repo, job_dir=job_dir, title=title)
    job = Job(
        job_id=job_id,
        title=title,
        repo_path=repo,
        branch=branch,
        base_commit=base_sha,
        state=JobState.DONE,
        worktree_path=wt_path,
    )
    save_job(job, state_dir)
    return job, repo, wt_path


def _commit(wt_path: Path, name: str, message: str, env: dict[str, str]) -> None:
    (wt_path / name).write_text("change\n", encoding="utf-8")
    _git(["add", name], wt_path, env)
    _git(["commit", "-m", message], wt_path, env)


def _merge_into_integration(
    job: Job, repo: Path, tmp_path: Path, env: dict[str, str], no_ff: bool = False
) -> None:
    int_wt = tmp_path / "integration-wt"
    _git(["worktree", "add", "-b", "integration", str(int_wt), "main"], repo, env)
    if no_ff:
        _git(["merge", "--no-ff", job.branch, "-m", "merge job branch"], int_wt, env)
    else:
        _git(["merge", "--ff-only", job.branch], int_wt, env)
    _git(["worktree", "remove", str(int_wt)], repo, env, check=False)


def test_cleanup_allows_branch_merged_into_head(
    tmp_path: Path, safe_git_env: dict[str, str]
) -> None:
    job, repo, _wt = _make_job(tmp_path, "Merged Into Head", safe_git_env)
    _commit(job.worktree_path, "feature.txt", "feature: add file", safe_git_env)
    _git(["merge", "--ff-only", job.branch], repo, safe_git_env)

    resp = cleanup_worktree(job, remove_worktree_flag=True, delete_branch_flag=True)

    assert resp.worktree_removed is True
    assert resp.branch_deleted is True
    remaining = _git(["branch", "--list", job.branch], repo, safe_git_env)
    assert job.branch not in remaining.stdout


def test_cleanup_allows_branch_merged_into_other_branch_only(
    tmp_path: Path, safe_git_env: dict[str, str]
) -> None:
    """Merged into integration while the source repo HEAD is still main."""
    job, repo, _wt = _make_job(tmp_path, "Merged Into Integration", safe_git_env)
    _commit(job.worktree_path, "feature.txt", "feature: add file", safe_git_env)
    _merge_into_integration(job, repo, tmp_path, safe_git_env)

    head = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo, safe_git_env)
    assert head.stdout.strip() == "main"

    resp = cleanup_worktree(job, remove_worktree_flag=True, delete_branch_flag=True)

    assert resp.branch_deleted is True
    remaining = _git(["branch", "--list", job.branch], repo, safe_git_env)
    assert job.branch not in remaining.stdout


def test_cleanup_allows_branch_merged_with_noff_merge_commit(
    tmp_path: Path, safe_git_env: dict[str, str]
) -> None:
    job, repo, _wt = _make_job(tmp_path, "No FF Merge", safe_git_env)
    _commit(job.worktree_path, "feature.txt", "feature: add file", safe_git_env)
    _merge_into_integration(job, repo, tmp_path, safe_git_env, no_ff=True)

    resp = cleanup_worktree(job, remove_worktree_flag=True, delete_branch_flag=True)

    assert resp.branch_deleted is True


def test_cleanup_refuses_branch_not_merged_anywhere(
    tmp_path: Path, safe_git_env: dict[str, str]
) -> None:
    job, repo, wt = _make_job(tmp_path, "Not Merged", safe_git_env)
    _commit(wt, "feature.txt", "feature: add file", safe_git_env)

    with pytest.raises(RuntimeError, match="Refusing to delete branch .* unmerged commits"):
        cleanup_worktree(job, remove_worktree_flag=True, delete_branch_flag=True)

    remaining = _git(["branch", "--list", job.branch], repo, safe_git_env)
    assert job.branch in remaining.stdout


def test_manager_cleanup_reports_unmerged_commits_error_code(
    tmp_path: Path, safe_git_env: dict[str, str]
) -> None:
    job, repo, wt = _make_job(tmp_path, "Unmerged Error Code", safe_git_env)
    _commit(wt, "feature.txt", "feature: add file", safe_git_env)
    state_dir = tmp_path / "state"
    manager = JobManager(
        config=Config(),
        runner=LocalProcessRunner(executable=FAKE_AGY),
        base_state_dir=state_dir,
        home=tmp_path,
    )

    with pytest.raises(ManagerError) as exc_info:
        manager.cleanup(job.job_id, remove_worktree=True, delete_branch=True)

    assert exc_info.value.code == "unmerged_commits"
    assert get_job(job.job_id, state_dir).state == JobState.DONE
    remaining = _git(["branch", "--list", job.branch], repo, safe_git_env)
    assert job.branch in remaining.stdout


def test_cleanup_force_deletes_unmerged_branch(
    tmp_path: Path, safe_git_env: dict[str, str]
) -> None:
    job, repo, wt = _make_job(tmp_path, "Force Unmerged", safe_git_env)
    _commit(wt, "feature.txt", "feature: add file", safe_git_env)

    resp = cleanup_worktree(job, remove_worktree_flag=True, delete_branch_flag=True, force=True)

    assert resp.branch_deleted is True
    remaining = _git(["branch", "--list", job.branch], repo, safe_git_env)
    assert job.branch not in remaining.stdout


def test_cleanup_remove_worktree_only_keeps_branch(
    tmp_path: Path, safe_git_env: dict[str, str]
) -> None:
    job, repo, wt = _make_job(tmp_path, "Remove Worktree Only", safe_git_env)
    _commit(wt, "feature.txt", "feature: add file", safe_git_env)

    resp = cleanup_worktree(job, remove_worktree_flag=True, delete_branch_flag=False)

    assert resp.worktree_removed is True
    assert resp.branch_deleted is False
    remaining = _git(["branch", "--list", job.branch], repo, safe_git_env)
    assert job.branch in remaining.stdout


def test_cleanup_refuses_squash_merged_branch(
    tmp_path: Path, safe_git_env: dict[str, str]
) -> None:
    """Squash/cherry-pick merges do not make the original commits reachable.

    They stay refused on purpose: the branch may carry work the squashed commit
    dropped, and git cannot prove the commits were preserved.
    """
    job, repo, wt = _make_job(tmp_path, "Squash Merged", safe_git_env)
    _commit(wt, "feature.txt", "feature: add file", safe_git_env)

    _git(["merge", "--squash", job.branch], repo, safe_git_env)
    _git(["commit", "-m", "squashed feature"], repo, safe_git_env)
    head_log = _git(["log", "--format=%s", "-n", "1"], repo, safe_git_env)
    assert head_log.stdout.strip() == "squashed feature"

    with pytest.raises(RuntimeError, match="Refusing to delete branch .* unmerged commits"):
        cleanup_worktree(job, remove_worktree_flag=True, delete_branch_flag=True)


def test_cleanup_allows_merged_branch_from_detached_head(
    tmp_path: Path, safe_git_env: dict[str, str]
) -> None:
    """A detached source HEAD must not make the merged-commit check fail."""
    job, repo, _wt = _make_job(tmp_path, "Detached Head", safe_git_env)
    _commit(job.worktree_path, "feature.txt", "feature: add file", safe_git_env)
    _merge_into_integration(job, repo, tmp_path, safe_git_env)
    _git(["checkout", "--detach", "main"], repo, safe_git_env)

    resp = cleanup_worktree(job, remove_worktree_flag=True, delete_branch_flag=True)

    assert resp.branch_deleted is True
