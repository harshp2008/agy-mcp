#!/usr/bin/env python3
"""Test Area 2: worktree entry dir replaced by a file or symlink."""
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "~/agy-review/gemini2")
from harness import *

SCRATCH = Path("~/agy-review/gemini2/scratch_area2")

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
        record_finding(
            repro_script=f"test_area2_worktree_entries.py ({name})",
            command=f"env -u PYTHONPATH .venv/bin/python ~/agy-review/gemini2/test_area2_worktree_entries.py",
            cleaner_said=f"candidate={ic}, reason={rr}",
            git_said=f"git_works={gw}, git_reg={gr}, out={go.strip()}",
            severity="FAIL-OPEN" if "FAIL-OPEN" in v else "CRASH",
            details=f"Worktree entry mutation {name}"
        )

print("Area 2: worktree entry dir mutations")

# In normal repo, the worktree entry is at .git/worktrees/wt_a
def entry_dir(r: Path) -> Path:
    wt_dir = r / ".git" / "worktrees"
    entries = list(wt_dir.iterdir())
    return entries[0]

mutations = {
    "entry → file": lambda r: (
        shutil.rmtree(entry_dir(r)),
        (r / ".git" / "worktrees" / "wt_a").write_text("not a dir\n"),
    ),
    "entry → dangling symlink": lambda r: (
        shutil.rmtree(entry_dir(r)),
        (r / ".git" / "worktrees" / "wt_a").symlink_to("nonexistent"),
    ),
    "entry → symlink to another dir with gitdir": lambda r: (
        entry_dir(r).rename(r / ".git" / "wt_moved"),
        (r / ".git" / "worktrees" / "wt_a").symlink_to(r / ".git" / "wt_moved"),
    ),
    "entry → chmod 000": lambda r: os.chmod(entry_dir(r), 0o000),
    "entry/gitdir → dir": lambda r: (
        (entry_dir(r) / "gitdir").unlink(),
        (entry_dir(r) / "gitdir").mkdir(),
    ),
    "entry/gitdir → dangling symlink": lambda r: (
        (entry_dir(r) / "gitdir").unlink(),
        (entry_dir(r) / "gitdir").symlink_to("nonexistent"),
    ),
    "entry/gitdir → fifo": lambda r: (
        (entry_dir(r) / "gitdir").unlink(),
        os.mkfifo(entry_dir(r) / "gitdir"),
    ),
}

for name, fn in mutations.items():
    test(name, fn)
