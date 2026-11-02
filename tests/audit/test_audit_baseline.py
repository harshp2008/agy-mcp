"""Tests for F2: audit baseline fails open; review and recovery compare against themselves."""

import json
from pathlib import Path

import pytest

from agy_mcp.audit import save_snapshot, take_snapshot
from agy_mcp.config import Config
from agy_mcp.jobs import create_job, get_job_dir, save_job
from agy_mcp.manager import JobManager, ManagerError
from agy_mcp.models import JobState
from agy_mcp.recovery import reconcile_on_startup
from agy_mcp.worktree import create_worktree
from tests.helpers.make_repo import make_repo


def test_review_uses_persisted_baseline_detects_tamper(tmp_path: Path) -> None:
    """review() must compare against persisted baseline, detecting host tampering."""
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True)
    bashrc = fake_home / ".bashrc"
    bashrc.write_text("# initial bashrc\n")

    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)

    manager = JobManager(
        config=Config(),
        base_state_dir=state_dir,
        home=fake_home,
    )

    job = create_job(
        repo_path=repo_dir,
        title="F2 Review Test",
        prompt="hello",
        model="gemini-3.8-flash-medium",
        max_minutes=10,
        base_state_dir=state_dir,
    )
    job_dir = get_job_dir(job.job_id, state_dir)
    job.worktree_path, _, _ = create_worktree(repo_dir, job_dir, job.title)
    job.state = JobState.DONE
    save_job(job, state_dir)
    baseline = take_snapshot(job, home=fake_home)
    save_snapshot(baseline, job_dir / "snapshot.json")

    # Tamper with .bashrc after baseline was recorded
    bashrc.write_text("# malicious payload\n")

    rev = manager.review(job.job_id)
    # Must detect host_path_modified audit violation
    assert len(rev.audit_findings) > 0, (
        "review() failed to detect tampering (compared against self)"
    )
    assert any(f["reason"] == "host_path_modified" for f in rev.audit_findings)


def test_recovery_uses_persisted_baseline_detects_tamper(tmp_path: Path) -> None:
    """recovery must compare against persisted baseline and transition to needs_review on tamper."""
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True)
    bashrc = fake_home / ".bashrc"
    bashrc.write_text("# initial bashrc\n")

    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)

    job = create_job(
        repo_path=repo_dir,
        title="F2 Recovery Test",
        prompt="hello",
        model="gemini-3.8-flash-medium",
        max_minutes=10,
        base_state_dir=state_dir,
    )
    job_dir = get_job_dir(job.job_id, state_dir)
    job.worktree_path, _, _ = create_worktree(repo_dir, job_dir, job.title)
    job.state = JobState.RUNNING
    job.pid = 999999  # dead pid
    save_job(job, state_dir)
    baseline = take_snapshot(job, home=fake_home)
    save_snapshot(baseline, job_dir / "snapshot.json")

    # Simulate fake agy stdout indicating success
    stdout_file = job_dir / "stdout.txt"
    stdout_file.write_text(json.dumps({
        "status": "SUCCESS",
        "response": "Job done",
    }))

    # Tamper with .bashrc after baseline was recorded
    bashrc.write_text("# malicious payload\n")

    reconciled = reconcile_on_startup(state_dir=state_dir, home=fake_home)
    assert len(reconciled) == 1
    rec_job = reconciled[0]
    assert rec_job.state == JobState.NEEDS_REVIEW
    assert rec_job.review_reason == "host_path_modified"


def test_corrupt_or_missing_snapshot_fails_closed(tmp_path: Path) -> None:
    """Missing or corrupted snapshot must fail closed in review, recovery, and resume."""
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True)

    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)

    manager = JobManager(
        config=Config(),
        base_state_dir=state_dir,
        home=fake_home,
    )

    job = create_job(
        repo_path=repo_dir,
        title="F2 Corrupt Snapshot Test",
        prompt="hello",
        model="gemini-3.8-flash-medium",
        max_minutes=10,
        base_state_dir=state_dir,
    )
    job.state = JobState.NEEDS_REVIEW
    job.review_reason = "verify_failed"
    save_job(job, state_dir)

    job_dir = get_job_dir(job.job_id, state_dir)
    # Corrupt snapshot file (e.g. truncated by git diff --output)
    (job_dir / "snapshot.json").write_text("{invalid json truncated")

    # 1. manager.review must report fatal finding, not pass
    rev = manager.review(job.job_id)
    assert len(rev.audit_findings) > 0
    assert any(f["reason"] == "audit_violation" for f in rev.audit_findings)

    # 2. manager.resume must fail closed with ManagerError
    with pytest.raises(ManagerError) as exc_info:
        manager.resume(job.job_id, "resume prompt")
    assert "snapshot" in str(exc_info.value).lower() or "audit" in str(exc_info.value).lower()
