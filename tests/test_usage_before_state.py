"""C4: the D68 ordering (usage row before the state becomes observable) must
also hold on the paths that persist a terminal or needs_review state outside
_finalize_terminated_process:

- watcher-driven finalization (invalid COMMIT_MSG, audit violation, partial
  commit) -> _finalize_non_running_process appends the usage row afterwards;
- the supervisor's 3-consecutive-tick-failures fail-closed path;
- crash recovery finalizing a dead process (transition, then record).

Each test delays usage.record_usage by 500 ms and polls job.json from another
thread. If the new state is observable while record_usage is still sleeping,
the reader has observed a state whose usage row does not exist yet.
"""

import json
import threading
import time
from pathlib import Path

import pytest

from agy_mcp.audit import save_snapshot, take_snapshot
from agy_mcp.jobs import create_job, get_job, get_job_dir, save_job, transition_job_state
from agy_mcp.models import Job
from agy_mcp.recovery import reconcile_on_startup
from agy_mcp.runner import LocalProcessRunner, build_argv
from agy_mcp.states import JobState
from agy_mcp.supervisor import JobSupervisor
from agy_mcp.usage import record_usage
from agy_mcp.util import env_allowlist
from agy_mcp.watcher import CommitWatcher
from agy_mcp.worktree import create_worktree
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"


def _make_usage_slow(monkeypatch: pytest.MonkeyPatch, delay: float = 0.5) -> list[JobState]:
    """Delay the real usage append; returns the recorded target states."""
    import agy_mcp.usage as usage_mod

    recorded = []
    real = usage_mod.record_usage

    def slow_record_usage(*args, **kwargs):
        recorded.append(kwargs.get("state"))
        time.sleep(delay)
        return real(*args, **kwargs)

    monkeypatch.setattr(usage_mod, "record_usage", slow_record_usage)
    return recorded


def _usage_rows(state_dir: Path) -> list[dict]:
    path = state_dir / "usage.jsonl"
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _setup_running_job(tmp_path: Path, title: str) -> tuple[Job, Path, Path]:
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    job = create_job(
        repo_path=repo_dir,
        title=title,
        prompt="Execute task",
        model="gemini-3.8-flash-medium",
        max_minutes=60,
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
    job.head_commit = head
    transition_job_state(
        job, JobState.RUNNING, event_trigger="Process launched", base_state_dir=state_dir
    )
    save_snapshot(take_snapshot(job, home=tmp_path), job_dir / "snapshot.json")
    return job, job_dir, state_dir


def _launch_fake_agy(
    job: Job, job_dir: Path, tmp_path: Path, scenario: str
) -> tuple[LocalProcessRunner, object]:
    runner = LocalProcessRunner(executable=FAKE_AGY)
    env = env_allowlist(job_tmp_dir=job_dir / "tmp", home=tmp_path)
    argv = build_argv(
        agy_binary=FAKE_AGY,
        prompt=f"Work on task __SCENARIO:{scenario}__",
        model="gemini-3.8-flash-medium",
        print_timeout_seconds=60,
        log_file=job_dir / "cli.log",
    )
    handle = runner.launch(
        job_id=job.job_id,
        workdir=job.worktree_path,
        args=argv,
        env=env,
        stdout_path=job_dir / "stdout.txt",
        stderr_path=job_dir / "stderr.txt",
    )
    return runner, handle


def _poll_state(state_dir: Path, job_id: str, deadline_seconds: float = 20.0):
    deadline = time.time() + deadline_seconds
    while time.time() < deadline:
        try:
            current = get_job(job_id, state_dir)
        except FileNotFoundError:
            current = None
        if current is not None and current.state not in (JobState.QUEUED, JobState.RUNNING):
            return current
        time.sleep(0.005)
    return None


def test_usage_row_exists_when_watcher_review_is_observable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job, job_dir, state_dir = _setup_running_job(tmp_path, "C4 Watcher Review")
    runner, handle = _launch_fake_agy(job, job_dir, tmp_path, "hang_after_invalid_commit_msg")
    supervisor = JobSupervisor(
        job=job,
        handle=handle,
        runner=runner,
        job_dir=job_dir,
        max_minutes=60,
        home=tmp_path,
        base_state_dir=state_dir,
        settle_window_seconds=0.1,
        poll_interval_seconds=0.02,
    )
    _make_usage_slow(monkeypatch)
    thread = threading.Thread(target=supervisor.run_loop, daemon=True)
    thread.start()
    try:
        observed = _poll_state(state_dir, job.job_id)
        assert observed is not None, "the job never left running"
        rows = _usage_rows(state_dir)
        assert rows, (
            f"state {observed.state.value!r} was observable in job.json before the "
            "usage.jsonl row existed"
        )
    finally:
        supervisor.stop()
        try:
            runner.kill(handle, grace_seconds=0.2)
        except Exception:
            pass
        runner.cleanup(handle)
        thread.join(timeout=10)


def test_usage_row_exists_when_tick_failure_state_is_observable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job, job_dir, state_dir = _setup_running_job(tmp_path, "C4 Tick Failure")
    runner, handle = _launch_fake_agy(job, job_dir, tmp_path, "hang")

    def failing_tick(self, now=None):
        raise RuntimeError("injected tick failure")

    monkeypatch.setattr(CommitWatcher, "tick", failing_tick)
    supervisor = JobSupervisor(
        job=job,
        handle=handle,
        runner=runner,
        job_dir=job_dir,
        max_minutes=60,
        home=tmp_path,
        base_state_dir=state_dir,
        poll_interval_seconds=0.02,
    )
    _make_usage_slow(monkeypatch)
    thread = threading.Thread(target=supervisor.run_loop, daemon=True)
    thread.start()
    try:
        observed = _poll_state(state_dir, job.job_id)
        assert observed is not None, "the job never left running"
        assert observed.state == JobState.FAILED
        rows = _usage_rows(state_dir)
        assert rows, (
            "state 'failed' was observable in job.json before the usage.jsonl row existed"
        )
    finally:
        supervisor.stop()
        try:
            runner.kill(handle, grace_seconds=0.2)
        except Exception:
            pass
        runner.cleanup(handle)
        thread.join(timeout=10)


def test_recovery_records_usage_before_the_recovered_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    job = create_job(
        repo_path=repo_dir,
        title="C4 Recovery",
        prompt="Execute task",
        model="gemini-3.8-flash-medium",
        max_minutes=60,
        base_state_dir=state_dir,
    )
    job_dir = get_job_dir(job.job_id, state_dir)
    (job_dir / "stdout.txt").write_text(
        json.dumps(
            {
                "status": "SUCCESS",
                "response": "recovered work",
                "usage": {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10},
            }
        ),
        encoding="utf-8",
    )
    job.state = JobState.RUNNING
    job.pid = None
    job.start_time = None
    save_job(job, state_dir)

    _make_usage_slow(monkeypatch)
    runner = LocalProcessRunner(executable=FAKE_AGY)
    thread = threading.Thread(
        target=lambda: reconcile_on_startup(state_dir=state_dir, runner=runner, home=tmp_path),
        daemon=True,
    )
    thread.start()
    try:
        observed = _poll_state(state_dir, job.job_id)
        assert observed is not None, "recovery never finalized the dead job"
        rows = _usage_rows(state_dir)
        assert rows, (
            f"recovered state {observed.state.value!r} was observable in job.json before "
            "the usage.jsonl row existed"
        )
    finally:
        thread.join(timeout=10)


def test_recovery_does_not_duplicate_a_usage_row_for_the_same_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The D68 crash window: row appended, then the process dies before the state.

    Recovery must not append a second row for the same run generation, or the
    run's duration and cumulative minutes are counted twice in usage.jsonl.
    """
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    job = create_job(
        repo_path=repo_dir,
        title="C4 Crash Window",
        prompt="Execute task",
        model="gemini-3.8-flash-medium",
        max_minutes=60,
        base_state_dir=state_dir,
    )
    job_dir = get_job_dir(job.job_id, state_dir)
    (job_dir / "stdout.txt").write_text(
        json.dumps({"status": "SUCCESS", "response": "recovered work"}),
        encoding="utf-8",
    )
    job.state = JobState.RUNNING
    job.pid = None
    job.start_time = None
    job.cumulative_minutes = 2.0
    save_job(job, state_dir)
    # The crashed finalizer already appended its row (D68) but died before the
    # state transition could become observable.
    record_usage(
        usage_path=state_dir / "usage.jsonl",
        job=job,
        duration_seconds=120.0,
        tokens={"total_tokens": 5},
        state=JobState.DONE,
    )
    assert len(_usage_rows(state_dir)) == 1

    reconcile_on_startup(
        state_dir=state_dir, runner=LocalProcessRunner(executable=FAKE_AGY), home=tmp_path
    )

    rows = _usage_rows(state_dir)
    assert get_job(job.job_id, state_dir).state != JobState.RUNNING
    assert len(rows) == 1, rows
    assert rows[0]["cumulative_minutes"] == 2.0
