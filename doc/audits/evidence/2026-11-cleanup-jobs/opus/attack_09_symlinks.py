#!/usr/bin/env python3
"""Attack 9: Symlink loops, symlinked parents of .git or worktrees."""
import os
import shutil
import subprocess
import sys
sys.path.insert(0, "~/agy-review/opus")
from pathlib import Path
from harness import *

SCRATCH = Path("~/agy-review/opus/scratch_09")

print("Attack 9: Symlink loops and symlinked parents")

def test(name, mutate_fn):
    repo, store, job_dir, job_id, work, env = make_normal_repo_job(SCRATCH)
    try:
        mutate_fn(repo)
    except Exception as e:
        print(f"  {name}: SETUP FAILED: {e}")
        return
    gw, gr, go = check_git(repo, work, env)
    ir, rr, ic, cm = check_cleaner(store, repo, job_dir, job_id)
    v = verdict(gw, gr, ic, cm)
    print(f"  {name}: git_works={gw} git_reg={gr} cleaner_reg={ir} candidate={ic} → {v}")
    if "FAIL-OPEN" in v or "CRASH" in v:
        print(f"    git: {go[:200]}")
        print(f"    reason: {rr}")

mutations = {
    # Symlink loop: .git/worktrees/work → .git/worktrees/work (self-referential)
    "worktrees entry → self symlink": lambda r: (
        shutil.rmtree(r / ".git" / "worktrees" / "work"),
        (r / ".git" / "worktrees" / "work").symlink_to(r / ".git" / "worktrees" / "work"),
    ),
    # Symlink loop: .git/worktrees → .git
    "worktrees/ → parent .git symlink": lambda r: (
        shutil.rmtree(r / ".git" / "worktrees"),
        (r / ".git" / "worktrees").symlink_to(r / ".git"),
    ),
    # Symlinked parent of .git: repo → symlink to real repo
    "repo itself is symlink": lambda r: (
        r.rename(r.with_name("repo_real")),
        r.symlink_to(r.with_name("repo_real")),
    ),
    # .git → symlink to a valid .git dir (should be allowed per SPEC R3)
    ".git → symlink to itself renamed": lambda r: (
        (r / ".git").rename(r / ".git_real"),
        (r / ".git").symlink_to(r / ".git_real"),
    ),
    # worktrees/work/gitdir → symlink loop
    "gitdir file → symlink loop": lambda r: (
        (r / ".git" / "worktrees" / "work" / "gitdir").unlink(),
        (r / ".git" / "worktrees" / "work" / "gitdir").symlink_to(
            r / ".git" / "worktrees" / "work" / "gitdir"
        ),
    ),
}

for name, fn in mutations.items():
    test(name, fn)

# Test: symlinked ancestor of repo_path
print("\nTest: repo_path under a symlinked parent directory")
safe_rmtree(SCRATCH)
SCRATCH.mkdir(parents=True)
env = get_env(SCRATCH)

real_parent = SCRATCH / "real_parent"
real_parent.mkdir()
repo = real_parent / "repo"
subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, env=env)
subprocess.run(["git", "commit", "--allow-empty", "-m", "init"],
                cwd=str(repo), check=True, capture_output=True, env=env)

# Create symlink to parent
sym_parent = SCRATCH / "sym_parent"
sym_parent.symlink_to(real_parent)
sym_repo = sym_parent / "repo"

store = SCRATCH / "store"
store_jobs = store / "jobs"
store_jobs.mkdir(parents=True)
job_id = "00000000-0000-0000-0000-000000000001"
job_dir = store_jobs / job_id
job_dir.mkdir()
work = job_dir / "work"

subprocess.run(["git", "worktree", "add", str(work), "-b", "wt_a"],
                cwd=str(repo), check=True, capture_output=True, env=env)
(work / ".git").unlink()

import json
# Point job.json repo_path to the symlinked path
job_data = {
    "job_id": job_id, "title": "attack", "state": "done",
    "repo_path": str(sym_repo),  # through symlink!
    "created_at": "2020-01-01T00:00:00Z", "updated_at": "2020-01-01T00:00:00Z",
}
(job_dir / "job.json").write_text(json.dumps(job_data, indent=2), encoding="utf-8")

# The normalized repo_path should resolve through symlink
gw, gr, go = check_git(repo, work, env)
ir, rr, ic, cm = check_cleaner(store, sym_repo, job_dir, job_id)
v = verdict(gw, gr, ic, cm)
print(f"  symlinked parent repo_path: git_works={gw} git_reg={gr} cleaner_reg={ir} candidate={ic} → {v}")
if "FAIL-OPEN" in v:
    print(f"    reason: {rr}")
