"""Typed per-account quota pools and windows.

Owns the immutable view of one account's ``/usage`` reading: both pools
(``Gemini Models`` and ``Claude and GPT models``) and both windows (weekly and
five-hour) with remaining fraction, reset time and reset countdown. It must never
perform I/O, never raise on a malformed reading (an unavailable account is a
first-class result) and never carry an unmasked email.
"""

from dataclasses import dataclass
from typing import Any, Optional

POOL_GEMINI = "Gemini Models"
POOL_CLAUDE_GPT = "Claude and GPT models"
WINDOW_WEEKLY = "weekly"
WINDOW_FIVE_HOUR = "5h"
POOL_NAMES = (POOL_GEMINI, POOL_CLAUDE_GPT)


@dataclass(frozen=True)
class WindowReading:
    """One quota window of one pool: remaining fraction and its reset."""

    window: str
    remaining_fraction: float
    reset_time_utc: Optional[str] = None
    resets_in_seconds: Optional[int] = None

    @property
    def remaining_pct(self) -> float:
        """Remaining quota as a percentage in ``[0, 100]``."""
        return round(self.remaining_fraction * 100.0, 4)


@dataclass(frozen=True)
class PoolReading:
    """One quota pool and its windows."""

    name: str
    windows: tuple[WindowReading, ...] = ()

    def window(self, window: str) -> Optional[WindowReading]:
        """Return the named window (``weekly`` or ``5h``) or None."""
        for reading in self.windows:
            if reading.window == window:
                return reading
        return None

    @property
    def weekly(self) -> Optional[WindowReading]:
        """The weekly window, or None when the pool did not report it."""
        return self.window(WINDOW_WEEKLY)

    @property
    def five_hour(self) -> Optional[WindowReading]:
        """The five-hour window, or None when the pool did not report it."""
        return self.window(WINDOW_FIVE_HOUR)


@dataclass(frozen=True)
class AccountUsage:
    """One account's typed usage reading.

    ``available=False`` carries a machine-readable ``reason`` (for example
    ``auth_required``, ``timeout`` or ``account_identity_mismatch``) so callers
    can skip the account without failing a start.
    """

    account: str
    available: bool
    pools: tuple[PoolReading, ...] = ()
    reason: Optional[str] = None
    masked_email: Optional[str] = None
    fetched_at: Optional[str] = None
    cached: bool = False
    identity: str = "unchecked"
    identity_sha256: Optional[str] = None

    def pool(self, name: str) -> Optional[PoolReading]:
        """Return the named pool or None."""
        for reading in self.pools:
            if reading.name == name:
                return reading
        return None

    def pool_for_model(self, model: Optional[str]) -> Optional[PoolReading]:
        """Return the pool that serves ``model``; None when it cannot be mapped."""
        name = pool_for_model(model)
        if name is None:
            if len(self.pools) == 1:
                return self.pools[0]
            return None
        return self.pool(name)


def pool_for_model(model: Optional[str]) -> Optional[str]:
    """Map an agy model identifier to its quota pool name.

    Mirrors ``usage.group_for_model`` but stays local so the typed view has no
    dependency on the parser module at import time. An unknown model returns None
    rather than guessing a pool.
    """
    name = (model or "").strip().lower()
    if name.startswith("gemini"):
        return POOL_GEMINI
    if name.startswith("claude") or name.startswith("gpt"):
        return POOL_CLAUDE_GPT
    return None


def identity_pool_key(masked_email: str) -> str:
    """Return the shared pool key for accounts that report the same login.

    Two named accounts that authenticate as the same masked email are one quota
    pool even when they have different HOMEs: the provider counts the login, not
    the directory. The masked form is already the only identity this wrapper
    keeps, so it can become the key without storing a full address (D116).
    """
    return f"identity:{masked_email}"


def _window_reading(raw: Any) -> Optional[WindowReading]:
    if not isinstance(raw, dict):
        return None
    window = raw.get("window")
    fraction = raw.get("remaining_fraction")
    if not isinstance(window, str) or not window.strip():
        return None
    if isinstance(fraction, bool) or not isinstance(fraction, (int, float)):
        return None
    value = float(fraction)
    if value < 0.0 or value > 1.0:
        return None
    # The parser normalizes to reset_time_utc; accept reset_time too so a raw
    # /usage group can be typed without a round trip through the parser.
    reset = raw.get("reset_time_utc") or raw.get("reset_time")
    reset_time = reset if isinstance(reset, str) and reset else None
    countdown = raw.get("resets_in_seconds")
    resets_in = (
        countdown if isinstance(countdown, int) and not isinstance(countdown, bool) else None
    )
    return WindowReading(
        window=window.strip(),
        remaining_fraction=value,
        reset_time_utc=reset_time,
        resets_in_seconds=resets_in,
    )


def account_usage_from_reading(account_name: str, reading: Any) -> AccountUsage:
    """Build a typed :class:`AccountUsage` from a ``get_usage`` reading.

    Fail-soft: a malformed or unavailable reading becomes ``available=False``
    with the reading's reason (or ``unrecognized_format``), never an exception.
    The account dict contributes only ``masked_email`` and ``identity``.
    """
    if not isinstance(reading, dict):
        return AccountUsage(account=account_name, available=False, reason="unrecognized_format")

    account_info = reading.get("account")
    account_info = account_info if isinstance(account_info, dict) else {}
    masked_email = account_info.get("masked_email")
    identity = account_info.get("identity", "unchecked")
    if not isinstance(identity, str):
        identity = "unchecked"
    identity_sha256 = account_info.get("identity_sha256")
    if not isinstance(identity_sha256, str):
        identity_sha256 = None

    if reading.get("available") is not True:
        reason = reading.get("reason")
        return AccountUsage(
            account=account_name,
            available=False,
            reason=str(reason) if reason else "unrecognized_format",
            masked_email=masked_email if isinstance(masked_email, str) else None,
            fetched_at=reading.get("fetched_at")
            if isinstance(reading.get("fetched_at"), str)
            else None,
            cached=bool(reading.get("cached", False)),
            identity=identity,
            identity_sha256=identity_sha256,
        )

    pools: list[PoolReading] = []
    raw_groups = reading.get("groups")
    if isinstance(raw_groups, list):
        for group in raw_groups:
            if not isinstance(group, dict):
                continue
            name = group.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            windows: list[WindowReading] = []
            raw_buckets = group.get("buckets")
            if isinstance(raw_buckets, list):
                for bucket in raw_buckets:
                    parsed = _window_reading(bucket)
                    if parsed is not None:
                        windows.append(parsed)
            if windows:
                pools.append(PoolReading(name=name.strip(), windows=tuple(windows)))

    if not pools:
        return AccountUsage(
            account=account_name,
            available=False,
            reason="unrecognized_format",
            masked_email=masked_email if isinstance(masked_email, str) else None,
            cached=bool(reading.get("cached", False)),
            identity=identity,
            identity_sha256=identity_sha256,
        )

    return AccountUsage(
        account=account_name,
        available=True,
        pools=tuple(pools),
        masked_email=masked_email if isinstance(masked_email, str) else None,
        fetched_at=(
            reading.get("fetched_at") if isinstance(reading.get("fetched_at"), str) else None
        ),
        cached=bool(reading.get("cached", False)),
        identity=identity,
        identity_sha256=identity_sha256,
    )
