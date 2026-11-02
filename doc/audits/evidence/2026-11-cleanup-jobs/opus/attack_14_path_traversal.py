#!/usr/bin/env python3
"""Attack 14: Path traversal in --job IDs.

Theory: What if --job is passed an ID like "../../../etc" or a UUID that
contains path separators? The code validates with _is_canonical_uuid() which
requires exactly the UUID pattern, so path traversal in IDs should be blocked.
But let's verify via the run_cleanup API directly.
"""
import sys
sys.path.insert(0, "~/Documents/GITHUB PROJECTS/agy-mcp/src")
from pathlib import Path
from agy_mcp.cleanup_jobs import run_cleanup
import json, os, shutil

SCRATCH = Path("~/agy-review/opus/scratch_14")

def safe_rmtree(p):
    if not p.exists(): return
    for root, dirs, files in os.walk(p, topdown=False):
        for f in files:
            try: os.chmod(Path(root)/f, 0o600)
            except: pass
        for d in dirs:
            try: os.chmod(Path(root)/d, 0o700)
            except: pass
    shutil.rmtree(p)

safe_rmtree(SCRATCH)
SCRATCH.mkdir(parents=True)

store = SCRATCH / "store"
store_jobs = store / "jobs"
store_jobs.mkdir(parents=True)

# Create a valid job
job_id = "11111111-1111-1111-1111-111111111111"
job_dir = store_jobs / job_id
job_dir.mkdir()
(job_dir / "job.json").write_text(json.dumps({
    "job_id": job_id, "state": "done", "repo_path": "/tmp/nonexistent",
    "created_at": "2020-01-01T00:00:00Z", "updated_at": "2020-01-01T00:00:00Z",
}))

print("Attack 14: Path traversal in --job IDs")

traversal_ids = [
    "../../../etc",
    "../../..",
    "11111111-1111-1111-1111-111111111111/../../..",
    "..",
    ".",
    "/etc/passwd",
    "11111111-1111-1111-1111-11111111111\x00",
    "11111111-1111-1111-1111-111111111111\n22222222",
]

for tid in traversal_ids:
    try:
        result = run_cleanup(store, job_ids=[tid], dry_run=True)
        print(f"  '{tid[:40]}': returned (unexpected!) candidates={result.get('candidates', [])}")
    except ValueError as e:
        print(f"  '{tid[:40]}': ValueError: {str(e)[:60]}")
    except Exception as e:
        print(f"  '{tid[:40]}': {type(e).__name__}: {str(e)[:60]}")

# Also test the CLI main with path traversal
print("\nAttack 14b: CLI main with path traversal")
from agy_mcp.cleanup_jobs import main as cli_main
import io
for tid in ["../../../etc", ".", ".."]:
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        rc = cli_main(["--store", str(store), "--job", tid])
        err = sys.stderr.getvalue()
        print(f"  '{tid}': exit={rc}", file=old_stderr)
    except Exception as e:
        print(f"  '{tid}': CRASH {e}", file=old_stderr)
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
