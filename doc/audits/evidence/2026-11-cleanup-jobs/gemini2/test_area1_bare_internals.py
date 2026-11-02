#!/usr/bin/env python3
"""Test Area 1: Bare repo internals mutations."""
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "~/agy-review/gemini2")
from harness import *

SCRATCH = Path("~/agy-review/gemini2/scratch_area1")

def test(name, mutate_fn):
    bare, store, job_dir, job_id, work, env = make_bare_repo_job(SCRATCH)
    try:
        mutate_fn(bare)
    except Exception as e:
        print(f"  {name}: SETUP FAILED: {e}")
        return
    gw, gr, go = check_git(bare, work, env)
    ir, rr, ic, cm = check_cleaner(store, bare, job_dir, job_id)
    v = verdict(gw, gr, ic, cm)
    print(f"  {name}: git_works={gw} git_reg={gr} cleaner_reg={ir} candidate={ic} → {v}")
    if "FAIL-OPEN" in v or "CRASH" in v:
        print(f"    git output: {go[:200]}")
        print(f"    cleaner reason: {rr}")
        record_finding(
            repro_script=f"test_area1_bare_internals.py ({name})",
            command=f"env -u PYTHONPATH .venv/bin/python ~/agy-review/gemini2/test_area1_bare_internals.py",
            cleaner_said=f"candidate={ic}, reason={rr}",
            git_said=f"git_works={gw}, git_reg={gr}, out={go.strip()}",
            severity="FAIL-OPEN" if "FAIL-OPEN" in v else "CRASH",
            details=f"Bare repo mutation {name}"
        )

print("Area 1: objects/refs/config/packed-refs mutations on bare repo")

mutations = {
    # objects/ mutations
    "objects/ deleted": lambda r: shutil.rmtree(r / "objects"),
    "objects/ → file": lambda r: (shutil.rmtree(r / "objects"), (r / "objects").write_text("x")),
    "objects/ → symlink dangling": lambda r: (shutil.rmtree(r / "objects"), (r / "objects").symlink_to("nonexistent")),
    "objects/ chmod 000": lambda r: os.chmod(r / "objects", 0o000),
    # refs/ mutations
    "refs/ deleted": lambda r: shutil.rmtree(r / "refs"),
    "refs/ → file": lambda r: (shutil.rmtree(r / "refs"), (r / "refs").write_text("x")),
    "refs/ → symlink dangling": lambda r: (shutil.rmtree(r / "refs"), (r / "refs").symlink_to("nonexistent")),
    "refs/ chmod 000": lambda r: os.chmod(r / "refs", 0o000),
    # config mutations
    "config deleted": lambda r: (r / "config").unlink(),
    "config → dir": lambda r: ((r / "config").unlink(), (r / "config").mkdir()),
    "config → symlink dangling": lambda r: ((r / "config").unlink(), (r / "config").symlink_to("nope")),
    # packed-refs
    "packed-refs created as dir": lambda r: (r / "packed-refs").mkdir(exist_ok=True),
    "packed-refs created as file": lambda r: (r / "packed-refs").write_text("invalid"),
    # worktrees/ dir itself
    "worktrees/ → symlink to real": lambda r: (
        (r / "worktrees").rename(r / "worktrees_real"),
        (r / "worktrees").symlink_to("worktrees_real"),
    ),
    "worktrees/ → dangling symlink": lambda r: (
        shutil.rmtree(r / "worktrees"),
        (r / "worktrees").symlink_to("nonexistent"),
    ),
    "worktrees/ → file": lambda r: (
        shutil.rmtree(r / "worktrees"),
        (r / "worktrees").write_text("file\n"),
    ),
    "worktrees/ chmod 000": lambda r: os.chmod(r / "worktrees", 0o000),
}

for name, fn in mutations.items():
    test(name, fn)
