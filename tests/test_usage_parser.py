"""Tests for parsing agy `/usage` output (real format, fail-soft variants)."""

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from agy_mcp.usage import mask_email, parse_auth_line, parse_usage_output, parse_usage_payload

FIXTURES = Path(__file__).parent / "fixtures"
REAL_USAGE = FIXTURES / "real_usage.json"
AUTH_LINE = FIXTURES / "real_usage_auth_line.txt"


def _fixture_payload() -> dict:
    return json.loads(REAL_USAGE.read_text(encoding="utf-8"))


def test_parse_real_fixture_structured_primary() -> None:
    """The real captured /usage payload parses into groups and buckets."""
    reading = parse_usage_output(REAL_USAGE.read_text(encoding="utf-8"))

    assert reading["available"] is True
    assert len(reading["groups"]) == 2

    gemini = reading["groups"][0]
    assert gemini["name"] == "Gemini Models"
    assert [b["window"] for b in gemini["buckets"]] == ["weekly", "5h"]

    weekly = gemini["buckets"][0]
    assert weekly["remaining_fraction"] == 0.6406044960021973
    assert weekly["reset_time_utc"] == "2026-11-01T09:54:57Z"
    assert isinstance(weekly["resets_in_seconds"], int)

    five_hour = gemini["buckets"][1]
    assert five_hour["remaining_fraction"] == 0.6454957723617554
    assert five_hour["reset_time_utc"] == "2026-10-25T19:54:57Z"

    claude = reading["groups"][1]
    assert claude["name"] == "Claude and GPT models"
    assert all(b["remaining_fraction"] == 1.0 for b in claude["buckets"])

    # Text lines and structured data agree for the real fixture.
    assert reading["cross_check"] == "ok"


def test_parse_zero_variant() -> None:
    """A 0% limit variant parses and reports a zero remaining fraction."""
    payload = _fixture_payload()
    for group in payload["command"]["data"]["groups"]:
        for bucket in group["buckets"]:
            bucket["remaining_fraction"] = 0.0
    lines = []
    for line in payload["response"].splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        parts[2] = "0%"
        lines.append("\t".join(parts))
    payload["response"] = "\n".join(lines) + "\n"

    reading = parse_usage_output(json.dumps(payload))
    assert reading["available"] is True
    for group in reading["groups"]:
        for bucket in group["buckets"]:
            assert bucket["remaining_fraction"] == 0.0
    assert reading["cross_check"] == "ok"


def test_parse_garbled_variant_fails_soft() -> None:
    """Text-only garbage with no structured groups returns unrecognized_format."""
    reading = parse_usage_output(
        json.dumps({"status": "SUCCESS", "response": "this is not a usage table\n"})
    )
    assert reading == {"available": False, "reason": "unrecognized_format"}


def test_parse_empty_variant_fails_soft() -> None:
    """Empty output returns unrecognized_format and never raises."""
    assert parse_usage_output("") == {
        "available": False,
        "reason": "unrecognized_format",
    }
    assert parse_usage_output("{}") == {
        "available": False,
        "reason": "unrecognized_format",
    }
    assert parse_usage_output("not json at all") == {
        "available": False,
        "reason": "unrecognized_format",
    }


def test_parse_structured_but_garbled_buckets_fails_soft() -> None:
    """Malformed structured data does not raise into the caller."""
    payload = {
        "command": {"data": {"groups": [{"name": "Gemini Models", "buckets": "bad"}]}},
        "response": "",
    }
    reading = parse_usage_output(json.dumps(payload))
    assert reading == {"available": False, "reason": "unrecognized_format"}

    weird = {"command": None, "response": None}
    assert parse_usage_payload(weird) == {
        "available": False,
        "reason": "unrecognized_format",
    }


def test_text_only_fallback_parses() -> None:
    """When structured data is absent, the tab-separated text is still usable."""
    payload = _fixture_payload()
    payload["command"]["data"] = {}
    reading = parse_usage_output(json.dumps(payload))
    assert reading["available"] is True
    assert reading["cross_check"] == "text_only"
    assert len(reading["groups"]) == 2


def test_reset_seconds_are_computed_from_now() -> None:
    """resets_in_seconds is derived from the reset timestamp relative to now."""
    now = datetime(2026, 10, 25, 18, 54, 57, tzinfo=timezone.utc)
    reading = parse_usage_output(REAL_USAGE.read_text(encoding="utf-8"), now=now)
    five_hour = reading["groups"][0]["buckets"][1]
    assert five_hour["resets_in_seconds"] == 3600
    weekly = reading["groups"][0]["buckets"][0]
    assert weekly["resets_in_seconds"] == 6 * 24 * 3600 + 15 * 3600
    # Past reset times clamp to zero rather than going negative.
    later = datetime(2027, 1, 1, tzinfo=timezone.utc)
    reading2 = parse_usage_output(REAL_USAGE.read_text(encoding="utf-8"), now=later)
    assert all(
        b["resets_in_seconds"] == 0
        for g in reading2["groups"]
        for b in g["buckets"]
    )


def test_auth_line_parsing_and_masking() -> None:
    """The real applyAuthResult line is parsed and the email is masked."""
    raw = AUTH_LINE.read_text(encoding="utf-8")
    email, method = parse_auth_line(raw)
    assert email == "REDACTED@example.com"
    assert method == "consumer"
    assert mask_email(email) == "RE***@example.com"
    assert mask_email("ab@b.com") == "***@b.com"
    assert mask_email("a@b.com") == "***@b.com"
    assert mask_email("abc@b.com") == "ab***@b.com"
    assert mask_email("") == "***"
    assert mask_email("not-an-email") == "***"


def test_cross_check_mismatch_is_reported_not_fatal() -> None:
    """Structured values win, but a text mismatch is surfaced."""
    payload = copy.deepcopy(_fixture_payload())
    payload["command"]["data"]["groups"][0]["buckets"][0]["remaining_fraction"] = 0.5
    reading = parse_usage_output(json.dumps(payload))
    assert reading["available"] is True
    assert reading["groups"][0]["buckets"][0]["remaining_fraction"] == 0.5
    assert reading["cross_check"] == "mismatch"
