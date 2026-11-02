"""Multiprocessing concurrency tests using spawn start method per SPEC section 17 & F36."""

import json
import multiprocessing as mp
from pathlib import Path

from agy_mcp.jobs import check_and_increment_daily_cap
from agy_mcp.models import Job
from agy_mcp.states import JobState
from agy_mcp.usage import record_usage

# Top-level worker functions required by spawn start method


def _proc_daily_cap_worker(state_dir_str: str, daily_cap: int, attempts: int, out_queue: mp.Queue):
    """Child process worker attempting daily cap increments."""
    state_dir = Path(state_dir_str)
    successes = 0
    failures = 0
    for _ in range(attempts):
        res = check_and_increment_daily_cap(state_dir, daily_cap=daily_cap)
        if res:
            successes += 1
        else:
            failures += 1
    out_queue.put((successes, failures))


def _proc_usage_worker(usage_file_str: str, proc_id: int, count: int, out_queue: mp.Queue):
    """Child process worker recording usage summaries under lock."""
    usage_file = Path(usage_file_str)
    for i in range(count):
        job = Job(
            job_id=f"mp-proc-{proc_id}-job-{i}",
            title=f"Task {proc_id}-{i}",
            repo_path=Path(f"/tmp/repo-{proc_id}-{i}"),
            branch=f"agy/branch-{proc_id}-{i}",
            base_commit="commit_sha",
            state=JobState.DONE,
            model="gemini-3.8-flash-low",
            commits_count=i,
            cumulative_minutes=float(i + 1),
        )
        record_usage(
            usage_path=usage_file,
            job=job,
            duration_seconds=float((i + 1) * 5),
            tokens={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        )
    out_queue.put(count)


def test_multiprocess_daily_cap_concurrency(tmp_path: Path):
    """4 separate processes spawned via multiprocessing increment daily cap under flock."""
    ctx = mp.get_context("spawn")
    state_dir = tmp_path / "mp_state"
    daily_cap = 10
    attempts_per_proc = 10
    num_procs = 4

    out_queue = ctx.Queue()
    procs = []

    for _ in range(num_procs):
        p = ctx.Process(
            target=_proc_daily_cap_worker,
            args=(str(state_dir), daily_cap, attempts_per_proc, out_queue),
        )
        p.start()
        procs.append(p)

    for p in procs:
        p.join(timeout=10)
        assert p.exitcode == 0

    total_success = 0
    total_failures = 0
    while not out_queue.empty():
        succ, fail = out_queue.get()
        total_success += succ
        total_failures += fail

    # Cap must never be exceeded
    assert total_success == daily_cap
    assert total_failures == (num_procs * attempts_per_proc) - daily_cap

    # Subsequent increment check must return False
    assert check_and_increment_daily_cap(state_dir, daily_cap=daily_cap) is False


def test_multiprocess_usage_log_no_torn_lines(tmp_path: Path):
    """4 separate processes write usage.jsonl concurrently without tearing or interleaving."""
    ctx = mp.get_context("spawn")
    usage_file = tmp_path / "usage.jsonl"
    num_procs = 4
    entries_per_proc = 15
    total_expected = num_procs * entries_per_proc

    out_queue = ctx.Queue()
    procs = []

    for proc_id in range(num_procs):
        p = ctx.Process(
            target=_proc_usage_worker,
            args=(str(usage_file), proc_id, entries_per_proc, out_queue),
        )
        p.start()
        procs.append(p)

    for p in procs:
        p.join(timeout=10)
        assert p.exitcode == 0

    assert usage_file.is_file()
    lines = [ln.strip() for ln in usage_file.read_text(encoding="utf-8").splitlines() if ln.strip()]

    # Exactly total_expected lines written
    assert len(lines) == total_expected

    # Every single line must be valid JSON with correct structure (no torn lines)
    seen_job_ids = set()
    for line in lines:
        record = json.loads(line)
        jid = record["job_id"]
        assert jid not in seen_job_ids
        seen_job_ids.add(jid)
        assert "prompt" not in record
        assert record["tokens"]["total_tokens"] == 150

    assert len(seen_job_ids) == total_expected
