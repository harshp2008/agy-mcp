"""H27 follow-up: an over-long agent subject is repaired, not rejected.

The original H27 repro pinned the old behavior (an 80-character subject was
rejected twice and the work survived only in the pre-kill checkpoint commit).
The wrapper now repairs an over-long subject at a word boundary, keeps the full
original first line at the top of the body and commits it.

This test also pins task 7: agy's JSON ``duration_seconds`` is the conversation
age (the fake reports 999999s), so the wrapper must record its own wall-clock
runtime for the run/usage row, never agy's value.
"""

import json
import re
from pathlib import Path

from agy_mcp.config import Config
from agy_mcp.gitops import run_git
from agy_mcp.jobs import get_job
from agy_mcp.manager import JobManager
from agy_mcp.models import JobState
from agy_mcp.runner import LocalProcessRunner
from agy_mcp.util import strict_uuid
from tests.helpers.make_repo import make_repo

CONVERSATION = "abcdef01-2345-4678-9abc-def012345678"
ORIGINAL_SUBJECT = (
    "Implement groups for the H27 regression with a subject that is far too long"
)


def _write_stub(path: Path) -> None:
    """A tiny agy stand-in: dirty file, over-long COMMIT_MSG, slow clean exit."""
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys, time\n"
        "from pathlib import Path\n"
        "# The wrapper probes usage with the same binary; answer that fast so the\n"
        "# test does not wait out the usage timeout.\n"
        "if '/usage' in sys.argv:\n"
        "    sys.exit(1)\n"
        "work = Path.cwd()\n"
        "(work / 'dirty_for_repair.py').write_text('# repaired\\n', encoding='utf-8')\n"
        "agy = work / '.agy'\n"
        "agy.mkdir(exist_ok=True)\n"
        "plan = agy / 'PLAN.md'\n"
        "if plan.is_file():\n"
        "    plan.write_text(plan.read_text().replace('[ ]', '[x]'))\n"
        f"(agy / 'COMMIT_MSG').write_text({ORIGINAL_SUBJECT!r} + '\\n\\nThe body.\\n',"
        " encoding='utf-8')\n"
        "print(json.dumps({'conversation_id': "
        f"{CONVERSATION!r}, 'status': 'SUCCESS',"
        " 'response': 'done',"
        " 'duration_seconds': 999999.0,"
        " 'usage': {'input_tokens': 100, 'output_tokens': 50, 'total_tokens': 150}}))\n"
        "time.sleep(1.2)\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _audit_events(job_dir: Path) -> list[dict]:
    path = job_dir / "audit.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_h27_overlong_subject_is_repaired_and_committed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo", initial_commit=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    stub = tmp_path / "agy-stub"
    _write_stub(stub)

    manager = JobManager(
        config=Config(max_concurrent_jobs=2, daily_jobs_cap=5),
        runner=LocalProcessRunner(executable=stub),
        base_state_dir=state_dir,
        home=tmp_path,
        settle_window_seconds=0.1,
        poll_interval_seconds=0.05,
    )
    env = manager.start(
        prompt="Implement groups",
        repo_path=str(repo),
        title="H27 Repair Repro",
    )
    try:
        res = manager.wait(env.job_id, seconds=20)
        job = get_job(env.job_id, state_dir)

        # The repaired message is committed; the job is not sent to review.
        assert res.state == JobState.DONE, res
        assert job.review_reason is None
        assert job.commits_count >= 1
        assert strict_uuid(job.conversation_id) == CONVERSATION

        # Subject <= 72 chars, truncated at a word boundary; the full original
        # first line is preserved at the top of the body.
        raw_message = run_git(
            ["show", "-s", "--format=%B", "HEAD"],
            job.worktree_path,
            check=True,
        ).stdout
        lines = raw_message.splitlines()
        subject = lines[0]
        assert len(subject) <= 72, subject
        assert subject in ORIGINAL_SUBJECT
        assert not subject.endswith(" ")
        assert ORIGINAL_SUBJECT in lines[2:], raw_message
        assert "The body." in raw_message

        events = _audit_events(Path(state_dir) / "jobs" / env.job_id)
        repaired = [event for event in events if event.get("event") == "commit_msg_repaired"]
        assert len(repaired) == 1, events
        assert repaired[0]["detail"] == {"original_len": len(ORIGINAL_SUBJECT)}

        # Task 7: the wrapper's own wall-clock is recorded for the run; agy's
        # 999999s conversation age is kept only as a diagnostic field.
        assert job.runs, job.runs
        run = job.runs[-1]
        assert run["agy_duration_seconds"] == 999999.0
        assert 0.0 <= run["wall_clock_seconds"] < 60.0
        assert run["tokens"]["total_tokens"] == 150

        # Task 8: per-run tokens reach job.json and the quota estimate.
        assert job.tokens.total_tokens == 150
        assert job.quota_estimate is not None
        assert job.quota_estimate["tokens"] == 150
        assert job.quota_estimate["estimated"] is True
        # Calibrated 2026-10-31 (docs/reports/quota-calibration-data.md): the weekly
        # coefficient is 0.0031 points per 1k tokens (was 0.00075).
        assert job.quota_estimate["weekly_pct_points"] == round(150 / 1000 * 0.0031, 4)

        # The usage row also carries the wrapper wall-clock, not the age.
        usage_path = Path(state_dir) / "usage.jsonl"
        rows = [json.loads(line) for line in usage_path.read_text().splitlines() if line]
        own_rows = [row for row in rows if row["job_id"] == env.job_id]
        assert own_rows
        assert own_rows[-1]["duration_seconds"] < 60.0
        assert not re.search(r"\b999999\b", json.dumps(own_rows[-1]))
    finally:
        current = get_job(env.job_id, state_dir)
        if current.state in (JobState.QUEUED, JobState.RUNNING):
            manager.cancel(env.job_id)
