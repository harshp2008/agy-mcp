"""Tests for files_modified tracking and 200-entry cap per SPEC sections 8, 10."""

from pathlib import Path

from agy_mcp.gitops import get_files_modified, run_git
from agy_mcp.manager import JobManager
from agy_mcp.models import JobState
from agy_mcp.runner import LocalProcessRunner
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"


def test_get_files_modified_basic_and_capped(tmp_path: Path, safe_git_env) -> None:
    """get_files_modified returns diff --name-only list capped at 200 entries."""
    repo = make_repo(tmp_path / "repo", env=safe_git_env)
    base_sha = run_git(["rev-parse", "HEAD"], repo, env=safe_git_env).stdout.strip()

    # Create 250 files
    for i in range(250):
        (repo / f"file_{i:03d}.txt").write_text(f"content {i}\n", encoding="utf-8")

    run_git(["add", "."], repo, env=safe_git_env)
    run_git(["commit", "-m", "Add 250 files"], repo, env=safe_git_env)
    head_sha = run_git(["rev-parse", "HEAD"], repo, env=safe_git_env).stdout.strip()

    modified = get_files_modified(repo, base_sha, head_sha, cap=200)
    assert len(modified) == 200
    assert modified[0] == "file_000.txt"
    assert modified[-1] == "file_199.txt"

    # Default cap is 200
    modified_default = get_files_modified(repo, base_sha, head_sha)
    assert len(modified_default) == 200


def test_files_modified_in_job_execution(tmp_path: Path) -> None:
    """Running a job with commits populates files_modified in the job result."""
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)

    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    runner = LocalProcessRunner(executable=FAKE_AGY)
    manager = JobManager(
        runner=runner,
        base_state_dir=state_dir,
        home=tmp_path,
        settle_window_seconds=0.1,
    )

    env = manager.start(
        prompt="Create files __SCENARIO:success_with_commits__",
        repo_path=str(repo_dir),
        title="Files Modified Test",
        model="gemini-3.8-flash-medium",
    )

    res = manager.wait(job_id=env.job_id, seconds=10)
    assert res.state == JobState.DONE
    assert res.commits_count >= 1

    status = manager.status(env.job_id)
    assert "src_feature.py" in status.files_modified

    result = manager.result(env.job_id)
    assert "src_feature.py" in result.files_modified
