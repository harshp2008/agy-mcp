"""Tests for finding F13: Concurrency cap atomic checking, queued counting, and flock protection."""

import threading
from pathlib import Path

from agy_mcp.config import Config
from agy_mcp.jobs import count_active_jobs, create_job, save_job
from agy_mcp.manager import JobManager, ManagerError
from agy_mcp.models import JobState
from agy_mcp.runner import LocalProcessRunner
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

    cfg = config or Config(max_concurrent_jobs=1)
    mgr = JobManager(
        runner=runner,
        config=cfg,
        base_state_dir=state_dir,
        home=home,
        settle_window_seconds=0.1,
    )
    return mgr, repo_dir


def test_f13_count_active_jobs_includes_queued(tmp_path: Path) -> None:
    """count_active_jobs must include QUEUED jobs in the active count."""
    state_dir = tmp_path / "state"
    job = create_job(
        repo_path=tmp_path / "repo",
        title="Queued Count Job",
        prompt="prompt",
        model="gemini-3.8-flash-low",
        max_minutes=60,
        base_state_dir=state_dir,
        job_id="job-queued-1",
    )
    job.state = JobState.QUEUED
    save_job(job, state_dir)

    active = count_active_jobs(state_dir)
    assert active == 1, f"Expected 1 active job for QUEUED state, got {active}"


def test_f13_simultaneous_starts_never_exceed_concurrency_cap(tmp_path: Path) -> None:
    """Starting multiple jobs concurrently must never exceed max_concurrent_jobs."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path, config=Config(max_concurrent_jobs=1))

    started = []
    rejected = []
    barrier = threading.Barrier(3)

    def _worker(idx: int) -> None:
        barrier.wait()
        try:
            env = manager.start(
                prompt=f"Task {idx} __SCENARIO:hang_after_partial_commit__",
                repo_path=str(repo_dir),
                title=f"Concurrent Job {idx}",
            )
            started.append(env)
        except ManagerError as exc:
            if exc.code == "concurrency_limit_exceeded":
                rejected.append(exc)
            else:
                raise

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # With max_concurrent_jobs = 1, exactly 1 must succeed and 2 must be rejected
    assert len(started) == 1, (
        f"Expected exactly 1 job to start under max_concurrent_jobs=1, got {len(started)}"
    )
    assert len(rejected) == 2, f"Expected 2 rejections, got {len(rejected)}"


def test_f13_resume_respects_cap_and_does_not_double_count(tmp_path: Path) -> None:
    """Resume must respect concurrency limit and not double-count the resuming job."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path, config=Config(max_concurrent_jobs=1))

    # Start a job that will pause in needs_decision
    env1 = manager.start(
        prompt="Job 1 __SCENARIO:needs_decision__",
        repo_path=str(repo_dir),
        title="Job 1",
    )
    manager.wait(env1.job_id, seconds=5)
    j1 = manager.status(env1.job_id)
    assert j1.state == JobState.NEEDS_DECISION

    # When j1 is in NEEDS_DECISION, active_count is 0, so resume should succeed
    res1 = manager.resume(env1.job_id, answer="proceed")
    assert res1.state == JobState.RUNNING

    # Now active_count is 1 (job1 is running). Starting job2 should fail concurrency cap
    try:
        manager.start(
            prompt="Job 2",
            repo_path=str(repo_dir),
            title="Job 2",
        )
        assert False, "Expected concurrency_limit_exceeded"
    except ManagerError as exc:
        assert exc.code == "concurrency_limit_exceeded"
