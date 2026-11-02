"""Tests for Extra Item A: Policy files (.gitignore, .gitattributes, .gitmodules).

Edits to policy files must NOT kill the run immediately; they are blocked from staging,
recorded in skipped_files, the run continues, and at completion the job transitions
to needs_review(reason=policy_file_modified, partial_commit=true).
Sensitive files (.env, private keys) keep today's behavior (immediate kill/partial_commit).
"""

import json
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock

from agy_mcp.audit import save_snapshot, take_snapshot
from agy_mcp.jobs import JobState, create_job, get_job_dir, transition_job_state
from agy_mcp.runner import ProcessHandle
from agy_mcp.supervisor import JobSupervisor
from agy_mcp.watcher import CommitWatcher
from agy_mcp.worktree import create_worktree
from tests.helpers.make_repo import make_repo


def test_policy_file_edit_does_not_kill_run_and_transitions_at_completion(
    tmp_path: Path,
) -> None:
    """Modifying .gitignore must not kill the run immediately, and transitions at completion."""
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)

    # Commit initial .gitignore to repo
    gitignore = repo_dir / ".gitignore"
    gitignore.write_text("*.log\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "add gitignore"], cwd=repo_dir, check=True)

    state_dir = tmp_path / "state"
    state_dir.mkdir()

    job = create_job(
        repo_path=repo_dir,
        title="Policy File Test",
        prompt="hello",
        model="gemini-3.8-flash-medium",
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
    job.head_commit = head

    transition_job_state(
        job,
        JobState.RUNNING,
        event_trigger="Process launched",
        base_state_dir=state_dir,
    )
    baseline = take_snapshot(job, home=tmp_path)
    save_snapshot(baseline, job_dir / "snapshot.json")

    # Simulate agent modifying .gitignore and creating normal code
    (wt_path / ".gitignore").write_text("*.log\n*.tmp\n", encoding="utf-8")
    (wt_path / "feature.py").write_text("print('feature')\n", encoding="utf-8")

    # Provide COMMIT_MSG
    agy_dir = wt_path / ".agy"
    agy_dir.mkdir(parents=True, exist_ok=True)
    (agy_dir / "COMMIT_MSG").write_text("feat: add feature\n", encoding="utf-8")

    watcher = CommitWatcher(
        job=job,
        base_state_dir=state_dir,
        settle_window_seconds=0.0,
        baseline_snapshot=baseline,
        home=tmp_path,
        is_process_alive=lambda: True,
    )
    commit_res = watcher.tick(now=100.0)

    # 1. feature.py must be committed
    assert commit_res is not None, "Watcher did not commit feature.py"
    assert job.commits_count == 1

    # 2. .gitignore must be blocked from staging and recorded in skipped_files
    assert any(".gitignore" in str(sf) for sf in job.skipped_files)

    # 3. Job must STILL be RUNNING (must NOT kill the run)
    assert job.state == JobState.RUNNING, (
        f"Job state was killed prematurely! Expected RUNNING, got {job.state}"
    )

    # Now simulate agent completing successfully
    stdout_path = job_dir / "stdout.txt"
    stderr_path = job_dir / "stderr.txt"
    stdout_path.write_text(json.dumps({"status": "SUCCESS", "response": "done"}))
    stderr_path.touch()

    runner = MagicMock()
    runner.poll.return_value = 0
    handle = ProcessHandle(
        pid=1234,
        pgid=1234,
        start_time=time.time(),
        job_id=job.job_id,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    supervisor = JobSupervisor(
        job=job,
        handle=handle,
        runner=runner,
        job_dir=job_dir,
        max_minutes=job.max_minutes,
        baseline_snapshot=baseline,
        base_state_dir=state_dir,
        home=tmp_path,
    )
    # Process has terminated with rc 0
    final_state = supervisor._finalize_terminated_process(0, time.time())

    # 4. At completion, job must transition to needs_review(reason=policy_file_modified)
    assert final_state == JobState.NEEDS_REVIEW
    assert supervisor.job.review_reason == "policy_file_modified"

    # 5. Check transition audit event has partial_commit=True
    audit_file = job_dir / "audit.jsonl"
    events = [
        json.loads(line)
        for line in audit_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    transition_events = [e for e in events if e.get("event") == "state_transition"]
    last_transition = transition_events[-1]
    detail = last_transition.get("detail", {})
    assert detail.get("review_reason") == "policy_file_modified"
    assert detail.get("partial_commit") is True


def test_sensitive_file_edit_kills_run_immediately(tmp_path: Path) -> None:
    """Modifying tracked .env must kill the run immediately (today's behavior preserved)."""
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)

    env_file = repo_dir / ".env"
    env_file.write_text("API_KEY=123\n", encoding="utf-8")
    subprocess.run(["git", "add", ".env"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "add .env"], cwd=repo_dir, check=True)

    state_dir = tmp_path / "state"
    state_dir.mkdir()

    job = create_job(
        repo_path=repo_dir,
        title="Sensitive File Test",
        prompt="hello",
        model="gemini-3.8-flash-medium",
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
    job.head_commit = head

    transition_job_state(
        job,
        JobState.RUNNING,
        event_trigger="Process launched",
        base_state_dir=state_dir,
    )
    baseline = take_snapshot(job, home=tmp_path)
    save_snapshot(baseline, job_dir / "snapshot.json")

    # Agent modifies tracked .env and code
    (wt_path / ".env").write_text("API_KEY=hacked\n", encoding="utf-8")
    (wt_path / "app.py").write_text("print(1)\n", encoding="utf-8")

    agy_dir = wt_path / ".agy"
    agy_dir.mkdir(parents=True, exist_ok=True)
    (agy_dir / "COMMIT_MSG").write_text("feat: changes\n", encoding="utf-8")

    watcher = CommitWatcher(
        job=job,
        base_state_dir=state_dir,
        settle_window_seconds=0.0,
        baseline_snapshot=baseline,
        home=tmp_path,
        is_process_alive=lambda: True,
    )
    watcher.tick(now=100.0)

    # Sensitive tracked file modified must immediately set needs_review(partial_commit)
    assert job.state == JobState.NEEDS_REVIEW
    assert job.review_reason == "partial_commit"
