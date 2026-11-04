"""The isolation proof ends at the auth marker, not agy's login timeout (D117).

All tests are hermetic: HOME, config and state live under ``tmp_path`` and the
only ``agy`` is a fake script. No test reads a credential file or logs in.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import pytest

from agy_mcp.accounts import cli
from agy_mcp.accounts.errors import AccountError
from agy_mcp.accounts.provision import probe_isolation
from agy_mcp.accounts.registry import AccountConfig
from tests.helpers.account_cli import FAKE_EMAIL, write_config
from tests.helpers.account_probe_agy import URL_SENTINEL, write_probe_agy


def _account(tmp_path: Path) -> AccountConfig:
    home = tmp_path / "accounts" / "a"
    home.mkdir(parents=True, mode=0o700)
    return AccountConfig(name="a", home=str(home))


def _pid_gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def test_marker_then_hang_passes_quickly_and_leaves_no_orphan(tmp_path: Path) -> None:
    """The marker is enough: stop the 60 s auth wait and reap the child."""
    pid_file = tmp_path / "probe.pid"
    fake = write_probe_agy(tmp_path / "agy", behavior="marker_sleep", pid_file=pid_file)

    started = time.monotonic()
    reading = probe_isolation(_account(tmp_path), str(fake), timeout_seconds=30.0)
    elapsed = time.monotonic() - started

    assert reading["available"] is False
    assert reading["reason"] == "auth_required"
    assert elapsed < 5.0, f"isolation proof took {elapsed:.2f}s instead of ending early"
    pid = int(pid_file.read_text(encoding="utf-8"))
    assert _pid_gone(pid), f"probe child {pid} is still running"
    assert not Path(f"/proc/{pid}").exists()


def test_marker_then_exit_passes(tmp_path: Path) -> None:
    """A child that exits on its own is evaluated as before."""
    fake = write_probe_agy(tmp_path / "agy", behavior="marker_exit")

    reading = probe_isolation(_account(tmp_path), str(fake), timeout_seconds=30.0)

    assert reading["available"] is False
    assert reading["reason"] == "auth_required"


def test_logged_in_usage_output_still_refuses(tmp_path: Path) -> None:
    """Quota rows for a fresh home are still a keyring leak."""
    fake = write_probe_agy(tmp_path / "agy", behavior="logged_in")

    with pytest.raises(AccountError) as excinfo:
        probe_isolation(_account(tmp_path), str(fake), timeout_seconds=30.0)

    assert excinfo.value.code == "account_cli_isolation_leak"


def test_silent_hang_still_times_out(tmp_path: Path) -> None:
    """Neither a marker nor an exit within the timeout keeps the old error."""
    pid_file = tmp_path / "probe.pid"
    fake = write_probe_agy(tmp_path / "agy", behavior="silent_hang", pid_file=pid_file)
    started = time.monotonic()

    with pytest.raises(AccountError) as excinfo:
        probe_isolation(_account(tmp_path), str(fake), timeout_seconds=0.5)
    elapsed = time.monotonic() - started

    assert excinfo.value.code == "account_cli_isolation_unproven"
    assert "reason: timeout" in excinfo.value.message
    assert elapsed < 5.0
    pid = int(pid_file.read_text(encoding="utf-8"))
    assert _pid_gone(pid), f"timed-out probe child {pid} is still running"


def test_auth_url_never_reaches_output_logs_or_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The marker passes the proof, but the URL after it is discarded."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    work = tmp_path / "work"
    config = tmp_path / "config" / "config.toml"
    write_config(
        config,
        'max_accounts = 5\n'
        f'allowed_workdir_roots = ["{work}"]\n',
    )
    state = tmp_path / "state"
    fake = write_probe_agy(tmp_path / "agy", behavior="marker_sleep")

    def login(account: AccountConfig, account_home: Path, binary: str) -> int:
        del account, binary
        (account_home / ".fake_logged_in").write_text("1", encoding="utf-8")
        return 0

    seams = cli.CliSeams(
        login_runner=login,
        isatty=lambda: True,
        prompt_email=lambda: FAKE_EMAIL,
    )
    caplog.set_level(logging.DEBUG)
    rc = cli.main(
        [
            "add",
            "a",
            "--yes",
            "--config",
            str(config),
            "--state-dir",
            str(state),
            "--agy-binary",
            str(fake),
            "--home",
            str(tmp_path / "accounts" / "a"),
        ],
        seams=seams,
    )
    captured = capsys.readouterr()
    output = captured.out + captured.err

    assert rc == 0
    assert URL_SENTINEL not in output
    assert URL_SENTINEL not in caplog.text
    needle = URL_SENTINEL.encode("utf-8")
    for root in (home, config.parent, state, tmp_path / "accounts"):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path != fake:
                assert needle not in path.read_bytes(), (
                    f"auth URL was written to {path}"
                )
