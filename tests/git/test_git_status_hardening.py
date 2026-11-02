"""Tests for F6: git status calls are hardened and fail closed."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from agy_mcp.gitops import GitError, run_git, stage_changes
from tests.helpers.make_repo import make_repo


def test_stage_changes_status_failure_fails_closed_and_unstages(tmp_path: Path) -> None:
    """When git status fails in stage_changes, fail closed and unstage secrets, not commit."""
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)

    # Create secret .env file
    env_file = repo_dir / ".env"
    env_file.write_text("SECRET_KEY=12345\n")

    # Mock subprocess.run for git status to simulate status failure
    orig_run = subprocess.run

    def mock_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and "status" in cmd:
            return subprocess.CompletedProcess(
                cmd, returncode=128, stdout=b"", stderr=b"fatal: corrupted git status"
            )
        return orig_run(cmd, *args, **kwargs)

    with patch("subprocess.run", side_effect=mock_run):
        # Must fail closed: raise GitError and NOT leave .env staged in index
        with pytest.raises(GitError):
            stage_changes(repo_dir)

    # Verify that .env is NOT staged in the index
    diff_res = run_git(["diff", "--cached", "--name-only"], repo_dir)
    assert ".env" not in diff_res.stdout, "Secret .env was left staged after git status failure!"


def test_is_worktree_dirty_status_failure_fails_closed(tmp_path: Path) -> None:
    """When git status fails in is_worktree_dirty, it must raise GitError, not claim clean."""
    from agy_mcp.gitops import is_worktree_dirty

    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)

    orig_run = subprocess.run

    def mock_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and "status" in cmd:
            return subprocess.CompletedProcess(
                cmd, returncode=128, stdout=b"", stderr=b"fatal: status failed"
            )
        return orig_run(cmd, *args, **kwargs)

    with patch("subprocess.run", side_effect=mock_run):
        with pytest.raises(GitError):
            is_worktree_dirty(repo_dir)


def test_safety_net_commit_status_failure_fails_closed(tmp_path: Path) -> None:
    """When status fails in safety_net_commit, raise GitError, not silently return None."""
    from agy_mcp.gitops import safety_net_commit

    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)

    orig_run = subprocess.run

    def mock_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and "status" in cmd:
            return subprocess.CompletedProcess(
                cmd, returncode=128, stdout=b"", stderr=b"fatal: status failed"
            )
        return orig_run(cmd, *args, **kwargs)

    with patch("subprocess.run", side_effect=mock_run):
        with pytest.raises(GitError):
            safety_net_commit(repo_dir, "Safety Net", audit_passed=True)
