"""Unit tests for JobSupervisor state machine, deadlines, audit integration, and watcher."""

import json
import threading
import time
from pathlib import Path

from agy_mcp.audit import save_snapshot, take_snapshot
from agy_mcp.gitops import is_worktree_dirty
from agy_mcp.jobs import create_job, get_job_dir, transition_job_state
from agy_mcp.models import Job
from agy_mcp.runner import LocalProcessRunner, ProcessHandle, build_argv
from agy_mcp.states import JobState
from agy_mcp.supervisor import JobSupervisor
from agy_mcp.util import env_allowlist
from agy_mcp.worktree import create_worktree
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"


def _setup_test_worktree(tmp_path: Path) -> tuple[Job, Path, Path]:
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)

    state_dir = tmp_path / "state"
    state_dir.mkdir()

    job = create_job(
        repo_path=repo_dir,
        title="Test Supervisor Job",
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
        job,
        JobState.RUNNING,
        event_trigger="Process launched",
        base_state_dir=state_dir,
    )
    snapshot = take_snapshot(job, home=tmp_path)
    save_snapshot(snapshot, job_dir / "snapshot.json")

    return job, job_dir, state_dir


def test_supervisor_clean_success(tmp_path: Path) -> None:
    job, job_dir, state_dir = _setup_test_worktree(tmp_path)
    runner = LocalProcessRunner(executable=FAKE_AGY)
    stdout_file = job_dir / "stdout.txt"
    stderr_file = job_dir / "stderr.txt"
    log_file = job_dir / "cli.log"
    tmp_env_dir = job_dir / "tmp"

    env = env_allowlist(job_tmp_dir=tmp_env_dir, home=tmp_path)
    argv = build_argv(
        agy_binary=FAKE_AGY,
        prompt="Run __SCENARIO:success_simple__",
        model="gemini-3.8-flash-medium",
        print_timeout_seconds=60,
        log_file=log_file,
    )

    handle = runner.launch(
        job_id=job.job_id,
        workdir=job.worktree_path,
        args=argv,
        env=env,
        stdout_path=stdout_file,
        stderr_path=stderr_file,
    )

    # Dirty worktree with uncommitted work
    (job.worktree_path / "modified_code.py").write_text("print('hello')\n")

    supervisor = JobSupervisor(
        job=job,
        handle=handle,
        runner=runner,
        job_dir=job_dir,
        max_minutes=60,
        home=tmp_path,
        base_state_dir=state_dir,
    )

    # Step supervisor until complete
    deadline = time.time() + 5.0
    final_state = None
    while time.time() < deadline:
        final_state = supervisor.tick(time.time())
        if final_state is not None:
            break
        time.sleep(0.05)

    assert final_state == JobState.DONE
    assert job.state == JobState.DONE
    assert job.tokens.total_tokens > 0


def test_supervisor_needs_decision(tmp_path: Path) -> None:
    job, job_dir, state_dir = _setup_test_worktree(tmp_path)
    runner = LocalProcessRunner(executable=FAKE_AGY)
    stdout_file = job_dir / "stdout.txt"
    stderr_file = job_dir / "stderr.txt"
    log_file = job_dir / "cli.log"
    tmp_env_dir = job_dir / "tmp"

    env = env_allowlist(job_tmp_dir=tmp_env_dir, home=tmp_path)
    argv = build_argv(
        agy_binary=FAKE_AGY,
        prompt="Run __SCENARIO:needs_decision__",
        model="gemini-3.8-flash-medium",
        print_timeout_seconds=60,
        log_file=log_file,
    )

    handle = runner.launch(
        job_id=job.job_id,
        workdir=job.worktree_path,
        args=argv,
        env=env,
        stdout_path=stdout_file,
        stderr_path=stderr_file,
    )

    # Dirty worktree with uncommitted work
    (job.worktree_path / "modified_code.py").write_text("print('hello')\n")

    supervisor = JobSupervisor(
        job=job,
        handle=handle,
        runner=runner,
        job_dir=job_dir,
        max_minutes=60,
        home=tmp_path,
        base_state_dir=state_dir,
    )

    deadline = time.time() + 5.0
    final_state = None
    while time.time() < deadline:
        final_state = supervisor.tick(time.time())
        if final_state is not None:
            break
        time.sleep(0.05)

    assert final_state == JobState.NEEDS_DECISION
    assert job.state == JobState.NEEDS_DECISION


def test_supervisor_denial_blocked(tmp_path: Path) -> None:
    job, job_dir, state_dir = _setup_test_worktree(tmp_path)
    runner = LocalProcessRunner(executable=FAKE_AGY)
    stdout_file = job_dir / "stdout.txt"
    stderr_file = job_dir / "stderr.txt"
    log_file = job_dir / "cli.log"
    tmp_env_dir = job_dir / "tmp"

    env = env_allowlist(job_tmp_dir=tmp_env_dir, home=tmp_path)
    argv = build_argv(
        agy_binary=FAKE_AGY,
        prompt="Run __SCENARIO:denial_blocked__",
        model="gemini-3.8-flash-medium",
        print_timeout_seconds=60,
        log_file=log_file,
    )

    handle = runner.launch(
        job_id=job.job_id,
        workdir=job.worktree_path,
        args=argv,
        env=env,
        stdout_path=stdout_file,
        stderr_path=stderr_file,
    )

    # Dirty worktree with uncommitted work
    (job.worktree_path / "modified_code.py").write_text("print('hello')\n")

    supervisor = JobSupervisor(
        job=job,
        handle=handle,
        runner=runner,
        job_dir=job_dir,
        max_minutes=60,
        home=tmp_path,
        base_state_dir=state_dir,
    )

    deadline = time.time() + 5.0
    final_state = None
    while time.time() < deadline:
        final_state = supervisor.tick(time.time())
        if final_state is not None:
            break
        time.sleep(0.05)

    assert final_state == JobState.NEEDS_REVIEW
    assert job.state == JobState.NEEDS_REVIEW
    assert job.review_reason == "blocked_by_permission"


def test_supervisor_deadline_kill(tmp_path: Path) -> None:
    job, job_dir, state_dir = _setup_test_worktree(tmp_path)
    runner = LocalProcessRunner(executable=FAKE_AGY)
    stdout_file = job_dir / "stdout.txt"
    stderr_file = job_dir / "stderr.txt"
    log_file = job_dir / "cli.log"
    tmp_env_dir = job_dir / "tmp"

    env = env_allowlist(job_tmp_dir=tmp_env_dir, home=tmp_path)
    argv = build_argv(
        agy_binary=FAKE_AGY,
        prompt="Hang test __SCENARIO:hang__",
        model="gemini-3.8-flash-medium",
        print_timeout_seconds=60,
        log_file=log_file,
    )

    handle = runner.launch(
        job_id=job.job_id,
        workdir=job.worktree_path,
        args=argv,
        env=env,
        stdout_path=stdout_file,
        stderr_path=stderr_file,
    )

    # Dirty worktree with uncommitted work
    (job.worktree_path / "modified_code.py").write_text("print('hello')\n")

    # Supervisor with short deadline (e.g. simulated clock tick beyond deadline)
    supervisor = JobSupervisor(
        job=job,
        handle=handle,
        runner=runner,
        job_dir=job_dir,
        max_minutes=1,
        home=tmp_path,
        base_state_dir=state_dir,
        grace_kill_seconds=0.5,
    )

    # Pure tick advancing clock beyond deadline
    simulated_future_time = supervisor.started_at + supervisor.deadline_seconds + 10.0
    supervisor.tick(now=simulated_future_time)

    # Process group must have been killed and state transitioned to TIMEOUT
    deadline = time.time() + 3.0
    final_state = None
    while time.time() < deadline:
        final_state = supervisor.tick(time.time())
        if final_state is not None:
            break
        time.sleep(0.05)

    assert final_state == JobState.TIMEOUT
    assert job.state == JobState.TIMEOUT
    # Pre-kill checkpoint commit must have committed modified_code.py before kill
    assert not is_worktree_dirty(job.worktree_path)


def test_supervisor_audit_violation_suppresses_commits(tmp_path: Path) -> None:
    job, job_dir, state_dir = _setup_test_worktree(tmp_path)
    runner = LocalProcessRunner(executable=FAKE_AGY)
    stdout_file = job_dir / "stdout.txt"
    stderr_file = job_dir / "stderr.txt"
    log_file = job_dir / "cli.log"
    tmp_env_dir = job_dir / "tmp"

    env = env_allowlist(job_tmp_dir=tmp_env_dir, home=tmp_path)
    argv = build_argv(
        agy_binary=FAKE_AGY,
        prompt="Exploit __SCENARIO:malicious_hooks__",
        model="gemini-3.8-flash-medium",
        print_timeout_seconds=60,
        log_file=log_file,
    )

    handle = runner.launch(
        job_id=job.job_id,
        workdir=job.worktree_path,
        args=argv,
        env=env,
        stdout_path=stdout_file,
        stderr_path=stderr_file,
    )

    # Dirty worktree with uncommitted work
    (job.worktree_path / "modified_code.py").write_text("print('hello')\n")

    supervisor = JobSupervisor(
        job=job,
        handle=handle,
        runner=runner,
        job_dir=job_dir,
        max_minutes=60,
        home=tmp_path,
        base_state_dir=state_dir,
        audit_interval_seconds=0.1,  # Fast audit tick
    )

    deadline = time.time() + 5.0
    final_state = None
    while time.time() < deadline:
        final_state = supervisor.tick(time.time())
        if final_state is not None:
            break
        time.sleep(0.05)

    assert final_state == JobState.NEEDS_REVIEW
    assert job.state == JobState.NEEDS_REVIEW
    assert job.review_reason == "audit_violation"
    # Suppresses commits: commits_count must remain 0
    assert job.commits_count == 0


def test_supervisor_explicit_cancel(tmp_path: Path) -> None:
    job, job_dir, state_dir = _setup_test_worktree(tmp_path)
    runner = LocalProcessRunner(executable=FAKE_AGY)
    stdout_file = job_dir / "stdout.txt"
    stderr_file = job_dir / "stderr.txt"
    log_file = job_dir / "cli.log"
    tmp_env_dir = job_dir / "tmp"

    env = env_allowlist(job_tmp_dir=tmp_env_dir, home=tmp_path)
    argv = build_argv(
        agy_binary=FAKE_AGY,
        prompt="Hang test __SCENARIO:hang__",
        model="gemini-3.8-flash-medium",
        print_timeout_seconds=60,
        log_file=log_file,
    )

    handle = runner.launch(
        job_id=job.job_id,
        workdir=job.worktree_path,
        args=argv,
        env=env,
        stdout_path=stdout_file,
        stderr_path=stderr_file,
    )

    # Dirty worktree with uncommitted work
    (job.worktree_path / "modified_code.py").write_text("print('hello')\n")

    supervisor = JobSupervisor(
        job=job,
        handle=handle,
        runner=runner,
        job_dir=job_dir,
        max_minutes=60,
        home=tmp_path,
        base_state_dir=state_dir,
        grace_kill_seconds=0.5,
    )

    # Cancel explicitly
    supervisor.terminate_group(grace_seconds=0.5)

    deadline = time.time() + 3.0
    final_state = None
    while time.time() < deadline:
        final_state = supervisor.tick(time.time())
        if final_state is not None:
            break
        time.sleep(0.05)

    assert final_state == JobState.CANCELLED
    assert job.state == JobState.CANCELLED


def test_supervisor_safety_net_commit(tmp_path: Path) -> None:
    job, job_dir, state_dir = _setup_test_worktree(tmp_path)
    runner = LocalProcessRunner(executable=FAKE_AGY)
    stdout_file = job_dir / "stdout.txt"
    stderr_file = job_dir / "stderr.txt"
    log_file = job_dir / "cli.log"
    tmp_env_dir = job_dir / "tmp"

    env = env_allowlist(job_tmp_dir=tmp_env_dir, home=tmp_path)
    # Dirty the worktree without making a commit message
    (job.worktree_path / "work_dirty.txt").write_text("Uncommitted modifications\n")
    assert is_worktree_dirty(job.worktree_path)

    argv = build_argv(
        agy_binary=FAKE_AGY,
        prompt="Run __SCENARIO:success_simple__",
        model="gemini-3.8-flash-medium",
        print_timeout_seconds=60,
        log_file=log_file,
    )

    handle = runner.launch(
        job_id=job.job_id,
        workdir=job.worktree_path,
        args=argv,
        env=env,
        stdout_path=stdout_file,
        stderr_path=stderr_file,
    )

    # Dirty worktree with uncommitted work
    (job.worktree_path / "modified_code.py").write_text("print('hello')\n")

    supervisor = JobSupervisor(
        job=job,
        handle=handle,
        runner=runner,
        job_dir=job_dir,
        max_minutes=60,
        home=tmp_path,
        base_state_dir=state_dir,
    )

    deadline = time.time() + 5.0
    final_state = None
    while time.time() < deadline:
        final_state = supervisor.tick(time.time())
        if final_state is not None:
            break
        time.sleep(0.05)

    assert final_state == JobState.DONE
    # Safety net commit was executed, worktree is clean
    assert not is_worktree_dirty(job.worktree_path)
    assert job.commits_count >= 1


def test_supervisor_verify_blocked_by_pre_verify_security_finding(tmp_path: Path) -> None:
    """Pre-verify security finding must prevent verify command from running."""
    job, job_dir, state_dir = _setup_test_worktree(tmp_path)
    runner = LocalProcessRunner(executable=FAKE_AGY)
    stdout_file = job_dir / "stdout.txt"
    stderr_file = job_dir / "stderr.txt"
    log_file = job_dir / "cli.log"
    tmp_env_dir = job_dir / "tmp"

    env = env_allowlist(job_tmp_dir=tmp_env_dir, home=tmp_path)

    # Sentinel script that would touch verify_sentinel.txt if executed
    sentinel_file = tmp_path / "verify_sentinel.txt"
    verify_script = tmp_path / "verify_cmd.py"
    sentinel_repr = repr(str(sentinel_file))
    verify_script.write_text(f"from pathlib import Path\nPath({sentinel_repr}).write_text('ran')\n")
    job.verify = f"python3 {verify_script}"

    argv = build_argv(
        agy_binary=FAKE_AGY,
        prompt="Run __SCENARIO:success_simple__",
        model="gemini-3.8-flash-medium",
        print_timeout_seconds=60,
        log_file=log_file,
    )

    handle = runner.launch(
        job_id=job.job_id,
        workdir=job.worktree_path,
        args=argv,
        env=env,
        stdout_path=stdout_file,
        stderr_path=stderr_file,
    )

    supervisor = JobSupervisor(
        job=job,
        handle=handle,
        runner=runner,
        job_dir=job_dir,
        max_minutes=60,
        home=tmp_path,
        base_state_dir=state_dir,
    )

    # Tamper with .git/config before process terminates
    git_config = job.repo_path / ".git" / "config"
    with git_config.open("a", encoding="utf-8") as f:
        f.write("\n[malicious]\nhack = true\n")

    deadline = time.time() + 5.0
    final_state = None
    while time.time() < deadline:
        final_state = supervisor.tick(time.time())
        if final_state is not None:
            break
        time.sleep(0.05)

    assert final_state == JobState.NEEDS_REVIEW
    assert job.review_reason == "audit_violation"
    assert not sentinel_file.exists(), "Verify command ran despite pre-verify fatal audit violation"


def test_supervisor_verify_post_audit_gate_catches_tampering(tmp_path: Path) -> None:
    """Post-verify audit gate must catch security violations caused by verify command itself."""
    job, job_dir, state_dir = _setup_test_worktree(tmp_path)
    runner = LocalProcessRunner(executable=FAKE_AGY)
    stdout_file = job_dir / "stdout.txt"
    stderr_file = job_dir / "stderr.txt"
    log_file = job_dir / "cli.log"
    tmp_env_dir = job_dir / "tmp"

    env = env_allowlist(job_tmp_dir=tmp_env_dir, home=tmp_path)

    # Verify script modifies .git/config and exits 0
    git_config = job.repo_path / ".git" / "config"
    verify_script = tmp_path / "tampering_verify.py"
    verify_script.write_text(
        f"with open({repr(str(git_config))}, 'a') as f:\n"
        f"    f.write('\\n[tampered]\\nbad = 1\\n')\n"
    )
    job.verify = f"python3 {verify_script}"

    argv = build_argv(
        agy_binary=FAKE_AGY,
        prompt="Run __SCENARIO:success_simple__",
        model="gemini-3.8-flash-medium",
        print_timeout_seconds=60,
        log_file=log_file,
    )

    handle = runner.launch(
        job_id=job.job_id,
        workdir=job.worktree_path,
        args=argv,
        env=env,
        stdout_path=stdout_file,
        stderr_path=stderr_file,
    )

    supervisor = JobSupervisor(
        job=job,
        handle=handle,
        runner=runner,
        job_dir=job_dir,
        max_minutes=60,
        home=tmp_path,
        base_state_dir=state_dir,
    )

    deadline = time.time() + 5.0
    final_state = None
    while time.time() < deadline:
        final_state = supervisor.tick(time.time())
        if final_state is not None:
            break
        time.sleep(0.05)

    assert final_state == JobState.NEEDS_REVIEW
    assert job.review_reason == "audit_violation"


def test_supervisor_tick_resilience_and_consecutive_failure_cap(
    tmp_path: Path, monkeypatch
) -> None:
    """Unhandled tick exceptions are logged and tolerated up to 3 consecutive failures."""
    import json
    import threading

    job, job_dir, state_dir = _setup_test_worktree(tmp_path)
    runner = LocalProcessRunner(executable=FAKE_AGY)
    stdout_file = job_dir / "stdout.txt"
    stderr_file = job_dir / "stderr.txt"
    log_file = job_dir / "cli.log"
    tmp_env_dir = job_dir / "tmp"

    env = env_allowlist(job_tmp_dir=tmp_env_dir, home=tmp_path)

    argv = build_argv(
        agy_binary=FAKE_AGY,
        prompt="Hang __SCENARIO:hang__",
        model="gemini-3.8-flash-medium",
        print_timeout_seconds=60,
        log_file=log_file,
    )

    handle = runner.launch(
        job_id=job.job_id,
        workdir=job.worktree_path,
        args=argv,
        env=env,
        stdout_path=stdout_file,
        stderr_path=stderr_file,
    )

    supervisor = JobSupervisor(
        job=job,
        handle=handle,
        runner=runner,
        job_dir=job_dir,
        max_minutes=60,
        home=tmp_path,
        base_state_dir=state_dir,
        poll_interval_seconds=0.05,
    )

    call_count = 0

    def failing_tick(now=None):
        nonlocal call_count
        call_count += 1
        raise RuntimeError(f"Simulated tick failure #{call_count}")

    monkeypatch.setattr(supervisor, "tick", failing_tick)

    t = threading.Thread(target=supervisor.run_loop, daemon=True)
    t.start()
    t.join(timeout=3.0)

    try:
        assert job.state == JobState.FAILED
        assert job.review_reason == "supervisor_failure"

        # Verify audit.jsonl records 3 supervisor_error entries
        audit_file = job_dir / "audit.jsonl"
        events = [json.loads(line) for line in audit_file.read_text().splitlines() if line.strip()]
        error_events = [e for e in events if e.get("event") == "supervisor_error"]
        assert len(error_events) >= 3
        assert error_events[0]["detail"]["consecutive_errors"] == 1
        assert error_events[1]["detail"]["consecutive_errors"] == 2
        assert error_events[2]["detail"]["consecutive_errors"] == 3
    finally:
        runner.cleanup(handle)


def test_supervisor_tick_error_and_audit_failure_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    """When tick raises and audit also raises, fail closed to needs_review(audit_unavailable)."""
    import json
    import threading

    job, job_dir, state_dir = _setup_test_worktree(tmp_path)
    runner = LocalProcessRunner(executable=FAKE_AGY)
    stdout_file = job_dir / "stdout.txt"
    stderr_file = job_dir / "stderr.txt"
    log_file = job_dir / "cli.log"
    tmp_env_dir = job_dir / "tmp"

    env = env_allowlist(job_tmp_dir=tmp_env_dir, home=tmp_path)

    argv = build_argv(
        agy_binary=FAKE_AGY,
        prompt="Hang __SCENARIO:hang__",
        model="gemini-3.8-flash-medium",
        print_timeout_seconds=60,
        log_file=log_file,
    )

    handle = runner.launch(
        job_id=job.job_id,
        workdir=job.worktree_path,
        args=argv,
        env=env,
        stdout_path=stdout_file,
        stderr_path=stderr_file,
    )

    supervisor = JobSupervisor(
        job=job,
        handle=handle,
        runner=runner,
        job_dir=job_dir,
        max_minutes=60,
        home=tmp_path,
        base_state_dir=state_dir,
        poll_interval_seconds=0.05,
    )

    def failing_tick(now=None):
        raise RuntimeError("Simulated unhandled tick failure")

    def failing_audit(*args, **kwargs):
        raise ValueError("Audit subsystem crashed: token=secret_value_12345")

    monkeypatch.setattr(supervisor, "tick", failing_tick)
    monkeypatch.setattr("agy_mcp.supervisor.audit", failing_audit)

    t = threading.Thread(target=supervisor.run_loop, daemon=True)
    t.start()
    t.join(timeout=3.0)

    try:
        assert job.state == JobState.NEEDS_REVIEW
        assert job.review_reason == "audit_unavailable"
        assert runner.poll(handle) is not None  # Process must be killed

        audit_file = job_dir / "audit.jsonl"
        events = [json.loads(line) for line in audit_file.read_text().splitlines() if line.strip()]
        audit_err_events = [e for e in events if e.get("event") == "audit_error_during_tick_error"]
        assert len(audit_err_events) >= 1
        ev = audit_err_events[0]
        assert ev["detail"]["exception_class"] == "ValueError"
        assert "secret_value_12345" not in ev["detail"]["message"]
        assert "[REDACTED]" in ev["detail"]["message"] or "token=" in ev["detail"]["message"]
    finally:
        runner.cleanup(handle)



def _audit_events(job_dir: Path) -> list[dict]:
    audit_file = job_dir / "audit.jsonl"
    if not audit_file.is_file():
        return []
    return [
        json.loads(line)
        for line in audit_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _cleanup_events(job_dir: Path) -> list[dict]:
    return [e for e in _audit_events(job_dir) if e.get("event") == "cleanup_error"]


def test_pre_kill_checkpoint_failure_is_audited(tmp_path: Path, monkeypatch) -> None:
    """A raising checkpoint commit appends cleanup_error and leaves state unchanged."""
    job, job_dir, state_dir = _setup_test_worktree(tmp_path)
    runner = LocalProcessRunner(executable=FAKE_AGY)
    handle = ProcessHandle(
        pid=0,
        pgid=0,
        start_time=0,
        job_id=job.job_id,
        stdout_path=job_dir / "stdout.txt",
        stderr_path=job_dir / "stderr.txt",
    )
    supervisor = JobSupervisor(
        job=job,
        handle=handle,
        runner=runner,
        job_dir=job_dir,
        max_minutes=60,
        home=tmp_path,
        base_state_dir=state_dir,
    )
    (job.worktree_path / "dirty.py").write_text("print('dirty')\n", encoding="utf-8")

    def boom(*args, **kwargs):
        raise RuntimeError(f"checkpoint exploded under {tmp_path}")

    monkeypatch.setattr("agy_mcp.supervisor.checkpoint_commit", boom)

    before = job.state
    supervisor._try_pre_kill_checkpoint()

    assert job.state == before == JobState.RUNNING
    events = _cleanup_events(job_dir)
    assert events
    assert events[0]["detail"]["code"] == "pre_kill_checkpoint_failed"
    assert str(tmp_path) not in json.dumps(events[0])


def test_safety_net_commit_failure_is_audited(tmp_path: Path, monkeypatch) -> None:
    """A raising safety-net commit appends cleanup_error; the job still ends done."""
    job, job_dir, state_dir = _setup_test_worktree(tmp_path)
    runner = LocalProcessRunner(executable=FAKE_AGY)
    stdout_file = job_dir / "stdout.txt"
    stderr_file = job_dir / "stderr.txt"
    log_file = job_dir / "cli.log"
    env = env_allowlist(job_tmp_dir=job_dir / "tmp", home=tmp_path)

    argv = build_argv(
        agy_binary=FAKE_AGY,
        prompt="Run __SCENARIO:success_simple__",
        model="gemini-3.8-flash-medium",
        print_timeout_seconds=60,
        log_file=log_file,
    )
    handle = runner.launch(
        job_id=job.job_id,
        workdir=job.worktree_path,
        args=argv,
        env=env,
        stdout_path=stdout_file,
        stderr_path=stderr_file,
    )
    (job.worktree_path / "dirty.py").write_text("print('dirty')\n", encoding="utf-8")

    supervisor = JobSupervisor(
        job=job,
        handle=handle,
        runner=runner,
        job_dir=job_dir,
        max_minutes=60,
        home=tmp_path,
        base_state_dir=state_dir,
    )

    def boom(**kwargs):
        raise RuntimeError(f"safety net exploded under {tmp_path}")

    monkeypatch.setattr("agy_mcp.supervisor.safety_net_commit", boom)

    try:
        deadline = time.time() + 5.0
        final_state = None
        while time.time() < deadline:
            final_state = supervisor.tick(time.time())
            if final_state is not None:
                break
            time.sleep(0.05)

        assert final_state == JobState.DONE
        events = _cleanup_events(job_dir)
        assert events
        assert events[0]["detail"]["code"] == "safety_net_commit_failed"
        assert str(tmp_path) not in json.dumps(events[0])
    finally:
        runner.cleanup(handle)


def test_run_loop_kill_after_tick_errors_is_audited(tmp_path: Path, monkeypatch) -> None:
    """A raising kill after three tick errors appends cleanup_error; state stays failed."""
    job, job_dir, state_dir = _setup_test_worktree(tmp_path)
    runner = LocalProcessRunner(executable=FAKE_AGY)
    stdout_file = job_dir / "stdout.txt"
    stderr_file = job_dir / "stderr.txt"
    log_file = job_dir / "cli.log"
    env = env_allowlist(job_tmp_dir=job_dir / "tmp", home=tmp_path)

    argv = build_argv(
        agy_binary=FAKE_AGY,
        prompt="Hang __SCENARIO:hang__",
        model="gemini-3.8-flash-medium",
        print_timeout_seconds=60,
        log_file=log_file,
    )
    handle = runner.launch(
        job_id=job.job_id,
        workdir=job.worktree_path,
        args=argv,
        env=env,
        stdout_path=stdout_file,
        stderr_path=stderr_file,
    )
    supervisor = JobSupervisor(
        job=job,
        handle=handle,
        runner=runner,
        job_dir=job_dir,
        max_minutes=60,
        home=tmp_path,
        base_state_dir=state_dir,
        poll_interval_seconds=0.05,
    )

    def failing_tick(now=None):
        raise RuntimeError(f"tick exploded under {tmp_path}")

    state = {"failed": False}
    orig_kill = runner.kill

    def fail_once(handle, grace_seconds: float = 3.0):
        if not state["failed"]:
            state["failed"] = True
            raise RuntimeError(f"kill exploded under {tmp_path}")
        return orig_kill(handle, grace_seconds=grace_seconds)

    monkeypatch.setattr(supervisor, "tick", failing_tick)
    monkeypatch.setattr(runner, "kill", fail_once)

    t = threading.Thread(target=supervisor.run_loop, daemon=True)
    t.start()
    t.join(timeout=3.0)

    try:
        assert job.state == JobState.FAILED
        assert job.review_reason == "supervisor_failure"
        assert runner.poll(handle) is not None
        events = _cleanup_events(job_dir)
        assert events
        assert events[0]["detail"]["code"] == "kill_after_tick_errors"
        assert str(tmp_path) not in json.dumps(events[0])
    finally:
        runner.cleanup(handle)
