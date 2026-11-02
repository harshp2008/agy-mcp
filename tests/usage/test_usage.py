"""Unit tests for locked usage logging and audit events per SPEC section 17."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agy_mcp.models import Job
from agy_mcp.states import JobState
from agy_mcp.usage import append_audit_event, check_daily_cap, record_usage


def test_record_usage_no_prompt_text_and_permissions(tmp_path: Path):
    """usage.jsonl records summary without prompt text, with 0600 permissions."""
    secret_prompt = "My super secret password and API token: sk-xyz123"
    job = Job(
        job_id="job-usage-1",
        title="Refactor auth",
        repo_path=tmp_path / "repo",
        branch="agy/refactor-auth",
        base_commit="sha1",
        state=JobState.DONE,
        model="gemini-3.8-flash-high",
        commits_count=3,
        cumulative_minutes=25.4,
    )
    usage_file = tmp_path / "usage.jsonl"

    record_usage(
        usage_path=usage_file,
        job=job,
        duration_seconds=120.5,
        tokens={"input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200},
    )

    assert usage_file.is_file()
    assert (usage_file.stat().st_mode & 0o777) == 0o600

    content = usage_file.read_text(encoding="utf-8")
    assert secret_prompt not in content
    assert "prompt" not in content

    data = json.loads(content.strip())
    assert data["job_id"] == "job-usage-1"
    assert data["state"] == "done"
    assert data["model"] == "gemini-3.8-flash-high"
    assert data["duration_seconds"] == 120.5
    assert data["cumulative_minutes"] == 25.4
    assert data["commits_count"] == 3
    assert data["tokens"]["total_tokens"] == 1200
    assert "prompt" not in data


def test_concurrent_usage_recording(tmp_path: Path):
    """Multiple concurrent jobs safely append to usage.jsonl under flock."""
    usage_file = tmp_path / "usage.jsonl"
    job_count = 20

    def log_one(i: int):
        j = Job(
            job_id=f"job-{i}",
            title=f"Title {i}",
            repo_path=tmp_path / f"repo-{i}",
            branch=f"agy/branch-{i}",
            base_commit="sha",
            state=JobState.DONE,
            model="gemini-3.8-flash-low",
            commits_count=i,
            cumulative_minutes=float(i),
        )
        record_usage(
            usage_path=usage_file,
            job=j,
            duration_seconds=float(i * 10),
            tokens={"input_tokens": i * 100, "output_tokens": i * 10, "total_tokens": i * 110},
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(log_one, range(job_count)))

    lines = [line.strip() for line in usage_file.read_text().splitlines() if line.strip()]
    assert len(lines) == job_count

    job_ids = {json.loads(line)["job_id"] for line in lines}
    assert job_ids == {f"job-{i}" for i in range(job_count)}


def test_append_audit_event(tmp_path: Path):
    """append_audit_event appends valid JSON records to audit.jsonl with 0600 mode."""
    job_dir = tmp_path / "job_dir"
    append_audit_event(job_dir, "test_event", {"key": "val1"})
    append_audit_event(job_dir, "test_event_2", {"key": "val2"})

    audit_file = job_dir / "audit.jsonl"
    assert audit_file.is_file()
    assert (audit_file.stat().st_mode & 0o777) == 0o600

    records = [json.loads(line) for line in audit_file.read_text().splitlines() if line.strip()]
    assert len(records) == 2
    assert records[0]["event"] == "test_event"
    assert records[0]["detail"]["key"] == "val1"
    assert records[1]["event"] == "test_event_2"
    assert records[1]["detail"]["key"] == "val2"


def test_check_daily_cap_delegation(tmp_path: Path):
    """check_daily_cap properly gates job starts."""
    state_dir = tmp_path / "state"
    assert check_daily_cap(state_dir, daily_cap=2) is True
    assert check_daily_cap(state_dir, daily_cap=2) is True
    assert check_daily_cap(state_dir, daily_cap=2) is False
