#!/usr/bin/env python3
"""Attack 12: Nested worktrees — worktree of a worktree.

Setup: Create repo A. Create worktree B from A (B is the repo_path in job.json).
Create worktree C from B (inside the job dir). C's .git file points into
B's gitdir, which points into A's gitdir.

Question: If we delete the .git file from C (work/), does the cleaner
correctly look up B's worktrees (which requires following B/.git → A/.git/worktrees/B → commondir → A/.git → A/.git/worktrees)?

This is similar to Layout B but with an extra nesting level.
"""
import json
import os
import shutil
import subprocess
import sys
sys.path.insert(0, "~/agy-review/opus")
from pathlib import Path
from harness import *

SCRATCH = Path("~/agy-review/opus/scratch_12")

print("Attack 12: Nested worktrees (worktree of a worktree)")
safe_rmtree(SCRATCH)
SCRATCH.mkdir(parents=True)
env = get_env(SCRATCH)

# Create base repo A
repo_a = SCRATCH / "repo_a"
subprocess.run(["git", "init", str(repo_a)], check=True, capture_output=True, env=env)
subprocess.run(["git", "commit", "--allow-empty", "-m", "init"],
                cwd=str(repo_a), check=True, capture_output=True, env=env)

# Create worktree B from A (this is the "repo_path")
repo_b = SCRATCH / "repo_b"
subprocess.run(["git", "worktree", "add", str(repo_b), "-b", "branch_b"],
                cwd=str(repo_a), check=True, capture_output=True, env=env)

print(f"repo_b/.git is file: {(repo_b / '.git').is_file()}")
print(f"repo_b/.git content: {(repo_b / '.git').read_text()!r}")

# Create store and job
store = SCRATCH / "store"
store_jobs = store / "jobs"
store_jobs.mkdir(parents=True)

job_id = "00000000-0000-0000-0000-000000000001"
job_dir = store_jobs / job_id
job_dir.mkdir()
work = job_dir / "work"

# Create worktree C from B (inside job dir)
subprocess.run(["git", "worktree", "add", str(work), "-b", "branch_c"],
                cwd=str(repo_b), check=True, capture_output=True, env=env)
print(f"work/.git content: {(work / '.git').read_text()!r}")

# Remove work/.git (simulate worktree work dir cleanup)
(work / ".git").unlink()

job_data = {
    "job_id": job_id, "title": "nested wt", "state": "done",
    "repo_path": str(repo_b),
    "created_at": "2020-01-01T00:00:00Z", "updated_at": "2020-01-01T00:00:00Z",
}
(job_dir / "job.json").write_text(json.dumps(job_data, indent=2), encoding="utf-8")

# Check git ground truth
gw, gr, go = check_git(repo_b, work, env)
print(f"\ngit_works={gw}, git_registered={gr}")
if gw:
    print(f"git output:\n{go}")

# Check cleaner
ir, rr, ic, cm = check_cleaner(store, repo_b, job_dir, job_id)
v = verdict(gw, gr, ic, cm)
print(f"cleaner: is_registered={ir}, reason={rr}, candidate={ic}")
print(f"Verdict: {v}")

if "FAIL-OPEN" in v:
    print("\n*** FAIL-OPEN with nested worktrees ***")

# Also check: git -C repo_a worktree list
print("\ngit -C repo_a worktree list:")
res_a = subprocess.run(["git", "-C", str(repo_a), "worktree", "list", "--porcelain"],
                        capture_output=True, text=True, env=env)
print(res_a.stdout)

# Now mutate: corrupt repo_b's .git file (the gitdir pointer)
print("\n--- Mutation: corrupt repo_b/.git file ---")
repo_b_git = repo_b / ".git"
orig = repo_b_git.read_bytes()

# Add extra lines
repo_b_git.write_bytes(orig + b"# extra comment\n")
gw2, gr2, go2 = check_git(repo_b, work, env)
ir2, rr2, ic2, cm2 = check_cleaner(store, repo_b, job_dir, job_id)
v2 = verdict(gw2, gr2, ic2, cm2)
print(f"extra lines: git_works={gw2} git_reg={gr2} cleaner_reg={ir2} candidate={ic2} → {v2}")

# Restore and try: BOM
repo_b_git.write_bytes(b"\xef\xbb\xbf" + orig)
gw3, gr3, go3 = check_git(repo_b, work, env)
ir3, rr3, ic3, cm3 = check_cleaner(store, repo_b, job_dir, job_id)
v3 = verdict(gw3, gr3, ic3, cm3)
print(f"BOM: git_works={gw3} git_reg={gr3} cleaner_reg={ir3} candidate={ic3} → {v3}")

# Restore and try: NUL byte
repo_b_git.write_bytes(orig[:5] + b"\x00" + orig[5:])
gw4, gr4, go4 = check_git(repo_b, work, env)
ir4, rr4, ic4, cm4 = check_cleaner(store, repo_b, job_dir, job_id)
v4 = verdict(gw4, gr4, ic4, cm4)
print(f"NUL: git_works={gw4} git_reg={gr4} cleaner_reg={ir4} candidate={ic4} → {v4}")
