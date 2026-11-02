#!/usr/bin/env python3
"""Test Area 12: TOCTOU mutations through before_delete seam."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "~/agy-review/gemini2")
from harness import *

SCRATCH = Path("~/agy-review/gemini2/scratch_area12")

def test_toctou(name, mutate_in_before_delete):
    safe_rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    env = get_env(SCRATCH)

    repo = SCRATCH / "repo"
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(repo), check=True, capture_output=True, env=env)

    store = SCRATCH / "store"
    store_jobs = store / "jobs"
    store_jobs.mkdir(parents=True, exist_ok=True)

    job_id = "00000000-0000-0000-0000-000000000001"
    job_dir = store_jobs / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    job_data = {
        "job_id": job_id,
        "title": "toctou job",
        "state": "done",
        "repo_path": str(repo),
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2020-01-01T00:00:00Z",
    }
    (job_dir / "job.json").write_text(json.dumps(job_data, indent=2), encoding="utf-8")

    def hook(cid):
        mutate_in_before_delete(repo, store, job_dir, cid, env)

    res = run_cleanup(
        store,
        project=str(repo),
        dry_run=False,
        older_than_days=0.0,
        before_delete=hook,
    )

    deleted = job_id in res["deleted_ids"]
    failed = job_id in res["failed_ids"]
    print(f"TOCTOU {name}: deleted={deleted}, failed={failed}, skipped={res['skipped']}")
    if deleted:
        print(f"  FAIL: {name} was DELETED when it should have been retained/failed!")
        record_finding(
            repro_script=f"test_area12_toctou.py ({name})",
            command="python -m agy_mcp.cleanup_jobs ... with before_delete hook",
            cleaner_said=f"deleted={deleted}, failed={failed}",
            git_said="N/A",
            severity="FAIL-OPEN",
            details=f"TOCTOU mutation {name} was deleted"
        )

# Mutations to test
def mut_add_worktree(repo, store, job_dir, cid, env):
    # Add real git worktree pointing to job_dir / "work"
    work = job_dir / "work"
    subprocess.run(["git", "worktree", "add", str(work), "-b", "new_branch"], cwd=str(repo), check=True, capture_output=True, env=env)

def mut_create_dot_git_file(repo, store, job_dir, cid, env):
    work = job_dir / "work"
    work.mkdir(exist_ok=True)
    (work / ".git").write_text("gitdir: somewhere\n")

def mut_state_running(repo, store, job_dir, cid, env):
    jf = job_dir / "job.json"
    d = json.loads(jf.read_text())
    d["state"] = "running"
    jf.write_text(json.dumps(d))

def mut_repo_mismatch(repo, store, job_dir, cid, env):
    jf = job_dir / "job.json"
    d = json.loads(jf.read_text())
    d["repo_path"] = str(repo.parent / "other_repo")
    jf.write_text(json.dumps(d))

def mut_corrupt_json(repo, store, job_dir, cid, env):
    jf = job_dir / "job.json"
    jf.write_text("{corrupt")

def mut_symlink_inside(repo, store, job_dir, cid, env):
    (job_dir / "sym").symlink_to("/etc/passwd")

print("Area 12: TOCTOU tests")
test_toctou("add real git worktree", mut_add_worktree)
test_toctou("create work/.git file", mut_create_dot_git_file)
test_toctou("state changed to running", mut_state_running)
test_toctou("repo_path changed", mut_repo_mismatch)
test_toctou("corrupt json", mut_corrupt_json)
test_toctou("symlink added inside job dir", mut_symlink_inside)
