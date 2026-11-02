"""Tests for login-expired detection (Item 7)."""

import time
from pathlib import Path
from typing import Callable

import pytest

from agy_mcp.config import Config, ConfigError, load_config
from agy_mcp.jobs import get_job
from agy_mcp.manager import JobManager
from agy_mcp.models import JobState
from agy_mcp.runner import LocalProcessRunner
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"
EXPECTED_ACTION = "ask the human to log in to agy again, then resume"


def poll_until(
    predicate: Callable[[], bool],
    timeout_seconds: float = 15.0,
    poll_interval: float = 0.05,
) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(poll_interval)
    raise TimeoutError("Timed out waiting for condition")


def _setup(tmp_path: Path, config: Config | None = None) -> tuple[JobManager, Path]:
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    manager = JobManager(
        config=config or Config(),
        runner=LocalProcessRunner(executable=FAKE_AGY),
        base_state_dir=tmp_path / "state",
        home=tmp_path,
        settle_window_seconds=0.05,
        poll_interval_seconds=0.05,
    )
    return manager, repo_dir


def _scenario(tmp_path: Path, name: str) -> None:
    (tmp_path / ".fake_agy_usage_scenario").write_text(name, encoding="utf-8")


def _run(manager: JobManager, repo_dir: Path, prompt: str):
    env = manager.start(
        prompt=prompt,
        repo_path=str(repo_dir),
        title="Auth Detection Job",
        model="gemini-3.8-flash-medium",
    )
    return manager.wait(env.job_id, seconds=20)


def _assert_auth_required(manager: JobManager, result) -> None:
    assert result.state == JobState.NEEDS_REVIEW
    assert get_job(result.job_id, manager.state_dir).review_reason == "auth_required"
    assert result.resume_after is None
    assert any(EXPECTED_ACTION in action.lower() for action in result.next_actions)


def test_usage_auth_required_detected_at_finalization(tmp_path: Path) -> None:
    """A /usage call reporting login-required marks the job auth_required."""
    _scenario(tmp_path, "usage_auth_required")
    config = Config(auth_error_patterns=["not logged in"])
    manager, repo_dir = _setup(tmp_path, config=config)

    result = _run(manager, repo_dir, "__SCENARIO:crash_rc1__")

    _assert_auth_required(manager, result)
    manager.shutdown()


def test_auth_error_pattern_on_stderr(tmp_path: Path) -> None:
    """A configured auth pattern in stderr marks the job auth_required."""
    _scenario(tmp_path, "usage_real")
    config = Config(auth_error_patterns=["log in again"])
    manager, repo_dir = _setup(tmp_path, config=config)

    result = _run(manager, repo_dir, "__SCENARIO:auth_error__")

    _assert_auth_required(manager, result)
    manager.shutdown()


def test_auth_error_pattern_in_cli_log_tail(tmp_path: Path) -> None:
    """A configured auth pattern in cli.log tail is honoured."""
    _scenario(tmp_path, "usage_real")
    config = Config(auth_error_patterns=["authentication expired"])
    manager, repo_dir = _setup(tmp_path, config=config)

    result = _run(manager, repo_dir, "__SCENARIO:auth_error__")

    _assert_auth_required(manager, result)
    manager.shutdown()


def test_auth_takes_precedence_over_quota(tmp_path: Path) -> None:
    """When both signals exist, login-expired wins over quota exhaustion."""
    _scenario(tmp_path, "usage_zero")
    config = Config(
        auth_error_patterns=["log in again"],
        quota_error_patterns=["log in again"],
    )
    manager, repo_dir = _setup(tmp_path, config=config)

    result = _run(manager, repo_dir, "__SCENARIO:auth_error__")

    _assert_auth_required(manager, result)
    manager.shutdown()


def test_auth_required_is_resumable(tmp_path: Path) -> None:
    """auth_required is a non-security needs_review reason and can be resumed."""
    _scenario(tmp_path, "usage_auth_required")
    config = Config(auth_error_patterns=["not logged in"])
    manager, repo_dir = _setup(tmp_path, config=config)

    result = _run(manager, repo_dir, "__SCENARIO:crash_rc1__")
    _assert_auth_required(manager, result)

    resumed = manager.resume(result.job_id, "Logged in again")
    assert resumed.state == JobState.RUNNING
    manager.cancel(result.job_id)
    manager.shutdown()


def test_auth_guard_does_not_block_start(tmp_path: Path) -> None:
    """An expired login makes usage unavailable, which warns but never blocks start."""
    _scenario(tmp_path, "usage_auth_required")
    config = Config(
        usage_guard="block",
        auth_error_patterns=["not logged in"],
    )
    manager, repo_dir = _setup(tmp_path, config=config)

    env = manager.start(
        prompt="Perform task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Auth Guard Job",
        model="gemini-3.8-flash-medium",
    )
    try:
        assert "usage_unavailable" in env.warnings
        assert "low_quota" not in env.warnings
    finally:
        manager.cancel(env.job_id)
        manager.shutdown()


def test_config_auth_error_patterns_validation(tmp_path: Path) -> None:
    """auth_error_patterns defaults to [] and validates list[str] plus regex syntax."""
    cfg = load_config(path=tmp_path / "missing.toml")
    assert cfg.auth_error_patterns == []

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        'auth_error_patterns = ["not logged in", "authentication (failed|expired)"]\n',
        encoding="utf-8",
    )
    cfg = load_config(path=cfg_file)
    assert cfg.auth_error_patterns == [
        "not logged in",
        "authentication (failed|expired)",
    ]

    for bad in ('"auth"', "[1]", '["bad[regex"]'):
        cfg_file.write_text(f"auth_error_patterns = {bad}\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(path=cfg_file)
