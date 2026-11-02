#!/usr/bin/env python3
"""Minimal reproduction script for FAIL-OPEN: .git/config corrupted with NUL bytes.

When a repository's .git/config contains NUL bytes, git fails to read the repository
(git status and git worktree list return exit code 128: "fatal: bad config line 1 in file .git/config").
However, cleanup_jobs.py line 205 only checks config_path.is_file() and config_path.read_bytes(),
without validating file content or character encoding.
As a result, _validate_full_git_dir returns (True, None). When the job's worktree is not registered
(e.g. unlinked or pruned), _check_worktree_status falls through to (False, None), classifying the job
as a candidate (deletable). Under --yes, the job record is deleted from the store while git cannot read the repo.
"""

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
SCRATCH = Path("~/agy-review/gemini3/scratch_repro_config_nul")

if SCRATCH.exists():
    shutil.rmtree(SCRATCH)
SCRATCH.mkdir(parents=True)

# Point HOME to SCRATCH so store is inside HOME
env = os.environ.copy()
env["HOME"] = str(SCRATCH)
env["GIT_CONFIG_NOSYSTEM"] = "1"
env["GIT_CONFIG_GLOBAL"] = "/dev/null"
env["GIT_AUTHOR_NAME"] = "Test Author"
env["GIT_AUTHOR_EMAIL"] = "author@example.com"
env["GIT_COMMITTER_NAME"] = "Test Committer"
env["GIT_COMMITTER_EMAIL"] = "committer@example.com"

# 1. Create a git repo
repo = SCRATCH / "repo"
subprocess.run(["git", "init", "-q", str(repo)], env=env, check=True)
subprocess.run(["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init", "-q"], env=env, check=True)

# 2. Create store with an unlinked job record
store = SCRATCH / "store"
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

# 3. Corrupt repo/.git/config with NUL bytes
(repo / ".git" / "config").write_bytes(b"\x00\x00[core]\n\trepositoryformatversion = 0\n")

# 4. Check git ground truth
git_status = subprocess.run(["git", "-C", str(repo), "status"], env=env, capture_output=True, text=True)
git_wt = subprocess.run(["git", "-C", str(repo), "worktree", "list"], env=env, capture_output=True, text=True)

print(f"Git status returncode: {git_status.returncode}")
print(f"Git status stderr: {git_status.stderr.strip()}")
print(f"Git worktree list returncode: {git_wt.returncode}")
print(f"Git worktree list stderr: {git_wt.stderr.strip()}")

# 5. Run cleaner CLI with --yes
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
print(f"Cleaner stdout: {cleaner_proc.stdout.strip()}")

report = json.loads(cleaner_proc.stdout)
is_deleted = jid in report.get("deleted_ids", [])
dir_exists = jdir.exists()

print(f"Job deleted by cleaner: {is_deleted}")
print(f"Job directory exists on disk: {dir_exists}")

if git_status.returncode != 0 and is_deleted and not dir_exists:
    print("VERDICT: FAIL-OPEN CONFIRMED")
else:
    print("VERDICT: NOT CONFIRMED")
