"""Regression tests added after the independent review of D85.

The frozen contract in ``tests/test_quota_calibration.py`` calls
``compute_quota_estimate`` with hand-built dicts, so it never exercises the
production path where ``TokenUsage.from_dict`` materializes an absent
``total_tokens`` as ``0``. These tests cover that composition: a zero total is
only trusted when the component counters do not disagree with it.

These tests are additive and were written after the freeze commit; the frozen
acceptance contract was not touched.
"""

from agy_mcp.parse_result import _extract_tokens
from agy_mcp.usage import compute_quota_estimate


def test_payload_without_total_uses_component_counters() -> None:
    """agy payload with input/output but no total must not estimate zero."""
    tokens = _extract_tokens({"usage": {"input_tokens": 400, "output_tokens": 100}})
    data = tokens.to_dict()

    assert data["total_tokens"] == 0  # from_dict fills the absent total with 0
    result = compute_quota_estimate(data)

    assert result is not None
    assert result["tokens"] == 500
    assert result["five_hour_pct_points"] == round(500 / 1000 * 0.011, 4)


def test_zero_total_with_nonzero_components_uses_the_components() -> None:
    result = compute_quota_estimate(
        {"total_tokens": 0, "input_tokens": 400, "output_tokens": 100}
    )

    assert result is not None
    assert result["tokens"] == 500


def test_zero_total_with_zero_components_is_still_the_base_value() -> None:
    result = compute_quota_estimate(
        {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0}
    )

    assert result is not None
    assert result["tokens"] == 0
    assert result["weekly_pct_points"] == 0.0
    assert result["five_hour_pct_points"] == 0.0


def test_unusable_total_falls_back_to_components() -> None:
    """A negative/non-numeric total is not usable; the counters still are."""
    result = compute_quota_estimate(
        {"total_tokens": -5, "input_tokens": 400, "output_tokens": 100}
    )

    assert result is not None
    assert result["tokens"] == 500
