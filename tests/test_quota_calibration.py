"""Frozen acceptance tests: calibrated token-based quota estimate.

Contract under test (owner spec, quota calibration 2026-10-31). Data and the fit are
recorded in ``docs/reports/quota-calibration-data.md``; the constants below are copied
from that document, not from the implementation:

    five-hour coefficient: 0.011   % points per 1k tokens
    weekly coefficient:    0.0031  % points per 1k tokens
    base value:            0.0     % points at zero tokens
    basis label:           "calibrated-2026-10-31, n=7"

Formula: ``pct_points = base + tokens / 1000 * coefficient``, rounded to 4 decimals.

The calibrated coefficients are deliberately conservative (upper envelope of the
observed measured/estimate ratios), so the estimate never under-reports a job that was
measured. The measured ``usage_cost`` delta remains authoritative.

These tests are frozen after the freeze commit: do not edit them.
"""

import json
from pathlib import Path
from typing import Any, Optional

import pytest

from agy_mcp.config import Config
from agy_mcp.jobs import create_job, save_job
from agy_mcp.manager import JobManager
from agy_mcp.models import Job, JobState
from agy_mcp.runner import LocalProcessRunner
from agy_mcp.usage import compute_quota_estimate
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"

# Copied from docs/reports/quota-calibration-data.md ("Constants to freeze").
FIVE_HOUR_PER_1K = 0.011
WEEKLY_PER_1K = 0.0031
BASE_PCT_POINTS = 0.0
BASIS = "calibrated-2026-10-31, n=7"

LEGACY_ESTIMATE = {
    "tokens": 150,
    "weekly_pct_points": 0.0001,
    "five_hour_pct_points": 0.0004,
    "weekly_per_1k_tokens": 0.00075,
    "five_hour_per_1k_tokens": 0.0027,
    "estimated": True,
    "note": "estimate from agy JSON tokens; measured usage_cost is authoritative",
}

LEGACY_USAGE_COST = {
    "five_hour_pct_points": 0.42,
    "weekly_pct_points": 0.12,
    "measured": True,
    "concurrent_jobs": 1,
    "note": "single active job; quota points attributed to this job",
}


def _manager(tmp_path: Path) -> tuple[JobManager, Path]:
    repo = tmp_path / "repo"
    make_repo(repo, initial_commit=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    manager = JobManager(
        config=Config(max_concurrent_jobs=1),
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
        "Quota Calibration Test",
        "prompt text must never appear in the estimate",
        "gemini-3.8-flash-medium",
        5,
        base_state_dir=manager.state_dir,
    )


# ---------------------------------------------------------------------------
# The estimate function: documented inputs -> documented outputs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tokens", "expected_weekly", "expected_five_hour"),
    [
        # 200 * 0.0031 = 0.62 ; 200 * 0.011 = 2.2
        ({"total_tokens": 200_000}, 0.62, 2.2),
        # 1000 * 0.0031 = 3.1 ; 1000 * 0.011 = 11.0
        ({"total_tokens": 1_000_000}, 3.1, 11.0),
        # 809.853 * 0.0031 = 2.5105443 -> 2.5105 ; 809.853 * 0.011 = 8.908383 -> 8.9084
        ({"total_tokens": 809_853}, 2.5105, 8.9084),
        # 0.15 * 0.0031 = 0.000465 -> 0.0005 ; 0.15 * 0.011 = 0.00165 -> 0.0016
        ({"total_tokens": 150}, 0.0005, 0.0016),
    ],
)
def test_estimate_matches_documented_values(
    tokens: dict[str, Any],
    expected_weekly: float,
    expected_five_hour: float,
) -> None:
    result = compute_quota_estimate(tokens)

    assert result is not None
    assert result["weekly_pct_points"] == expected_weekly
    assert result["five_hour_pct_points"] == expected_five_hour
    assert result["tokens"] == int(tokens["total_tokens"])


def test_estimate_reports_the_calibrated_basis_and_coefficients() -> None:
    result = compute_quota_estimate({"total_tokens": 1000})

    assert result is not None
    assert result["quota_estimate_basis"] == BASIS
    assert result["weekly_per_1k_tokens"] == pytest.approx(WEEKLY_PER_1K)
    assert result["five_hour_per_1k_tokens"] == pytest.approx(FIVE_HOUR_PER_1K)
    assert result["estimated"] is True
    # The estimate stays explicitly labelled as an estimate.
    assert "estimate" in result["note"].lower()


def test_estimate_is_monotone_in_tokens() -> None:
    counts = [0, 1, 1_000, 10_000, 100_000, 1_000_000]
    results = [compute_quota_estimate({"total_tokens": n}) for n in counts]

    assert all(result is not None for result in results)
    five_hour = [result["five_hour_pct_points"] for result in results]
    weekly = [result["weekly_pct_points"] for result in results]

    assert five_hour == sorted(five_hour)
    assert weekly == sorted(weekly)


def test_estimate_is_never_negative() -> None:
    for count in (0, 1, 150, 809_853, 10**9):
        result = compute_quota_estimate({"total_tokens": count})
        assert result is not None
        assert result["five_hour_pct_points"] >= 0.0
        assert result["weekly_pct_points"] >= 0.0

    # Non-negative coefficients are a documented invariant: a caller cannot make the
    # estimate negative through the keyword arguments either.
    result = compute_quota_estimate(
        {"total_tokens": 10_000},
        weekly_per_1k_tokens=-1.0,
        five_hour_per_1k_tokens=-1.0,
    )
    assert result is not None
    assert result["weekly_pct_points"] >= 0.0
    assert result["five_hour_pct_points"] >= 0.0


def test_zero_tokens_gives_the_documented_base_value() -> None:
    result = compute_quota_estimate({"total_tokens": 0})

    assert result is not None
    assert result["tokens"] == 0
    assert result["weekly_pct_points"] == BASE_PCT_POINTS
    assert result["five_hour_pct_points"] == BASE_PCT_POINTS
    assert result["estimated"] is False
    assert result["quota_estimate_basis"] == BASIS


@pytest.mark.parametrize(
    "tokens",
    [
        None,
        {},
        {"total_tokens": None},
        {"total_tokens": "not-a-number"},
        {"total_tokens": -5},
        {"input_tokens": 5},  # partial counts must not be invented into a total
        {"cache_read_tokens": 1_000_000},
    ],
)
def test_unknown_token_counts_give_null(tokens: Optional[dict[str, Any]]) -> None:
    assert compute_quota_estimate(tokens) is None


def test_missing_total_falls_back_to_input_plus_output() -> None:
    result = compute_quota_estimate({"input_tokens": 400, "output_tokens": 100})

    assert result is not None
    assert result["tokens"] == 500
    assert result["weekly_pct_points"] == round(500 / 1000 * WEEKLY_PER_1K, 4)
    assert result["five_hour_pct_points"] == round(500 / 1000 * FIVE_HOUR_PER_1K, 4)


def test_estimate_does_not_mutate_its_input() -> None:
    tokens = {"total_tokens": 1234, "input_tokens": 1000, "output_tokens": 234}
    before = json.dumps(tokens, sort_keys=True)

    compute_quota_estimate(tokens)

    assert json.dumps(tokens, sort_keys=True) == before
    # usage_cost is a separate, measured record and is never produced by the estimate.
    result = compute_quota_estimate(tokens)
    assert result is not None
    assert "usage_cost" not in result


# ---------------------------------------------------------------------------
# Persisted jobs: old estimates are not rewritten, measured cost is untouched
# ---------------------------------------------------------------------------


def test_status_does_not_rewrite_a_legacy_estimate_or_measured_cost(tmp_path: Path) -> None:
    manager, repo = _manager(tmp_path)
    job = _new_job(manager, repo)
    job.state = JobState.DONE
    job.tokens.total_tokens = 150
    job.quota_estimate = dict(LEGACY_ESTIMATE)
    job.usage_cost = dict(LEGACY_USAGE_COST)
    save_job(job, manager.state_dir)

    job_json = manager.state_dir / "jobs" / job.job_id / "job.json"
    before = job_json.read_text(encoding="utf-8")

    payload = manager.status(job.job_id).to_dict()

    assert payload["quota_estimate"] == LEGACY_ESTIMATE
    assert payload["usage_cost"] == LEGACY_USAGE_COST
    assert job_json.read_text(encoding="utf-8") == before


def test_legacy_job_estimate_is_not_recalibrated_on_read(tmp_path: Path) -> None:
    """A stored estimate keeps its old coefficients even if they differ from config."""
    manager, repo = _manager(tmp_path)
    job = _new_job(manager, repo)
    job.state = JobState.DONE
    job.quota_estimate = dict(LEGACY_ESTIMATE)
    save_job(job, manager.state_dir)

    payload = manager.status(job.job_id).to_dict()

    assert payload["quota_estimate"]["weekly_per_1k_tokens"] == 0.00075
    assert payload["quota_estimate"]["five_hour_per_1k_tokens"] == 0.0027
    assert payload["quota_estimate"]["weekly_pct_points"] == 0.0001
