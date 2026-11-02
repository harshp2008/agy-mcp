"""A3: `checkpoint_commit_minutes` must reach the CommitWatcher."""

import time
from pathlib import Path
from typing import Callable

import pytest

from agy_mcp.config import Config, ConfigError, load_config
from agy_mcp.manager import JobManager
from agy_mcp.runner import LocalProcessRunner
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


def _start_hanging_job(tmp_path: Path, config: Config) -> tuple[JobManager, str]:
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    manager = JobManager(
        config=config,
        runner=LocalProcessRunner(executable=FAKE_AGY),
        base_state_dir=tmp_path / "state",
        home=tmp_path,
        settle_window_seconds=0.05,
        poll_interval_seconds=0.05,
    )
    env = manager.start(
        prompt="Hang __SCENARIO:hang__",
        repo_path=str(repo_dir),
        title="Checkpoint Interval Test",
    )
    poll_until(lambda: manager._supervisors.get(env.job_id) is not None)
    return manager, env.job_id


def _watch_interval(manager: JobManager, job_id: str) -> int:
    supervisor = manager._supervisors[job_id]
    return supervisor.watcher.checkpoint_interval_seconds


def test_default_checkpoint_interval_is_600(tmp_path: Path) -> None:
    """The default checkpoint_commit_minutes=10 yields the historical 600 s."""
    manager, job_id = _start_hanging_job(tmp_path, Config())
    try:
        assert _watch_interval(manager, job_id) == 600
    finally:
        manager.cancel(job_id)
        manager.shutdown()


def test_checkpoint_minutes_one_reaches_watcher_as_60(tmp_path: Path) -> None:
    """Setting checkpoint_commit_minutes=1 changes the watcher interval to 60 s."""
    manager, job_id = _start_hanging_job(tmp_path, Config(checkpoint_commit_minutes=1))
    try:
        assert _watch_interval(manager, job_id) == 60
    finally:
        manager.cancel(job_id)
        manager.shutdown()


def test_invalid_checkpoint_values_are_still_rejected(tmp_path: Path) -> None:
    """Config validation still rejects zero and non-integer intervals."""
    cfg_file = tmp_path / "config.toml"

    cfg_file.write_text("checkpoint_commit_minutes = 0\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path=cfg_file)

    cfg_file.write_text('checkpoint_commit_minutes = "ten"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path=cfg_file)
