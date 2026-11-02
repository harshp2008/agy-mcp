"""Tests for finding F28: SPEC-vs-code drift reconciliation."""

from agy_mcp.models import JobState
from agy_mcp.parse_result import parse_agy_output


def test_supervisor_timeout_takes_precedence_over_termination_signal() -> None:
    """When supervisor times out and kills the process group with SIGTERM/SIGKILL,

    the supervisor timeout takes precedence over the negative signal produced by
    the kill, correctly classifying the job as TIMEOUT per SPEC sections 7 and 10.
    """
    res = parse_agy_output(
        stdout_content='{"conversation_id": "11111111-1111-4111-8111-111111111111"}',
        stderr_content="",
        return_code=-15,
        is_cancelled=False,
        is_audit_violated=False,
        supervisor_timed_out=True,
    )

    assert res.state == JobState.TIMEOUT
    assert res.conversation_id == "11111111-1111-4111-8111-111111111111"


def test_external_negative_signal_fails_closed() -> None:
    """When an unexpected negative signal occurs without supervisor timeout (e.g. OOM killer),

    it fails closed to state=FAILED with signal=abs(return_code).
    """
    res = parse_agy_output(
        stdout_content='{"conversation_id": "11111111-1111-4111-8111-111111111111"}',
        stderr_content="",
        return_code=-9,
        is_cancelled=False,
        is_audit_violated=False,
        supervisor_timed_out=False,
    )

    assert res.state == JobState.FAILED
    assert res.signal == 9
