"""Multi-account registry, identity, environment and (later) scheduling.

Owns the account registry parsed from ``config.toml``, the single email-masking
helper, per-account child environments and the typed account views used by later
phases. It must never read or print a token, keyring entry, cookie or credential
file, never write into an account home, and never copy the parent environment.
"""

from agy_mcp.accounts.errors import AccountError
from agy_mcp.accounts.registry import (
    DEFAULT_ACCOUNT_NAME,
    AccountConfig,
    AccountRegistry,
    DefaultAccountConfig,
    build_registry,
    parse_accounts,
    parse_default_account,
)

__all__ = [
    "AccountConfig",
    "AccountError",
    "AccountRegistry",
    "DEFAULT_ACCOUNT_NAME",
    "DefaultAccountConfig",
    "build_registry",
    "parse_accounts",
    "parse_default_account",
]
