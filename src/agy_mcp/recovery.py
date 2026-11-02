"""Startup reconciliation and status liveness (SPEC section 16).

Owns reattaching live supervisors after a server restart, finalizing orphaned
running jobs from their on-disk streams, failing stale queued jobs and exposing
redacted liveness. It must never launch a new agy process for a dead job, never
trust a PID without a start-time match, and never finalize a job as `done` when
its audit baseline or output freshness cannot be established.
"""

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agy_mcp.audit import AuditSeverity, Finding, Findings, audit, load_snapshot
from agy_mcp.gitops import is_worktree_dirty, run_git, safety_net_commit
from agy_mcp.jobs import (
    append_audit_event,
    get_job,
    get_job_dir,
    list_jobs,
    save_job,
    transition_job_state,
)
from agy_mcp.models import Job
from agy_mcp.parse_result import parse_agy_output
from agy_mcp.runner import Runner
from agy_mcp.states import JobState
from agy_mcp.util import redact_log_text, strict_uuid, truncate_head


def check_process_liveness(pid: int, expected_start_time: int) -> bool:
    """Verify via /proc/<pid>/stat that process is alive and PID has not been reused."""
    if pid <= 1 or expected_start_time <= 0:
        return False
    try:
        stat_path = Path(f"/proc/{pid}/stat")
        content = stat_path.read_text(encoding="utf-8", errors="replace")
        rparen = content.rfind(")")
        if rparen == -1:
            return False
        rest = content[rparen + 1 :].strip().split()
        # Field 3: state ('R', 'S', 'D', 'Z', 'T', 'X')
        state = rest[0]
        if state in ("Z", "X"):
            return False
        # Field 22: starttime
        # Comparing starttime (not just PID) prevents a recycled PID from being
        # mistaken for the original agy process.
        actual_start_time = int(rest[22 - 3])
        return actual_start_time == expected_start_time
    except (ProcessLookupError, FileNotFoundError, OSError, ValueError, IndexError):
        return False


def get_liveness_summary(job_dir: Path, worktree_path: Path) -> dict[str, object]:
    """Inspect cli.log mtime, git log, and dirty state to assess job liveness.

    Returns dict matching SPEC section 8 agy_status payload additions.
    """
    cli_log = job_dir / "cli.log"
    last_cli_log_mtime = 0.0
    recent_log_tail: list[str] = []

    if cli_log.is_file():
        try:
            last_cli_log_mtime = cli_log.stat().st_mtime
            from agy_mcp.cli_log import read_cli_log_tail

            raw_text = read_cli_log_tail(cli_log, max_bytes=64 * 1024)
            # Truncate to max 4 KB and max 50 lines
            truncated_raw = truncate_head(raw_text, 4096)
            redacted_raw = redact_log_text(truncated_raw)
            lines = [line.rstrip() for line in redacted_raw.splitlines() if line.strip()]
            recent_log_tail = lines[-50:]
        except OSError:
            pass

    last_commit_at: Optional[str] = None
    worktree_dirty = False

    resolved_wt = worktree_path.resolve() if worktree_path else None
    if resolved_wt and resolved_wt.is_dir():
        try:
            worktree_dirty = is_worktree_dirty(resolved_wt)
            res = run_git(["log", "-1", "--format=%cI"], resolved_wt, check=False)
            if res.returncode == 0 and res.stdout.strip():
                last_commit_at = res.stdout.strip()
        except Exception:
            pass

    return {
        "last_cli_log_mtime": last_cli_log_mtime,
        "last_commit_at": last_commit_at,
        "worktree_dirty": worktree_dirty,
        "recent_log_tail": recent_log_tail,
    }


def reconcile_on_startup(
    state_dir: Path,
    runner: Optional[Runner] = None,
    home: Optional[Path] = None,
) -> list[Job]:
    """Scan all job directories on startup, reattaching or finalizing orphaned jobs.

    Per SPEC section 16:
    1. Scans state_dir/jobs/*/job.json.
    2. For each job in state running:
       - If alive and start_time matches, preserves running status for supervisor reattachment.
       - If dead, parses final result, executes post-completion audit, executes safety-net commit
         if applicable, and transitions job to terminal state with recovered=true.
    3. Stale queued jobs are transitioned to failed with recovered=true.
    4. Runs git worktree prune across all distinct repositories.
    """
    jobs = list_jobs(base_state_dir=state_dir)
    reconciled: list[Job] = []
    seen_repos: set[Path] = set()

    for job in jobs:
        seen_repos.add(job.repo_path)
        job_dir = get_job_dir(job.job_id, base_state_dir=state_dir)

        if job.state == JobState.RUNNING:
            is_alive = False
            if job.pid is not None and job.start_time is not None:
                is_alive = check_process_liveness(job.pid, job.start_time)

            if is_alive:
                # Still running: remains running so caller / JobManager can reattach supervisor
                reconciled.append(job)
            else:
                # Dead process: finalize outcome from disk streams
                stdout_file = job_dir / "stdout.txt"
                stderr_file = job_dir / "stderr.txt"

                stdout_offset = getattr(job, "stdout_offset", 0) or 0
                stdout_text = ""
                stderr_text = ""
                is_fresh = False

                if stdout_file.is_file():
                    try:
                        st = stdout_file.stat()
                        if st.st_size > stdout_offset:
                            launch_time = getattr(job, "launch_wall_clock", None)
                            if launch_time is None or st.st_mtime >= (launch_time - 2.0):
                                with open(
                                    stdout_file, "r", encoding="utf-8", errors="replace"
                                ) as f:
                                    f.seek(stdout_offset)
                                    stdout_text = f.read()
                                if stdout_text.strip():
                                    is_fresh = True
                    except OSError:
                        pass

                if stderr_file.is_file():
                    try:
                        stderr_text = stderr_file.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        pass

                if not is_fresh:
                    # No output newer than the launch is not a clean result;
                    # fail closed to review instead of guessing from old bytes.
                    target_state = JobState.NEEDS_REVIEW
                    detail = {
                        "recovered": True,
                        "review_reason": "unconfirmed_recovery",
                    }
                    job.review_reason = "unconfirmed_recovery"
                else:
                    # Parse result from fresh output
                    parsed = parse_agy_output(
                        stdout_content=stdout_text,
                        stderr_content=stderr_text,
                        return_code=0 if stdout_text else 1,
                        commits_count=job.commits_count,
                    )

                    target_state = parsed.state
                    detail = {
                        "recovered": True,
                    }
                    if parsed.review_reason:
                        detail["review_reason"] = parsed.review_reason
                    if parsed.decision_prompt:
                        detail["decision_prompt"] = parsed.decision_prompt

                    if target_state == JobState.DONE and job.verify:
                        target_state = JobState.NEEDS_REVIEW
                        detail["review_reason"] = "unconfirmed_recovery"
                        job.review_reason = "unconfirmed_recovery"

                # Post-completion audit
                baseline = load_snapshot(job_dir / "snapshot.json")
                if baseline is None:
                    # A recovered job without a baseline cannot be proven safe;
                    # synthesize the same fatal finding the live path would emit.
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
                else:
                    findings = audit(job, baseline, home=home)
                if findings.has_fatal:
                    target_state = JobState.NEEDS_REVIEW
                    detail["review_reason"] = findings.fatal_reason or "audit_violation"
                elif any(f.reason == "policy_file_modified" for f in findings.items):
                    target_state = JobState.NEEDS_REVIEW
                    detail["review_reason"] = "policy_file_modified"
                    detail["partial_commit"] = True

                # Safety net commit on normal clean completion
                # Keep the recovered state even if the commit fails; the audit
                # finding, not the commit, decides the final state.
                if target_state == JobState.DONE and not findings.has_fatal:
                    wt = job.worktree_path.resolve() if job.worktree_path else None
                    if wt and wt.is_dir() and is_worktree_dirty(wt):
                        try:
                            safety_net_commit(wt, job.title, audit_passed=True, job_dir=job_dir)
                            job.commits_count += 1
                        except Exception:
                            pass

                job.recovered = True
                run_seconds = 0.0
                if job.launch_wall_clock is not None:
                    run_seconds = max(0.0, time.time() - float(job.launch_wall_clock))
                if is_fresh and parsed is not None:
                    job.tokens = parsed.tokens
                    job.denials = parsed.denials
                    job.warnings = parsed.warnings
                    stored_cid = strict_uuid(job.conversation_id)
                    observed_cid = strict_uuid(parsed.conversation_id)
                    if stored_cid is None and observed_cid is not None:
                        job.conversation_id = observed_cid
                    generation = int(job.run_generation or 1)
                    known = {
                        int(entry.get("run_generation", 0))
                        for entry in (job.runs or [])
                        if isinstance(entry, dict)
                    }
                    if generation not in known:
                        now_iso = datetime.now(timezone.utc).isoformat()
                        if job.launch_wall_clock is not None:
                            started_at_iso = datetime.fromtimestamp(
                                float(job.launch_wall_clock), tz=timezone.utc
                            ).isoformat()
                        else:
                            # job.start_time is /proc/<pid>/stat field 22 (clock
                            # ticks since boot), never a Unix epoch; created_at
                            # is the only safe legacy fallback.
                            started_at_iso = job.created_at or now_iso
                        ended_at_iso = now_iso
                        outcome_str = (
                            target_state.value
                            if isinstance(target_state, JobState)
                            else str(target_state)
                        )
                        job.runs = list(job.runs or [])[-49:] + [
                            {
                                "run_generation": generation,
                                "started_at": started_at_iso,
                                "ended_at": ended_at_iso,
                                "wall_clock_seconds": round(run_seconds, 3),
                                "agy_duration_seconds": parsed.agy_duration_seconds,
                                "tokens": (
                                    parsed.tokens.to_dict()
                                    if hasattr(parsed.tokens, "to_dict")
                                    else None
                                ),
                                "outcome": outcome_str,
                            }
                        ]
                # A crash may have happened after the CLI logged its model
                # resolution; recover it from the same bounded cli.log tail.
                from agy_mcp.cli_log import extract_model_resolved, read_cli_log_tail

                resolved_model = extract_model_resolved(read_cli_log_tail(job_dir / "cli.log"))
                if resolved_model:
                    job.model_resolved = resolved_model

                # D68/C4 ordering: append the usage row before the recovered
                # state becomes observable, and name the state that is about to
                # be persisted. A reader that sees the terminal/review state in
                # job.json therefore always finds the record.
                try:
                    from agy_mcp.usage import (
                        has_usage_record_for_generation,
                        record_usage,
                    )

                    usage_path = state_dir / "usage.jsonl"
                    # If the finalizer died after appending its row but before
                    # the transition was observable, this run already has a
                    # record; appending another would count its minutes twice.
                    if not has_usage_record_for_generation(
                        usage_path, job.job_id, job.run_generation or 1
                    ):
                        tokens_dict = (
                            job.tokens.to_dict() if hasattr(job.tokens, "to_dict") else {}
                        )
                        record_usage(
                            usage_path=usage_path,
                            job=job,
                            # Recovery measures this run from its own launch
                            # wall-clock, never agy's conversation-age duration.
                            duration_seconds=run_seconds,
                            tokens=tokens_dict,
                            usage_cost=job.usage_cost,
                            state=target_state,
                        )
                except Exception:
                    pass
                transition_job_state(
                    job,
                    target_state,
                    event_trigger="Crash recovered on startup",
                    detail=detail,
                    base_state_dir=state_dir,
                )
                reconciled.append(job)

        elif job.state == JobState.QUEUED:
            # Stale queued job from aborted previous session
            job.recovered = True
            job.state = JobState.FAILED
            append_audit_event(
                job_dir,
                event="crash_recovered",
                detail={
                    "from": "queued",
                    "to": "failed",
                    "error": "Server stopped while job was queued",
                },
            )
            save_job(job, state_dir)
            reconciled.append(job)

    # Prune worktrees across repositories
    for repo_path in seen_repos:
        if repo_path.is_dir() and (repo_path / ".git").is_dir():
            try:
                run_git(["worktree", "prune"], repo_path, check=False)
            except Exception:
                pass

    return [get_job(j.job_id, base_state_dir=state_dir) for j in reconciled]
