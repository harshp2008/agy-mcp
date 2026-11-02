"""Integration tests for gitops.py using real temporary git repositories."""

import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agy_mcp.gitops import (
    GitError,
    checkpoint_commit,
    commit_with_retry,
    diff_git_config_snapshots,
    get_ahead_behind,
    get_commits_list,
    get_diff_stat,
    get_merge_base,
    has_base_moved,
    is_sensitive_path,
    is_worktree_dirty,
    run_git,
    safety_net_commit,
    stage_changes,
    take_git_config_snapshot,
)
from tests.helpers.make_repo import make_repo


def test_is_sensitive_path():
    """Verify sensitive blacklist rules, prefixes, extensions, and allowed exceptions."""
    # Exceptions that must NOT be excluded as secrets
    assert not is_sensitive_path(".gitignore")
    assert not is_sensitive_path(".gitattributes")
    assert not is_sensitive_path(".gitmodules")
    assert not is_sensitive_path(".github/workflows/ci.yml")
    assert not is_sensitive_path(".env.example")
    assert not is_sensitive_path("sub/.env.example")
    assert not is_sensitive_path(".env.sample")
    assert not is_sensitive_path("secret_scan.py")
    assert not is_sensitive_path("docs/secrets.md")
    assert not is_sensitive_path("secretary.py")

    # .env variants (including case and nested paths)
    assert is_sensitive_path(".env")
    assert is_sensitive_path(".env.local")
    assert is_sensitive_path(".env.production")
    assert is_sensitive_path("a/b/.env")
    assert is_sensitive_path("A/B/.ENV")

    # Keys, certs, pems (including uppercase extensions)
    assert is_sensitive_path("x.pem")
    assert is_sensitive_path("x.key")
    assert is_sensitive_path("cert.PEM")
    assert is_sensitive_path("foo.KEY")
    assert is_sensitive_path("keystore.p12")
    assert is_sensitive_path("creds.pfx")
    assert is_sensitive_path("truststore.jks")
    assert is_sensitive_path("putty.ppk")

    # SSH / GPG keys (including uppercase and nested)
    assert is_sensitive_path("id_rsa")
    assert is_sensitive_path("ID_RSA")
    assert is_sensitive_path("id_rsa_backup")
    assert is_sensitive_path("id_ed25519")
    assert is_sensitive_path(".ssh/config")
    assert is_sensitive_path("a/b/.ssh/id_rsa")
    assert is_sensitive_path(".gnupg/x")
    assert is_sensitive_path("a/b/.gnupg/secring.gpg")

    # Exact credentials
    assert is_sensitive_path(".netrc")
    assert is_sensitive_path(".npmrc")
    assert is_sensitive_path(".pypirc")
    assert is_sensitive_path(".docker/config.json")
    assert is_sensitive_path("sub/.docker/config.json")

    # .agy directory
    assert is_sensitive_path(".agy/PLAN.md")
    assert is_sensitive_path(".agy/COMMIT_MSG")
    assert is_sensitive_path("subdir/.agy/foo")

    # Normal files
    assert not is_sensitive_path("main.py")
    assert not is_sensitive_path("README.md")
    assert not is_sensitive_path("src/auth/jwt.py")


def test_run_git_basic_and_check(tmp_path: Path, safe_git_env):
    """run_git executes commands in repo, captures output, and raises on error if check=True."""
    repo = make_repo(tmp_path / "repo", env=safe_git_env)
    res = run_git(["status"], repo, env=safe_git_env)
    assert res.returncode == 0
    assert "On branch main" in res.stdout

    # Failure with check=False
    fail_res = run_git(["log", "nonexistent_branch"], repo, env=safe_git_env, check=False)
    assert fail_res.returncode != 0

    # Failure with check=True raises GitError
    with pytest.raises(GitError) as exc_info:
        run_git(["log", "nonexistent_branch"], repo, env=safe_git_env, check=True)
    assert exc_info.value.returncode != 0


def test_stage_changes_sensitive_and_exclusions(tmp_path: Path, safe_git_env):
    """Staging excludes .agy, unstages untracked secrets, and detects partial commits."""
    repo = make_repo(tmp_path / "repo", env=safe_git_env)

    # 1. Pre-track a secret file in repository history
    tracked_secret = repo / "tracked_secret.key"
    tracked_secret.write_text("initial key content", encoding="utf-8")
    run_git(["add", "tracked_secret.key"], repo, env=safe_git_env, check=True)
    run_git(["commit", "-m", "Add tracked key"], repo, env=safe_git_env, check=True)

    # 2. Add normal files, allowed exception, untracked secret, and modify tracked secret
    (repo / "feature.py").write_text("print('hello')", encoding="utf-8")
    (repo / ".env.example").write_text("FOO=bar", encoding="utf-8")
    (repo / ".env").write_text("SECRET=12345", encoding="utf-8")
    (repo / "id_rsa").write_text("ssh-rsa ...", encoding="utf-8")
    (repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (repo / ".agy").mkdir()
    (repo / ".agy" / "PLAN.md").write_text("- [ ] Task", encoding="utf-8")

    # Modify the tracked secret
    tracked_secret.write_text("mutated key content", encoding="utf-8")

    # Stage changes
    staging = stage_changes(repo)

    # feature.py and .env.example must be staged
    assert "feature.py" in staging.staged_files
    assert ".env.example" in staging.staged_files

    # .agy/PLAN.md must NOT be in staged files
    assert ".agy/PLAN.md" not in staging.staged_files

    # .env, id_rsa, tracked_secret.key, and policy file .gitignore must be skipped
    assert ".env" in staging.skipped_files
    assert "id_rsa" in staging.skipped_files
    assert "tracked_secret.key" in staging.skipped_files
    assert ".gitignore" in staging.skipped_files

    # Policy files reported in separate field
    assert ".gitignore" in staging.policy_files

    # Because a TRACKED secret and policy file were modified/added, partial_commit must be True
    assert staging.partial_commit is True

    # Verify git status index: .env and id_rsa must NOT be staged in index
    status_proc = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain=v1"],
        capture_output=True,
        text=True,
        env=safe_git_env,
    )
    status_lines = status_proc.stdout.splitlines()
    for line in status_lines:
        if ".env" in line and not line.endswith(".example"):
            assert line.startswith("??")  # untracked, not staged
        if "id_rsa" in line:
            assert line.startswith("??")
        if "tracked_secret.key" in line:
            # Unstaged back to working tree
            assert line.startswith(" M")


def test_stage_changes_unicode_and_spaces(tmp_path: Path, safe_git_env):
    """Staging handles files with spaces, newlines, and unicode characters correctly."""
    repo = make_repo(tmp_path / "repo", env=safe_git_env)
    special_file = repo / "file with spaces and 🚀 unicode.txt"
    special_file.write_text("unicode content", encoding="utf-8")

    staging = stage_changes(repo)
    assert "file with spaces and 🚀 unicode.txt" in staging.staged_files
    assert not staging.partial_commit


def test_commit_with_retry_and_stale_lock(tmp_path: Path, safe_git_env):
    """commit_with_retry handles index.lock retries and removes stale locks (>5 min old)."""
    repo = make_repo(tmp_path / "repo", env=safe_git_env)
    (repo / "change.txt").write_text("change", encoding="utf-8")
    run_git(["add", "change.txt"], repo, env=safe_git_env, check=True)

    msg_file = repo / "msg.txt"
    msg_file.write_text("Test commit message\n", encoding="utf-8")

    # Create a stale index.lock file (> 300s old)
    lock_file = repo / ".git" / "index.lock"
    lock_file.write_text("locked", encoding="utf-8")
    stale_mtime = time.time() - 400
    os.utime(str(lock_file), (stale_mtime, stale_mtime))

    sleep_calls: list[float] = []

    commit_sha = commit_with_retry(
        repo,
        msg_file,
        max_attempts=5,
        backoff_seconds=0.01,
        sleep_func=lambda s: sleep_calls.append(s),
        is_process_alive_func=lambda: False,
    )

    assert len(commit_sha) == 40
    assert not lock_file.exists()


def test_concurrent_commits_to_two_worktrees(tmp_path: Path, safe_git_env):
    """Stress index.lock concurrency from two separate worktrees committing to the same repo."""
    repo = make_repo(tmp_path / "repo", env=safe_git_env)

    # Create worktrees wt1 and wt2
    wt1 = tmp_path / "wt1"
    wt2 = tmp_path / "wt2"
    run_git(["worktree", "add", "-b", "branch1", str(wt1)], repo, env=safe_git_env, check=True)
    run_git(["worktree", "add", "-b", "branch2", str(wt2)], repo, env=safe_git_env, check=True)

    def committer(wt_dir: Path, base_idx: int):
        for i in range(4):
            idx = base_idx + i
            f = wt_dir / f"file_{idx}.txt"
            f.write_text(f"content {idx}", encoding="utf-8")
            stage_changes(wt_dir)
            m = wt_dir / f"msg_{idx}.txt"
            m.write_text(f"Commit {idx}\n", encoding="utf-8")
            commit_with_retry(
                wt_dir,
                m,
                max_attempts=20,
                backoff_seconds=0.05,
                sleep_func=time.sleep,
            )

    # Run concurrent commits to two separate worktrees of the same repo
    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(committer, wt1, 0)
        f2 = executor.submit(committer, wt2, 100)
        f1.result()
        f2.result()

    # Verify both worktrees committed successfully
    res1 = run_git(["rev-parse", "HEAD"], wt1, env=safe_git_env, check=True)
    res2 = run_git(["rev-parse", "HEAD"], wt2, env=safe_git_env, check=True)
    assert res1.stdout.strip()
    assert res2.stdout.strip()


def test_checkpoint_and_safety_net_commits(tmp_path: Path, safe_git_env):
    """checkpoint_commit commits dirty changes; safety_net_commit is blocked on audit failure."""
    repo = make_repo(tmp_path / "repo", env=safe_git_env)
    assert not is_worktree_dirty(repo)

    # Checkpoint when clean -> returns None
    assert checkpoint_commit(repo) is None

    # Dirty worktree
    (repo / "dirty.txt").write_text("dirty content", encoding="utf-8")
    assert is_worktree_dirty(repo)

    sha = checkpoint_commit(repo)
    assert sha is not None
    assert not is_worktree_dirty(repo)

    # Safety net commit when clean -> None
    assert safety_net_commit(repo, "Title", audit_passed=True) is None

    # Dirty again
    (repo / "dirty2.txt").write_text("dirty 2", encoding="utf-8")

    # Safety net refused if audit failed
    assert safety_net_commit(repo, "Title", audit_passed=False) is None
    assert is_worktree_dirty(repo)

    # Safety net succeeds when audit passed
    sn_sha = safety_net_commit(repo, "Title", audit_passed=True)
    assert sn_sha is not None
    assert not is_worktree_dirty(repo)


def test_review_helpers(tmp_path: Path, safe_git_env):
    """Review helpers accurately calculate merge-base, ahead/behind, diff-stat, and commits."""
    repo = make_repo(tmp_path / "repo", env=safe_git_env)
    base_commit = run_git(["rev-parse", "HEAD"], repo, env=safe_git_env, check=True).stdout.strip()

    # Add 2 commits
    (repo / "f1.txt").write_text("line 1\n", encoding="utf-8")
    run_git(["add", "f1.txt"], repo, env=safe_git_env, check=True)
    msg1 = repo / "msg1.txt"
    msg1.write_text("Add f1\n", encoding="utf-8")
    commit_with_retry(repo, msg1)

    (repo / "f2.txt").write_text("line 2\n", encoding="utf-8")
    run_git(["add", "f2.txt"], repo, env=safe_git_env, check=True)
    msg2 = repo / "msg2.txt"
    msg2.write_text("Add f2\n", encoding="utf-8")
    head_commit = commit_with_retry(repo, msg2)

    # Merge base
    mb = get_merge_base(repo, base_commit, head_commit)
    assert mb == base_commit

    # Ahead / behind
    ahead, behind = get_ahead_behind(repo, base_commit, head_commit)
    assert ahead == 2
    assert behind == 0

    # Base moved check
    assert has_base_moved(repo, base_commit) is True
    assert has_base_moved(repo, head_commit) is False

    # Diff stat
    stat = get_diff_stat(repo, base_commit, head_commit)
    assert "f1.txt" in stat
    assert "f2.txt" in stat

    # Commits list
    commits = get_commits_list(repo, base_commit, head_commit)
    assert len(commits) == 2
    assert commits[0]["subject"] == "Add f2"
    assert commits[1]["subject"] == "Add f1"

    # Git config snapshot and diffing
    snap1 = take_git_config_snapshot(repo)
    run_git(["config", "--local", "user.name", "Changed User"], repo, env=safe_git_env)
    snap2 = take_git_config_snapshot(repo)
    diff = diff_git_config_snapshots(snap1, snap2)
    assert "user.name" in diff


def test_stage_changes_uses_agy_exclude_pathspec(tmp_path: Path, safe_git_env, monkeypatch):
    """Verify that stage_changes executes git add with :(exclude,top).agy pathspec per F04."""
    import agy_mcp.gitops as gitops_mod

    repo = make_repo(tmp_path / "repo", env=safe_git_env)
    add_calls: list[list[str]] = []
    orig_run_git = gitops_mod.run_git

    def spy_run_git(args, cwd, **kwargs):
        if len(args) > 0 and args[0] == "add":
            add_calls.append(list(args))
        return orig_run_git(args, cwd, **kwargs)

    monkeypatch.setattr(gitops_mod, "run_git", spy_run_git)
    stage_changes(repo)

    assert any(":(exclude,top).agy" in call for call in add_calls), (
        "git add was executed without ':(exclude,top).agy' pathspec"
    )


def test_junk_layer_isolation_pathspec_and_python_filter(tmp_path: Path, safe_git_env):
    """Test each layer (pathspec, Python filter) alone per SPEC 11 & Part A5(b)."""
    repo = make_repo(tmp_path / "repo_layers", env=safe_git_env)

    # Create real file and various junk artifacts
    (repo / "code.py").write_text("print('hello')\n")
    (repo / "__pycache__").mkdir(exist_ok=True)
    (repo / "__pycache__" / "code.cpython-312.pyc").write_bytes(b"\x00\x01\x02")
    (repo / "test.pyo").write_bytes(b"\x00\x01")
    (repo / ".pytest_cache").mkdir(exist_ok=True)
    (repo / ".pytest_cache" / "v").write_text("cache")
    (repo / ".ruff_cache").mkdir(exist_ok=True)
    (repo / ".ruff_cache" / "cache").write_text("cache")
    (repo / ".mypy_cache").mkdir(exist_ok=True)
    (repo / ".mypy_cache" / "cache").write_text("cache")
    (repo / ".tox").mkdir(exist_ok=True)
    (repo / ".tox" / "tox.txt").write_text("tox")
    (repo / "pkg.egg-info").mkdir(exist_ok=True)
    (repo / "pkg.egg-info" / "PKG-INFO").write_text("info")

    # Layer 1: pathspecs alone (python filter disabled)
    run_git(["reset", "--hard", "HEAD"], repo, env=safe_git_env)
    (repo / "code.py").write_text("print('hello 1')\n")
    (repo / "__pycache__" / "code.cpython-312.pyc").write_bytes(b"\x00\x01\x02")
    staging_l1 = stage_changes(repo, enable_pathspecs=True, enable_python_filter=False)
    assert "code.py" in staging_l1.staged_files
    for f in staging_l1.staged_files:
        assert "__pycache__" not in f
        assert not f.endswith(".pyc")
        assert ".pytest_cache" not in f

    # Layer 2: Python filter alone (pathspecs disabled)
    run_git(["reset", "--hard", "HEAD"], repo, env=safe_git_env)
    (repo / "code.py").write_text("print('hello 2')\n")
    (repo / "standalone.pyc").write_bytes(b"\x00\x01\x02")
    (repo / "__pycache__" / "code.cpython-312.pyc").write_bytes(b"\x00\x01\x02")
    (repo / "test.pyo").write_bytes(b"\x00\x01")
    staging_l2 = stage_changes(repo, enable_pathspecs=False, enable_python_filter=True)
    assert "code.py" in staging_l2.staged_files
    for f in staging_l2.staged_files:
        assert "__pycache__" not in f
        assert not f.endswith(".pyc")
        assert not f.endswith(".pyo")
    # Python filter recorded skipped files with build_artifact reason
    artifact_skips = [
        sf for sf in staging_l2.skipped_files
        if isinstance(sf, dict) and sf.get("reason") == "build_artifact"
    ]
    assert len(artifact_skips) > 0


def test_real_changes_plus_junk_only_real_committed(tmp_path: Path, safe_git_env):
    """Real changes plus junk -> only real changes committed per Part A5(c)."""
    repo = make_repo(tmp_path / "repo_real_plus_junk", env=safe_git_env)
    (repo / "real_feature.py").write_text("def feature(): pass\n")
    (repo / "__pycache__").mkdir(exist_ok=True)
    (repo / "__pycache__" / "junk.cpython-312.pyc").write_bytes(b"junk")
    (repo / ".pytest_cache").mkdir(exist_ok=True)
    (repo / ".pytest_cache" / "cache").write_text("cache")

    job_dir = tmp_path / "job_dir"
    job_dir.mkdir(parents=True, exist_ok=True)

    head_before = run_git(["rev-parse", "HEAD"], repo).stdout.strip()
    new_sha = safety_net_commit(repo, title="Feature Addition", audit_passed=True, job_dir=job_dir)
    assert new_sha is not None
    assert new_sha != head_before

    committed_files = run_git(
        ["diff", "--name-only", f"{head_before}..{new_sha}"], repo
    ).stdout.splitlines()
    assert "real_feature.py" in committed_files
    for f in committed_files:
        assert "__pycache__" not in f
        assert not f.endswith(".pyc")
        assert ".pytest_cache" not in f


def test_only_junk_no_commit_and_audit_event_written(tmp_path: Path, safe_git_env):
    """Only junk exists -> no commit made and audit event written per Part A5(d)."""
    import json
    repo = make_repo(tmp_path / "repo_only_junk", env=safe_git_env)
    (repo / "__pycache__").mkdir(exist_ok=True)
    (repo / "__pycache__" / "temp.cpython-312.pyc").write_bytes(b"junk")
    (repo / ".pytest_cache").mkdir(exist_ok=True)
    (repo / ".pytest_cache" / "data").write_text("junk")

    job_dir = tmp_path / "job_dir_only_junk"
    job_dir.mkdir(parents=True, exist_ok=True)

    head_before = run_git(["rev-parse", "HEAD"], repo).stdout.strip()
    new_sha = safety_net_commit(repo, title="Junk Only", audit_passed=True, job_dir=job_dir)
    assert new_sha is None
    head_after = run_git(["rev-parse", "HEAD"], repo).stdout.strip()
    assert head_before == head_after

    audit_file = job_dir / "audit.jsonl"
    assert audit_file.is_file()
    lines = [
        json.loads(line)
        for line in audit_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    events = [entry["event"] for entry in lines]
    assert "safety_net_skipped_only_artifacts" in events


def test_junk_tracked_from_start_is_untouched(tmp_path: Path, safe_git_env):
    """Junk present from start is untouched by filter if already tracked per Part A5(e).

    Chosen behaviour: Pre-existing tracked junk is never purged or rewritten from git history
    (we never run git rm or rewrite history), and is not reported in skipped_files as a new
    untracked build artifact.
    """
    repo = make_repo(tmp_path / "repo_tracked_junk", env=safe_git_env)
    pyc_file = repo / "initial.pyc"
    pyc_file.write_bytes(b"tracked_pyc_v1")
    run_git(["add", "-f", "initial.pyc"], repo, env=safe_git_env)
    run_git(["commit", "-m", "Track initial.pyc in history"], repo, env=safe_git_env)

    # Modify the tracked .pyc
    pyc_file.write_bytes(b"tracked_pyc_v2")

    staging = stage_changes(repo)
    # The tracked .pyc is never removed from git tracking
    ls_files = run_git(["ls-files", "initial.pyc"], repo, env=safe_git_env).stdout.strip()
    assert ls_files == "initial.pyc"
    # It is NOT reported as an untracked skipped build artifact
    assert "initial.pyc" not in staging.skipped_files


def test_safety_net_commit_uses_recent_agy_commit_msg(tmp_path: Path, safe_git_env):
    """Safety-net commit uses first line of most recent agy COMMIT_MSG if available per Part A4."""
    repo = make_repo(tmp_path / "repo_commit_msg", env=safe_git_env)
    (repo / "change.py").write_text("print('updated')\n")
    agy_dir = repo / ".agy"
    agy_dir.mkdir(parents=True, exist_ok=True)
    (agy_dir / "COMMIT_MSG.seen.1").write_text("fix(core): earlier fix\n\nBody.\n")
    (agy_dir / "COMMIT_MSG.seen.2").write_text(
        "fix(core): most recent fix subject\n\nDescriptive body.\n"
    )

    new_sha = safety_net_commit(repo, title="Fallback Title", audit_passed=True)
    assert new_sha is not None
    subject = run_git(["log", "-1", "--format=%s", new_sha], repo).stdout.strip()
    assert subject == "fix(core): most recent fix subject"

