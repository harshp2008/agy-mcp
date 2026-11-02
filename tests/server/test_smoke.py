"""Smoke test verifying module importability and state transition table fidelity with SPEC."""

from agy_mcp import (
    audit,
    config,
    gitops,
    jobs,
    models,
    parse_result,
    recovery,
    runner,
    server,
    states,
    supervisor,
    usage,
    verify,
    watcher,
    worktree,
)
from agy_mcp.states import TRANSITION_TABLE, JobState


def test_import_all_modules():
    """Verify that every module defined in SPEC can be imported cleanly."""
    modules = [
        audit,
        config,
        gitops,
        jobs,
        models,
        parse_result,
        recovery,
        runner,
        server,
        states,
        supervisor,
        usage,
        verify,
        watcher,
        worktree,
    ]
    assert all(m is not None for m in modules)


def test_state_enum_members():
    """Verify state enum members match SPEC section 7."""
    expected_states = {
        "queued",
        "running",
        "needs_decision",
        "needs_review",
        "done",
        "failed",
        "timeout",
        "cancelled",
    }
    actual_states = {s.value for s in JobState}
    assert actual_states == expected_states


def test_transition_table_matches_spec():
    """Verify that the TRANSITION_TABLE in states.py exactly reflects SPEC section 7."""
    assert len(TRANSITION_TABLE) == 16

    # Check initial queued transitions (running and cancelled)
    queued_transitions = [t for t in TRANSITION_TABLE if t.current_state == JobState.QUEUED]
    assert len(queued_transitions) == 2
    assert {t.target_state for t in queued_transitions} == {JobState.RUNNING, JobState.CANCELLED}

    # Check terminal targets from running
    running_transitions = [t for t in TRANSITION_TABLE if t.current_state == JobState.RUNNING]
    assert len(running_transitions) == 9

    targets_from_running = {t.target_state for t in running_transitions}
    assert targets_from_running == {
        JobState.DONE,
        JobState.NEEDS_REVIEW,
        JobState.TIMEOUT,
        JobState.NEEDS_DECISION,
        JobState.FAILED,
        JobState.CANCELLED,
    }

    # Verify security needs_review is non-resumable
    security_t = next(
        t for t in running_transitions if "Fatal audit violation" in t.event_trigger
    )
    assert security_t.target_state == JobState.NEEDS_REVIEW
    assert security_t.resumable is False

    # Verify operational needs_review transitions are resumable
    op_review_transitions = [
        t
        for t in running_transitions
        if t.target_state == JobState.NEEDS_REVIEW and t is not security_t
    ]
    assert len(op_review_transitions) == 3
    assert all(t.resumable is True for t in op_review_transitions)

    # Check resume transitions back to running. The normal gate stays at three
    # states; done and cancelled gain only the explicit follow_up=true edge.
    resume_transitions = [t for t in TRANSITION_TABLE if "agy_resume" in t.event_trigger]
    normal_resume = [t for t in resume_transitions if "follow_up" not in t.event_trigger]
    follow_up_resume = [t for t in resume_transitions if "follow_up" in t.event_trigger]
    assert len(normal_resume) == 3
    assert {t.current_state for t in normal_resume} == {
        JobState.NEEDS_DECISION,
        JobState.TIMEOUT,
        JobState.NEEDS_REVIEW,
    }
    assert all(t.target_state == JobState.RUNNING for t in normal_resume)
    assert len(follow_up_resume) == 2
    assert {t.current_state for t in follow_up_resume} == {
        JobState.DONE,
        JobState.CANCELLED,
    }
    assert all("follow_up=true" in t.event_trigger for t in follow_up_resume)
    assert all(t.target_state == JobState.RUNNING for t in follow_up_resume)
