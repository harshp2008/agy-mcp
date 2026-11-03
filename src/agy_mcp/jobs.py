"""Job persistence, the state-machine gate, concurrency and retention.

Owns the state directory layout, atomic ``job.json`` writes, the append-only
``audit.jsonl``, the only legitimate state-transition function, the daily cap,
the concurrency flock and disk/retention bookkeeping. It must never decide a
job's outcome (callers pass the target state), never delete unmerged branches,
and never write outside the configured state directory.
"""

import fcntl
import json
import logging
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from agy_mcp.models import AuditEvent, Job
from agy_mcp.states import TERMINAL_STATES, TRANSITION_TABLE, JobState
from agy_mcp.util import derive_branch_slug

logger = logging.getLogger(__name__)


def get_base_state_dir(base_state_dir: Optional[Path] = None) -> Path:
    """Return the resolved root state directory (~/.local/state/agy-mcp)."""
    if base_state_dir is not None:
        return base_state_dir
    xdg_state = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg_state) if xdg_state else Path.home() / ".local" / "state"
    return base / "agy-mcp"


_ACCOUNT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def cap_file_stem(account: Optional[str]) -> str:
    """Return the daily-cap file stem for an account.

    The implicit default account keeps the historical unsuffixed
    ``daily_cap`` names; every other account gets a validated suffix. The name is
    re-validated here because it becomes part of a file path (D105).
    """
    if account is None or account == "default":
        return "daily_cap"
    if not _ACCOUNT_NAME_RE.match(account):
        raise ValueError(f"invalid account name for a cap file: {account!r}")
    return f"daily_cap.{account}"


def job_belongs_to_account(job: Job, account: Optional[str]) -> bool:
    """True when ``job`` belongs to ``account``; None means the implicit default."""
    if account is None:
        return job.account is None or job.account == "default"
    return job.account == account


def get_job_dir(job_id: str, base_state_dir: Optional[Path] = None) -> Path:
    """Return the absolute path to the job state directory (~/.local/state/agy-mcp/jobs/<id>)."""
    return get_base_state_dir(base_state_dir) / "jobs" / job_id


def _atomic_write_file(target_path: Path, content: str, mode: int = 0o600) -> None:
    """Write content to a temporary file, fsync, and atomically replace the target.

    Also fsyncs the parent directory on POSIX to guarantee durability.
    """
    parent = target_path.parent
    parent.mkdir(parents=True, exist_ok=True)
    # Job state can contain prompts, diffs and logs; keep both dir and file
    # private even if the process umask is permissive.
    os.chmod(parent, 0o700)

    # Create temporary file in the same directory for atomic rename
    tmp_fd, tmp_path_str = tempfile.mkstemp(
        dir=parent, prefix=f".{target_path.name}.tmp."
    )
    tmp_path = Path(tmp_path_str)
    try:
        os.chmod(tmp_path, mode)
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, target_path)

        # Fsync parent directory descriptor
        parent_fd = os.open(str(parent), os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def append_audit_event(
    job_dir: Path,
    event: str,
    detail: dict[str, Any],
    ts: Optional[str] = None,
) -> None:
    """Append a security audit event to <jobdir>/audit.jsonl with fsync."""
    audit_path = job_dir / "audit.jsonl"
    job_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(job_dir, 0o700)

    record = AuditEvent(
        ts=ts or datetime.now(timezone.utc).isoformat(),
        event=event,
        detail=detail,
    )
    line = json.dumps(record.to_dict()) + "\n"

    # Open with O_WRONLY | O_CREAT | O_APPEND
    fd = os.open(
        str(audit_path),
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    try:
        os.chmod(audit_path, 0o600)
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def record_cleanup_error(job_dir: Path, code: str) -> None:
    """Log and audit a swallowed cleanup failure using a short code only.

    Never include exception text or host paths: the audit log is returned in
    envelopes, so it must stay free of local paths (D56/D58).

    This is the single copy shared by manager and supervisor (audit C2: the two
    were byte-identical, so one caller adding a code would have diverged them).
    The swallow is deliberate: a cleanup failure must not mask the primary error
    path that is already reporting.
    """
    logger.warning("cleanup_error code=%s", code)
    try:
        append_audit_event(job_dir, event="cleanup_error", detail={"code": code})
    except Exception:
        logger.warning("cleanup_error could not be audited code=%s", code)


def create_job(
    repo_path: Path,
    title: str,
    prompt: str,
    model: str,
    max_minutes: int,
    verify: Optional[str] = None,
    verify_timeout_seconds: int = 900,
    base_state_dir: Optional[Path] = None,
    job_id: Optional[str] = None,
    base_commit: str = "HEAD",
    branch: Optional[str] = None,
    account: Optional[str] = None,
    priority: str = "normal",
    account_masked_email: Optional[str] = None,
    quota_reservation: Optional[dict[str, Any]] = None,
) -> Job:
    """Initialize a new job record and provision its state directory per SPEC section 10."""
    jid = job_id or str(uuid.uuid4())
    job_dir = get_job_dir(jid, base_state_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(job_dir, 0o700)

    tmp_dir = job_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(tmp_dir, 0o700)

    # Provision prompt.txt (mode 0600)
    prompt_file = job_dir / "prompt.txt"
    _atomic_write_file(prompt_file, prompt, mode=0o600)

    # Derive branch name if not provided
    slug = derive_branch_slug(title)
    short_id = jid.replace("-", "")[:8]
    branch_name = branch or f"agy/{slug}-{short_id}"

    job = Job(
        job_id=jid,
        title=title,
        repo_path=repo_path,
        branch=branch_name,
        base_commit=base_commit,
        state=JobState.QUEUED,
        model=model,
        max_minutes=max_minutes,
        verify=verify,
        verify_timeout_seconds=verify_timeout_seconds,
        worktree_path=job_dir / "work",
        account=account,
        priority=priority,
        account_masked_email=account_masked_email,
        quota_reservation=quota_reservation,
    )

    save_job(job, base_state_dir)
    created_detail: dict[str, Any] = {
        "title": title,
        "model": model,
        "max_minutes": max_minutes,
        "verify": verify,
        "verify_timeout_seconds": verify_timeout_seconds,
    }
    # Tagging job_created lets the per-account daily-cap rebuild filter without
    # reading job.json, which retention may have moved (D105).
    if account is not None:
        created_detail["account"] = account
    append_audit_event(job_dir, event="job_created", detail=created_detail)
    if account is not None:
        detail_account = {"account": account}
        if account_masked_email is not None:
            detail_account["account_masked_email"] = account_masked_email
        append_audit_event(job_dir, event="account_selected", detail=detail_account)
    return job


def save_job(job: Job, base_state_dir: Optional[Path] = None) -> None:
    """Persist job state atomically to job.json (tempfile + fsync + replace)."""
    job_dir = get_job_dir(job.job_id, base_state_dir)
    job.updated_at = datetime.now(timezone.utc).isoformat()
    content = json.dumps(job.to_dict(), indent=2)
    _atomic_write_file(job_dir / "job.json", content, mode=0o600)


MAX_JOB_FILE_BYTES = 1024 * 1024  # 1 MB explicit size cap per F16
# job.json is parsed into memory on every status call; cap it so a corrupted or
# maliciously inflated file cannot exhaust the server.


def get_job(job_id: str, base_state_dir: Optional[Path] = None) -> Job:
    """Load and return an existing Job by its unique ID."""
    job_dir = get_job_dir(job_id, base_state_dir)
    job_file = job_dir / "job.json"
    if not job_file.exists():
        logger.warning("Job file not found for job_id '%s' at %s", job_id, job_file)
        raise FileNotFoundError(f"Job file not found for job_id '{job_id}'")

    st = job_file.stat()
    if st.st_size > MAX_JOB_FILE_BYTES:
        raise ValueError(
            f"job.json size ({st.st_size} bytes) exceeds limit of {MAX_JOB_FILE_BYTES} bytes"
        )

    with open(job_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return Job.from_dict(data)


def list_jobs(base_state_dir: Optional[Path] = None) -> list[Job]:
    """Scan and return all known jobs in the state directory."""
    jobs_root = get_base_state_dir(base_state_dir) / "jobs"
    if not jobs_root.exists():
        return []

    jobs: list[Job] = []
    for entry in jobs_root.iterdir():
        if entry.is_dir() and (entry / "job.json").is_file():
            try:
                jobs.append(get_job(entry.name, base_state_dir))
            except Exception:
                # Corrupt or incomplete job directories are skipped during listing
                # so one bad record cannot make agy_list unusable.
                continue

    # Sort descending by created_at
    jobs.sort(key=lambda j: j.created_at, reverse=True)
    return jobs


@contextmanager
def concurrency_lock(base_state_dir: Optional[Path] = None):
    """Acquire an exclusive flock on concurrency.lock in the state directory.

    This is the outermost lock of the hierarchy (SPEC §10): callers take it
    before creating a job or checking a resume slot, and never while holding a
    manager/job/commit in-memory lock.
    """
    state_dir = (base_state_dir or get_base_state_dir()).resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(state_dir, 0o700)
    lock_file = state_dir / "concurrency.lock"
    lock_fd = os.open(str(lock_file), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(lock_fd)


def count_active_jobs(
    base_state_dir: Optional[Path] = None,
    exclude_job_id: Optional[str] = None,
    account: Optional[str] = None,
) -> int:
    """Count active (queued/running) jobs, optionally only one account's.

    ``account=None`` keeps the historical global count. ``account="default"``
    includes old records with no ``account`` field, so the implicit default
    account keeps its historical jobs.
    """
    active_states = {JobState.QUEUED, JobState.RUNNING}
    jobs = list_jobs(base_state_dir)
    return sum(
        1
        for j in jobs
        if (j.state in active_states or getattr(j.state, "value", str(j.state)) == "starting")
        and (exclude_job_id is None or j.job_id != exclude_job_id)
        and (account is None or job_belongs_to_account(j, account))
    )


def transition_job_state(
    job: Job,
    target_state: JobState,
    event_trigger: str,
    detail: Optional[dict[str, Any]] = None,
    base_state_dir: Optional[Path] = None,
    follow_up: bool = False,
) -> Job:
    """Validate and execute a state transition per TRANSITION_TABLE and SPEC section 7.

    ``follow_up=True`` is the explicit agy_resume opt-in that unlocks the
    terminal -> running edges. Every other caller keeps the default gate.
    """
    valid_transitions = [
        t for t in TRANSITION_TABLE
        if t.current_state == job.state
        and t.target_state == target_state
        and (follow_up or not t.explicit_follow_up_only)
    ]

    if not valid_transitions:
        raise ValueError(
            f"Illegal state transition from {job.state.value} to {target_state.value}"
        )

    # Check resumption safety when transitioning from needs_review -> running
    # Security reasons are terminal by design (D55/D56); this is the last line
    # of defense even if a caller forgets the manager-level gate.
    if job.state == JobState.NEEDS_REVIEW and target_state == JobState.RUNNING:
        security_reasons = {
            "audit_violation",
            "host_path_modified",
            "instruction_file_modified",
            "audit_unavailable",
        }
        if job.review_reason in security_reasons:
            raise ValueError(
                f"Cannot resume job in needs_review with terminal security reason "
                f"'{job.review_reason}'"
            )

    old_state = job.state
    job.state = target_state

    # Update review_reason if supplied in detail
    if detail and "review_reason" in detail:
        job.review_reason = detail["review_reason"]
    elif target_state != JobState.NEEDS_REVIEW:
        job.review_reason = None
    if target_state != JobState.NEEDS_REVIEW:
        job.resume_after = None
        # Actionable needs_review errors (D80) stop applying once a new run starts;
        # failure errors without a next_action (process/audit failures) are kept.
        if (
            isinstance(job.error, dict)
            and job.error.get("next_action")
            and job.error.get("code")
            in ("invalid_commit_message", "verify_failed", "verify_timeout", "checkpoint_failed")
        ):
            job.error = None

    job_dir = get_job_dir(job.job_id, base_state_dir)
    append_audit_event(
        job_dir=job_dir,
        event="state_transition",
        detail={
            "from": old_state.value,
            "to": target_state.value,
            "trigger": event_trigger,
            **(detail or {}),
        },
    )
    save_job(job, base_state_dir)
    return job


def record_job_execution_time(
    job: Job,
    elapsed_minutes: float,
    max_minutes_cap: int = 360,
    base_state_dir: Optional[Path] = None,
) -> bool:
    """Accumulate execution minutes across launches and resumes.

    Returns:
        bool: True if cumulative execution remains within budget, False if exhausted.
    """
    job.cumulative_minutes += elapsed_minutes
    save_job(job, base_state_dir)
    effective_limit = min(job.max_minutes, max_minutes_cap)
    return job.cumulative_minutes < effective_limit


def get_daily_date(tz_name: Optional[str] = None) -> str:
    """Return ISO calendar date string (YYYY-MM-DD) for local time or configured timezone."""
    if not tz_name or tz_name.lower() in ("local", "system"):
        return datetime.now().astimezone().date().isoformat()
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(tz_name)).date().isoformat()
    except Exception:
        return datetime.now().astimezone().date().isoformat()


def check_and_increment_daily_cap(
    state_dir: Path,
    daily_cap: int,
    date_str: Optional[str] = None,
    tz_name: Optional[str] = None,
    account: Optional[str] = None,
) -> bool:
    """Check and increment daily job start counter under an exclusive flock.

    Returns:
        bool: True if start is allowed and incremented; False if daily_jobs_cap is reached.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(state_dir, 0o700)

    stem = cap_file_stem(account)
    lock_file = state_dir / f"{stem}.lock"
    data_file = state_dir / f"{stem}.json"
    target_date = date_str or get_daily_date(tz_name)

    lock_fd = os.open(str(lock_file), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        count = 0
        if data_file.exists():
            try:
                with open(data_file, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                if stored.get("date") == target_date:
                    count = int(stored.get("count", 0))
            except Exception:
                count = 0

        if count >= daily_cap:
            return False

        count += 1
        payload = json.dumps({"date": target_date, "count": count})
        _atomic_write_file(data_file, payload, mode=0o600)
        return True
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def rebuild_daily_cap_from_audit(
    state_dir: Path,
    date_str: Optional[str] = None,
    tz_name: Optional[str] = None,
    account: Optional[str] = None,
) -> int:
    """Rebuild one account's daily-cap count from job_created audit events."""
    state_dir.mkdir(parents=True, exist_ok=True)
    stem = cap_file_stem(account)
    lock_file = state_dir / f"{stem}.lock"
    data_file = state_dir / f"{stem}.json"
    target_date = date_str or get_daily_date(tz_name)

    lock_fd = os.open(str(lock_file), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        count = 0
        jobs_root = state_dir / "jobs"
        if jobs_root.exists():
            for job_dir in jobs_root.iterdir():
                audit_file = job_dir / "audit.jsonl"
                if not audit_file.is_file():
                    continue

                try:
                    with open(audit_file, "r", encoding="utf-8") as f:
                        for line in f:
                            if not line.strip():
                                continue
                            ev = json.loads(line)
                            if ev.get("event") == "job_created":
                                if account is not None:
                                    event_account = ev.get("detail", {}).get("account")
                                    if event_account != account:
                                        continue
                                ts = ev.get("ts", "")
                                try:
                                    iso_ts = ts.replace("Z", "+00:00")
                                    dt = datetime.fromisoformat(iso_ts)
                                    if tz_name and tz_name.lower() not in ("local", "system"):
                                        from zoneinfo import ZoneInfo

                                        target_tz = ZoneInfo(tz_name)
                                    else:
                                        target_tz = datetime.now().astimezone().tzinfo
                                    ev_date = dt.astimezone(target_tz).date().isoformat()
                                    if ev_date == target_date:
                                        count += 1
                                        break
                                except Exception:
                                    if ts.startswith(target_date):
                                        count += 1
                                        break
                except Exception:
                    continue

        payload = json.dumps({"date": target_date, "count": count})
        _atomic_write_file(data_file, payload, mode=0o600)
        return count
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def get_job_disk_usage(job_dir: Path) -> int:
    """Calculate the total disk bytes consumed by a job state directory."""
    if not job_dir.exists():
        return 0
    total = 0
    for root, _, files in os.walk(job_dir):
        for f in files:
            fp = Path(root) / f
            try:
                total += fp.stat().st_size
            except OSError:
                continue
    return total


def get_total_state_disk_usage(base_state_dir: Optional[Path] = None) -> int:
    """Calculate the total disk bytes consumed across all jobs."""
    jobs_root = get_base_state_dir(base_state_dir) / "jobs"
    if not jobs_root.exists():
        return 0
    total = 0
    for job_entry in jobs_root.iterdir():
        if job_entry.is_dir():
            total += get_job_disk_usage(job_entry)
    return total


def prune_retained_jobs(
    base_state_dir: Optional[Path] = None,
    retention_days: int = 14,
) -> list[str]:
    """Identify and return job IDs eligible for pruning based on age and terminal state."""
    cutoff_epoch = datetime.now(timezone.utc).timestamp() - (retention_days * 86400)
    prunable: list[str] = []

    for job in list_jobs(base_state_dir):
        if job.state in TERMINAL_STATES:
            try:
                # Parse created_at ISO timestamp
                created_dt = datetime.fromisoformat(job.created_at)
                if created_dt.timestamp() < cutoff_epoch:
                    prunable.append(job.job_id)
            except Exception:
                continue

    return prunable


def cleanup_retained_jobs(
    base_state_dir: Optional[Path] = None,
    retention_days: int = 14,
) -> list[str]:
    """Clean up finished job directories older than retention_days per F22.

    Only operates on terminal states (DONE, FAILED, CANCELLED).
    Never prunes running or active jobs.
    Never deletes a branch holding commits not in default branch.
    Moves finished job directories to <state_dir>/trash/<job_id>_<ts>.
    """
    import shutil
    import time

    from agy_mcp.gitops import run_git

    state_dir = get_base_state_dir(base_state_dir)
    trash_dir = state_dir / "trash"
    cutoff_epoch = time.time() - (retention_days * 86400)
    cleaned: list[str] = []

    for job in list_jobs(state_dir):
        if job.state not in (JobState.DONE, JobState.FAILED, JobState.CANCELLED):
            continue
        try:
            created_dt = datetime.fromisoformat(job.created_at)
            if created_dt.timestamp() >= cutoff_epoch:
                continue
        except Exception:
            continue

        job_dir = get_job_dir(job.job_id, state_dir)
        if not job_dir.exists():
            continue

        # Check unmerged branch safety
        if job.branch and job.repo_path and Path(job.repo_path).is_dir():
            try:
                br_check = run_git(["branch", "--list", job.branch], job.repo_path, check=False)
                if br_check.returncode == 0 and job.branch in br_check.stdout:
                    anc_check = run_git(
                        ["merge-base", "--is-ancestor", job.branch, "HEAD"],
                        job.repo_path,
                        check=False,
                    )
                    # If ancestor (merged), safe to remove worktree and delete branch
                    if anc_check.returncode == 0:
                        if job.worktree_path and Path(job.worktree_path).is_dir():
                            run_git(
                                ["worktree", "remove", "--force", str(job.worktree_path)],
                                job.repo_path,
                                check=False,
                            )
                        run_git(["branch", "-D", job.branch], job.repo_path, check=False)
            except Exception:
                pass

        trash_dir.mkdir(parents=True, exist_ok=True)
        dest = trash_dir / f"{job.job_id}_{int(time.time())}"
        try:
            shutil.move(str(job_dir), str(dest))
            cleaned.append(job.job_id)
        except OSError:
            pass

    return cleaned
