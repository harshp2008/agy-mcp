#!/usr/bin/env python3
"""Test submodule internals mutation against git oracle."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "~/agy-review/gemini2")
from agy_mcp.cleanup_jobs import _check_worktree_status, run_cleanup

from harness import *
SCRATCH = Path("~/agy-review/gemini2/scratch_submodule_internals")
safe_rmtree(SCRATCH)
SCRATCH.mkdir(parents=True, exist_ok=True)
env = get_env(SCRATCH)
env["PYTHONPATH"] = str(REPO_SRC)

# Create submodule
sub_repo = SCRATCH / "sub_repo"
subprocess.run(["git", "init", str(sub_repo)], check=True, capture_output=True, env=env)
subprocess.run(["git", "commit", "--allow-empty", "-m", "sub_init"], cwd=str(sub_repo), check=True, capture_output=True, env=env)

# Create main repo
main_repo = SCRATCH / "main_repo"
subprocess.run(["git", "init", str(main_repo)], check=True, capture_output=True, env=env)
subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(main_repo), check=True, capture_output=True, env=env)
subprocess.run(["git", "-c", "protocol.file.allow=always", "submodule", "add", str(sub_repo), "sub"], cwd=str(main_repo), check=True, capture_output=True, env=env)
subprocess.run(["git", "commit", "-m", "add sub"], cwd=str(main_repo), check=True, capture_output=True, env=env)

sub_in_main = main_repo / "sub"

# Create store and job for submodule
store = SCRATCH / "store"
store_jobs = store / "jobs"
store_jobs.mkdir(parents=True, exist_ok=True)

job_id = "00000000-0000-0000-0000-000000000001"
job_dir = store_jobs / job_id
job_dir.mkdir(parents=True, exist_ok=True)

job_data = {
    "job_id": job_id,
    "title": "submodule job",
    "state": "done",
    "repo_path": str(sub_in_main),
    "created_at": "2020-01-01T00:00:00Z",
    "updated_at": "2020-01-01T00:00:00Z",
}
(job_dir / "job.json").write_text(json.dumps(job_data, indent=2), encoding="utf-8")

# In main_repo/.git/modules/sub:
modules_sub = main_repo / ".git" / "modules" / "sub"
print("modules_sub exists:", modules_sub.exists())

# Mutation: delete objects from modules_sub
shutil.rmtree(modules_sub / "objects")

# What does git say?
res_git = subprocess.run(["git", "-C", str(sub_in_main), "worktree", "list"], capture_output=True, text=True, env=env)
git_works = res_git.returncode == 0
print(f"git -C sub: returncode={res_git.returncode}, stderr={res_git.stderr.strip()!r}")

# What does cleaner say?
is_reg, reason = _check_worktree_status(job_dir, job_data)
print(f"cleaner _check_worktree_status: is_registered={is_reg}, reason={reason}")

res_cleaner = run_cleanup(store, project=str(sub_in_main), dry_run=True, older_than_days=0.0)
cands = [c["job_id"] for c in res_cleaner.get("candidates", [])]
print(f"cleaner candidates: {cands}")
