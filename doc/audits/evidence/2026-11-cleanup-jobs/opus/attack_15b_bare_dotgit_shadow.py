#!/usr/bin/env python3
"""Attack 15b: Deep investigation of bare repo + .git dir inside.

When a .git dir exists inside a bare repo, the code takes the
"git_dir.is_dir()" branch (L302) which looks at .git/worktrees/ instead
of bare_repo/worktrees/. But the actual worktree entries are in
bare_repo/worktrees/, not bare_repo/.git/worktrees/.

So the cleaner looks in the wrong place and misses the registration.
This IS a FAIL-OPEN if git also can't handle this unusual setup.
Let me check what git does.
"""
import json
import os
import shutil
import subprocess
import sys
sys.path.insert(0, "~/agy-review/opus")
from pathlib import Path
from harness import *

SCRATCH = Path("~/agy-review/opus/scratch_15b")

safe_rmtree(SCRATCH)
SCRATCH.mkdir(parents=True)
env = get_env(SCRATCH)

# Create bare repo
bare = SCRATCH / "bare.git"
subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True, env=env)
tree = subprocess.run(["git", "write-tree"], cwd=str(bare), check=True,
                      capture_output=True, text=True, env=env).stdout.strip()
commit = subprocess.run(["git", "commit-tree", tree, "-m", "init"], cwd=str(bare),
                        check=True, capture_output=True, text=True, env=env).stdout.strip()
subprocess.run(["git", "update-ref", "refs/heads/main", commit],
                cwd=str(bare), check=True, capture_output=True, env=env)

store = SCRATCH / "store"
store_jobs = store / "jobs"
store_jobs.mkdir(parents=True)

job_id = "00000000-0000-0000-0000-000000000001"
job_dir = store_jobs / job_id
job_dir.mkdir()
work = job_dir / "work"

subprocess.run(["git", "worktree", "add", str(work), "main"],
                cwd=str(bare), check=True, capture_output=True, env=env)
(work / ".git").unlink()

job_data = {
    "job_id": job_id, "title": "attack", "state": "done",
    "repo_path": str(bare),
    "created_at": "2020-01-01T00:00:00Z", "updated_at": "2020-01-01T00:00:00Z",
}
(job_dir / "job.json").write_text(json.dumps(job_data, indent=2), encoding="utf-8")

# Before mutation: verify git sees worktree
print("Before mutation:")
res = subprocess.run(["git", "-C", str(bare), "worktree", "list", "--porcelain"],
                     capture_output=True, text=True, env=env)
print(f"  git returncode={res.returncode}")
for line in res.stdout.splitlines():
    if line.startswith("worktree "):
        print(f"  {line}")

# Verify worktree entries are in bare.git/worktrees/
print(f"\n  bare.git/worktrees/ exists: {(bare / 'worktrees').exists()}")
print(f"  bare.git/worktrees/work/ exists: {(bare / 'worktrees' / 'work').exists()}")

# MUTATION: Create .git as a directory inside bare repo with HEAD
(bare / ".git").mkdir()
(bare / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
# Note: bare.git/.git/worktrees/ does NOT exist

print("\nAfter creating bare.git/.git/ dir:")
print(f"  bare.git/.git/ exists: {(bare / '.git').exists()}")
print(f"  bare.git/.git/ is_dir: {(bare / '.git').is_dir()}")
print(f"  bare.git/.git/HEAD is_file: {(bare / '.git' / 'HEAD').is_file()}")
print(f"  bare.git/.git/worktrees/ exists: {(bare / '.git' / 'worktrees').exists()}")
print(f"  bare.git/worktrees/ exists: {(bare / 'worktrees').exists()}")

# Check git
res2 = subprocess.run(["git", "-C", str(bare), "worktree", "list", "--porcelain"],
                      capture_output=True, text=True, env=env)
git_works = res2.returncode == 0
work_resolved = str(work.resolve(strict=False))
found = False
if git_works:
    for line in res2.stdout.splitlines():
        if line.startswith("worktree "):
            wt = line[len("worktree "):].strip()
            try:
                if str(Path(wt).resolve(strict=False)) == work_resolved:
                    found = True
            except: pass

print(f"\n  git_works={git_works}, git_registered={found}")
print(f"  git output:\n{res2.stdout}")
if res2.stderr:
    print(f"  git stderr: {res2.stderr}")

# Check cleaner
from agy_mcp.cleanup_jobs import _check_worktree_status
is_reg, reason = _check_worktree_status(job_dir, {"repo_path": str(bare)})
print(f"\n  cleaner: is_registered={is_reg}, reason={reason}")

if not is_reg and (not git_works or found):
    print("\n*** FAIL-OPEN: cleaner misses worktree because .git dir shadows bare repo's worktrees/ ***")
elif not is_reg and git_works and not found:
    print("\n--- OK: git also doesn't see worktree (unusual setup confused git too) ---")
else:
    print("\n--- OK ---")
