#!/usr/bin/env python3
"""Test Area 9 & 10: Shared worktree paths and worktree equals repo_path."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "~/agy-review/gemini2")
from harness import *

SCRATCH = Path("~/agy-review/gemini2/scratch_area9")

def test_shared_worktree():
    safe_rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    env = get_env(SCRATCH)

    repo = SCRATCH / "repo"
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(repo), check=True, capture_output=True, env=env)

    store = SCRATCH / "store"
    store_jobs = store / "jobs"
    store_jobs.mkdir(parents=True, exist_ok=True)

    job1_id = "00000000-0000-0000-0000-000000000001"
    job1_dir = store_jobs / job1_id
    job1_dir.mkdir(parents=True, exist_ok=True)
    wt1 = job1_dir / "work"
    subprocess.run(["git", "worktree", "add", str(wt1), "-b", "wt1_b"], cwd=str(repo), check=True, capture_output=True, env=env)

    # Job 2 is running (kept), and points to job1's worktree!
    job2_id = "00000000-0000-0000-0000-000000000002"
    job2_dir = store_jobs / job2_id
    job2_dir.mkdir(parents=True, exist_ok=True)

    job1_data = {
        "job_id": job1_id,
        "title": "job1",
        "state": "done",
        "repo_path": str(repo),
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2020-01-01T00:00:00Z",
    }
    (job1_dir / "job.json").write_text(json.dumps(job1_data, indent=2), encoding="utf-8")

    job2_data = {
        "job_id": job2_id,
        "title": "job2 running",
        "state": "running",
        "repo_path": str(repo),
        "worktree_path": str(wt1),
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    (job2_dir / "job.json").write_text(json.dumps(job2_data, indent=2), encoding="utf-8")

    # Run cleaner on project
    res = run_cleanup(store, project=str(repo), dry_run=True, older_than_days=0.0)
    cands = [c["job_id"] for c in res.get("candidates", [])]
    print(f"Shared wt: candidates = {cands}")

    # Is job1 a candidate?
    # job1's work folder contains a registered worktree (wt1/.git exists), so job1 must be kept!
    # What if wt1/.git is unlinked?
    (wt1 / ".git").unlink()
    # Now check again: git still registers wt1 in repo/.git/worktrees/wt1_b!
    res2 = run_cleanup(store, project=str(repo), dry_run=True, older_than_days=0.0)
    cands2 = [c["job_id"] for c in res2.get("candidates", [])]
    print(f"Shared wt (wt1/.git unlinked): candidates = {cands2}")

    # What if we prune git worktree so git no longer lists wt1?
    subprocess.run(["git", "worktree", "prune"], cwd=str(repo), check=True, capture_output=True, env=env)
    # Now wt1 is NOT registered in git!
    # But job2 (running) references wt1 in its worktree_path!
    # wt1 path contains job1's UUID!
    # Edge protection should keep job1!
    res3 = run_cleanup(store, project=str(repo), dry_run=True, older_than_days=0.0)
    cands3 = [c["job_id"] for c in res3.get("candidates", [])]
    kept3 = {k["job_id"]: k["reasons"] for k in res3.get("kept", [])}
    print(f"Shared wt (pruned): candidates = {cands3}, job1 kept reasons = {kept3.get(job1_id)}")

    if job1_id in cands3:
        print("FAIL-OPEN: job1 is deletable while job2 (running) references its work folder!")
        record_finding(
            repro_script="test_area9_shared_wt.py",
            command="run_cleanup(...)",
            cleaner_said=f"candidates={cands3}",
            git_said="pruned",
            severity="FAIL-OPEN",
            details="Job 1 was candidate even though running Job 2 references its work folder"
        )

if __name__ == "__main__":
    test_shared_worktree()
