"""Read-only account doctor: registry, homes, policy, identity and isolation (D113)."""

import json
import os
import subprocess
import sys
from pathlib import Path

from agy_mcp.accounts.doctor import doctor_report, main, render_report
from agy_mcp.accounts.registry import parse_accounts
from agy_mcp.config import Config

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"
FIXTURE = Path(__file__).parent / "fixtures" / "real_usage.json"
FULL_EMAIL = "doctor.owner@example.com"


def _payload() -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for group in payload["command"]["data"]["groups"]:
        for bucket in group["buckets"]:
            bucket["remaining_fraction"] = 0.8
    return payload


def _account_home(root: Path, name: str = "account-b") -> Path:
    home = root / name
    home.mkdir(mode=0o700, exist_ok=True)
    home.chmod(0o700)
    (home / ".fake_agy_usage.json").write_text(json.dumps(_payload()), encoding="utf-8")
    (home / ".fake_agy_email").write_text(FULL_EMAIL + "\n", encoding="utf-8")
    (home / ".fake_agy_env_probe").write_text("1", encoding="utf-8")
    return home


def _policy(home: Path, rules: list[str]) -> None:
    path = home / ".gemini" / "config"
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text(
        json.dumps({"permissions": {"allow": rules}}), encoding="utf-8"
    )


def _config(home: Path, **extra) -> Config:
    return Config(
        accounts=parse_accounts(
            [{"name": "account-b", "home": str(home), **extra}], max_accounts=5
        ),
        max_accounts=5,
    )


def test_no_registry_is_healthy(tmp_path: Path) -> None:
    report = doctor_report(Config(), tmp_path, str(FAKE_AGY))
    assert report["ok"] is True
    assert report["findings"][0].check == "registry"


def test_healthy_account(tmp_path: Path) -> None:
    home = _account_home(tmp_path)
    primary = tmp_path / "primary"
    primary.mkdir()
    _policy(primary, ["command(git status)"])
    _policy(home, ["command(git status)"])
    report = doctor_report(_config(home), primary, str(FAKE_AGY))
    assert report["ok"] is True, [f.__dict__ for f in report["findings"]]
    probe = json.loads((home / "env-probe.json").read_text(encoding="utf-8"))
    assert probe["HOME"] == str(home)
    assert probe["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/dev/null"


def test_missing_home_is_a_problem(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    config = Config(
        accounts=parse_accounts([{"name": "account-b", "home": str(missing)}], max_accounts=5),
        max_accounts=5,
    )
    report = doctor_report(config, tmp_path, str(FAKE_AGY))
    assert report["ok"] is False
    home_findings = [f for f in report["findings"] if f.check == "home"]
    assert home_findings and home_findings[0].ok is False


def test_group_accessible_home_is_a_problem(tmp_path: Path) -> None:
    home = _account_home(tmp_path)
    os.chmod(home, 0o755)
    report = doctor_report(_config(home), tmp_path, str(FAKE_AGY))
    assert report["ok"] is False
    assert any(f.check == "home" and f.ok is False for f in report["findings"])


def test_policy_missing_is_a_problem(tmp_path: Path) -> None:
    home = _account_home(tmp_path)
    primary = tmp_path / "primary"
    primary.mkdir()
    _policy(primary, ["command(git status)"])
    report = doctor_report(_config(home), primary, str(FAKE_AGY))
    assert report["ok"] is False
    assert any(f.check == "policy" and f.ok is False for f in report["findings"])


def test_policy_drift_is_a_problem(tmp_path: Path) -> None:
    home = _account_home(tmp_path)
    primary = tmp_path / "primary"
    primary.mkdir()
    _policy(primary, ["command(git status)"])
    _policy(home, ["command(git status)", "command(rm)"])
    report = doctor_report(_config(home), primary, str(FAKE_AGY))
    assert report["ok"] is False
    assert any(
        f.check == "policy" and f.ok is False and "drift" in f.detail
        for f in report["findings"]
    )


def test_email_mismatch_is_a_problem_and_masked(tmp_path: Path) -> None:
    home = _account_home(tmp_path)
    report = doctor_report(
        _config(home, expected_email="someone.else@example.com"),
        tmp_path,
        str(FAKE_AGY),
    )
    assert report["ok"] is False
    rendered = render_report(report)
    assert FULL_EMAIL not in rendered
    assert "doc***er@example.com" in rendered


def test_report_rendering_is_one_line_per_check(tmp_path: Path) -> None:
    home = _account_home(tmp_path)
    report = doctor_report(_config(home), tmp_path, str(FAKE_AGY))
    lines = render_report(report).splitlines()
    assert len(lines) == len(report["findings"])
    assert all(line.startswith("account ") for line in lines)


def test_cli_exit_zero_for_a_healthy_single_account(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text("", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agy_mcp.accounts.doctor",
            "--config",
            str(config_file),
            "--agy-binary",
            str(FAKE_AGY),
            "--home",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "single-account" in result.stderr


def test_cli_exit_one_on_a_bad_registry(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        '[[accounts]]\nname = "account-b"\nhome = "relative"\n', encoding="utf-8"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agy_mcp.accounts.doctor",
            "--config",
            str(config_file),
            "--agy-binary",
            str(FAKE_AGY),
            "--home",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
        timeout=30,
    )
    assert result.returncode == 1
    assert "config invalid" in result.stderr


def test_main_writes_to_stderr_only(tmp_path: Path, capsys) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text("", encoding="utf-8")
    code = main(
        ["--config", str(config_file), "--agy-binary", str(FAKE_AGY), "--home", str(tmp_path)]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""
    assert "single-account" in captured.err
