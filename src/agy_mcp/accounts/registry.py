"""Parse and validate ``[[accounts]]`` entries; build the account registry.

Owns the :class:`AccountConfig` dataclass, the reserved ``default`` name, the
structural validation of the registry (no filesystem access) and the registry
view later phases consume. It must never open an account home, read a token or run
agy: the filesystem home check is a separate function called by start and doctor,
so loading ``config.toml`` still only reads that one file.
"""

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from agy_mcp.accounts.errors import AccountError

DEFAULT_ACCOUNT_NAME = "default"
MAX_ACCOUNTS_LIMIT = 32

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_POOL_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_ENTRY_KEYS = {
    "name",
    "home",
    "pool",
    "enabled",
    "max_concurrent_jobs",
    "expires_at",
    "expected_email",
    "expected_email_sha256",
}


@dataclass(frozen=True)
class DefaultAccountConfig:
    """The optional ``[default_account]`` table for the implicit default.

    Every field defaults to today's behavior: enabled with no identity
    expectation. A configured expectation is the same email/hash pair an
    ``[[accounts]]`` entry accepts, so the implicit default becomes verifiable
    too (D116).
    """

    enabled: bool = True
    expected_email: Optional[str] = None
    expected_email_sha256: Optional[str] = None


@dataclass(frozen=True)
class AccountConfig:
    """One account from the registry, or the implicit ``default`` account.

    ``home`` is stored exactly as configured (``~`` allowed); use
    :attr:`expanded_home` when a real path is needed. ``is_default`` is True only
    for the implicit account, which cannot be declared in config.
    """

    name: str
    home: str
    pool: Optional[str] = None
    enabled: bool = True
    max_concurrent_jobs: Optional[int] = None
    expires_at: Optional[str] = None
    expected_email: Optional[str] = None
    expected_email_sha256: Optional[str] = None
    is_default: bool = False

    @property
    def expanded_home(self) -> Path:
        """The account's HOME with ``~`` and environment references expanded."""
        return Path(os.path.expanduser(self.home))

    @property
    def agy_home(self) -> Path:
        """The agy state directory under this account's HOME."""
        return self.expanded_home / ".gemini" / "antigravity-cli"

    @property
    def pool_key(self) -> str:
        """The quota bucket key: the declared pool label, or the account name."""
        return self.pool or self.name


@dataclass(frozen=True)
class AccountRegistry:
    """The implicit default plus every declared account.

    ``accounts`` holds only the declared entries; :meth:`all` is the full list
    callers should iterate. An empty ``accounts`` is the single-account install
    and must behave exactly like the pre-multi-account server.
    """

    default: AccountConfig
    accounts: tuple[AccountConfig, ...] = ()

    def all(self) -> tuple[AccountConfig, ...]:
        """Return the default account followed by every declared account."""
        return (self.default, *self.accounts)

    def enabled(self) -> tuple[AccountConfig, ...]:
        """Return the accounts whose ``enabled`` flag is true."""
        return tuple(account for account in self.all() if account.enabled)

    def by_name(self, name: str) -> AccountConfig:
        """Return the account called ``name`` or raise ``account_not_found``."""
        for account in self.all():
            if account.name == name:
                return account
        raise AccountError(f"unknown account '{name}'", code="account_not_found")

    @property
    def configured(self) -> bool:
        """True when at least one account is declared (the single-account default)."""
        return bool(self.accounts)


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AccountError(f"'{field}' must be a non-empty string", code="account_config_invalid")
    return value.strip()


def _parse_iso_timestamp(value: Any) -> str:
    text = _require_string(value, "expires_at")
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AccountError(
            "'expires_at' must be an ISO-8601 timestamp", code="account_config_invalid"
        ) from exc
    return text


def _parse_expectations(
    entry: dict[str, Any], label: str
) -> tuple[Optional[str], Optional[str]]:
    """Validate one entry's optional email expectation pair.

    ``label`` names the entry in an error message ("account 'a'" or "the default
    account"). At most one of ``expected_email`` / ``expected_email_sha256`` may
    be set, exactly as an ``[[accounts]]`` entry; messages never echo the email.
    """
    expected_email = entry.get("expected_email")
    expected_hash = entry.get("expected_email_sha256")
    if expected_email is not None and expected_hash is not None:
        raise AccountError(
            f"{label} may set at most one of expected_email and expected_email_sha256",
            code="account_config_invalid",
        )
    if expected_email is not None:
        expected_email = _require_string(expected_email, "expected_email")
        local, _, domain = expected_email.rpartition("@")
        if not local or not domain or expected_email.count("@") != 1:
            raise AccountError(
                f"{label} expected_email is not a valid address",
                code="account_config_invalid",
            )
    if expected_hash is not None:
        expected_hash = _require_string(expected_hash, "expected_email_sha256")
        if not _SHA256_RE.match(expected_hash):
            raise AccountError(
                f"{label} expected_email_sha256 must be 64 lowercase hex characters",
                code="account_config_invalid",
            )
    return expected_email, expected_hash


def parse_default_account(raw: Any) -> Optional[DefaultAccountConfig]:
    """Validate the optional ``[default_account]`` table.

    ``None`` (no table) returns ``None`` so the registry can keep today's
    implicit account byte-identical. Otherwise ``enabled`` must be a bool
    (default true) and the email/hash pair follows the same rule as an
    ``[[accounts]]`` entry. Pure and filesystem-free; raises
    :class:`AccountError` with a stable code on any violation.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise AccountError(
            "'default_account' must be a table", code="account_config_invalid"
        )
    unknown = sorted(set(raw) - {"enabled", "expected_email", "expected_email_sha256"})
    if unknown:
        raise AccountError(
            "unknown [default_account] key(s): " + ", ".join(unknown),
            code="account_config_invalid",
        )
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise AccountError(
            "the default account enabled must be a bool", code="account_config_invalid"
        )
    expected_email, expected_hash = _parse_expectations(raw, "the default account")
    return DefaultAccountConfig(
        enabled=enabled,
        expected_email=expected_email,
        expected_email_sha256=expected_hash,
    )


def validate_account_name(value: Any) -> str:
    """Validate and return one registry account name.

    Public because the account CLI validates an argument before it writes
    anything; it enforces exactly the rule :func:`_parse_account_entry` uses, so
    the command and the config loader cannot drift. Raises
    ``account_name_invalid`` and never touches the filesystem.
    """
    name = _require_string(value, "name")
    if name == DEFAULT_ACCOUNT_NAME:
        raise AccountError(
            "'default' is reserved for the implicit account and cannot be declared",
            code="account_name_invalid",
        )
    if not _NAME_RE.match(name):
        raise AccountError(
            "account name must match [a-z0-9][a-z0-9-]{0,31}",
            code="account_name_invalid",
        )
    return name


def validate_account_home_value(value: Any) -> Path:
    """Validate and expand one account ``home`` value, returning its real path.

    Shares the loader's rule (non-empty string, absolute after ``~`` expansion)
    without checking that the directory exists; the CLI does that separately so
    it can create a fresh home.
    """
    home = _require_string(value, "home")
    expanded = os.path.expanduser(home)
    if not os.path.isabs(expanded):
        raise AccountError(
            "account home must be an absolute path after ~ expansion",
            code="account_home_invalid",
        )
    return Path(expanded)


def validate_account_pool(value: Any) -> str:
    """Validate and return one optional pool label (the loader's rule)."""
    pool = _require_string(value, "pool")
    if not _POOL_RE.match(pool):
        raise AccountError(
            "account pool must match [a-z0-9][a-z0-9-]{0,31}",
            code="account_config_invalid",
        )
    return pool


def validate_max_concurrent_jobs(value: Any) -> int:
    """Validate one optional per-account concurrency cap (>= 1 integer)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise AccountError(
            "account max_concurrent_jobs must be an integer",
            code="account_config_invalid",
        )
    if value < 1:
        raise AccountError(
            "account max_concurrent_jobs must be >= 1",
            code="account_config_invalid",
        )
    return value


def parse_account_entry(entry: Any) -> AccountConfig:
    """Validate one raw ``[[accounts]]`` entry and return its parsed account.

    Public wrapper around the loader's row parser so the account CLI can
    re-validate the exact entry it is about to append, using the same rules and
    stable codes as :func:`parse_accounts`.
    """
    return _parse_account_entry(entry)


def _parse_account_entry(entry: Any) -> AccountConfig:
    if not isinstance(entry, dict):
        raise AccountError("each [[accounts]] entry must be a table", code="account_config_invalid")

    unknown = sorted(set(entry) - _ENTRY_KEYS)
    if unknown:
        raise AccountError(
            "unknown [[accounts]] key(s): " + ", ".join(unknown),
            code="account_config_invalid",
        )

    if "name" not in entry or "home" not in entry:
        raise AccountError(
            "each [[accounts]] entry requires 'name' and 'home'",
            code="account_config_invalid",
        )

    name = validate_account_name(entry["name"])
    home = _require_string(entry["home"], "home")
    validate_account_home_value(home)

    pool = entry.get("pool")
    if pool is not None:
        pool = validate_account_pool(pool)

    enabled = entry.get("enabled", True)
    if not isinstance(enabled, bool):
        raise AccountError(
            f"account '{name}' enabled must be a bool", code="account_config_invalid"
        )

    max_concurrent = entry.get("max_concurrent_jobs")
    if max_concurrent is not None:
        max_concurrent = validate_max_concurrent_jobs(max_concurrent)

    expires_at = entry.get("expires_at")
    if expires_at is not None:
        expires_at = _parse_iso_timestamp(expires_at)

    expected_email, expected_hash = _parse_expectations(entry, f"account '{name}'")

    return AccountConfig(
        name=name,
        home=home,
        pool=pool,
        enabled=enabled,
        max_concurrent_jobs=max_concurrent,
        expires_at=expires_at,
        expected_email=expected_email,
        expected_email_sha256=expected_hash,
    )


def parse_accounts(raw_accounts: Any, max_accounts: int = 5) -> list[AccountConfig]:
    """Validate the raw ``accounts`` config value and return parsed entries.

    Pure and filesystem-free: it checks shape, names, uniqueness, the absolute
    home, pools, flags, timestamps and the email expectation. The total account
    count includes the implicit default, so ``len(entries) + 1`` must not exceed
    ``max_accounts``. Raises :class:`AccountError` with a stable code on any
    violation and never echoes a full expected email.
    """
    if raw_accounts is None:
        return []
    if not isinstance(raw_accounts, list):
        raise AccountError("'accounts' must be an array of tables", code="account_config_invalid")
    if 1 + len(raw_accounts) > max_accounts:
        raise AccountError(
            f"configured {len(raw_accounts)} extra account(s); the total including the "
            f"implicit 'default' account must not exceed max_accounts ({max_accounts})",
            code="account_limit_exceeded",
        )

    parsed: list[AccountConfig] = []
    seen: set[str] = set()
    for entry in raw_accounts:
        account = _parse_account_entry(entry)
        if account.name in seen:
            raise AccountError(
                f"duplicate account name '{account.name}'", code="account_name_duplicate"
            )
        seen.add(account.name)
        parsed.append(account)
    return parsed


def build_registry(config: Any, default_home: Path) -> AccountRegistry:
    """Build the registry from a parsed config and the invoking user's home.

    The implicit default always exists and keeps today's real home and env. An
    optional ``[default_account]`` table may disable it or give it an identity
    expectation (D116); without that table the built account is byte-identical
    to the pre-D116 one. The config may add accounts but can never replace or
    rename the default.
    """
    default_config = getattr(config, "default_account", None)
    if isinstance(default_config, DefaultAccountConfig):
        default = AccountConfig(
            name=DEFAULT_ACCOUNT_NAME,
            home=str(Path(default_home)),
            is_default=True,
            enabled=default_config.enabled,
            expected_email=default_config.expected_email,
            expected_email_sha256=default_config.expected_email_sha256,
        )
    else:
        default = AccountConfig(
            name=DEFAULT_ACCOUNT_NAME,
            home=str(Path(default_home)),
            is_default=True,
        )
    accounts = tuple(getattr(config, "accounts", ()) or ())
    return AccountRegistry(default=default, accounts=accounts)


def validate_account_home(account: AccountConfig) -> None:
    """Check a non-default account home exists, is a directory and is private.

    Called by start and doctor, never by config loading. The default account is
    the invoking user's real home and is intentionally not checked: its mode is
    not the wrapper's to enforce. Raises ``account_home_invalid`` or
    ``account_home_permissions``.
    """
    if account.is_default:
        return
    home = account.expanded_home
    try:
        stat_result = home.stat()
    except OSError as exc:
        raise AccountError(
            f"account '{account.name}' home does not exist or is unreadable",
            code="account_home_invalid",
        ) from exc
    import stat

    if not stat.S_ISDIR(stat_result.st_mode):
        raise AccountError(
            f"account '{account.name}' home is not a directory",
            code="account_home_invalid",
        )
    if stat_result.st_mode & 0o077:
        raise AccountError(
            f"account '{account.name}' home must not be accessible by group or others "
            "(mode 0700)",
            code="account_home_permissions",
        )
