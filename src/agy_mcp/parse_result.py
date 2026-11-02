"""Classify an agy termination into a job state (SPEC section 15 precedence).

Owns the ordered precedence rules and the tolerant last-JSON extraction used on
stdout. It must never execute anything, and it must never let a missing or
malformed payload mask a cancellation, audit violation or timeout that an
earlier-precedence input already established.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from agy_mcp.cli_log import (
    Denial,
    extract_denials_from_cli_log,
    format_denied_commands,
    read_cli_log_tail,
)
from agy_mcp.models import TokenUsage
from agy_mcp.states import JobState
from agy_mcp.util import strict_uuid, truncate_head

__all__ = [
    "Denial",
    "ParsedResult",
    "StdoutPayload",
    "extract_denials_from_cli_log",
    "extract_stdout_payload",
    "format_denied_commands",
    "parse_agy_output",
    "read_cli_log_tail",
]


@dataclass
class StdoutPayload:
    """The last JSON payload on stdout, extracted exactly once by a caller."""

    data: dict[str, Any]
    conversation_id: Optional[str]
    tokens: TokenUsage
    agy_duration_seconds: Optional[float]


@dataclass
class ParsedResult:
    """Result extracted from agy CLI stdout and stderr."""

    state: JobState
    review_reason: Optional[str] = None
    decision_prompt: Optional[str] = None
    response_text: str = ""
    conversation_id: Optional[str] = None
    tokens: TokenUsage = field(default_factory=TokenUsage)
    denials: list[dict[str, Any]] = field(default_factory=list)
    denied_commands: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error_message: Optional[str] = None
    failure_reason: Optional[str] = None
    signal: Optional[int] = None
    parse_error: bool = False
    raw_tail: str = ""
    # agy's JSON ``duration_seconds`` is the conversation's AGE, not this run's
    # runtime. It is recorded for diagnostics only; callers must use their own
    # wall-clock measurement for budgets, usage rows and logging.
    agy_duration_seconds: Optional[float] = None


def _extract_tokens(data: dict[str, Any]) -> TokenUsage:
    """Extract token counts from 'tokens' or 'usage' plus top-level 'num_turns'.

    Real agy puts ``thinking_tokens``/``cache_read_tokens`` inside ``usage`` and
    ``num_turns`` beside it (masked smoke evidence, finding 5).
    """
    t = data.get("tokens") or data.get("usage")
    usage = TokenUsage.from_dict(t) if isinstance(t, dict) else TokenUsage()
    num_turns = data.get("num_turns")
    if num_turns is not None:
        try:
            usage.num_turns = int(num_turns)
        except (TypeError, ValueError):
            pass
    return usage


def _extract_conversation_id(data: Optional[dict[str, Any]]) -> Optional[str]:
    """Return the stdout JSON conversation id only when it is a full UUID."""
    if not isinstance(data, dict):
        return None
    return strict_uuid(data.get("conversation_id"))


def _extract_agy_duration(data: Optional[dict[str, Any]]) -> Optional[float]:
    """Return agy's conversation-age duration when it is a non-negative number."""
    if not isinstance(data, dict):
        return None
    value = data.get("duration_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    duration = float(value)
    return duration if duration >= 0.0 else None


def _extract_last_json_object(stdout_content: str) -> Optional[dict[str, Any]]:
    """Scan stdout from the end to find the last valid JSON object line or block."""
    if not stdout_content or not stdout_content.strip():
        return None

    # Try line-by-line backwards scan
    # agy can emit human/log lines after its JSON object, so prefer the last
    # object-shaped line instead of assuming stdout is a single clean document.
    lines = [line.strip() for line in stdout_content.splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith("{") and line.endswith("}"):
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                continue

    # Fallback: attempt to parse entire stripped content
    try:
        parsed = json.loads(stdout_content.strip())
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    return None


def extract_stdout_payload(stdout_content: str) -> Optional[StdoutPayload]:
    """Extract the last stdout JSON object and its id/tokens/duration once.

    Callers that need several of these values (conversation id, tokens, agy's
    diagnostic duration) use this to avoid scanning/parsing the same payload
    more than once.
    """
    data = _extract_last_json_object(stdout_content)
    if data is None:
        return None
    return StdoutPayload(
        data=data,
        conversation_id=_extract_conversation_id(data),
        tokens=_extract_tokens(data),
        agy_duration_seconds=_extract_agy_duration(data),
    )


def parse_agy_output(
    stdout_content: str,
    stderr_content: str,
    return_code: int,
    is_cancelled: bool = False,
    is_audit_violated: bool = False,
    supervisor_timed_out: bool = False,
    commits_count: int = 0,
    files_modified_count: int = 0,
    has_cli_denials: bool = False,
) -> ParsedResult:
    """Parse agy execution output following strict precedence rules in SPEC section 15.

    Precedence order:
    1. Cancellation: is_cancelled -> state="cancelled"
    2. Audit Violation: is_audit_violated -> state="needs_review(reason=audit_violation)"
    3. Negative Signal: return_code < 0 -> state="failed" with signal=abs(rc)
    4. CLI Flag/Invocation Error: return_code in (1, 2) -> state="failed" with stderr tail
    5. Timeout: supervisor_timed_out or stderr contains print timeout -> state="timeout"
    6. Decision Marker: last non-empty line matches ^NEEDS_DECISION: -> state="needs_decision"
    7. Permission Blockage: tool denial with zero commits and empty response ->
       state="needs_review(reason=blocked_by_permission)"
    7b. Denied Without Progress (D49): exit 0 SUCCESS with zero commits, zero modified files,
       and at least one denial -> state="needs_review(reason=denied_without_progress)"
    8. Success: status == "SUCCESS" -> state="done"
       (zero-commit-no-denial adds warning "no_commits")
    9. Unparseable / Other: unhandled status or invalid JSON -> state="failed" with parse_error=True
    """
    # 1. Cancellation
    # Checks 1-5 intentionally run before any stdout parsing: a wrapper flag or
    # supervisor decision outranks whatever agy printed.
    if is_cancelled:
        # Cancellation outranks the payload, but a payload that already arrived
        # still carries the conversation id and usage tokens. Dropping them lost
        # the id on every cancelled job (resume then failed with
        # invalid_state_for_resume).
        cancelled_json = _extract_last_json_object(stdout_content)
        return ParsedResult(
            state=JobState.CANCELLED,
            conversation_id=_extract_conversation_id(cancelled_json),
            tokens=_extract_tokens(cancelled_json) if cancelled_json else TokenUsage(),
            agy_duration_seconds=_extract_agy_duration(cancelled_json),
            error_message="Job cancelled by wrapper",
        )

    # 2. Audit Violation
    if is_audit_violated:
        return ParsedResult(
            state=JobState.NEEDS_REVIEW,
            review_reason="audit_violation",
            error_message="Security audit detected unauthorized modification",
        )

    # 3. Timeout via Supervisor wall-clock expiration
    if supervisor_timed_out:
        parsed_json = _extract_last_json_object(stdout_content)
        return ParsedResult(
            state=JobState.TIMEOUT,
            conversation_id=_extract_conversation_id(parsed_json),
            tokens=_extract_tokens(parsed_json) if parsed_json else TokenUsage(),
            agy_duration_seconds=_extract_agy_duration(parsed_json),
            error_message="Job execution timed out",
        )

    # 4. Negative Signal
    if return_code < 0:
        sig = abs(return_code)
        return ParsedResult(
            state=JobState.FAILED,
            signal=sig,
            failure_reason="signal_exit",
            error_message=f"Process terminated by signal {sig}",
        )

    # 4. CLI Flag / Invocation Error (return code 1 or 2)
    # Keep the head, not the tail: CLI/flag errors appear at the start of stderr.
    if return_code in (1, 2):
        err_tail = truncate_head(stderr_content, 4096)
        return ParsedResult(
            state=JobState.FAILED,
            failure_reason="cli_error",
            raw_tail=err_tail,
            error_message=err_tail or f"Process exited with error code {return_code}",
        )

    # 5. Timeout
    if supervisor_timed_out or "[agy] print timeout after" in stderr_content:
        parsed_json = _extract_last_json_object(stdout_content)
        return ParsedResult(
            state=JobState.TIMEOUT,
            conversation_id=_extract_conversation_id(parsed_json),
            tokens=_extract_tokens(parsed_json) if parsed_json else TokenUsage(),
            agy_duration_seconds=_extract_agy_duration(parsed_json),
            error_message="Job execution timed out",
        )

    # Parse stdout JSON for subsequent steps
    data = _extract_last_json_object(stdout_content)
    if data is None:
        raw_tail = truncate_head(stdout_content or stderr_content, 4096)
        return ParsedResult(
            state=JobState.FAILED,
            failure_reason="json_parse_error",
            parse_error=True,
            raw_tail=raw_tail,
            error_message="Failed to parse valid JSON payload from agy stdout",
        )

    status = data.get("status")
    response_text = str(data.get("response", "") or "")
    conversation_id = _extract_conversation_id(data)
    tokens = _extract_tokens(data)
    agy_duration_seconds = _extract_agy_duration(data)
    denials = data.get("denied_actions", []) if isinstance(data.get("denied_actions"), list) else []
    warnings = data.get("warnings", []) if isinstance(data.get("warnings"), list) else []

    # 6. Decision Marker
    lines = [ln.strip() for ln in response_text.splitlines() if ln.strip()]
    if lines:
        last_line = lines[-1]
        decision_match = re.match(r"^NEEDS_DECISION:\s*(.*)$", last_line)
        if decision_match:
            return ParsedResult(
                state=JobState.NEEDS_DECISION,
                decision_prompt=decision_match.group(1).strip(),
                response_text=response_text,
                conversation_id=conversation_id,
                tokens=tokens,
                agy_duration_seconds=agy_duration_seconds,
                denials=denials,
                warnings=warnings,
            )

    # 7. Permission Blockage
    if len(denials) > 0 and commits_count == 0 and not response_text.strip():
        return ParsedResult(
            state=JobState.NEEDS_REVIEW,
            review_reason="blocked_by_permission",
            response_text=response_text,
            conversation_id=conversation_id,
            tokens=tokens,
            agy_duration_seconds=agy_duration_seconds,
            denials=denials,
            warnings=warnings,
            error_message="Tool permission denied and no progress was made",
        )

    # 7b. Denied Without Progress (D49)
    has_denials = (len(denials) > 0) or has_cli_denials
    if status == "SUCCESS" and commits_count == 0 and files_modified_count == 0 and has_denials:
        return ParsedResult(
            state=JobState.NEEDS_REVIEW,
            review_reason="denied_without_progress",
            response_text=response_text,
            conversation_id=conversation_id,
            tokens=tokens,
            agy_duration_seconds=agy_duration_seconds,
            denials=denials,
            warnings=warnings,
            error_message="Tool permission denied and no progress was made",
        )

    # 8. Success
    if status == "SUCCESS":
        effective_warnings = list(warnings)
        if commits_count == 0 and not has_denials:
            if "no_commits" not in effective_warnings:
                effective_warnings.append("no_commits")
        elif has_denials:
            if "denied_actions" not in effective_warnings:
                effective_warnings.append("denied_actions")

        return ParsedResult(
            state=JobState.DONE,
            response_text=response_text,
            conversation_id=conversation_id,
            tokens=tokens,
            agy_duration_seconds=agy_duration_seconds,
            denials=denials,
            warnings=effective_warnings,
        )

    # 9. Unhandled status / other
    raw_tail = truncate_head(stdout_content, 4096)
    return ParsedResult(
        state=JobState.FAILED,
        failure_reason="unhandled_status",
        parse_error=True,
        raw_tail=raw_tail,
        response_text=response_text,
        conversation_id=conversation_id,
        tokens=tokens,
        agy_duration_seconds=agy_duration_seconds,
        error_message=f"Unhandled status: {status!r}",
    )
