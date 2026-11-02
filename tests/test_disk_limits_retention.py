"""Tests for finding F22: disk limits, retention cleanup to trash, and usage recording."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agy_mcp.config import Config
from agy_mcp.gitops import run_git
from agy_mcp.jobs import (
    cleanup_retained_jobs,
    create_job,
    get_job,
    get_job_dir,
    save_job,
)
from agy_mcp.manager import JobManager, ManagerError
from agy_mcp.models import JobState
from agy_mcp.runner import LocalProcessRunner
from agy_mcp.worktree import create_worktree
from tests.helpers.make_repo import get_safe_git_env, make_repo


def _setup_manager_and_repo(
    tmp_path: Path, config: Config | None = None
) -> tuple[JobManager, Path]:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = home / "repo"
    make_repo(repo_dir, initial_commit=True, env=get_safe_git_env(home))

    fake_agy_path = Path(__file__).parent / "fake_agy" / "agy"
    runner = LocalProcessRunner(executable=str(fake_agy_path))

    cfg = config or Config()
    mgr = JobManager(
        runner=runner,
        config=cfg,
        base_state_dir=state_dir,
        home=home,
        settle_window_seconds=0.05,
    )
    return mgr, repo_dir


def test_min_free_gb_enforced_at_start(tmp_path: Path) -> None:
    """agy_start must check min_free_gb and refuse with insufficient_disk_space before start."""
    cfg = Config(min_free_gb=9999999.0)
    manager, repo_dir = _setup_manager_and_repo(tmp_path, config=cfg)

    with pytest.raises(ManagerError) as exc_info:
        manager.start(
            prompt="Simple prompt",
            repo_path=str(repo_dir),
            title="Disk Check Test",
        )
    assert exc_info.value.code == "insufficient_disk_space"


def test_max_state_disk_gb_enforced_at_start(tmp_path: Path) -> None:
    """agy_start must check max_state_disk_gb and refuse when state directory exceeds cap."""
    cfg = Config(max_state_disk_gb=0.000001)  # ~1 KB cap
    manager, repo_dir = _setup_manager_and_repo(tmp_path, config=cfg)

    # Put a 10 KB file in state directory
    dummy_job_dir = manager.state_dir / "jobs" / "dummy-job"
    dummy_job_dir.mkdir(parents=True, exist_ok=True)
    (dummy_job_dir / "big.bin").write_bytes(b"0" * 10240)

    with pytest.raises(ManagerError) as exc_info:
        manager.start(
            prompt="Simple prompt",
            repo_path=str(repo_dir),
            title="Max Disk Test",
        )
    assert exc_info.value.code == "state_disk_full"


def test_retention_cleanup_moves_terminal_jobs_to_trash(tmp_path: Path) -> None:
    """Finished jobs older than retention_days must be moved to trash directory, not deleted."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    state_dir = manager.state_dir

    job = create_job(
        repo_path=repo_dir,
        title="Old Done Job",
        prompt="p",
        model="gemini-3.8-flash",
        max_minutes=10,
        base_state_dir=state_dir,
    )
    job.state = JobState.DONE
    # Set created_at to 20 days ago
    old_ts = time.time() - (20 * 86400)
    job.created_at = datetime.fromtimestamp(old_ts, tz=timezone.utc).isoformat()
    save_job(job, state_dir)

    job_dir = get_job_dir(job.job_id, state_dir)
    assert job_dir.is_dir()

    cleaned = cleanup_retained_jobs(state_dir, retention_days=14)
    assert job.job_id in cleaned
    assert not job_dir.exists()

    trash_dir = state_dir / "trash"
    assert trash_dir.is_dir()
    trashed_items = list(trash_dir.glob(f"{job.job_id}*"))
    assert len(trashed_items) == 1
    assert (trashed_items[0] / "job.json").is_file()


def test_retention_cleanup_never_prunes_running_job(tmp_path: Path) -> None:
    """Running jobs must never be pruned regardless of age."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    state_dir = manager.state_dir

    job = create_job(
        repo_path=repo_dir,
        title="Old Running Job",
        prompt="p",
        model="gemini-3.8-flash",
        max_minutes=10,
        base_state_dir=state_dir,
    )
    job.state = JobState.RUNNING
    old_ts = time.time() - (20 * 86400)
    job.created_at = datetime.fromtimestamp(old_ts, tz=timezone.utc).isoformat()
    save_job(job, state_dir)

    job_dir = get_job_dir(job.job_id, state_dir)
    cleaned = cleanup_retained_jobs(state_dir, retention_days=14)
    assert job.job_id not in cleaned
    assert job_dir.is_dir()


def test_retention_cleanup_preserves_unmerged_branch(tmp_path: Path) -> None:
    """Retention cleanup must not delete branches holding commits not in default branch."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    state_dir = manager.state_dir

    job = create_job(
        repo_path=repo_dir,
        title="Old Job Unmerged Branch",
        prompt="p",
        model="gemini-3.8-flash",
        max_minutes=10,
        base_state_dir=state_dir,
    )
    job_dir = get_job_dir(job.job_id, state_dir)
    wt_path, branch, head = create_worktree(
        repo_path=repo_dir,
        job_dir=job_dir,
        title=job.title,
        base_state_dir=state_dir,
    )
    job.worktree_path = wt_path
    job.branch = branch

    # Create an unmerged commit on the job branch
    test_file = wt_path / "unique_feature.txt"
    test_file.write_text("unmerged work\n")
    run_git(["add", "unique_feature.txt"], wt_path)
    run_git(["commit", "-m", "unmerged commit on branch"], wt_path)

    job.state = JobState.DONE
    old_ts = time.time() - (20 * 86400)
    job.created_at = datetime.fromtimestamp(old_ts, tz=timezone.utc).isoformat()
    save_job(job, state_dir)

    cleanup_retained_jobs(state_dir, retention_days=14)

    # Branch must still exist in git repository
    branch_check = run_git(["branch", "--list", branch], repo_dir)
    assert branch in branch_check.stdout


def test_record_usage_written_on_job_completion(tmp_path: Path) -> None:
    """Completed job must record usage metrics into usage.jsonl without prompt or title."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="__SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Usage Metrics Test Title",
    )
    job_id = env.job_id

    for _ in range(100):
        j = get_job(job_id, manager.state_dir)
        if j.state != JobState.RUNNING and j.state != JobState.QUEUED:
            break
        time.sleep(0.05)

    j = get_job(job_id, manager.state_dir)
    assert j.state == JobState.DONE

    usage_file = manager.state_dir / "usage.jsonl"
    assert usage_file.is_file()
    lines = usage_file.read_text().splitlines()
    assert len(lines) >= 1
    last_record = json.loads(lines[-1])
    assert last_record["job_id"] == job_id
    assert last_record["state"] == "done"
    assert "duration_seconds" in last_record
    assert "tokens" in last_record
    assert "Usage Metrics Test Title" not in lines[-1]
    assert "success_simple" not in lines[-1]


def test_usage_record_is_written_before_done_is_observable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D68: seeing state==done in job.json must imply usage.jsonl already exists.

    The supervisor finalizes on a background thread. With the old ordering the
    terminal state was persisted first and ``record_usage`` appended afterwards,
    so a reader could observe ``done`` for several milliseconds while
    ``usage.jsonl`` was still missing (that is why the F22 test flaked). The
    500 ms delay injected here makes that window deterministic instead of
    timing-dependent.
    """
    from agy_mcp import usage as usage_module

    real_record_usage = usage_module.record_usage

    def slow_record_usage(*args: object, **kwargs: object) -> None:
        time.sleep(0.5)
        real_record_usage(*args, **kwargs)

    monkeypatch.setattr(usage_module, "record_usage", slow_record_usage)

    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="__SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Usage Ordering Test Title",
    )
    job_id = env.job_id

    deadline = time.time() + 30.0
    while time.time() < deadline:
        job = get_job(job_id, manager.state_dir)
        if job.state not in (JobState.RUNNING, JobState.QUEUED):
            break
        time.sleep(0.01)

    job = get_job(job_id, manager.state_dir)
    assert job.state == JobState.DONE
    assert (manager.state_dir / "usage.jsonl").is_file(), (
        "state==done was observable before usage.jsonl was appended"
    )
    manager.shutdown(timeout=10.0)
