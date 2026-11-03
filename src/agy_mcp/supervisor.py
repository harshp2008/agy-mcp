"""Per-job supervision thread and finalization (SPEC sections 10-16).

Owns the deadline, periodic audit, watcher driving, process polling, outcome
parsing, verify invocation, commit gates, denial enrichment, usage cost and the
final state transition. It must never leave an agy process group alive behind a
non-running job, never commit after a fatal audit, and never report `done`
without parsing a successful payload (D49).
"""

import logging
import os
import shlex
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from agy_mcp import scratchpad
from agy_mcp import usage as usage_utils
from agy_mcp.audit import (
    AuditSeverity,
    AuditSnapshot,
    Finding,
    Findings,
    audit,
)
from agy_mcp.cli_log import (
    cli_log_generation_start,
    extract_conversation_id_from_cli_log,
    extract_denials_from_cli_log,
    extract_model_resolved,
    extract_resuming_conversation_id_from_cli_log,
    format_denied_commands,
    read_cli_log_head,
    read_cli_log_since,
    read_cli_log_tail,
)
from agy_mcp.gitops import (
    GitError,
    checkpoint_commit,
    get_files_modified,
    is_worktree_dirty,
    safety_net_commit,
)
from agy_mcp.jobs import (
    append_audit_event,
    count_active_jobs,
    record_cleanup_error,
    record_job_execution_time,
    save_job,
    transition_job_state,
)
from agy_mcp.models import Job, TokenUsage, wrap_untrusted_text
from agy_mcp.parse_result import StdoutPayload, extract_stdout_payload, parse_agy_output
from agy_mcp.runner import ProcessHandle, Runner
from agy_mcp.states import JobState
from agy_mcp.transcript import read_denied_calls
from agy_mcp.util import (
    read_file_since,
    read_file_tail,
    redact_log_text,
    strict_uuid,
    truncate_tail,
)
from agy_mcp.watcher import CommitWatcher

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an accounts import cycle
    from agy_mcp.accounts.registry import AccountConfig

logger = logging.getLogger(__name__)

# "Created conversation <uuid>" is logged in the first lines of a run. Scan
# only this much of the head so a long-running job never re-reads the log.
MAX_EARLY_CONVERSATION_SCAN_BYTES = 256 * 1024
# Bound for reading the last stdout JSON payload when a non-running generation
# is finalized. agy's final JSON can be large; only its tail is needed.
MAX_STDOUT_PAYLOAD_BYTES = 10 * 1024 * 1024


def select_conversation_id(
    stored_value: object, observed_value: object
) -> tuple[Optional[str], bool]:
    """Choose between a stored and an observed conversation id.

    Returns ``(chosen, mismatch)``. A valid stored UUID is sticky: an observed
    different UUID is not applied (``mismatch`` is True so the caller can audit
    it). A stored non-UUID is treated as absent and gives way to a valid
    observation. Anything that is not a full UUID is ignored.
    """
    stored = strict_uuid(stored_value)
    observed = strict_uuid(observed_value)
    if observed is not None:
        if stored is not None:
            return stored, stored != observed
        return observed, False
    return stored, False


def _classify_tool_kind(tool: str) -> str:
    """Classify tool name into one of: shell, file_read, file_write, subagent, other."""
    tl = (tool or "").lower()
    if tl in ("runcommand", "run_command", "shell", "command"):
        return "shell"
    elif any(k in tl for k in ("read_file", "view_file", "viewfile", "view")):
        return "file_read"
    elif any(k in tl for k in ("write_file", "writefile", "edit_file", "create_file")):
        return "file_write"
    elif "subagent" in tl:
        return "subagent"
    return "other"


class JobSupervisor:
    """Supervises process group execution, enforces deadlines, and manages watcher."""

    def __init__(
        self,
        job: Job,
        handle: ProcessHandle,
        runner: Runner,
        job_dir: Path,
        max_minutes: int,
        max_minutes_cap: int = 360,
        on_state_change: Optional[Callable[[Job], None]] = None,
        baseline_snapshot: Optional[AuditSnapshot] = None,
        home: Optional[Path] = None,
        base_state_dir: Optional[Path] = None,
        poll_interval_seconds: float = 1.0,
        audit_interval_seconds: float = 5.0,
        grace_kill_seconds: float = 3.0,
        settle_window_seconds: float = 5.0,
        checkpoint_interval_seconds: int = 600,
        agy_home: Optional[Path] = None,
        commit_lock: Optional[threading.Lock] = None,
        agy_binary: Optional[str] = None,
        account_name: Optional[str] = None,
        account_masked_email: Optional[str] = None,
        account_home: Optional[Path] = None,
        account: Optional["AccountConfig"] = None,
        usage_cache: Optional[Any] = None,
        quota_error_patterns: Optional[list[str]] = None,
        auth_error_patterns: Optional[list[str]] = None,
        plan_nudge: int = 1,
        claim_phrases: Optional[list[str]] = None,
        quota_estimate_weekly_per_1k_tokens: float = 0.0031,
        quota_estimate_five_hour_per_1k_tokens: float = 0.011,
    ) -> None:
        """Wire one supervised generation: its process, paths, clocks and hooks.

        ``job``/``handle``/``runner`` are the live generation, ``job_dir`` its
        state directory (audit log, checkpoint, audit snapshot). The intervals,
        grace windows and ``home``/``agy_home`` are injected so tests can drive
        time and paths deterministically (SPEC §10); ``on_state_change`` is the
        manager's persistence hook and ``commit_lock`` is the lock the finalizer
        and the watcher share for checkpoint commits. The usage/quota knobs come
        from config so the supervisor never re-reads it.

        Derived here rather than passed: ``deadline_seconds`` =
        min(max_minutes, max_minutes_cap) * 60 + 120 (the 120 s grace lets agy's
        own print-timeout fire first), ``started_at`` from the launch wall clock
        or the job start time (falling back to now), and the audit baseline
        snapshot. A missing or corrupt baseline snapshot is fail-closed: the
        supervisor starts already ``is_audit_violated`` so the generation can
        never be reviewed as clean.

        The ``CommitWatcher`` is built last and gets ``is_process_alive`` and
        ``before_state_transition`` callbacks, so watcher and supervisor share one
        liveness answer and the usage row is recorded before the state write.
        """
        self.job = job
        self.handle = handle
        self.runner = runner
        self.job_dir = job_dir
        self.max_minutes = max_minutes
        self.max_minutes_cap = max_minutes_cap
        self.on_state_change = on_state_change
        self.home = home
        self.base_state_dir = base_state_dir
        self.poll_interval_seconds = poll_interval_seconds
        self.audit_interval_seconds = audit_interval_seconds
        self.grace_kill_seconds = grace_kill_seconds
        self.settle_window_seconds = settle_window_seconds
        self.checkpoint_interval_seconds = checkpoint_interval_seconds
        self.agy_home = agy_home
        self.commit_lock = commit_lock or threading.Lock()
        self.agy_binary = str(agy_binary or getattr(runner, "executable", "agy"))
        self.account_name = account_name
        self.account_masked_email = account_masked_email
        # Usage must be read from the job's account HOME, never the host HOME:
        # a non-default account's quota lives under its own home (D104/D107).
        # The account object also selects the isolated child env for the final
        # `/usage` reading (dead D-Bus, human test 2026-11-03).
        self.account_home = account_home
        self.account = account
        self.usage_cache = usage_cache
        self.quota_error_patterns = list(quota_error_patterns or [])
        self.auth_error_patterns = list(auth_error_patterns or [])
        self.plan_nudge = plan_nudge
        self.claim_phrases = list(claim_phrases or [])
        self.quota_estimate_weekly_per_1k_tokens = float(
            quota_estimate_weekly_per_1k_tokens
        )
        self.quota_estimate_five_hour_per_1k_tokens = float(
            quota_estimate_five_hour_per_1k_tokens
        )

        # Wall-clock deadline per SPEC section 10: min(max_minutes, max_minutes_cap) * 60 + 120s
        # The 120 s grace lets agy's own --print-timeout fire first and produce
        # a JSON payload before the supervisor resorts to killing the group.
        effective_minutes = min(self.max_minutes, self.max_minutes_cap)
        self.deadline_seconds = effective_minutes * 60 + 120

        if getattr(job, "launch_wall_clock", None) is not None:
            self.started_at = float(job.launch_wall_clock)
        elif job.start_time is not None:
            self.started_at = float(job.start_time)
        else:
            self.started_at = time.time()
        self.last_audit_time: float = self.started_at
        self.supervisor_timed_out: bool = False
        self.is_cancelled: bool = False
        self._verify_proc: Optional[Any] = None
        self.is_audit_violated: bool = False
        self.audit_violation_reason: Optional[str] = None
        self._stopped: bool = False
        self._recorded_audit_events: set[tuple[str, str, Any]] = set()
        # Conversation-id mismatch audit is once per (stored, observed) pair.
        self._mismatch_reported: set[tuple[str, str]] = set()
        # The UUID this generation was launched with (None for a fresh run).
        # Used to decide whether agy actually resumed "that same UUID".
        self._launched_conversation_id: Optional[str] = strict_uuid(job.conversation_id)
        # Last parsed stdout JSON payload, keyed by (size, mtime_ns) so a
        # non-running finalize parses stdout once and record_run/record_usage
        # share the same tokens (F3).
        self._stdout_payload_cache_key: Optional[tuple[int, int]] = None
        self._stdout_payload_cache: Optional[StdoutPayload] = None
        # Guards a single ``runs`` entry per generation (the run row and the
        # finalizer can both reach the recording helpers).
        self._run_recorded_generation: Optional[int] = None
        # Last cli.log size scanned for early conversation-id persistence.
        self._early_cli_log_size: Optional[int] = None
        # Set by _record_usage_before_state so _finalize_non_running_process
        # does not append a second usage row for the same run (C4).
        self._usage_recorded_state: Optional[JobState] = None

        # Take or use baseline snapshot
        from agy_mcp.audit import load_snapshot

        loaded_baseline = load_snapshot(self.job_dir / "snapshot.json")
        if baseline_snapshot is not None:
            self.baseline_snapshot = baseline_snapshot
        elif loaded_baseline is not None:
            self.baseline_snapshot = loaded_baseline
        else:
            self.baseline_snapshot = None
            self.is_audit_violated = True
            self.audit_violation_reason = "Missing or corrupted audit baseline snapshot"

        # Initialize commit watcher
        self.watcher = CommitWatcher(
            job=self.job,
            on_audit_violation=self._handle_watcher_audit_violation,
            baseline_snapshot=self.baseline_snapshot,
            home=self.home,
            base_state_dir=self.base_state_dir,
            settle_window_seconds=self.settle_window_seconds,
            checkpoint_interval_seconds=self.checkpoint_interval_seconds,
            commit_lock=self.commit_lock,
            is_process_alive=lambda: self.runner.poll(self.handle) is None,
            before_state_transition=self._record_usage_before_state,
        )

    def _handle_watcher_audit_violation(self, reason: str) -> None:
        self.is_audit_violated = True
        self.audit_violation_reason = reason
        self.runner.kill(self.handle, grace_seconds=self.grace_kill_seconds)

    def terminate_group(self, grace_seconds: float = 3.0) -> None:
        """Send SIGTERM to process group, wait grace period, then SIGKILL."""
        import signal

        self.is_cancelled = True
        vp = getattr(self, "_verify_proc", None)
        if vp is not None:
            try:
                os.killpg(vp.pid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            try:
                time.sleep(0.1)
                if vp.poll() is None:
                    os.killpg(vp.pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass

        # Pre-kill checkpoint if worktree dirty and not audit-violated
        if not self.is_audit_violated:
            self._try_pre_kill_checkpoint()
        self.runner.kill(self.handle, grace_seconds=grace_seconds)

    def _audit_conversation_id_mismatch(self, stored: str, observed: str) -> None:
        """Audit a stored/observed UUID difference once, without any message text."""
        key = (stored, observed)
        if key in self._mismatch_reported:
            return
        self._mismatch_reported.add(key)
        try:
            append_audit_event(
                self.job_dir,
                event="conversation_id_mismatch",
                detail={"stored": stored, "observed": observed},
            )
        except Exception:
            logger.warning("conversation_id_mismatch could not be audited")

    def _warn_conversation_id_mismatch(self) -> None:
        """Expose a stored/observed id difference as an additive result warning."""
        if "conversation_id_mismatch" not in self.job.warnings:
            self.job.warnings.append("conversation_id_mismatch")

    def _stdout_payload(self) -> Optional[StdoutPayload]:
        """Return the last stdout JSON payload, reading a bounded tail once.

        Cached by (size, mtime_ns) so the finalizer can hand the same tokens to
        ``_record_run`` and ``record_usage`` without parsing stdout twice.
        """
        stdout_path = self.job_dir / "stdout.txt"
        try:
            st = stdout_path.stat()
            key: Optional[tuple[int, int]] = (st.st_size, st.st_mtime_ns)
        except OSError:
            key = None
        if key is not None and self._stdout_payload_cache_key == key:
            return self._stdout_payload_cache
        # stdout.txt is appended across generations; only this generation's
        # bytes may supply tokens or a conversation id.
        offset = int(getattr(self.job, "stdout_offset", 0) or 0)
        if offset > 0:
            content = read_file_since(stdout_path, offset, MAX_STDOUT_PAYLOAD_BYTES)
        else:
            content = read_file_tail(stdout_path, MAX_STDOUT_PAYLOAD_BYTES)
        payload = extract_stdout_payload(content)
        self._stdout_payload_cache_key = key
        self._stdout_payload_cache = payload
        return payload

    def _stdout_json_conversation_id(self) -> Optional[str]:
        """Return a strict UUID from the last JSON object in stdout.txt, if any."""
        payload = self._stdout_payload()
        return payload.conversation_id if payload is not None else None

    def _generation_cli_log_text(self) -> str:
        """Return cli.log bytes written since this generation's recorded anchor."""
        cli_log_path = self.job_dir / "cli.log"
        offset = int(getattr(self.job, "cli_log_offset", 0) or 0)
        if offset <= 0:
            # Pre-upgrade jobs have no anchor; keep the historical head+tail read.
            return read_cli_log_head(cli_log_path) + "\n" + read_cli_log_tail(cli_log_path)
        return read_cli_log_since(
            cli_log_path,
            offset,
            getattr(self.job, "cli_log_prefix_sha256", None),
            MAX_EARLY_CONVERSATION_SCAN_BYTES,
        )

    def _extract_conversation_id(self, parsed_cid: Optional[str] = None) -> Optional[str]:
        """Resolve the conversation id with strict UUIDs and a fixed precedence.

        1. A valid id from agy's stdout JSON.
        2. This generation's ``Created conversation`` / ``resuming conversation``
           lines in cli.log.

        A UUID from a prior finalized run is sticky: it is never replaced by a
        different UUID (the mismatch is audited instead). An id that came only
        from this job's early cli.log scan is NOT sticky: when agy's own stdout
        later reports a different valid UUID, that stdout UUID wins and the
        mismatch is exposed as an additive warning (F1).
        """
        observed = strict_uuid(parsed_cid)
        stored = strict_uuid(self.job.conversation_id)
        source = getattr(self.job, "conversation_id_source", None)

        # F4: a valid stored UUID is authoritative; return it before reading
        # stdout at all. Callers that already parsed agy's payload pass the
        # observed id in, which is what lets a real stdout UUID replace an
        # early-scan-only id (F1).
        if observed is None and stored is not None:
            return stored
        if observed is None:
            observed = self._stdout_json_conversation_id()

        if observed is not None and stored is not None and observed != stored:
            if source == "early":
                self._warn_conversation_id_mismatch()
                self.job.conversation_id_source = "finalized"
                return observed
            chosen, mismatch = select_conversation_id(stored, observed)
            if mismatch:
                self._audit_conversation_id_mismatch(chosen, observed)
                self._warn_conversation_id_mismatch()
            return chosen

        chosen, mismatch = select_conversation_id(stored, observed)
        if mismatch and chosen is not None and observed is not None:
            self._audit_conversation_id_mismatch(chosen, observed)
            self._warn_conversation_id_mismatch()
        if chosen is not None:
            if observed is not None and chosen == observed:
                self.job.conversation_id_source = "finalized"
            return chosen

        # Last resort: this generation's cli.log (stdout JSON is preferred).
        cid = extract_conversation_id_from_cli_log(self._generation_cli_log_text())
        if cid is not None:
            self.job.conversation_id_source = "early"
        return cid

    def _apply_conversation_id(self, parsed_cid: Optional[str] = None) -> Optional[str]:
        """Resolve and persist-in-memory the conversation id, clearing non-UUIDs.

        The id merge itself never reads stdout for a stored UUID (F4). The one
        exception is an early-scan-only id with no observed id supplied: a
        bounded stdout tail is read here, before the merge, so a real stdout
        UUID can still replace it (F1).
        """
        if (
            parsed_cid is None
            and getattr(self.job, "conversation_id_source", None) == "early"
            and strict_uuid(self.job.conversation_id) is not None
        ):
            parsed_cid = self._stdout_json_conversation_id()
        cid = self._extract_conversation_id(parsed_cid)
        self.job.conversation_id = cid
        return cid

    def _update_conversation_continued(
        self, observed_cid: Optional[str], cli_text: Optional[str] = None
    ) -> None:
        """Set conversation_continued from what agy itself reported for this run.

        Only meaningful when this generation was launched with ``--conversation``.
        agy's real ``resuming conversation`` line wins; otherwise its stdout JSON
        id counts as the report. With no evidence the launch-time meaning stays.
        """
        launched = self._launched_conversation_id
        if launched is None:
            return
        if cli_text is None:
            cli_text = self._generation_cli_log_text()
        reported = extract_resuming_conversation_id_from_cli_log(cli_text)
        if reported is None:
            reported = strict_uuid(observed_cid)
        if reported is None:
            return
        continued = reported == launched
        self.job.conversation_continued = continued
        if not continued:
            warning = "conversation_not_continued: agy reported a different conversation id"
            if warning not in self.job.warnings:
                self.job.warnings.append(warning)

    def _persist_early_conversation_id(self) -> None:
        """Persist this generation's early conversation line while agy runs.

        A kill or cancel can happen before stdout's final JSON exists; reading
        the early cli.log line and saving job.json immediately means the id is
        never lost in that window. The scan starts at the generation anchor so a
        stale line in an appended log is never attributed to this run (F1).
        """
        stored = strict_uuid(self.job.conversation_id)
        source = getattr(self.job, "conversation_id_source", None)
        if stored is not None and source != "early":
            return
        cli_log_path = self.job_dir / "cli.log"
        if not cli_log_path.is_file():
            return
        try:
            size = cli_log_path.stat().st_size
        except OSError:
            return
        if size == 0:
            return
        # The Created line appears in the first lines of a run. Once this
        # generation's window has grown past the cap (or the size has not
        # changed), skip the scan so a multi-hour job does not re-read on every
        # tick. Resolve the generation start first so a truncated log that has
        # already grown past the old offset is still scanned from byte 0.
        offset = int(getattr(self.job, "cli_log_offset", 0) or 0)
        start = cli_log_generation_start(
            cli_log_path, offset, getattr(self.job, "cli_log_prefix_sha256", None)
        )
        if start < 0:
            return
        if size - start > MAX_EARLY_CONVERSATION_SCAN_BYTES:
            return
        if size == self._early_cli_log_size:
            return
        self._early_cli_log_size = size
        try:
            cid = extract_conversation_id_from_cli_log(self._generation_cli_log_text())
        except Exception:
            return
        if cid:
            if stored == cid and source == "early":
                return
            self.job.conversation_id = cid
            self.job.conversation_id_source = "early"
            try:
                save_job(self.job, self.base_state_dir)
            except Exception:
                logger.warning("early conversation_id save failed")


    def _current_run_seconds(self, now: Optional[float] = None) -> float:
        """This run's wrapper-measured wall-clock seconds (never agy's age)."""
        end = time.time() if now is None else now
        return max(0.0, end - self.started_at)

    def _record_run(
        self,
        wrapper_seconds: float,
        agy_duration_seconds: Optional[float],
        tokens: Optional[dict[str, Any]],
        outcome: Optional[str] = None,
        started_at: Optional[str] = None,
        ended_at: Optional[str] = None,
    ) -> None:
        """Append one additive per-run record to job.json (tokens live per run)."""
        generation = int(getattr(self.job, "run_generation", 1) or 1)
        start_iso = started_at or datetime.fromtimestamp(
            self.started_at, tz=timezone.utc
        ).isoformat()
        end_iso = ended_at or datetime.fromtimestamp(
            self.started_at + max(0.0, wrapper_seconds), tz=timezone.utc
        ).isoformat()

        if self._run_recorded_generation == generation:
            if outcome and self.job.runs:
                self.job.runs[-1]["outcome"] = outcome
            return
        entry: dict[str, Any] = {
            "run_generation": generation,
            "started_at": start_iso,
            "ended_at": end_iso,
            "wall_clock_seconds": round(max(0.0, wrapper_seconds), 3),
            "agy_duration_seconds": agy_duration_seconds,
            "tokens": tokens,
            "outcome": outcome,
        }
        runs = list(getattr(self.job, "runs", []) or [])
        runs.append(entry)
        self.job.runs = runs[-50:]
        self._run_recorded_generation = generation

    def _compute_quota_estimate(self) -> None:
        """Store a token-based quota estimate beside the measured usage_cost."""
        try:
            from agy_mcp.usage import compute_quota_estimate

            tokens = (
                self.job.tokens.to_dict()
                if hasattr(self.job.tokens, "to_dict")
                else dict(self.job.tokens or {})
            )
            self.job.quota_estimate = compute_quota_estimate(
                tokens,
                weekly_per_1k_tokens=self.quota_estimate_weekly_per_1k_tokens,
                five_hour_per_1k_tokens=self.quota_estimate_five_hour_per_1k_tokens,
            )
        except Exception:
            self.job.quota_estimate = None

    def _detect_interruption_reason(
        self,
        stderr_content: str,
        cli_log_text: str,
        final_usage_reading: dict[str, Any],
    ) -> tuple[Optional[str], Optional[str]]:
        """Detect quota exhaustion from the fresh reading and configured patterns."""
        combined_text = "\n".join([stderr_content or "", cli_log_text or ""])
        if (
            isinstance(final_usage_reading, dict)
            and final_usage_reading.get("reason") == "auth_required"
        ):
            return "auth_required", None
        if usage_utils.matches_patterns(combined_text, self.auth_error_patterns):
            return "auth_required", None
        exhausted, reset = usage_utils.exhausted_bucket_reset(
            final_usage_reading, self.job.model
        )
        if exhausted:
            return "quota_exhausted", reset
        if usage_utils.matches_patterns(combined_text, self.quota_error_patterns):
            return "quota_exhausted", reset
        return None, None

    def _fresh_usage_reading(self) -> dict[str, Any]:
        """Take a fresh (uncached) `/usage` reading for cost accounting (Item 5)."""
        # use_cache=False is deliberate: the FINAL reading must not come from
        # the 60 s cache. Otherwise a job shorter than 60 s reads back the
        # launch value and computes a zero cost delta.
        return usage_utils.get_usage(
            self.agy_binary,
            home=self.account_home or self.home,
            cache=self.usage_cache,
            use_cache=False,
            auth_error_patterns=self.auth_error_patterns,
            account_name=self.account_name,
            account=self.account,
        )

    def _final_usage_cost(self, final_reading: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Compute and store the job's usage cost at finalization (Item 5)."""
        try:
            base_state = (
                Path(self.base_state_dir)
                if self.base_state_dir
                else self.job_dir.parent.parent
            )
            concurrent_jobs = (
                count_active_jobs(
                    base_state,
                    exclude_job_id=self.job.job_id,
                    account=self.account_name if self.account_name else None,
                )
                + 1
            )
            cost = usage_utils.compute_usage_cost(
                self.job.model,
                self.job.usage_at_launch,
                final_reading,
                concurrent_jobs=concurrent_jobs,
            )
            self.job.usage_cost = cost
            return cost
        except Exception:
            return None

    def _read_plan_summary(self) -> dict[str, object]:
        """Read PLAN.md through the hardened scratchpad reader (D62).

        Missing/tampered files yield an empty plan; a read failure is audited
        with a short code and never raises out of finalization.
        """
        worktree = self.job.worktree_path
        if not worktree or not Path(worktree).is_dir():
            return {"total": 0, "done": 0, "remaining": []}
        try:
            return scratchpad.read_plan(Path(worktree))
        except Exception:
            record_cleanup_error(self.job_dir, "plan_read_failed")
            return {"total": 0, "done": 0, "remaining": []}

    def _verify_passed(self) -> bool:
        """True only when the wrapper ran verify and it returned success."""
        status = self.job.verify_status or {}
        return bool(status.get("ran")) and status.get("passed") is True

    def _capture_agent_report(self) -> None:
        """Store REPORT.md wrapped/capped and flag unverified test claims (D62)."""
        worktree = self.job.worktree_path
        if not worktree or not Path(worktree).is_dir():
            self.job.agent_report = None
            return
        report = scratchpad.read_scratchpad_file(
            Path(worktree), "REPORT.md", scratchpad.MAX_REPORT_READ_BYTES
        )
        if report.text is None:
            self.job.agent_report = None
            return
        self.job.agent_report = wrap_untrusted_text(
            scratchpad.cap_text_bytes(report.text, scratchpad.MAX_REPORT_BYTES)
        )
        claims = scratchpad.report_claims_tests_passed(report.text, self.claim_phrases)
        if claims and not self._verify_passed():
            if "report_claims_unverified" not in self.job.warnings:
                self.job.warnings.append("report_claims_unverified")

    def _record_usage_before_state(self, target_state: JobState) -> None:
        """Append the usage row before a state transition becomes observable (D68/C4).

        Used by paths that persist a terminal or needs_review state outside
        _finalize_terminated_process: the watcher-driven transitions and the
        tick-error fail-closed paths. A reader that observes the new state in
        job.json therefore always finds the matching usage.jsonl row. The
        record carries the state about to be persisted (D68 rule 2).

        Idempotent per generation: ``_usage_recorded_state`` makes
        _finalize_non_running_process skip a second append for the same run.
        """
        if self._usage_recorded_state is not None:
            return
        # Parse stdout once here: a watcher-driven transition can happen with a
        # complete JSON payload already on disk, and those tokens belong to this
        # generation's usage row.
        payload = self._stdout_payload()
        if payload is not None:
            self.job.tokens = payload.tokens
        try:
            self._final_usage_cost(self._fresh_usage_reading())
        except Exception:
            pass
        try:
            from agy_mcp.usage import record_usage

            base_state = (
                Path(self.base_state_dir)
                if self.base_state_dir
                else self.job_dir.parent.parent
            )
            tokens_dict = (
                payload.tokens.to_dict()
                if payload is not None
                else (
                    self.job.tokens.to_dict() if hasattr(self.job.tokens, "to_dict") else {}
                )
            )
            record_usage(
                usage_path=base_state / "usage.jsonl",
                job=self.job,
                duration_seconds=self._current_run_seconds(),
                tokens=tokens_dict,
                usage_cost=self.job.usage_cost,
                state=target_state,
                account=self.account_name,
                masked_email=self.account_masked_email,
            )
            self._usage_recorded_state = target_state
        except Exception:
            pass

    def _finalize_non_running_process(self, current_time: float) -> JobState:
        """Finalize when job state has left RUNNING while process group may still be alive."""
        # 1. Checkpoint-commit safe dirty work (suppressed on fatal audit violation)
        if not self.is_audit_violated and not getattr(self, "audit_violation_reason", None):
            self._try_pre_kill_checkpoint()

        # 2. Parse this generation's stdout JSON exactly once. Conversation id,
        # tokens and agy's diagnostic duration come from the same payload; the
        # id merge must never re-read the whole file (F3/F4).
        payload = self._stdout_payload()
        parsed_cid = payload.conversation_id if payload is not None else None
        generation_log = self._generation_cli_log_text()
        cid = self._apply_conversation_id(parsed_cid)
        self._update_conversation_continued(parsed_cid, generation_log)
        if cid:
            save_job(self.job, self.base_state_dir)
        elif self.job.conversation_id is not None:
            save_job(self.job, self.base_state_dir)

        # 3. Kill the process group (SIGTERM then SIGKILL) if still alive
        if self.runner.poll(self.handle) is None:
            self.runner.kill(self.handle, grace_seconds=self.grace_kill_seconds)

        # 3b. Per-run record uses this run's wrapper wall-clock, never agy's
        # conversation-age duration. Tokens come from the payload parsed above;
        # with no payload this generation has none, so the previous run's
        # numbers are never attributed to it.
        if payload is not None:
            self.job.tokens = payload.tokens
            self._compute_quota_estimate()
        else:
            self.job.tokens = TokenUsage()
        run_seconds = self._current_run_seconds(current_time)
        outcome_str = (
            self.job.state.value
            if isinstance(self.job.state, JobState)
            else str(self.job.state)
        )
        self._record_run(
            run_seconds,
            payload.agy_duration_seconds if payload is not None else None,
            payload.tokens.to_dict() if payload is not None else None,
            outcome=outcome_str,
        )
        save_job(self.job, self.base_state_dir)

        # 4. Usage row (D68/C4). When the transition that brought us here went
        # through _record_usage_before_state the row already exists and must
        # not be appended twice; otherwise append it now (cancel and other
        # external transitions) with the same parsed tokens.
        if self._usage_recorded_state is None:
            try:
                self._final_usage_cost(self._fresh_usage_reading())
            except Exception:
                pass
            try:
                from agy_mcp.usage import record_usage

                base_state = (
                    Path(self.base_state_dir)
                    if self.base_state_dir
                    else self.job_dir.parent.parent
                )
                usage_path = base_state / "usage.jsonl"
                tokens_dict = payload.tokens.to_dict() if payload is not None else {}
                record_usage(
                    usage_path=usage_path,
                    job=self.job,
                    duration_seconds=run_seconds,
                    tokens=tokens_dict,
                    usage_cost=self.job.usage_cost,
                    account=self.account_name,
                    masked_email=self.account_masked_email,
                )
            except Exception:
                pass

        # 5. Clean up handles
        self.runner.cleanup(self.handle)

        # 5. Write audit event
        append_audit_event(
            self.job_dir,
            event="process_terminated",
            detail={
                "state": self.job.state.value,
                "review_reason": (
                    self.job.review_reason
                    or getattr(self, "audit_violation_reason", None)
                ),
                "pgid": getattr(self.handle, "pgid", None),
            },
        )

        return self.job.state

    def _try_pre_kill_checkpoint(self) -> None:
        """Execute checkpoint commit before killing process group if dirty."""
        if not self.job.worktree_path:
            return
        wt = self.job.worktree_path.resolve()
        with self.commit_lock:
            if wt.is_dir() and is_worktree_dirty(wt):
                try:
                    findings = audit(self.job, self.baseline_snapshot, home=self.home)
                    if not findings.has_fatal:
                        sha = checkpoint_commit(
                            wt,
                            is_process_alive_func=lambda: self.runner.poll(self.handle) is None,
                            job=self.job,
                        )
                        if sha:
                            self.job.commits_count += 1
                            self.job.head_commit = sha
                            save_job(self.job, self.base_state_dir)
                except GitError as exc:
                    hook_output = (exc.stderr or exc.stdout or str(exc)).strip()
                    hook_line = (
                        hook_output.splitlines()[0][:300]
                        if hook_output
                        else "git commit failed"
                    )
                    cause = f"pre-kill checkpoint commit was rejected: {hook_line}"
                    self.job.error = {
                        "code": "invalid_commit_message",
                        "message": (
                            f"Cause: {cause} Next: fix the repository commit hook or its "
                            "message rule, then resume the job; dirty work is still on disk"
                        ),
                        "cause": cause,
                        "next_action": (
                            "fix the repository commit hook or its message rule, then "
                            "resume the job; dirty work is still on disk"
                        ),
                    }
                    append_audit_event(
                        self.job_dir,
                        event="pre_kill_checkpoint_rejected",
                        detail={"stderr": hook_output[-1000:], "cause": cause},
                    )
                    record_cleanup_error(self.job_dir, "pre_kill_checkpoint_failed")
                except Exception:
                    record_cleanup_error(self.job_dir, "pre_kill_checkpoint_failed")

    def tick(self, now: Optional[float] = None) -> Optional[JobState]:
        """Execute a single supervision tick. Returns JobState if terminal, None if running."""
        current_time = time.time() if now is None else now

        if self.job.state != JobState.RUNNING:
            return self._finalize_non_running_process(current_time)

        # Persist the conversation id as soon as the CLI logs it, before any
        # kill/cancel can race the final stdout JSON.
        self._persist_early_conversation_id()

        # 1. Wall-clock deadline check
        elapsed = current_time - self.started_at
        if elapsed >= self.deadline_seconds and not self.supervisor_timed_out:
            self.supervisor_timed_out = True
            append_audit_event(
                self.job_dir,
                event="deadline_expired",
                detail={"elapsed_seconds": elapsed, "deadline_seconds": self.deadline_seconds},
            )
            self._try_pre_kill_checkpoint()
            self.runner.kill(self.handle, grace_seconds=self.grace_kill_seconds)

        # 2. Periodic audit timer
        if (current_time - self.last_audit_time) >= self.audit_interval_seconds:
            self.last_audit_time = current_time
            if self.baseline_snapshot is None:
                self.is_audit_violated = True
                self.audit_violation_reason = "audit_violation"
                append_audit_event(
                    self.job_dir,
                    event="audit_violation",
                    detail={"reason": self.audit_violation_reason},
                )
                self.runner.kill(self.handle, grace_seconds=self.grace_kill_seconds)
            else:
                findings = audit(self.job, self.baseline_snapshot, home=self.home)
                self._process_audit_findings(findings)
                if findings.has_fatal:
                    self.is_audit_violated = True
                    self.audit_violation_reason = findings.fatal_reason or "audit_violation"
                    append_audit_event(
                        self.job_dir,
                        event="audit_violation",
                        detail={"reason": self.audit_violation_reason},
                    )
                    # Fatal violation: kill immediately, suppress pending commits
                    self.runner.kill(self.handle, grace_seconds=self.grace_kill_seconds)

        # 3. Watcher tick
        self.watcher.tick(now=current_time)

        # Check if watcher changed the state away from RUNNING
        if self.job.state != JobState.RUNNING:
            return self._finalize_non_running_process(current_time)

        # 4. Process liveness check
        rc = self.runner.poll(self.handle)
        if rc is None:
            # Process is still executing
            return None

        # 5. Process has terminated: finalize outcome
        return self._finalize_terminated_process(rc, current_time)

    def _process_audit_findings(self, findings: Any) -> None:
        """Process warnings and benign events from audit findings."""
        from agy_mcp.audit import _hash_or_missing

        for w in getattr(findings, "warnings", []):
            if w not in self.job.warnings:
                self.job.warnings.append(w)

        for ev in getattr(findings, "events", []):
            ev_key = (ev["event"], ev["detail"]["path"], ev["detail"].get("added_bytes"))
            if ev_key not in self._recorded_audit_events:
                self._recorded_audit_events.add(ev_key)
                append_audit_event(
                    self.job_dir,
                    event=ev["event"],
                    detail=ev["detail"],
                )
            p_str = ev["detail"]["path"]
            curr_h = _hash_or_missing(Path(p_str))
            if self.baseline_snapshot and self.baseline_snapshot.host_paths is not None:
                self.baseline_snapshot.host_paths[p_str] = curr_h

    def _finalize_terminated_process(self, return_code: int, current_time: float) -> JobState:
        """Parse streams, run post-completion audit, execute safety-net commit, and transition."""
        stdout_path = self.job_dir / "stdout.txt"
        stderr_path = self.job_dir / "stderr.txt"

        from agy_mcp.util import read_file_tail

        max_stream_cap = 10 * 1024 * 1024  # 10 MB tail cap per F16
        # Deliberately large: the final JSON result can be megabytes, and the
        # envelope caps are applied later (D58 item 3). Both streams are appended
        # across resumed generations, so read only this generation's bytes.
        stdout_offset = int(getattr(self.job, "stdout_offset", 0) or 0)
        if stdout_offset > 0:
            stdout_content = read_file_since(stdout_path, stdout_offset, max_stream_cap)
            stderr_content = read_file_since(stderr_path, stdout_offset, max_stream_cap)
        else:
            stdout_content = read_file_tail(stdout_path, max_stream_cap)
            stderr_content = read_file_tail(stderr_path, max_stream_cap)

        # Check cli.log denials before parsing
        cli_log_path = self.job_dir / "cli.log"
        cli_log_text = read_cli_log_tail(cli_log_path)
        extracted_denials = extract_denials_from_cli_log(cli_log_text)
        has_cli_denials = len(extracted_denials) > 0
        # The real CLI may silently resolve the requested alias (finding 2);
        # record what actually ran alongside the requested model.
        resolved_model = extract_model_resolved(cli_log_text)
        if resolved_model:
            self.job.model_resolved = resolved_model

        # Determine files modified count
        files_modified_count = 0
        wt = self.job.worktree_path.resolve() if self.job.worktree_path else None
        if wt and wt.is_dir():
            if self.job.commits_count > 0:
                files_modified_count = (
                    len(get_files_modified(wt, self.job.base_commit)) or self.job.commits_count
                )
            elif is_worktree_dirty(wt):
                files_modified_count = 1

        # Parse agy output per SPEC section 15
        parsed = parse_agy_output(
            stdout_content=stdout_content,
            stderr_content=stderr_content,
            return_code=return_code,
            is_cancelled=self.is_cancelled,
            is_audit_violated=self.is_audit_violated,
            supervisor_timed_out=self.supervisor_timed_out,
            commits_count=self.job.commits_count,
            files_modified_count=files_modified_count,
            has_cli_denials=has_cli_denials,
        )

        # Fresh finalization reading, reused for cost and quota/auth detection.
        try:
            final_usage_reading = self._fresh_usage_reading()
        except Exception:
            final_usage_reading = {
                "available": False,
                "reason": "unavailable",
                "groups": [],
            }

        # Post-completion audit check
        final_state = parsed.state
        detail: dict[str, object] = {
            "return_code": return_code,
        }

        if parsed.review_reason:
            detail["review_reason"] = parsed.review_reason
        elif self.audit_violation_reason:
            detail["review_reason"] = self.audit_violation_reason

        if parsed.decision_prompt:
            detail["decision_prompt"] = parsed.decision_prompt

        if final_state == JobState.FAILED:
            tail_str = parsed.raw_tail or parsed.error_message or ""
            capped_tail = truncate_tail(tail_str, 2048)
            redacted_tail = redact_log_text(capped_tail)
            wrapped_tail = wrap_untrusted_text(redacted_tail) if redacted_tail else None
            err_dict: dict[str, Any] = {
                "reason": parsed.failure_reason or "process_failed",
                "message": wrapped_tail or parsed.error_message or "Process failed",
            }
            if parsed.signal is not None:
                err_dict["signal"] = parsed.signal
            if wrapped_tail:
                err_dict["tail"] = wrapped_tail
            self.job.error = err_dict
            detail["error"] = err_dict

        if self.baseline_snapshot is None:
            post_findings = Findings(
                items=[
                    Finding(
                        severity=AuditSeverity.FATAL,
                        reason="audit_violation",
                        path="snapshot.json",
                        detail="Missing or corrupted audit baseline snapshot",
                    )
                ]
            )
        else:
            post_findings = audit(self.job, self.baseline_snapshot, home=self.home)
        self._process_audit_findings(post_findings)
        if post_findings.has_fatal:
            final_state = JobState.NEEDS_REVIEW
            detail["review_reason"] = post_findings.fatal_reason or "audit_violation"
        elif any(f.reason == "policy_file_modified" for f in post_findings.items):
            final_state = JobState.NEEDS_REVIEW
            detail["review_reason"] = "policy_file_modified"
            detail["partial_commit"] = True

        # Check verification if verify command configured per SPEC 13 & D54
        has_post_verify_fatal = False
        if final_state == JobState.DONE and not post_findings.has_fatal and self.job.verify:
            wt = self.job.worktree_path.resolve() if self.job.worktree_path else None
            if wt and wt.is_dir():
                from agy_mcp.verify import (
                    DEFAULT_VERIFY_TIMEOUT_SECONDS,
                    VerifyResult,
                    build_verify_excerpt,
                    diagnose_verify_output,
                    run_verify,
                )

                cmd_tokens = shlex.split(self.job.verify.strip())
                verify_timeout = int(
                    getattr(self.job, "verify_timeout_seconds", None)
                    or DEFAULT_VERIFY_TIMEOUT_SECONDS
                )
                append_audit_event(
                    self.job_dir,
                    event="verify_started",
                    detail={
                        "command": cmd_tokens,
                        "command_str": self.job.verify,
                        "timeout_seconds": verify_timeout,
                        "cwd": wt.name,
                    },
                )
                t_v0 = time.time()
                v_result = run_verify(
                    self.job.verify,
                    wt,
                    timeout_seconds=verify_timeout,
                    home=self.home,
                    on_proc_spawn=lambda p: setattr(self, "_verify_proc", p),
                )
                self._verify_proc = None
                if self.is_cancelled:
                    final_state = JobState.CANCELLED
                # Old callers/tests may return a plain (rc, tail) tuple; accept
                # both shapes so the status/metadata additions are backward
                # compatible.
                if isinstance(v_result, VerifyResult):
                    v_rc, v_tail = v_result.rc, v_result.output
                    v_timed_out = v_result.timed_out
                    v_killed_by_timeout = v_result.killed_by_timeout
                    v_group_alive = v_result.group_alive_after_kill
                    v_dur = v_result.duration_seconds
                    v_status = v_result.status
                else:
                    v_rc, v_tail = v_result[0], v_result[1]
                    v_timed_out = v_rc == 124
                    v_killed_by_timeout = v_timed_out
                    v_group_alive = False
                    v_dur = round(time.time() - t_v0, 3)
                    v_status = "timeout" if v_timed_out else ("passed" if v_rc == 0 else "failed")
                    v_result = VerifyResult(
                        v_rc,
                        v_tail,
                        command=self.job.verify,
                        timeout_seconds=verify_timeout,
                        timed_out=v_timed_out,
                        killed_by_timeout=v_killed_by_timeout,
                        duration_seconds=v_dur,
                    )
                v_passed = (v_rc == 0)
                v_tail_bytes = len(v_tail.encode("utf-8"))
                curr_att = (
                    self.job.verify_status.get("attempts", 0) if self.job.verify_status else 0
                ) + 1

                append_audit_event(
                    self.job_dir,
                    event="verify_finished",
                    detail={
                        "rc": v_rc,
                        "duration_seconds": v_dur,
                        "status": v_status,
                        "timed_out": v_timed_out,
                        "killed_by_timeout": v_killed_by_timeout,
                        "attempt": curr_att,
                        "tail_bytes": v_tail_bytes,
                        "passed": v_passed,
                        "command_str": self.job.verify,
                        "timeout_seconds": verify_timeout,
                    },
                )

                excerpt = build_verify_excerpt(v_result)
                capped_tail = truncate_tail(excerpt, 8192)
                wrapped_tail = wrap_untrusted_text(capped_tail) if capped_tail else None

                cause: Optional[str] = None
                next_action: Optional[str] = None
                if v_timed_out:
                    cause = (
                        f"verify command timed out after {verify_timeout}s "
                        f"(process group killed={v_killed_by_timeout}): {self.job.verify}"
                    )
                    next_action = (
                        "inspect the last 40 lines for the hang; fix it or raise "
                        "verify_timeout_seconds for this job, then resume with agy_resume"
                    )
                elif v_rc != 0:
                    cause, next_action = diagnose_verify_output(v_tail)
                    if not cause:
                        cause = (
                            f"verify command failed by itself with rc={v_rc} "
                            f"(not a timeout): {self.job.verify}"
                        )
                        next_action = (
                            "fix the failing code/tests shown in the last 40 lines, "
                            "then resume with agy_resume"
                        )

                self.job.verify_status = {
                    # Existing fields (unchanged shape).
                    "requested": True,
                    "ran": True,
                    "passed": v_passed,
                    "rc": v_rc,
                    "duration_seconds": v_dur,
                    "attempts": curr_att,
                    "last_tail": wrapped_tail,
                    # Additive diagnostics (D80).
                    "status": v_status,
                    "timed_out": v_timed_out,
                    "killed_by_timeout": v_killed_by_timeout,
                    "group_alive_after_kill": v_group_alive,
                    "command": self.job.verify,
                    "timeout_seconds": verify_timeout,
                    "cause": cause,
                    "next_action": next_action,
                }

                if v_rc != 0:
                    final_state = JobState.NEEDS_REVIEW
                    detail["review_reason"] = (
                        "verify_timeout" if v_timed_out else "verify_failed"
                    )
                    detail["verify_tail"] = redact_log_text(truncate_tail(excerpt, 2048))
                    self.job.error = {
                        "code": detail["review_reason"],
                        "message": f"Cause: {cause} Next: {next_action}",
                        "cause": cause,
                        "next_action": next_action,
                    }
                    try:
                        (self.job_dir / "verify_tail.txt").write_text(
                            excerpt, encoding="utf-8"
                        )
                    except Exception:
                        pass

                # Post-verify security audit gate per SPEC section 13
                post_verify_findings = audit(self.job, self.baseline_snapshot, home=self.home)
                self._process_audit_findings(post_verify_findings)
                if post_verify_findings.has_fatal:
                    has_post_verify_fatal = True
                    final_state = JobState.NEEDS_REVIEW
                    detail["review_reason"] = post_verify_findings.fatal_reason or "audit_violation"
                    detail.pop("verify_tail", None)
                    self.job.error = None
                elif any(f.reason == "policy_file_modified" for f in post_verify_findings.items):
                    has_post_verify_fatal = True
                    final_state = JobState.NEEDS_REVIEW
                    detail["review_reason"] = "policy_file_modified"
                    detail["partial_commit"] = True
                    detail.pop("verify_tail", None)
                    self.job.error = None
        elif self.job.verify and (
            not self.job.verify_status or not self.job.verify_status.get("ran")
        ):
            curr_att = (
                self.job.verify_status.get("attempts", 0) if self.job.verify_status else 0
            )
            reason_str = detail.get("review_reason") or final_state.value
            self.job.verify_status = {
                "requested": True,
                "ran": False,
                "passed": None,
                "rc": None,
                "duration_seconds": None,
                "attempts": curr_att,
                "last_tail": None,
                "reason": reason_str,
                "status": "not_run",
                "timed_out": False,
                "killed_by_timeout": False,
                "command": self.job.verify,
                "timeout_seconds": getattr(
                    self.job, "verify_timeout_seconds", None
                ),
                "cause": None,
                "next_action": None,
            }

        # Check safety-net commit on normal completion (SPEC section 11 & D54)
        # Ordering matters: verify and both audits must pass before a safety-net
        # commit could capture files the audit would have rejected.
        can_commit = not post_findings.has_fatal and not has_post_verify_fatal
        if final_state == JobState.DONE and can_commit:
            wt = self.job.worktree_path.resolve() if self.job.worktree_path else None
            if wt and wt.is_dir():
                with self.commit_lock:
                    try:
                        new_sha = safety_net_commit(
                            worktree_path=wt,
                            title=self.job.title,
                            audit_passed=True,
                            job_dir=self.job_dir,
                            job=self.job,
                        )
                        if new_sha:
                            self.job.head_commit = new_sha
                            self.job.commits_count += 1
                    except GitError as exc:
                        hook_output = (exc.stderr or exc.stdout or str(exc)).strip()
                        hook_line = (
                            hook_output.splitlines()[0][:300]
                            if hook_output
                            else "git commit failed"
                        )
                        cause = f"final safety-net commit was rejected: {hook_line}"
                        next_action = (
                            "fix the repository commit hook or its message rule, then "
                            "resume the job; the completed work is still uncommitted on disk"
                        )
                        self.job.error = {
                            "code": "invalid_commit_message",
                            "message": f"Cause: {cause} Next: {next_action}",
                            "cause": cause,
                            "next_action": next_action,
                        }
                        append_audit_event(
                            self.job_dir,
                            event="safety_net_commit_rejected",
                            detail={"stderr": hook_output[-1000:], "cause": cause},
                        )
                        if final_state == JobState.DONE:
                            final_state = JobState.NEEDS_REVIEW
                            detail["review_reason"] = "invalid_commit_message"
                            detail["cause"] = cause
                            detail["next_action"] = next_action
                        record_cleanup_error(self.job_dir, "safety_net_commit_failed")
                    except Exception:
                        record_cleanup_error(self.job_dir, "safety_net_commit_failed")

        # Update job telemetry
        self.job.tokens = parsed.tokens
        self.job.denials = parsed.denials
        # Strict merge: a valid stored UUID wins over a different observed one
        # (audited as conversation_id_mismatch); a stored non-UUID is replaced.
        # An early-scan-only id yields to stdout's real UUID (F1).
        self._apply_conversation_id(parsed.conversation_id)
        self._update_conversation_continued(
            parsed.conversation_id, self._generation_cli_log_text()
        )
        run_seconds = self._current_run_seconds(current_time)
        self._record_run(
            run_seconds,
            parsed.agy_duration_seconds,
            parsed.tokens.to_dict() if hasattr(parsed.tokens, "to_dict") else None,
            outcome=(
                final_state.value
                if isinstance(final_state, JobState)
                else str(final_state)
            ),
        )
        self._compute_quota_estimate()

        # Determine effective agy_home
        effective_agy_home = self.agy_home

        denied_tools: list[dict[str, Any]] = []
        denied_commands_map: dict[int, dict[str, Any]] = {}

        for d in extracted_denials:
            subagent_id = getattr(d, "subagent_id", None)
            kind = "subagent" if subagent_id else _classify_tool_kind(d.tool)
            tool_item: dict[str, Any] = {
                "step": d.step,
                "tool": d.tool,
                "kind": kind,
            }
            if subagent_id:
                tool_item["id"] = subagent_id

            cmd_text: Optional[str] = None
            target_cid = subagent_id or self.job.conversation_id
            if target_cid and effective_agy_home:
                try:
                    calls = read_denied_calls(
                        agy_home=effective_agy_home,
                        conversation_id=target_cid,
                        steps=[d.step],
                        job_dir=self.job_dir,
                    )
                    if calls:
                        first_c = calls[0]
                        if first_c.command:
                            cmd_text = first_c.command
                        if first_c.path:
                            tool_item["path"] = first_c.path
                except Exception:
                    pass

            denied_tools.append(tool_item)
            cmd_wrapped = (
                wrap_untrusted_text(truncate_tail(cmd_text, 500)) if cmd_text else None
            )
            raw_line_capped = truncate_tail(d.raw_line, 500) if d.raw_line else ""
            denied_commands_map[d.step] = {
                "step": d.step,
                "tool": d.tool,
                "command": cmd_wrapped,
                "raw_line": raw_line_capped,
            }

        if denied_commands_map:
            self.job.denied_commands = list(denied_commands_map.values())[:20]
        else:
            self.job.denied_commands = format_denied_commands(extracted_denials)

        self.job.denied_tools = denied_tools[:20]
        for w in parsed.warnings:
            if w not in self.job.warnings:
                self.job.warnings.append(w)

        # Update files_modified vs base_commit
        wt = self.job.worktree_path.resolve() if self.job.worktree_path else None
        if wt and wt.is_dir() and self.job.base_commit:
            self.job.files_modified = get_files_modified(
                wt, self.job.base_commit, self.job.head_commit or "HEAD", cap=200
            )

        # C1 rule: No false success when denied without progress (D49)
        # A zero-commit, zero-file SUCCESS plus any denial is not success; it is
        # review material (and the manager may auto-resume it once).
        has_any_denials = bool(self.job.denials) or bool(extracted_denials)
        if final_state == JobState.DONE:
            if self.job.commits_count == 0 and len(self.job.files_modified) == 0:
                if has_any_denials:
                    final_state = JobState.NEEDS_REVIEW
                    self.job.review_reason = "denied_without_progress"
                    detail["review_reason"] = "denied_without_progress"
                else:
                    if "no_commits" not in self.job.warnings:
                        self.job.warnings.append("no_commits")
            else:
                if has_any_denials and "denied_actions" not in self.job.warnings:
                    self.job.warnings.append("denied_actions")

        # Interruption detection (D59, Items 6-7): failed, timeout, or empty response.
        response_empty = not (parsed.response_text or "").strip()
        if (
            (final_state in (JobState.FAILED, JobState.TIMEOUT) or response_empty)
            and not self.is_cancelled
            and not post_findings.has_fatal
            and not has_post_verify_fatal
        ):
            interrupt_reason, resume_after = self._detect_interruption_reason(
                stderr_content, cli_log_text, final_usage_reading
            )
            if interrupt_reason:
                final_state = JobState.NEEDS_REVIEW
                detail["review_reason"] = interrupt_reason
                self.job.resume_after = resume_after
                if resume_after:
                    detail["resume_after"] = resume_after

        # Plan accounting (D62). Only a job that would otherwise be DONE is
        # turned into needs_review(plan_incomplete); any other final state keeps
        # its own reason and merely gains the warning.
        plan_summary = self._read_plan_summary()
        self.job.plan_summary = plan_summary
        if (
            not self.is_cancelled
            and int(plan_summary.get("total", 0) or 0) > 0
            and plan_summary.get("remaining")
        ):
            if "plan_incomplete" not in self.job.warnings:
                self.job.warnings.append("plan_incomplete")
            if final_state == JobState.DONE and self.plan_nudge:
                final_state = JobState.NEEDS_REVIEW
                detail["review_reason"] = "plan_incomplete"

        # Audit status is wrapper-observed; facts use it, never agent text.
        audit_failed = bool(post_findings.has_fatal or has_post_verify_fatal)
        self.job.audit_status = "failed" if audit_failed else "passed"
        self.job.audit_reason = detail.get("review_reason") if audit_failed else None

        # Agent report (D62): untrusted, capped to 4 KB in the envelope, and
        # compared against verify so a false "tests passed" claim is visible.
        self._capture_agent_report()

        # Per-job usage cost (D59, Item 5), persisted by the state transition below.
        try:
            self._final_usage_cost(final_usage_reading)
        except Exception:
            pass

        # State transition target. The transition itself is applied after the
        # usage record is appended (D68).
        if self.is_cancelled:
            final_state = JobState.CANCELLED
            detail = {"return_code": return_code, "cancelled": True}
            event_trigger = "Job cancelled by agy_cancel"
        else:
            event_trigger = f"Process terminated with rc {return_code}"

        outcome_str = (
            final_state.value
            if isinstance(final_state, JobState)
            else str(final_state)
        )
        if self.job.runs:
            self.job.runs[-1]["outcome"] = outcome_str

        # Accumulate execution time before the terminal state is persisted, so
        # job.json and the usage record carry the same cumulative_minutes.
        elapsed_minutes = (current_time - self.started_at) / 60.0
        record_job_execution_time(
            self.job,
            elapsed_minutes=elapsed_minutes,
            max_minutes_cap=self.max_minutes_cap,
            base_state_dir=self.base_state_dir,
        )

        # D68: append usage.jsonl BEFORE the terminal state becomes observable.
        # A reader that sees state==done in job.json therefore always finds the
        # record; the old order left a ~5 ms window where it did not exist.
        try:
            from agy_mcp.usage import record_usage

            base_state = (
                Path(self.base_state_dir)
                if self.base_state_dir
                else self.job_dir.parent.parent
            )
            usage_path = base_state / "usage.jsonl"
            tokens_dict = (
                self.job.tokens.to_dict() if hasattr(self.job.tokens, "to_dict") else {}
            )
            record_usage(
                usage_path=usage_path,
                job=self.job,
                duration_seconds=run_seconds,
                tokens=tokens_dict,
                usage_cost=self.job.usage_cost,
                state=final_state,
                account=self.account_name,
                masked_email=self.account_masked_email,
            )
            self._usage_recorded_state = final_state
        except Exception:
            pass

        transition_job_state(
            job=self.job,
            target_state=final_state,
            event_trigger=event_trigger,
            detail=detail,
            base_state_dir=self.base_state_dir,
        )

        if self.on_state_change:
            try:
                self.on_state_change(self.job)
            except Exception:
                pass

        self.runner.cleanup(self.handle)
        return self.job.state

    def stop(self) -> None:
        """Signal supervisor loop to exit cleanly without modifying job state."""
        self._stopped = True

    def run_loop(self) -> None:
        """Execute supervision loop until process group terminates or deadline fires."""
        consecutive_errors = 0
        while self.job.state == JobState.RUNNING and not self._stopped:
            try:
                state = self.tick(time.time())
                consecutive_errors = 0
                if state is not None:
                    break
            except Exception as exc:
                consecutive_errors += 1
                # Fail closed on repeated tick failures: one more audit attempt,
                # then kill and transition to needs_review rather than leaving a
                # live agy behind an unmonitored job (D57 item 1).
                if self.baseline_snapshot is not None:
                    try:
                        audit_check = audit(self.job, self.baseline_snapshot, home=self.home)
                        if audit_check.has_fatal:
                            self.is_audit_violated = True
                            self.audit_violation_reason = (
                                audit_check.fatal_reason or "audit_violation"
                            )
                            if self.job.state == JobState.RUNNING:
                                self._record_usage_before_state(JobState.NEEDS_REVIEW)
                                transition_job_state(
                                    job=self.job,
                                    target_state=JobState.NEEDS_REVIEW,
                                    event_trigger="Audit violation detected during supervision",
                                    detail={"review_reason": self.audit_violation_reason},
                                    base_state_dir=self.base_state_dir,
                                )
                            if self.runner.poll(self.handle) is None:
                                self.runner.kill(self.handle, grace_seconds=self.grace_kill_seconds)
                            self.runner.cleanup(self.handle)
                            break
                    except Exception as audit_exc:
                        msg = redact_log_text(truncate_tail(str(audit_exc), 500))
                        append_audit_event(
                            self.job_dir,
                            event="audit_error_during_tick_error",
                            detail={
                                "exception_class": type(audit_exc).__name__,
                                "exception_type": type(audit_exc).__name__,
                                "message": msg,
                            },
                        )
                        self.is_audit_violated = True
                        self.audit_violation_reason = "audit_unavailable"
                        if self.job.state == JobState.RUNNING:
                            self._record_usage_before_state(JobState.NEEDS_REVIEW)
                            transition_job_state(
                                job=self.job,
                                target_state=JobState.NEEDS_REVIEW,
                                event_trigger="Audit error during supervisor tick error",
                                detail={"review_reason": "audit_unavailable"},
                                base_state_dir=self.base_state_dir,
                            )
                        if self.runner.poll(self.handle) is None:
                            self.runner.kill(self.handle, grace_seconds=self.grace_kill_seconds)
                        self.runner.cleanup(self.handle)
                        break
                git_stderr = getattr(exc, "stderr", "")
                git_stderr_tail = (
                    git_stderr[-500:] if isinstance(git_stderr, str) and git_stderr else ""
                )
                append_audit_event(
                    self.job_dir,
                    event="supervisor_error",
                    detail={
                        "error": str(exc),
                        "exception_type": type(exc).__name__,
                        "message": str(exc),
                        "git_stderr": git_stderr_tail,
                        "consecutive_errors": consecutive_errors,
                    },
                )
                if consecutive_errors >= 3:
                    if self.job.state == JobState.RUNNING:
                        self._record_usage_before_state(JobState.FAILED)
                        try:
                            transition_job_state(
                                job=self.job,
                                target_state=JobState.FAILED,
                                event_trigger="Supervisor 3 consecutive tick failures",
                                detail={"review_reason": "supervisor_failure", "error": str(exc)},
                                base_state_dir=self.base_state_dir,
                            )
                        except Exception:
                            self.job.state = JobState.FAILED
                            save_job(self.job, self.base_state_dir)
                    if self.runner.poll(self.handle) is None:
                        try:
                            self.runner.kill(self.handle, grace_seconds=0.5)
                        except Exception:
                            record_cleanup_error(self.job_dir, "kill_after_tick_errors")
                    self.runner.cleanup(self.handle)
                    break
            time.sleep(self.poll_interval_seconds)

        # Ensure no path leaves a live agy with a non-running job
        if self.runner.poll(self.handle) is None and self.job.state != JobState.RUNNING:
            self._finalize_non_running_process(time.time())
