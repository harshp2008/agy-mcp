"""Helpers for the human account CLI tests (D117).

Everything here is fake: a tiny ``agy`` script, a config writer and job records.
It must never touch the operator's real HOME or run the real agy.
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Optional

from agy_mcp.jobs import save_job
from agy_mcp.models import Job, JobState

# Clearly fake, allowlisted domain; the CLI tests assert it never reaches output.
FAKE_EMAIL = "planted.person@example.com"
OTHER_EMAIL = "other.person@example.com"


def usage_payload(weekly: float = 0.8, five_hour: float = 0.9) -> dict:
    """Build a minimal /usage payload the fake agy prints."""
    return {
        "command": {
            "data": {
                "groups": [
                    {
                        "name": "Gemini Models",
                        "buckets": [
                            {
                                "window": "weekly",
                                "remaining_fraction": weekly,
                                "reset_time": "2026-11-10T00:00:00Z",
                            },
                            {
                                "window": "5h",
                                "remaining_fraction": five_hour,
                                "reset_time": "2026-11-05T08:00:00Z",
                            },
                        ],
                    }
                ]
            }
        },
        "response": (
            "Gemini Models\tWeekly\t80%\t2026-11-10T00:00:00Z\n"
            "Gemini Models\t5-hour\t90%\t2026-11-05T08:00:00Z\n"
        ),
    }


def write_fake_agy(
    path: Path,
    *,
    leak: bool = False,
    email: str = FAKE_EMAIL,
    invocation_marker: Optional[Path] = None,
    weekly: float = 0.8,
    five_hour: float = 0.9,
) -> Path:
    """Write a fake agy that authenticates per-home and can simulate a keyring leak.

    A home is signed in when it contains ``.fake_logged_in``. With ``leak=True``
    the ``/usage`` probe returns quota rows even for a fresh home, which is the
    isolation failure the add flow must abort on.
    """
    payload = usage_payload(weekly=weekly, five_hour=five_hour)
    marker = repr(str(invocation_marker)) if invocation_marker is not None else "None"
    script = f'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

EMAIL = {email!r}
LEAK = {leak!r}
MARKER = {marker}
PAYLOAD = {payload!r}

if MARKER:
    Path(MARKER).write_text("called", encoding="utf-8")

home = Path(os.environ["HOME"])

if "-p" in sys.argv and "/usage" in sys.argv:
    if LEAK or (home / ".fake_logged_in").is_file():
        if "--log-file" in sys.argv:
            log = Path(sys.argv[sys.argv.index("--log-file") + 1])
            log.write_text(
                "applyAuthResult: email=" + EMAIL + ", authMethod=consumer\\n",
                encoding="utf-8",
            )
        print(json.dumps(PAYLOAD))
        sys.exit(0)
    sys.stderr.write("Authentication required. Please visit the URL to log in:\\n")
    sys.exit(1)

(home / ".fake_logged_in").write_text("1", encoding="utf-8")
sys.exit(0)
'''
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)
    return path


def write_config(path: Path, text: str) -> Path:
    """Write a config.toml under a private parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(text, encoding="utf-8")
    return path


def make_job(
    state_dir: Path,
    *,
    account: Optional[str],
    state: JobState,
    title: str = "A job",
    job_id: Optional[str] = None,
    review_reason: Optional[str] = None,
) -> Job:
    """Persist one job record pinned to ``account`` without starting a process."""
    job = Job(
        job_id=job_id or f"job-{title.lower().replace(' ', '-')}",
        title=title,
        repo_path=Path("/tmp/example-repo"),
        branch="agy/example-0123abcd",
        base_commit="HEAD",
        state=state,
        account=account,
        review_reason=review_reason,
    )
    save_job(job, state_dir)
    return job


def tree_snapshot(*roots: Path) -> dict[str, tuple[int, int, bytes]]:
    """Snapshot paths, modes, mtimes and file bytes under ``roots``."""
    snapshot: dict[str, tuple[int, int, bytes]] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            info = path.lstat()
            data = b""
            if stat.S_ISREG(info.st_mode):
                data = path.read_bytes()
            snapshot[str(path)] = (stat.S_IMODE(info.st_mode), info.st_mtime_ns, data)
        info = root.lstat()
        snapshot[str(root)] = (stat.S_IMODE(info.st_mode), info.st_mtime_ns, b"")
    return snapshot
