"""Regression guard: tests can never resolve or write the live agy-mcp state dir.

The stale-jobs report (docs/reports/stale-jobs-report.md) found 40 ghost queued
jobs left by tests that omitted ``base_state_dir``. This module proves the
session fixture points every default state path at a temp dir and that the
audit-hook guard rejects writes to the real path without actually writing there.
"""

import os
import sys
from pathlib import Path

import pytest

from agy_mcp.jobs import get_base_state_dir


def test_session_env_points_away_from_live_state(isolated_state) -> None:
    """HOME/XDG_STATE_HOME/AGY_MCP_STATE_DIR are all session temp dirs."""
    assert Path.home() == isolated_state.home
    assert Path(os.environ["XDG_STATE_HOME"]) == isolated_state.xdg_state
    assert Path(os.environ["AGY_MCP_STATE_DIR"]) == isolated_state.state_env

    # The default state resolution (no explicit base_state_dir) is the temp dir,
    # not ~/.local/state/agy-mcp.
    assert get_base_state_dir() == isolated_state.state_env
    assert get_base_state_dir() != isolated_state.real_live_state
    assert isolated_state.home != isolated_state.real_live_state


def test_guard_blocks_open_write_under_live_state(isolated_state) -> None:
    """The installed audit hook refuses a write-open event; no file is created."""
    probe = isolated_state.real_live_state / "conftest-guard-probe.txt"
    with pytest.raises(RuntimeError, match="BLOCKED by test isolation guard"):
        # sys.audit fires the hook chain without opening or writing anything.
        sys.audit("open", str(probe), "w", 0)
    assert not probe.exists()


def test_guard_blocks_mutating_events_under_live_state(isolated_state) -> None:
    """mkdir/rename-style events are rejected before they reach the filesystem."""
    probe = isolated_state.real_live_state / "conftest-guard-dir"
    with pytest.raises(RuntimeError, match="BLOCKED by test isolation guard"):
        sys.audit("os.mkdir", str(probe), 0o700, -1)
    assert not probe.exists()


def test_guard_allows_reads_and_temp_writes(isolated_state) -> None:
    """Reads of the live path and writes under the session temp dir still work."""
    sys.audit("open", str(isolated_state.real_live_state / "job.json"), "r", os.O_RDONLY)
    sys.audit("open", str(isolated_state.home / "allowed.txt"), "w", os.O_CREAT)

    allowed = isolated_state.home / "allowed.txt"
    allowed.write_text("ok", encoding="utf-8")
    assert allowed.read_text(encoding="utf-8") == "ok"
