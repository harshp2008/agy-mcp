"""Per-account `/usage` cache, identity hook and usage-log attribution (D107)."""

import json
from pathlib import Path

from agy_mcp.accounts.pools import POOL_GEMINI, account_usage_from_reading
from agy_mcp.jobs import create_job
from agy_mcp.usage import UsageCache, get_usage, record_usage, usage_cache_key

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"
FIXTURE = Path(__file__).parent / "fixtures" / "real_usage.json"


def _payload(gemini_weekly: float) -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for group in payload["command"]["data"]["groups"]:
        if group["name"] == POOL_GEMINI:
            for bucket in group["buckets"]:
                if bucket["window"] == "weekly":
                    bucket["remaining_fraction"] = gemini_weekly
    return payload


def _account_home(tmp_path: Path, name: str, weekly: float | None = None) -> Path:
    home = tmp_path / name
    home.mkdir(mode=0o700)
    if weekly is not None:
        (home / ".fake_agy_usage.json").write_text(
            json.dumps(_payload(weekly)), encoding="utf-8"
        )
    return home


def _read(home: Path, **kwargs):
    return get_usage(
        FAKE_AGY,
        home=home,
        cache=kwargs.pop("cache", UsageCache()),
        env_overrides={"FAKE_AGY_SCENARIO": "usage_real"},
        **kwargs,
    )


def _gemini_weekly(reading: dict) -> float:
    return account_usage_from_reading("a", reading).pool(POOL_GEMINI).weekly.remaining_fraction


def test_cache_key_changes_with_account_but_not_without(tmp_path: Path) -> None:
    base = usage_cache_key(FAKE_AGY)
    assert base == usage_cache_key(FAKE_AGY, home=tmp_path, account_name=None)
    assert usage_cache_key(FAKE_AGY, home=tmp_path, account_name="a") != base
    assert usage_cache_key(
        FAKE_AGY, home=tmp_path, account_name="a"
    ) != usage_cache_key(FAKE_AGY, home=tmp_path, account_name="b")


def test_two_accounts_do_not_share_a_cache_entry(tmp_path: Path) -> None:
    home_a = _account_home(tmp_path, "a", weekly=0.9)
    home_b = _account_home(tmp_path, "b", weekly=0.1)
    cache = UsageCache()
    reading_a = _read(home_a, cache=cache, account_name="a", now_monotonic=1000.0)
    reading_b = _read(home_b, cache=cache, account_name="b", now_monotonic=1000.0)
    assert reading_a["cached"] is False
    assert reading_b["cached"] is False
    assert _gemini_weekly(reading_a) == 0.9
    assert _gemini_weekly(reading_b) == 0.1

    cached_a = _read(home_a, cache=cache, account_name="a", now_monotonic=1001.0)
    assert cached_a["cached"] is True
    assert _gemini_weekly(cached_a) == 0.9


def test_per_account_payload_drives_the_reading(tmp_path: Path) -> None:
    home = _account_home(tmp_path, "a", weekly=0.42)
    reading = _read(home, account_name="a")
    usage = account_usage_from_reading("a", reading)
    assert usage.available is True
    assert usage.pool(POOL_GEMINI).weekly.remaining_fraction == 0.42


def test_identity_match(tmp_path: Path) -> None:
    home = _account_home(tmp_path, "a", weekly=0.5)
    (home / ".fake_agy_email").write_text("alice@example.com\n", encoding="utf-8")
    reading = _read(home, account_name="a", expected_email="alice@example.com")
    assert reading["available"] is True
    assert reading["account"]["identity"] == "matched"


def test_identity_hash_match(tmp_path: Path) -> None:
    from agy_mcp.accounts.identity import email_sha256

    home = _account_home(tmp_path, "a", weekly=0.5)
    (home / ".fake_agy_email").write_text("alice@example.com\n", encoding="utf-8")
    reading = _read(
        home,
        account_name="a",
        expected_email_sha256=email_sha256("alice@example.com"),
    )
    assert reading["available"] is True
    assert reading["account"]["identity"] == "matched"


def test_identity_mismatch_is_unavailable_and_masked(tmp_path: Path) -> None:
    home = _account_home(tmp_path, "a", weekly=0.5)
    (home / ".fake_agy_email").write_text("alice@example.com\n", encoding="utf-8")
    reading = _read(home, account_name="a", expected_email="bob@example.com")
    assert reading["available"] is False
    assert reading["reason"] == "account_identity_mismatch"
    dumped = json.dumps(reading)
    assert "alice@example.com" not in dumped
    assert reading["account"]["masked_email"] == "al***@example.com"


def test_no_expectation_keeps_the_old_reading_shape(tmp_path: Path) -> None:
    home = _account_home(tmp_path, "a", weekly=0.5)
    reading = _read(home, account_name="a")
    assert "identity" not in reading["account"]


def test_not_signed_in_account_is_unavailable(tmp_path: Path) -> None:
    home = _account_home(tmp_path, "a")
    (home / ".fake_agy_usage_scenario").write_text("usage_auth_required", encoding="utf-8")
    reading = get_usage(
        FAKE_AGY,
        home=home,
        cache=UsageCache(),
        account_name="a",
        auth_error_patterns=["not logged in"],
    )
    assert reading["available"] is False
    assert reading["reason"] == "auth_required"


def _job(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return create_job(
        repo_path=repo,
        title="usage record",
        prompt="do work",
        model="gemini-3.8-flash-medium",
        max_minutes=10,
        verify=None,
        verify_timeout_seconds=900,
        base_state_dir=tmp_path / "state",
    )


def test_usage_record_without_account_is_unchanged(tmp_path: Path) -> None:
    job = _job(tmp_path)
    path = tmp_path / "usage.jsonl"
    record_usage(path, job, 1.0, {"total_tokens": 1})
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert "account" not in record
    assert "masked_email" not in record


def test_usage_record_with_account_adds_masked_fields_only(tmp_path: Path) -> None:
    job = _job(tmp_path)
    path = tmp_path / "usage.jsonl"
    record_usage(
        path,
        job,
        1.0,
        {"total_tokens": 1},
        account="account-b",
        masked_email="ali***om@example.com",
    )
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert record["account"] == "account-b"
    assert record["masked_email"] == "ali***om@example.com"
    assert "alice@example.com" not in json.dumps(record)
