"""Tests for quota-exhausted detection (Item 6)."""

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
GEMINI_WEEKLY_RESET = "2026-11-01T09:54:57Z"


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
        title="Quota Detection Job",
        model="gemini-3.8-flash-medium",
    )
    return manager.wait(env.job_id, seconds=20)


def test_failed_job_with_zero_quota_becomes_quota_exhausted(tmp_path: Path) -> None:
    """A failed job with a relevant 0% bucket becomes needs_review/quota_exhausted."""
    _scenario(tmp_path, "usage_zero")
    manager, repo_dir = _setup(tmp_path)

    result = _run(manager, repo_dir, "__SCENARIO:crash_rc1__ fatal crash")

    assert result.state == JobState.NEEDS_REVIEW
    assert get_job(result.job_id, manager.state_dir).review_reason == "quota_exhausted"
    assert result.resume_after == GEMINI_WEEKLY_RESET
    assert any(
        f"wait until {GEMINI_WEEKLY_RESET}" in action for action in result.next_actions
    )
    assert any("resume" in action.lower() for action in result.next_actions)
    manager.shutdown()


def test_timeout_with_zero_quota_becomes_quota_exhausted(tmp_path: Path) -> None:
    """A timeout with a relevant 0% bucket becomes needs_review/quota_exhausted."""
    _scenario(tmp_path, "usage_zero")
    manager, repo_dir = _setup(tmp_path)

    result = _run(manager, repo_dir, "__SCENARIO:timeout__")

    assert result.state == JobState.NEEDS_REVIEW
    assert get_job(result.job_id, manager.state_dir).review_reason == "quota_exhausted"
    assert result.resume_after == GEMINI_WEEKLY_RESET
    manager.shutdown()


def test_empty_response_with_zero_quota_becomes_quota_exhausted(tmp_path: Path) -> None:
    """A job that ends with an empty response also triggers quota detection."""
    _scenario(tmp_path, "usage_zero")
    manager, repo_dir = _setup(tmp_path)

    result = _run(manager, repo_dir, "__SCENARIO:denial_blocked__")

    assert result.state == JobState.NEEDS_REVIEW
    assert get_job(result.job_id, manager.state_dir).review_reason == "quota_exhausted"
    manager.shutdown()


def test_quota_error_pattern_on_stderr(tmp_path: Path) -> None:
    """A configured quota pattern in stderr marks the job quota_exhausted without 0%."""
    _scenario(tmp_path, "usage_real")
    config = Config(quota_error_patterns=["Fatal error"])
    manager, repo_dir = _setup(tmp_path, config=config)

    result = _run(manager, repo_dir, "__SCENARIO:crash_rc1__")

    assert result.state == JobState.NEEDS_REVIEW
    assert get_job(result.job_id, manager.state_dir).review_reason == "quota_exhausted"
    assert result.resume_after is None
    assert any("quota" in action.lower() for action in result.next_actions)
    manager.shutdown()


def test_quota_error_pattern_in_cli_log_tail(tmp_path: Path) -> None:
    """A configured quota pattern in cli.log tail is also honoured."""
    _scenario(tmp_path, "usage_real")
    config = Config(quota_error_patterns=["Fatal runtime error"])
    manager, repo_dir = _setup(tmp_path, config=config)

    result = _run(manager, repo_dir, "__SCENARIO:crash_rc1__")

    assert result.state == JobState.NEEDS_REVIEW
    assert get_job(result.job_id, manager.state_dir).review_reason == "quota_exhausted"
    manager.shutdown()


def test_no_false_quota_exhaustion_without_signal(tmp_path: Path) -> None:
    """A failed job with healthy quota and no patterns stays failed."""
    _scenario(tmp_path, "usage_real")
    manager, repo_dir = _setup(tmp_path)

    result = _run(manager, repo_dir, "__SCENARIO:crash_rc1__")

    assert result.state == JobState.FAILED
    job = get_job(result.job_id, manager.state_dir)
    assert job.review_reason is None
    assert result.resume_after is None
    manager.shutdown()


def test_quota_exhausted_job_is_resumable(tmp_path: Path) -> None:
    """quota_exhausted is a non-security needs_review reason and can be resumed."""
    _scenario(tmp_path, "usage_zero")
    manager, repo_dir = _setup(tmp_path)

    result = _run(manager, repo_dir, "__SCENARIO:crash_rc1__")
    assert get_job(result.job_id, manager.state_dir).review_reason == "quota_exhausted"

    resumed = manager.resume(result.job_id, "Continue after quota reset")
    assert resumed.state == JobState.RUNNING
    job = get_job(result.job_id, manager.state_dir)
    assert job.resume_after is None or job.state == JobState.RUNNING
    manager.cancel(result.job_id)
    manager.shutdown()


def test_config_quota_error_patterns_validation(tmp_path: Path) -> None:
    """quota_error_patterns defaults to [] and validates list[str] plus regex syntax."""
    cfg = load_config(path=tmp_path / "missing.toml")
    assert cfg.quota_error_patterns == []

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        'quota_error_patterns = ["quota exceeded", "resource[ ]exhausted"]\n',
        encoding="utf-8",
    )
    cfg = load_config(path=cfg_file)
    assert cfg.quota_error_patterns == ["quota exceeded", "resource[ ]exhausted"]

    for bad in ('"quota"', "[1, 2]", '["bad[regex"]'):
        cfg_file.write_text(f"quota_error_patterns = {bad}\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(path=cfg_file)
