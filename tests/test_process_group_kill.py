"""Tests for finding F11: kill() must signal process group even if leader exited."""

import os
import sys
import time
from pathlib import Path

from agy_mcp.config import Config
from agy_mcp.jobs import get_job
from agy_mcp.manager import JobManager
from agy_mcp.models import JobState
from agy_mcp.runner import LocalProcessRunner
from tests.helpers.make_repo import get_safe_git_env, make_repo


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
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
        settle_window_seconds=0.1,
    )
    return mgr, repo_dir


def test_f11_supervisor_finalize_kills_descendants_after_leader_exits(tmp_path: Path) -> None:
    """When leader exits leaving children in group, finalization must kill the children."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)

    env = manager.start(
        prompt="Work on task __SCENARIO:leader_exits_child_survives__",
        repo_path=str(repo_dir),
        title="F11 Descendants Job",
    )

    # Wait for job to finish
    wait_env = manager.wait(env.job_id, seconds=10)
    assert wait_env.state == JobState.DONE

    job = get_job(env.job_id, manager.state_dir)
    assert job.worktree_path is not None
    child_pid_file = job.worktree_path / "child_pid.txt"
    assert child_pid_file.is_file(), "child_pid.txt was not created by fake agy"

    child_pid = int(child_pid_file.read_text(encoding="utf-8").strip())

    # Wait briefly for kill/reap to take effect
    kill_deadline = time.time() + 2.0
    while time.time() < kill_deadline and _is_alive(child_pid):
        time.sleep(0.05)

    # CRITICAL CHECK: child must be dead
    assert not _is_alive(child_pid), f"Child PID {child_pid} survived supervisor finalization"


def test_f11_runner_kill_signals_group_when_leader_terminated(tmp_path: Path) -> None:
    """LocalProcessRunner.kill() must signal process group even when leader has exited."""
    runner = LocalProcessRunner()

    # Script spawns child in group, writes child pid, and exits immediately
    script = (
        "import os, sys, time, subprocess\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "with open('child.pid', 'w') as f: f.write(str(child.pid))\n"
        "sys.exit(0)\n"
    )
    script_file = tmp_path / "leader.py"
    script_file.write_text(script, encoding="utf-8")

    out_file = tmp_path / "stdout.txt"
    err_file = tmp_path / "stderr.txt"

    handle = runner.launch(
        job_id="test-f11-direct",
        workdir=tmp_path,
        args=[sys.executable, str(script_file)],
        env=dict(os.environ),
        stdout_path=out_file,
        stderr_path=err_file,
    )

    # Wait for leader to terminate
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if runner.poll(handle) is not None:
            break
        time.sleep(0.05)

    assert runner.poll(handle) is not None, "Leader failed to exit"

    child_pid_file = tmp_path / "child.pid"
    deadline = time.time() + 2.0
    while time.time() < deadline and not child_pid_file.is_file():
        time.sleep(0.05)
    assert child_pid_file.is_file()

    child_pid = int(child_pid_file.read_text().strip())
    assert _is_alive(child_pid), "Child should be alive before kill()"

    # Now call runner.kill() on the handle whose leader has already exited
    runner.kill(handle, grace_seconds=0.5)

    # Wait for reap
    deadline = time.time() + 2.0
    while time.time() < deadline and _is_alive(child_pid):
        time.sleep(0.05)

    assert not _is_alive(child_pid), f"Child PID {child_pid} was not killed by runner.kill()"
