"""The orchestration facade behind every MCP tool (SPEC sections 8-16).

Owns validation, job creation, the provisioning thread, supervisor threads and
their post-processing (auto-fix, denial auto-resume), status/result/review
assembly, cancel/cleanup, startup reconciliation, shutdown and the usage guard.
It must never execute the verification or commit pipeline inline, never bypass
the state machine, and never hold the concurrency flock while waiting on an
in-memory lock.
"""

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from agy_mcp import scratchpad
from agy_mcp import usage as usage_utils
from agy_mcp.accounts.env import build_account_env
from agy_mcp.accounts.errors import AccountError
from agy_mcp.accounts.handoff import build_handoff_prompt
from agy_mcp.accounts.policy import check_policy_parity
from agy_mcp.accounts.registry import (
    AccountConfig,
    build_registry,
    validate_account_home,
)
from agy_mcp.accounts.scheduler import (
    JobRequest,
    account_eligibility,
    remaining_until_reset,
)
from agy_mcp.accounts.selection import (
    AccountSelector,
    Selection,
    decision_trace_detail,
    scheduler_config_from_config,
)
from agy_mcp.accounts.transfer import transfer_conversation
from agy_mcp.audit import (
    AuditSeverity,
    Finding,
    Findings,
    audit,
    load_snapshot,
    save_snapshot,
    take_snapshot,
)
from agy_mcp.cli_log import capture_cli_log_anchor
from agy_mcp.config import Config
from agy_mcp.gitops import (
    checkpoint_commit,
    get_ahead_behind,
    get_commits_list,
    get_files_modified,
    get_merge_base,
    run_git,
)
from agy_mcp.jobs import (
    append_audit_event,
    check_and_increment_daily_cap,
    concurrency_lock,
    count_active_jobs,
    create_job,
    get_base_state_dir,
    get_job,
    get_job_dir,
    list_jobs,
    record_cleanup_error,
    save_job,
    transition_job_state,
)
from agy_mcp.models import (
    Job,
    JobCleanupResponse,
    JobListResponse,
    JobResultResponse,
    JobReviewResponse,
    JobStatusResponse,
    StandardEnvelope,
    TokenUsage,
    build_result_response,
    build_review_response,
    build_standard_envelope,
    standard_envelope_fields,
)
from agy_mcp.recovery import get_liveness_summary
from agy_mcp.runner import (
    MAX_PROMPT_BYTES,
    LocalProcessRunner,
    PromptTooLargeError,
    Runner,
    build_argv,
)
from agy_mcp.states import JobState
from agy_mcp.supervisor import JobSupervisor
from agy_mcp.util import (
    format_fresh_prompt,
    format_resume_prompt,
    strict_uuid,
)
from agy_mcp.verify import format_fix_prompt, is_verify_allowed, verify_rejection_message
from agy_mcp.worktree import (
    allocate_branch_name,
    cleanup_worktree,
    create_worktree,
    find_drifted_worktrees,
    inspect_permission_rules,
    provision_metadata_dir,
    validate_repo_path,
)

UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

logger = logging.getLogger(__name__)


class ManagerError(Exception):
    """Exception raised by JobManager when validation or lifecycle rules fail."""

    def __init__(self, message: str, code: str = "manager_error") -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


class JobManager:
    """Coordinates job creation, background supervision, and tool response assembly."""

    def __init__(
        self,
        config: Optional[Config] = None,
        runner: Optional[Runner] = None,
        base_state_dir: Optional[Path] = None,
        home: Optional[Path] = None,
        settle_window_seconds: float = 5.0,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self.config = config or Config()
        self.state_dir = get_base_state_dir(base_state_dir)
        self.home = (home or Path.home()).resolve()
        self.runner = runner or LocalProcessRunner()
        self.settle_window_seconds = settle_window_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self._supervisors: dict[str, JobSupervisor] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._auto_fix_counts: dict[str, int] = {}
        self._denial_resume_counts: dict[str, int] = {}
        self._plan_nudge_counts: dict[str, int] = {}
        self._resuming_jobs: set[str] = set()
        self._job_locks: dict[str, threading.Lock] = {}
        self._job_commit_locks: dict[str, threading.Lock] = {}
        self._lock = threading.RLock()
        self._usage_cache = usage_utils.UsageCache()
        self.registry = build_registry(self.config, self.home)
        self._account_selector_instance: Optional[AccountSelector] = None

    def _get_account_selector(self) -> AccountSelector:
        """Return the lazily built account selector (one per manager)."""
        if self._account_selector_instance is None:
            self._account_selector_instance = AccountSelector(
                self.config,
                self.registry,
                self.home,
                str(getattr(self.runner, "executable", "agy")),
                usage_cache=self._usage_cache,
                state_dir=self.state_dir,
            )
        return self._account_selector_instance

    def _account_for_job(self, job: Job) -> AccountConfig:
        """Return the account a job is pinned to (default for legacy records)."""
        name = job.account or "default"
        try:
            return self.registry.by_name(name)
        except AccountError as exc:
            raise ManagerError(
                f"job is pinned to account '{name}' which is no longer configured",
                code="account_not_found",
            ) from exc

    def _agy_home_for(self, account: AccountConfig) -> Path:
        """Return the agy state dir for an account (config override only for default)."""
        if account.is_default:
            return Path(self.config.agy_home).expanduser()
        return account.agy_home

    def _check_account_ready(self, account: AccountConfig) -> None:
        """Fail closed when an account home or its permission policy is unusable."""
        if account.is_default:
            return
        try:
            validate_account_home(account)
        except AccountError as exc:
            raise ManagerError(exc.message, code=exc.code) from exc
        policy = check_policy_parity(account.expanded_home, self.home)
        if not policy.ok:
            code = (
                "account_permission_policy_drift"
                if policy.status == "drift"
                else "account_permission_policy_missing"
            )
            raise ManagerError(
                f"account '{account.name}' permission policy does not match the "
                f"primary login ({policy.status})",
                code=code,
            )

    def _get_job_lock(self, job_id: str) -> threading.Lock:
        """Get or create a per-job lock to serialize operations on a job."""
        with self._lock:
            if job_id not in self._job_locks:
                self._job_locks[job_id] = threading.Lock()
            return self._job_locks[job_id]

    def _get_job_commit_lock(self, job_id: str) -> threading.Lock:
        """Get or create a per-job lock to serialize git commits on a worktree (F21)."""
        with self._lock:
            if job_id not in self._job_commit_locks:
                self._job_commit_locks[job_id] = threading.Lock()
            return self._job_commit_locks[job_id]

    def _wait_for_prior_supervisor(self, job_id: str, timeout_seconds: float = 30.0) -> None:
        """Wait for the previous run's supervisor thread to finish finalizing (D65).

        A supervisor's ``_run_supervisor`` finally block reads ``job.json`` and
        then writes it back while retaining the scratchpad. If ``resume`` starts
        a new generation in that window, the stale write can re-persist the
        previous ``needs_review`` state over the new ``running`` state and the
        old thread unregisters the new supervisor. Joining the previous thread
        here (without holding the job lock, so an in-thread auto-resume can
        still take it) removes the overlap. A thread that does not finish fails
        closed rather than racing.
        """
        current = threading.current_thread()
        with self._lock:
            thread = self._threads.get(job_id)
        if thread is None or thread is current:
            return
        thread.join(timeout_seconds)
        if thread.is_alive():
            raise ManagerError(
                "The previous supervisor for this job is still finalizing; retry resume",
                code="job_finalizing",
            )

    def _validate_job_id(self, job_id: str) -> None:
        if not isinstance(job_id, str) or not UUID_PATTERN.match(job_id):
            raise ManagerError(f"Invalid job_id format '{job_id}'", code="invalid_job_id")

    def _get_elapsed(self, job: Job) -> float:
        if not job.created_at:
            return 0.0
        try:
            created = datetime.fromisoformat(job.created_at)
            now = datetime.now(timezone.utc)
            return max(0.0, (now - created).total_seconds())
        except Exception:
            return 0.0

    def _validate_checklist(self, checklist: Optional[list[str]]) -> Optional[list[str]]:
        """Validate the optional PLAN.md checklist (SPEC section 8, D62).

        Counts and per-item lengths are rejected before a job record exists, so
        a bad call cannot leave a half-created job behind.
        """
        if checklist is None:
            return None
        if not isinstance(checklist, list) or not all(isinstance(item, str) for item in checklist):
            raise ManagerError("checklist must be a list of strings", code="invalid_checklist")
        if len(checklist) > scratchpad.MAX_CHECKLIST_ITEMS:
            raise ManagerError(
                f"checklist has {len(checklist)} items; maximum is "
                f"{scratchpad.MAX_CHECKLIST_ITEMS}",
                code="checklist_too_long",
            )
        for item in checklist:
            if len(item) > scratchpad.MAX_CHECKLIST_ITEM_CHARS:
                raise ManagerError(
                    f"checklist item exceeds {scratchpad.MAX_CHECKLIST_ITEM_CHARS} characters",
                    code="checklist_item_too_long",
                )
        return checklist

    def start(
        self,
        prompt: str,
        repo_path: str,
        title: str,
        model: str = "gemini-3.8-flash-medium",
        max_minutes: int = 240,
        verify: Optional[str] = None,
        checklist: Optional[list[str]] = None,
        verify_timeout_seconds: Optional[int] = None,
        account: Optional[str] = None,
        priority: str = "normal",
        force: bool = False,
    ) -> StandardEnvelope:
        """Start a new autonomous job per SPEC sections 8, 9, 10.

        With no ``[accounts]`` registry the implicit default account is used and
        the pre-multi-account path is unchanged. With a registry the scheduler
        picks the account; ``account`` overrides it and ``force`` skips only the
        quota filter (never identity, policy or cap) (D102, D108, D110).
        """
        # 0. Prompt non-empty check
        if not prompt or not prompt.strip():
            raise ManagerError("Prompt must not be empty", code="missing_prompt")

        # 0a. Per-job verify timeout (D80); defaults to the configured 900 s.
        effective_verify_timeout = (
            self.config.verify_timeout_seconds
            if verify_timeout_seconds is None
            else verify_timeout_seconds
        )
        if (
            isinstance(effective_verify_timeout, bool)
            or not isinstance(effective_verify_timeout, int)
            or effective_verify_timeout < 1
            or effective_verify_timeout > 86400
        ):
            raise ManagerError(
                "verify_timeout_seconds must be an integer between 1 and 86400",
                code="invalid_verify_timeout",
            )

        # 0b. Optional PLAN.md checklist (SPEC section 8, D62)
        checklist = self._validate_checklist(checklist)

        if priority not in ("normal", "background"):
            raise ManagerError(
                "priority must be 'normal' or 'background'", code="invalid_priority"
            )

        # 1. Prompt size limit check
        if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
            raise PromptTooLargeError(
                f"Prompt size {len(prompt.encode('utf-8'))} bytes exceeds limit of "
                f"{MAX_PROMPT_BYTES} bytes"
            )

        # 1b. Wall-clock budget cap check
        if max_minutes > self.config.max_minutes_cap:
            raise ManagerError(
                f"Requested max_minutes ({max_minutes}) exceeds maximum cap "
                f"({self.config.max_minutes_cap})",
                code="max_minutes_over_cap",
            )

        # 4. Model allowlist check
        if model not in self.config.model_allowlist:
            raise ManagerError(
                f"Model '{model}' is not in model_allowlist {self.config.model_allowlist}",
                code="model_not_allowed",
            )

        # 5. Verify allowlist check
        if verify is not None and not is_verify_allowed(
            verify, self.config.verify_allowlist, repo_path=Path(repo_path).resolve(strict=False)
        ):
            raise ManagerError(
                verify_rejection_message(verify, self.config.verify_allowlist),
                code="verify_not_allowed",
            )

        # 6. Repository preflight
        resolved_repo, source_dirty, base_commit_sha = validate_repo_path(
            repo_path_str=repo_path,
            allowed_roots=self.config.allowed_workdir_roots,
            require_clean=self.config.require_clean_source,
            home=self.home,
            base_state_dir=self.state_dir,
            trust_agents_dir=self.config.trust_agents_dir,
        )

        # 7. Check dangerous permission rules per SPEC 8 & D54
        dangerous_info = inspect_permission_rules(self.home)
        warnings: list[str] = []
        if source_dirty:
            warnings.append("source_dirty=true")
        allow_rules = (
            dangerous_info.get("dangerous_allow_rules", [])
            if isinstance(dangerous_info, dict)
            else []
        )
        if allow_rules:
            # Count only: a rule body can contain arbitrary command text, so the
            # envelope never echoes it back to the orchestrator.
            warnings.append(
                f"dangerous_permission_rules_present: {len(allow_rules)} dangerous allow rule(s)"
            )
        inspector_warnings = (
            dangerous_info.get("warnings", []) if isinstance(dangerous_info, dict) else []
        )
        if "permission_rules_unreadable" in inspector_warnings:
            warnings.append("permission_rules_unreadable")

        # Check free disk space on state directory per SPEC 8, 9 & F22
        state_root = self.state_dir.resolve()
        state_root.mkdir(parents=True, exist_ok=True)
        try:
            import shutil

            usage_stat = shutil.disk_usage(state_root)
            free_gb = usage_stat.free / (1024**3)
            if free_gb < self.config.min_free_gb:
                raise ManagerError(
                    f"Insufficient disk space on state filesystem: {free_gb:.2f} GB free, "
                    f"{self.config.min_free_gb:.2f} GB required",
                    code="insufficient_disk_space",
                )
        except OSError:
            pass

        # Check max state disk usage per F22
        from agy_mcp.jobs import get_total_state_disk_usage

        current_disk_bytes = get_total_state_disk_usage(self.state_dir)
        max_bytes = self.config.max_state_disk_gb * (1024**3)
        if current_disk_bytes >= max_bytes:
            raise ManagerError(
                f"State directory disk usage ({current_disk_bytes / (1024**3):.2f} GB) "
                f"exceeds limit of {self.config.max_state_disk_gb:.2f} GB",
                code="state_disk_full",
            )

        # 7b. Account selection (D102, D108, D109). With no registry this is the
        # implicit default account and the pre-multi-account path is unchanged.
        selection: Optional[Selection] = None
        selected_account = self.registry.default
        selection_masked_email: Optional[str] = None
        reservation: Optional[dict[str, Any]] = None
        if self.registry.configured:
            try:
                selection = self._get_account_selector().select(
                    model, max_minutes, priority, account=account, force=force
                )
            except AccountError as exc:
                raise ManagerError(exc.message, code=exc.code) from exc
            if selection.account is None:
                resume_after = selection.result.resume_after or "unknown"
                raise ManagerError(
                    "No configured account can take this job within quota; "
                    f"earliest reset: {resume_after}",
                    code="no_account_eligible",
                )
            selected_account = selection.account
            selection_masked_email = selection.masked_email
            if selection.result.estimated_cost is not None:
                cost = selection.result.estimated_cost
                reservation = {
                    "five_hour_pct": cost.five_hour_pct,
                    "weekly_pct": cost.weekly_pct,
                    "estimated_minutes": cost.estimated_minutes,
                }
        elif account not in (None, "default"):
            raise ManagerError(
                "An account override requires an [accounts] registry",
                code="account_not_found",
            )

        # 7c. Usage guard (D59, Item 4): warn or block before consuming a launch slot.
        self._apply_usage_guard(model, warnings, account=selected_account)

        # 2 & 3. Concurrency limit and daily cap check under flock + job creation (F13, SPEC 18)
        # The flock spans the count, the daily-cap increment and create_job so two
        # servers cannot both observe a free slot and both start a job.
        with concurrency_lock(self.state_dir):
            active_count = count_active_jobs(self.state_dir)
            if active_count >= self.config.max_concurrent_jobs:
                raise ManagerError(
                    f"Active jobs ({active_count}) reached concurrency limit "
                    f"({self.config.max_concurrent_jobs})",
                    code="concurrency_limit_exceeded",
                )

            account_cap = (
                selected_account.max_concurrent_jobs or self.config.max_concurrent_jobs
            )
            account_active = count_active_jobs(
                self.state_dir, account=selected_account.name
            )
            if account_active >= account_cap:
                raise ManagerError(
                    f"Account '{selected_account.name}' active jobs ({account_active}) "
                    f"reached its concurrency limit ({account_cap})",
                    code="account_concurrency_limit",
                )

            if not check_and_increment_daily_cap(
                self.state_dir,
                self.config.daily_jobs_cap,
                tz_name=self.config.timezone,
                account=selected_account.name,
            ):
                raise ManagerError(
                    f"Daily job start ceiling reached ({self.config.daily_jobs_cap})",
                    code="cap_exceeded",
                )

            # 8. Create Job record (queued). The branch name is allocated and
            # persisted here, before agy_start returns, so the envelope, the
            # provisioning thread, every later tool and git use one name (A3).
            branch_name = allocate_branch_name(resolved_repo, title)
            job = create_job(
                repo_path=resolved_repo,
                title=title,
                prompt=prompt,
                model=model,
                max_minutes=max_minutes,
                verify=verify,
                verify_timeout_seconds=effective_verify_timeout,
                base_state_dir=self.state_dir,
                base_commit=base_commit_sha,
                branch=branch_name,
                account=selected_account.name if self.registry.configured else None,
                priority=priority,
                account_masked_email=selection_masked_email,
                quota_reservation=reservation,
                decision_trace=(
                    decision_trace_detail(selection.trace) if selection is not None else None
                ),
            )
            job.source_dirty = source_dirty
            job.warnings.extend(warnings)
            save_job(job, self.state_dir)
            if selection is not None:
                job_dir = get_job_dir(job.job_id, self.state_dir)
                verified_detail: dict[str, Any] = {"account": selected_account.name}
                if selection_masked_email is not None:
                    verified_detail["account_masked_email"] = selection_masked_email
                verified_detail["identity_expectation"] = bool(
                    selected_account.expected_email
                    or selected_account.expected_email_sha256
                )
                append_audit_event(job_dir, event="account_verified", detail=verified_detail)
                for rejected in selection.rejections[:5]:
                    rejected_detail: dict[str, Any] = {
                        "account": rejected.name,
                        "reason": rejected.reason,
                    }
                    if rejected.masked_email is not None:
                        rejected_detail["account_masked_email"] = rejected.masked_email
                    append_audit_event(
                        job_dir, event="account_rejected", detail=rejected_detail
                    )

        # 9. Asynchronous worktree provisioning & process launch per SPEC section 8
        prov_thread = threading.Thread(
            target=self._provision_and_launch,
            args=(job, prompt, checklist),
            daemon=True,
        )
        prov_thread.start()

        return build_standard_envelope(job, 0.0)

    def _build_handoff_prompt(
        self, job: Job, from_account: str, to_account: str
    ) -> str:
        """Build the summary handoff from wrapper facts only (D111)."""
        job_dir = get_job_dir(job.job_id, self.state_dir)
        original = ""
        prompt_file = job_dir / "prompt.txt"
        if prompt_file.is_file():
            try:
                original = prompt_file.read_text(encoding="utf-8", errors="replace")[:4000]
            except OSError:
                original = ""
        plan = job.plan_summary if isinstance(job.plan_summary, dict) else {}
        remaining = plan.get("remaining") if isinstance(plan.get("remaining"), list) else []
        total = int(plan.get("total", 0) or 0)
        done_count = int(plan.get("done", 0) or 0)
        done = [f"{done_count} of {total} plan items completed"] if total else []
        worktree = job.worktree_path
        base = worktree if worktree and worktree.is_dir() else job.repo_path
        git_log = run_git(["log", "--oneline", "-n", "20"], base, check=False).stdout
        diff_stat = run_git(
            ["diff", "--stat", f"{job.base_commit}..HEAD"], base, check=False
        ).stdout
        return build_handoff_prompt(
            title=job.title,
            original_prompt=original,
            branch=job.branch,
            worktree=str(worktree) if worktree else None,
            base_commit=job.base_commit,
            head_commit=job.head_commit,
            plan_done=done,
            plan_remaining=[str(item) for item in remaining],
            git_log=git_log,
            diff_stat=diff_stat,
            last_report=job.agent_report,
            verify=job.verify,
            from_account=from_account,
            to_account=to_account,
        )

    def _maybe_switch_account(
        self, job: Job, allow_handoff: bool
    ) -> Optional[str]:
        """Switch a quota-paused job to another account, or return a handoff prompt.

        Returns None when the job stays on its pinned account (quota recovered, or
        the pause is short and the caller did not ask). When a target exists and
        the conversation db can be copied, the job is re-pinned and the same
        conversation continues there. Otherwise the conversation id is dropped and
        the returned summary prompt resumes the work on the target (D111).
        """
        if not self.registry.configured:
            return None
        pinned = job.account or "default"
        try:
            selection = self._get_account_selector().select(
                job.model, job.max_minutes, job.priority
            )
        except AccountError:
            return None
        now = datetime.now(timezone.utc)
        request = JobRequest(job.model, job.max_minutes, job.priority)
        scheduler_config = scheduler_config_from_config(self.config)
        pinned_reason = account_eligibility(
            selection.states,
            pinned,
            request,
            now,
            scheduler_config,
            allow_burn=job.priority == "background",
        )
        if pinned_reason is None:
            return None
        target = selection.account
        if target is None or target.name == pinned:
            return None

        wait_minutes: Optional[float] = None
        if job.resume_after:
            remaining = remaining_until_reset(job.resume_after, now)
            if remaining is not None:
                wait_minutes = remaining.total_seconds() / 60.0
        if not allow_handoff and (
            wait_minutes is None
            or wait_minutes <= self.config.account_handoff_min_wait_minutes
        ):
            return None

        source_account = self._account_for_job(job)
        if job.worktree_path and job.worktree_path.is_dir():
            from agy_mcp.gitops import is_worktree_dirty

            try:
                if is_worktree_dirty(job.worktree_path):
                    checkpoint_commit(job.worktree_path, job=job)
            except Exception as exc:
                raise ManagerError(
                    "failed to checkpoint work before switching accounts",
                    code="handoff_checkpoint_failed",
                ) from exc

        handoff_prompt: Optional[str] = None
        audit_event = "handoff_summary"
        audit_detail: dict[str, Any] = {
            "from_account": source_account.name,
            "to_account": target.name,
        }
        if selection.masked_email is not None:
            audit_detail["account_masked_email"] = selection.masked_email
        conversation_id = strict_uuid(job.conversation_id)
        if conversation_id is not None:
            try:
                transfer = transfer_conversation(
                    source_account.expanded_home,
                    target.expanded_home,
                    conversation_id,
                    self.state_dir,
                )
            except AccountError as exc:
                audit_detail["fallback_reason"] = exc.code
                handoff_prompt = self._build_handoff_prompt(
                    job, source_account.name, target.name
                )
                job.conversation_id = None
            else:
                audit_event = "conversation_transferred"
                audit_detail["conversation_id"] = transfer.conversation_id
                audit_detail["size_bytes"] = transfer.size_bytes
        else:
            handoff_prompt = self._build_handoff_prompt(
                job, source_account.name, target.name
            )

        job.account = target.name
        job.account_masked_email = selection.masked_email
        if selection.result.estimated_cost is not None:
            cost = selection.result.estimated_cost
            job.quota_reservation = {
                "five_hour_pct": cost.five_hour_pct,
                "weekly_pct": cost.weekly_pct,
                "estimated_minutes": cost.estimated_minutes,
            }
        if job.transferred_from is None:
            job.transferred_from = source_account.name
        job.transferred_to = target.name
        save_job(job, self.state_dir)
        append_audit_event(
            get_job_dir(job.job_id, self.state_dir),
            event=audit_event,
            detail=audit_detail,
        )
        return handoff_prompt

    def _provision_and_launch(
        self,
        job: Job,
        prompt: str,
        checklist: Optional[list[str]] = None,
    ) -> None:
        """Background worker thread to provision worktree and spawn process per SPEC section 8."""
        job_dir = get_job_dir(job.job_id, self.state_dir)
        handle = None
        try:
            # Check if cancelled while queued before starting provisioning
            current_job = get_job(job.job_id, self.state_dir)
            if current_job.state == JobState.CANCELLED:
                return

            # 1. Provision Worktree
            wt_path, branch, head = create_worktree(
                repo_path=job.repo_path,
                job_dir=job_dir,
                title=job.title,
                config=self.config,
                base_state_dir=self.state_dir,
                branch=job.branch,
            )
            job.worktree_path = wt_path
            job.branch = branch
            job.head_commit = head

            # Re-check if cancelled while worktree was being created
            current_job = get_job(job.job_id, self.state_dir)
            if current_job.state == JobState.CANCELLED:
                try:
                    run_git(
                        ["worktree", "remove", "--force", str(wt_path)],
                        job.repo_path,
                        check=False,
                    )
                    run_git(["worktree", "prune"], job.repo_path, check=False)
                    if branch:
                        run_git(["branch", "-D", branch], job.repo_path, check=False)
                except Exception:
                    pass
                return

            # 1b. Provision <worktree>/.agy/ (0700, scratchpad seed, local ignore)
            # per SPEC section 9 / audit A4. Any failure here must fail closed
            # before agy is launched; the outer handler removes the worktree.
            provision_metadata_dir(wt_path, job.title, checklist)

            # 2. Prepare execution argv & sanitized environment
            full_prompt = format_fresh_prompt(prompt)

            stdout_path = job_dir / "stdout.txt"
            stderr_path = job_dir / "stderr.txt"
            log_file = job_dir / "cli.log"
            tmp_dir = job_dir / "tmp"

            account = self._account_for_job(job)
            env = build_account_env(account, tmp_dir)
            agy_binary = getattr(self.runner, "executable", "agy")
            effective_timeout = max(1, int(min(job.max_minutes, self.config.max_minutes_cap) * 60))

            argv = build_argv(
                agy_binary=agy_binary,
                prompt=full_prompt,
                model=job.model,
                print_timeout_seconds=effective_timeout,
                log_file=log_file,
            )

            # 3. Fresh usage reading at launch (D59, Item 5). Kept for the whole job
            # so resume turns do not overwrite the starting point.
            if job.usage_at_launch is None:
                job.usage_at_launch = self._read_usage(use_cache=False, account=account)
                save_job(job, self.state_dir)

            # 4. Launch child process. A fresh start is a new generation: clear
            # per-run tokens/quota so nothing from an earlier attempt leaks in.
            job.tokens = TokenUsage()
            job.quota_estimate = None
            job.cli_log_offset, job.cli_log_prefix_sha256 = capture_cli_log_anchor(log_file)
            # Record the stdout append offset before spawn, so no byte written by
            # this generation can be mistaken for the previous one.
            job.stdout_offset = stdout_path.stat().st_size if stdout_path.is_file() else 0
            handle = self.runner.launch(
                job_id=job.job_id,
                workdir=job.worktree_path,
                args=argv,
                env=env,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )

            job.pid = handle.pid
            job.pgid = handle.pgid
            job.start_time = handle.start_time
            job.run_generation = 1
            job.launch_wall_clock = time.time()
            transition_job_state(
                job,
                JobState.RUNNING,
                event_trigger="Worktree ready, process spawned & PID confirmed",
                base_state_dir=self.state_dir,
            )

            # 4. Create baseline snapshot and start supervisor
            snapshot = take_snapshot(job, home=self.home)
            save_snapshot(snapshot, job_dir / "snapshot.json")

            supervisor = JobSupervisor(
                job=job,
                handle=handle,
                runner=self.runner,
                job_dir=job_dir,
                max_minutes=job.max_minutes,
                max_minutes_cap=self.config.max_minutes_cap,
                baseline_snapshot=snapshot,
                home=self.home,
                base_state_dir=self.state_dir,
                poll_interval_seconds=self.poll_interval_seconds,
                settle_window_seconds=self.settle_window_seconds,
                checkpoint_interval_seconds=self.config.checkpoint_commit_minutes * 60,
                agy_home=self._agy_home_for(account),
                agy_binary=str(getattr(self.runner, "executable", "agy")),
                account_name=job.account,
                account_masked_email=job.account_masked_email,
                account_home=account.expanded_home,
                account=account,
                usage_cache=self._usage_cache,
                quota_error_patterns=self.config.quota_error_patterns,
                auth_error_patterns=self.config.auth_error_patterns,
                plan_nudge=self.config.plan_nudge,
                claim_phrases=self.config.claim_phrases,
                commit_lock=self._get_job_commit_lock(job.job_id),
            )

            with self._lock:
                self._supervisors[job.job_id] = supervisor
                t = threading.Thread(
                    target=self._run_supervisor,
                    args=(supervisor, job),
                    daemon=True,
                )
                self._threads[job.job_id] = t
                t.start()

        except Exception as exc:
            # Check if job was cancelled
            current_job = get_job(job.job_id, self.state_dir)
            if current_job.state == JobState.CANCELLED:
                if handle is not None:
                    try:
                        self.runner.kill(handle, grace_seconds=0.5)
                        self.runner.cleanup(handle)
                    except Exception:
                        record_cleanup_error(job_dir, "kill_cancelled_launch")
                return

            # Setup failure cleanup per SPEC section 9
            # CRITICAL (F4): Kill process group before removing worktree
            if handle is not None:
                try:
                    self.runner.kill(handle, grace_seconds=0.5)
                except Exception:
                    record_cleanup_error(job_dir, "kill_setup_failure")
                try:
                    self.runner.cleanup(handle)
                except Exception:
                    pass

            try:
                if job.worktree_path and job.worktree_path.is_dir():
                    run_git(
                        ["worktree", "remove", "--force", str(job.worktree_path)],
                        job.repo_path,
                        check=False,
                    )
                run_git(["worktree", "prune"], job.repo_path, check=False)
                if job.branch:
                    run_git(["branch", "-D", job.branch], job.repo_path, check=False)
            except Exception:
                pass

            job.state = JobState.FAILED
            job.review_reason = "setup_failed"
            from agy_mcp.util import redact_log_text
            job.error = {
                "reason": "setup_failed",
                "message": redact_log_text(str(exc)),
            }
            append_audit_event(
                job_dir,
                event="setup_failed",
                detail={"error": str(exc), "review_reason": "setup_failed"},
            )
            save_job(job, self.state_dir)

    def _run_supervisor(self, supervisor: JobSupervisor, job: Job) -> None:
        """Thread body: run the loop, then apply at most one resume policy.

        The ``finally`` block always runs. It retains the scratchpad BEFORE
        unregistering the supervisor, because ``wait()`` treats a still-registered
        supervisor as busy and must not return a done envelope missing the
        retained-file warnings. It then unregisters only if this exact supervisor
        is still the registered generation -- a late finalizer must never drop a
        successor's registration (D65), which would make ``wait()`` return a
        transient state.

        After finalization, at most one policy may act on a NEEDS_REVIEW job with
        budget left: verification auto-fix, denial auto-resume, or the single plan
        nudge (D62). ``_resuming_jobs`` marks the job for the whole resume so a
        concurrent wait()/status() cannot mistake the transitional state for a
        finished one.
        """
        try:
            supervisor.run_loop()
        finally:
            # Retain the scratchpad BEFORE unregistering the supervisor: wait()
            # treats "still has a supervisor" as busy, so it cannot return a
            # done envelope that is missing the retained-file warnings.
            refreshed = get_job(job.job_id, self.state_dir)
            self._retain_scratchpad(refreshed)
            with self._lock:
                # Only the generation that is still registered may unregister.
                # A late finalizer must never drop a successor's registration
                # (D65); that would make wait() return a transient state.
                if self._supervisors.get(job.job_id) is supervisor:
                    self._supervisors.pop(job.job_id, None)
                    self._threads.pop(job.job_id, None)

            # Check verification auto-fix loop
            refreshed = get_job(job.job_id, self.state_dir)
            if supervisor.is_cancelled or refreshed.state == JobState.CANCELLED:
                return
            if (
                refreshed.state == JobState.NEEDS_REVIEW
                and refreshed.review_reason in ("verification_failed", "verify_failed")
                and refreshed.auto_fix_count < self.config.auto_fix_attempts
            ):
                with self._lock:
                    self._resuming_jobs.add(job.job_id)
                try:
                    self._handle_verification_auto_fix(refreshed)
                finally:
                    with self._lock:
                        self._resuming_jobs.discard(job.job_id)
            elif (
                refreshed.state == JobState.NEEDS_REVIEW
                and refreshed.review_reason in ("blocked_by_permission", "denied_without_progress")
                and refreshed.commits_count == 0
                and (
                    refreshed.denial_resume_count
                    < self.config.auto_resume_on_denial
                )
            ):
                with self._lock:
                    self._resuming_jobs.add(job.job_id)
                try:
                    self._handle_denial_auto_resume(refreshed)
                finally:
                    with self._lock:
                        self._resuming_jobs.discard(job.job_id)
            elif (
                refreshed.state == JobState.NEEDS_REVIEW
                and refreshed.review_reason == "plan_incomplete"
                and self.config.plan_nudge
                and refreshed.plan_nudge_count < 1
                and bool((refreshed.plan_summary or {}).get("remaining"))
            ):
                with self._lock:
                    self._resuming_jobs.add(job.job_id)
                try:
                    self._handle_plan_nudge(refreshed)
                finally:
                    with self._lock:
                        self._resuming_jobs.discard(job.job_id)

    def _retain_scratchpad(self, job: Job) -> None:
        """Copy PLAN/NOTES/LOG/REPORT into ``<job_dir>/scratchpad/`` (D62).

        Runs at finalization, before the worktree can be cleaned up. Only file
        names are stored on the job and later exposed in envelopes (F26); a
        symlinked/oversized/binary source is skipped with a name-only warning.
        """
        worktree = job.worktree_path
        job_dir = get_job_dir(job.job_id, self.state_dir)
        if not worktree or not Path(worktree).is_dir():
            job.scratchpad_files = []
            save_job(job, self.state_dir)
            return
        copied, skipped = scratchpad.copy_to_jobdir(Path(worktree), job_dir)
        job.scratchpad_files = copied
        for name in skipped:
            warning = f"scratchpad_file_skipped:{name}"
            if warning not in job.warnings:
                job.warnings.append(warning)
        save_job(job, self.state_dir)

    def _handle_plan_nudge(self, job: Job) -> None:
        """Send the single trusted plan nudge, then never loop (D62).

        The counter is persisted before resume, so a crash or a failed resume
        cannot cause a second automatic nudge.
        """
        plan = job.plan_summary or {}
        remaining = [str(item) for item in (plan.get("remaining") or [])]
        if not remaining:
            return
        job.plan_nudge_count = 1
        self._plan_nudge_counts[job.job_id] = 1
        save_job(job, self.state_dir)
        job_dir = get_job_dir(job.job_id, self.state_dir)
        append_audit_event(
            job_dir,
            event="plan_nudge",
            detail={"remaining": len(remaining)},
        )
        guidance = scratchpad.format_plan_nudge(remaining)
        try:
            self.resume(job.job_id, guidance, is_trusted=True)
        except Exception:
            # Leave the job in needs_review(plan_incomplete); the counter is
            # already 1 so _run_supervisor will not try again.
            save_job(job, self.state_dir)

    def _handle_denial_auto_resume(self, job: Job) -> None:
        attempts = job.denial_resume_count
        if attempts < self.config.auto_resume_on_denial:
            attempt_num = attempts + 1
            job.denial_resume_count = attempt_num
            self._denial_resume_counts[job.job_id] = attempt_num
            save_job(job, self.state_dir)
            job_dir = get_job_dir(job.job_id, self.state_dir)
            append_audit_event(
                job_dir,
                event="denial_auto_resume",
                detail={
                    "attempt": attempt_num,
                    "max_attempts": self.config.auto_resume_on_denial,
                },
            )
            guidance = (
                "Use only your built-in file tools (view, create, edit, list, search) "
                "to read and change files. "
                "Do NOT run shell commands: they are not available here and will end your run. "
                "If you believe a shell command is truly required, do not try it. "
                "Instead stop and end your final response with the last line exactly: "
                "NEEDS_DECISION: need command `<the exact command>` | "
                "allow it, or do it another way | <your recommendation>"
            )
            try:
                self.resume(job.job_id, guidance, is_trusted=True)
            except Exception:
                job.denial_resume_count = self.config.auto_resume_on_denial
                self._denial_resume_counts[job.job_id] = self.config.auto_resume_on_denial
                save_job(job, self.state_dir)

    def _handle_verification_auto_fix(self, job: Job) -> None:
        attempts = job.auto_fix_count
        if attempts < self.config.auto_fix_attempts:
            attempt_num = attempts + 1
            job.auto_fix_count = attempt_num
            self._auto_fix_counts[job.job_id] = attempt_num
            save_job(job, self.state_dir)
            tail_output = "Verification command failed on workspace"
            job_dir = get_job_dir(job.job_id, self.state_dir)
            tail_file = job_dir / "verify_tail.txt"
            if tail_file.is_file():
                try:
                    tail_output = tail_file.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass
            else:
                audit_file = job_dir / "audit.jsonl"
                if audit_file.is_file():
                    try:
                        for line in audit_file.read_text(encoding="utf-8").splitlines():
                            if line.strip():
                                ev = json.loads(line)
                                if ev.get("detail", {}).get("verify_tail"):
                                    tail_output = str(ev["detail"]["verify_tail"])
                    except Exception:
                        pass
            fix_prompt = format_fix_prompt(tail_output, include_preamble=False)
            try:
                self.resume(job.job_id, fix_prompt, is_trusted=True)
            except Exception:
                job.auto_fix_count = self.config.auto_fix_attempts
                self._auto_fix_counts[job.job_id] = self.config.auto_fix_attempts
                save_job(job, self.state_dir)

    def _apply_usage_guard(
        self,
        model: str,
        warnings: list[str],
        account: Optional[AccountConfig] = None,
    ) -> None:
        """Apply usage_guard=off|warn|block before starting or resuming (D59, Item 4).

        Unavailable usage only warns (``usage_unavailable``) and never blocks,
        regardless of guard mode. An available reading whose model group cannot
        be determined warns ``usage_unmeasurable`` and also never blocks.
        """
        guard = str(getattr(self.config, "usage_guard", "warn") or "warn").lower()
        if guard == "off":
            return

        reading = self._read_usage(use_cache=True, account=account)
        if not reading.get("available"):
            if "usage_unavailable" not in warnings:
                warnings.append("usage_unavailable")
            return

        # An available reading that cannot be mapped to this model's group is
        # not evidence of low quota. Warn that it is unmeasurable and never
        # block, in every mode except off (which returned above).
        if usage_utils.find_group(reading, model) is None:
            if "usage_unmeasurable" not in warnings:
                warnings.append("usage_unmeasurable")
            return

        low = usage_utils.low_buckets(
            reading,
            model,
            float(self.config.low_quota_five_hour_pct),
            float(self.config.low_quota_weekly_pct),
        )
        if not low:
            return

        if guard == "block":
            resets = [
                f"{bucket.get('window')} limit resets at {bucket.get('reset_time_utc')}"
                for bucket in low
                if bucket.get("reset_time_utc")
            ]
            detail = "; ".join(resets) if resets else "reset time unavailable"
            group_name = usage_utils.group_for_model(model) or "the relevant model group"
            raise ManagerError(
                f"Remaining agy quota for {group_name} is below the configured low "
                f"threshold ({detail})",
                code="low_quota",
            )

        if "low_quota" not in warnings:
            warnings.append("low_quota")

    def _read_usage(
        self,
        use_cache: bool = True,
        account: Optional[AccountConfig] = None,
    ) -> dict[str, Any]:
        """Read agy `/usage` through the manager's in-memory cache.

        With no account (the single-account path) the call is byte-identical to
        the pre-multi-account one. With an account the home, child env, cache
        key and identity expectation are that account's (D103/D104/D107).
        """
        configured = account is not None and self.registry.configured
        return usage_utils.get_usage(
            getattr(self.runner, "executable", "agy"),
            home=account.expanded_home if account is not None else self.home,
            cache=self._usage_cache,
            use_cache=use_cache,
            auth_error_patterns=getattr(self.config, "auth_error_patterns", None),
            account_name=account.name if configured else None,
            expected_email=account.expected_email if account is not None else None,
            expected_email_sha256=(
                account.expected_email_sha256 if account is not None else None
            ),
            # None keeps the pre-multi-account env; an account gets the
            # isolated env too, not only its HOME.
            account=account,
        )

    def usage(self) -> dict[str, Any]:
        """Return the read-only agy_usage payload (D59, Item 3)."""
        reading = self._read_usage(use_cache=True)
        annotated = usage_utils.annotate_low_flags(reading, self.config)
        return {
            "available": bool(annotated.get("available")),
            "reason": annotated.get("reason"),
            "cached": bool(annotated.get("cached")),
            "account": annotated.get("account")
            or {"masked_email": None, "auth_method": None},
            "groups": annotated.get("groups", []),
        }

    def status(self, job_id: str, tail_lines: int = 20) -> JobStatusResponse:
        """Return status telemetry and recent redacted logs for a job."""
        self._validate_job_id(job_id)
        job = get_job(job_id, self.state_dir)
        job_dir = get_job_dir(job_id, self.state_dir)

        if not job.files_modified and job.commits_count > 0 and job.base_commit:
            ref_path = (
                job.worktree_path
                if (job.worktree_path and job.worktree_path.is_dir())
                else job.repo_path
            )
            job.files_modified = get_files_modified(
                ref_path, job.base_commit, job.head_commit or job.branch, cap=200
            )

        liveness = get_liveness_summary(job_dir, job.worktree_path)
        envelope = build_standard_envelope(job, self._get_elapsed(job))

        recent_tail = liveness["recent_log_tail"]
        if isinstance(recent_tail, list):
            recent_tail = recent_tail[-tail_lines:]
        else:
            recent_tail = []

        raw_runs = getattr(job, "runs", None) or []
        projected_runs: list[dict[str, Any]] = []
        for idx, entry in enumerate(raw_runs):
            if not isinstance(entry, dict):
                continue
            w_clock = entry.get("wall_clock_seconds")
            if w_clock is not None:
                try:
                    w_clock = float(w_clock)
                except (ValueError, TypeError):
                    w_clock = None
            projected_runs.append(
                {
                    "index": idx,
                    "started_at": entry.get("started_at"),
                    "ended_at": entry.get("ended_at"),
                    "wall_clock_seconds": w_clock,
                    "outcome": entry.get("outcome"),
                }
            )

        clocks = [
            r["wall_clock_seconds"]
            for r in projected_runs
            if r["wall_clock_seconds"] is not None
        ]
        # null when no run has finished (no run row, or no recorded wall clock)
        wall_clock_seconds = round(sum(clocks), 3) if clocks else None

        return JobStatusResponse(
            **standard_envelope_fields(envelope),
            last_cli_log_mtime=float(liveness["last_cli_log_mtime"]),
            last_commit_at=str(liveness["last_commit_at"]) if liveness["last_commit_at"] else None,
            worktree_dirty=bool(liveness["worktree_dirty"]),
            recent_log_tail=recent_tail,
            conversation_id=job.conversation_id,
            conversation_id_source=getattr(job, "conversation_id_source", None),
            runs=projected_runs,
            wall_clock_seconds=wall_clock_seconds,
        )

    def wait(self, job_id: str, seconds: int = 30) -> StandardEnvelope:
        """Wait until job transitions out of RUNNING (or QUEUED) or call timeout expires.

        Per SPEC section 8.3 & D58:
        'seconds: int = 30 (Timeout for call; default 30s, maximum 300s to prevent
        MCP client timeouts)'
        Clamped to maximum 300s with a warning field added to the envelope if clamped.
        """
        self._validate_job_id(job_id)
        get_job(job_id, self.state_dir)

        warnings_to_add: list[str] = []
        if seconds > 300:
            # The MCP client has its own timeout; clamping here keeps the server
            # responsive and makes the clamp visible in `warnings` (D58 item 8).
            warnings_to_add.append(f"Wait timeout clamped to 300s (requested: {seconds}s)")
            capped_seconds = 300
        else:
            capped_seconds = max(seconds, 1)

        deadline = time.time() + capped_seconds

        while time.time() < deadline:
            with self._lock:
                is_resuming = job_id in self._resuming_jobs
                has_supervisor = job_id in self._supervisors

            if is_resuming:
                time.sleep(0.05)
                continue

            refreshed = get_job(job_id, self.state_dir)
            if has_supervisor:
                time.sleep(0.05)
                continue

            # If auto-resume on denial is pending, keep waiting for the auto-resume turn
            if (
                refreshed.state == JobState.NEEDS_REVIEW
                and refreshed.review_reason in ("blocked_by_permission", "denied_without_progress")
                and refreshed.commits_count == 0
                and refreshed.denial_resume_count < self.config.auto_resume_on_denial
            ):
                time.sleep(0.05)
                continue

            # If verification auto-fix is pending, keep waiting
            if (
                refreshed.state == JobState.NEEDS_REVIEW
                and refreshed.review_reason in ("verification_failed", "verify_failed")
                and refreshed.auto_fix_count < self.config.auto_fix_attempts
            ):
                time.sleep(0.05)
                continue

            # If the one-shot plan nudge is pending, keep waiting for that turn.
            if (
                refreshed.state == JobState.NEEDS_REVIEW
                and refreshed.review_reason == "plan_incomplete"
                and self.config.plan_nudge
                and refreshed.plan_nudge_count < 1
                and bool((refreshed.plan_summary or {}).get("remaining"))
            ):
                time.sleep(0.05)
                continue

            if refreshed.state not in (JobState.QUEUED, JobState.RUNNING):
                envelope = build_standard_envelope(refreshed, self._get_elapsed(refreshed))
                if warnings_to_add:
                    envelope.warnings.extend(warnings_to_add)
                return envelope
            time.sleep(0.05)

        refreshed = get_job(job_id, self.state_dir)
        envelope = build_standard_envelope(refreshed, self._get_elapsed(refreshed))
        if warnings_to_add:
            envelope.warnings.extend(warnings_to_add)
        return envelope

    def reconcile(self) -> list[Job]:
        """Perform startup crash recovery and reattach running jobs per SPEC section 16."""
        from agy_mcp.recovery import reconcile_on_startup
        from agy_mcp.runner import ProcessHandle

        reconciled_jobs = reconcile_on_startup(
            state_dir=self.state_dir,
            runner=self.runner,
            home=self.home,
        )

        with self._lock:
            for job in reconciled_jobs:
                if job.state == JobState.RUNNING and job.job_id not in self._supervisors:
                    job_dir = get_job_dir(job.job_id, self.state_dir)
                    snapshot = load_snapshot(job_dir / "snapshot.json")
                    if snapshot is None:
                        handle = ProcessHandle(pid=job.pid or 0, pgid=job.pgid or 0)
                        self.runner.kill(handle)
                        transition_job_state(
                            job=job,
                            target_state=JobState.NEEDS_REVIEW,
                            event_trigger=(
                                "Crash recovery found missing or corrupted audit baseline snapshot"
                            ),
                            detail={"review_reason": "audit_violation"},
                            base_state_dir=self.state_dir,
                        )
                        continue
                    handle = ProcessHandle(
                        pid=job.pid or 0,
                        pgid=job.pgid or 0,
                        start_time=job.start_time or 0,
                        job_id=job.job_id,
                        stdout_path=job_dir / "stdout.txt",
                        stderr_path=job_dir / "stderr.txt",
                    )
                    account = self._account_for_job(job)
                    supervisor = JobSupervisor(
                        job=job,
                        handle=handle,
                        runner=self.runner,
                        job_dir=job_dir,
                        max_minutes=job.max_minutes,
                        max_minutes_cap=self.config.max_minutes_cap,
                        baseline_snapshot=snapshot,
                        home=self.home,
                        base_state_dir=self.state_dir,
                        poll_interval_seconds=self.poll_interval_seconds,
                        settle_window_seconds=self.settle_window_seconds,
                        checkpoint_interval_seconds=self.config.checkpoint_commit_minutes * 60,
                        agy_home=self._agy_home_for(account),
                        agy_binary=str(getattr(self.runner, "executable", "agy")),
                        account_name=job.account,
                        account_masked_email=job.account_masked_email,
                        account_home=account.expanded_home,
                        account=account,
                        usage_cache=self._usage_cache,
                        quota_error_patterns=self.config.quota_error_patterns,
                        auth_error_patterns=self.config.auth_error_patterns,
                        plan_nudge=self.config.plan_nudge,
                        claim_phrases=self.config.claim_phrases,
                        commit_lock=self._get_job_commit_lock(job.job_id),
                    )
                    self._supervisors[job.job_id] = supervisor
                    t = threading.Thread(
                        target=self._run_supervisor,
                        args=(supervisor, job),
                        daemon=True,
                    )
                    self._threads[job.job_id] = t
                    t.start()

        try:
            from agy_mcp.jobs import cleanup_retained_jobs

            cleanup_retained_jobs(self.state_dir, self.config.job_retention_days)
        except Exception:
            pass

        return reconciled_jobs

    def result(self, job_id: str) -> JobResultResponse:
        """Return final job outcome with diff stats and capped output text."""
        self._validate_job_id(job_id)
        job = get_job(job_id, self.state_dir)
        job_dir = get_job_dir(job_id, self.state_dir)

        if not job.files_modified and job.commits_count > 0 and job.base_commit:
            ref_path = (
                job.worktree_path
                if (job.worktree_path and job.worktree_path.is_dir())
                else job.repo_path
            )
            job.files_modified = get_files_modified(
                ref_path, job.base_commit, job.head_commit or job.branch, cap=200
            )

        # 1. Output response
        stdout_file = job_dir / "stdout.txt"
        raw_response = ""
        if stdout_file.is_file():
            try:
                from agy_mcp.util import read_file_tail

                content = read_file_tail(stdout_file, 4 * 1024 * 1024)
                from agy_mcp.parse_result import _extract_last_json_object

                obj = _extract_last_json_object(content)
                if obj and "response" in obj:
                    raw_response = str(obj["response"])
                else:
                    raw_response = content
            except OSError:
                pass

        if not raw_response and job.state == JobState.FAILED:
            stderr_file = job_dir / "stderr.txt"
            if stderr_file.is_file():
                try:
                    from agy_mcp.util import read_file_tail

                    raw_response = read_file_tail(stderr_file, 4 * 1024 * 1024)
                except OSError:
                    pass

        if job.review_reason in ("verification_failed", "verify_failed", "verify_timeout"):
            tail_file = job_dir / "verify_tail.txt"
            if tail_file.is_file():
                try:
                    from agy_mcp.util import read_file_tail

                    v_tail_str = read_file_tail(tail_file, 64 * 1024)
                    if raw_response:
                        raw_response = f"{raw_response}\n\n{v_tail_str}"
                    else:
                        raw_response = v_tail_str
                except OSError:
                    pass

        # 2. Diff stat and commit log
        raw_diff_stat = ""
        commits_list: list[dict[str, Any]] = []
        if job.worktree_path and job.worktree_path.is_dir():
            try:
                head_parse = run_git(["rev-parse", "HEAD"], job.worktree_path, check=False)
                head_ref = (
                    head_parse.stdout.strip()
                    if head_parse.returncode == 0 and head_parse.stdout.strip()
                    else (job.head_commit or "HEAD")
                )
                if head_parse.returncode == 0 and head_parse.stdout.strip():
                    job.head_commit = head_ref
                diff_res = run_git(
                    ["diff", "--stat", f"{job.base_commit}..{head_ref}"],
                    job.worktree_path,
                    check=False,
                )
                if diff_res.returncode == 0:
                    raw_diff_stat = diff_res.stdout

                log_res = run_git(
                    [
                        "log",
                        "-n",
                        "50",
                        "--format=%H|%s|%an|%cI",
                        f"{job.base_commit}..{head_ref}",
                    ],
                    job.worktree_path,
                    check=False,
                )
                if log_res.returncode == 0:
                    for line in log_res.stdout.splitlines():
                        if not line.strip():
                            continue
                        parts = line.split("|", 3)
                        if len(parts) == 4:
                            commits_list.append(
                                {
                                    "sha": parts[0],
                                    "subject": parts[1],
                                    "author": parts[2],
                                    "timestamp": parts[3],
                                }
                            )
            except Exception:
                pass

        # 3. Audit events tail
        audit_events: list[dict[str, Any]] = []
        audit_file = job_dir / "audit.jsonl"
        if audit_file.is_file():
            try:
                from agy_mcp.util import read_file_tail

                audit_tail_text = read_file_tail(audit_file, 256 * 1024, notice="")
                for line in audit_tail_text.splitlines():
                    if line.strip():
                        try:
                            audit_events.append(json.loads(line.strip()))
                        except Exception:
                            pass
            except Exception:
                pass

        return build_result_response(
            job=job,
            raw_response=raw_response,
            raw_diff_stat=raw_diff_stat,
            raw_commits=commits_list,
            audit_events_tail=audit_events[-20:],
            elapsed_seconds=self._get_elapsed(job),
            home=self.home,
        )

    def cancel(self, job_id: str) -> StandardEnvelope:
        """Cancel a running job, terminate its process group, and checkpoint changes."""
        self._validate_job_id(job_id)
        job_lock = self._get_job_lock(job_id)
        with job_lock:
            job = get_job(job_id, self.state_dir)
            if job.state == JobState.QUEUED:
                transition_job_state(
                    job,
                    JobState.CANCELLED,
                    event_trigger="Explicit call to agy_cancel",
                    base_state_dir=self.state_dir,
                )
                if job.worktree_path and job.worktree_path.is_dir():
                    try:
                        run_git(
                            ["worktree", "remove", "--force", str(job.worktree_path)],
                            job.repo_path,
                            check=False,
                        )
                        run_git(["worktree", "prune"], job.repo_path, check=False)
                        if job.branch:
                            run_git(["branch", "-D", job.branch], job.repo_path, check=False)
                    except Exception:
                        pass
            elif job.state == JobState.RUNNING:
                with self._lock:
                    supervisor = self._supervisors.get(job_id)
                    t = self._threads.get(job_id)
                if supervisor:
                    # terminate_group performs the pre-kill checkpoint and the
                    # SIGTERM/SIGKILL sequence; joining bounds the response time.
                    supervisor.terminate_group()
                    if t:
                        t.join(timeout=10.0)
                else:
                    if job.pgid and job.pgid > 1:
                        try:
                            os.killpg(job.pgid, 15)  # SIGTERM
                        except OSError:
                            pass
                    if job.worktree_path and job.worktree_path.is_dir():
                        commit_lock = self._get_job_commit_lock(job_id)
                        with commit_lock:
                            try:
                                sha = checkpoint_commit(job.worktree_path)
                                if sha:
                                    job.commits_count += 1
                                    job.head_commit = sha
                            except Exception:
                                pass
                    transition_job_state(
                        job,
                        JobState.CANCELLED,
                        event_trigger="Explicit call to agy_cancel",
                        base_state_dir=self.state_dir,
                    )

            refreshed = get_job(job_id, self.state_dir)
            return build_standard_envelope(refreshed, self._get_elapsed(refreshed))

    def list(self) -> JobListResponse:
        """List all jobs with global disk metrics and drifted worktrees."""
        try:
            from agy_mcp.jobs import cleanup_retained_jobs

            cleanup_retained_jobs(self.state_dir, self.config.job_retention_days)
        except Exception:
            pass

        jobs = list_jobs(self.state_dir)
        envelopes = [build_standard_envelope(j, self._get_elapsed(j)) for j in jobs]
        active_count = sum(1 for j in jobs if j.state in (JobState.QUEUED, JobState.RUNNING))

        total_disk = 0
        jobs_dir = self.state_dir / "jobs"
        if jobs_dir.is_dir():
            for root, _, files in os.walk(jobs_dir):
                for f in files:
                    try:
                        total_disk += (Path(root) / f).stat().st_size
                    except OSError:
                        pass

        drifted = find_drifted_worktrees(self.state_dir)
        return JobListResponse(
            jobs=envelopes,
            total_active_jobs=active_count,
            disk_usage_bytes=total_disk,
            drifted_worktrees=drifted,
        )

    def _build_state_block(self, job: Job) -> str:
        """Build the trusted resume state block from wrapper observations (D62).

        Commit subjects, plan titles and NOTES.md are agent-derived, so they are
        read through the hardened scratchpad reader (for notes) and wrapped
        individually by ``scratchpad.format_state_block``. Git output is capped.
        """
        worktree = job.worktree_path
        commits: list[str] = []
        notes = ""
        if worktree and Path(worktree).is_dir():
            try:
                entries = get_commits_list(
                    Path(worktree),
                    job.base_commit,
                    job.head_commit or "HEAD",
                    max_count=10,
                    max_subject_bytes=300,
                )
                commits = [str(entry.get("subject", "")) for entry in entries[:10]]
            except Exception:
                commits = []
            try:
                notes_result = scratchpad.read_scratchpad_file(
                    Path(worktree), "NOTES.md", scratchpad.MAX_NOTES_BYTES
                )
                notes = notes_result.text or ""
            except Exception:
                notes = ""

        verify_status = job.verify_status or {}
        if verify_status.get("ran"):
            verify_result = (
                f"ran=True passed={verify_status.get('passed')} "
                f"rc={verify_status.get('rc')} "
                f"status={verify_status.get('status') or 'unknown'} "
                f"command={verify_status.get('command') or job.verify or 'unknown'} "
                f"timeout={verify_status.get('timeout_seconds') or 'unknown'}s "
                f"timed_out={bool(verify_status.get('timed_out'))}"
            )
        elif job.verify:
            verify_result = (
                f"not run (reason={verify_status.get('reason') or job.review_reason or 'unknown'})"
            )
        else:
            verify_result = "not run (no verify command requested)"

        remaining = [
            str(item) for item in ((job.plan_summary or {}).get("remaining") or [])
        ]

        # A resume must be self-contained even without a conversation id, so
        # restate the task, the actual HEAD and the files already changed.
        head_commit = job.head_commit or ""
        if worktree and Path(worktree).is_dir():
            try:
                head_res = run_git(["rev-parse", "HEAD"], Path(worktree), check=False)
                if head_res.returncode == 0 and head_res.stdout.strip():
                    head_commit = head_res.stdout.strip()
            except Exception:
                pass
        files_changed = [str(path) for path in (job.files_modified or [])[:20]]
        return scratchpad.format_state_block(
            commits,
            verify_result,
            remaining,
            notes,
            original_task_summary=job.title,
            head_commit=head_commit or None,
            files_changed=files_changed,
        )

    def resume(
        self,
        job_id: str,
        answer: str,
        is_trusted: bool = False,
        follow_up: bool = False,
        allow_handoff: bool = False,
    ) -> StandardEnvelope:
        """Resume a conversation after decision, timeout, or non-security review.

        ``follow_up=True`` is the explicit opt-in that also permits done and
        cancelled jobs to continue the same conversation on the same worktree.
        The default gate is unchanged. ``allow_handoff=True`` lets a
        quota-paused job move to another account immediately instead of waiting
        for the configured handoff threshold (D111).
        """
        self._validate_job_id(job_id)
        # Do not start a new generation while the previous supervisor is still
        # finalizing its job.json write (D65). Done before the job lock so an
        # in-thread auto-resume can still acquire that lock.
        self._wait_for_prior_supervisor(job_id)
        job_lock = self._get_job_lock(job_id)

        with job_lock:
            job = get_job(job_id, self.state_dir)

            # Resumption state gate
            # job_lock is taken before the concurrency flock below. That is the
            # actual order in this codebase; no path takes them in reverse, so
            # the slight deviation from SPEC §10's stated tier order is safe
            # today (see docs/AUDIT_ORGANIZATION.md).
            if job.state == JobState.NEEDS_REVIEW:
                security_reasons = {
                    "audit_violation",
                    "host_path_modified",
                    "instruction_file_modified",
                    "audit_unavailable",
                }
                if job.review_reason in security_reasons:
                    raise ManagerError(
                        (
                            "Cannot resume job in needs_review with security reason "
                            f"'{job.review_reason}'"
                        ),
                        code="cannot_resume_security_review",
                    )
            elif job.state in (JobState.DONE, JobState.CANCELLED):
                if not follow_up:
                    raise ManagerError(
                        f"Cannot resume job in state '{job.state.value}' without "
                        "follow_up=true",
                        code="invalid_state_for_resume",
                    )
            elif job.state not in (JobState.NEEDS_DECISION, JobState.TIMEOUT):
                raise ManagerError(
                    f"Cannot resume job in state '{job.state.value}'",
                    code="invalid_state_for_resume",
                )

            # Budget check
            effective_limit = min(job.max_minutes, self.config.max_minutes_cap)
            if job.cumulative_minutes >= effective_limit:
                raise ManagerError("Cumulative job time budget exhausted", code="cap_exceeded")

            # Pinned account: verify home/policy, then consider a handoff to a
            # different account when the pinned one is still exhausted (D111).
            account = self._account_for_job(job)
            self._check_account_ready(account)
            handoff_prompt = self._maybe_switch_account(job, allow_handoff)
            if handoff_prompt is not None:
                answer = f"{handoff_prompt}\n\n## Caller answer\n{answer}"
                account = self._account_for_job(job)
                self._check_account_ready(account)

            # Usage guard on resume (D59, Item 4).
            self._apply_usage_guard(job.model, job.warnings, account=account)

            # Mandatory resume audit gate
            job_dir = get_job_dir(job_id, self.state_dir)
            baseline = load_snapshot(job_dir / "snapshot.json")
            if baseline is None:
                raise ManagerError(
                    "Resume blocked by missing or corrupted audit baseline snapshot",
                    code="audit_violation",
                )
            findings = audit(job, baseline, home=self.home)
            if findings.has_fatal:
                raise ManagerError(
                    f"Resume blocked by security audit violation: {findings.fatal_reason}",
                    code="audit_violation",
                )

            # Revalidate verify command on resume per SPEC section 13 & F9
            if job.verify:
                if not is_verify_allowed(
                    job.verify,
                    self.config.verify_allowlist,
                    worktree_path=job.worktree_path,
                    repo_path=job.repo_path,
                ):
                    raise ManagerError(
                        verify_rejection_message(job.verify, self.config.verify_allowlist),
                        code="verify_not_allowed",
                    )

            # Conversation honesty: never pass a non-UUID and never pretend a
            # fresh run continued the old conversation.
            conversation_id = strict_uuid(job.conversation_id)
            conversation_continued = conversation_id is not None
            if job.conversation_id is not None and conversation_id is None:
                # Drop a sticky non-UUID so it is never persisted or passed.
                job.conversation_id = None
            if not conversation_continued:
                warning = (
                    "conversation_not_continued: no valid conversation id; resumed "
                    "with a self-contained prompt instead of continuing the old "
                    "conversation"
                )
                if warning not in job.warnings:
                    job.warnings.append(warning)

            # Standing preamble + wrapper state block + user answer (SPEC §14, D62)
            state_block = self._build_state_block(job)
            resume_prompt = format_resume_prompt(
                answer, is_trusted=is_trusted, state_block=state_block
            )

            stdout_path = job_dir / "stdout.txt"
            stderr_path = job_dir / "stderr.txt"
            log_file = job_dir / "cli.log"
            tmp_dir = job_dir / "tmp"

            env = build_account_env(account, tmp_dir)
            agy_binary = getattr(self.runner, "executable", "agy")
            remaining_budget = max(60, int((effective_limit - job.cumulative_minutes) * 60))

            argv = build_argv(
                agy_binary=agy_binary,
                prompt=resume_prompt,
                model=job.model,
                print_timeout_seconds=remaining_budget,
                log_file=log_file,
                conversation_id=conversation_id,
            )

            handle = None
            try:
                with concurrency_lock(self.state_dir):
                    active_count = count_active_jobs(self.state_dir, exclude_job_id=job.job_id)
                    if active_count >= self.config.max_concurrent_jobs:
                        raise ManagerError(
                            f"Active jobs ({active_count}) reached concurrency limit "
                            f"({self.config.max_concurrent_jobs})",
                            code="concurrency_limit_exceeded",
                        )
                    account_cap = (
                        account.max_concurrent_jobs or self.config.max_concurrent_jobs
                    )
                    account_active = count_active_jobs(
                        self.state_dir,
                        exclude_job_id=job.job_id,
                        account=account.name,
                    )
                    if account_active >= account_cap:
                        raise ManagerError(
                            f"Account '{account.name}' active jobs ({account_active}) "
                            f"reached its concurrency limit ({account_cap})",
                            code="account_concurrency_limit",
                        )

                    # Every resume is a new generation: anchor the reused
                    # cli.log and clear the previous run's token/quota numbers.
                    job.tokens = TokenUsage()
                    job.quota_estimate = None
                    job.cli_log_offset, job.cli_log_prefix_sha256 = capture_cli_log_anchor(
                        log_file
                    )
                    job.conversation_continued = conversation_continued
                    # Same for the appended stdout/stderr streams: anchor before
                    # spawn so this generation cannot re-read the prior payload.
                    job.stdout_offset = (
                        stdout_path.stat().st_size if stdout_path.is_file() else 0
                    )
                    handle = self.runner.launch(
                        job_id=job.job_id,
                        workdir=job.worktree_path,
                        args=argv,
                        env=env,
                        stdout_path=stdout_path,
                        stderr_path=stderr_path,
                    )

                    job.pid = handle.pid
                    job.pgid = handle.pgid
                    job.start_time = handle.start_time
                    job.run_generation = (job.run_generation or 0) + 1
                    job.launch_wall_clock = time.time()
                    trigger = f"Resumed with answer: {answer[:40]}"
                    if follow_up and job.state in (JobState.DONE, JobState.CANCELLED):
                        trigger = f"Follow-up resumed with answer: {answer[:40]}"
                    transition_job_state(
                        job,
                        JobState.RUNNING,
                        event_trigger=trigger,
                        base_state_dir=self.state_dir,
                        follow_up=follow_up,
                    )

                supervisor = JobSupervisor(
                    job=job,
                    handle=handle,
                    runner=self.runner,
                    job_dir=job_dir,
                    max_minutes=job.max_minutes,
                    max_minutes_cap=self.config.max_minutes_cap,
                    home=self.home,
                    base_state_dir=self.state_dir,
                    poll_interval_seconds=self.poll_interval_seconds,
                    settle_window_seconds=self.settle_window_seconds,
                    checkpoint_interval_seconds=self.config.checkpoint_commit_minutes * 60,
                    agy_home=self._agy_home_for(account),
                    agy_binary=str(getattr(self.runner, "executable", "agy")),
                    account_name=job.account,
                    account_masked_email=job.account_masked_email,
                    account_home=account.expanded_home,
                    account=account,
                    usage_cache=self._usage_cache,
                    quota_error_patterns=self.config.quota_error_patterns,
                    auth_error_patterns=self.config.auth_error_patterns,
                    plan_nudge=self.config.plan_nudge,
                    claim_phrases=self.config.claim_phrases,
                    quota_estimate_weekly_per_1k_tokens=(
                        self.config.quota_estimate_weekly_per_1k_tokens
                    ),
                    quota_estimate_five_hour_per_1k_tokens=(
                        self.config.quota_estimate_five_hour_per_1k_tokens
                    ),
                    commit_lock=self._get_job_commit_lock(job.job_id),
                )

                with self._lock:
                    self._supervisors[job_id] = supervisor
                    t = threading.Thread(
                        target=self._run_supervisor,
                        args=(supervisor, job),
                        daemon=True,
                    )
                    self._threads[job_id] = t
                    t.start()
            except Exception:
                if handle is not None:
                    try:
                        self.runner.kill(handle, grace_seconds=0.5)
                    except Exception:
                        pass
                    try:
                        self.runner.cleanup(handle)
                    except Exception:
                        pass
                raise

        envelope = build_standard_envelope(job, self._get_elapsed(job))
        envelope.conversation_continued = job.conversation_continued
        return envelope

    def review(self, job_id: str) -> JobReviewResponse:
        """Inspect merge readiness, branch drift, ahead/behind counts, and untrusted diff."""
        self._validate_job_id(job_id)
        job = get_job(job_id, self.state_dir)

        job_dir = get_job_dir(job_id, self.state_dir)
        merge_base = ""
        ahead = 0
        behind = 0
        base_moved = False
        raw_diff = ""
        audit_findings: list[dict[str, Any]] = []
        audit_unavailable = False

        baseline = load_snapshot(job_dir / "snapshot.json")
        if baseline is None:
            findings = Findings(
                items=[
                    Finding(
                        severity=AuditSeverity.FATAL,
                        reason="audit_violation",
                        path="snapshot.json",
                        detail="Missing or corrupted audit baseline snapshot",
                    )
                ]
            )
            audit_findings = [f.to_dict() for f in findings.items]

        if job.worktree_path and job.worktree_path.is_dir():
            try:
                head_parse = run_git(["rev-parse", "HEAD"], job.worktree_path, check=False)
                head_ref = (
                    head_parse.stdout.strip()
                    if head_parse.returncode == 0 and head_parse.stdout.strip()
                    else (job.head_commit or "HEAD")
                )
                if head_parse.returncode == 0 and head_parse.stdout.strip():
                    job.head_commit = head_ref
                merge_base = get_merge_base(job.worktree_path, job.base_commit, head_ref)
                ahead, behind = get_ahead_behind(job.worktree_path, job.base_commit, head_ref)

                repo_head = run_git(["rev-parse", "HEAD"], job.repo_path, check=False)
                if repo_head.returncode == 0:
                    base_moved = repo_head.stdout.strip() != job.base_commit

                diff_res = run_git(
                    ["diff", f"{job.base_commit}...{head_ref}"],
                    job.worktree_path,
                    check=False,
                )
                if diff_res.returncode == 0:
                    raw_diff = diff_res.stdout

                if baseline is not None:
                    findings = audit(job, baseline, home=self.home)
                    audit_findings = [f.to_dict() for f in findings.items]
            except Exception:
                # A security review must never look clean because the audit or a
                # git query failed. Surface a fatal, code-only finding and force
                # the envelope to say a human must review; never include the
                # exception text or any host path.
                audit_unavailable = True

        if audit_unavailable:
            audit_findings.append(
                Finding(
                    severity=AuditSeverity.FATAL,
                    reason="audit_unavailable",
                    path="audit",
                    detail="audit_unavailable",
                ).to_dict()
            )

        return build_review_response(
            job=job,
            merge_base=merge_base,
            ahead=ahead,
            behind=behind,
            base_moved=base_moved,
            audit_findings=audit_findings,
            raw_untrusted_diff=raw_diff,
            elapsed_seconds=self._get_elapsed(job),
            audit_unavailable=audit_unavailable,
        )

    def cleanup(
        self,
        job_id: str,
        remove_worktree: bool = True,
        delete_branch: bool = False,
        force: bool = False,
    ) -> JobCleanupResponse:
        """Clean up worktree and delete branch with cleanliness safety gates."""
        self._validate_job_id(job_id)
        job = get_job(job_id, self.state_dir)
        if job.state in (JobState.RUNNING, JobState.QUEUED):
            if not force:
                raise ManagerError(
                    f"Cannot clean up running job '{job_id}' without force=True",
                    code="job_is_running",
                )
            # Force cleanup on running/queued job: cancel first to kill process group and finalize
            # Never remove a worktree out from under a live agy process (F4/F12).
            self.cancel(job_id)
            job = get_job(job_id, self.state_dir)

        try:
            return cleanup_worktree(
                job=job,
                remove_worktree_flag=remove_worktree,
                delete_branch_flag=delete_branch,
                force=force,
            )
        except RuntimeError as e:
            if "dirty worktree" in str(e):
                raise ManagerError(str(e), code="worktree_dirty") from e
            elif "unmerged commits" in str(e):
                raise ManagerError(str(e), code="unmerged_commits") from e
            raise ManagerError(str(e), code="cleanup_failed") from e

    def shutdown(self, timeout: float = 5.0) -> None:
        """Stop all supervisor background threads cleanly without killing agy processes."""
        with self._lock:
            supervisors = list(self._supervisors.values())
            threads = list(self._threads.values())
        for s in supervisors:
            s.stop()
        for t in threads:
            t.join(timeout=timeout)
