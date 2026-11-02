"""Unit and fixture tests for agy transcript reader per SPEC sections 14, 15, and D50."""

import json
import shutil
from pathlib import Path

from agy_mcp.models import unwrap_untrusted_text
from agy_mcp.transcript import (
    cli_step_to_transcript_step,
    read_denied_calls,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "sanitized_transcripts"


def _setup_mock_agy_home(base_dir: Path, cid: str, fixture_name: str) -> Path:
    """Set up <agy_home>/brain/<cid>/.system_generated/logs/transcript.jsonl using a fixture."""
    agy_home = base_dir / "antigravity-cli"
    transcript_dest = agy_home / "brain" / cid / ".system_generated" / "logs" / "transcript.jsonl"
    transcript_dest.parent.mkdir(parents=True, exist_ok=True)
    fixture_src = FIXTURES_DIR / fixture_name
    shutil.copyfile(fixture_src, transcript_dest)
    return agy_home


def test_step_mapping_rule() -> None:
    """cli_step_to_transcript_step maps step N to step_index N - 1."""
    assert cli_step_to_transcript_step(6) == 5
    assert cli_step_to_transcript_step(8) == 7
    assert cli_step_to_transcript_step(1) == 0
    assert "Empirical Evidence" in (cli_step_to_transcript_step.__doc__ or "")


def test_s3_real_fixture(tmp_path: Path) -> None:
    """S3 transcript yields command 'cat README.md' at step 6 (CLI numbering)."""
    cid = "9d4181aa-13a5-4ff0-87ac-38c1db0a99b3"
    agy_home = _setup_mock_agy_home(tmp_path, cid, f"{cid}.jsonl")
    job_dir = tmp_path / "job_s3"
    job_dir.mkdir()

    calls = read_denied_calls(agy_home, cid, steps=[6], job_dir=job_dir)
    assert len(calls) == 1
    call = calls[0]
    assert call.step == 6
    assert call.tool == "run_command"
    assert call.command is not None
    assert unwrap_untrusted_text(call.command) == "cat README.md"
    assert call.summary == "Run cat README.md"


def test_s1_real_fixture(tmp_path: Path) -> None:
    """S1 transcript yields command 'find src tests' at step 8 (CLI numbering)."""
    cid = "3681aaa8-4f93-46d6-8d1c-3ca36637ad52"
    agy_home = _setup_mock_agy_home(tmp_path, cid, f"{cid}.jsonl")
    job_dir = tmp_path / "job_s1"
    job_dir.mkdir()

    calls = read_denied_calls(agy_home, cid, steps=[8], job_dir=job_dir)
    assert len(calls) == 1
    call = calls[0]
    assert call.step == 8
    assert call.tool == "run_command"
    assert call.command is not None
    assert unwrap_untrusted_text(call.command) == "find src tests"
    assert call.summary == "List files in src and tests"


def test_h2_parent_and_subagent_fixtures(tmp_path: Path) -> None:
    """H2: Subagent transcript holds the denied call (view_file on gitdir at step 8)."""
    parent_cid = "df6a0385-f2fe-453c-b1d9-61161e5100b8"
    subagent_cid = "55c62dde-8307-484a-bc8a-4e954192ba01"

    agy_home = _setup_mock_agy_home(tmp_path, parent_cid, f"{parent_cid}.jsonl")
    _setup_mock_agy_home(tmp_path, subagent_cid, f"{subagent_cid}.jsonl")
    job_dir = tmp_path / "job_h2"
    job_dir.mkdir()

    # The subagent transcript holds the denied call at step 8 as identified in A2
    subagent_calls = read_denied_calls(agy_home, subagent_cid, steps=[8], job_dir=job_dir)
    assert len(subagent_calls) == 1
    call = subagent_calls[0]
    assert call.step == 8
    assert call.tool == "view_file"
    assert call.path is not None
    unwrapped_path = unwrap_untrusted_text(call.path)
    assert unwrapped_path == "/home/user/agy-smoke/repo/.git/worktrees/work1/gitdir"


def test_synthetic_empty_file(tmp_path: Path) -> None:
    """Empty transcript file returns [] cleanly without exceptions."""
    cid = "00000000-0000-0000-0000-000000000001"
    agy_home = tmp_path / "antigravity-cli"
    t_file = agy_home / "brain" / cid / ".system_generated" / "logs" / "transcript.jsonl"
    t_file.parent.mkdir(parents=True, exist_ok=True)
    t_file.write_text("")

    calls = read_denied_calls(agy_home, cid, steps=[1, 2])
    assert calls == []


def test_synthetic_malformed_and_binary_garbage(tmp_path: Path) -> None:
    """Tolerate malformed lines and binary garbage without raising."""
    cid = "00000000-0000-0000-0000-000000000002"
    agy_home = tmp_path / "antigravity-cli"
    t_file = agy_home / "brain" / cid / ".system_generated" / "logs" / "transcript.jsonl"
    t_file.parent.mkdir(parents=True, exist_ok=True)

    valid_step = json.dumps(
        {
            "step_index": 3,
            "type": "PLANNER_RESPONSE",
            "tool_calls": [
                {
                    "name": "run_command",
                    "args": {"CommandLine": '"pytest -v"'},
                }
            ],
        }
    )
    garbage_content = (
        b"\x00\xff\xfe\x01\x02\n{not json\n" + valid_step.encode("utf-8") + b"\n\x80\x81\x82\n"
    )
    t_file.write_bytes(garbage_content)

    calls = read_denied_calls(agy_home, cid, steps=[4])
    assert len(calls) == 1
    assert calls[0].step == 4
    assert calls[0].tool == "run_command"
    assert unwrap_untrusted_text(calls[0].command) == "pytest -v"


def test_synthetic_oversized_file_tail_read(tmp_path: Path) -> None:
    """File > 5 MB triggers tail-read of last 2 MB and drops partial line."""
    cid = "00000000-0000-0000-0000-000000000003"
    agy_home = tmp_path / "antigravity-cli"
    t_file = agy_home / "brain" / cid / ".system_generated" / "logs" / "transcript.jsonl"
    t_file.parent.mkdir(parents=True, exist_ok=True)

    # Write 6 MB of padding followed by valid step at end
    valid_step = (
        json.dumps(
            {
                "step_index": 9,
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {
                        "name": "run_command",
                        "args": {"CommandLine": '"tail -n 10 file.txt"'},
                    }
                ],
            }
        )
        + "\n"
    )

    chunk = b"x" * 1024 * 1024
    with open(t_file, "wb") as f:
        for _ in range(6):
            f.write(chunk)
        f.write(b"\n")
        f.write(valid_step.encode("utf-8"))

    assert t_file.stat().st_size > 5 * 1024 * 1024

    calls = read_denied_calls(agy_home, cid, steps=[10])
    assert len(calls) == 1
    assert calls[0].step == 10
    assert unwrap_untrusted_text(calls[0].command) == "tail -n 10 file.txt"


def test_safety_symlink_escape_rejected(tmp_path: Path) -> None:
    """Symlink escape is rejected and records transcript_unreadable audit event."""
    cid = "00000000-0000-0000-0000-000000000004"
    agy_home = tmp_path / "antigravity-cli"
    brain = agy_home / "brain"
    brain.mkdir(parents=True, exist_ok=True)

    outside_dir = tmp_path / "outside"
    outside_file = outside_dir / ".system_generated" / "logs" / "transcript.jsonl"
    outside_file.parent.mkdir(parents=True, exist_ok=True)
    outside_file.write_text(
        '{"step_index": 0, "type": "PLANNER_RESPONSE", '
        '"tool_calls": [{"name": "run_command", "args": {"CommandLine": "\\"pwd\\""}}]}\n'
    )

    # Create symlink pointing outside brain
    escaped_target = brain / cid
    escaped_target.symlink_to(outside_dir, target_is_directory=True)

    job_dir = tmp_path / "job_symlink"
    job_dir.mkdir()

    calls = read_denied_calls(agy_home, cid, steps=[1], job_dir=job_dir)
    assert calls == []

    audit_file = job_dir / "audit.jsonl"
    assert audit_file.exists()
    events = [json.loads(line) for line in audit_file.read_text().splitlines()]
    assert any(e.get("event") == "transcript_unreadable" for e in events)


def test_safety_bad_uuid_and_path_traversal(tmp_path: Path) -> None:
    """Invalid UUIDs and path traversal patterns return [] and audit event."""
    agy_home = tmp_path / "antigravity-cli"
    (agy_home / "brain").mkdir(parents=True, exist_ok=True)
    job_dir = tmp_path / "job_bad_uuid"
    job_dir.mkdir()

    assert read_denied_calls(agy_home, "not-a-uuid", [1], job_dir=job_dir) == []
    assert read_denied_calls(agy_home, "../escape", [1], job_dir=job_dir) == []
    assert (
        read_denied_calls(
            agy_home, "00000000-0000-0000-0000-000000000000/../escape", [1], job_dir=job_dir
        )
        == []
    )

    audit_file = job_dir / "audit.jsonl"
    events = [json.loads(line) for line in audit_file.read_text().splitlines()]
    assert len(events) >= 3


def test_missing_transcript_file(tmp_path: Path) -> None:
    """Missing transcript file returns [] and logs audit event."""
    agy_home = tmp_path / "antigravity-cli"
    job_dir = tmp_path / "job_missing"
    job_dir.mkdir()
    cid = "00000000-0000-0000-0000-000000000005"

    calls = read_denied_calls(agy_home, cid, steps=[1], job_dir=job_dir)
    assert calls == []

    audit_file = job_dir / "audit.jsonl"
    events = [json.loads(line) for line in audit_file.read_text().splitlines()]
    assert any(e.get("event") == "transcript_unreadable" for e in events)


def test_guard_against_gemini_reading_scope_creep() -> None:
    """Guard against scope creep: only transcript.py and runner agy invocation read
    .gemini paths."""
    src_root = Path(__file__).parent.parent / "src" / "agy_mcp"
    offending: list[str] = []

    allowed_files = {"transcript.py", "config.py"}

    for py_file in src_root.glob("*.py"):
        if py_file.name in allowed_files:
            continue
        text = py_file.read_text(encoding="utf-8")
        # Check if code reads or opens any .gemini path
        for idx, line in enumerate(text.splitlines(), 1):
            if ".gemini" in line and not line.strip().startswith("#"):
                offending.append(f"{py_file.name}:{idx} -> {line.strip()}")

    assert not offending, f"Found unexpected references to .gemini paths in src/: {offending}"
