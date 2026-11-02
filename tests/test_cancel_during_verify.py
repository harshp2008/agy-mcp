"""Tests for finding F3: Cancel during verification phase."""

import json
import time
from pathlib import Path

from agy_mcp.config import Config
from agy_mcp.gitops import run_git
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

    cfg = config or Config(
        verify_allowlist=["python3"],
        auto_fix_attempts=2,
    )
    mgr = JobManager(
        runner=runner,
        config=cfg,
        base_state_dir=state_dir,
        home=home,
        settle_window_seconds=0.1,
    )
    return mgr, repo_dir


def test_f3_cancel_during_slow_verify(tmp_path: Path) -> None:
    """Cancelling while verify is running must kill verify, end CANCELLED, and not flip to DONE."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)

    # Slow verify script that sleeps for 5 seconds
    verify_script = repo_dir / "slow_verify.py"
    verify_script.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
    run_git(["add", "slow_verify.py"], repo_dir)
    run_git(["commit", "-m", "Add slow verify script"], repo_dir)

    env = manager.start(
        prompt="Work on task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="F3 Slow Verify Cancel Job",
        verify="python3 slow_verify.py",
    )

    # Wait for verify to start (audit event verify_started)
    job_dir = tmp_path / "state" / "jobs" / env.job_id
    audit_file = job_dir / "audit.jsonl"
    verify_started = False
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if audit_file.is_file():
            try:
                for line in audit_file.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        ev = json.loads(line)
                        if ev.get("event") == "verify_started":
                            verify_started = True
                            break
            except Exception:
                pass
        if verify_started:
            break
        time.sleep(0.05)

    assert verify_started, "Verify did not start in time"

    # Now cancel while verify is in progress
    cancel_res = manager.cancel(env.job_id)

    # agy_cancel must return the real state: cancelled (not running)
    assert cancel_res.state == JobState.CANCELLED, (
        f"Expected cancelled, but manager.cancel returned {cancel_res.state}"
    )

    # Verify that the job on disk remains cancelled after verify returns
    time.sleep(1.0)
    job = get_job(env.job_id, manager.state_dir)
    assert job.state == JobState.CANCELLED, f"Job flipped away from cancelled to {job.state}"

    # Verify no auto-fix attempts were scheduled
    assert manager._auto_fix_counts.get(env.job_id, 0) == 0
