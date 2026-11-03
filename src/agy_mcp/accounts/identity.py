"""Email masking and account identity verification.

Owns the one ``mask_email`` helper used wherever an email is shown, and the
comparison of the full address parsed from agy's ``applyAuthResult`` line against
the registry's expectation. It must never store, persist, log or return an
unmasked address: the full value is a local argument, and every error message
carries only the masked form.
"""

import hashlib
from dataclasses import dataclass
from typing import Optional

from agy_mcp.accounts.errors import AccountError


def mask_email(email: str, head: int = 3, tail: int = 2) -> str:
    """Mask an email so it stays recognisable without exposing most of it.

    Shows at most ``head`` leading and ``tail`` trailing local-part characters,
    but never more than half of the local part (rounded down). Local parts of one
    or two characters are masked completely, and a value without a usable
    local/domain split becomes ``***``. The domain stays visible because it does
    not identify the account on its own. Never raises: this helper is on logging
    paths where a malformed value must not break the caller.
    """
    if not isinstance(email, str) or "@" not in email:
        return "***"
    local, _, domain = email.rpartition("@")
    if not local or not domain:
        return "***"
    if len(local) <= 2:
        return f"***@{domain}"

    max_visible = len(local) // 2
    visible_head = min(max(0, head), max_visible)
    visible_tail = min(max(0, tail), max_visible - visible_head)
    shown = local[:visible_head]
    if visible_tail:
        shown += local[-visible_tail:]
    return f"{shown}***@{domain}"


def email_sha256(email: str) -> str:
    """Return the lowercase SHA-256 hex of a normalized full email.

    Normalization is ``strip`` + ``casefold`` so a registry hash and the value
    parsed from a log agree on case, which Google accounts do not distinguish.
    The full value never leaves this function except as the digest.
    """
    normalized = email.strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IdentityCheck:
    """Outcome of verifying the observed account email against the registry.

    ``verified`` is True when an expectation is configured and matched; when no
    expectation exists the check is ``verified=False`` with ``reason``
    ``no_expectation``, which callers treat as "nothing to verify" rather than a
    failure.
    """

    verified: bool
    masked_email: Optional[str]
    reason: str = "ok"


def verify_identity(
    observed_email: Optional[str],
    *,
    expected_email: Optional[str] = None,
    expected_email_sha256: Optional[str] = None,
) -> IdentityCheck:
    """Compare the full observed email with the registry expectation.

    Exactly one expectation may be configured. A configured expectation that is
    missing from agy's log raises ``account_identity_unknown``; a mismatch raises
    ``account_identity_mismatch``. Both errors carry only the masked observed
    value. No expectation is not a failure: the account simply is not verified.
    """
    if expected_email is not None and expected_email_sha256 is not None:
        raise AccountError(
            "configure at most one of expected_email and expected_email_sha256",
            code="account_config_invalid",
        )

    if expected_email is None and expected_email_sha256 is None:
        return IdentityCheck(
            verified=False,
            masked_email=mask_email(observed_email) if observed_email else None,
            reason="no_expectation",
        )

    if not observed_email:
        raise AccountError(
            "agy did not report the account email; cannot verify the login",
            code="account_identity_unknown",
        )

    masked = mask_email(observed_email)
    if expected_email is not None:
        if observed_email.strip().casefold() != expected_email.strip().casefold():
            raise AccountError(
                f"observed account {masked} does not match the expected account",
                code="account_identity_mismatch",
            )
    elif expected_email_sha256 is not None:
        if email_sha256(observed_email) != expected_email_sha256:
            raise AccountError(
                f"observed account {masked} does not match the expected email hash",
                code="account_identity_mismatch",
            )
    else:
        # Guarded by the early return above; keeping the branch explicit avoids
        # an assert that disappears under `python -O`.
        raise AccountError(
            "no account email expectation is configured",
            code="account_config_invalid",
        )

    return IdentityCheck(verified=True, masked_email=masked, reason="matched")
