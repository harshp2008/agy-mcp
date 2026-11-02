"""The authoritative job state machine (SPEC section 7).

Owns the :class:`JobState` enum, the terminal-state set and
``TRANSITION_TABLE``. ``jobs.transition_job_state`` is the only code allowed to
apply these edges. This module must never grow runtime behavior, side effects
or per-tool policy: it is a pure data definition shared by persistence,
supervision, parsing and the envelope builders.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class JobState(str, Enum):
    """Governed lifecycle states for an agy-mcp job."""

    QUEUED = "queued"
    RUNNING = "running"
    NEEDS_DECISION = "needs_decision"
    NEEDS_REVIEW = "needs_review"
    DONE = "done"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


# timeout and needs_review are deliberately NOT terminal: both are resumable
# per SPEC section 7. done/failed/cancelled cannot leave their state through the
# normal resume path; done/cancelled gain an explicit follow_up=true edge only.
TERMINAL_STATES = frozenset(
    {
        JobState.DONE,
        JobState.FAILED,
        JobState.CANCELLED,
    }
)


@dataclass(frozen=True)
class StateTransition:
    """Represents an allowed state transition in the state machine."""

    current_state: JobState
    event_trigger: str
    target_state: JobState
    resumable: Optional[bool]
    notes: str
    # Terminal -> running edges exist only for an explicit agy_resume(..., follow_up=true).
    # transition_job_state filters them out unless that flag is passed, so the
    # default resume gate and every other caller stay terminal.
    explicit_follow_up_only: bool = False


# Transition table defined exactly as in SPEC.md section 7
TRANSITION_TABLE: list[StateTransition] = [
    StateTransition(
        current_state=JobState.QUEUED,
        event_trigger="Worktree ready, process spawned & PID confirmed",
        target_state=JobState.RUNNING,
        resumable=None,
        notes="Transition occurs immediately upon background spawn.",
    ),
    StateTransition(
        current_state=JobState.QUEUED,
        event_trigger="Explicit call to agy_cancel",
        target_state=JobState.CANCELLED,
        resumable=False,
        notes="Cancelled before process launch; worktree cleaned up.",
    ),
    StateTransition(
        current_state=JobState.RUNNING,
        event_trigger="Clean exit 0, audit passes, verify passes/none",
        target_state=JobState.DONE,
        resumable=False,
        notes="Normal success.",
    ),
    StateTransition(
        current_state=JobState.RUNNING,
        event_trigger="Fatal audit violation, host path modified, or instruction file altered",
        target_state=JobState.NEEDS_REVIEW,
        resumable=False,
        notes=(
            "Process group killed; no commit made; audit_violation, "
            "host_path_modified, or instruction_file_modified."
        ),
    ),
    StateTransition(
        current_state=JobState.RUNNING,
        event_trigger="Verify attempts exhausted (verify_allowlist)",
        target_state=JobState.NEEDS_REVIEW,
        resumable=True,
        notes='reason="verification_failed". User may resume with advice.',
    ),
    StateTransition(
        current_state=JobState.RUNNING,
        event_trigger="Zero progress made and tool permission denied",
        target_state=JobState.NEEDS_REVIEW,
        resumable=True,
        notes='reason="blocked_by_permission". Non-fatal warning.',
    ),
    StateTransition(
        current_state=JobState.RUNNING,
        event_trigger="Malformed .agy/COMMIT_MSG after retries",
        target_state=JobState.NEEDS_REVIEW,
        resumable=True,
        notes='reason="invalid_commit_message".',
    ),
    StateTransition(
        current_state=JobState.RUNNING,
        event_trigger="Wall-clock supervisor or print timeout expires",
        target_state=JobState.TIMEOUT,
        resumable=True,
        notes="Process group killed; checkpoint committed; resumable.",
    ),
    StateTransition(
        current_state=JobState.RUNNING,
        event_trigger="Last non-empty line is NEEDS_DECISION:",
        target_state=JobState.NEEDS_DECISION,
        resumable=True,
        notes="Checkpoint committed; waiting for user decision.",
    ),
    StateTransition(
        current_state=JobState.RUNNING,
        event_trigger="Unhandled process crash, signal, or syntax error",
        target_state=JobState.FAILED,
        resumable=False,
        notes="Captures stderr tail.",
    ),
    StateTransition(
        current_state=JobState.RUNNING,
        event_trigger="Explicit call to agy_cancel",
        target_state=JobState.CANCELLED,
        resumable=False,
        notes="Process group killed; checkpoint committed.",
    ),
    StateTransition(
        current_state=JobState.NEEDS_DECISION,
        event_trigger="agy_resume(job_id, answer)",
        target_state=JobState.RUNNING,
        resumable=None,
        notes="Answer injected as prompt with preamble; audit gated.",
    ),
    StateTransition(
        current_state=JobState.TIMEOUT,
        event_trigger="agy_resume(job_id, answer)",
        target_state=JobState.RUNNING,
        resumable=None,
        notes="Resumes conversation; audit gated.",
    ),
    StateTransition(
        current_state=JobState.NEEDS_REVIEW,
        event_trigger="agy_resume(job_id, answer)",
        target_state=JobState.RUNNING,
        resumable=None,
        notes="Only allowed if reason is non-security. Security reasons are terminal.",
    ),
    StateTransition(
        current_state=JobState.DONE,
        event_trigger="agy_resume(job_id, answer, follow_up=true)",
        target_state=JobState.RUNNING,
        resumable=None,
        notes=(
            "Explicit follow-up only: continues the same conversation and worktree "
            "after a done job. Default resume remains invalid_state_for_resume."
        ),
        explicit_follow_up_only=True,
    ),
    StateTransition(
        current_state=JobState.CANCELLED,
        event_trigger="agy_resume(job_id, answer, follow_up=true)",
        target_state=JobState.RUNNING,
        resumable=None,
        notes=(
            "Explicit follow-up only: continues the same conversation and worktree "
            "after a cancelled job. Default resume remains invalid_state_for_resume."
        ),
        explicit_follow_up_only=True,
    ),
]
