"""Read-only multi-account health check for ``--doctor`` (D113).

Owns the account portion of the doctor: registry validity, each account home and
mode, permission-policy parity, the expected-email comparison and a short
isolated ``/usage`` probe that also proves the account is logged in. It must
never log in or out, never write an account home, never print a full email and
never block on an unauthenticated account longer than its short timeout.
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from agy_mcp.accounts.env import ISOLATED_DBUS_SESSION_BUS_ADDRESS
from agy_mcp.accounts.errors import AccountError
from agy_mcp.accounts.identity import mask_email
from agy_mcp.accounts.policy import POLICY_DRIFT, POLICY_MISSING, check_policy_parity
from agy_mcp.accounts.registry import AccountConfig, build_registry, validate_account_home
from agy_mcp.config import Config, ConfigError, load_config
from agy_mcp.usage import get_usage

DEFAULT_USAGE_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class DoctorFinding:
    """One doctor check for one account (or the registry as a whole)."""

    account: str
    check: str
    ok: bool
    detail: str


def _finding(account: str, check: str, ok: bool, detail: str) -> DoctorFinding:
    return DoctorFinding(account=account, check=check, ok=ok, detail=detail)


def _check_account(
    account: AccountConfig,
    default_home: Path,
    agy_binary: str,
    timeout_seconds: float,
) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    try:
        validate_account_home(account)
    except AccountError as exc:
        findings.append(_finding(account.name, "home", False, exc.code))
    else:
        findings.append(_finding(account.name, "home", True, "exists, mode 0700"))

    policy = check_policy_parity(account.expanded_home, default_home)
    if policy.ok:
        detail = (
            "matches the primary"
            if policy.status != "primary_empty"
            else "primary has no explicit policy"
        )
        findings.append(_finding(account.name, "policy", True, detail))
    else:
        status = POLICY_DRIFT if policy.status == POLICY_DRIFT else POLICY_MISSING
        findings.append(
            _finding(
                account.name,
                "policy",
                False,
                f"{status} ({policy.account_rule_count} account rule(s))",
            )
        )

    reading = get_usage(
        agy_binary,
        home=account.expanded_home,
        timeout_seconds=timeout_seconds,
        use_cache=False,
        env_overrides={"DBUS_SESSION_BUS_ADDRESS": ISOLATED_DBUS_SESSION_BUS_ADDRESS},
        expected_email=account.expected_email,
        expected_email_sha256=account.expected_email_sha256,
        account_name=account.name,
    )
    account_info = reading.get("account") if isinstance(reading, dict) else None
    masked = account_info.get("masked_email") if isinstance(account_info, dict) else None
    masked_text = masked if isinstance(masked, str) else mask_email("")
    if reading.get("available") is True:
        findings.append(
            _finding(
                account.name,
                "isolation_and_login",
                True,
                f"isolated /usage ok as {masked_text}",
            )
        )
        identity = (
            str(account_info.get("identity", "unchecked"))
            if isinstance(account_info, dict)
            else "unchecked"
        )
        findings.append(
            _finding(
                account.name,
                "email",
                identity not in ("account_identity_mismatch", "account_identity_unknown"),
                f"{masked_text} ({identity})",
            )
        )
    elif str(reading.get("reason")) == "account_identity_mismatch":
        findings.append(
            _finding(account.name, "isolation_and_login", True, f"logged in as {masked_text}")
        )
        findings.append(
            _finding(account.name, "email", False, f"{masked_text} does not match the expectation")
        )
    else:
        findings.append(
            _finding(
                account.name,
                "isolation_and_login",
                False,
                str(reading.get("reason") or "unavailable"),
            )
        )
    return findings


def doctor_report(
    config: Config,
    default_home: Path,
    agy_binary: str,
    timeout_seconds: float = DEFAULT_USAGE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Return ``{"ok": bool, "findings": [...]}`` for the account registry.

    With no ``[[accounts]]`` the report is a single healthy "single-account"
    finding, so a pre-multi-account install's doctor output stays healthy. Every
    configured account is checked with read-only operations only.
    """
    registry = build_registry(config, default_home)
    if not registry.configured:
        return {
            "ok": True,
            "findings": [
                _finding(
                    "-",
                    "registry",
                    True,
                    "no [[accounts]] configured (single-account install)",
                )
            ],
        }
    findings: list[DoctorFinding] = [
        _finding("-", "registry", True, f"{len(registry.accounts)} extra account(s)")
    ]
    for account in registry.accounts:
        findings.extend(
            _check_account(account, default_home, agy_binary, timeout_seconds)
        )
    return {"ok": all(finding.ok for finding in findings), "findings": findings}


def render_report(report: dict[str, Any]) -> str:
    """Render findings as one safe line each (masked emails only)."""
    lines: list[str] = []
    for finding in report.get("findings", []):
        if isinstance(finding, DoctorFinding):
            account, check, ok, detail = (
                finding.account,
                finding.check,
                finding.ok,
                finding.detail,
            )
        elif isinstance(finding, dict):
            account = finding.get("account")
            check = finding.get("check")
            ok = bool(finding.get("ok"))
            detail = finding.get("detail")
        else:
            continue
        marker = "ok" if ok else "problem"
        lines.append(f"account {account}: {check}: {marker}: {detail}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point for the installer's ``--doctor`` account checks."""
    parser = argparse.ArgumentParser(
        prog="agy-mcp-accounts-doctor",
        description="Read-only multi-account checks for agy-mcp --doctor",
    )
    parser.add_argument("--config", default=None, help="Path to config.toml")
    parser.add_argument("--agy-binary", default="agy", help="Path to the agy executable")
    parser.add_argument("--home", default=None, help="The invoking user's real HOME")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_USAGE_TIMEOUT_SECONDS,
        help="Per-account /usage probe timeout in seconds",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(path=Path(args.config) if args.config else None)
    except ConfigError as exc:
        sys.stderr.write(f"account doctor: config invalid: {exc}\n")
        return 1

    default_home = Path(args.home) if args.home else Path.home()
    report = doctor_report(
        config,
        default_home,
        args.agy_binary,
        timeout_seconds=args.timeout,
    )
    sys.stderr.write(render_report(report) + "\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
