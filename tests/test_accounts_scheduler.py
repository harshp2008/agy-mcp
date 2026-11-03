"""Table-driven scheduler tests: cost, filter, ranking, burn and exhaustion (D108/D109)."""

from datetime import datetime, timezone

import pytest

from agy_mcp.accounts.pools import (
    POOL_CLAUDE_GPT,
    POOL_GEMINI,
    AccountUsage,
    PoolReading,
    WindowReading,
)
from agy_mcp.accounts.scheduler import (
    PRIORITY_BACKGROUND,
    PRIORITY_NORMAL,
    AccountState,
    HistoryCostModel,
    JobRequest,
    SchedulerConfig,
    estimate_cost,
    remaining_until_reset,
    schedule_account,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
DEFAULT_COST = estimate_cost(JobRequest("gemini-3.8-flash", 240), SchedulerConfig())


def _usage(
    gemini_weekly: float = 0.9,
    gemini_five: float = 0.9,
    *,
    weekly_reset: str = "2026-11-08T12:00:00Z",
    five_reset: str = "2026-11-03T16:00:00Z",
    available: bool = True,
) -> AccountUsage:
    pools = (
        PoolReading(
            POOL_GEMINI,
            (
                WindowReading("weekly", gemini_weekly, weekly_reset),
                WindowReading("5h", gemini_five, five_reset),
            ),
        ),
        PoolReading(
            POOL_CLAUDE_GPT,
            (
                WindowReading("weekly", 0.9, weekly_reset),
                WindowReading("5h", 0.9, five_reset),
            ),
        ),
    )
    return AccountUsage(account="a", available=available, pools=pools)


def _account(name: str, **kwargs) -> AccountState:
    usage = kwargs.pop("usage", _usage())
    pool_key = kwargs.pop("pool_key", name)
    return AccountState(name=name, pool_key=pool_key, usage=usage, **kwargs)


def _request(priority: str = PRIORITY_NORMAL, max_minutes: int = 240) -> JobRequest:
    return JobRequest("gemini-3.8-flash", max_minutes, priority)


# ---------------------------------------------------------------------------
# Cost estimate
# ---------------------------------------------------------------------------


def test_default_cost_estimate() -> None:
    cost = estimate_cost(_request(), SchedulerConfig())
    assert cost.estimated_minutes == 30
    assert cost.tokens == 1_059_000
    assert cost.five_hour_pct == pytest.approx(11.649)
    assert cost.weekly_pct == pytest.approx(3.2829)


def test_horizon_is_capped_by_max_minutes() -> None:
    cost = estimate_cost(JobRequest("gemini-3.8-flash", 10), SchedulerConfig())
    assert cost.estimated_minutes == 10
    assert cost.tokens == 353_000
    assert cost.five_hour_pct == pytest.approx(3.883)


def test_history_refines_rate_and_horizon() -> None:
    history = HistoryCostModel(rate_p75_tokens_per_minute=100_000, median_minutes=60)
    cost = estimate_cost(JobRequest("gemini-3.8-flash", 240), SchedulerConfig(), history)
    assert cost.estimated_minutes == 60
    assert cost.tokens == 6_000_000
    assert cost.five_hour_pct == pytest.approx(66.0)


def test_invalid_priority_is_rejected() -> None:
    with pytest.raises(ValueError):
        _request("urgent").validated()


# ---------------------------------------------------------------------------
# Strict filter
# ---------------------------------------------------------------------------


def test_normal_job_fits() -> None:
    result = schedule_account((_account("a"),), _request(), NOW)
    assert result.exhausted is False
    assert result.account == "a"
    assert result.reason == "strict"


def test_weekly_below_required_is_exhausted_with_reset() -> None:
    state = _account("a", usage=_usage(gemini_weekly=0.04))
    result = schedule_account((state,), _request(), NOW)
    assert result.exhausted is True
    assert result.account is None
    assert result.reason == "quota_exhausted"
    assert result.resume_after == "2026-11-08T12:00:00Z"


def test_five_hour_floor_refuses_even_when_cheap() -> None:
    state = _account("a", usage=_usage(gemini_five=0.10))
    result = schedule_account((state,), _request(), NOW)
    assert result.exhausted is True


def test_disabled_and_unverified_accounts_are_skipped() -> None:
    states = (
        _account("a", enabled=False),
        _account("b", identity_ok=False),
        _account("c", policy_ok=False),
        _account("d", model_ok=False),
        _account("e", usage=_usage(available=False)),
    )
    assert schedule_account(states, _request(), NOW).exhausted is True


def test_per_account_concurrency_is_enforced() -> None:
    state = _account("a", active_jobs=1, max_concurrent_jobs=1)
    assert schedule_account((state,), _request(), NOW).exhausted is True


def test_reservations_count_against_remaining_quota() -> None:
    state = _account(
        "a",
        usage=_usage(gemini_weekly=0.20),
        reserved_weekly_pct=16.0,
    )
    assert schedule_account((state,), _request(), NOW).exhausted is True


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def test_higher_expiry_urgency_wins() -> None:
    urgent = _account(
        "urgent",
        usage=_usage(gemini_weekly=0.80, weekly_reset="2026-11-03T14:00:00Z"),
    )
    relaxed = _account(
        "relaxed",
        usage=_usage(gemini_weekly=0.50, weekly_reset="2026-11-03T22:00:00Z"),
    )
    result = schedule_account((relaxed, urgent), _request(), NOW)
    assert result.account == "urgent"


def test_expires_at_boost_wins() -> None:
    expiring = _account(
        "expiring",
        usage=_usage(gemini_weekly=0.10, weekly_reset="2026-11-07T12:00:00Z"),
        expires_at="2026-11-03T13:00:00Z",
    )
    stable = _account(
        "stable",
        usage=_usage(gemini_weekly=0.50, weekly_reset="2026-11-03T22:00:00Z"),
    )
    assert schedule_account((stable, expiring), _request(), NOW).account == "expiring"


def test_ties_are_deterministic_by_name() -> None:
    states = (_account("b"), _account("a"))
    first = schedule_account(states, _request(), NOW)
    again = schedule_account(tuple(reversed(states)), _request(), NOW)
    assert first.account == again.account == "a"


def test_least_recently_used_breaks_a_score_tie() -> None:
    newer = _account("newer", last_used_epoch=200.0)
    older = _account("older", last_used_epoch=100.0)
    assert schedule_account((newer, older), _request(), NOW).account == "older"


def test_one_two_and_five_accounts() -> None:
    for count in (1, 2, 5):
        states = tuple(_account(f"a{index}") for index in range(count))
        result = schedule_account(states, _request(), NOW)
        assert result.exhausted is False
        assert result.account is not None


# ---------------------------------------------------------------------------
# Shared pools
# ---------------------------------------------------------------------------


def test_shared_pool_uses_the_minimum_remaining() -> None:
    rich = _account("rich", pool_key="family", usage=_usage(gemini_weekly=0.90))
    poor = _account("poor", pool_key="family", usage=_usage(gemini_weekly=0.03))
    result = schedule_account((rich, poor), _request(), NOW)
    assert result.exhausted is True


def test_shared_pool_reservations_are_summed() -> None:
    one = _account(
        "one", pool_key="family", usage=_usage(gemini_weekly=0.40), reserved_weekly_pct=10.0
    )
    two = _account(
        "two", pool_key="family", usage=_usage(gemini_weekly=0.40), reserved_weekly_pct=10.0
    )
    # 40 - 20 = 20, still above the 4.92 required.
    assert schedule_account((one, two), _request(), NOW).exhausted is False


# ---------------------------------------------------------------------------
# Background burn mode
# ---------------------------------------------------------------------------


def test_worked_example_background_burns_expiring_quota() -> None:
    expiring = _account(
        "expiring",
        usage=_usage(gemini_weekly=0.04, weekly_reset="2026-11-03T13:00:00Z"),
    )
    better = _account(
        "better",
        usage=_usage(gemini_weekly=0.50, weekly_reset="2026-11-03T22:00:00Z"),
    )

    normal = schedule_account((expiring, better), _request(PRIORITY_NORMAL), NOW)
    assert normal.account == "better"

    background = schedule_account((expiring, better), _request(PRIORITY_BACKGROUND), NOW)
    assert background.account == "expiring"
    assert background.reason == "burn"


def test_burn_horizon_boundary() -> None:
    inside = _account(
        "inside",
        usage=_usage(gemini_weekly=0.04, weekly_reset="2026-11-03T13:30:00Z"),
    )
    outside = _account(
        "outside",
        usage=_usage(gemini_weekly=0.04, weekly_reset="2026-11-03T13:41:00Z"),
    )
    assert schedule_account((inside,), _request(PRIORITY_BACKGROUND), NOW).account == "inside"
    assert schedule_account((outside,), _request(PRIORITY_BACKGROUND), NOW).exhausted is True


def test_background_prefers_nearest_reset_then_smallest_remaining() -> None:
    soon = _account(
        "soon", usage=_usage(gemini_weekly=0.03, weekly_reset="2026-11-03T12:30:00Z")
    )
    later = _account(
        "later", usage=_usage(gemini_weekly=0.02, weekly_reset="2026-11-03T13:00:00Z")
    )
    assert schedule_account((later, soon), _request(PRIORITY_BACKGROUND), NOW).account == "soon"


def test_anti_starvation_keeps_the_last_slot_for_a_normal_job() -> None:
    state = _account("a", max_concurrent_jobs=1)
    result = schedule_account(
        (state,),
        _request(PRIORITY_BACKGROUND),
        NOW,
        normal_waiters=frozenset({"a"}),
    )
    assert result.exhausted is True
    # Normal jobs are unaffected by the waiter set.
    assert (
        schedule_account(
            (state,), _request(PRIORITY_NORMAL), NOW, normal_waiters=frozenset({"a"})
        ).account
        == "a"
    )


def test_background_may_use_the_last_slot_without_a_waiter() -> None:
    state = _account("a", max_concurrent_jobs=1)
    result = schedule_account((state,), _request(PRIORITY_BACKGROUND), NOW)
    assert result.account == "a"


# ---------------------------------------------------------------------------
# Exhaustion
# ---------------------------------------------------------------------------


def test_earliest_resume_across_accounts() -> None:
    slow = _account("slow", usage=_usage(gemini_weekly=0.01, weekly_reset="2026-11-03T15:00:00Z"))
    fast = _account("fast", usage=_usage(gemini_weekly=0.01, weekly_reset="2026-11-03T14:00:00Z"))
    result = schedule_account((slow, fast), _request(), NOW)
    assert result.exhausted is True
    assert result.resume_after == "2026-11-03T14:00:00Z"


def test_unknown_reset_cannot_promise_a_time() -> None:
    state = _account("a", usage=_usage(gemini_weekly=0.01, weekly_reset="", five_reset=""))
    result = schedule_account((state,), _request(), NOW)
    assert result.exhausted is True
    assert result.resume_after is None


def test_remaining_until_reset() -> None:
    assert remaining_until_reset("2026-11-03T13:00:00Z", NOW).total_seconds() == 3600
    assert remaining_until_reset("2026-11-03T11:00:00Z", NOW).total_seconds() == 0
    assert remaining_until_reset(None, NOW) is None


def test_decision_is_deterministic() -> None:
    states = (
        _account("a", usage=_usage(gemini_weekly=0.4)),
        _account("b", usage=_usage(gemini_weekly=0.6)),
        _account("c", usage=_usage(gemini_weekly=0.5)),
    )
    results = {schedule_account(states, _request(), NOW).account for _ in range(20)}
    assert results == {schedule_account(states, _request(), NOW).account}
    assert len(results) == 1


# ---------------------------------------------------------------------------
# Full windows have no expiry pressure (D115)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("weekly_remaining", [1.0, 0.9999, 0.9995, 0.999])
@pytest.mark.parametrize(
    "placeholder_reset",
    ["2026-11-03T12:05:00Z", "2026-11-03T13:00:00Z", "2026-11-03T17:00:00Z"],
)
def test_full_weekly_window_contributes_zero_urgency(
    weekly_remaining: float, placeholder_reset: str
) -> None:
    """A 100% (or within 0.1%) window must not win on a placeholder reset."""
    full = _account(
        "full",
        usage=_usage(gemini_weekly=weekly_remaining, weekly_reset=placeholder_reset),
    )
    partly_used = _account(
        "partly-used",
        usage=_usage(gemini_weekly=0.20, weekly_reset="2026-11-03T13:00:00Z"),
    )
    result = schedule_account((full, partly_used), _request(), NOW)
    assert result.account == "partly-used"
    rows = {row.name: row for row in result.trace.accounts}
    assert rows["full"].urgency_score == pytest.approx(0.0)
    assert rows["partly-used"].urgency_score > 0.0


def test_a_window_just_below_full_still_has_urgency() -> None:
    """The 0.1% margin is a boundary, not a blanket "high quota" discount."""
    almost_full = _account(
        "almost-full",
        usage=_usage(gemini_weekly=0.998, weekly_reset="2026-11-03T12:05:00Z"),
    )
    ordinary = _account(
        "ordinary",
        usage=_usage(gemini_weekly=0.20, weekly_reset="2026-11-03T13:00:00Z"),
    )
    result = schedule_account((almost_full, ordinary), _request(), NOW)
    assert result.account == "almost-full"
    rows = {row.name: row for row in result.trace.accounts}
    assert rows["almost-full"].urgency_score > 0.0


def test_full_window_margin_is_tunable() -> None:
    """Raising the margin makes a 96%-remaining window count as full."""
    config = SchedulerConfig(full_window_margin_pct=5.0)
    nearly_full = _account(
        "nearly-full",
        usage=_usage(gemini_weekly=0.96, weekly_reset="2026-11-03T12:05:00Z"),
    )
    ordinary = _account(
        "ordinary",
        usage=_usage(gemini_weekly=0.50, weekly_reset="2026-11-03T18:00:00Z"),
    )
    result = schedule_account((nearly_full, ordinary), _request(), NOW, config)
    assert result.account == "ordinary"
    rows = {row.name: row for row in result.trace.accounts}
    assert rows["nearly-full"].urgency_score == pytest.approx(0.0)


def test_worked_example_with_a_full_alternative() -> None:
    """96% weekly used + reset in 1 h vs an untouched full window.

    A normal job cannot fit the 4%-left account and uses the alternative; a
    background job burns the expiring quota. The full window's near placeholder
    reset must not make it the urgent winner.
    """
    expiring = _account(
        "expiring",
        usage=_usage(gemini_weekly=0.04, weekly_reset="2026-11-03T13:00:00Z"),
    )
    untouched = _account(
        "untouched",
        usage=_usage(gemini_weekly=1.0, weekly_reset="2026-11-03T12:30:00Z"),
    )
    normal = schedule_account((expiring, untouched), _request(PRIORITY_NORMAL), NOW)
    assert normal.account == "untouched"

    background = schedule_account(
        (expiring, untouched), _request(PRIORITY_BACKGROUND), NOW
    )
    assert background.account == "expiring"
    assert background.reason == "burn"
    rows = {row.name: row for row in background.trace.accounts}
    assert rows["expiring"].rank == 1
    assert rows["untouched"].urgency_score == pytest.approx(0.0)
    assert rows["untouched"].rank == 2


def test_full_window_is_never_the_burn_binding_window() -> None:
    """A full five-hour placeholder reset must not bind a weekly burn."""
    state = _account(
        "a",
        usage=_usage(
            gemini_weekly=1.0,
            weekly_reset="2026-11-03T12:05:00Z",
            gemini_five=0.04,
            five_reset="2026-11-03T13:00:00Z",
        ),
    )
    result = schedule_account((state,), _request(PRIORITY_BACKGROUND), NOW)
    assert result.account == "a"
    assert result.reason == "burn"
    row = result.trace.accounts[0]
    assert row.binding_window == "5h"
    assert row.hours_to_binding_reset == pytest.approx(1.0)
    assert row.urgency_score == pytest.approx(0.0)


def test_expires_at_boost_flips_an_otherwise_lower_urgency() -> None:
    """The trace rule names the deciding criterion, not just the winner."""
    boosted = _account(
        "boosted",
        usage=_usage(gemini_weekly=0.10, weekly_reset="2026-11-03T13:00:00Z"),
        expires_at="2026-11-03T13:00:00Z",
    )
    urgent = _account(
        "urgent",
        usage=_usage(gemini_weekly=0.80, weekly_reset="2026-11-03T14:00:00Z"),
    )
    result = schedule_account((boosted, urgent), _request(), NOW)
    assert result.account == "boosted"
    assert result.trace.rule == "expires_at"
