"""End-to-end account selection, pinning and single-account equivalence (D102–D110)."""

import json
from pathlib import Path

import pytest

from agy_mcp.accounts.registry import parse_accounts
from agy_mcp.config import Config
from agy_mcp.jobs import get_job
from agy_mcp.manager import JobManager, ManagerError
from agy_mcp.runner import LocalProcessRunner
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"
FIXTURE = Path(__file__).parent / "fixtures" / "real_usage.json"
FULL_EMAIL = "account.owner@example.com"


def _payload(
    gemini_weekly: float,
    gemini_five: float = 0.9,
    reset: str = "2026-11-08T12:00:00Z",
) -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for group in payload["command"]["data"]["groups"]:
        if group["name"] == "Gemini Models":
            for bucket in group["buckets"]:
                if bucket["window"] == "weekly":
                    bucket["remaining_fraction"] = gemini_weekly
                    bucket["reset_time"] = reset
                elif bucket["window"] == "5h":
                    bucket["remaining_fraction"] = gemini_five
    return payload


def _write_usage(home: Path, *args, **kwargs) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / ".fake_agy_usage.json").write_text(
        json.dumps(_payload(*args, **kwargs)), encoding="utf-8"
    )
    (home / ".fake_agy_email").write_text(FULL_EMAIL + "\n", encoding="utf-8")


def _write_policy(home: Path, rules: list[str]) -> None:
    policy_dir = home / ".gemini" / "config"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "config.json").write_text(
        json.dumps({"permissions": {"allow": rules}}), encoding="utf-8"
    )


def _account_home(root: Path, name: str) -> Path:
    home = root / name
    home.mkdir(mode=0o700, exist_ok=True)
    home.chmod(0o700)
    return home


def _manager(
    tmp_path: Path,
    accounts: list[dict] | None = None,
    **config_overrides,
) -> tuple[JobManager, Path, Path]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    repo = tmp_path / "repo"
    make_repo(repo, initial_commit=True)
    parsed = parse_accounts(accounts or [], max_accounts=5)
    config = Config(
        accounts=parsed,
        max_accounts=5,
        model_allowlist=["gemini-3.8-flash-medium"],
        allowed_workdir_roots=[str(tmp_path)],
        **config_overrides,
    )
    manager = JobManager(
        config=config,
        runner=LocalProcessRunner(executable=FAKE_AGY),
        base_state_dir=tmp_path / "state",
        home=home,
        settle_window_seconds=0.05,
        poll_interval_seconds=0.05,
    )
    return manager, repo, home


def _start(manager: JobManager, repo: Path, text: str = "do work", **kwargs):
    return manager.start(
        prompt=text,
        repo_path=str(repo),
        title="Multi Account Job",
        model="gemini-3.8-flash-medium",
        max_minutes=10,
        **kwargs,
    )


class TestSingleAccountEquivalence:
    """With no [accounts] registry the job record and envelope are unchanged."""

    def test_job_record_has_no_account_fields(self, tmp_path: Path) -> None:
        manager, repo, _ = _manager(tmp_path)
        envelope = _start(manager, repo)
        manager.wait(envelope.job_id, seconds=20)
        job = get_job(envelope.job_id, manager.state_dir)
        data = job.to_dict()
        assert job.account is None
        assert job.priority == "normal"
        assert "account" not in data
        assert "priority" not in data
        assert "quota_reservation" not in data
        job_json = json.loads(
            (manager.state_dir / "jobs" / envelope.job_id / "job.json").read_text(
                encoding="utf-8"
            )
        )
        assert "account" not in job_json
        assert "priority" not in job_json
        assert "quota_reservation" not in job_json

    def test_envelope_has_no_account_fields(self, tmp_path: Path) -> None:
        manager, repo, _ = _manager(tmp_path)
        envelope = _start(manager, repo)
        payload = envelope.to_dict()
        assert "account" not in payload
        assert "account_email" not in payload
        manager.wait(envelope.job_id, seconds=20)

    def test_usage_row_has_no_account_fields(self, tmp_path: Path) -> None:
        manager, repo, _ = _manager(tmp_path)
        envelope = _start(manager, repo)
        manager.wait(envelope.job_id, seconds=20)
        rows = [
            json.loads(line)
            for line in (manager.state_dir / "usage.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert rows
        assert "account" not in rows[-1]
        assert "masked_email" not in rows[-1]


def test_registry_selects_the_account_with_quota(tmp_path: Path) -> None:
    account_b = _account_home(tmp_path, "account-b")
    manager, repo, home = _manager(
        tmp_path, [{"name": "account-b", "home": str(account_b)}]
    )
    _write_usage(home, 0.01)
    _write_usage(account_b, 0.90)

    envelope = _start(manager, repo)
    manager.wait(envelope.job_id, seconds=20)
    job = get_job(envelope.job_id, manager.state_dir)
    assert job.account == "account-b"
    assert job.account_masked_email
    assert FULL_EMAIL not in json.dumps(job.to_dict())
    audit = (manager.state_dir / "jobs" / envelope.job_id / "audit.jsonl").read_text(
        encoding="utf-8"
    )
    assert "account_selected" in audit
    assert "account_verified" in audit
    assert FULL_EMAIL not in audit
    assert (manager.state_dir / "daily_cap.account-b.json").is_file()
    rows = [
        json.loads(line)
        for line in (manager.state_dir / "usage.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows[-1]["account"] == "account-b"
    assert "masked_email" in rows[-1]
    assert FULL_EMAIL not in json.dumps(rows[-1])


def test_three_accounts_pick_the_most_urgent(tmp_path: Path) -> None:
    account_b = _account_home(tmp_path, "account-b")
    account_c = _account_home(tmp_path, "account-c")
    manager, repo, home = _manager(
        tmp_path,
        [
            {"name": "account-b", "home": str(account_b)},
            {"name": "account-c", "home": str(account_c)},
        ],
    )
    _write_usage(home, 0.50, reset="2026-11-04T12:00:00Z")
    _write_usage(account_b, 0.80, reset="2026-11-03T14:00:00Z")
    _write_usage(account_c, 0.60, reset="2026-11-05T12:00:00Z")

    envelope = _start(manager, repo)
    manager.wait(envelope.job_id, seconds=20)
    assert get_job(envelope.job_id, manager.state_dir).account == "account-b"


def test_explicit_account_override(tmp_path: Path) -> None:
    account_b = _account_home(tmp_path, "account-b")
    manager, repo, home = _manager(
        tmp_path, [{"name": "account-b", "home": str(account_b)}]
    )
    _write_usage(home, 0.90)
    _write_usage(account_b, 0.90)
    envelope = _start(manager, repo, account="account-b")
    manager.wait(envelope.job_id, seconds=20)
    assert get_job(envelope.job_id, manager.state_dir).account == "account-b"


def test_explicit_account_requires_quota_unless_forced(tmp_path: Path) -> None:
    account_b = _account_home(tmp_path, "account-b")
    manager, repo, home = _manager(
        tmp_path, [{"name": "account-b", "home": str(account_b)}]
    )
    _write_usage(home, 0.90)
    _write_usage(account_b, 0.01)

    with pytest.raises(ManagerError) as exc:
        _start(manager, repo, account="account-b")
    assert exc.value.code == "account_not_eligible"

    envelope = _start(manager, repo, account="account-b", force=True)
    manager.wait(envelope.job_id, seconds=20)
    assert get_job(envelope.job_id, manager.state_dir).account == "account-b"


def test_no_eligible_account_is_a_clear_error(tmp_path: Path) -> None:
    account_b = _account_home(tmp_path, "account-b")
    manager, repo, home = _manager(
        tmp_path, [{"name": "account-b", "home": str(account_b)}]
    )
    _write_usage(home, 0.01, reset="2026-11-08T12:00:00Z")
    _write_usage(account_b, 0.01, reset="2026-11-08T12:00:00Z")
    with pytest.raises(ManagerError) as exc:
        _start(manager, repo)
    assert exc.value.code == "no_account_eligible"
    assert "2026-11-08T12:00:00Z" in str(exc.value)


def test_policy_missing_blocks_an_explicit_account(tmp_path: Path) -> None:
    account_b = _account_home(tmp_path, "account-b")
    manager, repo, home = _manager(
        tmp_path, [{"name": "account-b", "home": str(account_b)}]
    )
    _write_usage(home, 0.90)
    _write_usage(account_b, 0.90)
    _write_policy(home, ["command(git status)"])
    with pytest.raises(ManagerError) as exc:
        _start(manager, repo, account="account-b")
    assert exc.value.code == "account_permission_policy_missing"


def test_policy_drift_blocks_an_explicit_account(tmp_path: Path) -> None:
    account_b = _account_home(tmp_path, "account-b")
    manager, repo, home = _manager(
        tmp_path, [{"name": "account-b", "home": str(account_b)}]
    )
    _write_usage(home, 0.90)
    _write_usage(account_b, 0.90)
    _write_policy(home, ["command(git status)"])
    _write_policy(account_b, ["command(git status)", "command(rm)"])
    with pytest.raises(ManagerError) as exc:
        _start(manager, repo, account="account-b")
    assert exc.value.code == "account_permission_policy_drift"


def test_resume_stays_on_the_pinned_account(tmp_path: Path) -> None:
    account_b = _account_home(tmp_path, "account-b")
    manager, repo, home = _manager(
        tmp_path, [{"name": "account-b", "home": str(account_b)}]
    )
    _write_usage(home, 0.01)
    _write_usage(account_b, 0.90)
    envelope = _start(manager, repo)
    manager.wait(envelope.job_id, seconds=20)
    first = get_job(envelope.job_id, manager.state_dir)
    assert first.account == "account-b"

    resumed = manager.resume(
        envelope.job_id,
        answer="continue your work",
        follow_up=True,
    )
    manager.wait(resumed.job_id, seconds=20)
    assert get_job(envelope.job_id, manager.state_dir).account == "account-b"
