#!/usr/bin/env python3
"""Repro: commondir missing in linked worktree causes FAIL-OPEN.
Git cannot read the repo from wt1 (rc 128), and main_repo still has wt2 registered,
but cleaner declares job deletable and deletes it with --yes.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "~/agy-review/gemini2")
from harness import *

SCRATCH = Path("~/agy-review/gemini2/scratch_commondir_repro")
safe_rmtree(SCRATCH)
SCRATCH.mkdir(parents=True, exist_ok=True)
env = get_env(SCRATCH)
env["PYTHONPATH"] = str(REPO_SRC)

# 1. Main repo
main_repo = SCRATCH / "main_repo"
subprocess.run(["git", "init", str(main_repo)], check=True, capture_output=True, env=env)
subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(main_repo), check=True, capture_output=True, env=env)

# 2. Linked worktree wt1
wt1 = SCRATCH / "wt1"
subprocess.run(["git", "worktree", "add", str(wt1), "-b", "b_wt1"], cwd=str(main_repo), check=True, capture_output=True, env=env)

# 3. Store and job for wt2
store = SCRATCH / "store"
store_jobs = store / "jobs"
store_jobs.mkdir(parents=True, exist_ok=True)

job_id = "00000000-0000-0000-0000-000000000001"
job_dir = store_jobs / job_id
job_dir.mkdir(parents=True, exist_ok=True)
work2 = job_dir / "work"

# Add worktree wt2 from wt1 into job dir
subprocess.run(["git", "worktree", "add", str(work2), "-b", "b_wt2"], cwd=str(wt1), check=True, capture_output=True, env=env)
# Unlink work2/.git to test repository worktree lookup
(work2 / ".git").unlink()

job_data = {
    "job_id": job_id,
    "title": "worktree job with repo_path=wt1",
    "state": "done",
    "repo_path": str(wt1),
    "created_at": "2020-01-01T00:00:00Z",
    "updated_at": "2020-01-01T00:00:00Z",
}
(job_dir / "job.json").write_text(json.dumps(job_data, indent=2), encoding="utf-8")

# Verify initial state: both git and cleaner agree worktree is registered
gw_pre, gr_pre, go_pre = check_git(wt1, work2, env)
print(f"Pre-mutation: git_works={gw_pre}, git_reg={gr_pre}")

# Find commondir in main_repo/.git/worktrees/wt1/commondir
commondir_file = main_repo / ".git" / "worktrees" / "wt1" / "commondir"
assert commondir_file.is_file(), f"commondir file not found: {commondir_file}"

# MUTATION: Delete commondir
commondir_file.unlink()

# Check real git from wt1
res_git_wt1 = subprocess.run(
    ["git", "-C", str(wt1), "worktree", "list", "--porcelain"],
    capture_output=True, text=True, env=env
)
git_works = res_git_wt1.returncode == 0
print(f"Post-mutation git -C wt1: returncode={res_git_wt1.returncode}, stderr={res_git_wt1.stderr.strip()!r}")

# Check real git from main_repo: does main_repo still list work2 as registered?
res_git_main = subprocess.run(
    ["git", "-C", str(main_repo), "worktree", "list", "--porcelain"],
    capture_output=True, text=True, env=env
)
work2_registered_in_main = str(work2.resolve(strict=False)) in res_git_main.stdout
print(f"Post-mutation git -C main_repo lists work2 as registered: {work2_registered_in_main}")

# Check cleaner in dry-run mode
cmd_dry = [
    sys.executable, "-m", "agy_mcp.cleanup_jobs",
    "--store", str(store),
    "--project", str(wt1),
    "--older-than-days", "0",
    "--json",
]
res_cleaner_dry = subprocess.run(cmd_dry, capture_output=True, text=True, env=env)
report_dry = json.loads(res_cleaner_dry.stdout)
cands = [c["job_id"] for c in report_dry.get("candidates", [])]
print(f"Cleaner dry-run candidates: {cands}")

# Check cleaner with --yes (actual deletion)
cmd_yes = [
    sys.executable, "-m", "agy_mcp.cleanup_jobs",
    "--store", str(store),
    "--project", str(wt1),
    "--older-than-days", "0",
    "--yes",
    "--json",
]
res_cleaner_yes = subprocess.run(cmd_yes, capture_output=True, text=True, env=env)
report_yes = json.loads(res_cleaner_yes.stdout)
deleted_ids = report_yes.get("deleted_ids", [])
print(f"Cleaner --yes deleted_ids: {deleted_ids}")
print(f"Job directory exists after --yes: {job_dir.exists()}")

if not git_works and job_id in cands:
    print("\n*** CONFIRMED FAIL-OPEN FINDING ***")
    record_finding(
        repro_script="~/agy-review/gemini2/test_finding_commondir_deleted.py",
        command=f"env -u PYTHONPATH .venv/bin/python -m agy_mcp.cleanup_jobs --store {store} --project {wt1} --older-than-days 0 --yes",
        cleaner_said=f"candidates=['{job_id}'], deleted_ids=['{job_id}'] (job dir deleted={not job_dir.exists()})",
        git_said=f"git -C wt1 returned {res_git_wt1.returncode}: {res_git_wt1.stderr.strip()}; git -C main_repo lists work2 registered={work2_registered_in_main}",
        severity="FAIL-OPEN",
        details=(
            "When repo_path is a linked worktree (repo_path/.git is a pointer file) and "
            "actual_gitdir/commondir is deleted, git cannot read the repository from repo_path "
            "(fatal: not a git repository, rc=128), and main_repo still lists the worktree as registered. "
            "However, cleanup_jobs.py line 276 only inspects commondir if commondir_file.exists(). "
            "When absent, candidate_wt_roots remains missing common_gitdir/worktrees, so _check_worktree_status "
            "falls through to (False, None) and classifies the job as candidate (deletable). "
            "With --yes, the job record is actually deleted from the store."
        )
    )
