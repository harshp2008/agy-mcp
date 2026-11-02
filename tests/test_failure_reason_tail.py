"""Tests for finding F8: failed jobs must carry structured failure reason and error tail."""

import time
from pathlib import Path

from agy_mcp.config import Config
from agy_mcp.jobs import get_job
from agy_mcp.manager import JobManager
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

    cfg = config or Config()
    mgr = JobManager(
        runner=runner,
        config=cfg,
        base_state_dir=state_dir,
        home=home,
        settle_window_seconds=0.05,
    )
    return mgr, repo_dir


def test_failed_job_rc1_carries_error_envelope(tmp_path: Path) -> None:
    """Exit code 1 must populate structured error with cli_error reason and wrapped stderr tail."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    job_envelope = manager.start(
        repo_path=str(repo_dir),
        prompt="__SCENARIO:crash_rc1__",
        title="F8 crash rc1 test",
    )
    job_id = job_envelope.job_id

    # Wait for completion
    for _ in range(100):
        j = get_job(job_id, manager.state_dir)
        if j.state not in (JobState.QUEUED, JobState.RUNNING):
            break
        time.sleep(0.05)

    j = get_job(job_id, manager.state_dir)
    assert j.state == JobState.FAILED

    st = manager.status(job_id)
    assert st.state == JobState.FAILED
    assert st.error is not None
    assert isinstance(st.error, dict)
    assert st.error.get("reason") == "cli_error"
    assert "Fatal error" in st.error.get("message", "")
    assert "<untrusted_text" in st.error.get("message", "")

    res = manager.result(job_id)
    assert res.state == JobState.FAILED
    assert res.error is not None
    assert isinstance(res.error, dict)
    assert res.error.get("reason") == "cli_error"
    assert "Fatal error" in res.error.get("message", "")
    assert "<untrusted_text" in res.error.get("message", "")
    assert "Fatal error" in res.response


def test_failed_job_signal_carries_error_envelope(tmp_path: Path) -> None:
    """Agy killed by signal must populate structured error with signal_exit reason and signal."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    job_envelope = manager.start(
        repo_path=str(repo_dir),
        prompt="__SCENARIO:crash_signal__",
        title="F8 crash signal test",
    )
    job_id = job_envelope.job_id

    for _ in range(100):
        j = get_job(job_id, manager.state_dir)
        if j.state not in (JobState.QUEUED, JobState.RUNNING):
            break
        time.sleep(0.05)

    j = get_job(job_id, manager.state_dir)
    assert j.state == JobState.FAILED

    st = manager.status(job_id)
    assert st.state == JobState.FAILED
    assert st.error is not None
    assert isinstance(st.error, dict)
    assert st.error.get("reason") == "signal_exit"
    assert st.error.get("signal") == 6  # SIGABRT
    assert "signal 6" in st.error.get("message", "").lower()


def test_failed_job_garbage_stdout_carries_error_envelope(tmp_path: Path) -> None:
    """Non-JSON stdout must populate structured error with json_parse_error reason."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    job_envelope = manager.start(
        repo_path=str(repo_dir),
        prompt="__SCENARIO:garbage_stdout__",
        title="F8 garbage stdout test",
    )
    job_id = job_envelope.job_id

    for _ in range(100):
        j = get_job(job_id, manager.state_dir)
        if j.state not in (JobState.QUEUED, JobState.RUNNING):
            break
        time.sleep(0.05)

    j = get_job(job_id, manager.state_dir)
    assert j.state == JobState.FAILED

    st = manager.status(job_id)
    assert st.state == JobState.FAILED
    assert st.error is not None
    assert isinstance(st.error, dict)
    assert st.error.get("reason") == "json_parse_error"
    assert "<untrusted_text" in st.error.get("message", "")

