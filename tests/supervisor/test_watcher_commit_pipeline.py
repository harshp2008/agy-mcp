"""Unit and integration tests for watcher.py commit pipeline logic."""

from pathlib import Path

import pytest

from agy_mcp.audit import take_snapshot
from agy_mcp.gitops import run_git, safety_net_commit
from agy_mcp.jobs import create_job, transition_job_state
from agy_mcp.states import JobState
from agy_mcp.watcher import CommitWatcher, validate_commit_message
from agy_mcp.worktree import create_worktree, provision
from tests.helpers.make_repo import make_repo


def test_validate_commit_message(tmp_path: Path):
    """Verify commit message rules (<= 72 char subject, non-empty, UTF-8, no control chars)."""
    # 1. Valid standard message (72 ok)
    p_valid = tmp_path / "valid.txt"
    p_valid.write_text(
        "Add JWT auth middleware\n\nDetailed explanation of tokens.\n", encoding="utf-8"
    )
    valid, err = validate_commit_message(p_valid)
    assert valid is True
    assert err is None

    # 2. Empty message
    p_empty = tmp_path / "empty.txt"
    p_empty.write_text("", encoding="utf-8")
    valid, err = validate_commit_message(p_empty)
    assert valid is False
    assert "empty" in err

    # 2b. Whitespace-only message
    p_ws = tmp_path / "ws.txt"
    p_ws.write_text("   \n\n\t  \n", encoding="utf-8")
    valid, err = validate_commit_message(p_ws)
    assert valid is False
    assert "empty" in err

    # 3. Subject line > 72 chars (73 rejected)
    p_long = tmp_path / "long.txt"
    long_subject = "A" * 73 + "\n\nBody here.\n"
    p_long.write_text(long_subject, encoding="utf-8")
    valid, err = validate_commit_message(p_long)
    assert valid is False
    assert "exceeds 72 characters" in err

    # 4. Exactly 72 chars is allowed
    p_exact = tmp_path / "exact.txt"
    exact_subject = "A" * 72 + "\n\nBody.\n"
    p_exact.write_text(exact_subject, encoding="utf-8")
    valid, err = validate_commit_message(p_exact)
    assert valid is True

    # 4b. Multibyte characters counted as characters (72 emoji characters ok)
    p_mb_72 = tmp_path / "mb_72.txt"
    p_mb_72.write_text("🚀" * 72 + "\n\nBody with emojis.\n", encoding="utf-8")
    valid, err = validate_commit_message(p_mb_72)
    assert valid is True

    # 4c. Multibyte characters counted as characters (73 emoji characters rejected)
    p_mb_73 = tmp_path / "mb_73.txt"
    p_mb_73.write_text("🚀" * 73 + "\n\nBody with emojis.\n", encoding="utf-8")
    valid, err = validate_commit_message(p_mb_73)
    assert valid is False
    assert "exceeds 72 characters" in err

    # 5. Invalid UTF-8
    p_binary = tmp_path / "binary.txt"
    p_binary.write_bytes(b"\xff\xfe\x00\x00")
    valid, err = validate_commit_message(p_binary)
    assert valid is False
    assert "UTF-8" in err

    # 6. NUL byte in message
    p_nul = tmp_path / "nul.txt"
    p_nul.write_bytes(b"Commit subject\x00\n\nBody.\n")
    valid, err = validate_commit_message(p_nul)
    assert valid is False
    assert "NUL byte" in err

    # 7. Illegal control character
    p_ctrl = tmp_path / "ctrl.txt"
    p_ctrl.write_text("Commit title\n\x07Bell character\n", encoding="utf-8")
    valid, err = validate_commit_message(p_ctrl)
    assert valid is False
    assert "control character" in err

    # 8. CRLF line endings are allowed
    p_crlf = tmp_path / "crlf.txt"
    p_crlf.write_bytes(b"Commit subject line\r\n\r\nCRLF formatted body.\r\n")
    valid, err = validate_commit_message(p_crlf)
    assert valid is True

    # 9. Standalone carriage return (not part of CRLF) is rejected
    p_cr = tmp_path / "cr.txt"
    p_cr.write_bytes(b"Commit subject line\rRogue carriage return\n")
    valid, err = validate_commit_message(p_cr)
    assert valid is False
    assert "control character" in err


@pytest.fixture
def watcher_setup(tmp_path: Path, fake_home: Path, safe_git_env):
    """Set up real repo, worktree, job record, and CommitWatcher."""
    repo = make_repo(fake_home / "repo", env=safe_git_env)
    job_dir = tmp_path / "jobs" / "job_watch"
    job_dir.mkdir(parents=True)

    wt_path, branch, base_sha = create_worktree(
        repo_path=repo,
        job_dir=job_dir,
        title="Watcher Test",
    )
    provision(wt_path, "Watcher Test")

    job = create_job(
        repo_path=repo,
        title="Watcher Test",
        prompt="Write code",
        model="gemini-3.8-flash-low",
        max_minutes=60,
        base_state_dir=tmp_path,
        job_id="job_watch",
        base_commit=base_sha,
        branch=branch,
    )
    job.worktree_path = wt_path
    transition_job_state(job, JobState.RUNNING, "spawned", base_state_dir=tmp_path)

    snapshot = take_snapshot(job, home=fake_home)
    audit_violations: list[str] = []

    watcher = CommitWatcher(
        job=job,
        on_audit_violation=lambda r: audit_violations.append(r),
        checkpoint_interval_seconds=600,
        baseline_snapshot=snapshot,
        home=fake_home,
        base_state_dir=tmp_path,
    )

    return repo, wt_path, job, watcher, audit_violations


def test_watcher_settle_window_and_commit_pipeline(watcher_setup):
    """CommitWatcher waits >= 5s stability, validates, consumes, stages, and commits."""
    repo, wt_path, job, watcher, violations = watcher_setup
    commit_msg = wt_path / ".agy" / "COMMIT_MSG"

    # Make a code change in worktree
    (wt_path / "auth.py").write_text("def auth(): pass\n", encoding="utf-8")

    # Tick at t=0: no COMMIT_MSG yet
    assert watcher.tick(now=1000.0) is None

    # agy writes COMMIT_MSG
    commit_msg.write_text("Add auth module\n\nImplementation of auth.\n", encoding="utf-8")

    # First tick with COMMIT_MSG at t=1001.0 (settle timer begins)
    assert watcher.tick(now=1001.0) is None
    assert commit_msg.exists()

    # Tick at t=1003.0 (only 2s stable, still settling)
    assert watcher.tick(now=1003.0) is None
    assert commit_msg.exists()

    # Tick at t=1006.5 (5.5s stable >= 5s stability window)
    sha = watcher.tick(now=1006.5)
    assert sha is not None
    assert len(sha) == 40

    # Verify consume step: .agy/COMMIT_MSG was renamed to .agy/COMMIT_MSG.seen.1
    assert not commit_msg.exists()
    seen_1 = wt_path / ".agy" / "COMMIT_MSG.seen.1"
    assert seen_1.is_file()

    # Verify job generation and commit count updated
    assert job.commit_msg_generation == 1
    assert job.commits_count == 1
    assert job.head_commit == sha

    # Test writing the SAME text a second time (F12 fix: must still commit as generation 2)
    (wt_path / "auth.py").write_text("def auth(): return True\n", encoding="utf-8")
    commit_msg.write_text("Add auth module\n\nImplementation of auth.\n", encoding="utf-8")

    watcher.tick(now=1010.0)
    sha2 = watcher.tick(now=1016.0)
    assert sha2 is not None
    assert sha2 != sha
    assert (wt_path / ".agy" / "COMMIT_MSG.seen.2").is_file()
    assert job.commit_msg_generation == 2
    assert job.commits_count == 2

    # Step 3: Write a third COMMIT_MSG to verify 3 wrapper commits with agy-written messages
    (wt_path / "auth.py").write_text("def auth(): return 'v3'\n", encoding="utf-8")
    commit_msg.write_text("Update auth to v3\n\nThird step finished.\n", encoding="utf-8")

    watcher.tick(now=1020.0)
    sha3 = watcher.tick(now=1026.0)
    assert sha3 is not None
    assert sha3 != sha2
    assert (wt_path / ".agy" / "COMMIT_MSG.seen.3").is_file()
    assert job.commit_msg_generation == 3
    assert job.commits_count == 3


def test_watcher_invalid_commit_msg_retry_then_needs_review(watcher_setup):
    """First invalid COMMIT_MSG retries; second invalid message sets needs_review."""
    repo, wt_path, job, watcher, violations = watcher_setup
    commit_msg = wt_path / ".agy" / "COMMIT_MSG"

    # Write a message that stays invalid (NUL byte); an over-long subject is
    # repaired and committed by the wrapper instead.
    commit_msg.write_text("X\x00invalid\n", encoding="utf-8")

    # Settle
    watcher.tick(now=1000.0)
    # First settle: retries once, returns None, state still RUNNING
    assert watcher.tick(now=1006.0) is None
    assert job.state == JobState.RUNNING

    # Settle again with the same invalid message
    watcher.tick(now=1010.0)
    assert watcher.tick(now=1016.0) is None

    # Second failure transitions job to needs_review(reason="invalid_commit_message")
    assert job.state == JobState.NEEDS_REVIEW
    assert job.review_reason == "invalid_commit_message"


def test_watcher_audit_gate_blocks_commit(watcher_setup):
    """Audit violation prior to commit aborts commit and sets needs_review(audit_violation)."""
    repo, wt_path, job, watcher, violations = watcher_setup
    commit_msg = wt_path / ".agy" / "COMMIT_MSG"

    # Write valid code and commit message
    (wt_path / "file.txt").write_text("data\n", encoding="utf-8")
    commit_msg.write_text("Valid commit title\n", encoding="utf-8")

    # Tamper with worktree .git pointer
    (wt_path / ".git").write_text("gitdir: /fake\n", encoding="utf-8")

    watcher.tick(now=1000.0)
    # On settle, audit gate fires
    assert watcher.tick(now=1006.0) is None

    # Commit was aborted: COMMIT_MSG still exists, no commit made
    assert commit_msg.exists()
    assert job.state == JobState.NEEDS_REVIEW
    assert job.review_reason == "audit_violation"
    assert "audit_violation" in violations


def test_watcher_checkpoint_and_final_commits(watcher_setup):
    """Timed checkpoint commits when dirty; final_commit respects audit status and outcome."""
    repo, wt_path, job, watcher, violations = watcher_setup

    # Make dirty
    (wt_path / "code.py").write_text("x = 10\n", encoding="utf-8")

    # Tick before checkpoint interval -> no commit
    assert watcher.tick(now=100.0) is None

    # Tick after 600s checkpoint interval -> triggers checkpoint commit
    sha_cp = watcher.tick(now=750.0)
    assert sha_cp is not None

    # Final commit on done with clean worktree -> None
    assert watcher.final_commit("done") is None

    # Make dirty again
    (wt_path / "final.py").write_text("final code\n", encoding="utf-8")

    # Final commit on done with dirty worktree -> safety net commit
    sha_final = watcher.final_commit("done")
    assert sha_final is not None

    # Verify commit log
    log_res = run_git(["log", "-n", "1", "--format=%s"], wt_path)
    assert log_res.stdout.strip() == "agy: Watcher Test"


def test_safety_net_and_final_commit_suppressed_on_security_review(watcher_setup):
    """safety_net_commit and final_commit obey F05: no commit on audit violation or security."""
    repo, wt_path, job, watcher, violations = watcher_setup

    # Make dirty
    (wt_path / "leak.py").write_text("evil = 1\n", encoding="utf-8")

    # 1. When in needs_review with security reason, final_commit returns None
    job.state = JobState.NEEDS_REVIEW
    job.review_reason = "audit_violation"
    assert watcher.run_safety_net_commit(title=job.title) is None
    assert watcher.final_commit("done") is None

    # 2. When in needs_review with instruction_file_modified, final_commit returns None
    job.review_reason = "instruction_file_modified"
    assert watcher.run_safety_net_commit(title=job.title) is None
    assert watcher.final_commit("done") is None

    # 3. Direct safety_net_commit with audit_passed=False returns None
    assert safety_net_commit(wt_path, title="title", audit_passed=False) is None
