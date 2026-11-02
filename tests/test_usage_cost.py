"""Tests for per-job usage cost accounting (Item 5)."""

import json
import time
from pathlib import Path
from typing import Callable

from agy_mcp.config import Config
from agy_mcp.jobs import get_job
from agy_mcp.manager import JobManager
from agy_mcp.models import JobState
from agy_mcp.runner import LocalProcessRunner
from agy_mcp.usage import compute_usage_cost, parse_usage_output
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"
FIXTURE = Path(__file__).parent / "fixtures" / "real_usage.json"


def poll_until(
    predicate: Callable[[], bool],
    timeout_seconds: float = 15.0,
    poll_interval: float = 0.01,
) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(poll_interval)
    raise TimeoutError("Timed out waiting for condition")


def _reading() -> dict:
    return parse_usage_output(FIXTURE.read_text(encoding="utf-8"))


def _zero_reading() -> dict:
    reading = _reading()
    for group in reading["groups"]:
        for bucket in group["buckets"]:
            bucket["remaining_fraction"] = 0.0
    return reading


def _unavailable() -> dict:
    return {
        "available": False,
        "reason": "unrecognized_format",
        "groups": [],
        "account": {"masked_email": None, "auth_method": None},
    }


def test_compute_usage_cost_positive_delta() -> None:
    """Cost is the drop in remaining percent for the model's group."""
    cost = compute_usage_cost(
        "gemini-3.8-flash-medium",
        launch_reading=_reading(),
        final_reading=_zero_reading(),
        concurrent_jobs=1,
    )
    assert cost["measured"] is True
    assert cost["five_hour_pct_points"] == 64.55
    assert cost["weekly_pct_points"] == 64.06
    assert cost["concurrent_jobs"] == 1
    assert "single" in cost["note"].lower()


def test_compute_usage_cost_concurrent_is_approximate() -> None:
    """More than one concurrent job is reported as approximate in note."""
    cost = compute_usage_cost(
        "gemini-3.8-flash-medium",
        launch_reading=_reading(),
        final_reading=_zero_reading(),
        concurrent_jobs=3,
    )
    assert cost["measured"] is True
    assert cost["concurrent_jobs"] == 3
    assert "approx" in cost["note"].lower()
    assert "3" in cost["note"]


def test_compute_usage_cost_unavailable_is_not_an_error() -> None:
    """Unavailable readings yield measured=false with zero points and a note."""
    cost = compute_usage_cost(
        "gemini-3.8-flash-medium",
        launch_reading=_unavailable(),
        final_reading=_zero_reading(),
        concurrent_jobs=1,
    )
    assert cost["measured"] is False
    assert cost["five_hour_pct_points"] == 0.0
    assert cost["weekly_pct_points"] == 0.0
    assert "unavailable" in cost["note"].lower()

    cost2 = compute_usage_cost(
        "gemini-3.8-flash-medium",
        launch_reading=_reading(),
        final_reading=None,
        concurrent_jobs=1,
    )
    assert cost2["measured"] is False


def test_compute_usage_cost_reset_clamps_to_zero() -> None:
    """A quota refresh during the job clamps negative deltas to zero."""
    cost = compute_usage_cost(
        "gemini-3.8-flash-medium",
        launch_reading=_zero_reading(),
        final_reading=_reading(),
        concurrent_jobs=1,
    )
    assert cost["measured"] is True
    assert cost["five_hour_pct_points"] == 0.0
    assert cost["weekly_pct_points"] == 0.0
    assert "reset" in cost["note"].lower()


def test_compute_usage_cost_claude_group() -> None:
    """claude*/gpt* jobs read the Claude and GPT models group."""
    fixture = _reading()
    final = _reading()
    for group in final["groups"]:
        if group["name"] == "Claude and GPT models":
            for bucket in group["buckets"]:
                bucket["remaining_fraction"] = 0.5
    cost = compute_usage_cost(
        "claude-3-7-sonnet",
        launch_reading=fixture,
        final_reading=final,
        concurrent_jobs=1,
    )
    assert cost["measured"] is True
    assert cost["weekly_pct_points"] == 50.0
    assert cost["five_hour_pct_points"] == 50.0


def _setup(tmp_path: Path) -> tuple[JobManager, Path]:
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    manager = JobManager(
        config=Config(),
        runner=LocalProcessRunner(executable=FAKE_AGY),
        base_state_dir=tmp_path / "state",
        home=tmp_path,
        settle_window_seconds=0.05,
        poll_interval_seconds=0.05,
    )
    return manager, repo_dir


def test_usage_cost_recorded_in_job_usage_and_envelope(tmp_path: Path) -> None:
    """A job records launch/final cost in job.json, usage.jsonl, and the envelope."""
    (tmp_path / ".fake_agy_usage_scenario").write_text("usage_real", encoding="utf-8")
    manager, repo_dir = _setup(tmp_path)

    env = manager.start(
        prompt="Slow __SCENARIO:slow_progress__",
        repo_path=str(repo_dir),
        title="Usage Cost Job",
        model="gemini-3.8-flash-medium",
    )
    assert env.usage_cost is None

    try:
        poll_until(
            lambda: get_job(env.job_id, manager.state_dir).usage_at_launch is not None
        )
        (tmp_path / ".fake_agy_usage_scenario").write_text("usage_zero", encoding="utf-8")

        result = manager.wait(env.job_id, seconds=15)
        assert result.state == JobState.DONE

        cost = result.usage_cost
        assert cost is not None
        assert cost["measured"] is True
        assert cost["weekly_pct_points"] > 60.0
        assert cost["five_hour_pct_points"] > 60.0
        assert cost["concurrent_jobs"] >= 1

        on_disk = get_job(env.job_id, manager.state_dir)
        assert on_disk.usage_cost == cost

        usage_file = manager.state_dir / "usage.jsonl"
        records = [
            json.loads(line)
            for line in usage_file.read_text().splitlines()
            if line.strip()
        ]
        record = next(r for r in records if r["job_id"] == env.job_id)
        assert record["usage_cost"]["measured"] is True
        assert record["usage_cost"]["weekly_pct_points"] > 60.0
    finally:
        manager.cancel(env.job_id)
        manager.shutdown()


def test_usage_cost_unavailable_final_reading_is_not_an_error(tmp_path: Path) -> None:
    """If the final reading is unavailable the job still completes with measured=false."""
    (tmp_path / ".fake_agy_usage_scenario").write_text("usage_real", encoding="utf-8")
    manager, repo_dir = _setup(tmp_path)

    env = manager.start(
        prompt="Slow __SCENARIO:slow_progress__",
        repo_path=str(repo_dir),
        title="Usage Cost Unavailable Job",
        model="gemini-3.8-flash-medium",
    )
    try:
        poll_until(
            lambda: get_job(env.job_id, manager.state_dir).usage_at_launch is not None
        )
        (tmp_path / ".fake_agy_usage_scenario").write_text("usage_garbled", encoding="utf-8")

        result = manager.wait(env.job_id, seconds=15)
        assert result.state == JobState.DONE
        assert result.usage_cost is not None
        assert result.usage_cost["measured"] is False
        assert result.usage_cost["five_hour_pct_points"] == 0.0
        assert "unavailable" in result.usage_cost["note"].lower()
        assert result.error is None
    finally:
        manager.cancel(env.job_id)
        manager.shutdown()
