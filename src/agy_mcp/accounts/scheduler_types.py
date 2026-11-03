"""Value objects for the pure account scheduler (D108, D109).

Owns the immutable inputs and outputs of the scheduling decision: the tunable
config, one account's verified state, the job request, the cost estimate, the
result and the bounded audit trace. It performs no I/O and holds no logic beyond
request validation, so the scheduler module can stay a small decision function.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from agy_mcp.accounts.pools import AccountUsage

PRIORITY_NORMAL = "normal"
PRIORITY_BACKGROUND = "background"
PRIORITIES = (PRIORITY_NORMAL, PRIORITY_BACKGROUND)

MIN_HOURS = 1.0 / 60.0

# Hard bound on the decision trace carried by one audit event. ``max_accounts``
# is already capped at 32 by config validation, so this normally truncates
# nothing; it exists so a future registry bug cannot grow the audit line without
# bound. A truncated trace records ``DecisionTrace.truncated = True``.
MAX_TRACE_ACCOUNTS = 32


def parse_utc(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp into an aware UTC datetime, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_utc(value: datetime) -> str:
    """Format an aware datetime as the wrapper's second-precision UTC string."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class CostEstimate:
    """A pre-launch estimate of one job's quota cost, in percentage points."""

    five_hour_pct: float
    weekly_pct: float
    estimated_minutes: int
    tokens: int


@dataclass(frozen=True)
class HistoryCostModel:
    """Optional history-derived refinement for the cost estimate.

    ``rate_p75_tokens_per_minute`` is the conservative (upper-quartile) observed
    rate; ``median_minutes`` is the observed median duration. Both are computed
    outside the scheduler so the decision stays pure.
    """

    rate_p75_tokens_per_minute: float
    median_minutes: float


@dataclass(frozen=True)
class SchedulerConfig:
    """Tunable scheduler thresholds; defaults are heuristics from finding 08."""

    tokens_per_minute: float = 35300.0
    expected_minutes: int = 30
    safety_factor: float = 1.5
    five_hour_floor_pct: float = 15.0
    burn_horizon_minutes: int = 90
    expires_boost_horizon_hours: int = 168
    five_hour_per_1k_tokens: float = 0.011
    weekly_per_1k_tokens: float = 0.0031
    # A window whose remaining percentage is at or above
    # ``100 - full_window_margin_pct`` is "full": no quota is at risk of
    # expiring, so it contributes zero urgency and is never the binding window
    # for burn mode. The 0.1% margin absorbs float noise in ``/usage`` (a real
    # reading is a fraction such as 1.0, but a rounded 99.95 must still count).
    full_window_margin_pct: float = 0.1


@dataclass(frozen=True)
class AccountState:
    """Everything the scheduler may consider about one account.

    ``reserved_*`` are the estimated costs of the account's running/queued jobs.
    ``last_used_epoch`` is any monotonic or wall-clock value; only comparisons
    matter. ``expires_at`` is the optional billing expiry (informational).
    """

    name: str
    pool_key: str
    enabled: bool = True
    identity_ok: bool = True
    policy_ok: bool = True
    home_error: Optional[str] = None
    model_ok: bool = True
    usage: Optional[AccountUsage] = None
    active_jobs: int = 0
    reserved_jobs: int = 0
    max_concurrent_jobs: int = 2
    reserved_five_hour_pct: float = 0.0
    reserved_weekly_pct: float = 0.0
    last_used_epoch: Optional[float] = None
    expires_at: Optional[str] = None


@dataclass(frozen=True)
class JobRequest:
    """The scheduling request for one job."""

    model: Optional[str]
    max_minutes: int
    priority: str = PRIORITY_NORMAL

    def validated(self) -> "JobRequest":
        """Return self after checking the priority and minute budget."""
        if self.priority not in PRIORITIES:
            raise ValueError(f"priority must be one of {PRIORITIES}, got {self.priority!r}")
        if self.max_minutes < 1:
            raise ValueError("max_minutes must be >= 1")
        return self


@dataclass(frozen=True)
class RankedAccount:
    """One eligible account and why it ranked where it did."""

    account: str
    score: float
    reason: str
    wait_minutes: float
    remaining_weekly_pct: float = 0.0


@dataclass(frozen=True)
class TraceAccount:
    """One account's audit-trace row (D115).

    Rejected accounts carry ``eligible=False`` and a machine-readable
    ``rejection``. Eligible accounts carry the ranking inputs that produced
    their ``rank`` (1 is the winner). ``masked_email`` is filled in by the
    caller that owns the usage readings; the pure scheduler never sees an
    address.
    """

    name: str
    eligible: bool = False
    rejection: Optional[str] = None
    pool: Optional[str] = None
    weekly_remaining_pct: Optional[float] = None
    five_hour_remaining_pct: Optional[float] = None
    hours_to_binding_reset: Optional[float] = None
    binding_window: Optional[str] = None
    urgency_score: Optional[float] = None
    expiry_boost: Optional[float] = None
    rank: Optional[int] = None
    masked_email: Optional[str] = None


@dataclass(frozen=True)
class DecisionTrace:
    """Why one account won, for the additive ``account_selected`` detail (D115).

    ``rule`` is the first ranking criterion that separated the winner from the
    runner-up: ``urgency``, ``expires_at``, ``load``, ``lru``,
    ``only_candidate``, ``explicit_account`` or ``force``. ``lru`` also covers
    the final deterministic account-name tie-break.
    """

    accounts: tuple[TraceAccount, ...] = ()
    winner: Optional[str] = None
    rule: str = ""
    truncated: bool = False


@dataclass(frozen=True)
class SchedulerResult:
    """The scheduler's decision or its exhaustion reason."""

    account: Optional[str]
    estimated_cost: Optional[CostEstimate]
    exhausted: bool
    resume_after: Optional[str] = None
    reason: str = "ok"
    candidates: tuple[RankedAccount, ...] = ()
    trace: DecisionTrace = DecisionTrace()
