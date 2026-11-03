"""Every agy spawn for a non-default account must use ``build_account_env``.

Regression for the live bug (human test 2026-11-03): the scheduler's ``/usage``
probe ran without the dead-D-Bus override, so agy authenticated through the
desktop keyring as the primary login and the start failed closed with
``account_identity_mismatch``. These tests drive the fake agy, which records the
``HOME``/``DBUS_SESSION_BUS_ADDRESS`` of each spawn and can simulate the shared
keyring. No sleeps: the manager waits on job state, the probes are files.
"""

import json
import os
from pathlib import Path

from agy_mcp.accounts.registry import parse_accounts
from agy_mcp.accounts.selection import AccountSelector
from agy_mcp.config import Config
from agy_mcp.manager import JobManager
from agy_mcp.runner import LocalProcessRunner
from agy_mcp.usage import UsageCache, get_usage
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"
FIXTURE = Path(__file__).parent / "fixtures" / "real_usage.json"
ACCOUNT_EMAIL = "account.owner@example.com"
PRIMARY_EMAIL = "primary.owner@example.com"
DEAD_DBUS = "unix:path=/dev/null"


def _payload(weekly: float = 0.9) -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for group in payload["command"]["data"]["groups"]:
        if group["name"] == "Gemini Models":
            for bucket in group["buckets"]:
                bucket["remaining_fraction"] = weekly
    return payload


def _write_home(
    home: Path,
    email: str,
    *,
    probe: bool = False,
    keyring_email: str | None = None,
) -> None:
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    (home / ".fake_agy_usage.json").write_text(json.dumps(_payload()), encoding="utf-8")
    (home / ".fake_agy_email").write_text(email + "\n", encoding="utf-8")
    if keyring_email is not None:
        (home / ".fake_agy_keyring_email").write_text(keyring_email + "\n", encoding="utf-8")
    if probe:
        (home / ".fake_agy_env_probe").write_text("1", encoding="utf-8")


def _write_policy(home: Path, rules: list[str]) -> None:
    policy_dir = home / ".gemini" / "config"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "config.json").write_text(
        json.dumps({"permissions": {"allow": rules}}), encoding="utf-8"
    )


def _probes(home: Path) -> list[dict]:
    path = home / "env-probe.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _usage_probes(home: Path) -> list[dict]:
    return [entry for entry in _probes(home) if "/usage" in entry["argv"]]


def _job_probes(home: Path) -> list[dict]:
    return [entry for entry in _probes(home) if "/usage" not in entry["argv"]]


def _label(entry: dict) -> dict:
    return {
        "HOME": entry["HOME"],
        "DBUS_SESSION_BUS_ADDRESS": entry["DBUS_SESSION_BUS_ADDRESS"],
    }


def _manager(tmp_path: Path, account_home: Path) -> tuple[JobManager, Path, Path]:
    home = tmp_path / "home"
    home.mkdir()
    _write_home(home, PRIMARY_EMAIL)
    _write_home(account_home, ACCOUNT_EMAIL)
    _write_policy(home, ["command(git status)"])
    _write_policy(account_home, ["command(git status)"])
    repo = tmp_path / "repo"
    make_repo(repo, initial_commit=True)
    config = Config(
        accounts=parse_accounts(
            [{"name": "account-b", "home": str(account_home)}], max_accounts=5
        ),
        max_accounts=5,
        model_allowlist=["gemini-3.8-flash-medium"],
        allowed_workdir_roots=[str(tmp_path)],
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


def _start_on_b(manager: JobManager, repo: Path, prompt: str = "do work"):
    return manager.start(
        prompt=prompt,
        repo_path=str(repo),
        title="Usage Env Job",
        model="gemini-3.8-flash-medium",
        max_minutes=10,
        account="account-b",
        force=True,
    )


def test_selector_usage_probe_is_isolated_for_a_non_default_account(tmp_path: Path) -> None:
    account_home = tmp_path / "account-b"
    manager, _repo, _home = _manager(tmp_path, account_home)
    (account_home / ".fake_agy_env_probe").write_text("1", encoding="utf-8")

    selector = AccountSelector(
        manager.config,
        manager.registry,
        manager.home,
        str(FAKE_AGY),
        usage_cache=UsageCache(),
        state_dir=manager.state_dir,
    )
    selection = selector.select(
        "gemini-3.8-flash-medium", 10, account="account-b", force=True
    )
    assert selection.account is not None
    assert selection.account.name == "account-b"

    probes = _usage_probes(account_home)
    assert probes, "the /usage probe did not run for account-b"
    for probe in probes:
        assert _label(probe) == {
            "HOME": str(account_home),
            "DBUS_SESSION_BUS_ADDRESS": DEAD_DBUS,
        }


def test_job_launch_and_usage_reads_use_the_isolated_env(tmp_path: Path) -> None:
    account_home = tmp_path / "account-b"
    manager, repo, _home = _manager(tmp_path, account_home)
    (account_home / ".fake_agy_env_probe").write_text("1", encoding="utf-8")

    envelope = _start_on_b(manager, repo)
    manager.wait(envelope.job_id, seconds=20)

    expected = {"HOME": str(account_home), "DBUS_SESSION_BUS_ADDRESS": DEAD_DBUS}
    usage = _usage_probes(account_home)
    # Selection + the at-launch reading + the supervisor's final reading.
    assert len(usage) >= 2
    assert all(_label(probe) == expected for probe in usage)

    launches = _job_probes(account_home)
    assert len(launches) == 1
    assert _label(launches[0]) == expected


def test_resume_uses_the_isolated_env(tmp_path: Path) -> None:
    account_home = tmp_path / "account-b"
    manager, repo, _home = _manager(tmp_path, account_home)
    (account_home / ".fake_agy_env_probe").write_text("1", encoding="utf-8")

    envelope = _start_on_b(manager, repo)
    manager.wait(envelope.job_id, seconds=20)
    resumed = manager.resume(
        envelope.job_id,
        answer="resume marker alpha",
        follow_up=True,
    )
    manager.wait(resumed.job_id, seconds=20)

    expected = {"HOME": str(account_home), "DBUS_SESSION_BUS_ADDRESS": DEAD_DBUS}
    launches = _job_probes(account_home)
    assert len(launches) == 2
    for launch in launches:
        assert _label(launch) == expected
    assert any("resume marker alpha" in " ".join(launch["argv"]) for launch in launches)
    assert all(_label(probe) == expected for probe in _usage_probes(account_home))


def test_a_leaky_usage_env_is_caught_and_the_isolated_env_passes(tmp_path: Path) -> None:
    account_home = tmp_path / "account-b"
    _write_home(
        account_home,
        ACCOUNT_EMAIL,
        probe=True,
        keyring_email=PRIMARY_EMAIL,
    )
    account = parse_accounts(
        [{"name": "account-b", "home": str(account_home)}], max_accounts=5
    )[0]

    # Without the account env the fake agy reaches the simulated keyring and
    # reports the primary address: exactly the live failure.
    leaky = get_usage(
        FAKE_AGY,
        home=account_home,
        use_cache=False,
        account_name="account-b",
        expected_email=ACCOUNT_EMAIL,
    )
    assert leaky["available"] is False
    assert leaky["reason"] == "account_identity_mismatch"
    assert PRIMARY_EMAIL not in json.dumps(leaky)

    isolated = get_usage(
        FAKE_AGY,
        home=account_home,
        use_cache=False,
        account=account,
        account_name="account-b",
        expected_email=ACCOUNT_EMAIL,
    )
    assert isolated["available"] is True
    assert isolated["account"]["identity"] == "matched"

    labels = [_label(probe) for probe in _usage_probes(account_home)]
    assert labels == [
        {"HOME": str(account_home), "DBUS_SESSION_BUS_ADDRESS": None},
        {"HOME": str(account_home), "DBUS_SESSION_BUS_ADDRESS": DEAD_DBUS},
    ]


def test_default_account_usage_env_is_unchanged(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _write_home(home, PRIMARY_EMAIL, probe=True)
    default = parse_accounts([], max_accounts=5)
    assert default == []

    without_account = get_usage(FAKE_AGY, home=home, use_cache=False)
    assert without_account["available"] is True

    from agy_mcp.accounts.registry import AccountConfig

    with_account = get_usage(
        FAKE_AGY,
        home=home,
        use_cache=False,
        account=AccountConfig(name="default", home=str(home), is_default=True),
    )
    assert with_account["available"] is True

    first, second = [_label(probe) for probe in _usage_probes(home)]
    assert first == second == {"HOME": str(home), "DBUS_SESSION_BUS_ADDRESS": None}
