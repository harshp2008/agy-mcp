#!/usr/bin/env python3
"""Test what cleaner does when commondir is missing in a linked worktree."""
import tempfile, subprocess, shutil, json, sys
from pathlib import Path

sys.path.insert(0, "~/Documents/GITHUB PROJECTS/agy-mcp/src")
from agy_mcp.cleanup_jobs import _check_worktree_status, run_cleanup

with tempfile.TemporaryDirectory(dir="~/agy-review/gemini2") as td:
    scratch = Path(td)
    main_repo = scratch / "main"
    subprocess.run(["git", "init", str(main_repo)], check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(main_repo), check=True, capture_output=True)

    wt1 = scratch / "wt1"
    subprocess.run(["git", "worktree", "add", str(wt1), "-b", "wt1_b"], cwd=str(main_repo), check=True, capture_output=True)

    # Job is created with repo_path = wt1
    store = scratch / "store"
    store_jobs = store / "jobs"
    store_jobs.mkdir(parents=True)
    job_id = "00000000-0000-0000-0000-000000000001"
    job_dir = store_jobs / job_id
    job_dir.mkdir()
    job_data = {
        "job_id": job_id,
        "title": "linked wt job",
        "state": "done",
        "repo_path": str(wt1),
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2020-01-01T00:00:00Z",
    }
    (job_dir / "job.json").write_text(json.dumps(job_data))

    # Delete commondir
    entries = list((main_repo / ".git" / "worktrees").iterdir())
    commondir = entries[0] / "commondir"
    commondir.unlink()

    # What does git say?
    res_git = subprocess.run(["git", "-C", str(wt1), "worktree", "list"], capture_output=True, text=True)
    git_works = res_git.returncode == 0
    print("git_works:", git_works, "rc:", res_git.returncode, "stderr:", res_git.stderr.strip())

    # What does cleaner say?
    is_reg, reason = _check_worktree_status(job_dir, job_data)
    print("cleaner _check_worktree_status:", is_reg, "reason:", reason)

    res_cleaner = run_cleanup(store, project=str(wt1), dry_run=True, older_than_days=0.0)
    cands = [c["job_id"] for c in res_cleaner.get("candidates", [])]
    print("cleaner candidates:", cands)
