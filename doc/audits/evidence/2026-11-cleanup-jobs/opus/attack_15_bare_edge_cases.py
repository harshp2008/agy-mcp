#!/usr/bin/env python3
"""Attack 15: Bare repo variants - worktrees/ present without HEAD.

The root cause from F1 is: when .git is absent and HEAD is not a file,
the bare-repo worktree check is entirely skipped. Let me check if there
are OTHER conditions that skip the bare repo path when worktrees/ exists.

Also: what if repo_path has BOTH .git (as a dir) AND a worktrees/ dir
at repo_path level? This shouldn't happen with real git but is worth testing
for defensive purposes.

And: what if repo_path is a bare repo that has .git as a DIRECTORY
(unusual but possible if someone mkdir'd .git inside a bare repo)?
"""
import json
import os
import shutil
import subprocess
import sys
sys.path.insert(0, "~/agy-review/opus")
from pathlib import Path
from harness import *

SCRATCH = Path("~/agy-review/opus/scratch_15")

print("Attack 15: Bare repo edge cases")

# Test 1: bare repo with HEAD permission denied
def test_bare_head_perms():
    bare, store, job_dir, job_id, work, env = make_bare_repo_job(SCRATCH)
    os.chmod(bare / "HEAD", 0o000)
    gw, gr, go = check_git(bare, work, env)
    ir, rr, ic, cm = check_cleaner(store, bare, job_dir, job_id)
    v = verdict(gw, gr, ic, cm)
    print(f"  HEAD chmod 000: git_works={gw} git_reg={gr} cleaner_reg={ir} cand={ic} → {v}")
    os.chmod(bare / "HEAD", 0o644)  # restore

test_bare_head_perms()

# Test 2: bare repo where worktrees/ dir is chmod 000
def test_bare_wt_perms():
    bare, store, job_dir, job_id, work, env = make_bare_repo_job(SCRATCH)
    os.chmod(bare / "worktrees", 0o000)
    gw, gr, go = check_git(bare, work, env)
    ir, rr, ic, cm = check_cleaner(store, bare, job_dir, job_id)
    v = verdict(gw, gr, ic, cm)
    print(f"  worktrees/ chmod 000: git_works={gw} git_reg={gr} cleaner_reg={ir} cand={ic} → {v}")
    os.chmod(bare / "worktrees", 0o755)

test_bare_wt_perms()

# Test 3: bare repo where HEAD is a FIFO (is_file()=False for FIFOs)
def test_bare_head_fifo():
    bare, store, job_dir, job_id, work, env = make_bare_repo_job(SCRATCH)
    (bare / "HEAD").unlink()
    os.mkfifo(bare / "HEAD")
    # Don't check git (would hang on FIFO)
    ir, rr, ic, cm = check_cleaner(store, bare, job_dir, job_id)
    print(f"  HEAD → FIFO: cleaner_reg={ir} cand={ic}")
    if ic:
        print(f"    *** FAIL-OPEN (FIFO HEAD on bare repo) ***")
    os.unlink(bare / "HEAD")

test_bare_head_fifo()

# Test 4: bare repo with .git dir created inside it
def test_bare_with_dotgit_dir():
    bare, store, job_dir, job_id, work, env = make_bare_repo_job(SCRATCH)
    # Create .git as a directory inside the bare repo
    (bare / ".git").mkdir()
    (bare / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    gw, gr, go = check_git(bare, work, env)
    ir, rr, ic, cm = check_cleaner(store, bare, job_dir, job_id)
    v = verdict(gw, gr, ic, cm)
    print(f"  bare + .git dir inside: git_works={gw} git_reg={gr} cleaner_reg={ir} cand={ic} → {v}")

test_bare_with_dotgit_dir()

# Test 5: normal repo where .git/HEAD is a FIFO
def test_normal_head_fifo():
    repo, store, job_dir, job_id, work, env = make_normal_repo_job(SCRATCH)
    (repo / ".git" / "HEAD").unlink()
    os.mkfifo(repo / ".git" / "HEAD")
    # _read_git_metadata_file won't hang on FIFO because it checks is_file() first
    # Actually, is_file() returns False for FIFOs. So (git_dir / "HEAD").is_file() = False
    # → returns (True, "worktree registrations unreadable (.git is not a git directory)")
    ir, rr, ic, cm = check_cleaner(store, repo, job_dir, job_id)
    print(f"  normal repo HEAD → FIFO: cleaner_reg={ir} reason={rr}")
    os.unlink(repo / ".git" / "HEAD")

test_normal_head_fifo()
