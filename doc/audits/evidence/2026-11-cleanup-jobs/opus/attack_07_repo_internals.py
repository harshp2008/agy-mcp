#!/usr/bin/env python3
"""Attack 7: objects/refs/config/packed-refs replaced with dirs/symlinks in normal repo.

When .git/HEAD is valid but other critical git internals are corrupted,
does git fail while cleaner says deletable?
"""
import os
import shutil
import sys
sys.path.insert(0, "~/agy-review/opus")
from harness import *

SCRATCH = Path("~/agy-review/opus/scratch_07")

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
        print(f"    git output: {go[:200]}")
        print(f"    cleaner reason: {rr}")

print("Attack 7: objects/refs/config/packed-refs mutations on normal repo")

mutations = {
    # objects/ mutations
    "objects/ deleted": lambda r: shutil.rmtree(r / ".git" / "objects"),
    "objects/ → file": lambda r: (shutil.rmtree(r / ".git" / "objects"), (r / ".git" / "objects").write_text("x")),
    "objects/ → symlink dangling": lambda r: (shutil.rmtree(r / ".git" / "objects"), (r / ".git" / "objects").symlink_to("nonexistent")),
    "objects/ chmod 000": lambda r: os.chmod(r / ".git" / "objects", 0o000),
    # refs/ mutations
    "refs/ deleted": lambda r: shutil.rmtree(r / ".git" / "refs"),
    "refs/ → file": lambda r: (shutil.rmtree(r / ".git" / "refs"), (r / ".git" / "refs").write_text("x")),
    # config mutations
    "config deleted": lambda r: (r / ".git" / "config").unlink(),
    "config → dir": lambda r: ((r / ".git" / "config").unlink(), (r / ".git" / "config").mkdir()),
    "config → symlink dangling": lambda r: ((r / ".git" / "config").unlink(), (r / ".git" / "config").symlink_to("nope")),
    # packed-refs (may not exist)
    "packed-refs created as dir": lambda r: (r / ".git" / "packed-refs").mkdir(exist_ok=True),
    # worktrees/ dir itself
    "worktrees/ → symlink to real": lambda r: (
        (r / ".git" / "worktrees").rename(r / ".git" / "worktrees_real"),
        (r / ".git" / "worktrees").symlink_to("worktrees_real"),
    ),
    "worktrees/ → dangling symlink": lambda r: (
        shutil.rmtree(r / ".git" / "worktrees"),
        (r / ".git" / "worktrees").symlink_to("nonexistent"),
    ),
    "worktrees/ → file": lambda r: (
        shutil.rmtree(r / ".git" / "worktrees"),
        (r / ".git" / "worktrees").write_text("file\n"),
    ),
    "worktrees/ chmod 000": lambda r: os.chmod(r / ".git" / "worktrees", 0o000),
}

for name, fn in mutations.items():
    test(name, fn)
