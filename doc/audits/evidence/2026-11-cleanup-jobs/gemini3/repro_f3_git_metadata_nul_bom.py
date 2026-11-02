#!/usr/bin/env python3
"""Confirmed Finding Reproduction: Git repository metadata text files (HEAD and config)
corrupted with NUL bytes or UTF-8 BOM cause FAIL-OPEN deletion.

Oracle: Real git.
FAIL-OPEN definition: Cleaner says deletable while git still lists the worktree,
OR git cannot read the repo.

When a repository's HEAD or config file contains NUL bytes or UTF-8 BOM:
1. Real git fails to read the repository:
   - git status / git worktree list return exit code 128:
     "fatal: not a git repository (or any of the parent directories): .git" (for HEAD)
     "fatal: bad config line 1 in file .../config" (for config)
2. However, cleanup_jobs.py lines 145-210 validates HEAD and config using raw.strip()
   and read_bytes() without checking for NUL bytes, BOM, or UTF-8 validity.
3. _validate_full_git_dir returns (True, None) instead of failing closed.
4. _check_worktree_status finds no registered worktree (for unlinked/pruned worktrees)
   and falls through to (False, None), declaring the job DELETABLE.
5. Under --yes, the job record is deleted from the store while git cannot read the repo.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path("~/Documents/GITHUB PROJECTS/agy-mcp")
PYTHON_EXE = REPO_ROOT / ".venv" / "bin" / "python"
SCRATCH_ROOT = Path("~/agy-review/gemini3/scratch_findings_repro")


def test_variant(name: str, file_rel: str, corruption_bytes: bytes, is_bare: bool = False):
    print(f"\n=======================================================")
    print(f"Testing Variant: {name}")
    print(f"=======================================================")

    scratch = SCRATCH_ROOT / name
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)

    env = os.environ.copy()
    env["HOME"] = str(scratch)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_AUTHOR_NAME"] = "Test Author"
    env["GIT_AUTHOR_EMAIL"] = "author@example.com"
    env["GIT_COMMITTER_NAME"] = "Test Committer"
    env["GIT_COMMITTER_EMAIL"] = "committer@example.com"

    # 1. Setup repo
    repo = scratch / ("bare.git" if is_bare else "repo")
    if is_bare:
        subprocess.run(["git", "init", "--bare", "-q", str(repo)], env=env, check=True)
        tree = subprocess.run(["git", "--git-dir", str(repo), "write-tree"], env=env, capture_output=True, text=True, check=True).stdout.strip()
        commit = subprocess.run(["git", "--git-dir", str(repo), "commit-tree", tree, "-m", "init"], env=env, capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "--git-dir", str(repo), "update-ref", "refs/heads/main", commit], env=env, check=True)
        subprocess.run(["git", "--git-dir", str(repo), "symbolic-ref", "HEAD", "refs/heads/main"], env=env, check=True)
    else:
        subprocess.run(["git", "init", "-q", str(repo)], env=env, check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init", "-q"], env=env, check=True)

    # 2. Setup job record in store
    store = scratch / "store"
    store_jobs = store / "jobs"
    store_jobs.mkdir(parents=True)
    jid = str(uuid.uuid4())
    jdir = store_jobs / jid
    jdir.mkdir()

    now = datetime.now(timezone.utc).isoformat()
    (jdir / "job.json").write_text(json.dumps({
        "job_id": jid,
        "repo_path": str(repo),
        "state": "done",
        "created_at": now,
        "updated_at": now,
    }), encoding="utf-8")

    # 3. Apply corruption
    target_file = repo / file_rel
    target_file.write_bytes(corruption_bytes)

    # 4. Check git ground truth
    git_status = subprocess.run(["git", "-C", str(repo), "status"], env=env, capture_output=True, text=True)
    git_wt = subprocess.run(["git", "-C", str(repo), "worktree", "list"], env=env, capture_output=True, text=True)

    print(f"Git status returncode: {git_status.returncode}")
    print(f"Git status stderr: {git_status.stderr.strip()}")
    print(f"Git worktree list returncode: {git_wt.returncode}")
    print(f"Git worktree list stderr: {git_wt.stderr.strip()}")

    # 5. Run cleaner with --yes
    cmd = [
        str(PYTHON_EXE), "-m", "agy_mcp.cleanup_jobs",
        "--store", str(store),
        "--project", str(repo),
        "--older-than-days", "0",
        "--yes",
        "--json"
    ]
    cleaner_proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    print(f"Cleaner returncode: {cleaner_proc.returncode}")
    print(f"Cleaner stdout: {cleaner_proc.stdout.strip()[:200]}")

    report = json.loads(cleaner_proc.stdout)
    is_deleted = jid in report.get("deleted_ids", [])
    dir_exists = jdir.exists()

    print(f"Job deleted by cleaner: {is_deleted}")
    print(f"Job directory exists on disk: {dir_exists}")

    if git_status.returncode != 0 and is_deleted and not dir_exists:
        print(f"RESULT: FAIL-OPEN CONFIRMED for {name}")
        return True
    else:
        print(f"RESULT: NOT CONFIRMED for {name}")
        return False


def main():
    variants = [
        ("Normal_repo_config_NUL", ".git/config", b"\x00\x00[core]\n\trepositoryformatversion = 0\n", False),
        ("Normal_repo_HEAD_NUL", ".git/HEAD", b"\x00\x00ref: refs/heads/main\n", False),
        ("Normal_repo_HEAD_BOM", ".git/HEAD", b"\xef\xbb\xbfref: refs/heads/main\n", False),
        ("Bare_repo_config_NUL", "config", b"\x00\x00[core]\n\trepositoryformatversion = 0\n", True),
        ("Bare_repo_HEAD_NUL", "HEAD", b"\x00\x00ref: refs/heads/main\n", True),
        ("Bare_repo_HEAD_BOM", "HEAD", b"\xef\xbb\xbfref: refs/heads/main\n", True),
    ]

    results = []
    for name, frel, bdata, bare in variants:
        ok = test_variant(name, frel, bdata, is_bare=bare)
        results.append((name, ok))

    print("\n\n=== SUMMARY OF REPRODUCTIONS ===")
    all_ok = True
    for name, ok in results:
        status = "CONFIRMED FAIL-OPEN" if ok else "FAILED"
        print(f"{name}: {status}")
        if not ok: all_ok = False

    if all_ok:
        print("\nAll 6 variants confirmed FAIL-OPEN!")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
