"""``agy_usage`` can name an account and reports which one it read (D116).

The no-argument behavior stays the historical default reading; a disabled
implicit default falls back to the first enabled account by name and reports it
through the additive ``account_name`` key.
"""

import json
from pathlib import Path

import pytest

from agy_mcp.accounts.registry import DefaultAccountConfig, parse_accounts
from agy_mcp.config import Config
from agy_mcp.jobs import list_jobs
from agy_mcp.manager import JobManager, ManagerError
from agy_mcp.runner import LocalProcessRunner

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"
FIXTURE = Path(__file__).parent / "fixtures" / "real_usage.json"
EMAIL_A = "avery.chen17@example.com"
EMAIL_B = "blair.nguyen29@example.com"


def _payload(weekly: float) -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for group in payload["command"]["data"]["groups"]:
        for bucket in group["buckets"]:
            if bucket["window"] == "weekly":
                bucket["remaining_fraction"] = weekly
    return payload


def _write_home(home: Path, weekly: float, email: str) -> None:
    home.mkdir(mode=0o700, exist_ok=True)
    home.chmod(0o700)
    (home / ".fake_agy_usage.json").write_text(
        json.dumps(_payload(weekly)), encoding="utf-8"
    )
    (home / ".fake_agy_email").write_text(email + "\n", encoding="utf-8")


def _manager(
    tmp_path: Path,
    accounts: list[dict] | None = None,
    default_account: DefaultAccountConfig | None = None,
) -> tuple[JobManager, Path]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    config = Config(
        accounts=parse_accounts(accounts or [], max_accounts=5),
        default_account=default_account,
        max_accounts=5,
    )
    manager = JobManager(
        config=config,
        runner=LocalProcessRunner(executable=FAKE_AGY),
        base_state_dir=tmp_path / "state",
        home=home,
        settle_window_seconds=0.05,
        poll_interval_seconds=0.05,
    )
    return manager, home


def _two_accounts(tmp_path: Path) -> list[dict]:
    account_a = tmp_path / "account-a"
    account_b = tmp_path / "account-b"
    _write_home(account_a, 0.50, EMAIL_A)
    _write_home(account_b, 0.25, EMAIL_B)
    return [
        {"name": "account-a", "home": str(account_a)},
        {"name": "account-b", "home": str(account_b)},
    ]


def test_no_argument_keeps_the_default_reading_shape(tmp_path: Path) -> None:
    manager, home = _manager(tmp_path)
    _write_home(home, 0.90, EMAIL_A)

    payload = manager.usage()

    assert payload["available"] is True
    assert "account_name" not in payload
    assert "groups" in payload


def test_explicit_account_reads_that_account_and_reports_it(tmp_path: Path) -> None:
    manager, home = _manager(tmp_path, _two_accounts(tmp_path))
    _write_home(home, 0.90, EMAIL_A)

    payload = manager.usage("account-b")

    assert payload["available"] is True
    assert payload["account_name"] == "account-b"
    masked = payload["account"]["masked_email"]
    assert masked is not None and EMAIL_B not in json.dumps(payload)
    weekly = [
        bucket
        for group in payload["groups"]
        for bucket in group["buckets"]
        if bucket["window"] == "weekly"
    ]
    assert weekly and all(bucket["remaining_fraction"] == pytest.approx(0.25) for bucket in weekly)


def test_disabled_default_falls_back_to_the_first_enabled_name(tmp_path: Path) -> None:
    manager, home = _manager(
        tmp_path, _two_accounts(tmp_path), DefaultAccountConfig(enabled=False)
    )
    _write_home(home, 0.90, EMAIL_A)

    payload = manager.usage()

    assert payload["account_name"] == "account-a"
    assert payload["available"] is True


def test_explicit_default_is_refused_when_disabled(tmp_path: Path) -> None:
    manager, _ = _manager(
        tmp_path, _two_accounts(tmp_path), DefaultAccountConfig(enabled=False)
    )
    with pytest.raises(ManagerError) as exc:
        manager.usage("default")
    assert exc.value.code == "default_account_disabled"


def test_no_enabled_account_is_a_stable_code(tmp_path: Path) -> None:
    manager, _ = _manager(
        tmp_path,
        [{"name": "account-b", "home": str(tmp_path / "account-b"), "enabled": False}],
        DefaultAccountConfig(enabled=False),
    )
    with pytest.raises(ManagerError) as exc:
        manager.usage()
    assert exc.value.code == "account_none_enabled"


def test_unknown_account_name_is_account_not_found(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path, _two_accounts(tmp_path))
    with pytest.raises(ManagerError) as exc:
        manager.usage("account-z")
    assert exc.value.code == "account_not_found"


def test_usage_does_not_create_a_job(tmp_path: Path) -> None:
    manager, home = _manager(tmp_path, _two_accounts(tmp_path))
    _write_home(home, 0.90, EMAIL_A)
    manager.usage("account-a")
    assert list_jobs(manager.state_dir) == []
