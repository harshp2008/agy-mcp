#!/usr/bin/env python3
"""Attack 13: CRLF and trailing-space variants of git metadata that might
bypass _read_git_metadata_file but still be valid for git.

Also: gitdir with multiple lines (extra lines after the path).
"""
import json
import os
import shutil
import subprocess
import sys
sys.path.insert(0, "/home/harmful/agy-review/opus")
sys.path.insert(0, "~/agy-review/opus")
from pathlib import Path
from harness import *

SCRATCH = Path("~/agy-review/opus/scratch_13")

print("Attack 13: CRLF/whitespace variants in git metadata")

def test(name, mutate_fn):
    repo, store, job_dir, job_id, work, env = make_normal_repo_job(SCRATCH)
    entry_dir = repo / ".git" / "worktrees" / "work"
    gitdir_file = entry_dir / "gitdir"
    
    try:
        mutate_fn(gitdir_file, work)
    except Exception as e:
        print(f"  {name}: SETUP FAILED: {e}")
        return
    
    gw, gr, go = check_git(repo, work, env)
    ir, rr, ic, cm = check_cleaner(store, repo, job_dir, job_id)
    v = verdict(gw, gr, ic, cm)
    print(f"  {name}: git_works={gw} git_reg={gr} cleaner_reg={ir} cand={ic} → {v}")
    if "FAIL-OPEN" in v:
        print(f"    git: {go[:300]}")
        print(f"    reason: {rr}")
        print(f"    gitdir content: {gitdir_file.read_bytes()!r}")

mutations = {
    # Multiple trailing newlines
    "gitdir double newline": lambda f, w: f.write_bytes(
        str(w / ".git").encode() + b"\n\n"
    ),
    # Trailing CRLF
    "gitdir CRLF ending": lambda f, w: f.write_bytes(
        str(w / ".git").encode() + b"\r\n"
    ),
    # Double CRLF
    "gitdir double CRLF": lambda f, w: f.write_bytes(
        str(w / ".git").encode() + b"\r\n\r\n"
    ),
    # Leading whitespace
    "gitdir leading spaces": lambda f, w: f.write_bytes(
        b"  " + str(w / ".git").encode() + b"\n"
    ),
    # Leading tab
    "gitdir leading tab": lambda f, w: f.write_bytes(
        b"\t" + str(w / ".git").encode() + b"\n"
    ),
    # Trailing spaces before newline
    "gitdir trailing spaces": lambda f, w: f.write_bytes(
        str(w / ".git").encode() + b"   \n"
    ),
    # Path with trailing slash
    "gitdir trailing slash": lambda f, w: f.write_bytes(
        str(w / ".git").encode() + b"/\n"
    ),
    # No newline at end
    "gitdir no newline": lambda f, w: f.write_bytes(
        str(w / ".git").encode()
    ),
    # Extra line with comment
    "gitdir extra comment line": lambda f, w: f.write_bytes(
        str(w / ".git").encode() + b"\n# comment\n"
    ),
    # Multiple paths
    "gitdir two paths": lambda f, w: f.write_bytes(
        str(w / ".git").encode() + b"\n/tmp/other\n"
    ),
    # Unicode BOM at start
    "gitdir BOM": lambda f, w: f.write_bytes(
        b"\xef\xbb\xbf" + str(w / ".git").encode() + b"\n"
    ),
    # Lone CR
    "gitdir lone CR": lambda f, w: f.write_bytes(
        str(w / ".git").encode() + b"\r"
    ),
    # NUL in middle
    "gitdir NUL": lambda f, w: f.write_bytes(
        str(w / ".git").encode()[:5] + b"\x00" + str(w / ".git").encode()[5:] + b"\n"
    ),
    # Non-UTF-8 bytes
    "gitdir non-utf8": lambda f, w: f.write_bytes(
        str(w / ".git").encode() + b"\xff\xfe\n"
    ),
}

for name, fn in mutations.items():
    test(name, fn)
