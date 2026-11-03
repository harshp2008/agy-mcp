"""Data records, envelopes, caps and serialization (SPEC sections 8, 10, 16).

Owns the public response shapes, byte budgets, untrusted-text delimiting,
``next_actions`` text and the persisted ``job.json`` schema. It must never
perform I/O, start processes, or decide policy: callers pass fully-formed state
in, and this module only caps, wraps and serializes it.
"""

import re
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from agy_mcp.states import JobState
from agy_mcp.util import sanitize_absolute_paths, truncate_tail

# Byte caps from SPEC section 8. They are module constants (not config) because
# exceeding them would break the MCP client contract, not tune a deployment.
MAX_RESPONSE_BYTES = 64 * 1024  # 64 KB
MAX_VERIFY_TAIL_BYTES = 8 * 1024  # 8 KB
MAX_DIFF_STAT_BYTES = 8 * 1024  # 8 KB
MAX_UNTRUSTED_DIFF_BYTES = 32 * 1024  # 32 KB
MAX_COMMIT_MSG_BYTES = 1024  # 1 KB
MAX_COMMITS_COUNT = 50
CURRENT_SCHEMA_VERSION = 1


def wrap_untrusted_text(text: str, nonce: Optional[str] = None) -> str:
    """Delimit untrusted repository- or agent-derived text per SPEC section 5 & F27.

    Guarantees that malicious payloads containing closing tag variations (e.g.
    `</untrusted_text>`, case variants, whitespace/split attempts) cannot escape or
    prematurely close the wrapper block.

    Uses a per-call random delimiter id recorded in the tag and escapes any
    embedded tag opening sequences in the payload.
    """
    # A per-call nonce makes it impossible for payload text to forge a matching
    # closing tag; the regex still escapes any nested opening sequence.
    tag_id = nonce or secrets.token_hex(8)
    # Neutralize any opening or closing tag attempts and case variants in payload
    sanitized = re.sub(
        r"(?i)<\s*(/?\s*untrusted(?:_text)?)",
        r"&lt;\1",
        text,
    )
    return f'<untrusted_text id="{tag_id}">\n{sanitized}\n</untrusted_text id="{tag_id}">'


def unwrap_untrusted_text(wrapped: str) -> str:
    """Extract untrusted text from a wrapped envelope, reversing tag escaping."""
    match = re.match(
        r'^<untrusted_text id="([a-f0-9]+)">\n([\s\S]*?)\n</untrusted_text id="\1">$',
        wrapped.strip(),
    )
    if match:
        payload = match.group(2)
        # Restore escaped tags
        return re.sub(r"(?i)&lt;\s*(/?\s*untrusted(?:_text)?)", r"<\1", payload)
    return wrapped


def _optional_int(value: Any) -> Optional[int]:
    """Coerce an optional JSON number to int, keeping absence as None."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass
class TokenUsage:
    """Token consumption accounting.

    The real CLI reports ``thinking_tokens`` and ``cache_read_tokens`` inside
    ``usage`` and ``num_turns`` beside it (masked smoke evidence). Those three
    stay None when a payload does not carry them so old envelopes keep their
    three original counters.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    thinking_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    num_turns: Optional[int] = None

    def to_dict(self) -> dict[str, Optional[int]]:
        """Serialize token counters, keeping absent optional counters as null."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TokenUsage":
        """Build counters from a JSON object, defaulting missing required fields to 0."""
        return cls(
            input_tokens=int(data.get("input_tokens", 0)),
            output_tokens=int(data.get("output_tokens", 0)),
            total_tokens=int(data.get("total_tokens", 0)),
            thinking_tokens=_optional_int(data.get("thinking_tokens")),
            cache_read_tokens=_optional_int(data.get("cache_read_tokens")),
            num_turns=_optional_int(data.get("num_turns")),
        )


@dataclass
class AuditEvent:
    """Security audit event written to audit.jsonl per SPEC section 17."""

    ts: str
    event: str
    detail: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Serialize the event to the audit.jsonl line shape."""
        return {
            "ts": self.ts,
            "event": self.event,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditEvent":
        """Reconstruct an event from one parsed audit.jsonl line."""
        return cls(
            ts=str(data["ts"]),
            event=str(data["event"]),
            detail=dict(data.get("detail", {})),
        )


@dataclass
class StandardEnvelope:
    """Standardized response envelope returned by all tools per SPEC section 8."""

    job_id: str
    state: JobState
    elapsed_seconds: float
    title: str
    branch: str
    base_commit: str
    head_commit: str
    commits_count: int
    model_resolved: Optional[str] = None
    files_modified: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    denials: list[dict[str, Any]] = field(default_factory=list)
    denied_commands: list[dict[str, Any]] = field(default_factory=list)
    denied_tools: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tokens: TokenUsage = field(default_factory=TokenUsage)
    next_actions: list[str] = field(default_factory=list)
    error: Optional[Any] = None
    usage_cost: Optional[dict[str, Any]] = None
    quota_estimate: Optional[dict[str, Any]] = None
    # Resume-only additive field: True when a valid conversation id was passed
    # to agy, False when the wrapper resumed with a self-contained prompt.
    conversation_continued: Optional[bool] = None
    resume_after: Optional[str] = None
    plan: dict[str, Any] = field(
        default_factory=lambda: {"total": 0, "done": 0, "remaining": []}
    )
    facts: dict[str, Any] = field(default_factory=dict)
    agent_report: Optional[str] = None
    scratchpad: list[str] = field(default_factory=list)
    # Multi-account fields appear only when a registry selected an account (D103/D110).
    account: Optional[str] = None
    account_email: Optional[str] = None
    transferred_from: Optional[str] = None
    transferred_to: Optional[str] = None
    verify: dict[str, Any] = field(
        default_factory=lambda: {
            "requested": False,
            "ran": False,
            "passed": None,
            "rc": None,
            "duration_seconds": None,
            "attempts": 0,
            "last_tail": None,
            "status": None,
            "timed_out": False,
            "killed_by_timeout": False,
            "command": None,
            "timeout_seconds": None,
            "cause": None,
            "next_action": None,
        }
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the base envelope, converting the state enum to its value."""
        data: dict[str, Any] = {
            "job_id": self.job_id,
            "state": self.state.value if isinstance(self.state, JobState) else self.state,
            "elapsed_seconds": self.elapsed_seconds,
            "title": self.title,
            "model_resolved": self.model_resolved,
            "branch": self.branch,
            "base_commit": self.base_commit,
            "head_commit": self.head_commit,
            "commits_count": self.commits_count,
            "files_modified": self.files_modified,
            "skipped_files": self.skipped_files,
            "denials": self.denials,
            "denied_commands": self.denied_commands,
            "denied_tools": self.denied_tools,
            "warnings": self.warnings,
            "tokens": self.tokens.to_dict(),
            "next_actions": self.next_actions,
            "error": self.error,
            "usage_cost": self.usage_cost,
            "quota_estimate": self.quota_estimate,
            "conversation_continued": self.conversation_continued,
            "resume_after": self.resume_after,
            "plan": self.plan,
            "facts": self.facts,
            "agent_report": self.agent_report,
            "scratchpad": self.scratchpad,
            "verify": self.verify,
        }
        if self.account is not None:
            data["account"] = self.account
            data["account_email"] = self.account_email
        if self.transferred_from is not None:
            data["transferred_from"] = self.transferred_from
        if self.transferred_to is not None:
            data["transferred_to"] = self.transferred_to
        return data


@dataclass
class Job:
    """Represents the in-memory and persisted job record per SPEC sections 8, 10, 16."""

    job_id: str
    title: str
    repo_path: Path
    branch: str
    base_commit: str
    head_commit: str = ""
    state: JobState = JobState.QUEUED
    model: str = "gemini-3.8-flash-medium"
    model_resolved: Optional[str] = None
    # Multi-account pinning (D110). None means the implicit default account, so
    # a single-account job record stays byte-identical.
    account: Optional[str] = None
    priority: str = "normal"
    account_masked_email: Optional[str] = None
    quota_reservation: Optional[dict[str, Any]] = None
    transferred_from: Optional[str] = None
    transferred_to: Optional[str] = None
    max_minutes: int = 240
    verify: Optional[str] = None
    verify_timeout_seconds: int = 900
    conversation_id: Optional[str] = None
    pid: Optional[int] = None
    pgid: Optional[int] = None
    start_time: Optional[int] = None
    worktree_path: Optional[Path] = None
    commit_msg_generation: int = 0
    review_reason: Optional[str] = None
    source_dirty: bool = False
    recovered: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    cumulative_minutes: float = 0.0
    commits_count: int = 0
    files_modified: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    denials: list[dict[str, Any]] = field(default_factory=list)
    denied_commands: list[dict[str, Any]] = field(default_factory=list)
    denied_tools: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tokens: TokenUsage = field(default_factory=TokenUsage)
    schema_version: int = CURRENT_SCHEMA_VERSION
    verify_status: Optional[dict[str, Any]] = None
    auto_fix_count: int = 0
    denial_resume_count: int = 0
    error: Optional[dict[str, Any]] = None
    stdout_offset: int = 0
    # Where this generation starts in the reused <jobdir>/cli.log branch. agy
    # may append or truncate the log on resume; the anchor (size + prefix hash)
    # lets the supervisor accept conversation lines only from this generation.
    cli_log_offset: int = 0
    cli_log_prefix_sha256: Optional[str] = None
    # Provenance of conversation_id: "early" (read from cli.log while running),
    # "finalized" (agy's own stdout JSON), or absent for pre-upgrade jobs. Only a
    # finalized id is sticky across generations; an early-scan-only id yields to
    # a later stdout UUID.
    conversation_id_source: Optional[str] = None
    # Refined when agy's own output shows whether it resumed the requested UUID.
    conversation_continued: Optional[bool] = None
    run_generation: int = 1
    launch_wall_clock: Optional[float] = None
    usage_at_launch: Optional[dict[str, Any]] = None
    usage_cost: Optional[dict[str, Any]] = None
    quota_estimate: Optional[dict[str, Any]] = None
    # Additive per-run records. agy's JSON tokens and duration_seconds describe
    # one run (its duration is the conversation age), so each resumed run adds
    # an entry rather than overwriting the previous one.
    runs: list[dict[str, Any]] = field(default_factory=list)
    resume_after: Optional[str] = None
    plan_summary: Optional[dict[str, Any]] = None
    plan_nudge_count: int = 0
    agent_report: Optional[str] = None
    audit_status: Optional[str] = None
    audit_reason: Optional[str] = None
    scratchpad_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert job to a JSON-serializable dictionary.

        Multi-account fields are emitted only when they differ from the
        single-account default (D110), so an old job record and a new job on the
        implicit default account serialize identically.
        """
        data: dict[str, Any] = {
            "schema_version": self.schema_version,
            "job_id": self.job_id,
            "title": self.title,
            "repo_path": str(self.repo_path),
            "branch": self.branch,
            "base_commit": self.base_commit,
            "head_commit": self.head_commit,
            "state": self.state.value if isinstance(self.state, JobState) else self.state,
            "model": self.model,
            "model_resolved": self.model_resolved,
            "max_minutes": self.max_minutes,
            "verify": self.verify,
            "verify_timeout_seconds": self.verify_timeout_seconds,
            "conversation_id": self.conversation_id,
            "pid": self.pid,
            "pgid": self.pgid,
            "start_time": self.start_time,
            "worktree_path": str(self.worktree_path) if self.worktree_path else None,
            "commit_msg_generation": self.commit_msg_generation,
            "review_reason": self.review_reason,
            "source_dirty": self.source_dirty,
            "recovered": self.recovered,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "cumulative_minutes": self.cumulative_minutes,
            "commits_count": self.commits_count,
            "files_modified": self.files_modified,
            "skipped_files": self.skipped_files,
            "denials": self.denials,
            "denied_commands": self.denied_commands,
            "denied_tools": self.denied_tools,
            "warnings": self.warnings,
            "tokens": self.tokens.to_dict(),
            "verify_status": self.verify_status,
            "auto_fix_count": self.auto_fix_count,
            "denial_resume_count": self.denial_resume_count,
            "error": self.error,
            "stdout_offset": self.stdout_offset,
            "cli_log_offset": self.cli_log_offset,
            "cli_log_prefix_sha256": self.cli_log_prefix_sha256,
            "conversation_id_source": self.conversation_id_source,
            "conversation_continued": self.conversation_continued,
            "run_generation": self.run_generation,
            "launch_wall_clock": self.launch_wall_clock,
            "usage_at_launch": self.usage_at_launch,
            "usage_cost": self.usage_cost,
            "quota_estimate": self.quota_estimate,
            "runs": self.runs,
            "resume_after": self.resume_after,
            "plan_summary": self.plan_summary,
            "plan_nudge_count": self.plan_nudge_count,
            "agent_report": self.agent_report,
            "audit_status": self.audit_status,
            "audit_reason": self.audit_reason,
            "scratchpad_files": self.scratchpad_files,
        }
        if self.account is not None:
            data["account"] = self.account
            data["account_masked_email"] = self.account_masked_email
            data["quota_reservation"] = self.quota_reservation
        if self.priority != "normal":
            data["priority"] = self.priority
        if self.transferred_from is not None:
            data["transferred_from"] = self.transferred_from
        if self.transferred_to is not None:
            data["transferred_to"] = self.transferred_to
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Job":
        """Reconstruct a Job from a parsed JSON dictionary with schema validation."""
        version = data.get("schema_version", 1)
        if version > CURRENT_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported job schema version {version} "
                f"(max supported: {CURRENT_SCHEMA_VERSION})"
            )

        tokens_data = data.get("tokens", {})
        tokens = (
            TokenUsage.from_dict(tokens_data) if isinstance(tokens_data, dict) else TokenUsage()
        )
        # Lazy import: gitops imports util/models helpers, so a module-level
        # import here would create an import cycle.
        from agy_mcp.gitops import SkippedFile

        raw_skipped = data.get("skipped_files", [])
        skipped_files = [
            SkippedFile(f["path"], f.get("reason", "skipped"))
            if isinstance(f, dict) and "path" in f
            else f
            for f in raw_skipped
        ]

        return cls(
            job_id=str(data["job_id"]),
            title=str(data["title"]),
            repo_path=Path(data["repo_path"]),
            branch=str(data["branch"]),
            base_commit=str(data["base_commit"]),
            head_commit=str(data.get("head_commit", "")),
            state=JobState(data["state"]),
            model=str(data.get("model", "gemini-3.8-flash-medium")),
            model_resolved=data.get("model_resolved"),
            account=data.get("account"),
            priority=str(data.get("priority", "normal")),
            account_masked_email=data.get("account_masked_email"),
            quota_reservation=data.get("quota_reservation"),
            transferred_from=data.get("transferred_from"),
            transferred_to=data.get("transferred_to"),
            max_minutes=int(data.get("max_minutes", 240)),
            verify=data.get("verify"),
            verify_timeout_seconds=int(data.get("verify_timeout_seconds", 900)),
            conversation_id=data.get("conversation_id"),
            pid=data.get("pid"),
            pgid=data.get("pgid"),
            start_time=data.get("start_time"),
            worktree_path=Path(data["worktree_path"]) if data.get("worktree_path") else None,
            commit_msg_generation=int(data.get("commit_msg_generation", 0)),
            review_reason=data.get("review_reason"),
            source_dirty=bool(data.get("source_dirty", False)),
            recovered=bool(data.get("recovered", False)),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            cumulative_minutes=float(data.get("cumulative_minutes", 0.0)),
            commits_count=int(data.get("commits_count", 0)),
            files_modified=list(data.get("files_modified", [])),
            skipped_files=skipped_files,
            denials=list(data.get("denials", [])),
            denied_commands=list(data.get("denied_commands", [])),
            denied_tools=list(data.get("denied_tools", [])),
            warnings=list(data.get("warnings", [])),
            tokens=tokens,
            schema_version=version,
            verify_status=data.get("verify_status"),
            auto_fix_count=int(data.get("auto_fix_count", 0)),
            denial_resume_count=int(data.get("denial_resume_count", 0)),
            error=data.get("error"),
            stdout_offset=int(data.get("stdout_offset", 0)),
            cli_log_offset=int(data.get("cli_log_offset", 0)),
            cli_log_prefix_sha256=data.get("cli_log_prefix_sha256"),
            conversation_id_source=data.get("conversation_id_source"),
            conversation_continued=(
                bool(data["conversation_continued"])
                if data.get("conversation_continued") is not None
                else None
            ),
            run_generation=int(data.get("run_generation", 1)),
            launch_wall_clock=(
                float(data["launch_wall_clock"])
                if data.get("launch_wall_clock") is not None
                else None
            ),
            usage_at_launch=data.get("usage_at_launch"),
            usage_cost=data.get("usage_cost"),
            quota_estimate=data.get("quota_estimate"),
            runs=list(data.get("runs", []) or []),
            resume_after=data.get("resume_after"),
            plan_summary=data.get("plan_summary"),
            plan_nudge_count=int(data.get("plan_nudge_count", 0)),
            agent_report=data.get("agent_report"),
            audit_status=data.get("audit_status"),
            audit_reason=data.get("audit_reason"),
            scratchpad_files=list(data.get("scratchpad_files", [])),
        )


def derive_next_actions(
    state: JobState,
    review_reason: Optional[str] = None,
    denied_commands: Optional[list[dict[str, Any]]] = None,
    verify_passed: Optional[bool] = None,
    resume_after: Optional[str] = None,
    verify: Optional[dict[str, Any]] = None,
) -> list[str]:
    """Provide contextual next actions for the orchestrator based on current state.

    ``verify`` is the additive verify envelope (D80) used to name the command,
    the timeout and the kill result in the timeout/failure next action.
    """
    if state == JobState.QUEUED:
        return ["Wait for execution to start with agy_wait", "Check status with agy_status"]
    elif state == JobState.RUNNING:
        return ["Wait for completion with agy_wait", "Check status with agy_status"]
    elif state == JobState.NEEDS_DECISION:
        return ["Resume job with decision using agy_resume", "Cancel job with agy_cancel"]
    elif state == JobState.NEEDS_REVIEW:
        if review_reason == "auth_required":
            return [
                (
                    "Ask the human to log in to agy again, then resume with agy_resume"
                ),
                "Inspect changes with agy_review",
                "Cancel job with agy_cancel",
            ]
        if review_reason == "quota_exhausted":
            when = (
                f"wait until {resume_after}"
                if resume_after
                else "wait for the quota window to reset"
            )
            return [
                f"Quota exhausted: {when} then resume with agy_resume",
                "Inspect changes with agy_review",
                "Cancel job with agy_cancel",
            ]
        if review_reason in ("verify_failed", "verification_failed"):
            failed_cmd = (verify or {}).get("command") or "the verify command"
            failed_rc = (verify or {}).get("rc")
            rc_note = f" (rc={failed_rc})" if failed_rc is not None else ""
            return [
                (
                    f"Verification tests failed; {failed_cmd}{rc_note} exited on its own "
                    "(not a timeout). Analyze the last 40 lines with agy_result and resume "
                    "with guidance using agy_resume"
                ),
                "Inspect changes with agy_review",
                "Cancel job with agy_cancel",
            ]
        if review_reason == "verify_timeout":
            timed_cmd = (verify or {}).get("command") or "the verify command"
            timed_out_after = (verify or {}).get("timeout_seconds")
            timeout_note = f" after {timed_out_after}s" if timed_out_after else ""
            killed = (verify or {}).get("killed_by_timeout")
            kill_note = (
                "process group killed"
                if killed
                else "process group already gone"
            )
            return [
                (
                    f"Verification tests timed out{timeout_note}; {timed_cmd} was killed by "
                    f"the wrapper ({kill_note}). Inspect the last 40 lines with agy_result, "
                    "fix the hang or raise verify_timeout_seconds, then resume with agy_resume "
                    "or cancel with agy_cancel"
                ),
                "Inspect changes with agy_review",
                "Cancel job with agy_cancel",
            ]
        if review_reason in ("blocked_by_permission", "denied_without_progress"):
            step_val = "N"
            known_cmd: Optional[str] = None
            if denied_commands and len(denied_commands) > 0:
                step_val = str(denied_commands[0].get("step", "N"))
                for d in denied_commands:
                    cmd_val = d.get("command")
                    if cmd_val:
                        known_cmd = str(cmd_val)
                        break

            if known_cmd:
                guidance = (
                    f"agy tried to run: {known_cmd}. "
                    "Approve by resuming with guidance, or tell the user."
                )
            else:
                guidance = (
                    f"agy was halted running a shell tool (step {step_val}); "
                    "the command text is not available. "
                    "Resume with guidance to use file tools only, or ask the user."
                )
            return [
                guidance,
                "Resume job with advice using agy_resume",
                "Cancel job with agy_cancel",
            ]
        security_reasons = {
            "audit_violation",
            "host_path_modified",
            "instruction_file_modified",
            "audit_unavailable",
        }
        if review_reason in security_reasons:
            return ["Inspect security violation with agy_review", "Clean up job with agy_cleanup"]
        if verify_passed is False:
            return [
                "Verification did not pass; inspect error output with agy_result or agy_review",
                "Inspect changes with agy_review",
                "Resume job with advice using agy_resume",
                "Cancel job with agy_cancel",
            ]
        return [
            "Inspect changes with agy_review",
            "Resume job with advice using agy_resume",
            "Cancel job with agy_cancel",
        ]
    elif state == JobState.TIMEOUT:
        actions = ["Resume job with agy_resume", "Clean up job with agy_cleanup"]
        if verify_passed is False:
            actions.insert(
                0, "Verification did not pass; inspect error output with agy_result or agy_review"
            )
        return actions
    elif state == JobState.DONE:
        actions = [
            "Inspect result with agy_result",
            "Review diff with agy_review",
            "Clean up worktree with agy_cleanup",
        ]
        if verify_passed is False:
            actions.insert(
                0, "Verification did not pass; inspect error output with agy_result or agy_review"
            )
        return actions
    elif state == JobState.FAILED:
        actions = ["Inspect failure details with agy_result", "Clean up with agy_cleanup"]
        if verify_passed is False:
            actions.insert(
                0, "Verification did not pass; inspect error output with agy_result or agy_review"
            )
        return actions
    elif state == JobState.CANCELLED:
        return ["Clean up with agy_cleanup"]
    return ["Check status with agy_status"]


def get_effective_verify_envelope(job: Job) -> dict[str, Any]:
    """Derive standard verify envelope field per SPEC 13 & D54."""
    requested = bool(job.verify and str(job.verify).strip())
    if not requested:
        return {
            "requested": False,
            "ran": False,
            "passed": None,
            "rc": None,
            "duration_seconds": None,
            "attempts": 0,
            "last_tail": None,
            "status": None,
            "timed_out": False,
            "killed_by_timeout": False,
            "command": None,
            "timeout_seconds": None,
            "cause": None,
            "next_action": None,
        }

    # Verify was requested
    if job.verify_status and job.verify_status.get("ran"):
        status = job.verify_status
        rc = status.get("rc")
        passed = status.get("passed")
        timed_out = bool(status.get("timed_out", rc == 124))
        derived_status = status.get("status")
        if not derived_status:
            derived_status = "timeout" if timed_out else ("passed" if passed else "failed")
        env_dict: dict[str, Any] = {
            "requested": True,
            "ran": True,
            "passed": passed,
            "rc": rc,
            "duration_seconds": status.get("duration_seconds"),
            "attempts": status.get("attempts", 1),
            "last_tail": status.get("last_tail"),
            # Additive diagnostics (D80); timeout is distinguishable from a
            # command that failed by itself in both fields.
            "status": derived_status,
            "timed_out": timed_out,
            "killed_by_timeout": bool(status.get("killed_by_timeout", timed_out)),
            "command": status.get("command", job.verify),
            "timeout_seconds": status.get(
                "timeout_seconds", getattr(job, "verify_timeout_seconds", None)
            ),
            "cause": status.get("cause"),
            "next_action": status.get("next_action"),
        }
        return env_dict

    # Verify was requested but did not run (job ended before it, or in progress)
    reason: Optional[str] = None
    if job.verify_status and job.verify_status.get("reason"):
        reason = job.verify_status["reason"]
    elif job.state in (
        JobState.FAILED,
        JobState.CANCELLED,
        JobState.TIMEOUT,
        JobState.NEEDS_REVIEW,
    ):
        reason = job.review_reason or (
            job.state.value if isinstance(job.state, JobState) else str(job.state)
        )

    attempts_val = job.verify_status.get("attempts", 0) if job.verify_status else 0
    res: dict[str, Any] = {
        "requested": True,
        "ran": False,
        "passed": None,
        "rc": None,
        "duration_seconds": None,
        "attempts": attempts_val,
        "last_tail": None,
        "status": "not_run",
        "timed_out": False,
        "killed_by_timeout": False,
        "command": job.verify,
        "timeout_seconds": getattr(job, "verify_timeout_seconds", None),
        "cause": None,
        "next_action": None,
    }
    if reason:
        res["reason"] = reason
    return res


def get_plan_envelope(job: Job) -> dict[str, Any]:
    """Build the envelope ``plan`` field from wrapper-computed plan counts (SPEC §8).

    ``remaining`` titles come from the agent's PLAN.md, so each is wrapped as
    untrusted text and capped. This function performs no I/O: the manager reads
    and stores ``plan_summary`` at finalization.
    """
    summary = job.plan_summary or {}
    try:
        total = int(summary.get("total", 0) or 0)
    except (TypeError, ValueError):
        total = 0
    try:
        done = int(summary.get("done", 0) or 0)
    except (TypeError, ValueError):
        done = 0
    raw_remaining = summary.get("remaining") or []
    remaining: list[str] = []
    for title in list(raw_remaining)[:30]:
        text = str(title).strip()
        if text:
            remaining.append(wrap_untrusted_text(text[:200]))
    return {"total": total, "done": done, "remaining": remaining}


def build_facts(job: Job, verify_env: dict[str, Any]) -> dict[str, Any]:
    """Build the envelope ``facts`` block from wrapper-owned job state only.

    Every value here comes from what the wrapper observed (git counts, its own
    verify and audit results, parsed plan counts, denial records). Agent text is
    never copied into ``facts``; that is what ``agent_report`` is for.
    """
    summary = job.plan_summary or {}
    try:
        plan_total = int(summary.get("total", 0) or 0)
    except (TypeError, ValueError):
        plan_total = 0
    try:
        plan_done = int(summary.get("done", 0) or 0)
    except (TypeError, ValueError):
        plan_done = 0
    remaining = summary.get("remaining") or []
    try:
        plan_remaining = len(list(remaining))
    except TypeError:
        plan_remaining = 0

    audit: dict[str, Any] = {"status": job.audit_status or "not_run"}
    if job.audit_reason:
        audit["reason"] = job.audit_reason

    return {
        "commits_count": int(job.commits_count),
        "files_modified_count": len(job.files_modified),
        "verify": {
            "requested": bool(verify_env.get("requested")),
            "ran": bool(verify_env.get("ran")),
            "passed": verify_env.get("passed"),
            "rc": verify_env.get("rc"),
            "attempts": verify_env.get("attempts", 0),
        },
        "audit": audit,
        "plan": {
            "total": plan_total,
            "done": plan_done,
            "remaining": plan_remaining,
        },
        "denials": {
            "count": len(job.denials),
            "commands": len(job.denied_commands),
            "tools": len(job.denied_tools),
        },
    }


def _error_with_untrusted_first_line(error: Optional[Any]) -> Optional[Any]:
    """Return the envelope error with ``first_line_repr`` wrapped as untrusted.

    ``first_line_repr`` is agent-written COMMIT_MSG text kept as rejection
    evidence. Like every other agent output in the envelope it must be wrapped
    so it cannot spoof envelope tags; the raw value stays in ``job.error``.
    """
    if not isinstance(error, dict) or "first_line_repr" not in error:
        return error
    raw = error.get("first_line_repr")
    if not isinstance(raw, str) or raw.startswith("<untrusted_text"):
        return error
    safe = dict(error)
    safe["first_line_repr"] = wrap_untrusted_text(raw[:200])
    return safe


def build_standard_envelope(
    job: Job,
    elapsed_seconds: float = 0.0,
    error: Optional[Any] = None,
) -> StandardEnvelope:
    """Build a StandardEnvelope from a Job instance."""
    verify_env = get_effective_verify_envelope(job)
    v_passed = verify_env.get("passed")
    effective_error = _error_with_untrusted_first_line(
        error if error is not None else job.error
    )
    return StandardEnvelope(
        job_id=job.job_id,
        state=job.state,
        elapsed_seconds=round(elapsed_seconds, 2),
        title=job.title,
        model_resolved=job.model_resolved,
        branch=job.branch,
        base_commit=job.base_commit,
        head_commit=job.head_commit or job.base_commit,
        commits_count=job.commits_count,
        files_modified=job.files_modified,
        skipped_files=job.skipped_files,
        denials=job.denials,
        denied_commands=job.denied_commands,
        denied_tools=job.denied_tools,
        warnings=job.warnings,
        tokens=job.tokens,
        next_actions=derive_next_actions(
            job.state,
            job.review_reason,
            job.denied_commands,
            verify_passed=v_passed,
            resume_after=job.resume_after,
            verify=verify_env,
        ),
        error=effective_error,
        usage_cost=job.usage_cost,
        quota_estimate=job.quota_estimate,
        conversation_continued=job.conversation_continued,
        resume_after=job.resume_after,
        plan=get_plan_envelope(job),
        facts=build_facts(job, verify_env),
        agent_report=job.agent_report,
        scratchpad=[str(name) for name in job.scratchpad_files],
        account=job.account,
        account_email=job.account_masked_email,
        transferred_from=job.transferred_from,
        transferred_to=job.transferred_to,
        verify=verify_env,
    )


def standard_envelope_fields(envelope: StandardEnvelope) -> dict[str, Any]:
    """Return every ``StandardEnvelope`` field as keyword arguments.

    The extended payloads (status, result, review) are dataclasses that inherit
    the base envelope. They used to copy base fields one by one, so a newly
    added base field silently fell back to its dataclass default (that is how
    agy_review lost ``plan``/``facts``/``agent_report``/``scratchpad`` while
    agy_wait returned them). Building every extended payload through this one
    helper keeps SPEC section 8's "consistent base schema" promise in one
    place; each builder only adds or overrides its own fields.
    """
    return {
        "job_id": envelope.job_id,
        "state": envelope.state,
        "elapsed_seconds": envelope.elapsed_seconds,
        "title": envelope.title,
        "model_resolved": envelope.model_resolved,
        "branch": envelope.branch,
        "base_commit": envelope.base_commit,
        "head_commit": envelope.head_commit,
        "commits_count": envelope.commits_count,
        "files_modified": envelope.files_modified,
        "skipped_files": envelope.skipped_files,
        "denials": envelope.denials,
        "denied_commands": envelope.denied_commands,
        "denied_tools": envelope.denied_tools,
        "warnings": envelope.warnings,
        "tokens": envelope.tokens,
        "next_actions": envelope.next_actions,
        "error": envelope.error,
        "usage_cost": envelope.usage_cost,
        "quota_estimate": envelope.quota_estimate,
        "conversation_continued": envelope.conversation_continued,
        "resume_after": envelope.resume_after,
        "plan": envelope.plan,
        "facts": envelope.facts,
        "agent_report": envelope.agent_report,
        "scratchpad": envelope.scratchpad,
        "verify": envelope.verify,
    }


@dataclass
class JobStatusResponse(StandardEnvelope):
    """Extended payload for agy_status."""

    last_cli_log_mtime: float = 0.0
    last_commit_at: Optional[str] = None
    worktree_dirty: bool = False
    recent_log_tail: list[str] = field(default_factory=list)
    conversation_id: Optional[str] = None
    conversation_id_source: Optional[str] = None
    runs: list[dict[str, Any]] = field(default_factory=list)
    wall_clock_seconds: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the status payload, adding liveness fields to the base envelope."""
        base = super().to_dict()
        base.update(
            {
                "last_cli_log_mtime": self.last_cli_log_mtime,
                "last_commit_at": self.last_commit_at,
                "worktree_dirty": self.worktree_dirty,
                "recent_log_tail": self.recent_log_tail,
                "conversation_id": self.conversation_id,
                "conversation_id_source": self.conversation_id_source,
                "runs": self.runs,
                "wall_clock_seconds": self.wall_clock_seconds,
            }
        )
        return base


@dataclass
class JobResultResponse(StandardEnvelope):
    """Extended payload for agy_result with enforced byte caps."""

    response: str = ""
    diff_stat: str = ""
    commits: list[dict[str, Any]] = field(default_factory=list)
    audit_events_tail: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the result payload, adding capped output fields."""
        base = super().to_dict()
        base.update(
            {
                "response": self.response,
                "diff_stat": self.diff_stat,
                "commits": self.commits,
                "audit_events_tail": self.audit_events_tail,
            }
        )
        return base


def build_result_response(
    job: Job,
    raw_response: str,
    raw_diff_stat: str,
    raw_commits: list[dict[str, Any]],
    audit_events_tail: list[dict[str, Any]],
    elapsed_seconds: float = 0.0,
    error: Optional[str] = None,
    home: Optional[Path] = None,
) -> JobResultResponse:
    """Build a JobResultResponse applying strict byte caps and untrusted_text wrapping.

    The response body is path-sanitized before wrapping (smoke finding 9) so the
    orchestrator never receives the absolute worktree or home directory.
    """
    capped_response = truncate_tail(raw_response, MAX_RESPONSE_BYTES)
    sanitized_response = sanitize_absolute_paths(capped_response, job.worktree_path, home)
    wrapped_response = wrap_untrusted_text(sanitized_response)

    # Reserve overhead for <untrusted_text id="..."> wrapper tags (~85 bytes)
    # Wrapper tags cost bytes but are not part of the content budget, so every
    # capped field below subtracts a conservative overhead before truncating.
    diff_stat_overhead = 100
    capped_diff_stat = truncate_tail(
        raw_diff_stat, max(0, MAX_DIFF_STAT_BYTES - diff_stat_overhead)
    )
    wrapped_diff_stat = wrap_untrusted_text(capped_diff_stat)

    # Cap commits to MAX_COMMITS_COUNT and each commit message subject to MAX_COMMIT_MSG_BYTES
    commit_msg_overhead = 100
    capped_commits: list[dict[str, Any]] = []
    for c in raw_commits[:MAX_COMMITS_COUNT]:
        commit_copy = dict(c)
        if "subject" in commit_copy and isinstance(commit_copy["subject"], str):
            capped_subj = truncate_tail(
                commit_copy["subject"], max(0, MAX_COMMIT_MSG_BYTES - commit_msg_overhead)
            )
            commit_copy["subject"] = wrap_untrusted_text(capped_subj)
        capped_commits.append(commit_copy)

    # Bounded audit events tail: max 20 entries, each string field in detail capped to 1024 bytes
    # Keep the audit tail small: 20 events and 1 KB per detail string, otherwise
    # a single noisy event could dominate the result envelope.
    capped_audit_events: list[dict[str, Any]] = []
    for event in audit_events_tail[-20:]:
        ev_copy = dict(event)
        if "detail" in ev_copy and isinstance(ev_copy["detail"], dict):
            detail_copy = dict(ev_copy["detail"])
            for k, v in detail_copy.items():
                if isinstance(v, str):
                    detail_copy[k] = truncate_tail(v, 1024)
            ev_copy["detail"] = detail_copy
        capped_audit_events.append(ev_copy)

    envelope = build_standard_envelope(job, elapsed_seconds=elapsed_seconds, error=error)
    fields = standard_envelope_fields(envelope)
    return JobResultResponse(
        **fields,
        response=wrapped_response,
        diff_stat=wrapped_diff_stat,
        commits=capped_commits,
        audit_events_tail=capped_audit_events,
    )


@dataclass
class JobReviewResponse(StandardEnvelope):
    """Extended payload for agy_review."""

    merge_base: str = ""
    ahead: int = 0
    behind: int = 0
    base_moved: bool = False
    audit_findings: list[dict[str, Any]] = field(default_factory=list)
    untrusted_diff: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize the review payload, adding divergence and audit fields."""
        base = super().to_dict()
        base.update(
            {
                "merge_base": self.merge_base,
                "ahead": self.ahead,
                "behind": self.behind,
                "base_moved": self.base_moved,
                "audit_findings": self.audit_findings,
                "untrusted_diff": self.untrusted_diff,
            }
        )
        return base


def build_review_response(
    job: Job,
    merge_base: str,
    ahead: int,
    behind: int,
    base_moved: bool,
    audit_findings: list[dict[str, Any]],
    raw_untrusted_diff: str,
    elapsed_seconds: float = 0.0,
    error: Optional[str] = None,
    audit_unavailable: bool = False,
) -> JobReviewResponse:
    """Build a JobReviewResponse applying 32KB byte cap and untrusted_text wrapping."""
    capped_diff = truncate_tail(raw_untrusted_diff, MAX_UNTRUSTED_DIFF_BYTES)
    wrapped_diff = wrap_untrusted_text(capped_diff)

    envelope = build_standard_envelope(job, elapsed_seconds=elapsed_seconds, error=error)
    next_actions = list(envelope.next_actions)
    if audit_unavailable:
        next_actions.insert(
            0,
            "Audit data is unavailable; a human must review this job before merging",
        )
    fields = standard_envelope_fields(envelope)
    fields["next_actions"] = next_actions
    return JobReviewResponse(
        **fields,
        merge_base=merge_base,
        ahead=ahead,
        behind=behind,
        base_moved=base_moved,
        audit_findings=audit_findings,
        untrusted_diff=wrapped_diff,
    )


@dataclass
class JobCleanupResponse:
    """Payload returned by agy_cleanup."""

    job_id: str
    worktree_removed: bool
    branch_deleted: bool
    freed_bytes: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize the cleanup payload (worktree/branch flags and freed bytes)."""
        return asdict(self)


@dataclass
class JobListResponse:
    """Payload returned by agy_list."""

    jobs: list[StandardEnvelope]
    total_active_jobs: int
    disk_usage_bytes: int
    drifted_worktrees: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Serialize the list payload, nesting each job envelope."""
        return {
            "jobs": [j.to_dict() for j in self.jobs],
            "total_active_jobs": self.total_active_jobs,
            "disk_usage_bytes": self.disk_usage_bytes,
            "drifted_worktrees": self.drifted_worktrees,
        }
