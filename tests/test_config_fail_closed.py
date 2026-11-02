"""Tests for F25: config errors must fail closed and refuse to start."""

from pathlib import Path

import pytest

from agy_mcp.config import ConfigError
from agy_mcp.server import main, setup_server


def test_setup_server_raises_on_invalid_config(tmp_path: Path) -> None:
    """setup_server must raise ConfigError when config is invalid, not continue on defaults."""
    bad_cfg = tmp_path / "config.toml"
    bad_cfg.write_text("invalid_syntax = [[[", encoding="utf-8")
    state_dir = tmp_path / "state"

    fake_bin = tmp_path / "fake_agy"
    fake_bin.touch(mode=0o755)

    with pytest.raises(ConfigError):
        setup_server(
            agy_binary=str(fake_bin),
            state_dir=str(state_dir),
            config_path=str(bad_cfg),
        )


def test_main_exits_on_config_error(tmp_path: Path) -> None:
    """main must exit with status 1 on ConfigError, refusing to start."""
    bad_cfg = tmp_path / "config.toml"
    bad_cfg.write_text("max_concurrent_jobs = -5\n", encoding="utf-8")
    state_dir = tmp_path / "state"

    fake_bin = tmp_path / "fake_agy"
    fake_bin.touch(mode=0o755)

    cmd_args = [
        "--config",
        str(bad_cfg),
        "--state-dir",
        str(state_dir),
        "--agy-binary",
        str(fake_bin),
    ]
    with pytest.raises(SystemExit) as exc_info:
        main(cmd_args)
    assert exc_info.value.code == 1


def test_missing_config_starts_with_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing config.toml on first run starts with default settings without failing."""
    state_dir = tmp_path / "state"
    fake_bin = tmp_path / "fake_agy"
    fake_bin.touch(mode=0o755)

    # 1. Non-existent path passed explicitly
    missing_cfg = tmp_path / "does_not_exist" / "config.toml"
    manager = setup_server(
        agy_binary=str(fake_bin),
        state_dir=str(state_dir),
        config_path=str(missing_cfg),
    )
    assert manager.config.max_concurrent_jobs == 2
    assert manager.config.max_minutes_cap == 360

    # 2. Default XDG / HOME path missing
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()
    monkeypatch.setenv("HOME", str(empty_home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    manager_default = setup_server(
        agy_binary=str(fake_bin),
        state_dir=str(state_dir),
        config_path=None,
    )
    assert manager_default.config.max_concurrent_jobs == 2
    assert manager_default.config.max_minutes_cap == 360

