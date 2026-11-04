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

from agy_mcp.accounts.errors import AccountError
from agy_mcp.accounts.identity import mask_email
from agy_mcp.accounts.policy import POLICY_DRIFT, POLICY_MISSING, check_policy_parity
from agy_mcp.accounts.registry import (
    AccountConfig,
    AccountRegistry,
    build_registry,
    validate_account_home,
)
from agy_mcp.config import Config, ConfigError, load_config
from agy_mcp.usage import get_usage

DEFAULT_USAGE_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class DoctorFinding:
    """One doctor check for one account (or the registry as a whole).

    ``warn`` marks a finding that must be visible but must not fail the doctor
    (the shared-keyring and duplicate-identity advisories, D116); ``ok`` stays
    the only input to the report's overall exit status.
    """

    account: str
    check: str
    ok: bool
    detail: str
    warn: bool = False


def _finding(account: str, check: str, ok: bool, detail: str, warn: bool = False) -> DoctorFinding:
    return DoctorFinding(account=account, check=check, ok=ok, detail=detail, warn=warn)


def _check_account(
    account: AccountConfig,
    default_home: Path,
    agy_binary: str,
    timeout_seconds: float,
) -> tuple[list[DoctorFinding], Optional[str], Optional[str]]:
    """Check one account and return its findings, masked email and fingerprint."""
    findings: list[DoctorFinding] = []
    if account.is_default:
        # The default's home is the invoking user's real home; its mode is not
        # the wrapper's to enforce, so this is reported separately from the
        # per-account "home" check (D116).
        findings.append(
            _finding(account.name, "default_home", True, "the invoking user's real home")
        )
    else:
        try:
            validate_account_home(account)
        except AccountError as exc:
            findings.append(_finding(account.name, "home", False, exc.code))
        else:
            findings.append(_finding(account.name, "home", True, "exists, mode 0700"))

    if account.is_default:
        # The default account *is* the primary: parity with itself is vacuous.
        findings.append(_finding(account.name, "policy", True, "primary login"))
    else:
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

    # One env builder for every account spawn: the account HOME plus the dead
    # D-Bus address (D104). Doctor findings previously passed the address as an
    # ad-hoc override; this keeps doctor and the runtime probes identical.
    reading = get_usage(
        agy_binary,
        home=account.expanded_home,
        timeout_seconds=timeout_seconds,
        use_cache=False,
        expected_email=account.expected_email,
        expected_email_sha256=account.expected_email_sha256,
        account_name=account.name,
        account=account,
    )
    account_info = reading.get("account") if isinstance(reading, dict) else None
    masked = account_info.get("masked_email") if isinstance(account_info, dict) else None
    fingerprint = (
        account_info.get("identity_sha256") if isinstance(account_info, dict) else None
    )
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
        masked = None
        fingerprint = None
    return (
        findings,
        masked if isinstance(masked, str) else None,
        fingerprint if isinstance(fingerprint, str) else None,
    )


def doctor_report(
    config: Config,
    default_home: Path,
    agy_binary: str,
    timeout_seconds: float = DEFAULT_USAGE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Return ``{"ok": bool, "findings": [...]}`` for the account registry.

    With no ``[[accounts]]`` and no ``[default_account]`` the report is a single
    healthy "single-account" finding, so a pre-multi-account install's doctor
    output stays healthy. Once accounts exist the implicit default is checked
    too, because it shares the desktop keyring and can silently collide with
    another login (D116). Advisories (``warn=True``) never flip ``ok``.
    """
    registry = build_registry(config, default_home)
    explicit_default = getattr(config, "default_account", None) is not None
    if not registry.configured and not explicit_default:
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
    enabled = [account for account in registry.all() if account.enabled]
    if not enabled:
        findings.append(
            _finding(
                "-",
                "registry",
                False,
                "account_none_enabled: no configured account is enabled",
            )
        )
    masked_by_name: dict[str, Optional[str]] = {}
    fingerprint_by_name: dict[str, Optional[str]] = {}
    # Probe the implicit default whenever it is enabled: with a registry it
    # competes with the declared accounts, and an explicit table can add an
    # identity expectation to the single-account path.
    if registry.default.enabled:
        default_findings, default_masked, default_fingerprint = _check_account(
            registry.default, default_home, agy_binary, timeout_seconds
        )
        findings.extend(default_findings)
        masked_by_name[registry.default.name] = default_masked
        fingerprint_by_name[registry.default.name] = default_fingerprint
    for account in registry.accounts:
        account_findings, account_masked, account_fingerprint = _check_account(
            account, default_home, agy_binary, timeout_seconds
        )
        findings.extend(account_findings)
        masked_by_name[account.name] = account_masked
        fingerprint_by_name[account.name] = account_fingerprint

    if registry.configured and registry.default.enabled:
        if (
            registry.default.expected_email is None
            and registry.default.expected_email_sha256 is None
        ):
            findings.append(
                _finding(
                    registry.default.name,
                    "shared_keyring",
                    True,
                    "warning: shared keyring identity can change when the desktop app "
                    "switches accounts; disable default or set an expectation",
                    warn=True,
                )
            )
    findings.extend(
        _duplicate_identity_findings(registry, masked_by_name, fingerprint_by_name)
    )
    return {"ok": all(finding.ok for finding in findings), "findings": findings}


def _duplicate_identity_findings(
    registry: AccountRegistry,
    masked_by_name: dict[str, Optional[str]],
    fingerprint_by_name: dict[str, Optional[str]],
) -> list[DoctorFinding]:
    """Report enabled accounts whose probes show the same login (D116).

    This is the 2026-11-05 collision: the default account and a declared account
    authenticated as the same Google login, so two names described one quota
    pool. The digest groups names even when their mask shapes differ (one
    verified, one not); it is advisory, not a failure, because the scheduler
    already merges a verified group and the operator decides whether to disable
    one.
    """
    by_identity: dict[str, list[str]] = {}
    masked_by_identity: dict[str, str] = {}
    for account in registry.all():
        if not account.enabled:
            continue
        fingerprint = fingerprint_by_name.get(account.name)
        masked = masked_by_name.get(account.name)
        identity_key = fingerprint or masked
        if not identity_key:
            continue
        by_identity.setdefault(identity_key, []).append(account.name)
        if masked:
            masked_by_identity.setdefault(identity_key, masked)
    findings: list[DoctorFinding] = []
    for identity_key, names in sorted(by_identity.items()):
        if len(names) < 2:
            continue
        masked = masked_by_identity.get(identity_key, "***")
        findings.append(
            _finding(
                ", ".join(names),
                "shared_identity",
                True,
                f"warning: these accounts report the same login ({masked}); "
                "the scheduler treats a verified duplicate as one quota pool",
                warn=True,
            )
        )
    return findings


def render_report(report: dict[str, Any]) -> str:
    """Render findings as one safe line each (masked emails only)."""
    lines: list[str] = []
    for finding in report.get("findings", []):
        warn = False
        if isinstance(finding, DoctorFinding):
            account, check, ok, detail, warn = (
                finding.account,
                finding.check,
                finding.ok,
                finding.detail,
                finding.warn,
            )
        elif isinstance(finding, dict):
            account = finding.get("account")
            check = finding.get("check")
            ok = bool(finding.get("ok"))
            detail = finding.get("detail")
            warn = bool(finding.get("warn", False))
        else:
            continue
        marker = "warn" if warn and ok else ("ok" if ok else "problem")
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
