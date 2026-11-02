"""Tests for F18: job.skipped_files must be populated from StagingResult."""

from pathlib import Path

from agy_mcp.gitops import checkpoint_commit, safety_net_commit
from agy_mcp.jobs import JobState, create_job, get_job_dir
from agy_mcp.models import build_standard_envelope
from agy_mcp.watcher import CommitWatcher
from tests.helpers.make_repo import make_repo


def test_watcher_populates_job_skipped_files(tmp_path: Path) -> None:
    """When watcher encounters sensitive or junk files, job.skipped_files must be populated."""
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)

    # Add a tracked sensitive file into git repo HEAD
    env_file = repo_dir / ".env"
    env_file.write_text("SECRET=123", encoding="utf-8")
    import subprocess
    subprocess.run(["git", "add", ".env"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "add tracked .env"], cwd=repo_dir, check=True)

    state_dir = tmp_path / "state"
    state_dir.mkdir()

    job = create_job(
        repo_path=repo_dir,
        title="F18 Test Watcher",
        prompt="hello",
        model="gemini-3.8-flash-medium",
        max_minutes=10,
        base_state_dir=state_dir,
    )

    from agy_mcp.worktree import create_worktree
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
    job.state = JobState.RUNNING

    # Simulate agent modifying tracked .env and a regular file
    (wt_path / ".env").write_text("SECRET=456", encoding="utf-8")
    (wt_path / "code.py").write_text("print('hello')", encoding="utf-8")

    # Provide COMMIT_MSG
    agy_dir = wt_path / ".agy"
    agy_dir.mkdir(parents=True, exist_ok=True)
    (agy_dir / "COMMIT_MSG").write_text("feat: change code", encoding="utf-8")

    watcher = CommitWatcher(
        job=job,
        base_state_dir=state_dir,
        settle_window_seconds=0.0,
        is_process_alive=lambda: True,
    )
    watcher.tick(now=100.0)

    # Job must have skipped_files populated with .env and reason sensitive
    assert len(job.skipped_files) > 0, "job.skipped_files was not populated by watcher"
    assert any(
        (sf.get("path") == ".env" and sf.get("reason") == "sensitive")
        if isinstance(sf, dict) else sf == ".env"
        for sf in job.skipped_files
    )

    # Check status and result envelope
    status_env = build_standard_envelope(job, elapsed_seconds=5.0)
    assert len(status_env.skipped_files) > 0
    assert any(".env" in str(sf) for sf in status_env.skipped_files)


def test_safety_net_populates_job_skipped_files(tmp_path: Path) -> None:
    """Safety net commit must populate job.skipped_files when files are skipped."""
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    job = create_job(
        repo_path=repo_dir,
        title="F18 Test Safety Net",
        prompt="hello",
        model="gemini-3.8-flash-medium",
        max_minutes=10,
        base_state_dir=state_dir,
    )
    from agy_mcp.worktree import create_worktree
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

    # Create untracked junk/sensitive files and a regular file
    (wt_path / ".coverage").write_text("junk", encoding="utf-8")
    (wt_path / "main.py").write_text("print('ok')", encoding="utf-8")

    safety_net_commit(
        worktree_path=wt_path,
        title=job.title,
        job_dir=job_dir,
        job=job,
    )

    assert len(job.skipped_files) > 0, "job.skipped_files was not populated by safety net"
    assert any(".coverage" in str(sf) for sf in job.skipped_files)


def test_checkpoint_populates_job_skipped_files(tmp_path: Path) -> None:
    """Checkpoint commit must populate job.skipped_files when files are skipped."""
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    job = create_job(
        repo_path=repo_dir,
        title="F18 Test Checkpoint",
        prompt="hello",
        model="gemini-3.8-flash-medium",
        max_minutes=10,
        base_state_dir=state_dir,
    )
    from agy_mcp.worktree import create_worktree
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

    (wt_path / ".coverage").write_text("junk", encoding="utf-8")
    (wt_path / "test_file.py").write_text("x = 1", encoding="utf-8")

    checkpoint_commit(
        worktree_path=wt_path,
        job=job,
    )

    assert len(job.skipped_files) > 0, "job.skipped_files was not populated by checkpoint"
    assert any(".coverage" in str(sf) for sf in job.skipped_files)
