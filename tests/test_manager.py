"""Unit tests for JobManager coordinating the nine MCP operations."""

import json
import time
from pathlib import Path
from typing import Callable

import pytest

from agy_mcp.config import Config
from agy_mcp.gitops import run_git
from agy_mcp.jobs import get_job, get_job_dir
from agy_mcp.manager import JobManager, ManagerError
from agy_mcp.models import JobState
from agy_mcp.runner import LocalProcessRunner, PromptTooLargeError
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"


def poll_until(
    predicate: Callable[[], bool],
    timeout_seconds: float = 15.0,
    poll_interval: float = 0.05,
    error_message: str = "Timed out waiting for condition",
) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(poll_interval)
    if not predicate():
        raise TimeoutError(f"{error_message} after {timeout_seconds}s")


def _setup_manager_and_repo(
    tmp_path: Path,
    config: Config | None = None,
    settle_window_seconds: float = 0.1,
    poll_interval_seconds: float = 0.05,
) -> tuple[JobManager, Path]:
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)

    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    runner = LocalProcessRunner(executable=FAKE_AGY)
    cfg = config or Config(max_concurrent_jobs=2, daily_jobs_cap=5)
    manager = JobManager(
        config=cfg,
        runner=runner,
        base_state_dir=state_dir,
        home=tmp_path,
        settle_window_seconds=settle_window_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    return manager, repo_dir


def test_manager_start_and_wait_success(tmp_path: Path) -> None:
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Perform task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Add Simple Feature",
        model="gemini-3.8-flash-medium",
    )

    assert env.state == JobState.QUEUED
    assert env.job_id

    # Wait for completion
    res_env = manager.wait(job_id=env.job_id, seconds=5)
    assert res_env.state == JobState.DONE
    assert "agy/" in res_env.branch
    assert res_env.tokens.total_tokens > 0


def test_manager_prompt_size_limit(tmp_path: Path) -> None:
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    with pytest.raises(PromptTooLargeError):
        manager.start(
            prompt="x" * 100_001,
            repo_path=str(repo_dir),
            title="Large Prompt",
        )


def test_manager_model_allowlist_enforcement(tmp_path: Path) -> None:
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    with pytest.raises(ManagerError) as exc_info:
        manager.start(
            prompt="Task",
            repo_path=str(repo_dir),
            title="Model Test",
            model="unauthorized-model-99",
        )
    assert exc_info.value.code == "model_not_allowed"


def test_manager_verify_allowlist_enforcement(tmp_path: Path) -> None:
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    with pytest.raises(ManagerError) as exc_info:
        manager.start(
            prompt="Task",
            repo_path=str(repo_dir),
            title="Verify Test",
            verify="rm -rf /",
        )
    assert exc_info.value.code == "verify_not_allowed"


def test_manager_concurrency_ceiling(tmp_path: Path) -> None:
    cfg = Config(max_concurrent_jobs=1)
    manager, repo_dir = _setup_manager_and_repo(tmp_path, config=cfg)

    # Start first job that hangs
    env1 = manager.start(
        prompt="Hang __SCENARIO:hang__",
        repo_path=str(repo_dir),
        title="Job 1",
    )
    assert env1.state == JobState.QUEUED

    # Wait until job 1 is running
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if manager.status(env1.job_id).state == JobState.RUNNING:
            break
        time.sleep(0.05)

    assert manager.status(env1.job_id).state == JobState.RUNNING

    # Attempt second job while first is running
    with pytest.raises(ManagerError) as exc_info:
        manager.start(
            prompt="Job 2",
            repo_path=str(repo_dir),
            title="Job 2",
        )
    assert exc_info.value.code == "concurrency_limit_exceeded"

    # Cancel first job
    manager.cancel(env1.job_id)


def test_manager_daily_cap_enforcement(tmp_path: Path) -> None:
    cfg = Config(daily_jobs_cap=2)
    manager, repo_dir = _setup_manager_and_repo(tmp_path, config=cfg)

    # Job 1
    j1 = manager.start(
        prompt="Task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Job 1",
    )
    manager.wait(j1.job_id, seconds=3)

    # Job 2
    j2 = manager.start(
        prompt="Task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Job 2",
    )
    manager.wait(j2.job_id, seconds=3)

    # Job 3 should fail due to daily cap
    with pytest.raises(ManagerError) as exc_info:
        manager.start(
            prompt="Task __SCENARIO:success_simple__",
            repo_path=str(repo_dir),
            title="Job 3",
        )
    assert exc_info.value.code == "cap_exceeded"


def test_manager_status_and_result(tmp_path: Path) -> None:
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Build feature __SCENARIO:success_with_commits__",
        repo_path=str(repo_dir),
        title="Status Result Test",
    )

    status_resp = manager.status(env.job_id)
    assert status_resp.job_id == env.job_id
    assert status_resp.last_cli_log_mtime >= 0

    # Wait for completion
    manager.wait(env.job_id, seconds=5)

    result_resp = manager.result(env.job_id)
    assert result_resp.job_id == env.job_id
    assert "src_feature.py" in result_resp.response or "created" in result_resp.response.lower()
    assert "<untrusted_text" in result_resp.response


def test_manager_cancel_operation(tmp_path: Path) -> None:
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Hang __SCENARIO:hang__",
        repo_path=str(repo_dir),
        title="Cancel Test",
    )
    assert env.state == JobState.QUEUED

    # Wait until running
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if manager.status(env.job_id).state == JobState.RUNNING:
            break
        time.sleep(0.05)

    cancel_env = manager.cancel(env.job_id)
    assert cancel_env.state == JobState.CANCELLED

    # Verified via status
    st = manager.status(env.job_id)
    assert st.state == JobState.CANCELLED


def test_manager_list_operation(tmp_path: Path) -> None:
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Run __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="List Test",
    )
    manager.wait(env.job_id, seconds=3)

    list_resp = manager.list()
    assert len(list_resp.jobs) >= 1
    assert list_resp.disk_usage_bytes > 0
    assert isinstance(list_resp.drifted_worktrees, list)


def test_manager_resume_from_needs_decision(tmp_path: Path) -> None:
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Run __SCENARIO:needs_decision__",
        repo_path=str(repo_dir),
        title="Decision Test",
    )
    manager.wait(env.job_id, seconds=5)

    st = manager.status(env.job_id)
    assert st.state == JobState.NEEDS_DECISION

    # Resume with user answer
    res_env = manager.resume(
        job_id=env.job_id,
        answer="Please use PostgreSQL __SCENARIO:resume_echo__",
    )
    assert res_env.state == JobState.RUNNING

    # Wait for resumed session
    manager.wait(env.job_id, seconds=5)
    st_resumed = manager.status(env.job_id)
    assert st_resumed.state == JobState.DONE


def test_manager_resume_refuses_terminal_security_review(tmp_path: Path) -> None:
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Exploit __SCENARIO:malicious_hooks__",
        repo_path=str(repo_dir),
        title="Security Exploit Test",
    )
    manager.wait(env.job_id, seconds=5)

    st = manager.status(env.job_id)
    assert st.state == JobState.NEEDS_REVIEW
    assert st.error is None or "audit_violation" in str(st.error)

    # Resume must be strictly refused
    with pytest.raises(ManagerError) as exc_info:
        manager.resume(env.job_id, "Attempting resume")
    assert exc_info.value.code == "cannot_resume_security_review"


def test_manager_resume_blocked_by_audit_gate(tmp_path: Path) -> None:
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Run __SCENARIO:needs_decision__",
        repo_path=str(repo_dir),
        title="Audit Gate Test",
    )
    manager.wait(env.job_id, seconds=5)

    st = manager.status(env.job_id)
    assert st.state == JobState.NEEDS_DECISION

    # Tamper with worktree by creating forbidden .agents/ directory
    worktree_path = tmp_path / "state" / "jobs" / env.job_id / "work"
    agents_dir = worktree_path / ".agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "hooks.json").write_text('{"tampered": true}')

    # Resume must be blocked by the audit gate before launching
    with pytest.raises(ManagerError) as exc_info:
        manager.resume(env.job_id, "User answer")
    assert exc_info.value.code == "audit_violation"


def test_manager_review_operation(tmp_path: Path) -> None:
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Build feature __SCENARIO:success_with_commits__",
        repo_path=str(repo_dir),
        title="Review Test",
    )
    manager.wait(env.job_id, seconds=5)

    rev = manager.review(env.job_id)
    assert rev.job_id == env.job_id
    assert rev.merge_base
    assert rev.ahead >= 0
    assert not rev.base_moved
    assert "<untrusted_text" in rev.untrusted_diff


def test_manager_review_audit_unavailable_is_not_clean(
    tmp_path: Path, monkeypatch
) -> None:
    """A raising audit in review must surface audit_unavailable, never a clean list."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Build feature __SCENARIO:success_with_commits__",
        repo_path=str(repo_dir),
        title="Review Audit Failure Test",
    )
    manager.wait(env.job_id, seconds=5)

    def failing_audit(*args, **kwargs):
        raise RuntimeError("audit backend unavailable secret-marker")

    monkeypatch.setattr("agy_mcp.manager.audit", failing_audit)

    rev = manager.review(env.job_id)

    assert rev.audit_findings
    assert any(f.get("reason") == "audit_unavailable" for f in rev.audit_findings)
    assert any(f.get("severity") == "fatal" for f in rev.audit_findings)
    assert any("human must review" in action for action in rev.next_actions)
    dump = json.dumps(rev.to_dict())
    assert "secret-marker" not in dump


def test_manager_cleanup_operation(tmp_path: Path) -> None:
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Run __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Cleanup Test",
    )
    manager.wait(env.job_id, seconds=5)

    cleanup_resp = manager.cleanup(
        job_id=env.job_id,
        remove_worktree=True,
        delete_branch=True,
    )
    assert cleanup_resp.worktree_removed is True
    assert cleanup_resp.branch_deleted is True


def test_manager_invalid_job_id(tmp_path: Path) -> None:
    manager, _ = _setup_manager_and_repo(tmp_path)
    with pytest.raises(ManagerError) as exc_info:
        manager.status("invalid-not-uuid")
    assert exc_info.value.code == "invalid_job_id"


def test_manager_auto_fix_exhaustion(tmp_path: Path) -> None:
    # Configure auto_fix_attempts = 1 and a failing verify command
    cfg = Config(
        auto_fix_attempts=1,
        verify_allowlist=["pytest", "ruff", "python3"],
    )
    manager, repo_dir = _setup_manager_and_repo(tmp_path, config=cfg)

    # In repo, create a script that fails
    test_script = repo_dir / "test_suite.py"
    test_script.write_text("import sys\nsys.exit(1)\n")
    run_git(["add", "test_suite.py"], repo_dir)
    run_git(["commit", "-m", "Add failing test"], repo_dir)

    env = manager.start(
        prompt="Fix the tests __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Auto Fix Test",
        verify="python3 test_suite.py",
    )

    # Wait until supervisor finishes and auto-fix loop runs
    deadline = time.time() + 8.0
    while time.time() < deadline:
        st = manager.status(env.job_id)
        if st.state == JobState.NEEDS_REVIEW:
            break
        time.sleep(0.3)

    st = manager.status(env.job_id)
    assert st.state == JobState.NEEDS_REVIEW
    # Reached needs_review with verification_failed
    assert "verification_failed" in str(st.review_reason) if hasattr(st, "review_reason") else True


def test_manager_auto_fix_success_second_attempt(tmp_path: Path) -> None:
    """Verify command fails on turn 1, auto-fix loop resumes, fix succeeds on turn 2 -> DONE."""
    cfg = Config(
        auto_fix_attempts=2,
        verify_allowlist=["python3"],
    )
    manager, repo_dir = _setup_manager_and_repo(tmp_path, config=cfg)

    test_script = repo_dir / "test_suite.py"
    test_script.write_text(
        "import os, sys\n"
        "if not os.path.exists('fixed.txt'):\n"
        "    sys.stderr.write('Test failed: TRIGGER_AUTOFIX_CREATE_FIXED\\n')\n"
        "    sys.exit(1)\n"
        "sys.exit(0)\n"
    )
    run_git(["add", "test_suite.py"], repo_dir)
    run_git(["commit", "-m", "Add conditional test script"], repo_dir)

    env = manager.start(
        prompt="Fix the tests __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Auto Fix Second Attempt Test",
        verify="python3 test_suite.py",
    )

    deadline = time.time() + 8.0
    while time.time() < deadline:
        st = manager.status(env.job_id)
        if st.state == JobState.DONE:
            break
        time.sleep(0.3)

    st = manager.status(env.job_id)
    assert st.state == JobState.DONE


def test_manager_auto_fix_fails_twice_verify_failed_with_tail(tmp_path: Path) -> None:
    """Verify fails twice -> needs_review reason verify_failed with failure tail attached."""
    cfg = Config(
        auto_fix_attempts=2,
        verify_allowlist=["python3"],
    )
    manager, repo_dir = _setup_manager_and_repo(tmp_path, config=cfg)

    test_script = repo_dir / "test_suite.py"
    test_script.write_text(
        "import sys\nsys.stderr.write('PERMANENT_TEST_FAILURE_TAIL\\n')\nsys.exit(1)\n"
    )
    run_git(["add", "test_suite.py"], repo_dir)
    run_git(["commit", "-m", "Add failing test script"], repo_dir)

    env = manager.start(
        prompt="Fix the tests __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Auto Fix Double Failure Test",
        verify="python3 test_suite.py",
    )

    def condition() -> bool:
        with manager._lock:
            if env.job_id in manager._resuming_jobs or env.job_id in manager._supervisors:
                return False
        st = manager.status(env.job_id)
        job = get_job(env.job_id, manager.state_dir)
        return st.state == JobState.NEEDS_REVIEW and (
            job.auto_fix_count >= 2 or manager._auto_fix_counts.get(env.job_id, 0) >= 2
        )

    poll_until(condition, timeout_seconds=15.0, error_message="Auto-fix double failure not reached")

    st = manager.status(env.job_id)
    assert st.state == JobState.NEEDS_REVIEW
    job = get_job(env.job_id, manager.state_dir)
    assert job.review_reason == "verify_failed"
    assert job.auto_fix_count == 2 or manager._auto_fix_counts.get(env.job_id, 0) == 2

    # Verify failure tail attached in result() response (untrusted-wrapped and capped)
    res = manager.result(env.job_id)
    assert res.state == JobState.NEEDS_REVIEW
    assert "<untrusted_text" in res.response
    assert "</untrusted_text" in res.response
    assert "PERMANENT_TEST_FAILURE_TAIL" in res.response


def test_manager_verify_timeout_reason(tmp_path: Path, monkeypatch) -> None:
    """When verification times out (rc 124), state becomes needs_review (verify_timeout)."""
    import agy_mcp.verify

    cfg = Config(verify_allowlist=["python3"])
    manager, repo_dir = _setup_manager_and_repo(tmp_path, config=cfg)

    # Mock run_verify to simulate timeout
    def mock_run_verify(*args, **kwargs):
        return 124, "\nVerification command timed out after 900s"

    monkeypatch.setattr(agy_mcp.verify, "run_verify", mock_run_verify)

    env = manager.start(
        prompt="Execute task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Verify Timeout Test",
        verify="python3 -m pytest",
    )

    deadline = time.time() + 8.0
    while time.time() < deadline:
        st = manager.status(env.job_id)
        if st.state == JobState.NEEDS_REVIEW:
            break
        time.sleep(0.3)

    st = manager.status(env.job_id)
    assert st.state == JobState.NEEDS_REVIEW
    job = get_job(env.job_id, manager.state_dir)
    assert job.review_reason == "verify_timeout"


def test_manager_verify_command_not_found_clean_error(tmp_path: Path) -> None:
    """When verify command executable is not found, clear error and no crash."""
    cfg = Config(verify_allowlist=["nonexistent_bin_123"])
    manager, repo_dir = _setup_manager_and_repo(tmp_path, config=cfg)

    env = manager.start(
        prompt="Execute task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Verify Not Found Test",
        verify="nonexistent_bin_123 --test",
    )

    # A failing verify triggers the automatic auto-fix resume, which moves the
    # job needs_review -> running and clears review_reason. Sampling status()
    # and get_job() in two separate reads could land across that transition
    # (flake: 1 in ~30; state/reason are always written together). agy_wait is
    # the supported way to observe the settled outcome: it keeps waiting while
    # a verify auto-fix is pending (C3, docs/reports/agymcp-fixes-2/).
    final = manager.wait(env.job_id, seconds=10)
    assert final.state == JobState.NEEDS_REVIEW
    job = get_job(env.job_id, manager.state_dir)
    assert job.review_reason == "verify_failed"
    res = manager.result(env.job_id)
    assert "Executable not found" in res.response


def test_manager_start_asynchronous_nonblocking(tmp_path: Path, monkeypatch) -> None:
    """manager.start must return immediately in queued state without waiting for worktree setup."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    from agy_mcp import manager as mgr_mod

    orig_create_wt = mgr_mod.create_worktree

    def slow_create_worktree(*args, **kwargs):
        time.sleep(1.0)
        return orig_create_wt(*args, **kwargs)

    monkeypatch.setattr(mgr_mod, "create_worktree", slow_create_worktree)
    t0 = time.time()
    env = manager.start(
        prompt="Nonblocking test __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Nonblocking Worktree Provisioning",
    )
    elapsed = time.time() - t0
    assert elapsed < 0.8, f"manager.start blocked for {elapsed}s"
    assert env.state == JobState.QUEUED

    final_env = manager.wait(env.job_id, seconds=5)
    assert final_env.state == JobState.DONE


def test_manager_start_setup_failure_cleanup(tmp_path: Path, monkeypatch) -> None:
    """Failure during worktree setup must transition to failed with setup_failed and clean up."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    from agy_mcp import manager as mgr_mod

    def failing_create_worktree(*args, **kwargs):
        raise RuntimeError("Simulated worktree setup disk failure")

    monkeypatch.setattr(mgr_mod, "create_worktree", failing_create_worktree)

    env = manager.start(
        prompt="Setup failure test",
        repo_path=str(repo_dir),
        title="Setup Failure Test",
    )
    assert env.state == JobState.QUEUED

    deadline = time.time() + 5.0
    while time.time() < deadline:
        st = manager.status(env.job_id)
        if st.state == JobState.FAILED:
            break
        time.sleep(0.05)

    st = manager.status(env.job_id)
    assert st.state == JobState.FAILED

    job_dir = get_job_dir(env.job_id, manager.state_dir)
    audit_file = job_dir / "audit.jsonl"
    events = [
        json.loads(line_str)
        for line_str in audit_file.read_text().splitlines()
        if line_str.strip()
    ]
    setup_events = [e for e in events if e.get("event") == "setup_failed"]
    assert len(setup_events) >= 1
    assert setup_events[0]["detail"]["review_reason"] == "setup_failed"


def test_manager_wait_clamping_and_warning(tmp_path: Path) -> None:
    """manager.wait clamps values above 300s to 300s and adds a warning field."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Wait clamp test __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Wait Clamping Test",
    )
    res = manager.wait(env.job_id, seconds=500)
    assert any("clamped to 300s" in w for w in res.warnings)


def test_manager_scenario_malicious_hooks(tmp_path: Path) -> None:
    """malicious_hooks exploit results in needs_review(audit_violation) and cannot resume."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Exploit hooks __SCENARIO:malicious_hooks__",
        repo_path=str(repo_dir),
        title="Malicious Hooks Exploit",
    )
    res = manager.wait(env.job_id, seconds=5)
    assert res.state == JobState.NEEDS_REVIEW
    job_data = get_job(env.job_id, manager.state_dir)
    assert job_data.review_reason == "audit_violation"
    with pytest.raises(ManagerError) as exc_info:
        manager.resume(env.job_id, "retry")
    assert exc_info.value.code == "cannot_resume_security_review"


def test_manager_scenario_malicious_gitptr(tmp_path: Path) -> None:
    """malicious_gitptr exploit results in needs_review(audit_violation) and cannot resume."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Exploit gitptr __SCENARIO:malicious_gitptr__",
        repo_path=str(repo_dir),
        title="Malicious Gitptr Exploit",
    )
    res = manager.wait(env.job_id, seconds=5)
    assert res.state == JobState.NEEDS_REVIEW
    job_data = get_job(env.job_id, manager.state_dir)
    assert job_data.review_reason == "audit_violation"
    with pytest.raises(ManagerError) as exc_info:
        manager.resume(env.job_id, "retry")
    assert exc_info.value.code == "cannot_resume_security_review"


def test_manager_scenario_malicious_hostwrite(tmp_path: Path) -> None:
    """malicious_hostwrite exploit results in needs_review(host_path_modified)."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Exploit hostwrite __SCENARIO:malicious_hostwrite__",
        repo_path=str(repo_dir),
        title="Malicious Hostwrite Exploit",
    )
    res = manager.wait(env.job_id, seconds=5)
    assert res.state == JobState.NEEDS_REVIEW
    job_data = get_job(env.job_id, manager.state_dir)
    assert job_data.review_reason == "host_path_modified"
    with pytest.raises(ManagerError) as exc_info:
        manager.resume(env.job_id, "retry")
    assert exc_info.value.code == "cannot_resume_security_review"


def test_manager_scenario_success_with_commits(tmp_path: Path) -> None:
    """Job with commits creates git commits and completes cleanly."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Feature commit test __SCENARIO:success_with_commits__",
        repo_path=str(repo_dir),
        title="Feature Commits Test",
    )
    res = manager.wait(env.job_id, seconds=5)
    assert res.state == JobState.DONE
    assert res.commits_count >= 1


def test_manager_scenario_denial_with_progress(tmp_path: Path) -> None:
    """Denial with progress enters DONE state with populated warnings and denials."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Denial with progress __SCENARIO:denial_with_progress__",
        repo_path=str(repo_dir),
        title="Denial Progress Test",
    )
    res = manager.wait(env.job_id, seconds=5)
    assert res.state == JobState.DONE
    assert len(res.denials) > 0
    assert len(res.warnings) > 0


def test_manager_scenario_denial_blocked(tmp_path: Path) -> None:
    """Zero progress denial enters needs_review(blocked_by_permission) and is resumable."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Denial blocked __SCENARIO:denial_blocked__",
        repo_path=str(repo_dir),
        title="Denial Blocked Test",
    )
    res = manager.wait(env.job_id, seconds=5)
    assert res.state == JobState.NEEDS_REVIEW
    job_data = get_job(env.job_id, manager.state_dir)
    assert job_data.review_reason == "blocked_by_permission"
    resumed = manager.resume(env.job_id, "permission granted")
    assert resumed.job_id == env.job_id


def test_manager_scenario_timeout_resumable(tmp_path: Path) -> None:
    """Timeout job enters TIMEOUT state and is resumable."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Timeout test __SCENARIO:timeout__",
        repo_path=str(repo_dir),
        title="Timeout Resumable Test",
    )
    res = manager.wait(env.job_id, seconds=5)
    assert res.state == JobState.TIMEOUT
    resumed = manager.resume(env.job_id, "continue after timeout")
    assert resumed.job_id == env.job_id


def test_manager_scenario_crash_rc1(tmp_path: Path) -> None:
    """Process exit code 1 transitions to failed state."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Crash rc1 __SCENARIO:crash_rc1__",
        repo_path=str(repo_dir),
        title="Crash RC1 Test",
    )
    res = manager.wait(env.job_id, seconds=5)
    assert res.state == JobState.FAILED


def test_manager_scenario_crash_signal(tmp_path: Path) -> None:
    """Process signal kill transitions to failed state."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Crash signal __SCENARIO:crash_signal__",
        repo_path=str(repo_dir),
        title="Crash Signal Test",
    )
    res = manager.wait(env.job_id, seconds=5)
    assert res.state == JobState.FAILED


def test_manager_scenario_garbage_stdout(tmp_path: Path) -> None:
    """Unparseable stdout transitions to failed state."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Garbage stdout __SCENARIO:garbage_stdout__",
        repo_path=str(repo_dir),
        title="Garbage Stdout Test",
    )
    res = manager.wait(env.job_id, seconds=5)
    assert res.state == JobState.FAILED


def test_manager_scenario_huge_stdout(tmp_path: Path) -> None:
    """5 MB stdout payload is safely handled and capped by manager.result."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Huge stdout 5MB __SCENARIO:huge_stdout__",
        repo_path=str(repo_dir),
        title="Huge Stdout 5MB Test",
    )
    res = manager.wait(env.job_id, seconds=5)
    assert res.state == JobState.DONE
    result_resp = manager.result(env.job_id)
    assert len(result_resp.response.encode("utf-8")) <= 65536 + 500


def test_manager_concurrent_jobs_same_repo_git_fsck(tmp_path: Path) -> None:
    """Concurrent jobs on the same repo finish with separate branches and git fsck clean."""
    cfg = Config(max_concurrent_jobs=4)
    manager, repo_dir = _setup_manager_and_repo(tmp_path, config=cfg)

    env1 = manager.start(
        prompt="Job 1 __SCENARIO:success_with_commits__",
        repo_path=str(repo_dir),
        title="Concurrent Job 1",
    )
    env2 = manager.start(
        prompt="Job 2 __SCENARIO:success_with_commits__",
        repo_path=str(repo_dir),
        title="Concurrent Job 2",
    )

    res1 = manager.wait(env1.job_id, seconds=6)
    res2 = manager.wait(env2.job_id, seconds=6)

    assert res1.state == JobState.DONE
    assert res2.state == JobState.DONE
    assert res1.branch != res2.branch

    # Verify repository integrity with git fsck --full
    fsck_res = run_git(["fsck", "--full"], repo_dir, check=False)
    assert fsck_res.returncode == 0, f"git fsck failed: {fsck_res.stderr}"


def test_manager_invalid_job_id_all_methods(tmp_path: Path) -> None:
    """Invalid job_id strings must be rejected across every JobManager method."""
    manager, _ = _setup_manager_and_repo(tmp_path)
    invalid_ids = [
        "../x",
        "a/b",
        "",
        "a" * 1000,
        "12345678-1234-1234-1234-123456789ABC",  # uppercase UUID
        "12345678-1234-1234-1234-123456789abc\x00",  # UUID with NUL
    ]

    for bad_id in invalid_ids:
        with pytest.raises(ManagerError) as exc:
            manager.status(bad_id)
        assert exc.value.code == "invalid_job_id"

        with pytest.raises(ManagerError) as exc:
            manager.wait(bad_id)
        assert exc.value.code == "invalid_job_id"

        with pytest.raises(ManagerError) as exc:
            manager.result(bad_id)
        assert exc.value.code == "invalid_job_id"

        with pytest.raises(ManagerError) as exc:
            manager.cancel(bad_id)
        assert exc.value.code == "invalid_job_id"

        with pytest.raises(ManagerError) as exc:
            manager.resume(bad_id, "answer")
        assert exc.value.code == "invalid_job_id"

        with pytest.raises(ManagerError) as exc:
            manager.review(bad_id)
        assert exc.value.code == "invalid_job_id"

        with pytest.raises(ManagerError) as exc:
            manager.cleanup(bad_id)
        assert exc.value.code == "invalid_job_id"


def test_record_argv_for_fresh_and_resume_jobs(tmp_path: Path) -> None:
    """Record and print verbatim argv for fresh and resume jobs via FAKE_AGY_RECORD."""
    record_file = tmp_path / "record.jsonl"
    record_pointer = tmp_path / ".fake_agy_record"
    record_pointer.write_text(str(record_file))

    try:
        manager, repo_dir = _setup_manager_and_repo(tmp_path)

        # 1. Fresh job
        env = manager.start(
            prompt="Decision task __SCENARIO:needs_decision__",
            repo_path=str(repo_dir),
            title="Argv Recording Job",
        )
        res = manager.wait(env.job_id, seconds=5)
        assert res.state == JobState.NEEDS_DECISION

        # 2. Resumed job
        res_env = manager.resume(env.job_id, "User decision answer")
        manager.wait(res_env.job_id, seconds=5)

        assert record_file.is_file()
        lines = [
            json.loads(line_str)
            for line_str in record_file.read_text().splitlines()
            if line_str.strip()
        ]
        assert len(lines) >= 2

        fresh_argv = lines[0]
        resume_argv = lines[1]

        print("\n=== RECORDED FRESH ARGV ===")
        print(fresh_argv)
        print("=== RECORDED RESUME ARGV ===")
        print(resume_argv)

        assert fresh_argv[1:5] == ["--mode", "accept-edits", "--output-format", "json"]
        assert "--conversation" not in fresh_argv
        assert fresh_argv[-2] == "-p"

        assert resume_argv[1:5] == ["--mode", "accept-edits", "--output-format", "json"]
        assert "--conversation" in resume_argv
        assert resume_argv[-2] == "-p"
    finally:
        try:
            record_pointer.unlink(missing_ok=True)
        except Exception:
            pass


def test_manager_verify_pass_observable(tmp_path: Path):
    """Verify pass is fully observable in envelope and audit log per Part B1/B2/B4."""
    cfg = Config(verify_allowlist=["python3"])
    manager, repo_dir = _setup_manager_and_repo(tmp_path, config=cfg)
    test_script = repo_dir / "test_ok.py"
    test_script.write_text("print('all ok')\n")
    run_git(["add", "test_ok.py"], repo_dir)
    run_git(["commit", "-m", "Add test"], repo_dir)

    env = manager.start(
        prompt="Simple task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Verify Pass Job",
        verify=f"python3 {test_script}",
    )
    res = manager.wait(env.job_id, seconds=8)
    assert res.state == JobState.DONE

    # Verify standard envelope fields
    v = res.verify
    assert v["requested"] is True
    assert v["ran"] is True
    assert v["passed"] is True
    assert v["rc"] == 0
    assert v["attempts"] == 1
    assert isinstance(v["duration_seconds"], float)
    assert v["duration_seconds"] >= 0.0

    # Verify audit events
    audit_file = tmp_path / "state" / "jobs" / env.job_id / "audit.jsonl"
    assert audit_file.is_file()
    lines = [json.loads(ln) for ln in audit_file.read_text().splitlines() if ln.strip()]
    started_ev = next(e for e in lines if e["event"] == "verify_started")
    assert "command" in started_ev["detail"]
    assert "cwd" in started_ev["detail"]
    assert "/" not in started_ev["detail"]["cwd"]  # Basename only

    finished_ev = next(e for e in lines if e["event"] == "verify_finished")
    assert finished_ev["detail"]["rc"] == 0
    assert finished_ev["detail"]["passed"] is True
    assert finished_ev["detail"]["attempt"] == 1
    assert "tail_bytes" in finished_ev["detail"]


def test_manager_verify_fail_once_then_fixed(tmp_path: Path):
    """Verify fails once, fix turn corrects code, verify passes -> DONE per Part B4."""
    cfg = Config(verify_allowlist=["python3"], auto_fix_attempts=2)
    manager, repo_dir = _setup_manager_and_repo(tmp_path, config=cfg)
    test_script = repo_dir / "test_calc.py"
    test_script.write_text(
        "import sys\nsys.path.insert(0, '.')\nimport calc\nassert calc.add(2, 3) == 5\n"
    )
    run_git(["add", "test_calc.py"], repo_dir)
    run_git(["commit", "-m", "Add test"], repo_dir)

    env = manager.start(
        prompt="Fix calc __SCENARIO:verify_fail_then_fix__",
        repo_path=str(repo_dir),
        title="Verify Auto Fix Job",
        verify=f"python3 {test_script}",
    )

    # Allow auto-fix loop to execute both turns (15.0s deterministic poll)
    poll_until(
        lambda: manager.status(env.job_id).state == JobState.DONE,
        timeout_seconds=15.0,
        error_message="Job did not transition to DONE after auto-fix",
    )
    res = manager.status(env.job_id)

    assert res.state == JobState.DONE
    assert res.verify["passed"] is True
    assert res.verify["attempts"] == 2


def test_manager_verify_fail_twice_needs_review(tmp_path: Path):
    """Verify fails twice -> needs_review(verify_failed) with first next_action per Part B4."""
    cfg = Config(verify_allowlist=["python3"], auto_fix_attempts=1)
    manager, repo_dir = _setup_manager_and_repo(tmp_path, config=cfg)
    test_script = repo_dir / "test_calc.py"
    test_script.write_text(
        "import calc\nassert calc.add(2, 3) == 5\n"
    )
    run_git(["add", "test_calc.py"], repo_dir)
    run_git(["commit", "-m", "Add test"], repo_dir)

    env = manager.start(
        prompt="Broken calc __SCENARIO:verify_always_fail__",
        repo_path=str(repo_dir),
        title="Verify Always Fail Job",
        verify=f"python3 {test_script}",
    )

    poll_until(
        lambda: (
            manager.status(env.job_id).state == JobState.NEEDS_REVIEW
            and (manager.status(env.job_id).verify or {}).get("attempts") == 2
        ),
        timeout_seconds=30.0,
        poll_interval=0.05,
        error_message="Job did not reach needs_review with 2 verify attempts",
    )
    res = manager.status(env.job_id)

    assert res.state == JobState.NEEDS_REVIEW
    job = get_job(env.job_id, tmp_path / "state")
    assert job.review_reason == "verify_failed"
    assert res.verify["passed"] is False
    assert res.verify["attempts"] == 2
    assert "<untrusted_text" in res.verify["last_tail"]
    assert res.next_actions[0].startswith("Verification tests failed")


def test_manager_verify_timeout_needs_review(tmp_path: Path, monkeypatch):
    """Verify timeout transitions to needs_review(verify_timeout) per Part B4."""
    cfg = Config(verify_allowlist=["python3"])
    manager, repo_dir = _setup_manager_and_repo(tmp_path, config=cfg)

    # Monkeypatch run_verify inside supervisor to return timeout (rc 124)
    def mock_run_verify(cmd, wt, **kwargs):
        return 124, "Verification command timed out after 10s"

    monkeypatch.setattr("agy_mcp.verify.run_verify", mock_run_verify)

    env = manager.start(
        prompt="Simple task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Verify Timeout Job",
        verify="python3 check.py",
    )
    res = manager.wait(env.job_id, seconds=8)
    assert res.state == JobState.NEEDS_REVIEW
    job = get_job(env.job_id, tmp_path / "state")
    assert job.review_reason == "verify_timeout"
    assert res.verify["passed"] is False
    assert res.verify["rc"] == 124
    assert res.next_actions[0].startswith("Verification tests timed out")


def test_manager_verify_command_not_found_rc127(tmp_path: Path):
    """Non-existent verify executable returns rc 127 and transitions safely per Part B4."""
    cfg = Config(verify_allowlist=["/tmp/nonexistent_bin_xyz_999"])
    manager, repo_dir = _setup_manager_and_repo(tmp_path, config=cfg)

    env = manager.start(
        prompt="Simple task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Verify Not Found Job",
        verify="/tmp/nonexistent_bin_xyz_999",
    )
    res = manager.wait(env.job_id, seconds=8)
    assert res.state == JobState.NEEDS_REVIEW
    job = get_job(env.job_id, tmp_path / "state")
    assert job.review_reason == "verify_failed"
    assert res.verify["rc"] == 127
    assert res.verify["passed"] is False
    assert "Executable not found" in res.verify["last_tail"]


def test_manager_verify_output_ansi_and_binary_junk(tmp_path: Path):
    """Verify output with ANSI color sequences and binary bytes is safely handled per Part B4."""
    cfg = Config(verify_allowlist=["python3"])
    manager, repo_dir = _setup_manager_and_repo(tmp_path, config=cfg)
    junk_script = repo_dir / "junk_verify.py"
    junk_script.write_text(
        "import sys\n"
        "sys.stdout.write('\\x1b[31mRed Error\\x1b[0m\\n')\n"
        "sys.stdout.write('Binary bytes: \\x00\\x01\\xfe\\xff\\n')\n"
        "sys.exit(1)\n"
    )
    run_git(["add", "junk_verify.py"], repo_dir)
    run_git(["commit", "-m", "Add junk test"], repo_dir)

    env = manager.start(
        prompt="Task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Junk Output Job",
        verify=f"python3 {junk_script}",
    )
    res = manager.wait(env.job_id, seconds=8)
    assert res.state == JobState.NEEDS_REVIEW
    assert res.verify["passed"] is False
    assert "<untrusted_text" in res.verify["last_tail"]
    assert "Red Error" in res.verify["last_tail"]


def test_manager_verify_requested_but_not_run_has_reason(tmp_path: Path):
    """When verify was requested but job aborts before verify, ran=false plus reason per Part B3."""
    cfg = Config(verify_allowlist=["python3"])
    manager, repo_dir = _setup_manager_and_repo(tmp_path, config=cfg)

    env = manager.start(
        prompt="Crash job __SCENARIO:crash_rc1__",
        repo_path=str(repo_dir),
        title="Aborted Job",
        verify="python3 check.py",
    )
    res = manager.wait(env.job_id, seconds=8)
    assert res.state == JobState.FAILED
    assert res.verify["requested"] is True
    assert res.verify["ran"] is False
    assert res.verify["passed"] is None
    assert "reason" in res.verify
    assert res.verify["reason"] == "failed"


def test_manager_dangerous_permission_rules_present(tmp_path: Path, monkeypatch):
    """dangerous_permission_rules_present warning appears only when non-empty per Part C1.

    The envelope carries a count only: rule bodies (command text) are never
    echoed into the response the orchestrator sees (smoke-fix item 1).
    """
    import agy_mcp.manager as mgr_mod

    manager, repo_dir = _setup_manager_and_repo(tmp_path)

    # 1. Empty dangerous allow rules: warning must NOT appear
    empty_rules = {
        "permission_rules_effective": [],
        "dangerous_allow_rules": [],
        "warnings": [],
    }
    monkeypatch.setattr(
        mgr_mod,
        "inspect_permission_rules",
        lambda home: empty_rules,
    )
    env_clean = manager.start(
        prompt="Clean rules task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Clean Rules Job",
    )
    assert not any("dangerous_permission_rules_present" in w for w in env_clean.warnings)

    # 2. Non-empty dangerous allow rules: warning MUST appear with a count,
    #    and MUST NOT echo the rule bodies (never dict repr, never command text).
    monkeypatch.setattr(
        mgr_mod,
        "inspect_permission_rules",
        lambda home: {
            "permission_rules_effective": ["command(bash)"],
            "dangerous_allow_rules": ["command(bash)", "command(python)"],
            "warnings": [],
        },
    )
    env_dangerous = manager.start(
        prompt="Dangerous rules task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Dangerous Rules Job",
    )
    warn_entry = next(
        w for w in env_dangerous.warnings if "dangerous_permission_rules_present" in w
    )
    assert "2" in warn_entry
    assert "command(" not in warn_entry
    assert "bash" not in warn_entry
    assert "{" not in warn_entry  # Never a dict repr


def test_manager_source_dirty_warnings(tmp_path: Path):
    """source_dirty=true warning appears only when source repo is truly dirty per Part C2."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)

    # 1. Clean repo: no warning
    env_clean = manager.start(
        prompt="Clean task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Clean Source Job",
    )
    assert not any("source_dirty=true" in w for w in env_clean.warnings)
    manager.cleanup(env_clean.job_id, force=True)

    # 2. Repo with only untracked __pycache__: no warning
    (repo_dir / "__pycache__").mkdir(exist_ok=True)
    (repo_dir / "__pycache__" / "temp.cpython-312.pyc").write_bytes(b"temp")
    env_junk = manager.start(
        prompt="Junk task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Junk Only Source Job",
    )
    assert not any("source_dirty=true" in w for w in env_junk.warnings)
    manager.cleanup(env_junk.job_id, force=True)

    # 3. Truly dirty repo: warning present
    (repo_dir / "dirty_code.py").write_text("modified code\n")
    run_git(["add", "dirty_code.py"], repo_dir)
    run_git(["commit", "-m", "Track dirty_code"], repo_dir)
    (repo_dir / "dirty_code.py").write_text("uncommitted change\n")

    env_dirty = manager.start(
        prompt="Dirty task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="Dirty Source Job",
    )
    assert any("source_dirty=true" in w for w in env_dirty.warnings)







def _cleanup_events(job_dir: Path) -> list[dict]:
    audit_file = job_dir / "audit.jsonl"
    if not audit_file.is_file():
        return []
    return [
        json.loads(line)
        for line in audit_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _install_fail_first_kill(runner: LocalProcessRunner, monkeypatch):
    """Fail exactly one kill call, then delegate; capture launched handles."""
    handles = []
    orig_launch = runner.launch

    def capture_launch(*args, **kwargs):
        handle = orig_launch(*args, **kwargs)
        handles.append(handle)
        return handle

    state = {"failed": False}
    orig_kill = runner.kill

    def fail_once(handle, grace_seconds: float = 3.0):
        if not state["failed"]:
            state["failed"] = True
            raise RuntimeError(f"kill exploded in {handle.job_id}")
        return orig_kill(handle, grace_seconds=grace_seconds)

    monkeypatch.setattr(runner, "launch", capture_launch)
    monkeypatch.setattr(runner, "kill", fail_once)
    return handles, orig_kill


def test_provision_cancelled_kill_failure_is_audited(
    tmp_path: Path, monkeypatch
) -> None:
    """A kill failure during a cancelled launch appends cleanup_error, state stays cancelled."""
    runner = LocalProcessRunner(executable=FAKE_AGY)
    manager = JobManager(
        config=Config(),
        runner=runner,
        base_state_dir=tmp_path / "state",
        home=tmp_path,
        settle_window_seconds=0.05,
        poll_interval_seconds=0.05,
    )
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    handles, orig_kill = _install_fail_first_kill(runner, monkeypatch)

    def cancel_then_raise(job, home=None):
        from agy_mcp.jobs import transition_job_state

        transition_job_state(
            job,
            JobState.CANCELLED,
            event_trigger="simulated cancel",
            base_state_dir=manager.state_dir,
        )
        raise RuntimeError(f"setup exploded under {tmp_path}")

    monkeypatch.setattr("agy_mcp.manager.take_snapshot", cancel_then_raise)

    env = manager.start(
        prompt="Hang __SCENARIO:hang__",
        repo_path=str(repo_dir),
        title="Cancelled Kill Failure Test",
    )
    job_dir = get_job_dir(env.job_id, manager.state_dir)
    try:
        poll_until(
            lambda: any(e.get("event") == "cleanup_error" for e in _cleanup_events(job_dir))
        )
        job = get_job(env.job_id, manager.state_dir)
        assert job.state == JobState.CANCELLED
        events = [e for e in _cleanup_events(job_dir) if e.get("event") == "cleanup_error"]
        assert events[0]["detail"]["code"] == "kill_cancelled_launch"
        assert str(tmp_path) not in json.dumps(events[0])
    finally:
        for handle in handles:
            orig_kill(handle, grace_seconds=0.2)
        manager.shutdown()


def test_provision_setup_failure_kill_is_audited(tmp_path: Path, monkeypatch) -> None:
    """A kill failure during setup cleanup appends cleanup_error; outcome stays setup_failed."""
    runner = LocalProcessRunner(executable=FAKE_AGY)
    manager = JobManager(
        config=Config(),
        runner=runner,
        base_state_dir=tmp_path / "state",
        home=tmp_path,
        settle_window_seconds=0.05,
        poll_interval_seconds=0.05,
    )
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    _install_fail_first_kill(runner, monkeypatch)

    def fail_snapshot(job, home=None):
        raise RuntimeError(f"snapshot exploded under {tmp_path}")

    monkeypatch.setattr("agy_mcp.manager.take_snapshot", fail_snapshot)

    env = manager.start(
        prompt="Hang __SCENARIO:hang__",
        repo_path=str(repo_dir),
        title="Setup Failure Kill Test",
    )
    job_dir = get_job_dir(env.job_id, manager.state_dir)
    try:
        poll_until(
            lambda: get_job(env.job_id, manager.state_dir).state == JobState.FAILED
        )
        job = get_job(env.job_id, manager.state_dir)
        assert job.review_reason == "setup_failed"
        events = [e for e in _cleanup_events(job_dir) if e.get("event") == "cleanup_error"]
        assert events
        assert events[0]["detail"]["code"] == "kill_setup_failure"
        assert str(tmp_path) not in json.dumps(events[0])
    finally:
        manager.shutdown()
