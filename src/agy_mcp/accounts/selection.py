"""Gather per-account state and select an account for one job (D102–D109).

Owns the I/O around the pure scheduler: validate an account home, check policy
parity, read that account's ``/usage`` with the right cache key and identity
expectation, count its active jobs and reservations, then call
``schedule_account``. It must never log in/out, never read a token or keyring,
never write into an account home, and never invent an eligible account when the
scheduler says the quota is exhausted.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from agy_mcp.accounts.errors import AccountError
from agy_mcp.accounts.policy import PolicyCheck, check_policy_parity
from agy_mcp.accounts.pools import AccountUsage, account_usage_from_reading
from agy_mcp.accounts.registry import AccountConfig, AccountRegistry, validate_account_home
from agy_mcp.accounts.scheduler import (
    PRIORITY_BACKGROUND,
    AccountState,
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
        burn_horizon_minutes=int(config.account_burn_horizon_minutes),
        expires_boost_horizon_hours=int(config.account_expires_boost_horizon_hours),
        five_hour_per_1k_tokens=float(config.quota_estimate_five_hour_per_1k_tokens),
        weekly_per_1k_tokens=float(config.quota_estimate_weekly_per_1k_tokens),
    )


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
        only the quota filter; it never skips identity, policy, home or cap.
        """
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
            result = SchedulerResult(
                account=account,
                estimated_cost=estimate_cost(request, scheduler_config),
                exhausted=False,
                reason="forced" if force else "override",
            )
            return Selection(
                account=config_account,
                result=result,
                states=state_tuple,
                masked_email=masked_by_name.get(account),
                policy=policies.get(account),
                rejections=rejections,
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
        )
