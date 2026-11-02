"""D66: an invalid COMMIT_MSG is discarded when the job enters needs_review.

The agent controls ``.agy``. These tests pin the discard rename, the resumed
generation's behavior, the 20-file cap, and the symlink refusals.
"""

import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Callable

import pytest

from agy_mcp.audit import audit, take_snapshot
from agy_mcp.config import Config
from agy_mcp.gitops import run_git
from agy_mcp.jobs import create_job, get_job, get_job_dir, transition_job_state
from agy_mcp.manager import JobManager
from agy_mcp.models import JobState
from agy_mcp.runner import LocalProcessRunner
from agy_mcp.watcher import CommitWatcher, validate_commit_message
from agy_mcp.worktree import create_worktree, provision
from tests.helpers.make_repo import get_safe_git_env, make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"


def _audit_events(job_dir: Path) -> list[dict]:
    path = job_dir / "audit.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _invalid_transitions(events: list[dict]) -> list[dict]:
    return [
        event
        for event in events
        if event.get("event") == "state_transition"
        and event.get("detail", {}).get("review_reason") == "invalid_commit_message"
    ]


def _poll_until(predicate: Callable[[], bool], timeout_seconds: float = 12.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    assert predicate(), "timed out waiting for condition"


@pytest.fixture
def watcher_setup(tmp_path: Path, fake_home: Path, safe_git_env):
    repo = make_repo(fake_home / "repo", env=safe_git_env)
    job_dir = tmp_path / "jobs" / "job_stale"
    job_dir.mkdir(parents=True)

    wt_path, branch, base_sha = create_worktree(
        repo_path=repo,
        job_dir=job_dir,
        title="Stale Commit Msg Test",
    )
    provision(wt_path, "Stale Commit Msg Test")

    job = create_job(
        repo_path=repo,
        title="Stale Commit Msg Test",
        prompt="Write code",
        model="gemini-3.8-flash-low",
        max_minutes=60,
        base_state_dir=tmp_path,
        job_id="job_stale",
        base_commit=base_sha,
        branch=branch,
    )
    job.worktree_path = wt_path
    transition_job_state(job, JobState.RUNNING, "spawned", base_state_dir=tmp_path)

    snapshot = take_snapshot(job, home=fake_home)
    watcher = CommitWatcher(
        job=job,
        checkpoint_interval_seconds=600,
        baseline_snapshot=snapshot,
        home=fake_home,
        base_state_dir=tmp_path,
    )
    return tmp_path, wt_path, job, watcher, fake_home


def _setup_manager_and_repo(tmp_path: Path) -> tuple[JobManager, Path]:
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True, env=get_safe_git_env(tmp_path))
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    runner = LocalProcessRunner(executable=str(FAKE_AGY))
    manager = JobManager(
        runner=runner,
        config=Config(max_concurrent_jobs=2, daily_jobs_cap=5),
        base_state_dir=state_dir,
        home=tmp_path,
        settle_window_seconds=0.1,
        poll_interval_seconds=0.05,
    )
    return manager, repo_dir


def _wait_for_review(manager: JobManager, job_id: str) -> None:
    _poll_until(
        lambda: manager.status(job_id).state == JobState.NEEDS_REVIEW,
        timeout_seconds=12.0,
    )


def test_invalid_commit_msg_discarded_and_resume_does_not_refail(tmp_path: Path) -> None:
    """After needs_review the stale message is renamed, so the resumed run is clean."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Work on task __SCENARIO:stale_commit_msg__",
        repo_path=str(repo_dir),
        title="Stale Commit Message Test",
    )
    _wait_for_review(manager, env.job_id)

    job = get_job(env.job_id, manager.state_dir)
    job_dir = get_job_dir(env.job_id, manager.state_dir)
    wt_path = Path(job.worktree_path or "")
    assert job.review_reason == "invalid_commit_message"

    discarded = wt_path / ".agy" / "COMMIT_MSG.invalid.1"
    assert not (wt_path / ".agy" / "COMMIT_MSG").exists()
    assert discarded.is_file()
    assert discarded.read_text(encoding="utf-8").startswith("Bad stale message")

    events = _audit_events(job_dir)
    assert len(_invalid_transitions(events)) == 1

    resumed = manager.resume(job.job_id, "fixed commit message format")
    assert resumed.state in (JobState.RUNNING, JobState.DONE)
    final = manager.wait(job.job_id, seconds=10)
    assert final.state == JobState.DONE

    events = _audit_events(job_dir)
    assert len(_invalid_transitions(events)) == 1, "resumed generation re-failed on a stale message"
    assert not (wt_path / ".agy" / "COMMIT_MSG").exists()
    assert discarded.is_file()


def test_valid_message_after_resume_commits_normally(tmp_path: Path) -> None:
    """The resumed run can still write a valid message and get it committed."""
    manager, repo_dir = _setup_manager_and_repo(tmp_path)
    env = manager.start(
        prompt="Work on task __SCENARIO:stale_commit_msg_valid_resume__",
        repo_path=str(repo_dir),
        title="Stale Commit Message Valid Resume",
    )
    _wait_for_review(manager, env.job_id)

    resumed = manager.resume(env.job_id, "fixed commit message format")
    assert resumed.state in (JobState.RUNNING, JobState.DONE)
    final = manager.wait(env.job_id, seconds=10)
    assert final.state == JobState.DONE

    job = get_job(env.job_id, manager.state_dir)
    wt_path = Path(job.worktree_path or "")
    assert job.commits_count >= 1
    subject = run_git(["log", "-n", "1", "--format=%s"], wt_path)
    assert subject.returncode == 0
    assert subject.stdout.strip() == "Resume after discard"


def test_symlinked_commit_msg_link_is_renamed_not_the_target(
    watcher_setup,
) -> None:
    """A symlinked COMMIT_MSG entry is renamed; its target is never modified."""
    tmp_path, wt_path, job, watcher, fake_home = watcher_setup
    target = tmp_path / "outside_target.txt"
    original = "SYMLINK_TARGET_" + "E" * 80 + "\n\nBody."
    target.write_text(original, encoding="utf-8")
    before = target.stat()
    link = wt_path / ".agy" / "COMMIT_MSG"
    link.symlink_to(target)

    watcher.tick(now=1000.0)
    watcher.tick(now=1006.0)
    watcher.tick(now=1010.0)
    watcher.tick(now=1016.0)

    assert job.state == JobState.NEEDS_REVIEW
    discarded = wt_path / ".agy" / "COMMIT_MSG.invalid.1"
    assert discarded.is_symlink()
    assert not link.exists()
    assert target.read_text(encoding="utf-8") == original
    after = target.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)


def test_symlinked_agy_directory_is_not_followed(watcher_setup) -> None:
    """A symlinked ``.agy`` parent is refused; the target message is untouched."""
    tmp_path, wt_path, job, watcher, fake_home = watcher_setup
    outside = tmp_path / "outside_agy"
    outside.mkdir()
    target = outside / "COMMIT_MSG"
    original = "F" * 80 + "\n\nBody in the symlink target."
    target.write_text(original, encoding="utf-8")
    before = target.stat()

    shutil.rmtree(wt_path / ".agy")
    (wt_path / ".agy").symlink_to(outside, target_is_directory=True)

    watcher.tick(now=1000.0)
    watcher.tick(now=1006.0)
    watcher.tick(now=1010.0)
    watcher.tick(now=1016.0)

    assert job.state == JobState.RUNNING
    assert target.read_text(encoding="utf-8") == original
    after = target.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)
    assert not list(outside.glob("COMMIT_MSG.invalid.*"))


def test_non_regular_commit_msg_is_rejected_without_blocking(watcher_setup) -> None:
    """A FIFO is rejected by lstat before any open can block the watcher."""
    tmp_path, wt_path, job, watcher, fake_home = watcher_setup
    fifo = wt_path / ".agy" / "COMMIT_MSG"
    if fifo.exists() or fifo.is_symlink():
        fifo.unlink()
    os.mkfifo(fifo)

    result: list[tuple[bool, str | None]] = []
    reader = threading.Thread(
        target=lambda: result.append(validate_commit_message(fifo)),
        daemon=True,
    )
    reader.start()
    reader.join(1.0)
    try:
        assert not reader.is_alive(), "validate_commit_message blocked opening a FIFO"
        assert result and result[0][0] is False
    finally:
        # Unblock a reader that the mutation left waiting for a writer.
        try:
            fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            pass
        else:
            os.close(fd)
        reader.join(1.0)


def test_discard_cap_is_twenty_and_last_slot_is_reused(watcher_setup) -> None:
    """At the cap the last slot is overwritten; no slot file is deleted."""
    tmp_path, wt_path, job, watcher, fake_home = watcher_setup
    agy_dir = wt_path / ".agy"
    for n in range(1, 21):
        (agy_dir / f"COMMIT_MSG.invalid.{n}").write_text(f"old-{n}\n", encoding="utf-8")

    (agy_dir / "COMMIT_MSG").write_text("G\x00invalid\n\nBody.", encoding="utf-8")
    watcher.tick(now=1000.0)
    watcher.tick(now=1006.0)
    watcher.tick(now=1010.0)
    watcher.tick(now=1016.0)

    assert job.state == JobState.NEEDS_REVIEW
    assert not (agy_dir / "COMMIT_MSG").exists()
    assert (agy_dir / "COMMIT_MSG.invalid.1").read_text(encoding="utf-8") == "old-1\n"
    last = (agy_dir / "COMMIT_MSG.invalid.20").read_text(encoding="utf-8")
    assert last.startswith("G\x00invalid")
    assert len(list(agy_dir.glob("COMMIT_MSG.invalid.*"))) == 20


def test_discard_audit_event_has_no_message_text_or_absolute_path(watcher_setup) -> None:
    """The audit event names only a short code and the new file name."""
    tmp_path, wt_path, job, watcher, fake_home = watcher_setup
    # A NUL byte keeps this message rejected (privacy check, not repair).
    secret = "LEAKME_" + "Z" * 80 + "\x00\n\nSecret body text."
    (wt_path / ".agy" / "COMMIT_MSG").write_text(secret, encoding="utf-8")

    watcher.tick(now=1000.0)
    watcher.tick(now=1006.0)
    watcher.tick(now=1010.0)
    watcher.tick(now=1016.0)

    assert job.state == JobState.NEEDS_REVIEW
    job_dir = get_job_dir(job.job_id, tmp_path)
    discarded = [
        event
        for event in _audit_events(job_dir)
        if event.get("event") == "commit_msg_discarded"
    ]
    assert len(discarded) == 1
    event = discarded[0]
    assert event["detail"] == {"code": "invalid_message", "file": "COMMIT_MSG.invalid.1"}
    raw = json.dumps(event)
    assert "LEAKME" not in raw
    assert "Secret body text" not in raw
    assert str(wt_path) not in raw

    findings = audit(job, watcher.baseline_snapshot, home=fake_home)
    assert not findings.has_fatal

    # The renamed file lives under excluded .agy/ and is never committed.
    transition_job_state(job, JobState.RUNNING, "test resume", base_state_dir=tmp_path)
    (wt_path / "code.py").write_text("value = 1\n", encoding="utf-8")
    (wt_path / ".agy" / "COMMIT_MSG").write_text(
        "Add code\n\nValid message.", encoding="utf-8"
    )
    watcher.tick(now=1100.0)
    sha = watcher.tick(now=1106.0)
    assert sha is not None
    names = run_git(["show", "--name-only", "--format=", sha], wt_path)
    assert names.returncode == 0
    assert ".agy/" not in names.stdout
    assert "code.py" in names.stdout
    assert (wt_path / ".agy" / "COMMIT_MSG.invalid.1").is_file()


def test_overlong_subject_is_repaired_and_committed(watcher_setup) -> None:
    """An over-long subject is truncated at a word boundary and committed."""
    tmp_path, wt_path, job, watcher, fake_home = watcher_setup
    (wt_path / "repair_me.py").write_text("value = 1\n", encoding="utf-8")
    original = "Add the repaired subject for a very long commit message that needs shortening"
    assert len(original) > 72
    (wt_path / ".agy" / "COMMIT_MSG").write_text(
        original + "\n\nBody text stays.\n", encoding="utf-8"
    )

    watcher.tick(now=1000.0)
    sha = watcher.tick(now=1006.0)

    assert sha is not None
    assert job.state == JobState.RUNNING
    message = run_git(["show", "-s", "--format=%B", sha], wt_path, check=True).stdout
    lines = message.splitlines()
    assert len(lines[0]) <= 72
    assert lines[0] in original
    assert original in lines[2:]
    assert "Body text stays." in message

    events = _audit_events(get_job_dir(job.job_id, tmp_path))
    repaired = [event for event in events if event.get("event") == "commit_msg_repaired"]
    assert len(repaired) == 1
    assert repaired[0]["detail"] == {"original_len": len(original)}


def test_rejected_message_evidence_is_preserved_in_the_job_dir(watcher_setup) -> None:
    """A still-invalid message is copied to the job dir and its first line is in job.error."""
    tmp_path, wt_path, job, watcher, fake_home = watcher_setup
    raw = b"LEAKME_preserved\x00\n\nSecret body text."
    (wt_path / ".agy" / "COMMIT_MSG").write_bytes(raw)

    watcher.tick(now=1000.0)
    watcher.tick(now=1006.0)
    watcher.tick(now=1010.0)
    watcher.tick(now=1016.0)

    assert job.state == JobState.NEEDS_REVIEW
    assert job.review_reason == "invalid_commit_message"
    job_dir = get_job_dir(job.job_id, tmp_path)
    evidence = job_dir / "rejected_COMMIT_MSG.txt"
    assert evidence.is_file()
    assert evidence.read_bytes() == raw
    assert job.error is not None
    assert job.error["evidence_file"] == "rejected_COMMIT_MSG.txt"
    assert job.error["first_line_repr"] == repr("LEAKME_preserved\x00")[:120]

    # Audit privacy is unchanged: the discarded event still carries only code/file.
    discarded = [
        event
        for event in _audit_events(job_dir)
        if event.get("event") == "commit_msg_discarded"
    ]
    assert discarded
    assert discarded[0]["detail"] == {
        "code": "invalid_message",
        "file": "COMMIT_MSG.invalid.1",
    }
    assert "LEAKME" not in json.dumps(discarded[0])
