"""Tests for finding F1: Watcher-initiated needs_review kills process and enables resume."""

import os
import threading
import time
from pathlib import Path

import pytest

from agy_mcp.config import Config
from agy_mcp.gitops import run_git
from agy_mcp.jobs import get_job
from agy_mcp.manager import JobManager
from agy_mcp.models import JobState
from agy_mcp.runner import LocalProcessRunner
from agy_mcp.util import strict_uuid
from tests.helpers.make_repo import get_safe_git_env, make_repo


def _is_alive(pid: int) -> bool:
    try:
        stat_path = Path(f"/proc/{pid}/stat")
        if not stat_path.exists():
            return False
        content = stat_path.read_text(encoding="utf-8", errors="replace")
        rparen = content.rfind(")")
        if rparen != -1:
            state = content[rparen + 1 :].strip().split()[0]
            if state in ("Z", "X"):
                return False
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


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
        settle_window_seconds=0.1,  # Fast settle for testing
    )
    return mgr, repo_dir


def test_f1_partial_commit_kills_process_and_resumes(tmp_path: Path) -> None:
    """Watcher-driven needs_review(partial_commit) must kill process and persist conversation_id."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)

    # Pre-track a sensitive .env file in the repository
    env_file = repo_dir / ".env"
    env_file.write_text("SECRET=initial\n", encoding="utf-8")
    run_git(["add", ".env"], repo_dir)
    run_git(["commit", "-m", "Track sensitive .env"], repo_dir)

    env = manager.start(
        prompt="Work on task __SCENARIO:hang_after_partial_commit__",
        repo_path=str(repo_dir),
        title="F1 Partial Commit Test",
    )

    # Wait for needs_review
    deadline = time.time() + 10.0
    job = None
    while time.time() < deadline:
        st = manager.status(env.job_id)
        if st.state == JobState.NEEDS_REVIEW:
            job = get_job(env.job_id, manager.state_dir)
            break
        time.sleep(0.1)

    assert job is not None
    assert job.state == JobState.NEEDS_REVIEW
    assert job.review_reason == "partial_commit"
    assert job.pid is not None

    # Wait briefly to confirm process is killed by supervisor finalization
    kill_deadline = time.time() + 5.0
    while time.time() < kill_deadline and _is_alive(job.pid):
        time.sleep(0.05)

    # CRITICAL CHECK: process must be dead
    assert not _is_alive(job.pid), f"PID {job.pid} is still alive after partial_commit needs_review"

    # Refresh job state from disk after supervisor finalization
    poll_deadline = time.time() + 5.0
    while time.time() < poll_deadline:
        job = get_job(env.job_id, manager.state_dir)
        if job.conversation_id is not None:
            break
        time.sleep(0.05)

    # conversation_id must be persisted
    assert job.conversation_id is not None
    assert strict_uuid(job.conversation_id) == job.conversation_id

    # Resume must succeed
    res = manager.resume(job.job_id, "User approves partial commit")
    assert res.state in (JobState.RUNNING, JobState.DONE)
    final_env = manager.wait(job.job_id, seconds=5)
    assert final_env.state == JobState.DONE


def test_f1_invalid_commit_msg_kills_process_and_resumes(tmp_path: Path) -> None:
    """Watcher-driven invalid_commit_message must kill process and persist conversation_id."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)

    env = manager.start(
        prompt="Work on task __SCENARIO:hang_after_invalid_commit_msg__",
        repo_path=str(repo_dir),
        title="F1 Invalid Commit Msg Test",
    )

    # Wait for needs_review
    deadline = time.time() + 10.0
    job = None
    while time.time() < deadline:
        st = manager.status(env.job_id)
        if st.state == JobState.NEEDS_REVIEW:
            job = get_job(env.job_id, manager.state_dir)
            break
        time.sleep(0.1)

    assert job is not None
    assert job.state == JobState.NEEDS_REVIEW
    assert job.review_reason == "invalid_commit_message"
    assert job.pid is not None

    # Wait briefly to confirm process is killed
    kill_deadline = time.time() + 5.0
    while time.time() < kill_deadline and _is_alive(job.pid):
        time.sleep(0.05)

    # CRITICAL CHECK: process must be dead
    assert not _is_alive(job.pid), (
        f"PID {job.pid} is still alive after invalid_commit_msg needs_review"
    )

    # Refresh job state from disk after supervisor finalization
    poll_deadline = time.time() + 5.0
    while time.time() < poll_deadline:
        job = get_job(env.job_id, manager.state_dir)
        if job.conversation_id is not None:
            break
        time.sleep(0.05)

    # conversation_id must be persisted
    assert job.conversation_id is not None
    assert strict_uuid(job.conversation_id) == job.conversation_id

    # Resume must succeed
    res = manager.resume(job.job_id, "Fixed commit message format")
    assert res.state in (JobState.RUNNING, JobState.DONE)
    final_env = manager.wait(job.job_id, seconds=5)
    assert final_env.state == JobState.DONE


def test_f1_resume_serializes_with_previous_supervisor_finalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resume must not overlap the previous supervisor's finalization.

    The previous supervisor reads job.json, then writes it back while retaining
    the scratchpad. If resume starts a new generation in that window, the stale
    write puts ``needs_review`` back over ``running`` (and the old supervisor
    unregisters the new one), so agy_wait returns needs_review and the resumed
    run looks failed. Blocking the previous supervisor in that final write makes
    the interleaving deterministic.
    """
    manager, repo_dir = _setup_manager_and_repo(tmp_path)

    original_retain = manager._retain_scratchpad
    retain_calls: list[str] = []
    retain_lock = threading.Lock()
    retain_started = threading.Event()
    retain_done = threading.Event()

    def delayed_retain(job) -> None:
        with retain_lock:
            is_first = not retain_calls
            retain_calls.append(job.job_id)
        if is_first:
            retain_started.set()
            time.sleep(0.5)
        try:
            original_retain(job)
        finally:
            if is_first:
                retain_done.set()

    monkeypatch.setattr(manager, "_retain_scratchpad", delayed_retain)

    env = manager.start(
        prompt="Work on task __SCENARIO:hang_after_invalid_commit_msg__",
        repo_path=str(repo_dir),
        title="F1 Resume Serialization Test",
    )

    deadline = time.time() + 10.0
    job = None
    while time.time() < deadline:
        st = manager.status(env.job_id)
        if st.state == JobState.NEEDS_REVIEW:
            job = get_job(env.job_id, manager.state_dir)
            break
        time.sleep(0.1)
    assert job is not None
    assert job.review_reason == "invalid_commit_message"

    kill_deadline = time.time() + 5.0
    while time.time() < kill_deadline and _is_alive(job.pid):
        time.sleep(0.05)
    assert not _is_alive(job.pid)

    poll_deadline = time.time() + 5.0
    while time.time() < poll_deadline:
        job = get_job(env.job_id, manager.state_dir)
        if job.conversation_id is not None:
            break
        time.sleep(0.05)
    assert job.conversation_id is not None

    # The previous supervisor has already read job.json and is now stuck in its
    # final write. Resuming here is exactly the production race.
    assert retain_started.wait(timeout=5.0)
    res = manager.resume(job.job_id, "Fixed commit message format")
    assert res.state in (JobState.RUNNING, JobState.DONE)
    assert retain_done.wait(timeout=5.0)

    resumed = get_job(job.job_id, manager.state_dir)
    assert resumed.state != JobState.NEEDS_REVIEW, (
        f"previous supervisor clobbered the resumed generation: "
        f"state={resumed.state} reason={resumed.review_reason} "
        f"run_generation={resumed.run_generation}"
    )
    final_env = manager.wait(job.job_id, seconds=10)
    assert final_env.state == JobState.DONE
