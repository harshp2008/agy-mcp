#!/usr/bin/env python3
"""Repro: worktree_path not checked in repository worktrees registrations.
When job has custom worktree_path whose .git is unlinked (before prune),
real git still lists the worktree in repo/.git/worktrees, but cleaner
only matches against job_dir and job_dir/work, failing to check worktree_path.
Cleaner says deletable (FAIL-OPEN) and deletes the job with --yes.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "~/agy-review/gemini2")
from harness import *

SCRATCH = Path("~/agy-review/gemini2/scratch_wt_path_repro")
safe_rmtree(SCRATCH)
SCRATCH.mkdir(parents=True, exist_ok=True)
env = get_env(SCRATCH)
env["PYTHONPATH"] = str(REPO_SRC)

# 1. Repo
repo = SCRATCH / "repo"
subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, env=env)
subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(repo), check=True, capture_output=True, env=env)

# 2. External worktree path outside store
ext_worktree = SCRATCH / "custom_worktree"
subprocess.run(["git", "worktree", "add", str(ext_worktree), "-b", "ext_branch"], cwd=str(repo), check=True, capture_output=True, env=env)

# 3. Store and job
store = SCRATCH / "store"
store_jobs = store / "jobs"
store_jobs.mkdir(parents=True, exist_ok=True)

job_id = "00000000-0000-0000-0000-000000000001"
job_dir = store_jobs / job_id
job_dir.mkdir(parents=True, exist_ok=True)

job_data = {
    "job_id": job_id,
    "title": "job with custom worktree_path",
    "state": "done",
    "repo_path": str(repo),
    "worktree_path": str(ext_worktree),
    "created_at": "2020-01-01T00:00:00Z",
    "updated_at": "2020-01-01T00:00:00Z",
}
(job_dir / "job.json").write_text(json.dumps(job_data, indent=2), encoding="utf-8")

# 4. Unlink ext_worktree/.git (simulate worktree directory cleanup prior to prune)
(ext_worktree / ".git").unlink()

# 5. Check real git oracle
res_git = subprocess.run(
    ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
    capture_output=True, text=True, env=env
)
git_works = res_git.returncode == 0
ext_res = str(ext_worktree.resolve(strict=False))
git_registered = False
for line in res_git.stdout.splitlines():
    if line.startswith("worktree "):
        wt_path = line[len("worktree "):].strip()
        if str(Path(wt_path).resolve(strict=False)) == ext_res:
            git_registered = True
            break

print(f"Git ground truth: git_works={git_works}, git_registered={git_registered}")
print(f"Git output:\n{res_git.stdout}")

# 6. Check cleaner in dry-run
cmd_dry = [
    sys.executable, "-m", "agy_mcp.cleanup_jobs",
    "--store", str(store),
    "--project", str(repo),
    "--older-than-days", "0",
    "--json",
]
res_cleaner_dry = subprocess.run(cmd_dry, capture_output=True, text=True, env=env)
report_dry = json.loads(res_cleaner_dry.stdout)
cands = [c["job_id"] for c in report_dry.get("candidates", [])]
print(f"Cleaner dry-run candidates: {cands}")

# 7. Check cleaner with --yes (actual deletion)
cmd_yes = [
    sys.executable, "-m", "agy_mcp.cleanup_jobs",
    "--store", str(store),
    "--project", str(repo),
    "--older-than-days", "0",
    "--yes",
    "--json",
]
res_cleaner_yes = subprocess.run(cmd_yes, capture_output=True, text=True, env=env)
report_yes = json.loads(res_cleaner_yes.stdout)
deleted_ids = report_yes.get("deleted_ids", [])
print(f"Cleaner --yes deleted_ids: {deleted_ids}")
print(f"Job directory exists after --yes: {job_dir.exists()}")

if git_registered and job_id in cands:
    print("\n*** CONFIRMED FAIL-OPEN FINDING ***")
    record_finding(
        repro_script="~/agy-review/gemini2/test_finding_worktree_path_unregistered.py",
        command=f"env -u PYTHONPATH .venv/bin/python -m agy_mcp.cleanup_jobs --store {store} --project {repo} --older-than-days 0 --yes",
        cleaner_said=f"candidates=['{job_id}'], deleted_ids=['{job_id}'] (job dir deleted={not job_dir.exists()})",
        git_said=f"git -C repo lists worktree as registered ({ext_worktree})",
        severity="FAIL-OPEN",
        details=(
            "When job.json specifies a custom worktree_path and worktree_path/.git is unlinked "
            "before 'git worktree prune' is run, real git continues to list the worktree as registered "
            "in repo/.git/worktrees. However, _check_worktree_status lines 422-427 only checks if target_path "
            "matches job_dir or job_dir/work (or their parents). It completely fails to check if target_path "
            "matches the job's worktree_path. As a result, cleaner returns is_registered=False, classifies "
            "the job record as a candidate, and under --yes deletes it while git still lists the worktree."
        )
    )
