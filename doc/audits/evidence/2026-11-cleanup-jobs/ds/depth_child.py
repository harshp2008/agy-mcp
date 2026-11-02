"""Child probe for the pre-delete recheck scenario (runs at DEFAULT recursion limit).

usage: depth_child.py <repo_src> <store> <project> <mode: jobs|fixtures>

Classifies a normal candidate once, then rewrites its job.json into a
100000-level nested bomb through the cleaner's own before_delete hook, exactly
where the pre-delete recheck happens. Prints a JSON summary of the report.
"""

import json
import sys
from pathlib import Path

REPO_SRC, STORE, PROJECT, MODE = sys.argv[1:5]
sys.path.insert(0, REPO_SRC)

DEPTH = 100_000
BOMB = '{"k":' * DEPTH + '"leaf"' + '}' * DEPTH
JOB_FMT = (
    '{"job_id":"%s","state":"done","repo_path":"%s",'
    '"created_at":"2020-01-01T00:00:00+00:00","nested":%s}'
)


def mutate(job_id: str) -> None:
    path = Path(STORE) / "jobs" / job_id / "job.json"
    path.write_text(JOB_FMT % (job_id, PROJECT, BOMB), encoding="utf-8")


if MODE == "jobs":
    from agy_mcp.cleanup_jobs import run_cleanup

    report = run_cleanup(STORE, project=PROJECT, dry_run=False, before_delete=mutate)
else:
    from agy_mcp.cleanup_fixtures import run_cleanup

    report = run_cleanup(Path(STORE), dry_run=False, before_delete=mutate)

print(
    json.dumps(
        {
            "recursion_limit": sys.getrecursionlimit(),
            "deleted_count": report["deleted_count"],
            "failed_ids": report["failed_ids"],
            "skipped": report["skipped"],
        }
    )
)
