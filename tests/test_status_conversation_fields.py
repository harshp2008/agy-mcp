"""Frozen acceptance tests: additive ``agy_status`` conversation and run fields.

Contract under test (owner spec, quickwins task 1), all additive:
- ``conversation_id`` (str | None) and ``conversation_id_source``
  (``"early"`` | ``"finalized"`` | None) appear in the status payload.
- ``runs`` is a projection of ``Job.runs[]`` with exactly
  ``{index, started_at, ended_at, wall_clock_seconds, outcome}`` per entry;
  internal run fields (``run_generation``, ``agy_duration_seconds``, ``tokens``)
  are omitted and prompt text is never copied.
- ``wall_clock_seconds`` is the sum of the run wall clocks and is ``None`` when
  no run has finished.
- Jobs persisted before these fields existed load and report ``None``/``[]``
  without crashing.

These tests are frozen after the freeze commit: do not edit them.
"""

import json
import time
import uuid
from pathlib import Path
from typing import Callable

from agy_mcp.config import Config
from agy_mcp.jobs import create_job, get_job_dir, save_job
from agy_mcp.manager import JobManager
from agy_mcp.models import Job, JobState
from agy_mcp.runner import LocalProcessRunner
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"

PRE_EXISTING_STATUS_KEYS = {
    "job_id",
    "state",
    "elapsed_seconds",
    "title",
    "model_resolved",
    "branch",
    "base_commit",
    "head_commit",
    "commits_count",
    "files_modified",
    "skipped_files",
    "denials",
    "denied_commands",
    "denied_tools",
    "warnings",
    "tokens",
    "next_actions",
    "error",
    "usage_cost",
    "quota_estimate",
    "conversation_continued",
    "resume_after",
    "plan",
    "facts",
    "agent_report",
    "scratchpad",
    "verify",
    "last_cli_log_mtime",
    "last_commit_at",
    "worktree_dirty",
    "recent_log_tail",
}


def _manager_and_repo(tmp_path: Path) -> tuple[JobManager, Path]:
    repo = tmp_path / "repo"
    make_repo(repo, initial_commit=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    manager = JobManager(
        config=Config(max_concurrent_jobs=2, daily_jobs_cap=5),
        runner=LocalProcessRunner(executable=FAKE_AGY),
        base_state_dir=state_dir,
        home=tmp_path,
        settle_window_seconds=0.1,
        poll_interval_seconds=0.05,
    )
    return manager, repo


def _new_job(manager: JobManager, repo: Path) -> Job:
    return create_job(
        repo,
        "Status Fields Test",
        "prompt text must never leak into runs",
        "gemini-3.8-flash-medium",
        5,
        base_state_dir=manager.state_dir,
    )


def _wait_until(predicate: Callable[[], bool], timeout_seconds: float = 30.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"condition not met within {timeout_seconds}s")


def test_status_projects_conversation_and_run_fields(tmp_path: Path) -> None:
    manager, repo = _manager_and_repo(tmp_path)
    job = _new_job(manager, repo)
    job.state = JobState.DONE
    job.conversation_id = "0f8fad5b-d9cb-469f-a165-70867728950e"
    job.conversation_id_source = "finalized"
    job.runs = [
        {
            "run_generation": 1,
            "started_at": "2026-10-30T10:00:00+00:00",
            "ended_at": "2026-10-30T10:00:12.500000+00:00",
            "wall_clock_seconds": 12.5,
            "agy_duration_seconds": 600.0,
            "tokens": {"total_tokens": 123},
            "outcome": "done",
        },
        {
            "run_generation": 2,
            "started_at": "2026-10-30T10:05:00+00:00",
            "ended_at": "2026-10-30T10:05:07.250000+00:00",
            "wall_clock_seconds": 7.25,
            "agy_duration_seconds": 1200.0,
            "tokens": {"total_tokens": 7},
            "outcome": "needs_review",
        },
    ]
    save_job(job, manager.state_dir)

    payload = manager.status(job.job_id).to_dict()

    assert payload["conversation_id"] == "0f8fad5b-d9cb-469f-a165-70867728950e"
    assert payload["conversation_id_source"] == "finalized"
    assert payload["runs"] == [
        {
            "index": 0,
            "started_at": "2026-10-30T10:00:00+00:00",
            "ended_at": "2026-10-30T10:00:12.500000+00:00",
            "wall_clock_seconds": 12.5,
            "outcome": "done",
        },
        {
            "index": 1,
            "started_at": "2026-10-30T10:05:00+00:00",
            "ended_at": "2026-10-30T10:05:07.250000+00:00",
            "wall_clock_seconds": 7.25,
            "outcome": "needs_review",
        },
    ]
    assert payload["wall_clock_seconds"] == 19.75
    # Additive only: every pre-existing status key is still present.
    assert PRE_EXISTING_STATUS_KEYS.issubset(payload)


def test_status_empty_runs_and_unset_conversation(tmp_path: Path) -> None:
    manager, repo = _manager_and_repo(tmp_path)
    job = _new_job(manager, repo)

    payload = manager.status(job.job_id).to_dict()

    assert payload["conversation_id"] is None
    assert payload["conversation_id_source"] is None
    assert payload["runs"] == []
    assert payload["wall_clock_seconds"] is None
    assert PRE_EXISTING_STATUS_KEYS.issubset(payload)


def test_status_legacy_job_missing_new_keys(tmp_path: Path) -> None:
    """A job.json written before these keys existed must not crash status."""
    manager, repo = _manager_and_repo(tmp_path)
    job_id = str(uuid.uuid4())
    job_dir = get_job_dir(job_id, manager.state_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    legacy = {
        "schema_version": 1,
        "job_id": job_id,
        "title": "Legacy Job",
        "repo_path": str(repo),
        "branch": "agy/legacy-00000000",
        "base_commit": "HEAD",
        "state": "done",
        "conversation_id": "0f8fad5b-d9cb-469f-a165-70867728950e",
        # Deliberately absent: conversation_id_source, runs.
    }
    (job_dir / "job.json").write_text(json.dumps(legacy), encoding="utf-8")

    payload = manager.status(job_id).to_dict()

    assert payload["conversation_id"] == "0f8fad5b-d9cb-469f-a165-70867728950e"
    assert payload["conversation_id_source"] is None
    assert payload["runs"] == []
    assert payload["wall_clock_seconds"] is None


def test_status_legacy_run_entries_missing_timestamps(tmp_path: Path) -> None:
    """Old run rows have only internal fields; projection returns nulls, not errors."""
    manager, repo = _manager_and_repo(tmp_path)
    job = _new_job(manager, repo)
    job.state = JobState.DONE
    job.runs = [
        {
            "run_generation": 1,
            "wall_clock_seconds": 3.5,
            "agy_duration_seconds": 60.0,
            "tokens": {"total_tokens": 1},
        }
    ]
    save_job(job, manager.state_dir)

    payload = manager.status(job.job_id).to_dict()

    assert payload["runs"] == [
        {
            "index": 0,
            "started_at": None,
            "ended_at": None,
            "wall_clock_seconds": 3.5,
            "outcome": None,
        }
    ]
    assert payload["wall_clock_seconds"] == 3.5


def test_completed_run_records_timestamps_and_outcome(tmp_path: Path) -> None:
    """A freshly finished run stores timestamps/outcome and status exposes them."""
    manager, repo = _manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Build feature __SCENARIO:success_with_commits__",
        repo_path=str(repo),
        title="Run Fields Test",
    )
    _wait_until(
        lambda: manager.status(env.job_id).state
        in (JobState.DONE, JobState.NEEDS_REVIEW, JobState.FAILED, JobState.CANCELLED)
    )

    payload = manager.status(env.job_id).to_dict()

    assert payload["runs"], "a finished run must be recorded in Job.runs[]"
    run = payload["runs"][0]
    assert set(run) == {
        "index",
        "started_at",
        "ended_at",
        "wall_clock_seconds",
        "outcome",
    }
    assert run["index"] == 0
    assert isinstance(run["wall_clock_seconds"], (int, float))
    assert run["wall_clock_seconds"] >= 0
    assert run["started_at"] is not None
    assert run["ended_at"] is not None
    assert isinstance(run["outcome"], str) and run["outcome"].strip()
    assert isinstance(payload["wall_clock_seconds"], (int, float))
    assert payload["wall_clock_seconds"] >= run["wall_clock_seconds"]
