"""Tests for finding F27: dead code removal and deduplication."""

import inspect

from agy_mcp import verify, watcher, worktree
from agy_mcp.manager import JobManager


def test_verify_error_dead_code_removed() -> None:
    """verify.VerifyError is completely unused and must be removed."""
    assert not hasattr(verify, "VerifyError")


def test_worktree_sanitize_slug_dead_code_removed() -> None:
    """worktree.sanitize_slug is completely unused and must be removed."""
    assert not hasattr(worktree, "sanitize_slug")


def test_watcher_check_once_dead_code_removed() -> None:
    """watcher.CommitWatcher.check_once is an unused alias and must be removed."""
    assert not hasattr(watcher.CommitWatcher, "check_once")


def test_manager_status_does_not_duplicate_files_modified() -> None:
    """manager.status source code must not duplicate the get_files_modified block."""
    src = inspect.getsource(JobManager.status)
    assert src.count("job.files_modified = get_files_modified(") == 1
