"""Duplicate-identity safety: one login is one quota pool (D116).

The 2026-11-05 observation: the implicit default authenticated through the
shared desktop keyring as the same login as the configured ``account-b``, so the
scheduler counted one quota pool as two accounts. These tests use the fake agy
and an injected clock; no sleeps.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from agy_mcp.accounts.doctor import doctor_report, render_report
from agy_mcp.accounts.registry import (
    DefaultAccountConfig,
    build_registry,
    parse_accounts,
)
from agy_mcp.accounts.selection import AccountSelector
from agy_mcp.config import Config
from agy_mcp.usage import mask_email as reading_mask_email

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"
FIXTURE = Path(__file__).parent / "fixtures" / "real_usage.json"
SHARED_EMAIL = "jordan.lee81@example.com"
OTHER_EMAIL = "casey.morgan42@example.com"
NOW = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)


def _payload(weekly: float) -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for group in payload["command"]["data"]["groups"]:
        for bucket in group["buckets"]:
            bucket["remaining_fraction"] = weekly if bucket["window"] == "weekly" else 0.9
            if bucket["window"] == "weekly":
                bucket["reset_time"] = "2026-11-08T12:00:00Z"
            else:
                bucket["reset_time"] = "2026-11-05T17:00:00Z"
    return payload


def _write_home(home: Path, weekly: float, email: str) -> None:
    home.mkdir(mode=0o700, exist_ok=True)
    home.chmod(0o700)
    (home / ".fake_agy_usage.json").write_text(
        json.dumps(_payload(weekly)), encoding="utf-8"
    )
    (home / ".fake_agy_email").write_text(email + "\n", encoding="utf-8")


def _account(tmp_path: Path, name: str, weekly: float, email: str, **extra) -> dict:
    home = tmp_path / name
    _write_home(home, weekly, email)
    return {"name": name, "home": str(home), "expected_email": email, **extra}


def _selector(
    tmp_path: Path,
    accounts: list[dict],
    default_account: DefaultAccountConfig | None = None,
    *,
    default_weekly: float = 0.9,
    default_email: str | None = None,
) -> AccountSelector:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    if default_email is not None:
        _write_home(home, default_weekly, default_email)
    parsed = parse_accounts(accounts, max_accounts=5)
    config = Config(
        accounts=parsed,
        default_account=default_account,
        max_accounts=5,
        model_allowlist=["gemini-3.8-flash-medium"],
        allowed_workdir_roots=[str(tmp_path)],
    )
    return AccountSelector(
        config,
        build_registry(config, home),
        home,
        str(FAKE_AGY),
        state_dir=tmp_path / "state",
        now_func=lambda: NOW,
    )


def test_two_verified_accounts_with_one_login_share_a_pool(tmp_path: Path) -> None:
    selector = _selector(
        tmp_path,
        [
            _account(tmp_path, "account-a", 0.90, SHARED_EMAIL),
            _account(tmp_path, "account-b", 0.02, SHARED_EMAIL),
        ],
        DefaultAccountConfig(enabled=False),
    )
    selection = selector.select("gemini-3.8-flash-medium", 240)

    # One pool means the minimum remaining (2%) governs both names, so neither
    # can take a job whose estimate needs more. Counted separately, account-a's
    # 90% would have won.
    assert selection.account is None
    assert selection.result.exhausted is True
    states = {state.name: state for state in selection.states}
    assert states["account-a"].pool_key == states["account-b"].pool_key
    assert states["account-a"].pool_key.startswith("identity:")


def test_verified_accounts_with_distinct_logins_stay_separate(tmp_path: Path) -> None:
    selector = _selector(
        tmp_path,
        [
            _account(tmp_path, "account-a", 0.90, OTHER_EMAIL),
            _account(tmp_path, "account-b", 0.02, SHARED_EMAIL),
        ],
        DefaultAccountConfig(enabled=False),
    )
    selection = selector.select("gemini-3.8-flash-medium", 240)
    assert selection.account is not None
    assert selection.account.name == "account-a"
    states = {state.name: state for state in selection.states}
    assert states["account-a"].pool_key != states["account-b"].pool_key


def test_verified_default_and_a_declared_account_share_a_pool(tmp_path: Path) -> None:
    selector = _selector(
        tmp_path,
        [_account(tmp_path, "account-b", 0.02, SHARED_EMAIL)],
        DefaultAccountConfig(expected_email=SHARED_EMAIL),
        default_weekly=0.90,
        default_email=SHARED_EMAIL,
    )
    selection = selector.select("gemini-3.8-flash-medium", 240)
    assert selection.account is None
    states = {state.name: state for state in selection.states}
    assert states["default"].pool_key == states["account-b"].pool_key


def test_observed_unverified_default_merges_with_a_verified_account(
    tmp_path: Path,
) -> None:
    """The 2026-11-05 case: default (no expectation) and a verified account-b.

    The mask shapes differ (legacy first-two vs identity first3+tail2), so the
    full-email digest is what proves they are one login. Counting them separately
    would let the stale/keyring default reading win with 90%.
    """
    selector = _selector(
        tmp_path,
        [_account(tmp_path, "account-b", 0.02, SHARED_EMAIL)],
        default_weekly=0.90,
        default_email=SHARED_EMAIL,
    )
    selection = selector.select("gemini-3.8-flash-medium", 240)
    assert selection.account is None
    states = {state.name: state for state in selection.states}
    assert states["default"].pool_key == states["account-b"].pool_key


def test_doctor_reports_the_observed_shared_keyring_collision(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write_home(home, 0.90, SHARED_EMAIL)
    account_b = tmp_path / "account-b"
    _write_home(account_b, 0.90, SHARED_EMAIL)
    config = Config(
        accounts=parse_accounts(
            [{"name": "account-b", "home": str(account_b)}], max_accounts=5
        ),
        max_accounts=5,
    )

    report = doctor_report(config, home, str(FAKE_AGY))
    checks = {(finding.account, finding.check): finding for finding in report["findings"]}
    shared_keyring = checks[("default", "shared_keyring")]
    assert shared_keyring.ok is True and shared_keyring.warn is True
    assert "shared keyring" in shared_keyring.detail

    shared_identity = checks[("default, account-b", "shared_identity")]
    assert shared_identity.ok is True and shared_identity.warn is True
    assert reading_mask_email(SHARED_EMAIL) in shared_identity.detail

    rendered = render_report(report)
    assert SHARED_EMAIL not in rendered
    assert "warn" in rendered
    # Advisories never fail the doctor.
    assert report["ok"] is True


def test_doctor_omits_the_shared_keyring_warning_with_an_expectation(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write_home(home, 0.90, SHARED_EMAIL)
    account_b = tmp_path / "account-b"
    _write_home(account_b, 0.90, OTHER_EMAIL)
    config = Config(
        accounts=parse_accounts(
            [{"name": "account-b", "home": str(account_b)}], max_accounts=5
        ),
        default_account=DefaultAccountConfig(expected_email=SHARED_EMAIL),
        max_accounts=5,
    )
    report = doctor_report(config, home, str(FAKE_AGY))
    assert not any(finding.check == "shared_keyring" for finding in report["findings"])
    assert not any(finding.check == "shared_identity" for finding in report["findings"])


def test_doctor_reports_no_enabled_account_as_a_problem(tmp_path: Path) -> None:
    account_b = tmp_path / "account-b"
    account_b.mkdir(mode=0o700)
    config = Config(
        accounts=parse_accounts(
            [{"name": "account-b", "home": str(account_b), "enabled": False}],
            max_accounts=5,
        ),
        default_account=DefaultAccountConfig(enabled=False),
        max_accounts=5,
    )
    report = doctor_report(config, tmp_path / "home", str(FAKE_AGY))
    assert report["ok"] is False
    assert any(
        finding.check == "registry" and "account_none_enabled" in finding.detail
        for finding in report["findings"]
    )
