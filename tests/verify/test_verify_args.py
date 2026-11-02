"""Tests for F9: verify command arguments are restricted and revalidated on resume."""

from pathlib import Path

import pytest

from agy_mcp.config import Config
from agy_mcp.jobs import create_job, get_job_dir, save_job
from agy_mcp.manager import JobManager, ManagerError
from agy_mcp.models import JobState
from agy_mcp.verify import is_verify_allowed
from tests.helpers.make_repo import make_repo

DEFAULT_ALLOWLIST = ["pytest", "ruff", "npm test", "cargo test", "go test"]


def test_verify_rejects_dangerous_flags() -> None:
    """is_verify_allowed must reject dangerous flags like -c, --rootdir, -p, --prefix."""
    assert is_verify_allowed("pytest -c /tmp/evil.ini", DEFAULT_ALLOWLIST) is False
    assert is_verify_allowed("pytest --rootdir=/home/other", DEFAULT_ALLOWLIST) is False
    assert is_verify_allowed("pytest -p evilplugin", DEFAULT_ALLOWLIST) is False
    assert is_verify_allowed("npm test --prefix /home/other", DEFAULT_ALLOWLIST) is False
    assert is_verify_allowed("pytest -o evil=1", DEFAULT_ALLOWLIST) is False


def test_verify_rejects_paths_outside_worktree(tmp_path: Path) -> None:
    """is_verify_allowed must reject paths outside the worktree / repo."""
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()

    # Outside absolute path must be rejected
    assert is_verify_allowed(
        f"pytest {outside_dir / 'test_evil.py'}",
        DEFAULT_ALLOWLIST,
        worktree_path=repo_dir,
    ) is False

    # Path inside repo must be accepted
    inside_test = repo_dir / "tests" / "test_ok.py"
    assert is_verify_allowed(
        f"pytest {inside_test}",
        DEFAULT_ALLOWLIST,
        worktree_path=repo_dir,
    ) is True


def test_resume_revalidates_verify_allowlist(tmp_path: Path) -> None:
    """manager.resume must revalidate job.verify and reject tampered unallowed commands."""
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    manager = JobManager(
        config=Config(verify_allowlist=["pytest"]),
        base_state_dir=state_dir,
        home=tmp_path,
    )

    job = create_job(
        repo_path=repo_dir,
        title="F9 Resume Verify Test",
        prompt="hello",
        model="gemini-3.8-flash-medium",
        max_minutes=10,
        base_state_dir=state_dir,
    )
    job_dir = get_job_dir(job.job_id, state_dir)
    from agy_mcp.audit import save_snapshot, take_snapshot
    save_snapshot(take_snapshot(job, home=tmp_path), job_dir / "snapshot.json")

    job.state = JobState.NEEDS_DECISION
    # Tamper job.verify in state to an unlisted command
    job.verify = "rm -rf /"
    save_job(job, state_dir)

    # resume must fail closed and reject the unlisted verify command
    with pytest.raises(ManagerError) as exc_info:
        manager.resume(job.job_id, "answer")
    assert "verify" in str(exc_info.value).lower()


def test_verify_flag_restrictions_per_tool() -> None:
    """Verify dangerous flag rules are applied per-tool per Item 2."""
    assert is_verify_allowed("cargo test -p mycrate", DEFAULT_ALLOWLIST) is True
    assert (
        is_verify_allowed('cargo test --config build.rustc-wrapper="x"', DEFAULT_ALLOWLIST)
        is False
    )
    assert is_verify_allowed("go test -p 4 ./...", DEFAULT_ALLOWLIST) is True
    assert is_verify_allowed("go test -exec ./evil ./...", DEFAULT_ALLOWLIST) is False
    assert is_verify_allowed("pytest -p evilplugin", DEFAULT_ALLOWLIST) is False
    assert is_verify_allowed("npm test -- --foo", DEFAULT_ALLOWLIST) is False

