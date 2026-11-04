"""Gather per-account state and select an account for one job (D102–D109).

Owns the I/O around the pure scheduler: validate an account home, check policy
parity, read that account's ``/usage`` with the right cache key and identity
expectation, count its active jobs and reservations, then call
``schedule_account``. It must never log in/out, never read a token or keyring,
never write into an account home, and never invent an eligible account when the
scheduler says the quota is exhausted. It is the only layer that attaches masked
emails to the scheduler's decision trace.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from agy_mcp.accounts.errors import AccountError
from agy_mcp.accounts.policy import PolicyCheck, check_policy_parity
from agy_mcp.accounts.pools import AccountUsage, account_usage_from_reading, identity_pool_key
from agy_mcp.accounts.registry import (
    DEFAULT_ACCOUNT_NAME,
    AccountConfig,
    AccountRegistry,
    validate_account_home,
)
from agy_mcp.accounts.scheduler import (
    MAX_TRACE_ACCOUNTS,
    PRIORITY_BACKGROUND,
    AccountState,
    DecisionTrace,
    JobRequest,
    SchedulerConfig,
    SchedulerResult,
    account_eligibility,
    estimate_cost,
    schedule_account,
)
from agy_mcp.config import Config
from agy_mcp.jobs import job_belongs_to_account, list_jobs
from agy_mcp.models import Job, JobState
from agy_mcp.usage import UsageCache, get_usage

_REASON_CODES = {
    "disabled": "account_disabled",
    "identity_unverified": "account_identity_mismatch",
    "policy_unverified": "account_permission_policy_missing",
    "model_unavailable": "account_model_unavailable",
    "usage_unavailable": "account_usage_unavailable",
    "account_concurrency": "account_concurrency_limit",
    "reserved_for_normal": "account_concurrency_limit",
    "quota_exhausted": "account_not_eligible",
    "account_not_found": "account_not_found",
}


@dataclass(frozen=True)
class RejectedAccount:
    """One account the scheduler could not use, with the reason."""

    name: str
    reason: str
    masked_email: Optional[str] = None


@dataclass(frozen=True)
class Selection:
    """The chosen account (or exhaustion) plus the evidence behind it."""

    account: Optional[AccountConfig]
    result: SchedulerResult
    states: tuple[AccountState, ...]
    masked_email: Optional[str] = None
    policy: Optional[PolicyCheck] = None
    rejections: tuple[RejectedAccount, ...] = ()
    trace: Optional[DecisionTrace] = None

    @property
    def account_name(self) -> Optional[str]:
        """The selected registry name, or None when no account was chosen."""
        return self.account.name if self.account is not None else None


def scheduler_config_from_config(config: Config) -> SchedulerConfig:
    """Translate the tunable config keys into the scheduler's value object."""
    return SchedulerConfig(
        tokens_per_minute=float(config.account_cost_tokens_per_minute),
        expected_minutes=int(config.account_cost_expected_minutes),
        safety_factor=float(config.account_cost_safety_factor),
        five_hour_floor_pct=float(config.account_five_hour_floor_pct),
        full_window_margin_pct=float(config.account_full_window_margin_pct),
        burn_horizon_minutes=int(config.account_burn_horizon_minutes),
        expires_boost_horizon_hours=int(config.account_expires_boost_horizon_hours),
        five_hour_per_1k_tokens=float(config.quota_estimate_five_hour_per_1k_tokens),
        weekly_per_1k_tokens=float(config.quota_estimate_weekly_per_1k_tokens),
    )


def _trace_with_emails(
    trace: Optional[DecisionTrace], masked_by_name: dict[str, Optional[str]]
) -> Optional[DecisionTrace]:
    """Return the trace with each row's masked email filled in.

    The scheduler never sees an address; this module owns the usage readings, so
    it is the only place that can attach the masked form. ``replace`` keeps the
    pure trace immutable.
    """
    if trace is None:
        return None
    rows = tuple(
        replace(row, masked_email=masked_by_name.get(row.name)) for row in trace.accounts
    )
    return replace(trace, accounts=rows)


def decision_trace_detail(trace: Optional[DecisionTrace]) -> Optional[dict[str, Any]]:
    """Render a :class:`DecisionTrace` as the additive audit detail (D115).

    The shape is JSON-serializable and bounded by ``MAX_TRACE_ACCOUNTS``. A row
    for an ineligible account carries only its name, eligibility and rejection
    code; an eligible row also carries the ranking inputs and its 1-based rank.
    The winner's masked email is already in the parent event detail, and every
    row repeats only the masked form, never a full address.
    """
    if trace is None:
        return None
    accounts: list[dict[str, Any]] = []
    for row in trace.accounts[:MAX_TRACE_ACCOUNTS]:
        item: dict[str, Any] = {
            "account": row.name,
            "eligible": row.eligible,
        }
        if row.masked_email is not None:
            item["account_masked_email"] = row.masked_email
        if not row.eligible:
            item["rejection"] = row.rejection
            accounts.append(item)
            continue
        item["pool"] = row.pool
        item["weekly_remaining_pct"] = row.weekly_remaining_pct
        item["five_hour_remaining_pct"] = row.five_hour_remaining_pct
        item["hours_to_binding_reset"] = row.hours_to_binding_reset
        item["binding_window"] = row.binding_window
        item["urgency_score"] = row.urgency_score
        item["expiry_boost"] = row.expiry_boost
        item["rank"] = row.rank
        accounts.append(item)
    detail: dict[str, Any] = {"accounts": accounts}
    if trace.winner is not None:
        detail["winner"] = trace.winner
    if trace.rule:
        detail["rule"] = trace.rule
    if trace.truncated:
        detail["truncated"] = True
    return detail


def _is_active(job: Job) -> bool:
    return job.state in (JobState.QUEUED, JobState.RUNNING) or getattr(
        job.state, "value", str(job.state)
    ) == "starting"


def _reservation(job: Job) -> tuple[float, float]:
    reservation = job.quota_reservation
    if not isinstance(reservation, dict):
        return 0.0, 0.0
    try:
        return float(reservation.get("five_hour_pct", 0.0)), float(
            reservation.get("weekly_pct", 0.0)
        )
    except (TypeError, ValueError):
        return 0.0, 0.0


def shared_identity_pools(
    states: tuple[AccountState, ...],
    masked_by_name: dict[str, Optional[str]],
) -> tuple[dict[str, str], dict[str, str]]:
    """Return the shared-pool keys and representatives for one verified login.

    Enabled accounts whose ``/usage`` readings report the identical masked email
    are one Google login when at least one of them verified an identity
    expectation. The scheduler already merges accounts by ``pool_key``; the first
    returned map supplies the matching key for the observed collision (the
    2026-11-05 observation, D116). The second maps each shared key to a
    representative: the lowest declared account, because its isolated HOME
    reading is the authoritative one.

    A group whose members all have an unverified masked value is not merged: the
    implicit default's value comes from the shared keyring and can change under
    the desktop app, so it is not evidence that two independent quotas are one.
    The doctor reports that case for every enabled account.
    """
    by_identity: dict[str, list[str]] = {}
    verified: set[str] = set()
    masked_by_identity: dict[str, str] = {}
    for state in states:
        if not state.enabled:
            continue
        usage = state.usage
        if usage is None:
            continue
        identity_key = usage.identity_sha256
        if not identity_key:
            identity_key = masked_by_name.get(state.name)
        if not identity_key:
            continue
        by_identity.setdefault(identity_key, []).append(state.name)
        masked = masked_by_name.get(state.name)
        if masked:
            masked_by_identity.setdefault(identity_key, masked)
        if usage.identity == "matched":
            verified.add(state.name)
    overrides: dict[str, str] = {}
    representatives: dict[str, str] = {}
    for identity_key, names in by_identity.items():
        if len(names) < 2 or not any(name in verified for name in names):
            continue
        declared = sorted(name for name in names if name != DEFAULT_ACCOUNT_NAME)
        representative = declared[0] if declared else sorted(names)[0]
        label = masked_by_name.get(representative) or masked_by_identity.get(identity_key)
        if not label:
            continue
        key = identity_pool_key(label)
        representatives[key] = representative
        for name in names:
            overrides[name] = key
    return overrides, representatives


class AccountSelector:
    """Read the live account state and delegate the decision to the scheduler."""

    def __init__(
        self,
        config: Config,
        registry: AccountRegistry,
        real_home: Path,
        agy_binary: str,
        usage_cache: Optional[UsageCache] = None,
        state_dir: Optional[Path] = None,
        now_func: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.config = config
        self.registry = registry
        self.real_home = real_home
        self.agy_binary = agy_binary
        self.usage_cache = usage_cache or UsageCache()
        self.state_dir = state_dir
        self._now_func = now_func or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        now = self._now_func()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)

    def _usage_for(self, account: AccountConfig) -> tuple[AccountUsage, Optional[str]]:
        configured = self.registry.configured
        reading = get_usage(
            self.agy_binary,
            home=account.expanded_home,
            cache=self.usage_cache,
            use_cache=self.config.account_usage_max_age_seconds > 0,
            account_name=account.name if configured else None,
            expected_email=account.expected_email,
            expected_email_sha256=account.expected_email_sha256,
            auth_error_patterns=self.config.auth_error_patterns or None,
            # A non-default account must probe with the isolated env (dead
            # D-Bus); without it agy authenticates through the desktop keyring
            # as the primary login (human test 2026-11-03).
            account=account,
        )
        usage = account_usage_from_reading(account.name, reading)
        account_info = reading.get("account") if isinstance(reading, dict) else None
        masked = None
        if isinstance(account_info, dict):
            masked = account_info.get("masked_email")
        return usage, masked if isinstance(masked, str) else None

    def _policy_for(self, account: AccountConfig) -> Optional[PolicyCheck]:
        if account.is_default:
            return None
        return check_policy_parity(account.expanded_home, self.real_home)

    def _active_jobs(self, account: AccountConfig) -> list[Job]:
        return [
            job
            for job in list_jobs(self.state_dir)
            if job_belongs_to_account(job, account.name) and _is_active(job)
        ]

    def _state_for(
        self, account: AccountConfig, policy: Optional[PolicyCheck]
    ) -> tuple[AccountState, Optional[str]]:
        """Build one AccountState and return (state, masked email)."""
        home_error: Optional[str] = None
        if not account.is_default:
            try:
                validate_account_home(account)
            except AccountError as exc:
                home_error = exc.code
        if home_error is not None or not account.enabled:
            # Do not probe an account that cannot be used: an unauthenticated
            # HOME starts an interactive OAuth wait, so an unused account must
            # never cost a 30 s /usage timeout (Phase 1 experiment 1).
            usage = AccountUsage(
                account=account.name,
                available=False,
                reason=home_error or "disabled",
            )
            masked = None
        else:
            usage, masked = self._usage_for(account)
        active = self._active_jobs(account)
        reserved_five = sum(_reservation(job)[0] for job in active)
        reserved_weekly = sum(_reservation(job)[1] for job in active)
        identity_ok = usage.identity not in (
            "account_identity_mismatch",
            "account_identity_unknown",
        )
        policy_ok = policy.ok if policy is not None else True
        state = AccountState(
            name=account.name,
            pool_key=account.pool_key,
            enabled=account.enabled,
            identity_ok=identity_ok,
            home_error=home_error,
            policy_ok=policy_ok,
            model_ok=True,
            usage=usage,
            active_jobs=len(active),
            reserved_jobs=0,
            max_concurrent_jobs=account.max_concurrent_jobs
            or self.config.max_concurrent_jobs,
            reserved_five_hour_pct=reserved_five,
            reserved_weekly_pct=reserved_weekly,
            expires_at=account.expires_at,
        )
        return state, masked

    def select(
        self,
        model: str,
        max_minutes: int,
        priority: str = "normal",
        account: Optional[str] = None,
        force: bool = False,
    ) -> Selection:
        """Choose an account for one start, or return the exhaustion result.

        An explicit ``account`` must pass the same checks unless ``force`` skips
        only the quota filter; it never skips identity, policy, home or cap. A
        disabled implicit default is rejected with ``default_account_disabled``,
        and a registry with no enabled account with ``account_none_enabled``
        (D116).
        """
        if account == DEFAULT_ACCOUNT_NAME and not self.registry.default.enabled:
            raise AccountError(
                "the default account is disabled; pass a configured account",
                code="default_account_disabled",
            )
        if not any(candidate.enabled for candidate in self.registry.all()):
            raise AccountError(
                "no configured account is enabled; enable one or the default account",
                code="account_none_enabled",
            )
        now = self._now()
        request = JobRequest(model, max_minutes, priority)
        scheduler_config = scheduler_config_from_config(self.config)
        jobs = list_jobs(self.state_dir)
        normal_waiters = frozenset(
            job.account
            for job in jobs
            if job.state == JobState.QUEUED and job.priority != PRIORITY_BACKGROUND
        )
        states: list[AccountState] = []
        masked_by_name: dict[str, Optional[str]] = {}
        policies: dict[str, Optional[PolicyCheck]] = {}
        for config_account in self.registry.all():
            policy = self._policy_for(config_account)
            policies[config_account.name] = policy
            state, masked = self._state_for(config_account, policy)
            states.append(state)
            masked_by_name[config_account.name] = masked
        state_tuple = tuple(states)
        pool_overrides, representatives = shared_identity_pools(
            state_tuple, masked_by_name
        )
        if pool_overrides:
            # The scheduler sees one pool key per observed login, so a duplicate
            # identity can never be counted as two independent quota buckets. The
            # implicit default is the shared-keyring alias: when it collides with
            # a declared account, that account's isolated reading is authoritative
            # for the shared login, because the keyring reading can change under
            # the desktop app (D116).
            by_name = {state.name: state for state in state_tuple}
            adjusted: list[AccountState] = []
            for state in state_tuple:
                pool_key = pool_overrides.get(state.name, state.pool_key)
                usage = state.usage
                if state.name == DEFAULT_ACCOUNT_NAME and state.name in pool_overrides:
                    representative = by_name.get(representatives.get(pool_key, ""), None)
                    if (
                        representative is not None
                        and representative.name != state.name
                        and representative.usage is not None
                    ):
                        usage = replace(representative.usage, account=state.name)
                adjusted.append(replace(state, pool_key=pool_key, usage=usage))
            state_tuple = tuple(adjusted)
        rejections = tuple(
            RejectedAccount(
                name=state.name,
                reason=reason,
                masked_email=masked_by_name.get(state.name),
            )
            for state in state_tuple
            if (reason := account_eligibility(
                state_tuple,
                state.name,
                request,
                now,
                scheduler_config,
                allow_burn=priority == PRIORITY_BACKGROUND,
            ))
            is not None
        )

        if account is not None:
            config_account = self.registry.by_name(account)
            reason = account_eligibility(
                state_tuple,
                account,
                request,
                now,
                scheduler_config,
                allow_burn=priority == PRIORITY_BACKGROUND,
            )
            if reason is not None and not (force and reason == "quota_exhausted"):
                code = _REASON_CODES.get(
                    reason,
                    reason if reason.startswith("account_") else "account_not_eligible",
                )
                if reason == "policy_unverified":
                    policy_status = (
                        policies.get(account).status if policies.get(account) else "missing"
                    )
                    code = (
                        "account_permission_policy_drift"
                        if policy_status == "drift"
                        else "account_permission_policy_missing"
                    )
                raise AccountError(
                    f"account '{account}' cannot take this job: {reason}",
                    code=code,
                )
            # The automatic ranking is still computed so the audit trace records
            # every considered account; the explicit override only replaces the
            # winner and the deciding rule (D115).
            ranking = schedule_account(
                state_tuple,
                request,
                now,
                scheduler_config,
                normal_waiters=frozenset(),
            )
            trace = replace(
                ranking.trace,
                winner=account,
                rule="force" if force else "explicit_account",
            )
            result = SchedulerResult(
                account=account,
                estimated_cost=estimate_cost(request, scheduler_config),
                exhausted=False,
                reason="forced" if force else "override",
                trace=trace,
            )
            return Selection(
                account=config_account,
                result=result,
                states=state_tuple,
                masked_email=masked_by_name.get(account),
                policy=policies.get(account),
                rejections=rejections,
                trace=_trace_with_emails(trace, masked_by_name),
            )

        result = schedule_account(
            state_tuple,
            request,
            now,
            scheduler_config,
            normal_waiters=normal_waiters,
        )
        if result.account is None:
            structural = [
                rejected
                for rejected in rejections
                if rejected.reason not in ("quota_exhausted", "usage_unavailable")
            ]
            if structural and not any(
                rejected.reason == "quota_exhausted" for rejected in rejections
            ):
                first = structural[0]
                code = _REASON_CODES.get(
                    first.reason,
                    first.reason
                    if first.reason.startswith("account_")
                    else "account_not_eligible",
                )
                policy = policies.get(first.name)
                if first.reason == "policy_unverified" and policy is not None:
                    code = (
                        "account_permission_policy_drift"
                        if policy.status == "drift"
                        else "account_permission_policy_missing"
                    )
                raise AccountError(
                    f"account '{first.name}' cannot take this job: {first.reason}",
                    code=code,
                )
        chosen = self.registry.by_name(result.account) if result.account else None
        return Selection(
            account=chosen,
            result=result,
            states=state_tuple,
            masked_email=masked_by_name.get(result.account) if result.account else None,
            policy=policies.get(result.account) if result.account else None,
            rejections=rejections,
            trace=_trace_with_emails(result.trace, masked_by_name),
        )
