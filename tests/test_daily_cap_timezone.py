"""Tests for finding F19: daily cap timezone configuration and validation before consumption."""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from agy_mcp.config import Config
from agy_mcp.jobs import (
    check_and_increment_daily_cap,
    get_daily_date,
    rebuild_daily_cap_from_audit,
)
from agy_mcp.manager import JobManager, ManagerError
from agy_mcp.runner import LocalProcessRunner
from tests.helpers.make_repo import get_safe_git_env, make_repo


def _setup_manager_and_repo(
    tmp_path: Path, config: Config | None = None
) -> tuple[JobManager, Path]:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = home / "repo"
    make_repo(repo_dir, initial_commit=True, env=get_safe_git_env(home))

    fake_agy_path = Path(__file__).parent / "fake_agy" / "agy"
    runner = LocalProcessRunner(executable=str(fake_agy_path))

    cfg = config or Config()
    mgr = JobManager(
        runner=runner,
        config=cfg,
        base_state_dir=state_dir,
        home=home,
        settle_window_seconds=0.05,
    )
    return mgr, repo_dir


def test_daily_cap_not_consumed_by_rejected_start(tmp_path: Path) -> None:
    """Validation rejections (model, verify, empty prompt) must not consume daily cap slots."""
    cfg = Config(daily_jobs_cap=1)
    manager, repo_dir = _setup_manager_and_repo(tmp_path, config=cfg)

    # 1. Invalid prompt
    with pytest.raises(ManagerError):
        manager.start(prompt="", repo_path=str(repo_dir), title="Bad Prompt")

    # 2. Invalid model
    with pytest.raises(ManagerError):
        manager.start(
            prompt="valid prompt",
            repo_path=str(repo_dir),
            title="Bad Model",
            model="unauthorized-model-xyz",
        )

    # 3. Disallowed verify
    with pytest.raises(ManagerError):
        manager.start(
            prompt="valid prompt",
            repo_path=str(repo_dir),
            title="Bad Verify",
            verify="rm -rf /",
        )

    # Cap file should have count 0 or not exist
    cap_file = manager.state_dir / "daily_cap.json"
    if cap_file.exists():
        stored = json.loads(cap_file.read_text())
        assert stored.get("count", 0) == 0

    # 4. Valid start must succeed because quota was not burned
    env = manager.start(
        prompt="valid prompt",
        repo_path=str(repo_dir),
        title="Valid Job 1",
    )
    assert env.job_id is not None

    # 5. Second start hits cap
    with pytest.raises(ManagerError) as exc_info:
        manager.start(
            prompt="valid prompt",
            repo_path=str(repo_dir),
            title="Valid Job 2",
        )
    assert exc_info.value.code == "cap_exceeded"


def test_daily_cap_uses_local_or_configured_timezone(tmp_path: Path) -> None:
    """Daily cap must compute date based on local time by default, or configured timezone."""
    # Default without tz_name should match local date
    local_date = datetime.now().astimezone().date().isoformat()
    assert get_daily_date() == local_date

    # Configured timezone check
    ny_date = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    assert get_daily_date("America/New_York") == ny_date

    tokyo_date = datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()
    assert get_daily_date("Asia/Tokyo") == tokyo_date

    # check_and_increment_daily_cap honors configured timezone
    state_dir = tmp_path / "state"
    assert check_and_increment_daily_cap(state_dir, 10, tz_name="Asia/Tokyo")

    cap_file = state_dir / "daily_cap.json"
    stored = json.loads(cap_file.read_text())
    assert stored["date"] == tokyo_date
    assert stored["count"] == 1


def test_rebuild_daily_cap_honors_timezone(tmp_path: Path) -> None:
    """Rebuilding daily cap from audit logs must convert audit UTC timestamps to target timezone."""
    state_dir = tmp_path / "state"
    job_dir = state_dir / "jobs" / "job-1"
    job_dir.mkdir(parents=True, exist_ok=True)
    audit_file = job_dir / "audit.jsonl"

    # Suppose a job was created at 2026-10-25T23:30:00Z (UTC)
    # In Asia/Tokyo (+09:00), this is 2026-10-26T08:30:00 (Oct 26)
    ev = {
        "ts": "2026-10-25T23:30:00Z",
        "event": "job_created",
        "detail": {"title": "Tokyo Job"},
    }
    audit_file.write_text(json.dumps(ev) + "\n")

    # In Tokyo timezone, target date 2026-10-26 should match this event
    count_tokyo = rebuild_daily_cap_from_audit(
        state_dir, date_str="2026-10-26", tz_name="Asia/Tokyo"
    )
    assert count_tokyo == 1

    # In UTC timezone, target date 2026-10-26 should NOT match (it was Oct 25 in UTC)
    count_utc = rebuild_daily_cap_from_audit(state_dir, date_str="2026-10-26", tz_name="UTC")
    assert count_utc == 0
