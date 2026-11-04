"""The implicit default account is optional and verifiable (D116).

Covers the ``[default_account]`` table, the disabled-default start/resume gates,
the identity expectation on the single-account path, and the unchanged behavior
when no table is present. All tests drive the fake agy; none sleeps.
"""

import json
from pathlib import Path

import pytest

from agy_mcp.accounts.registry import (
    DefaultAccountConfig,
    build_registry,
    parse_accounts,
)
from agy_mcp.config import Config, ConfigError, load_config
from agy_mcp.jobs import get_job, list_jobs
from agy_mcp.manager import JobManager, ManagerError
from agy_mcp.runner import LocalProcessRunner
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"
FIXTURE = Path(__file__).parent / "fixtures" / "real_usage.json"
EMAIL = "jordan.lee81@example.com"
OTHER_EMAIL = "casey.morgan42@example.com"


def _payload(weekly: float = 0.9) -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for group in payload["command"]["data"]["groups"]:
        for bucket in group["buckets"]:
            if bucket["window"] == "weekly":
                bucket["remaining_fraction"] = weekly
            else:
                bucket["remaining_fraction"] = 0.9
    return payload


def _write_home(home: Path, *, weekly: float = 0.9, email: str | None = None) -> None:
    home.mkdir(mode=0o700, exist_ok=True)
    home.chmod(0o700)
    (home / ".fake_agy_usage.json").write_text(
        json.dumps(_payload(weekly)), encoding="utf-8"
    )
    if email is not None:
        (home / ".fake_agy_email").write_text(email + "\n", encoding="utf-8")


def _account_home(root: Path, name: str) -> Path:
    home = root / name
    home.mkdir(mode=0o700, exist_ok=True)
    home.chmod(0o700)
    return home


def _manager(
    tmp_path: Path,
    accounts: list[dict] | None = None,
    default_account: DefaultAccountConfig | None = None,
    **config_overrides,
) -> tuple[JobManager, Path, Path]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    repo = tmp_path / "repo"
    make_repo(repo, initial_commit=True)
    config = Config(
        accounts=parse_accounts(accounts or [], max_accounts=5),
        default_account=default_account,
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


def _start(manager: JobManager, repo: Path, **kwargs):
    return manager.start(
        prompt="do work",
        repo_path=str(repo),
        title="Default Optional Job",
        model="gemini-3.8-flash-medium",
        max_minutes=10,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


def test_absent_table_is_none_and_the_default_stays_enabled(tmp_path: Path) -> None:
    config = Config()
    assert config.default_account is None
    registry = build_registry(config, tmp_path)
    assert registry.default.enabled is True
    assert registry.default.expected_email is None
    assert registry.default.expected_email_sha256 is None


def test_load_default_account_table(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        f"""
[default_account]
enabled = false
expected_email = "{EMAIL}"
""",
        encoding="utf-8",
    )
    config = load_config(path=config_file)
    assert config.default_account is not None
    assert config.default_account.enabled is False
    assert config.default_account.expected_email == EMAIL
    assert config.unknown_keys == []


def test_default_account_rejects_both_expectations(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    digest = "a" * 64
    config_file.write_text(
        "[default_account]\n"
        f'expected_email = "{EMAIL}"\n'
        f'expected_email_sha256 = "{digest}"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="default_account"):
        load_config(path=config_file)


def test_default_account_rejects_unknown_key(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text("[default_account]\nhome = \"/tmp/x\"\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="default_account"):
        load_config(path=config_file)


def test_default_account_rejects_non_bool_enabled(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text('[default_account]\nenabled = "no"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="default_account"):
        load_config(path=config_file)


# ---------------------------------------------------------------------------
# Start and resume gates
# ---------------------------------------------------------------------------


def test_default_disabled_is_never_selected(tmp_path: Path) -> None:
    account_b = _account_home(tmp_path, "account-b")
    _write_home(account_b, weekly=0.9)
    manager, repo, home = _manager(
        tmp_path,
        [{"name": "account-b", "home": str(account_b)}],
        DefaultAccountConfig(enabled=False),
    )
    _write_home(home, weekly=0.9)

    envelope = _start(manager, repo)
    manager.wait(envelope.job_id, seconds=20)
    assert get_job(envelope.job_id, manager.state_dir).account == "account-b"


def test_default_disabled_with_no_other_account_has_a_stable_code(tmp_path: Path) -> None:
    manager, repo, _ = _manager(
        tmp_path, default_account=DefaultAccountConfig(enabled=False)
    )
    with pytest.raises(ManagerError) as exc:
        _start(manager, repo)
    assert exc.value.code == "account_none_enabled"


def test_all_declared_accounts_disabled_has_a_stable_code(tmp_path: Path) -> None:
    account_b = _account_home(tmp_path, "account-b")
    manager, repo, _ = _manager(
        tmp_path,
        [{"name": "account-b", "home": str(account_b), "enabled": False}],
        DefaultAccountConfig(enabled=False),
    )
    with pytest.raises(ManagerError) as exc:
        _start(manager, repo)
    assert exc.value.code == "account_none_enabled"


def test_explicit_default_is_rejected_when_disabled(tmp_path: Path) -> None:
    account_b = _account_home(tmp_path, "account-b")
    _write_home(account_b)
    manager, repo, _ = _manager(
        tmp_path,
        [{"name": "account-b", "home": str(account_b)}],
        DefaultAccountConfig(enabled=False),
    )
    with pytest.raises(ManagerError) as exc:
        _start(manager, repo, account="default")
    assert exc.value.code == "default_account_disabled"
    assert list_jobs(manager.state_dir) == []


def test_default_identity_mismatch_never_starts_a_job(tmp_path: Path) -> None:
    manager, repo, home = _manager(
        tmp_path,
        default_account=DefaultAccountConfig(expected_email=EMAIL),
    )
    _write_home(home, email=OTHER_EMAIL)
    with pytest.raises(ManagerError) as exc:
        _start(manager, repo)
    assert exc.value.code == "account_identity_mismatch"
    assert list_jobs(manager.state_dir) == []


def test_default_identity_match_starts_the_single_account_path(tmp_path: Path) -> None:
    manager, repo, home = _manager(
        tmp_path,
        default_account=DefaultAccountConfig(expected_email=EMAIL),
    )
    _write_home(home, email=EMAIL)
    envelope = _start(manager, repo)
    manager.wait(envelope.job_id, seconds=20)
    job = get_job(envelope.job_id, manager.state_dir)
    assert job.account is None
    assert job.state.value == "done"
    # The single-account job record still omits every multi-account field.
    assert "account" not in job.to_dict()


def test_resume_of_a_default_pinned_job_when_default_is_disabled(tmp_path: Path) -> None:
    manager, repo, home = _manager(tmp_path)
    _write_home(home)
    envelope = _start(manager, repo)
    manager.wait(envelope.job_id, seconds=20)

    resumed_manager = JobManager(
        config=Config(
            default_account=DefaultAccountConfig(enabled=False),
            max_concurrent_jobs=2,
            model_allowlist=["gemini-3.8-flash-medium"],
            allowed_workdir_roots=[str(tmp_path)],
        ),
        runner=LocalProcessRunner(executable=FAKE_AGY),
        base_state_dir=manager.state_dir,
        home=home,
        settle_window_seconds=0.05,
        poll_interval_seconds=0.05,
    )
    with pytest.raises(ManagerError) as exc:
        resumed_manager.resume(envelope.job_id, answer="continue", follow_up=True)
    assert exc.value.code == "default_account_disabled"
    # Never silently moved: the record is still the implicitly-default one.
    assert get_job(envelope.job_id, manager.state_dir).account is None


def test_no_table_no_accounts_keeps_the_single_account_record(tmp_path: Path) -> None:
    manager, repo, home = _manager(tmp_path)
    _write_home(home)
    envelope = _start(manager, repo)
    manager.wait(envelope.job_id, seconds=20)
    data = get_job(envelope.job_id, manager.state_dir).to_dict()
    assert "account" not in data
    assert "priority" not in data
    assert "quota_reservation" not in data
