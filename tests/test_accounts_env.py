"""Per-account child environment building (D104)."""

from pathlib import Path

from agy_mcp.accounts.env import (
    ISOLATED_DBUS_SESSION_BUS_ADDRESS,
    build_account_env,
)
from agy_mcp.accounts.registry import AccountConfig
from agy_mcp.util import env_allowlist


def _default(home: Path) -> AccountConfig:
    return AccountConfig(name="default", home=str(home), is_default=True)


def _extra(name: str, home: Path) -> AccountConfig:
    return AccountConfig(name=name, home=str(home))


def test_default_env_is_exactly_the_existing_allowlist(tmp_path: Path) -> None:
    home = tmp_path / "home"
    tmp = tmp_path / "tmp"
    assert build_account_env(_default(home), tmp) == env_allowlist(job_tmp_dir=tmp, home=home)


def test_default_env_has_no_dbus_override(tmp_path: Path) -> None:
    env = build_account_env(_default(tmp_path), tmp_path / "tmp")
    assert "DBUS_SESSION_BUS_ADDRESS" not in env


def test_non_default_env_uses_account_home_and_dead_dbus(tmp_path: Path) -> None:
    account_home = tmp_path / "account-b"
    env = build_account_env(_extra("account-b", account_home), tmp_path / "tmp")
    assert env["HOME"] == str(account_home)
    assert env["DBUS_SESSION_BUS_ADDRESS"] == ISOLATED_DBUS_SESSION_BUS_ADDRESS
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/dev/null"


def test_parent_environment_is_not_inherited(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SOME_SECRET", "do-not-leak")
    monkeypatch.setenv("GEMINI_API_KEY", "do-not-leak")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    env = build_account_env(_extra("account-b", tmp_path / "account-b"), tmp_path / "tmp")
    assert "SOME_SECRET" not in env
    assert "GEMINI_API_KEY" not in env
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/dev/null"


def test_disable_hooks_false_is_passed_through(tmp_path: Path) -> None:
    env = build_account_env(_default(tmp_path), tmp_path / "tmp", disable_hooks=False)
    assert env["GIT_CONFIG_COUNT"] == "2"
    values = {env[f"GIT_CONFIG_KEY_{index}"] for index in range(2)}
    assert "core.hooksPath" not in values


def test_path_override(tmp_path: Path) -> None:
    env = build_account_env(_default(tmp_path), tmp_path / "tmp", path="/custom/bin")
    assert env["PATH"] == "/custom/bin"
