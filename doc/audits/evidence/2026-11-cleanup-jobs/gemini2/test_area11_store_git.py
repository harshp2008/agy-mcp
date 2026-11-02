#!/usr/bin/env python3
"""Test Area 11: repo_path equal to store or inside store."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "~/agy-review/gemini2")
from harness import *

SCRATCH = Path("~/agy-review/gemini2/scratch_area11")

def test_store_is_repo():
    safe_rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    env = get_env(SCRATCH)

    store = SCRATCH / "store"
    store_jobs = store / "jobs"
    store_jobs.mkdir(parents=True, exist_ok=True)

    # Init git in store
    subprocess.run(["git", "init", str(store)], check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(store), check=True, capture_output=True, env=env)

    job_id = "00000000-0000-0000-0000-000000000001"
    job_dir = store_jobs / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    work = job_dir / "work"

    subprocess.run(["git", "worktree", "add", str(work), "-b", "store_wt"], cwd=str(store), check=True, capture_output=True, env=env)
    (work / ".git").unlink()

    job_data = {
        "job_id": job_id,
        "title": "store repo job",
        "state": "done",
        "repo_path": str(store),
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2020-01-01T00:00:00Z",
    }
    (job_dir / "job.json").write_text(json.dumps(job_data, indent=2), encoding="utf-8")

    gw, gr, go = check_git(store, work, env)
    ir, rr, ic, cm = check_cleaner(store, store, job_dir, job_id)
    v = verdict(gw, gr, ic, cm)
    print(f"store is repo: git_works={gw} git_reg={gr} cleaner_reg={ir} candidate={ic} → {v}")
    if "FAIL-OPEN" in v or "CRASH" in v:
        print(f"  git out: {go[:200]}")
        print(f"  cleaner reason: {rr}")
        record_finding(
            repro_script="test_area11_store_git.py",
            command="run_cleanup with store as repo",
            cleaner_said=f"candidate={ic}, reason={rr}",
            git_said=f"git_works={gw}, git_reg={gr}, out={go.strip()}",
            severity="FAIL-OPEN" if "FAIL-OPEN" in v else "CRASH",
            details="Store is git repo"
        )

if __name__ == "__main__":
    test_store_is_repo()
