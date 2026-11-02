"""Unit tests for the shared job.json loader (audit C3a).

``load_job_json`` replaced five hand-written "stat -> 1 MiB cap -> read -> safe
parse" copies in ``cleanup_jobs.py``. These tests pin the failure taxonomy the
call sites map back to their original reason strings, plus the two properties
the audit called out: the cap is the same value as ``jobs.MAX_JOB_FILE_BYTES``,
and a non-regular file (FIFO, directory) is rejected without ever being opened,
so the cleaner cannot block on it.
"""

import json
import os

import pytest

from agy_mcp.cleanup_jobs import (
    MAX_JOB_JSON_BYTES,
    JobJsonProblem,
    load_job_json,
)
from agy_mcp.jobs import MAX_JOB_FILE_BYTES


def test_cap_matches_the_server_job_json_cap() -> None:
    """The cleaner must not drift from the server's job.json cap again."""
    assert MAX_JOB_JSON_BYTES == MAX_JOB_FILE_BYTES


def test_valid_object_loads(tmp_path) -> None:
    """A plain object is returned unchanged with no failure."""
    path = tmp_path / "job.json"
    path.write_text(json.dumps({"job_id": "x", "state": "done"}), encoding="utf-8")

    data, failure = load_job_json(path)

    assert failure is None
    assert data == {"job_id": "x", "state": "done"}


def test_non_object_root_is_returned_for_the_caller_to_judge(tmp_path) -> None:
    """The loader must not collapse a non-object root into a failure code."""
    path = tmp_path / "job.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    data, failure = load_job_json(path)

    assert failure is None
    assert data == [1, 2, 3]


def test_exactly_at_the_cap_is_allowed(tmp_path) -> None:
    """The cap is a maximum: a record of exactly MAX bytes still loads."""
    path = tmp_path / "job.json"
    body = b"{}" + b" " * (MAX_JOB_JSON_BYTES - 2)
    assert len(body) == MAX_JOB_JSON_BYTES
    path.write_bytes(body)

    data, failure = load_job_json(path)

    assert failure is None
    assert data == {}


def test_one_byte_over_the_cap_is_too_large(tmp_path) -> None:
    """One byte past the cap is refused before parsing."""
    path = tmp_path / "job.json"
    path.write_bytes(b"{}" + b" " * (MAX_JOB_JSON_BYTES - 1))

    data, failure = load_job_json(path)

    assert data is None
    assert failure is not None
    assert failure.problem is JobJsonProblem.TOO_LARGE


def test_directory_is_not_regular(tmp_path) -> None:
    """A directory swapped in for job.json is refused without being read."""
    path = tmp_path / "job.json"
    path.mkdir()

    data, failure = load_job_json(path)

    assert data is None
    assert failure is not None
    assert failure.problem is JobJsonProblem.NOT_REGULAR
    assert failure.detail == "job.json is not a regular file"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="os.mkfifo is required")
def test_fifo_is_rejected_without_blocking(tmp_path) -> None:
    """stat() runs first, so a FIFO must be classified, not opened (R10/R11)."""
    path = tmp_path / "job.json"
    os.mkfifo(path)

    data, failure = load_job_json(path)

    assert data is None
    assert failure is not None
    assert failure.problem is JobJsonProblem.NOT_REGULAR


def test_missing_file_is_unreadable(tmp_path) -> None:
    """A missing record reports the stat error text for the caller's message."""
    data, failure = load_job_json(tmp_path / "missing.json")

    assert data is None
    assert failure is not None
    assert failure.problem is JobJsonProblem.UNREADABLE
    assert failure.detail


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        (b"{", "truncated JSON"),
        (b'{"a": 1, "a": 2}', "duplicate keys"),
        (b"\xff\xfe{}", "invalid UTF-8"),
    ],
)
def test_unparseable_records_are_unreadable(tmp_path, raw: bytes, why: str) -> None:
    """Parse, duplicate-key and decode failures all map to UNREADABLE."""
    path = tmp_path / "job.json"
    path.write_bytes(raw)

    data, failure = load_job_json(path)

    assert data is None, why
    assert failure is not None, why
    assert failure.problem is JobJsonProblem.UNREADABLE, why
