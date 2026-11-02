#!/usr/bin/env python3
"""Test Area 3: Symlink loops in various places."""
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "~/agy-review/gemini2")
from harness import *

SCRATCH = Path("~/agy-review/gemini2/scratch_area3")

def test(name, mutate_fn, is_store_mutation=False):
    repo, store, job_dir, job_id, work, env = make_normal_repo_job(SCRATCH)
    try:
        mutate_fn(repo, store, job_dir)
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
            repro_script=f"test_area3_symlink_loops.py ({name})",
            command=f"env -u PYTHONPATH .venv/bin/python ~/agy-review/gemini2/test_area3_symlink_loops.py",
            cleaner_said=f"candidate={ic}, reason={rr}",
            git_said=f"git_works={gw}, git_reg={gr}, out={go.strip()}",
            severity="FAIL-OPEN" if "FAIL-OPEN" in v else "CRASH",
            details=f"Symlink loop {name}"
        )

print("Area 3: Symlink loops")

def loop_dot_git(r, s, j):
    shutil.rmtree(r / ".git")
    (r / ".git").symlink_to(".git")

def loop_head(r, s, j):
    (r / ".git" / "HEAD").unlink()
    (r / ".git" / "HEAD").symlink_to("HEAD")

def loop_gitdir(r, s, j):
    gd = list((r / ".git" / "worktrees").iterdir())[0] / "gitdir"
    gd.unlink()
    gd.symlink_to("gitdir")

def loop_commondir(r, s, j):
    cd = list((r / ".git" / "worktrees").iterdir())[0] / "commondir"
    cd.symlink_to("commondir")

def loop_worktrees_dir(r, s, j):
    wt = r / ".git" / "worktrees"
    shutil.rmtree(wt)
    wt.symlink_to("worktrees")

def loop_inside_job_dir(r, s, j):
    (j / "loop").symlink_to("loop")

def loop_job_json(r, s, j):
    (j / "job.json").unlink()
    (j / "job.json").symlink_to("job.json")

def loop_repo_path(r, s, j):
    # r / "loop" -> "loop"
    pass

mutations = {
    ".git -> .git loop": loop_dot_git,
    "HEAD -> HEAD loop": loop_head,
    "gitdir -> gitdir loop": loop_gitdir,
    "commondir -> commondir loop": loop_commondir,
    "worktrees -> worktrees loop": loop_worktrees_dir,
    "inside job_dir loop": loop_inside_job_dir,
    "job.json loop": loop_job_json,
}

for name, fn in mutations.items():
    test(name, fn)
