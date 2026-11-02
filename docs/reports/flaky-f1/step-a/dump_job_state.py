#!/usr/bin/env python3
"""Dump the on-disk state of every job under a pytest --basetemp tree.

Used only for flaky-f1 evidence collection. Read-only.
"""

import hashlib
import json
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def tail(path: Path, lines: int = 40) -> str:
    if not path.is_file():
        return "<missing>"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"<unreadable: {exc}>"
    return "\n".join(text.splitlines()[-lines:])


def dump_job(job_dir: Path) -> None:
    print(f"=== JOB DIR: {job_dir}")
    job_json = job_dir / "job.json"
    if job_json.is_file():
        job = json.loads(job_json.read_text(encoding="utf-8"))
        summary = {
            key: job.get(key)
            for key in (
                "job_id",
                "state",
                "review_reason",
                "pid",
                "pgid",
                "run_generation",
                "conversation_id",
                "commits_count",
                "cumulative_minutes",
                "warnings",
                "plan_summary",
                "error",
            )
        }
        print("--- job.json summary (ordered):")
        print(json.dumps(summary, indent=2, sort_keys=False))
        worktree = job.get("worktree_path")
    else:
        print("--- job.json: MISSING")
        worktree = None

    audit = job_dir / "audit.jsonl"
    print(f"--- audit.jsonl exists={audit.is_file()} lines={0 if not audit.is_file() else len(audit.read_text(encoding='utf-8', errors='replace').splitlines())}")
    if audit.is_file():
        for idx, line in enumerate(audit.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
                print(f"  [{idx}] ts={ev.get('ts') or ev.get('timestamp')} event={ev.get('event')} trigger={ev.get('event_trigger') or ev.get('trigger')} detail={json.dumps(ev.get('detail'))}")
            except json.JSONDecodeError:
                print(f"  [{idx}] RAW {line}")

    print(f"--- cli.log tail ({job_dir / 'cli.log'}):")
    print(tail(job_dir / "cli.log"))
    print(f"--- stdout.txt tail ({job_dir / 'stdout.txt'}):")
    print(tail(job_dir / "stdout.txt"))
    print(f"--- stderr.txt tail ({job_dir / 'stderr.txt'}):")
    print(tail(job_dir / "stderr.txt"))

    if worktree:
        wt = Path(worktree)
        print(f"--- worktree: {wt} exists={wt.is_dir()}")
        agy = wt / ".agy"
        print(f"--- .agy dir exists={agy.is_dir()}")
        if agy.is_dir():
            for entry in sorted(agy.iterdir()):
                if entry.is_file():
                    print(
                        f"    {entry.name} size={entry.stat().st_size} "
                        f"sha256={sha256(entry)}"
                    )
                    if entry.name == "COMMIT_MSG" or entry.name.startswith("COMMIT_MSG.seen"):
                        print(f"      CONTENT: {entry.read_text(encoding='utf-8', errors='replace')!r}")
                else:
                    print(f"    {entry.name} (non-regular)")
        print(f"--- stale COMMIT_MSG present: {(agy / 'COMMIT_MSG').is_file()}")
        seen = sorted(agy.glob("COMMIT_MSG.seen.*")) if agy.is_dir() else []
        print(f"--- COMMIT_MSG.seen.* files: {[p.name for p in seen]}")
        print(f"--- .fake_agy_scenario: {(agy.parent / '.fake_agy_scenario').is_file()}")
        marker = job_dir / ".fake_agy_scenario"
        print(f"--- jobdir .fake_agy_scenario={marker.is_file()} content={marker.read_text(encoding='utf-8', errors='replace').strip() if marker.is_file() else ''}")


def main() -> None:
    root = Path(sys.argv[1])
    print(f"=== ROOT: {root}")
    jobs = sorted(root.glob("**/state/jobs/*"))
    if not jobs:
        print("NO JOB DIRS FOUND")
    for job_dir in jobs:
        if job_dir.is_dir():
            dump_job(job_dir)


if __name__ == "__main__":
    main()
