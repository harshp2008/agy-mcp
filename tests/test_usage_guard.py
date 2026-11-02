"""Tests for the start/resume usage guard modes (Item 4)."""

import time
from pathlib import Path
from typing import Callable

import pytest

from agy_mcp.config import Config, ConfigError, load_config
from agy_mcp.jobs import get_job
from agy_mcp.manager import JobManager, ManagerError
from agy_mcp.models import JobState
from agy_mcp.runner import LocalProcessRunner
from agy_mcp.usage import group_for_model
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"


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
    runner = LocalProcessRunner(executable=FAKE_AGY)
    manager = JobManager(
        config=config or Config(),
        runner=runner,
        base_state_dir=tmp_path / "state",
        home=tmp_path,
        settle_window_seconds=0.05,
        poll_interval_seconds=0.05,
    )
    return manager, repo_dir


def _scenario(tmp_path: Path, name: str) -> None:
    (tmp_path / ".fake_agy_usage_scenario").write_text(name, encoding="utf-8")


def _start(manager: JobManager, repo_dir: Path, model: str = "gemini-3.8-flash-medium"):
    return manager.start(
        prompt="Perform task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Usage Guard Test",
        model=model,
    )


def test_guard_warn_adds_low_quota_warning(tmp_path: Path) -> None:
    """warn mode allows the start and adds warning low_quota."""
    _scenario(tmp_path, "usage_zero")
    manager, repo_dir = _setup(tmp_path, Config(usage_guard="warn"))

    env = _start(manager, repo_dir)
    try:
        assert "low_quota" in env.warnings
        assert env.state == JobState.QUEUED
    finally:
        manager.cancel(env.job_id)
        manager.shutdown()


def test_guard_block_refuses_with_reset_times(tmp_path: Path) -> None:
    """block mode refuses with error.code=low_quota and reset times."""
    _scenario(tmp_path, "usage_zero")
    manager, repo_dir = _setup(tmp_path, Config(usage_guard="block"))

    with pytest.raises(ManagerError) as exc_info:
        _start(manager, repo_dir)
    assert exc_info.value.code == "low_quota"
    assert "2026-11-01T09:54:57Z" in exc_info.value.message
    assert "2026-10-25T19:54:57Z" in exc_info.value.message


def test_guard_off_never_warns_or_blocks(tmp_path: Path) -> None:
    """off mode neither warns nor blocks even at 0%."""
    _scenario(tmp_path, "usage_zero")
    manager, repo_dir = _setup(tmp_path, Config(usage_guard="off"))

    env = _start(manager, repo_dir)
    try:
        assert "low_quota" not in env.warnings
        assert "usage_unavailable" not in env.warnings
    finally:
        manager.cancel(env.job_id)
        manager.shutdown()


def test_guard_unavailable_never_blocks_in_block_mode(tmp_path: Path) -> None:
    """Unavailable usage warns but never blocks, even in block mode."""
    _scenario(tmp_path, "usage_garbled")
    manager, repo_dir = _setup(tmp_path, Config(usage_guard="block"))

    env = _start(manager, repo_dir)
    try:
        assert "usage_unavailable" in env.warnings
        assert "low_quota" not in env.warnings
    finally:
        manager.cancel(env.job_id)
        manager.shutdown()


def test_guard_maps_model_to_group(tmp_path: Path) -> None:
    """gemini* and claude*/gpt* map to their respective usage groups."""
    assert group_for_model("gemini-3.8-flash-medium") == "Gemini Models"
    assert group_for_model("claude-3-7-sonnet") == "Claude and GPT models"
    assert group_for_model("gpt-5") == "Claude and GPT models"
    assert group_for_model("unknown-model") is None

    _scenario(tmp_path, "usage_zero_claude")
    config = Config(
        usage_guard="block",
        model_allowlist=["gemini-3.8-flash-medium", "claude-3-7-sonnet"],
    )
    manager, repo_dir = _setup(tmp_path, config)

    # Gemini group still has quota, so it is not blocked.
    env = _start(manager, repo_dir, model="gemini-3.8-flash-medium")
    manager.cancel(env.job_id)
    manager.shutdown()

    with pytest.raises(ManagerError) as exc_info:
        _start(manager, repo_dir, model="claude-3-7-sonnet")
    assert exc_info.value.code == "low_quota"
    assert "Claude and GPT models" in exc_info.value.message


def test_guard_applies_on_resume_block(tmp_path: Path) -> None:
    """Resume is refused with low_quota when the relevant group is exhausted."""
    _scenario(tmp_path, "usage_real")
    manager, repo_dir = _setup(tmp_path, Config(usage_guard="block"))

    env = manager.start(
        prompt="Choose __SCENARIO:needs_decision__",
        repo_path=str(repo_dir),
        title="Resume Guard Test",
        model="gemini-3.8-flash-medium",
    )
    try:
        poll_until(
            lambda: get_job(env.job_id, manager.state_dir).state == JobState.NEEDS_DECISION
        )
        _scenario(tmp_path, "usage_zero")
        manager._usage_cache.clear()

        with pytest.raises(ManagerError) as exc_info:
            manager.resume(job_id=env.job_id, answer="Pick PostgreSQL")
        assert exc_info.value.code == "low_quota"
        assert "2026-11-01T09:54:57Z" in exc_info.value.message
    finally:
        manager.shutdown()


def test_guard_applies_on_resume_warn(tmp_path: Path) -> None:
    """Resume in warn mode proceeds and adds low_quota to the envelope."""
    _scenario(tmp_path, "usage_real")
    manager, repo_dir = _setup(tmp_path, Config(usage_guard="warn"))

    env = manager.start(
        prompt="Choose __SCENARIO:needs_decision__",
        repo_path=str(repo_dir),
        title="Resume Guard Warn Test",
        model="gemini-3.8-flash-medium",
    )
    try:
        poll_until(
            lambda: get_job(env.job_id, manager.state_dir).state == JobState.NEEDS_DECISION
        )
        _scenario(tmp_path, "usage_zero")
        manager._usage_cache.clear()

        resumed = manager.resume(job_id=env.job_id, answer="Pick PostgreSQL")
        assert "low_quota" in resumed.warnings
    finally:
        manager.cancel(env.job_id)
        manager.shutdown()


def test_config_usage_guard_defaults_and_validation(tmp_path: Path) -> None:
    """usage_guard defaults to warn and only accepts off|warn|block."""
    cfg = load_config(path=tmp_path / "missing.toml")
    assert cfg.usage_guard == "warn"

    cfg_file = tmp_path / "config.toml"
    for value in ("off", "warn", "block"):
        cfg_file.write_text(f'usage_guard = "{value}"\n', encoding="utf-8")
        assert load_config(path=cfg_file).usage_guard == value

    for bad in ('"sometimes"', "1", "true"):
        cfg_file.write_text(f"usage_guard = {bad}\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(path=cfg_file)


@pytest.mark.parametrize(
    ("guard", "expect_warning"),
    [("warn", True), ("block", True), ("off", False)],
)
def test_guard_unmeasurable_model_group_warns_and_never_blocks(
    tmp_path: Path, guard: str, expect_warning: bool
) -> None:
    """An available reading with no determinable group warns and never blocks."""
    _scenario(tmp_path, "usage_real")
    config = Config(usage_guard=guard, model_allowlist=["mystery-model"])
    manager, repo_dir = _setup(tmp_path, config)

    env = _start(manager, repo_dir, model="mystery-model")
    try:
        assert ("usage_unmeasurable" in env.warnings) is expect_warning
        assert "low_quota" not in env.warnings
    finally:
        manager.cancel(env.job_id)
        manager.shutdown()
