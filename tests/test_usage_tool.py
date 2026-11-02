"""Tests for the read-only `agy_usage` MCP tool and low-quota flags (Item 3)."""

from pathlib import Path

import pytest

from agy_mcp.config import Config, ConfigError, load_config
from agy_mcp.manager import JobManager
from agy_mcp.runner import LocalProcessRunner
from agy_mcp.server import mcp

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"
MASKED_EMAIL = "fa***@example.invalid"


def _manager(tmp_path: Path, config: Config | None = None) -> JobManager:
    return JobManager(
        config=config or Config(),
        runner=LocalProcessRunner(executable=FAKE_AGY),
        base_state_dir=tmp_path / "state",
        home=tmp_path,
    )


def _set_scenario(tmp_path: Path, scenario: str) -> None:
    (tmp_path / ".fake_agy_usage_scenario").write_text(scenario, encoding="utf-8")


def test_usage_tool_payload_real_fixture(tmp_path: Path) -> None:
    """The tool payload exposes account, groups/buckets, flags, cache, availability."""
    _set_scenario(tmp_path, "usage_real")
    manager = _manager(tmp_path)

    payload = manager.usage()

    assert payload["available"] is True
    assert payload["reason"] is None
    assert payload["cached"] is False
    assert payload["account"] == {
        "masked_email": MASKED_EMAIL,
        "auth_method": "consumer",
    }
    assert len(payload["groups"]) == 2
    assert [b["window"] for b in payload["groups"][0]["buckets"]] == ["weekly", "5h"]
    assert all(
        bucket["low"] is False
        for group in payload["groups"]
        for bucket in group["buckets"]
    )


def test_usage_tool_zero_fixture_marks_low(tmp_path: Path) -> None:
    """0% remaining sets low=true on every bucket."""
    _set_scenario(tmp_path, "usage_zero")
    manager = _manager(tmp_path)

    payload = manager.usage()

    assert payload["available"] is True
    gemini = payload["groups"][0]
    assert gemini["name"] == "Gemini Models"
    assert all(bucket["low"] is True for bucket in gemini["buckets"])


def test_usage_tool_second_call_is_cached(tmp_path: Path) -> None:
    """The manager-level cache serves the second call with cached=true."""
    _set_scenario(tmp_path, "usage_real")
    manager = _manager(tmp_path)

    assert manager.usage()["cached"] is False
    assert manager.usage()["cached"] is True


def test_usage_thresholds_are_configurable(tmp_path: Path) -> None:
    """The 5% fixture is low by default but not low with 1% thresholds."""
    _set_scenario(tmp_path, "usage_low")

    default_manager = _manager(tmp_path)
    default_payload = default_manager.usage()
    assert all(
        bucket["low"] is True
        for group in default_payload["groups"]
        for bucket in group["buckets"]
    )

    relaxed = Config(low_quota_five_hour_pct=1.0, low_quota_weekly_pct=1.0)
    relaxed_manager = _manager(tmp_path, config=relaxed)
    relaxed_payload = relaxed_manager.usage()
    assert all(
        bucket["low"] is False
        for group in relaxed_payload["groups"]
        for bucket in group["buckets"]
    )


def test_usage_tool_unavailable_payload(tmp_path: Path) -> None:
    """Unavailable usage still returns a well-formed payload."""
    _set_scenario(tmp_path, "usage_garbled")
    manager = _manager(tmp_path)

    payload = manager.usage()
    assert payload["available"] is False
    assert payload["reason"] == "unrecognized_format"
    assert payload["groups"] == []
    # The account line may still be present even when the limit table is unusable.
    assert payload["account"]["masked_email"] == MASKED_EMAIL


def test_agy_usage_registered() -> None:
    """agy_usage is registered as a read-only parameterless tool."""
    tools = mcp._tool_manager.list_tools()
    by_name = {tool.name: tool for tool in tools}
    assert "agy_usage" in by_name
    tool = by_name["agy_usage"]
    assert tool.parameters == {
        "properties": {},
        "title": "agy_usageArguments",
        "type": "object",
    }
    assert "CRITICAL RULES:" in tool.description
    assert "usage" in tool.description.lower()


def test_config_usage_threshold_defaults(tmp_path: Path) -> None:
    """Config defaults are 15% five-hour and 10% weekly."""
    cfg = load_config(path=tmp_path / "missing.toml")
    assert cfg.low_quota_five_hour_pct == 15.0
    assert cfg.low_quota_weekly_pct == 10.0


def test_config_usage_thresholds_validated(tmp_path: Path) -> None:
    """Thresholds accept numbers in 0..100 and reject bools, strings, and out-of-range."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        "low_quota_five_hour_pct = 20\nlow_quota_weekly_pct = 5.5\n",
        encoding="utf-8",
    )
    cfg = load_config(path=cfg_file)
    assert cfg.low_quota_five_hour_pct == 20.0
    assert cfg.low_quota_weekly_pct == 5.5

    for bad in ("true", "'15'", "101", "-1"):
        cfg_file.write_text(f"low_quota_five_hour_pct = {bad}\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(path=cfg_file)
