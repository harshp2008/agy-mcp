"""Pure quota-aware account scheduler (D108, D109).

Owns the cost estimate, eligibility filter, ranking, burn-before-expiry mode,
the exhaustion result and the per-account decision trace. Every input is a plain
data class and the clock is passed in, so a decision is deterministic and
testable; this module performs no I/O, never sleeps, and never reads a token or
an account home.

The decision inputs are deliberately narrow:

* :class:`SchedulerConfig` — tunable thresholds and coefficients.
* :class:`AccountState` — one account's verified flags, typed usage and load.
* :class:`JobRequest` — the model, ``max_minutes`` and priority.

Accounts that share a ``pool_key`` are one quota bucket: their remaining quota is
the minimum across the group and their reservations are summed.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Optional

from agy_mcp.accounts.pools import (
    WINDOW_FIVE_HOUR,
    WINDOW_WEEKLY,
    PoolReading,
)
from agy_mcp.accounts.scheduler_types import (
    MAX_TRACE_ACCOUNTS,
    MIN_HOURS,
    PRIORITIES,
    PRIORITY_BACKGROUND,
    PRIORITY_NORMAL,
    AccountState,
    CostEstimate,
    DecisionTrace,
    HistoryCostModel,
    JobRequest,
    RankedAccount,
    SchedulerConfig,
    SchedulerResult,
    TraceAccount,
    format_utc,
    parse_utc,
)

__all__ = [
    "MAX_TRACE_ACCOUNTS",
    "MIN_HOURS",
    "PRIORITIES",
    "PRIORITY_BACKGROUND",
    "PRIORITY_NORMAL",
    "AccountState",
    "CostEstimate",
    "DecisionTrace",
    "HistoryCostModel",
    "JobRequest",
    "RankedAccount",
    "SchedulerConfig",
    "SchedulerResult",
    "TraceAccount",
    "account_eligibility",
    "estimate_cost",
    "remaining_until_reset",
    "schedule_account",
]

# A quota window is a percentage in [0, 100]; at 100% it is full (D115).
_FULL_WINDOW_PCT = 100.0

# Tolerance for comparing two scores, waits or remaining percentages. Inputs are
# percentages and minutes, so 1e-9 absorbs binary-float noise without hiding any
# decision a human could see.
_TIE_EPSILON = 1e-9


def estimate_cost(
    request: JobRequest,
    config: SchedulerConfig,
    history: Optional[HistoryCostModel] = None,
) -> CostEstimate:
    """Estimate a job's cost in five-hour and weekly percentage points.

    The horizon is ``min(max_minutes, expected_minutes)`` because ``max_minutes``
    is a ceiling, not an expected duration. A history model raises the per-minute
    rate to the observed p75 and the horizon to the observed median (still capped
    by ``max_minutes``). The result is a scheduling heuristic, never a claim
    about actual consumption.
    """
    request.validated()
    rate = float(config.tokens_per_minute)
    horizon = min(request.max_minutes, config.expected_minutes)
    if history is not None:
        rate = max(rate, float(history.rate_p75_tokens_per_minute))
        horizon = min(
            request.max_minutes,
            max(config.expected_minutes, int(history.median_minutes)),
        )
    horizon = max(1, horizon)
    tokens = int(round(rate * horizon))
    return CostEstimate(
        five_hour_pct=round(tokens / 1000.0 * config.five_hour_per_1k_tokens, 4),
        weekly_pct=round(tokens / 1000.0 * config.weekly_per_1k_tokens, 4),
        estimated_minutes=horizon,
        tokens=tokens,
    )


def _pool_for(state: AccountState, request: JobRequest) -> Optional[PoolReading]:
    if state.usage is None or not state.usage.available:
        return None
    return state.usage.pool_for_model(request.model)


def _later(left: Optional[str], right: Optional[str]) -> Optional[str]:
    left_dt = parse_utc(left)
    right_dt = parse_utc(right)
    if left_dt is None:
        return right
    if right_dt is None:
        return left
    return left if left_dt >= right_dt else right


def _group_window(
    group: list[AccountState], request: JobRequest, window: str
) -> Optional[tuple[float, Optional[str]]]:
    """Return (minimum remaining fraction, latest reset) for one group window.

    The minimum is the binding member; when several tie, the latest reset is kept
    so a wait covers every binding member. None when any member cannot report the
    window: the group is then not schedulable for this model.
    """
    best_fraction: Optional[float] = None
    best_reset: Optional[str] = None
    for state in group:
        pool = _pool_for(state, request)
        if pool is None:
            return None
        bucket = pool.window(window)
        if bucket is None:
            return None
        if best_fraction is None or bucket.remaining_fraction < best_fraction - 1e-12:
            best_fraction = bucket.remaining_fraction
            best_reset = bucket.reset_time_utc
        elif abs(bucket.remaining_fraction - best_fraction) <= 1e-12:
            best_reset = _later(best_reset, bucket.reset_time_utc)
    if best_fraction is None:
        return None
    return best_fraction, best_reset


def _groups(states: tuple[AccountState, ...]) -> dict[str, list[AccountState]]:
    grouped: dict[str, list[AccountState]] = {}
    for state in sorted(states, key=lambda item: item.name):
        grouped.setdefault(state.pool_key, []).append(state)
    return grouped


def _base_eligible(
    state: AccountState,
    request: JobRequest,
    normal_waiters: frozenset[str],
) -> Optional[str]:
    """Return a rejection reason, or None when the non-quota checks pass."""
    if state.home_error:
        return state.home_error
    if not state.enabled:
        return "disabled"
    if not state.identity_ok:
        return "identity_unverified"
    if not state.policy_ok:
        return "policy_unverified"
    if not state.model_ok:
        return "model_unavailable"
    if state.usage is None or not state.usage.available:
        return "usage_unavailable"
    if state.active_jobs + state.reserved_jobs >= state.max_concurrent_jobs:
        return "account_concurrency"
    if (
        request.priority == PRIORITY_BACKGROUND
        and state.name in normal_waiters
        and state.active_jobs + state.reserved_jobs + 1 >= state.max_concurrent_jobs
    ):
        # Anti-starvation: a background job may not take the last free slot of an
        # account on which a normal job is waiting.
        return "reserved_for_normal"
    return None


@dataclass(frozen=True)
class _Quota:
    """One account's group quota after reservations, plus the raw group values.

    ``*_remaining`` are percentage points after subtracting the group's
    reservations. ``*_raw_pct`` are the group window minimums before
    reservations; they identify a genuinely full window (a placeholder reset on
    a 100% window must never look like real expiry pressure).
    """

    five_remaining: float
    weekly_remaining: float
    five_reset: Optional[str]
    weekly_reset: Optional[str]
    five_raw_pct: float
    weekly_raw_pct: float


def _group_quota(
    grouped: dict[str, list[AccountState]], state: AccountState, request: JobRequest
) -> Optional[_Quota]:
    """Return the group's quota after reservations, or None when unreportable."""
    group = grouped[state.pool_key]
    five = _group_window(group, request, WINDOW_FIVE_HOUR)
    weekly = _group_window(group, request, WINDOW_WEEKLY)
    if five is None or weekly is None:
        return None
    five_raw = five[0] * 100.0
    weekly_raw = weekly[0] * 100.0
    return _Quota(
        five_remaining=five_raw - sum(member.reserved_five_hour_pct for member in group),
        weekly_remaining=weekly_raw - sum(member.reserved_weekly_pct for member in group),
        five_reset=five[1],
        weekly_reset=weekly[1],
        five_raw_pct=five_raw,
        weekly_raw_pct=weekly_raw,
    )


def _is_full_window(remaining_pct: float, config: SchedulerConfig) -> bool:
    """Return True when a window is at (or within the margin of) 100% remaining.

    A full window has no quota at risk of expiring: whatever remains is already
    the maximum, so it contributes zero urgency and must never become the
    binding window for burn mode (D115). The margin is the tunable
    ``full_window_margin_pct`` config key.
    """
    return remaining_pct >= _FULL_WINDOW_PCT - config.full_window_margin_pct


def _window_waits(
    quota: _Quota,
    cost: CostEstimate,
    config: SchedulerConfig,
    now: datetime,
) -> Optional[tuple[float, float]]:
    """Return ``(five-hour wait, weekly wait)`` in minutes, or None when unknown."""
    required_five = max(config.five_hour_floor_pct, cost.five_hour_pct * config.safety_factor)
    required_weekly = cost.weekly_pct * config.safety_factor

    def wait_for(
        remaining: float, required: float, reset: Optional[str], full: bool
    ) -> Optional[float]:
        # A full window is usable regardless of the cost estimate: its reset is
        # a rolling placeholder, not a real wait.
        if full or remaining >= required:
            return 0.0
        reset_dt = parse_utc(reset)
        if reset_dt is None:
            return None
        return max(0.0, (reset_dt - now).total_seconds() / 60.0)

    five_wait = wait_for(
        quota.five_remaining,
        required_five,
        quota.five_reset,
        _is_full_window(quota.five_raw_pct, config),
    )
    weekly_wait = wait_for(
        quota.weekly_remaining,
        required_weekly,
        quota.weekly_reset,
        _is_full_window(quota.weekly_raw_pct, config),
    )
    if five_wait is None or weekly_wait is None:
        return None
    return five_wait, weekly_wait


def _wait_minutes(
    quota: _Quota,
    cost: CostEstimate,
    config: SchedulerConfig,
    now: datetime,
) -> Optional[float]:
    """Minutes until both windows are usable for a burn job; None when unknown."""
    waits = _window_waits(quota, cost, config, now)
    if waits is None:
        return None
    return max(waits)


def _binding_window(
    quota: _Quota, five_wait: float, weekly_wait: float
) -> tuple[str, Optional[str]]:
    """Return the window with the larger wait and its reset.

    A full window always has a zero wait, so it can never be the binding window;
    when both windows are immediately usable the weekly reset is used because it
    is the denominator of the normal urgency score.
    """
    if weekly_wait >= five_wait:
        return WINDOW_WEEKLY, quota.weekly_reset
    return WINDOW_FIVE_HOUR, quota.five_reset


def _hours_to(reset: Optional[str], now: datetime, default: float = 1.0) -> float:
    reset_dt = parse_utc(reset)
    if reset_dt is None:
        return default
    return max((reset_dt - now).total_seconds() / 3600.0, MIN_HOURS)


def _expiry_boost(state: AccountState, config: SchedulerConfig, now: datetime) -> float:
    expires = parse_utc(state.expires_at)
    if expires is None or expires <= now:
        return 0.0
    hours = (expires - now).total_seconds() / 3600.0
    horizon = max(1, config.expires_boost_horizon_hours)
    if hours > horizon:
        return 0.0
    return (horizon - hours) / horizon * 100.0


def _urgency(
    weekly_remaining: float,
    weekly_reset: Optional[str],
    config: SchedulerConfig,
    now: datetime,
) -> float:
    """Remaining weekly quota per hour to its reset, or zero for a full window.

    ``remaining / hours`` rewards quota that is both plentiful and close to a
    reset. A full window must not win that comparison on a placeholder reset:
    nothing is at risk of expiring, so its urgency is exactly zero (D115).
    """
    if _is_full_window(weekly_remaining, config):
        return 0.0
    return weekly_remaining / _hours_to(weekly_reset, now)


def _score(
    state: AccountState,
    weekly_remaining: float,
    weekly_reset: Optional[str],
    config: SchedulerConfig,
    now: datetime,
) -> tuple[float, float, float]:
    """Return ``(score, urgency, expiry_boost)`` for one account."""
    urgency = _urgency(weekly_remaining, weekly_reset, config, now)
    boost = _expiry_boost(state, config, now)
    return urgency + boost, urgency, boost


def _sort_key(rank: RankedAccount, state: AccountState, background: bool) -> tuple:
    if background:
        # Burn-eligible accounts come first: a background job exists to consume
        # quota that would otherwise expire, so it prefers an account whose
        # windows are about to reset over one that merely fits right now. Within
        # burn candidates the nearest binding reset, then the smallest remaining
        # quota, then name decide (D109).
        burn_first = 0 if rank.reason == "burn" else 1
        return (
            burn_first,
            rank.wait_minutes,
            rank.remaining_weekly_pct,
            state.active_jobs + state.reserved_jobs,
            state.name,
        )
    last_used = state.last_used_epoch if state.last_used_epoch is not None else float("-inf")
    return (-rank.score, state.active_jobs + state.reserved_jobs, last_used, state.name)


def _earliest_resume(
    states: tuple[AccountState, ...],
    request: JobRequest,
    cost: CostEstimate,
    config: SchedulerConfig,
    now: datetime,
) -> Optional[str]:
    """Earliest time some account's pool could satisfy the strict filter."""
    grouped = _groups(states)
    candidates: list[datetime] = []
    for state in sorted(states, key=lambda item: item.name):
        if _base_eligible(state, request, frozenset()) is not None:
            continue
        quota = _group_quota(grouped, state, request)
        if quota is None:
            continue
        required_five = max(config.five_hour_floor_pct, cost.five_hour_pct * config.safety_factor)
        required_weekly = cost.weekly_pct * config.safety_factor
        candidate = now
        for remaining, required, reset in (
            (quota.five_remaining, required_five, quota.five_reset),
            (quota.weekly_remaining, required_weekly, quota.weekly_reset),
        ):
            if remaining >= required:
                continue
            reset_dt = parse_utc(reset)
            if reset_dt is None:
                candidate = None
                break
            candidate = max(candidate, reset_dt)
        if candidate is not None:
            candidates.append(candidate)
    if not candidates:
        return None
    return format_utc(min(candidates))


def _hours_until(reset: Optional[str], now: datetime) -> Optional[float]:
    """Hours from ``now`` to a reset (zero when past), or None when unparseable."""
    reset_dt = parse_utc(reset)
    if reset_dt is None:
        return None
    return max(0.0, (reset_dt - now).total_seconds() / 3600.0)


def _deciding_rule(
    ranked: list[tuple[RankedAccount, AccountState]],
    background: bool,
    scores: dict[str, tuple[float, float, float]],
) -> str:
    """Name the first ranking criterion that separated the winner from second.

    ``urgency`` covers the normal urgency score and, for background jobs, the
    burn-first/nearest-reset/smallest-remaining order. ``expires_at`` means the
    billing-expiry boost flipped an otherwise-lower urgency. ``lru`` also covers
    the final deterministic account-name tie-break.
    """
    if len(ranked) == 1:
        return "only_candidate"
    winner, winner_state = ranked[0]
    runner, runner_state = ranked[1]
    winner_load = winner_state.active_jobs + winner_state.reserved_jobs
    runner_load = runner_state.active_jobs + runner_state.reserved_jobs
    if background:
        if (winner.reason == "burn") != (runner.reason == "burn"):
            return "urgency"
        if abs(winner.wait_minutes - runner.wait_minutes) > _TIE_EPSILON:
            return "urgency"
        if abs(winner.remaining_weekly_pct - runner.remaining_weekly_pct) > _TIE_EPSILON:
            return "urgency"
        return "load" if winner_load != runner_load else "lru"
    winner_score, winner_urgency, _ = scores[winner.account]
    runner_score, runner_urgency, _ = scores[runner.account]
    if abs(winner_score - runner_score) > _TIE_EPSILON:
        if winner_urgency > runner_urgency + _TIE_EPSILON:
            return "urgency"
        return "expires_at"
    return "load" if winner_load != runner_load else "lru"


def schedule_account(
    states: tuple[AccountState, ...],
    request: JobRequest,
    now: datetime,
    config: Optional[SchedulerConfig] = None,
    history: Optional[HistoryCostModel] = None,
    normal_waiters: frozenset[str] = frozenset(),
) -> SchedulerResult:
    """Choose an account for one job, or report the earliest plausible resume.

    Normal jobs use the strict filter (both windows at or above
    ``cost * safety_factor``, five-hour above its floor) and rank by expiring
    quota. Background jobs may burn an account whose windows are not yet usable
    when the longest wait is within ``burn_horizon_minutes``, and never take the
    last slot while a normal job waits for that account. The clock is injected.
    The result carries a bounded per-account trace for the audit event (D115).
    """
    config = config or SchedulerConfig()
    request = request.validated()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)

    cost = estimate_cost(request, config, history)
    background = request.priority == PRIORITY_BACKGROUND
    grouped = _groups(states)
    ranked: list[tuple[RankedAccount, AccountState]] = []
    scores: dict[str, tuple[float, float, float]] = {}
    trace_rows: list[TraceAccount] = []

    for state in sorted(states, key=lambda item: item.name):
        rejection = _base_eligible(state, request, normal_waiters)
        if rejection is not None:
            trace_rows.append(
                TraceAccount(name=state.name, eligible=False, rejection=rejection)
            )
            continue
        quota = _group_quota(grouped, state, request)
        if quota is None:
            trace_rows.append(
                TraceAccount(
                    name=state.name, eligible=False, rejection="usage_unavailable"
                )
            )
            continue
        required_five = max(config.five_hour_floor_pct, cost.five_hour_pct * config.safety_factor)
        required_weekly = cost.weekly_pct * config.safety_factor
        strict = (
            quota.five_remaining >= required_five
            and quota.weekly_remaining >= required_weekly
        )
        waits = _window_waits(quota, cost, config, now)
        wait = max(waits) if waits is not None else None
        score, _urgency_value, boost = _score(
            state, quota.weekly_remaining, quota.weekly_reset, config, now
        )
        scores[state.name] = (score, _urgency_value, boost)
        if strict:
            eligible = True
            rejection_code: Optional[str] = None
            reason = "strict"
            rank_wait = 0.0
        elif background and wait is not None and wait <= config.burn_horizon_minutes:
            eligible = True
            rejection_code = None
            reason = "burn"
            rank_wait = wait
        else:
            eligible = False
            rejection_code = "quota_exhausted"
            reason = ""
            rank_wait = 0.0
        hours_to_binding_reset: Optional[float] = None
        binding_window: Optional[str] = None
        if eligible:
            five_wait, weekly_wait = waits if waits is not None else (0.0, 0.0)
            binding_window, binding_reset = _binding_window(quota, five_wait, weekly_wait)
            hours_to_binding_reset = _hours_until(binding_reset, now)
            ranked.append(
                (
                    RankedAccount(
                        state.name, score, reason, rank_wait, quota.weekly_remaining
                    ),
                    state,
                )
            )
        trace_rows.append(
            TraceAccount(
                name=state.name,
                eligible=eligible,
                rejection=rejection_code,
                pool=state.pool_key,
                weekly_remaining_pct=round(quota.weekly_remaining, 4),
                five_hour_remaining_pct=round(quota.five_remaining, 4),
                hours_to_binding_reset=(
                    round(hours_to_binding_reset, 4)
                    if hours_to_binding_reset is not None
                    else None
                ),
                binding_window=binding_window,
                urgency_score=round(score, 4),
                expiry_boost=round(boost, 4),
            )
        )

    if not ranked:
        return SchedulerResult(
            account=None,
            estimated_cost=cost,
            exhausted=True,
            resume_after=_earliest_resume(states, request, cost, config, now),
            reason="quota_exhausted",
            trace=DecisionTrace(accounts=tuple(trace_rows[:MAX_TRACE_ACCOUNTS])),
        )

    ranked.sort(key=lambda pair: _sort_key(pair[0], pair[1], background))
    rank_by_name = {
        rank.account: position for position, (rank, _) in enumerate(ranked, start=1)
    }
    traced = tuple(replace(row, rank=rank_by_name.get(row.name)) for row in trace_rows)
    truncated = len(traced) > MAX_TRACE_ACCOUNTS
    return SchedulerResult(
        account=ranked[0][0].account,
        estimated_cost=cost,
        exhausted=False,
        reason=ranked[0][0].reason,
        candidates=tuple(rank for rank, _ in ranked),
        trace=DecisionTrace(
            accounts=traced[:MAX_TRACE_ACCOUNTS],
            winner=ranked[0][0].account,
            rule=_deciding_rule(ranked, background, scores),
            truncated=truncated,
        ),
    )


def account_eligibility(
    states: tuple[AccountState, ...],
    name: str,
    request: JobRequest,
    now: datetime,
    config: Optional[SchedulerConfig] = None,
    allow_burn: bool = False,
) -> Optional[str]:
    """Return None when ``name`` may take the job, else a rejection reason.

    Used by the explicit ``account`` override, which must not silently fall back
    to another account. ``allow_burn`` mirrors the background relaxation. The
    result is computed against the full state set so a shared pool is respected.
    """
    config = config or SchedulerConfig()
    request = request.validated()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    by_name = {state.name: state for state in states}
    state = by_name.get(name)
    if state is None:
        return "account_not_found"
    rejection = _base_eligible(state, request, frozenset())
    if rejection is not None:
        return rejection
    grouped = _groups(states)
    quota = _group_quota(grouped, state, request)
    if quota is None:
        return "usage_unavailable"
    cost = estimate_cost(request, config)
    required_five = max(config.five_hour_floor_pct, cost.five_hour_pct * config.safety_factor)
    required_weekly = cost.weekly_pct * config.safety_factor
    if (
        quota.five_remaining >= required_five
        and quota.weekly_remaining >= required_weekly
    ):
        return None
    if allow_burn:
        wait = _wait_minutes(quota, cost, config, now)
        if wait is not None and wait <= config.burn_horizon_minutes:
            return None
    return "quota_exhausted"


def remaining_until_reset(reset_time_utc: Optional[str], now: datetime) -> Optional[timedelta]:
    """Return the time until a reset, clamped at zero, or None when unparseable."""
    reset_dt = parse_utc(reset_time_utc)
    if reset_dt is None:
        return None
    return max(timedelta(0), reset_dt - now)
