"""Typed per-account quota pools and windows (D107)."""

import json
from pathlib import Path

from agy_mcp.accounts.pools import (
    POOL_CLAUDE_GPT,
    POOL_GEMINI,
    WINDOW_FIVE_HOUR,
    WINDOW_WEEKLY,
    AccountUsage,
    account_usage_from_reading,
    pool_for_model,
)

FIXTURE = Path(__file__).parent / "fixtures" / "real_usage.json"


def _reading() -> dict:
    from agy_mcp.usage import parse_usage_payload

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    parsed = parse_usage_payload(payload)
    return {
        "available": True,
        "groups": parsed["groups"],
        "account": {"masked_email": "fa***@example.invalid", "auth_method": "consumer"},
        "cached": False,
        "fetched_at": "2026-11-03T00:00:00+00:00",
    }


def test_both_pools_and_windows_are_typed() -> None:
    usage = account_usage_from_reading("account-b", _reading())
    assert usage.available is True
    assert usage.account == "account-b"
    assert usage.masked_email == "fa***@example.invalid"
    assert {pool.name for pool in usage.pools} == {POOL_GEMINI, POOL_CLAUDE_GPT}
    gemini = usage.pool(POOL_GEMINI)
    assert gemini is not None
    assert gemini.weekly is not None and gemini.weekly.window == WINDOW_WEEKLY
    assert gemini.five_hour is not None and gemini.five_hour.window == WINDOW_FIVE_HOUR
    assert 0.0 <= gemini.weekly.remaining_fraction <= 1.0
    assert gemini.weekly.remaining_pct == round(gemini.weekly.remaining_fraction * 100.0, 4)
    assert gemini.weekly.reset_time_utc


def test_pool_for_model() -> None:
    assert pool_for_model("gemini-3.8-flash") == POOL_GEMINI
    assert pool_for_model("claude-4") == POOL_CLAUDE_GPT
    assert pool_for_model("gpt-5") == POOL_CLAUDE_GPT
    assert pool_for_model("unknown-model") is None
    assert pool_for_model(None) is None


def test_pool_for_model_single_pool_fallback() -> None:
    usage = AccountUsage(
        account="a",
        available=True,
        pools=account_usage_from_reading("a", _reading()).pools[:1],
    )
    assert usage.pool_for_model("unknown-model") is usage.pools[0]


def test_unavailable_reading_is_typed_not_raised() -> None:
    usage = account_usage_from_reading("a", {"available": False, "reason": "timeout"})
    assert usage.available is False
    assert usage.reason == "timeout"
    assert usage.pools == ()


def test_not_a_dict_is_unavailable() -> None:
    usage = account_usage_from_reading("a", None)
    assert usage.available is False
    assert usage.reason == "unrecognized_format"


def test_malformed_buckets_make_the_reading_unavailable() -> None:
    reading = {
        "available": True,
        "groups": [{"name": POOL_GEMINI, "buckets": [{"window": "weekly"}]}],
        "account": {"masked_email": "fa***@example.invalid"},
    }
    usage = account_usage_from_reading("a", reading)
    assert usage.available is False
    assert usage.reason == "unrecognized_format"


def test_missing_group_name_is_skipped() -> None:
    reading = {
        "available": True,
        "groups": [
            {"name": "", "buckets": []},
            {"name": POOL_GEMINI, "buckets": [{"window": "weekly", "remaining_fraction": 0.5}]},
        ],
    }
    usage = account_usage_from_reading("a", reading)
    assert usage.available is True
    assert [pool.name for pool in usage.pools] == [POOL_GEMINI]
    assert usage.pool(POOL_GEMINI).weekly.remaining_fraction == 0.5


def test_identity_is_carried_through() -> None:
    reading = _reading()
    reading["account"]["identity"] = "account_identity_mismatch"
    usage = account_usage_from_reading("a", reading)
    assert usage.identity == "account_identity_mismatch"
