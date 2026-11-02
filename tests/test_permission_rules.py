"""Smoke-fix item 1: dangerous permission-rule inspection (finding 1).

Real agy loads ``<home>/.gemini/config/config.json`` (masked evidence cli.log:
``applyUserSettings: stored shared config permissions: allow=31 deny=0 ask=0
from /home/<user>/.gemini/config/config.json``). The inspector must read that
file first, never follow symlinks, cap the size, and fail soft.
"""

import json
import os
from pathlib import Path

from agy_mcp.config import Config
from agy_mcp.manager import JobManager
from agy_mcp.runner import LocalProcessRunner
from agy_mcp.worktree import inspect_permission_rules
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"


def _write_config(home: Path, rules: list[str]) -> Path:
    cfg = home / ".gemini" / "config" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"permissions": {"allow": rules}}), encoding="utf-8")
    return cfg


def _setup_manager(tmp_path: Path) -> tuple[JobManager, Path]:
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    manager = JobManager(
        config=Config(max_concurrent_jobs=2, daily_jobs_cap=5),
        runner=LocalProcessRunner(executable=FAKE_AGY),
        base_state_dir=state_dir,
        home=tmp_path,
        settle_window_seconds=0.1,
        poll_interval_seconds=0.05,
    )
    return manager, repo_dir


def test_inspector_reads_real_config_location(tmp_path: Path) -> None:
    """The first candidate is <home>/.gemini/config/config.json, not <home>/config."""
    home = tmp_path / "home"
    home.mkdir()
    _write_config(home, ["command(git status)", "command(bash)"])

    result = inspect_permission_rules(home)

    assert "command(bash)" in result["dangerous_allow_rules"]
    assert "command(git status)" in result["permission_rules_effective"]


def test_inspector_does_not_follow_symlinked_config(tmp_path: Path) -> None:
    """A symlinked config is skipped with the unreadable warning, never followed."""
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"permissions": {"allow": ["command(bash)"]}}), encoding="utf-8")
    cfg = home / ".gemini" / "config" / "config.json"
    cfg.parent.mkdir(parents=True)
    os.symlink(outside, cfg)

    result = inspect_permission_rules(home)

    assert result["dangerous_allow_rules"] == []
    assert "permission_rules_unreadable" in result["warnings"]


def test_inspector_size_cap_fails_soft(tmp_path: Path) -> None:
    """A config larger than the cap is skipped with the unreadable warning."""
    home = tmp_path / "home"
    home.mkdir()
    cfg = _write_config(home, ["command(bash)"])
    cfg.write_text(
        " " * (1024 * 1024 + 10) + '{"permissions": {"allow": ["command(bash)"]}}',
        encoding="utf-8",
    )

    result = inspect_permission_rules(home)

    assert result["dangerous_allow_rules"] == []
    assert "permission_rules_unreadable" in result["warnings"]


def test_start_envelope_warns_on_dangerous_rule_and_hides_body(tmp_path: Path) -> None:
    """A dangerous rule in the real config path adds the warning without its body."""
    manager, repo_dir = _setup_manager(tmp_path)
    _write_config(tmp_path, ["command(bash)", "command(git status)", "command(git push)"])

    env = manager.start(
        prompt="Check rules __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Dangerous Rules Job",
    )
    try:
        warn_entry = next(
            w for w in env.warnings if w.startswith("dangerous_permission_rules_present")
        )
        assert "2" in warn_entry
        assert "command(" not in warn_entry
        assert "git status" not in warn_entry
    finally:
        manager.wait(env.job_id, seconds=8)


def test_start_envelope_no_warning_for_benign_rule(tmp_path: Path) -> None:
    """A benign allow-rule in the real config path does not warn."""
    manager, repo_dir = _setup_manager(tmp_path)
    _write_config(tmp_path, ["command(git status)", "command(ls)"])

    env = manager.start(
        prompt="Check benign rules __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Benign Rules Job",
    )
    try:
        assert not any("dangerous_permission_rules_present" in w for w in env.warnings)
    finally:
        manager.wait(env.job_id, seconds=8)


def test_start_envelope_survives_unreadable_config(tmp_path: Path) -> None:
    """A malformed config never raises or blocks; it surfaces the unreadable warning."""
    manager, repo_dir = _setup_manager(tmp_path)
    cfg = tmp_path / ".gemini" / "config" / "config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{ unclosed invalid json", encoding="utf-8")

    env = manager.start(
        prompt="Unreadable config __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Unreadable Config Job",
    )
    try:
        assert "permission_rules_unreadable" in env.warnings
        assert not any("dangerous_permission_rules_present" in w for w in env.warnings)
    finally:
        manager.wait(env.job_id, seconds=8)
