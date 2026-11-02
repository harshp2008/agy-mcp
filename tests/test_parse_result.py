"""Table-driven unit tests for result parsing precedence per SPEC section 15."""

from pathlib import Path

import pytest

from agy_mcp.parse_result import ParsedResult, parse_agy_output
from agy_mcp.states import JobState

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture():
    def _loader(filename: str) -> str:
        return (FIXTURES_DIR / filename).read_text()

    return _loader


# 36 Table-driven test cases covering all 9 precedence levels and boundary edge cases
TEST_CASES = [
    # 1. Cancellation (top precedence)
    {
        "id": "cancel_on_rc_0",
        "stdout": '{"status": "SUCCESS", "response": "done"}',
        "stderr": "",
        "rc": 0,
        "cancelled": True,
        "audit_violated": False,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.CANCELLED,
    },
    {
        "id": "cancel_overrides_rc_1",
        "stdout": "",
        "stderr": "Error flag",
        "rc": 1,
        "cancelled": True,
        "audit_violated": False,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.CANCELLED,
    },
    {
        "id": "cancel_overrides_signal",
        "stdout": "",
        "stderr": "",
        "rc": -15,
        "cancelled": True,
        "audit_violated": False,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.CANCELLED,
    },
    {
        "id": "cancel_overrides_audit_violation",
        "stdout": "",
        "stderr": "",
        "rc": 0,
        "cancelled": True,
        "audit_violated": True,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.CANCELLED,
    },
    # 2. Audit Violation (precedes signal, rc, timeout, output)
    {
        "id": "audit_violation_rc_0",
        "stdout": '{"status": "SUCCESS"}',
        "stderr": "",
        "rc": 0,
        "cancelled": False,
        "audit_violated": True,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.NEEDS_REVIEW,
        "expected_reason": "audit_violation",
    },
    {
        "id": "audit_violation_overrides_signal",
        "stdout": "",
        "stderr": "",
        "rc": -9,
        "cancelled": False,
        "audit_violated": True,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.NEEDS_REVIEW,
        "expected_reason": "audit_violation",
    },
    {
        "id": "audit_violation_overrides_rc_1",
        "stdout": "",
        "stderr": "syntax error",
        "rc": 1,
        "cancelled": False,
        "audit_violated": True,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.NEEDS_REVIEW,
        "expected_reason": "audit_violation",
    },
    {
        "id": "audit_violation_overrides_decision",
        "stdout": '{"status": "SUCCESS", "response": "NEEDS_DECISION: foo"}',
        "stderr": "",
        "rc": 0,
        "cancelled": False,
        "audit_violated": True,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.NEEDS_REVIEW,
        "expected_reason": "audit_violation",
    },
    # 3. Negative Signals (rc < 0)
    {
        "id": "sigterm_neg_15",
        "stdout": "",
        "stderr": "",
        "rc": -15,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.FAILED,
        "expected_signal": 15,
    },
    {
        "id": "sigkill_neg_9",
        "stdout": "",
        "stderr": "",
        "rc": -9,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.FAILED,
        "expected_signal": 9,
    },
    {
        "id": "sigsegv_neg_11",
        "stdout": "",
        "stderr": "Segmentation fault",
        "rc": -11,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.FAILED,
        "expected_signal": 11,
    },
    {
        "id": "sighup_neg_1",
        "stdout": "",
        "stderr": "",
        "rc": -1,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.FAILED,
        "expected_signal": 1,
    },
    # 4. CLI Flag / Invocation Error (rc 1 or 2)
    {
        "id": "rc_1_effort_mismatch",
        "stdout": "",
        "stderr": "Error: flag --effort conflicts with model effort",
        "rc": 1,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.FAILED,
        "expected_err_substr": "flag --effort conflicts",
    },
    {
        "id": "rc_2_usage_error",
        "stdout": "",
        "stderr": "Usage: agy [OPTIONS]",
        "rc": 2,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.FAILED,
        "expected_err_substr": "Usage: agy",
    },
    {
        "id": "rc_1_empty_stderr",
        "stdout": "",
        "stderr": "",
        "rc": 1,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.FAILED,
        "expected_err_substr": "error code 1",
    },
    {
        "id": "rc_1_ignores_success_stdout",
        "stdout": '{"status": "SUCCESS"}',
        "stderr": "Unexpected failure",
        "rc": 1,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.FAILED,
    },
    # 5. Timeout
    {
        "id": "supervisor_timeout_flag",
        "stdout": "",
        "stderr": "",
        "rc": 0,
        "cancelled": False,
        "audit_violated": False,
        "timeout": True,
        "commits": 0,
        "expected_state": JobState.TIMEOUT,
    },
    {
        "id": "stderr_print_timeout_marker",
        "stdout": '{"conversation_id": "11111111-1111-4111-8111-111111111111"}',
        "stderr": "[agy] print timeout after 120s with turn in progress; returning partial output",
        "rc": 0,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.TIMEOUT,
        "expected_cid": "11111111-1111-4111-8111-111111111111",
    },
    {
        "id": "timeout_with_tokens",
        "stdout": '{"conversation_id": "c-99", "tokens": {"total_tokens": 500}}',
        "stderr": "[agy] print timeout after 60s",
        "rc": 0,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.TIMEOUT,
        "expected_tokens": 500,
    },
    {
        "id": "timeout_empty_stdout",
        "stdout": "",
        "stderr": "[agy] print timeout after 180s",
        "rc": 0,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.TIMEOUT,
    },
    # 6. Decision Marker (^NEEDS_DECISION:)
    {
        "id": "decision_simple",
        "stdout": (
            '{"status": "SUCCESS", "response": "Work done\\nNEEDS_DECISION: Proceed? | y,n | y"}'
        ),
        "stderr": "",
        "rc": 0,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 1,
        "expected_state": JobState.NEEDS_DECISION,
        "expected_prompt": "Proceed? | y,n | y",
    },
    {
        "id": "decision_trailing_blank_lines",
        "stdout": (
            '{"status": "SUCCESS", "response": "Summary\\nNEEDS_DECISION: Pick database\\n\\n"}'
        ),
        "stderr": "",
        "rc": 0,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.NEEDS_DECISION,
        "expected_prompt": "Pick database",
    },
    {
        "id": "decision_in_middle_is_not_decision",
        "stdout": (
            '{"status": "SUCCESS", "response": "NEEDS_DECISION: Old prompt\\nFinal summary line"}'
        ),
        "stderr": "",
        "rc": 0,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 1,
        "expected_state": JobState.DONE,
    },
    {
        "id": "decision_fixture",
        "fixture": "needs_decision.json",
        "stderr": "",
        "rc": 0,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 1,
        "expected_state": JobState.NEEDS_DECISION,
        "expected_prompt": "Which migration tool should be used? | alembic, sql | alembic",
    },
    # 7. Permission Blockage
    {
        "id": "permission_blocked_zero_commits_empty_resp",
        "stdout": '{"status": "SUCCESS", "response": "", "denied_actions": [{"action": "cmd"}]}',
        "stderr": "",
        "rc": 0,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.NEEDS_REVIEW,
        "expected_reason": "blocked_by_permission",
    },
    {
        "id": "permission_blocked_whitespace_resp",
        "stdout": (
            '{"status": "SUCCESS", "response": "  \\n  ", "denied_actions": [{"action": "cmd"}]}'
        ),
        "stderr": "",
        "rc": 0,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.NEEDS_REVIEW,
        "expected_reason": "blocked_by_permission",
    },
    {
        "id": "denial_with_commits_is_done",
        "stdout": '{"status": "SUCCESS", "response": "", "denied_actions": [{"action": "cmd"}]}',
        "stderr": "",
        "rc": 0,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 1,
        "expected_state": JobState.DONE,
    },
    {
        "id": "denial_with_response_is_done",
        "fixture": "denial_with_progress.json",
        "stderr": "",
        "rc": 0,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 0,
        "files_modified": 1,
        "expected_state": JobState.DONE,
    },
    {
        "id": "denial_blocked_fixture",
        "fixture": "denial_blocked.json",
        "stderr": "",
        "rc": 0,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.NEEDS_REVIEW,
        "expected_reason": "blocked_by_permission",
    },
    # 8. Success (status == SUCCESS)
    {
        "id": "success_clean_fixture",
        "fixture": "success_clean.json",
        "stderr": "",
        "rc": 0,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 1,
        "expected_state": JobState.DONE,
        "expected_cid": "c23620ff-d233-4205-8593-50bae5d2bdb3",
        "expected_tokens": 6926,
    },
    {
        "id": "success_preceded_by_log_lines",
        "stdout": "INFO: initialising\nDEBUG: running\n"
        '{"status": "SUCCESS", "response": "all good"}\n',
        "stderr": "",
        "rc": 0,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 2,
        "expected_state": JobState.DONE,
    },
    {
        "id": "success_with_warnings",
        "stdout": '{"status": "SUCCESS", "response": "ok", "warnings": ["dirty warning"]}',
        "stderr": "",
        "rc": 0,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 1,
        "expected_state": JobState.DONE,
    },
    # 9. Unparseable / Other Status / Truncation
    {
        "id": "empty_stdout_rc_0",
        "stdout": "",
        "stderr": "",
        "rc": 0,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.FAILED,
        "expected_parse_error": True,
    },
    {
        "id": "truncated_json_mid_write",
        "stdout": '{"status": "SUCC',
        "stderr": "",
        "rc": 0,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.FAILED,
        "expected_parse_error": True,
    },
    {
        "id": "non_json_plain_text",
        "stdout": "Fatal error: memory corruption in runner",
        "stderr": "",
        "rc": 0,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.FAILED,
        "expected_parse_error": True,
    },
    {
        "id": "unknown_future_status_fixture",
        "fixture": "unknown_status.json",
        "stderr": "",
        "rc": 0,
        "cancelled": False,
        "audit_violated": False,
        "timeout": False,
        "commits": 0,
        "expected_state": JobState.FAILED,
        "expected_parse_error": True,
    },
]


@pytest.mark.parametrize("case", TEST_CASES, ids=lambda c: c["id"])
def test_parse_agy_output_precedence(case: dict, load_fixture):
    """Verify parse_agy_output implements the exact 9 precedence levels from SPEC section 15."""
    stdout = load_fixture(case["fixture"]) if "fixture" in case else case["stdout"]

    files_modified_count = case.get("files_modified", 0)
    res: ParsedResult = parse_agy_output(
        stdout_content=stdout,
        stderr_content=case["stderr"],
        return_code=case["rc"],
        is_cancelled=case["cancelled"],
        is_audit_violated=case["audit_violated"],
        supervisor_timed_out=case["timeout"],
        commits_count=case["commits"],
        files_modified_count=files_modified_count,
    )

    assert res.state == case["expected_state"]

    if "expected_reason" in case:
        assert res.review_reason == case["expected_reason"]

    if "expected_signal" in case:
        assert res.signal == case["expected_signal"]

    if "expected_prompt" in case:
        assert res.decision_prompt == case["expected_prompt"]

    if "expected_cid" in case:
        assert res.conversation_id == case["expected_cid"]

    if "expected_tokens" in case:
        assert res.tokens.total_tokens == case["expected_tokens"]

    if "expected_err_substr" in case:
        assert case["expected_err_substr"] in (res.error_message or "")

    if "expected_parse_error" in case:
        assert res.parse_error is True
