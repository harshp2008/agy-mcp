#!/usr/bin/env python3
"""Attack 8: job.json wrong types, null, huge ints, unicode lookalike IDs,
path traversal in IDs, worktree_path attacks."""
import json
import os
import sys
sys.path.insert(0, "~/agy-review/opus")
sys.path.insert(0, "~/Documents/GITHUB PROJECTS/agy-mcp/src")
from pathlib import Path
from harness import safe_rmtree, get_env

from agy_mcp.cleanup_jobs import run_cleanup, _check_worktree_status

SCRATCH = Path("~/agy-review/opus/scratch_08")

def make_store_with_job(scratch, job_id, job_data):
    safe_rmtree(scratch)
    scratch.mkdir(parents=True)
    store = scratch / "store"
    store_jobs = store / "jobs"
    store_jobs.mkdir(parents=True)
    job_dir = store_jobs / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "job.json").write_text(json.dumps(job_data, indent=2), encoding="utf-8")
    return store, job_dir

def test_classify(name, job_id, job_data, repo_path_str=None):
    """Test if a job with given data gets classified correctly."""
    s = SCRATCH / name.replace(" ", "_").replace("/", "_")
    store, job_dir = make_store_with_job(s, job_id, job_data)
    rp = repo_path_str or job_data.get("repo_path", "/tmp/nonexistent")
    
    # Create the repo dir if needed
    repo = Path(rp)
    if not repo.exists():
        repo.mkdir(parents=True, exist_ok=True)
        (repo / ".git").mkdir(exist_ok=True)
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    
    try:
        result = run_cleanup(store, project=rp, dry_run=True, older_than_days=0.0)
        cand_ids = [c["job_id"] for c in result.get("candidates", [])]
        is_candidate = job_id in cand_ids
        crash = False
        crash_msg = ""
    except Exception as exc:
        is_candidate = False
        crash = True
        crash_msg = str(exc)
    
    status = "CRASH" if crash else ("CANDIDATE" if is_candidate else "KEPT")
    print(f"  {name}: {status}", end="")
    if crash:
        print(f" ({crash_msg})")
    else:
        print()
    return status, crash_msg

print("Attack 8: job.json type attacks")
base_id = "11111111-1111-1111-1111-111111111111"
base_repo = str(SCRATCH / "repo")

# Wrong types for various fields
tests = {
    "state=integer": {"job_id": base_id, "state": 42, "repo_path": base_repo,
                       "created_at": "2020-01-01T00:00:00Z", "updated_at": "2020-01-01T00:00:00Z"},
    "state=null": {"job_id": base_id, "state": None, "repo_path": base_repo,
                    "created_at": "2020-01-01T00:00:00Z", "updated_at": "2020-01-01T00:00:00Z"},
    "state=list": {"job_id": base_id, "state": ["done"], "repo_path": base_repo,
                    "created_at": "2020-01-01T00:00:00Z", "updated_at": "2020-01-01T00:00:00Z"},
    "pid=huge_int": {"job_id": base_id, "state": "done", "repo_path": base_repo,
                      "pid": 99999999999999999999, "created_at": "2020-01-01T00:00:00Z",
                      "updated_at": "2020-01-01T00:00:00Z"},
    "pid=negative": {"job_id": base_id, "state": "done", "repo_path": base_repo,
                      "pid": -1, "created_at": "2020-01-01T00:00:00Z",
                      "updated_at": "2020-01-01T00:00:00Z"},
    "pid=float": {"job_id": base_id, "state": "done", "repo_path": base_repo,
                   "pid": 3.14, "created_at": "2020-01-01T00:00:00Z",
                   "updated_at": "2020-01-01T00:00:00Z"},
    "pid=string": {"job_id": base_id, "state": "done", "repo_path": base_repo,
                    "pid": "1234", "created_at": "2020-01-01T00:00:00Z",
                    "updated_at": "2020-01-01T00:00:00Z"},
    "pid=true": {"job_id": base_id, "state": "done", "repo_path": base_repo,
                  "pid": True, "created_at": "2020-01-01T00:00:00Z",
                  "updated_at": "2020-01-01T00:00:00Z"},
    "pid=zero": {"job_id": base_id, "state": "done", "repo_path": base_repo,
                  "pid": 0, "created_at": "2020-01-01T00:00:00Z",
                  "updated_at": "2020-01-01T00:00:00Z"},
    "repo_path=integer": {"job_id": base_id, "state": "done", "repo_path": 42,
                           "created_at": "2020-01-01T00:00:00Z",
                           "updated_at": "2020-01-01T00:00:00Z"},
    "repo_path=null": {"job_id": base_id, "state": "done", "repo_path": None,
                        "created_at": "2020-01-01T00:00:00Z",
                        "updated_at": "2020-01-01T00:00:00Z"},
    "repo_path=list": {"job_id": base_id, "state": "done", "repo_path": ["/tmp"],
                        "created_at": "2020-01-01T00:00:00Z",
                        "updated_at": "2020-01-01T00:00:00Z"},
    "repo_path=empty": {"job_id": base_id, "state": "done", "repo_path": "",
                          "created_at": "2020-01-01T00:00:00Z",
                          "updated_at": "2020-01-01T00:00:00Z"},
    "repo_path=spaces": {"job_id": base_id, "state": "done", "repo_path": "   ",
                           "created_at": "2020-01-01T00:00:00Z",
                           "updated_at": "2020-01-01T00:00:00Z"},
    "created_at=integer": {"job_id": base_id, "state": "done", "repo_path": base_repo,
                            "created_at": 1234567890},
    "created_at=null": {"job_id": base_id, "state": "done", "repo_path": base_repo,
                         "created_at": None},
    "updated_at=integer": {"job_id": base_id, "state": "done", "repo_path": base_repo,
                            "created_at": "2020-01-01T00:00:00Z", "updated_at": 999},
    "job_id=wrong_case": {"job_id": base_id.upper(), "state": "done", "repo_path": base_repo,
                           "created_at": "2020-01-01T00:00:00Z",
                           "updated_at": "2020-01-01T00:00:00Z"},
    "title=huge_string": {"job_id": base_id, "state": "done", "repo_path": base_repo,
                           "title": "A" * 500000,
                           "created_at": "2020-01-01T00:00:00Z",
                           "updated_at": "2020-01-01T00:00:00Z"},
}

for name, data in tests.items():
    # For non-string repo_path, use a default valid repo
    rp = data.get("repo_path") if isinstance(data.get("repo_path"), str) and data["repo_path"].strip() else base_repo
    test_classify(name, base_id, data, rp)

# Unicode lookalike ID
print("\nAttack 8b: Unicode lookalike UUIDs")
# U+FF11 = fullwidth digit 1
lookalike_id = "11111111-1111-1111-1111-11111111111\uff11"
try:
    result = test_classify("unicode_lookalike_uuid", lookalike_id,
        {"job_id": lookalike_id, "state": "done", "repo_path": base_repo,
         "created_at": "2020-01-01T00:00:00Z", "updated_at": "2020-01-01T00:00:00Z"},
        base_repo)
except Exception as e:
    print(f"  unicode_lookalike: {e}")

# Path traversal in worktree_path
print("\nAttack 8c: worktree_path pointing at $HOME or dangerous paths")
home = os.environ.get("HOME", "~")
dangerous_paths = {
    "worktree_path=$HOME": home,
    "worktree_path=/": "/",
    "worktree_path=repo_path": base_repo,
    "worktree_path=store": str(SCRATCH / "store"),
    "worktree_path=../..": "../..",
    "worktree_path=with_null": f"/tmp/foo\x00bar",
}

for name, wtp in dangerous_paths.items():
    data = {
        "job_id": base_id, "state": "done", "repo_path": base_repo,
        "worktree_path": wtp,
        "created_at": "2020-01-01T00:00:00Z", "updated_at": "2020-01-01T00:00:00Z",
    }
    test_classify(name, base_id, data, base_repo)
