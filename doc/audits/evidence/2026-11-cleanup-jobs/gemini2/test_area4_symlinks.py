#!/usr/bin/env python3
"""Test Area 4: Symlinked parents of .git, worktrees, etc."""
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "~/agy-review/gemini2")
from harness import *

SCRATCH = Path("~/agy-review/gemini2/scratch_area4")

def test(name, mutate_fn):
    repo, store, job_dir, job_id, work, env = make_normal_repo_job(SCRATCH)
    try:
        mutate_fn(repo, store, job_dir, work)
    except Exception as e:
        print(f"  {name}: SETUP FAILED: {e}")
        return
    gw, gr, go = check_git(repo, work, env)
    ir, rr, ic, cm = check_cleaner(store, repo, job_dir, job_id)
    v = verdict(gw, gr, ic, cm)
    print(f"  {name}: git_works={gw} git_reg={gr} cleaner_reg={ir} candidate={ic} → {v}")
    if "FAIL-OPEN" in v or "CRASH" in v:
        print(f"    git output: {go[:200]}")
        print(f"    cleaner reason: {rr}")
        record_finding(
            repro_script=f"test_area4_symlinks.py ({name})",
            command=f"env -u PYTHONPATH .venv/bin/python ~/agy-review/gemini2/test_area4_symlinks.py",
            cleaner_said=f"candidate={ic}, reason={rr}",
            git_said=f"git_works={gw}, git_reg={gr}, out={go.strip()}",
            severity="FAIL-OPEN" if "FAIL-OPEN" in v else "CRASH",
            details=f"Symlink mutation {name}"
        )

print("Area 4: Symlinked parents and targets")

def dot_git_symlink_to_dir(r, s, j, w):
    actual_git = r.parent / "actual_dot_git"
    (r / ".git").rename(actual_git)
    (r / ".git").symlink_to(actual_git)

def worktrees_symlink_to_dir(r, s, j, w):
    actual_wt = r.parent / "actual_worktrees"
    (r / ".git" / "worktrees").rename(actual_wt)
    (r / ".git" / "worktrees").symlink_to(actual_wt)

def repo_parent_symlink(r, s, j, w):
    # repo is at scratch/repo, make scratch/sym_repo -> scratch/repo
    sym_repo = r.parent / "sym_repo"
    sym_repo.symlink_to(r)
    # update job.json to use sym_repo
    job_file = j / "job.json"
    data = json.loads(job_file.read_text())
    data["repo_path"] = str(sym_repo)
    job_file.write_text(json.dumps(data))

def work_parent_symlink(r, s, j, w):
    pass

mutations = {
    ".git is symlink to dir": dot_git_symlink_to_dir,
    ".git/worktrees is symlink to dir": worktrees_symlink_to_dir,
    "repo_path in job.json is symlink to repo": repo_parent_symlink,
}

for name, fn in mutations.items():
    test(name, fn)
