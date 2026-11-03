"""Permission-policy parity between an account home and the primary login.

Owns the read-only comparison that makes a non-default account as safe as the
primary: the account's normalized ``permissions.allow`` set must equal the
primary's. It must never write a policy file, never create an account home and
never weaken a policy; a missing or drifted policy is a refusal, not a repair.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

POLICY_SAME = "same"
POLICY_MISSING = "missing"
POLICY_DRIFT = "drift"
POLICY_PRIMARY_EMPTY = "primary_empty"


@dataclass(frozen=True)
class PolicyCheck:
    """Result of comparing one account's effective allow-rules to the primary's."""

    ok: bool
    status: str
    account_rules: tuple[str, ...] = ()
    primary_rules: tuple[str, ...] = ()

    @property
    def account_rule_count(self) -> int:
        """Number of effective allow-rules on the account."""
        return len(self.account_rules)


def _effective_rules(home: Optional[Path]) -> tuple[str, ...]:
    """Return the normalized, sorted allow-rules under one home (read-only)."""
    if home is None:
        return ()
    from agy_mcp.worktree import inspect_permission_rules

    info = inspect_permission_rules(home)
    rules = info.get("permission_rules_effective", [])
    return tuple(sorted({rule.strip() for rule in rules if isinstance(rule, str) and rule.strip()}))


def check_policy_parity(account_home: Path, primary_home: Path) -> PolicyCheck:
    """Compare an account's effective allow-rules with the primary's.

    ``primary_empty`` means the primary has no explicit allow-rules, so there is
    no policy to mirror; the account is not refused for that reason alone. A
    non-empty primary with an empty account is ``missing``; any set difference is
    ``drift``. The comparison is on stripped rules, order-independent.
    """
    primary = _effective_rules(primary_home)
    account = _effective_rules(account_home)
    if not primary:
        return PolicyCheck(
            ok=True,
            status=POLICY_PRIMARY_EMPTY,
            account_rules=account,
            primary_rules=primary,
        )
    if not account:
        return PolicyCheck(
            ok=False,
            status=POLICY_MISSING,
            account_rules=account,
            primary_rules=primary,
        )
    if set(account) != set(primary):
        return PolicyCheck(
            ok=False,
            status=POLICY_DRIFT,
            account_rules=account,
            primary_rules=primary,
        )
    return PolicyCheck(
        ok=True,
        status=POLICY_SAME,
        account_rules=account,
        primary_rules=primary,
    )
