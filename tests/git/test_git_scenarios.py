"""Comprehensive scenario tests B1 through B12 for the git layer.

Real git repos only, isolated tmp_path, GIT_CONFIG_GLOBAL=/dev/null, GIT_CONFIG_NOSYSTEM=1.
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agy_mcp.audit import take_snapshot
from agy_mcp.config import Config
from agy_mcp.gitops import (
    GitError,
    commit_with_retry,
    run_git,
    stage_changes,
)
from agy_mcp.jobs import create_job, get_job, save_job
from agy_mcp.states import JobState
from agy_mcp.watcher import CommitWatcher
from agy_mcp.worktree import PreflightError, create_worktree, preflight, provision
from tests.helpers.make_repo import make_repo


def test_b1_hostile_root_gitignore(tmp_path: Path, safe_git_env):
    """B1: Hostile root .gitignore containing !.agy negations cannot cause .agy to be tracked."""
    repo = make_repo(tmp_path / "repo", env=safe_git_env)
    job_dir = tmp_path / "job_b1"
    job_dir.mkdir(parents=True)

    wt_path, branch, _ = create_worktree(repo, job_dir=job_dir, title="test-b1")
    provision(wt_path, title="test-b1")

    # Inject hostile root .gitignore with !.agy negations
    (wt_path / ".gitignore").write_text(
        "*.tmp\n!.agy/PLAN.md\n!.agy/*\n!.agy\n", encoding="utf-8"
    )
    (wt_path / "app.py").write_text("print('hello')", encoding="utf-8")
    (wt_path / ".agy" / "COMMIT_MSG").write_text("Initial commit\n", encoding="utf-8")

    # Stage and commit changes
    staging = stage_changes(wt_path)
    assert "app.py" in staging.staged_files
    assert ".agy/PLAN.md" not in staging.staged_files
    assert ".agy/COMMIT_MSG" not in staging.staged_files

    msg_file = wt_path / "msg.txt"
    msg_file.write_text("Commit app\n", encoding="utf-8")
    commit_with_retry(wt_path, msg_file=msg_file)

    # Assert git ls-files contains NOTHING under .agy/
    ls_res = run_git(["ls-files"], wt_path, env=safe_git_env, check=True)
    tracked_files = ls_res.stdout.splitlines()
    for f in tracked_files:
        assert not f.startswith(".agy"), f"Hostile .gitignore leaked .agy file into git: {f}"


def test_b2_branch_slug_title_table(tmp_path: Path, safe_git_env):
    """B2: Diverse and hostile titles produce valid git branch refs starting with agy/."""
    repo = make_repo(tmp_path / "repo", env=safe_git_env)

    titles_table = [
        "a/b",
        "..",
        "~x",
        "x^y",
        "x:y",
        "-rf",
        "x.lock",
        ".hidden",
        "🚀🔥",
        "",
        "A" * 500,
        "Title\nwith\nnewlines",
    ]

    for idx, title in enumerate(titles_table):
        job_dir = tmp_path / f"job_b2_{idx}"
        job_dir.mkdir(parents=True)

        wt_path, branch, _ = create_worktree(repo, job_dir=job_dir, title=title)
        assert branch.startswith("agy/"), f"Branch {branch} does not start with agy/ for {title!r}"

        # Must pass git check-ref-format --branch
        ref_check = run_git(["check-ref-format", "--branch", branch], repo, env=safe_git_env)
        assert ref_check.returncode == 0, f"Branch ref invalid: {branch} for title {title!r}"


def test_b3_branch_collision_retry(tmp_path: Path, safe_git_env):
    """B3: When candidate branch already exists, retry loop allocates a fresh branch id."""
    repo = make_repo(tmp_path / "repo", env=safe_git_env)
    job_dir = tmp_path / "job_b3"
    job_dir.mkdir(parents=True)

    # Pre-create candidate branch in repo
    run_git(["branch", "agy/task-colliding", "HEAD"], repo, env=safe_git_env, check=True)

    # Injected ID generator provides colliding first, then resolved
    id_sequence = iter(["colliding", "resolved"])

    wt_path, branch, _ = create_worktree(
        repo,
        job_dir=job_dir,
        title="task",
        id_generator=lambda: next(id_sequence),
    )

    assert branch == "agy/task-resolved"


def test_b4_sensitive_staging_variants(tmp_path: Path, safe_git_env):
    """B4: Tracked .env.prod modified => skipped+partial_commit; untracked id_rsa skipped.

    Deleted secret => partial_commit.
    """
    repo = make_repo(tmp_path / "repo", env=safe_git_env)

    # Pre-track a secret and a .env.production file
    (repo / ".env.production").write_text("DB_PASS=1234\n", encoding="utf-8")
    (repo / "secrets.pem").write_text("CERT DATA\n", encoding="utf-8")
    run_git(["add", ".env.production", "secrets.pem"], repo, env=safe_git_env, check=True)
    run_git(["commit", "-m", "Initial credentials"], repo, env=safe_git_env, check=True)

    # 1. Modify tracked .env.production
    (repo / ".env.production").write_text("DB_PASS=mutated\n", encoding="utf-8")
    # 2. Add new untracked id_rsa
    (repo / "id_rsa").write_text("ssh-rsa ...\n", encoding="utf-8")
    # 3. Delete tracked secrets.pem
    (repo / "secrets.pem").unlink()
    # 4. Add legitimate code change
    (repo / "legit.py").write_text("print(1)\n", encoding="utf-8")

    staging = stage_changes(repo)

    assert "legit.py" in staging.staged_files
    assert ".env.production" in staging.skipped_files
    assert "id_rsa" in staging.skipped_files
    assert "secrets.pem" in staging.skipped_files
    assert staging.partial_commit is True

    # Untracked id_rsa must remain untracked
    status_proc = run_git(["status", "--porcelain=v1"], repo, env=safe_git_env, check=True)
    assert "? id_rsa" in status_proc.stdout or "?? id_rsa" in status_proc.stdout


def test_tracked_dot_env_never_committed_in_diff(tmp_path: Path, safe_git_env):
    """Regression test: modifications to tracked .env are never committed into git history."""
    repo = make_repo(tmp_path / "repo", env=safe_git_env)

    # Commit initial tracked .env
    (repo / ".env").write_text("SECRET=initial\n", encoding="utf-8")
    run_git(["add", ".env"], repo, env=safe_git_env, check=True)
    run_git(["commit", "-m", "Initial tracked .env"], repo, env=safe_git_env, check=True)

    # Modify .env and create code file
    (repo / ".env").write_text("SECRET=leaked_new_key\n", encoding="utf-8")
    (repo / "app.py").write_text("print('safe')\n", encoding="utf-8")

    staging = stage_changes(repo)
    assert ".env" in staging.skipped_files
    assert "app.py" in staging.staged_files
    assert staging.partial_commit is True

    # Commit the staged changes
    msg_file = repo / "msg.txt"
    msg_file.write_text("feat: add app", encoding="utf-8")
    sha = commit_with_retry(repo, msg_file=msg_file)
    assert sha is not None

    # Inspect commit diff: .env must NOT be modified in commit diff
    diff_res = run_git(["diff", "HEAD~1..HEAD"], repo, env=safe_git_env, check=True)
    assert ".env" not in diff_res.stdout
    assert "SECRET=leaked_new_key" not in diff_res.stdout
    assert "app.py" in diff_res.stdout


def test_b5_rename_normal_vs_sensitive(tmp_path: Path, safe_git_env):
    """B5: Normal file rename staged cleanly; rename into sensitive name skipped and flagged."""
    repo = make_repo(tmp_path / "repo", env=safe_git_env)

    (repo / "normal.txt").write_text("hello\n", encoding="utf-8")
    (repo / "source.txt").write_text("secret\n", encoding="utf-8")
    run_git(["add", "normal.txt", "source.txt"], repo, env=safe_git_env, check=True)
    run_git(["commit", "-m", "Seed files"], repo, env=safe_git_env, check=True)

    # Normal rename
    run_git(["mv", "normal.txt", "renamed_normal.txt"], repo, env=safe_git_env, check=True)
    # Rename into sensitive name
    run_git(["mv", "source.txt", ".env.local"], repo, env=safe_git_env, check=True)

    staging = stage_changes(repo)

    assert "renamed_normal.txt" in staging.staged_files
    assert ".env.local" in staging.skipped_files
    assert staging.partial_commit is True


def test_b6_special_filenames_staging(tmp_path: Path, safe_git_env):
    """B6: Filenames with newline, tab, quote, unicode, and dash staged via porcelain -z."""
    repo = make_repo(tmp_path / "repo", env=safe_git_env)

    files = [
        "file\nwith\nnewline.txt",
        "file\twith\ttab.txt",
        'file"with"quote.txt',
        "file_ünicode_🚀.txt",
        "-leading_dash.txt",
    ]

    for fname in files:
        (repo / fname).write_text("content\n", encoding="utf-8")

    staging = stage_changes(repo)
    for fname in files:
        assert fname in staging.staged_files

    msg_file = repo / "msg.txt"
    msg_file.write_text("Special filenames commit\n", encoding="utf-8")
    commit_with_retry(repo, msg_file=msg_file)

    ls_res = run_git(["ls-files", "-z"], repo, env=safe_git_env, check=True)
    tracked_null = ls_res.stdout.encode("utf-8").split(b"\x00")
    tracked_str = [t.decode("utf-8") for t in tracked_null if t]

    for fname in files:
        assert fname in tracked_str


def test_b7_same_commit_msg_across_restart(tmp_path: Path, safe_git_env):
    """B7: Identical COMMIT_MSG content across two steps creates two distinct commits.

    Persists across process reload.
    """
    repo = make_repo(tmp_path / "repo", env=safe_git_env)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)

    job = create_job(
        repo_path=repo,
        title="Restart Test",
        prompt="Test prompt",
        model="gemini-3.8-flash-low",
        max_minutes=10,
        base_state_dir=state_dir,
    )
    job_dir = tmp_path / "job_b7"
    job_dir.mkdir(parents=True)

    wt_path, branch, _ = create_worktree(repo, job_dir=job_dir, title="Restart Test")
    job.worktree_path = wt_path
    provision(wt_path, title="Restart Test")
    job.state = JobState.RUNNING
    save_job(job, base_state_dir=state_dir)

    base_snapshot = take_snapshot(job, home=tmp_path / "home")
    watcher1 = CommitWatcher(
        job=job,
        baseline_snapshot=base_snapshot,
        base_state_dir=state_dir,
        settle_window_seconds=1.0,
        home=tmp_path / "home",
    )

    # Step 1: Write commit message
    (wt_path / "code1.py").write_text("v = 1\n", encoding="utf-8")
    commit_msg = wt_path / ".agy" / "COMMIT_MSG"
    commit_msg.write_text("Implement feature step\n\nIdentical body.\n", encoding="utf-8")

    # Settle and commit
    watcher1.tick(now=100.0)
    sha1 = watcher1.tick(now=102.0)
    assert sha1 is not None
    assert job.commits_count == 1
    assert job.commit_msg_generation == 1
    assert (wt_path / ".agy" / "COMMIT_MSG.seen.1").exists()

    # Step 2: Simulate watcher/process restart by reloading job from state_dir
    job_reloaded = get_job(job.job_id, base_state_dir=state_dir)
    assert job_reloaded.commits_count == 1
    assert job_reloaded.commit_msg_generation == 1

    watcher2 = CommitWatcher(
        job=job_reloaded,
        baseline_snapshot=base_snapshot,
        base_state_dir=state_dir,
        settle_window_seconds=1.0,
        home=tmp_path / "home",
    )

    # Write identical commit message text again
    (wt_path / "code2.py").write_text("v = 2\n", encoding="utf-8")
    commit_msg.write_text("Implement feature step\n\nIdentical body.\n", encoding="utf-8")

    # Settle and commit step 2
    watcher2.tick(now=200.0)
    sha2 = watcher2.tick(now=202.0)
    assert sha2 is not None
    assert sha2 != sha1
    assert job_reloaded.commits_count == 2
    assert job_reloaded.commit_msg_generation == 2
    assert (wt_path / ".agy" / "COMMIT_MSG.seen.2").exists()


def test_b8_half_written_commit_msg(tmp_path: Path, safe_git_env):
    """B8: Half-written COMMIT_MSG resets window and commits only the final completed message."""
    repo = make_repo(tmp_path / "repo", env=safe_git_env)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)

    job = create_job(
        repo_path=repo,
        title="Partial Write Test",
        prompt="Test prompt",
        model="gemini-3.8-flash-low",
        max_minutes=10,
        base_state_dir=state_dir,
    )
    job_dir = tmp_path / "job_b8"
    job_dir.mkdir(parents=True)

    wt_path, branch, _ = create_worktree(repo, job_dir=job_dir, title="Partial Write Test")
    job.worktree_path = wt_path
    provision(wt_path, title="Partial Write Test")
    job.state = JobState.RUNNING
    save_job(job, base_state_dir=state_dir)

    base_snapshot = take_snapshot(job, home=tmp_path / "home")
    watcher = CommitWatcher(
        job=job,
        baseline_snapshot=base_snapshot,
        base_state_dir=state_dir,
        settle_window_seconds=2.0,
        home=tmp_path / "home",
    )

    (wt_path / "code.py").write_text("x = 42\n", encoding="utf-8")
    commit_msg = wt_path / ".agy" / "COMMIT_MSG"

    # Write partial 5 bytes
    commit_msg.write_text("Parti", encoding="utf-8")

    # Tick at t=10.0: seen for the first time
    assert watcher.tick(now=10.0) is None

    # Write remaining text at t=11.0
    full_msg = "Add complete feature implementation\n\nDetailed body.\n"
    commit_msg.write_text(full_msg, encoding="utf-8")

    # Tick at t=12.0: hash changed, stability window resets
    assert watcher.tick(now=12.0) is None

    # Tick at t=13.0 (only 1.0s elapsed < 2.0s settle window): still not settled
    assert watcher.tick(now=13.0) is None

    # Tick at t=14.5 (2.5s elapsed >= 2.0s): now settled and commits
    sha = watcher.tick(now=14.5)
    assert sha is not None
    assert job.commits_count == 1

    # Verify committed message subject
    subject_res = run_git(
        ["log", "-n", "1", "--format=%s"], wt_path, env=safe_git_env, check=True
    )
    assert subject_res.stdout.strip() == "Add complete feature implementation"


def test_b9_safety_net_after_audit_violation(tmp_path: Path, safe_git_env):
    """B9: Safety-net commit is suppressed after an audit violation; git log remains unchanged."""
    repo = make_repo(tmp_path / "repo", env=safe_git_env)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)

    job = create_job(
        repo_path=repo,
        title="Audit Suppression Test",
        prompt="Test prompt",
        model="gemini-3.8-flash-low",
        max_minutes=10,
        base_state_dir=state_dir,
    )
    job_dir = tmp_path / "job_b9"
    job_dir.mkdir(parents=True)

    wt_path, branch, _ = create_worktree(repo, job_dir=job_dir, title="Audit Suppression Test")
    job.worktree_path = wt_path
    provision(wt_path, title="Audit Suppression Test")

    base_snapshot = take_snapshot(job, home=tmp_path / "home")
    watcher = CommitWatcher(
        job=job,
        baseline_snapshot=base_snapshot,
        base_state_dir=state_dir,
        home=tmp_path / "home",
    )

    # Add uncommitted dirty work
    (wt_path / "uncommitted.py").write_text("secret_work = 1\n", encoding="utf-8")
    initial_log = run_git(["rev-parse", branch], repo, env=safe_git_env, check=True).stdout.strip()

    # Tamper with worktree .git pointer file
    (wt_path / ".git").write_text("gitdir: /escaped\n", encoding="utf-8")

    # Attempt safety-net commit
    res = watcher.final_commit("done")
    assert res is None

    # Verify git branch ref is strictly unchanged in repo
    post_log = run_git(["rev-parse", branch], repo, env=safe_git_env, check=True).stdout.strip()
    assert initial_log == post_log


def test_b10_eight_worktrees_concurrent_commits(tmp_path: Path, safe_git_env):
    """B10: 8 worktrees committing concurrently without index.lock leaks or corruption."""
    repo = make_repo(tmp_path / "repo", env=safe_git_env)

    worktrees: list[Path] = []
    for i in range(8):
        wt_dir = tmp_path / f"wt_{i}"
        run_git(
            ["worktree", "add", "-b", f"branch_{i}", str(wt_dir)],
            repo,
            env=safe_git_env,
            check=True,
        )
        worktrees.append(wt_dir)

    def do_worktree_commit(wt_dir: Path, idx: int) -> str:
        f = wt_dir / f"file_{idx}.txt"
        f.write_text(f"content from worker {idx}\n", encoding="utf-8")
        stage_changes(wt_dir)
        m = wt_dir / f"msg_{idx}.txt"
        m.write_text(f"Commit from worker {idx}\n", encoding="utf-8")
        return commit_with_retry(
            wt_dir,
            m,
            max_attempts=30,
            backoff_seconds=0.05,
            sleep_func=time.sleep,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(do_worktree_commit, wt, i) for i, wt in enumerate(worktrees)]
        shas = [f.result() for f in futures]

    assert len(shas) == 8
    assert all(sha for sha in shas)

    # Verify no index.lock exists in repo or any worktree
    dot_git = repo / ".git"
    assert not (dot_git / "index.lock").exists()
    for wt in worktrees:
        assert not (wt / ".git" / "index.lock").exists()

    # Full git fsck must pass cleanly
    fsck_res = run_git(["fsck", "--full"], repo, env=safe_git_env)
    assert fsck_res.returncode == 0, f"git fsck failed: {fsck_res.stderr}"


def test_b11_stale_index_lock_reclamation(tmp_path: Path, safe_git_env):
    """B11: Stale index.lock (>5 min, dead proc) removed; young lock (<5 min) not removed."""
    repo = make_repo(tmp_path / "repo", env=safe_git_env)
    job_dir = tmp_path / "job_b11"
    job_dir.mkdir(parents=True)

    wt_path, _, _ = create_worktree(repo, job_dir=job_dir, title="Lock Test")

    # Locate linked worktree index.lock target path
    gitdir_line = (wt_path / ".git").read_text(encoding="utf-8").strip()
    gitdir = Path(gitdir_line.split("gitdir:", 1)[1].strip())
    lock_file = gitdir / "index.lock"

    # Part 1: Stale lock (> 300s old with no active process) -> removed, commit succeeds
    (wt_path / "file1.txt").write_text("test1\n", encoding="utf-8")
    stage_changes(wt_path)
    lock_file.write_text("stale lock\n", encoding="utf-8")
    os.utime(lock_file, (1000.0, 1000.0))

    msg1 = wt_path / "msg1.txt"
    msg1.write_text("Stale lock recovery commit\n", encoding="utf-8")

    sha = commit_with_retry(
        wt_path,
        msg_file=msg1,
        max_attempts=5,
        backoff_seconds=0.01,
        sleep_func=lambda _: None,
        is_process_alive_func=lambda: False,
        now_func=lambda: 1500.0,  # 500s > 300s
    )
    assert sha is not None
    assert not lock_file.exists()

    # Part 2: Young lock (< 300s old) -> NOT removed, commit fails with GitError
    (wt_path / "file2.txt").write_text("test2\n", encoding="utf-8")
    stage_changes(wt_path)
    lock_file.write_text("active young lock\n", encoding="utf-8")
    os.utime(lock_file, (1000.0, 1000.0))

    msg2 = wt_path / "msg2.txt"
    msg2.write_text("Young lock commit\n", encoding="utf-8")

    with pytest.raises(GitError) as exc_info:
        commit_with_retry(
            wt_path,
            msg_file=msg2,
            max_attempts=3,
            backoff_seconds=0.01,
            sleep_func=lambda _: None,
            is_process_alive_func=lambda: False,
            now_func=lambda: 1100.0,  # 100s < 300s
        )

    assert "index.lock" in exc_info.value.stderr or "Unable to create" in exc_info.value.stderr
    assert lock_file.exists(), "Young lock should not have been deleted"
    lock_file.unlink(missing_ok=True)


def test_b12_preflight_advanced_checks(tmp_path: Path, safe_git_env):
    """B12: Preflight rejects symlinks outside HOME, state dir paths, submodule files."""
    fake_home = Path(safe_git_env["HOME"])
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    # 1. Symlink pointing outside HOME rejected
    outside_dir = tmp_path / "outside_root" / "repo"
    outside_repo = make_repo(outside_dir, env=safe_git_env)
    symlink_repo = fake_home / "symlink_repo"
    os.symlink(outside_repo, symlink_repo)

    with pytest.raises(PreflightError) as exc1:
        preflight(str(symlink_repo), home=fake_home, base_state_dir=state_dir)
    assert exc1.value.code == "path_outside_home"

    # 2. Path inside state dir rejected
    state_repo = make_repo(state_dir / "repo_in_state", env=safe_git_env)
    with pytest.raises(PreflightError) as exc2:
        preflight(str(state_repo), home=fake_home, base_state_dir=state_dir)
    assert exc2.value.code == "state_dir_prohibited"

    # 3. Repo with submodule .git FILE rejected
    submodule_repo = fake_home / "submodule_repo"
    submodule_repo.mkdir()
    (submodule_repo / ".git").write_text("gitdir: ../.git/modules/sub\n", encoding="utf-8")
    with pytest.raises(PreflightError) as exc3:
        preflight(str(submodule_repo), home=fake_home, base_state_dir=state_dir)
    assert exc3.value.code == "linked_worktree_prohibited"

    # 4. require_clean_source=False + dirty tree => source_dirty=True without raising
    home_repo = make_repo(fake_home / "normal_repo", env=safe_git_env)
    (home_repo / "dirty_file.txt").write_text("dirty\n", encoding="utf-8")

    cfg = Config(require_clean_source=False)
    resolved, source_dirty, base_sha = preflight(
        str(home_repo),
        config=cfg,
        home=fake_home,
        base_state_dir=state_dir,
    )
    assert resolved == home_repo.resolve()
    assert source_dirty is True
    assert base_sha
