#!/usr/bin/env python3
"""Attack 11: worktree_path=$HOME pointing at directories that exist with .git.

The _check_worktree_status code at L148-161 checks worktree_path/.git:
    worktree_path_val = data.get("worktree_path")
    if worktree_path_val:
        try:
            wt_git = Path(worktree_path_val) / ".git"
            if wt_git.exists() or wt_git.is_file() or wt_git.is_dir():
                return (True, "worktree registered ...")
        except OSError:
            return (True, "worktree registered ...")

So if worktree_path=$HOME and $HOME/.git exists, it would be KEPT.
If $HOME/.git does NOT exist, it falls through.

But wait — in Attack 8, worktree_path=$HOME was CANDIDATE.
That means $HOME/.git doesn't exist. That's fine — the cleaner correctly
decides the worktree_path doesn't have a .git, so it's not registered.

The question is: is worktree_path used for DELETION scope? No — 
worktree_path is only used in the registration check. The deletion
only targets the job_dir itself. So worktree_path=$HOME is not an
OUT-OF-SCOPE-DELETE risk.

Let me instead look for remaining json/utf-8-sig paths.
"""
import sys
sys.path.insert(0, "~/Documents/GITHUB PROJECTS/agy-mcp/src")
import ast
import os

# Scan all .py files under src/agy_mcp/ for json.loads or utf-8-sig usage
src_dir = "~/Documents/GITHUB PROJECTS/agy-mcp/src/agy_mcp"

print("Attack 11: Scan for json.loads calls that DON'T use _safe_json_loads")
print("=" * 70)

for fname in sorted(os.listdir(src_dir)):
    if not fname.endswith(".py"):
        continue
    fpath = os.path.join(src_dir, fname)
    with open(fpath, "r") as f:
        content = f.read()
    
    # Search for json.loads that is NOT _safe_json_loads
    lines = content.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if "json.loads" in stripped and "_safe_json_loads" not in stripped and "def _safe_json_loads" not in stripped:
            print(f"  {fname}:{i}: {stripped}")
        if "utf-8-sig" in stripped:
            print(f"  {fname}:{i}: [utf-8-sig] {stripped}")
        if "utf_8_sig" in stripped:
            print(f"  {fname}:{i}: [utf_8_sig] {stripped}")

print()
print("Attack 11b: Scan for .read_text() calls in cleanup_jobs.py")
cleanup_file = os.path.join(src_dir, "cleanup_jobs.py")
with open(cleanup_file) as f:
    for i, line in enumerate(f, 1):
        if ".read_text(" in line or ".read_bytes(" in line:
            print(f"  L{i}: {line.rstrip()}")
