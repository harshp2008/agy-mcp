"""Unit tests for job persistence, atomic store, audit logging, and state machine."""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agy_mcp.jobs import (
    _atomic_write_file,
    check_and_increment_daily_cap,
    count_active_jobs,
    create_job,
    get_job,
    get_job_dir,
    get_job_disk_usage,
    list_jobs,
    prune_retained_jobs,
    rebuild_daily_cap_from_audit,
    record_job_execution_time,
    save_job,
    transition_job_state,
)
from agy_mcp.states import JobState


def test_create_and_load_job_permissions(tmp_path: Path):
    """Job creation provisions state dir (0700) and files (0600) per SPEC section 10."""
    job = create_job(
        repo_path=tmp_path / "repo",
        title="Test Permissions",
        prompt="Write secure code",
        model="gemini-3.8-flash-low",
        max_minutes=60,
        verify="pytest",
        base_state_dir=tmp_path,
    )

    job_dir = get_job_dir(job.job_id, tmp_path)
    assert job_dir.is_dir()
    # Directory permissions 0700
    assert (job_dir.stat().st_mode & 0o777) == 0o700

    tmp_dir = job_dir / "tmp"
    assert tmp_dir.is_dir()
    assert (tmp_dir.stat().st_mode & 0o777) == 0o700

    prompt_file = job_dir / "prompt.txt"
    assert prompt_file.is_file()
    assert (prompt_file.stat().st_mode & 0o777) == 0o600

    job_file = job_dir / "job.json"
    assert job_file.is_file()
    assert (job_file.stat().st_mode & 0o777) == 0o600

    audit_file = job_dir / "audit.jsonl"
    assert audit_file.is_file()
    assert (audit_file.stat().st_mode & 0o777) == 0o600

    loaded = get_job(job.job_id, tmp_path)
    assert loaded.job_id == job.job_id
    assert loaded.title == "Test Permissions"
    assert loaded.state == JobState.QUEUED


def test_atomic_write_crash_simulation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """If a write fails before atomic replace, the original file remains intact."""
    target_file = tmp_path / "important.json"
    target_file.write_text('{"original": true}')

    def failing_replace(src, dst):
        raise OSError("Simulated crash during atomic replace")

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError, match="Simulated crash"):
        _atomic_write_file(target_file, '{"corrupted": true}')

    # Target file content must still be intact
    assert target_file.read_text() == '{"original": true}'


def test_state_transitions_allowed_and_audit_trail(tmp_path: Path):
    """Allowed state transitions update job.state and record audit events."""
    job = create_job(
        repo_path=tmp_path / "repo",
        title="Transition Test",
        prompt="Test state transitions",
        model="gemini-3.8-flash-medium",
        max_minutes=120,
        base_state_dir=tmp_path,
    )
    assert job.state == JobState.QUEUED

    # 1. queued -> running
    transition_job_state(
        job,
        JobState.RUNNING,
        event_trigger="Worktree ready, process spawned & PID confirmed",
        base_state_dir=tmp_path,
    )
    assert job.state == JobState.RUNNING

    # 2. running -> needs_decision
    transition_job_state(
        job,
        JobState.NEEDS_DECISION,
        event_trigger="Last non-empty line is NEEDS_DECISION:",
        detail={"prompt": "Choose option A or B"},
        base_state_dir=tmp_path,
    )
    assert job.state == JobState.NEEDS_DECISION

    # 3. needs_decision -> running (resume)
    transition_job_state(
        job,
        JobState.RUNNING,
        event_trigger="agy_resume(job_id, answer)",
        base_state_dir=tmp_path,
    )
    assert job.state == JobState.RUNNING

    # 4. running -> needs_review (operational: verification_failed)
    transition_job_state(
        job,
        JobState.NEEDS_REVIEW,
        event_trigger="Verify attempts exhausted (verify_allowlist)",
        detail={"review_reason": "verification_failed"},
        base_state_dir=tmp_path,
    )
    assert job.state == JobState.NEEDS_REVIEW
    assert job.review_reason == "verification_failed"

    # 5. needs_review -> running (allowed for operational reason)
    transition_job_state(
        job,
        JobState.RUNNING,
        event_trigger="agy_resume(job_id, answer)",
        base_state_dir=tmp_path,
    )
    assert job.state == JobState.RUNNING
    assert job.review_reason is None

    # 6. running -> done (terminal)
    transition_job_state(
        job,
        JobState.DONE,
        event_trigger="Clean exit 0, audit passes, verify passes/none",
        base_state_dir=tmp_path,
    )
    assert job.state == JobState.DONE

    # Verify audit.jsonl contains all transitions
    job_dir = get_job_dir(job.job_id, tmp_path)
    with open(job_dir / "audit.jsonl", "r", encoding="utf-8") as f:
        events = [line.strip() for line in f if line.strip()]

    # 1 job_created + 6 transitions = 7 events
    assert len(events) == 7


# Independently declared follow-up edges (done/cancelled -> running). The test
# must not derive follow_up from the row's own flag: if a row lost that flag the
# transition would fail, and a row that wrongly had it would be caught here.
FOLLOW_UP_EDGES = {
    (JobState.DONE, JobState.RUNNING),
    (JobState.CANCELLED, JobState.RUNNING),
}


def test_all_sixteen_state_transitions_individually(tmp_path: Path):
    """Test all 16 transitions in TRANSITION_TABLE individually."""
    # Test each transition starting from an appropriately initialized job
    from agy_mcp.states import TRANSITION_TABLE

    for idx, t in enumerate(TRANSITION_TABLE):
        job = create_job(
            repo_path=tmp_path / "repo",
            title=f"Transition-{idx}",
            prompt="test",
            model="gemini-3.8-flash-low",
            max_minutes=60,
            base_state_dir=tmp_path,
            job_id=f"job-trans-{idx}",
        )
        # Put job into current_state
        job.state = t.current_state
        if t.current_state == JobState.NEEDS_REVIEW:
            job.review_reason = "verification_failed"  # non-security so resumable
        save_job(job, tmp_path)

        is_follow_up = (t.current_state, t.target_state) in FOLLOW_UP_EDGES
        if is_follow_up:
            assert t.explicit_follow_up_only is True, (
                f"{t.current_state.value}->{t.target_state.value} must be an "
                "explicit follow_up edge"
            )
        else:
            assert t.explicit_follow_up_only is False, (
                f"{t.current_state.value}->{t.target_state.value} must not be a "
                "follow_up edge"
            )

        # Execute transition
        detail = {}
        if "reason" in t.notes:
            # extract reason if present
            if "audit_violation" in t.notes:
                detail["review_reason"] = "audit_violation"
            elif "verification_failed" in t.notes:
                detail["review_reason"] = "verification_failed"
            elif "blocked_by_permission" in t.notes:
                detail["review_reason"] = "blocked_by_permission"
            elif "invalid_commit_message" in t.notes:
                detail["review_reason"] = "invalid_commit_message"

        updated = transition_job_state(
            job,
            t.target_state,
            event_trigger=t.event_trigger,
            detail=detail,
            base_state_dir=tmp_path,
            follow_up=is_follow_up,
        )
        assert updated.state == t.target_state
        assert get_job(job.job_id, tmp_path).state == t.target_state


def test_disallowed_state_transitions(tmp_path: Path):
    """Terminal states cannot transition; illegal jumps raise ValueError."""
    job = create_job(
        repo_path=tmp_path / "repo",
        title="Illegal Transitions",
        prompt="test",
        model="gemini-3.8-flash-low",
        max_minutes=60,
        base_state_dir=tmp_path,
    )

    # Illegal direct jump: queued -> done
    with pytest.raises(ValueError, match="Illegal state transition from queued to done"):
        transition_job_state(
            job,
            JobState.DONE,
            event_trigger="Clean exit 0",
            base_state_dir=tmp_path,
        )

    # Transition to running then done
    transition_job_state(
        job,
        JobState.RUNNING,
        event_trigger="Worktree ready",
        base_state_dir=tmp_path,
    )
    transition_job_state(
        job,
        JobState.DONE,
        event_trigger="Clean exit 0",
        base_state_dir=tmp_path,
    )

    # Done is terminal: done -> running is forbidden
    with pytest.raises(ValueError, match="Illegal state transition from done to running"):
        transition_job_state(
            job,
            JobState.RUNNING,
            event_trigger="resume",
            base_state_dir=tmp_path,
        )


@pytest.mark.parametrize(
    "sec_reason",
    ["audit_violation", "host_path_modified", "instruction_file_modified"],
)
def test_security_needs_review_is_terminal(tmp_path: Path, sec_reason: str):
    """Resuming a job from needs_review with a security reason is strictly refused."""
    job = create_job(
        repo_path=tmp_path / "repo",
        title="Security Review",
        prompt="test",
        model="gemini-3.8-flash-low",
        max_minutes=60,
        base_state_dir=tmp_path,
    )
    transition_job_state(
        job,
        JobState.RUNNING,
        event_trigger="Process spawned",
        base_state_dir=tmp_path,
    )
    transition_job_state(
        job,
        JobState.NEEDS_REVIEW,
        event_trigger="Security violation",
        detail={"review_reason": sec_reason},
        base_state_dir=tmp_path,
    )

    with pytest.raises(
        ValueError,
        match=f"Cannot resume job in needs_review with terminal security reason '{sec_reason}'",
    ):
        transition_job_state(
            job,
            JobState.RUNNING,
            event_trigger="agy_resume(job_id, answer)",
            base_state_dir=tmp_path,
        )


def test_cumulative_budget_accounting(tmp_path: Path):
    """Cumulative minutes are tracked across calls and checked against budget."""
    job = create_job(
        repo_path=tmp_path / "repo",
        title="Budget Test",
        prompt="test",
        model="gemini-3.8-flash-low",
        max_minutes=100,
        base_state_dir=tmp_path,
    )

    # 40 minutes executed
    assert record_job_execution_time(job, elapsed_minutes=40.0, base_state_dir=tmp_path) is True
    assert job.cumulative_minutes == 40.0

    # Another 50 minutes executed (total 90 < 100)
    assert record_job_execution_time(job, elapsed_minutes=50.0, base_state_dir=tmp_path) is True
    assert job.cumulative_minutes == 90.0

    # Another 15 minutes executed (total 105 >= 100 cap)
    assert record_job_execution_time(job, elapsed_minutes=15.0, base_state_dir=tmp_path) is False
    assert job.cumulative_minutes == 105.0


def test_concurrent_daily_cap_increments(tmp_path: Path):
    """Concurrent threads safely increment daily cap up to limit without race conditions."""
    daily_cap = 15
    state_dir = tmp_path / "state"

    def worker(_):
        return check_and_increment_daily_cap(state_dir, daily_cap=daily_cap)

    # Run 30 concurrent attempts for 15 slots
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(worker, range(30)))

    # Exactly 15 must succeed
    assert results.count(True) == 15
    assert results.count(False) == 15

    # Re-check: subsequent call must also be False
    assert check_and_increment_daily_cap(state_dir, daily_cap=daily_cap) is False


def test_rebuild_daily_cap_from_audit(tmp_path: Path):
    """Rebuilding daily cap scans audit logs and computes accurate job start counts."""
    state_dir = tmp_path / "state"
    target_date = "2026-10-24"

    # Create 3 jobs on target date, 1 job on another date
    for i in range(3):
        j = create_job(
            repo_path=tmp_path / "repo",
            title=f"Job {i}",
            prompt="p",
            model="m",
            max_minutes=10,
            base_state_dir=state_dir,
            job_id=f"job-{i}",
        )
        j_dir = get_job_dir(j.job_id, state_dir)
        # Rewrite audit.jsonl with explicit target_date timestamp
        (j_dir / "audit.jsonl").write_text(
            f'{{"ts": "{target_date}T10:00:00Z", "event": "job_created", "detail": {{}}}}\n'
        )

    # Job on different date
    diff_job = create_job(
        repo_path=tmp_path / "repo",
        title="Old Job",
        prompt="p",
        model="m",
        max_minutes=10,
        base_state_dir=state_dir,
        job_id="job-old",
    )
    diff_dir = get_job_dir(diff_job.job_id, state_dir)
    (diff_dir / "audit.jsonl").write_text(
        '{"ts": "2026-10-20T10:00:00Z", "event": "job_created", "detail": {}}\n'
    )

    rebuilt_count = rebuild_daily_cap_from_audit(state_dir, date_str=target_date)
    assert rebuilt_count == 3


def test_list_jobs_and_concurrency(tmp_path: Path):
    """list_jobs lists all jobs and count_active_jobs counts only RUNNING jobs."""
    j1 = create_job(
        repo_path=tmp_path / "repo",
        title="J1",
        prompt="p",
        model="m",
        max_minutes=10,
        base_state_dir=tmp_path,
        job_id="j1",
    )
    j2 = create_job(
        repo_path=tmp_path / "repo",
        title="J2",
        prompt="p",
        model="m",
        max_minutes=10,
        base_state_dir=tmp_path,
        job_id="j2",
    )
    assert count_active_jobs(tmp_path) == 2

    transition_job_state(j1, JobState.RUNNING, "spawned", base_state_dir=tmp_path)
    assert count_active_jobs(tmp_path) == 2

    transition_job_state(j2, JobState.RUNNING, "spawned", base_state_dir=tmp_path)
    assert count_active_jobs(tmp_path) == 2

    transition_job_state(j1, JobState.DONE, "exit 0", base_state_dir=tmp_path)
    assert count_active_jobs(tmp_path) == 1

    transition_job_state(j2, JobState.DONE, "exit 0", base_state_dir=tmp_path)
    assert count_active_jobs(tmp_path) == 0

    all_jobs = list_jobs(tmp_path)
    assert len(all_jobs) == 2
    assert {j.job_id for j in all_jobs} == {"j1", "j2"}


def test_retention_and_disk_usage(tmp_path: Path):
    """Disk usage computes byte counts and prunable identifies expired terminal jobs."""
    j = create_job(
        repo_path=tmp_path / "repo",
        title="Retention",
        prompt="test",
        model="m",
        max_minutes=10,
        base_state_dir=tmp_path,
        job_id="ret-job",
    )
    job_dir = get_job_dir(j.job_id, tmp_path)
    (job_dir / "large_file.bin").write_bytes(b"0" * 1024)

    usage = get_job_disk_usage(job_dir)
    assert usage >= 1024

    # Running job is never prunable
    assert prune_retained_jobs(tmp_path, retention_days=14) == []

    # Transition to RUNNING then DONE and backdate created_at
    transition_job_state(j, JobState.RUNNING, "spawned", base_state_dir=tmp_path)
    transition_job_state(j, JobState.DONE, "done", base_state_dir=tmp_path)
    j.created_at = "2020-01-01T00:00:00+00:00"
    save_job(j, tmp_path)

    prunable = prune_retained_jobs(tmp_path, retention_days=14)
    assert prunable == ["ret-job"]
