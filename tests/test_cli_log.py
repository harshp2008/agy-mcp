"""Tests for CLI log denial extraction and tail-reading per SPEC section 15."""

from pathlib import Path

from agy_mcp.cli_log import (
    Denial,
    extract_denials_from_cli_log,
    format_denied_commands,
    read_cli_log_tail,
)
from agy_mcp.models import (
    Job,
    TokenUsage,
    build_standard_envelope,
    derive_next_actions,
    unwrap_untrusted_text,
)
from agy_mcp.states import JobState

FIXTURES_DIR = Path("tests/fixtures/real_logs")


def test_extract_denials_real_log() -> None:
    """Real agy CLI log fixture parses the step 6 soft-denial correctly."""
    log_file = FIXTURES_DIR / "real_log.log"
    assert log_file.is_file()
    text = read_cli_log_tail(log_file)
    denials = extract_denials_from_cli_log(text)

    assert len(denials) == 1
    d = denials[0]
    assert d.step == 6
    assert d.tool == "RunCommand"
    assert d.command is None
    assert 'Print mode: soft-denying tool confirmation "RunCommand" at step 6' in d.raw_line
    # Test __getitem__ access
    assert d["step"] == 6
    assert d["tool"] == "RunCommand"


def test_extract_denials_empty_log() -> None:
    """Empty CLI log returns zero denial records."""
    log_file = FIXTURES_DIR / "empty_log.log"
    text = read_cli_log_tail(log_file)
    denials = extract_denials_from_cli_log(text)
    assert denials == []


def test_extract_denials_several_denials() -> None:
    """Log with multiple soft-denials parses each step in order."""
    log_file = FIXTURES_DIR / "several_denials.log"
    text = read_cli_log_tail(log_file)
    denials = extract_denials_from_cli_log(text)

    assert len(denials) == 3
    assert [d.step for d in denials] == [2, 5, 9]
    assert all(d.tool == "RunCommand" for d in denials)
    assert all(d.command is None for d in denials)


def test_extract_denials_truncated_log() -> None:
    """Incomplete/truncated log line does not produce false-positive denial."""
    log_file = FIXTURES_DIR / "truncated_log.log"
    text = read_cli_log_tail(log_file)
    denials = extract_denials_from_cli_log(text)
    assert denials == []


def test_extract_denials_binary_garbage() -> None:
    """Binary garbage in CLI log is decoded safely without exceptions."""
    log_file = FIXTURES_DIR / "binary_garbage.log"
    text = read_cli_log_tail(log_file)
    assert isinstance(text, str)
    denials = extract_denials_from_cli_log(text)
    assert denials == []


def test_extract_denials_huge_log_tail_read() -> None:
    """Log > 2MB only reads the tail 2MB and finds trailing denial without reading entire file."""
    log_file = FIXTURES_DIR / "huge_log.log"
    file_size = log_file.stat().st_size
    assert file_size > 2 * 1024 * 1024

    text = read_cli_log_tail(log_file, max_bytes=2 * 1024 * 1024)
    # The read text should be capped at 2MB
    assert len(text.encode("utf-8")) <= 2 * 1024 * 1024
    denials = extract_denials_from_cli_log(text)
    assert len(denials) == 1
    assert denials[0].step == 15
    assert denials[0].tool == "RunCommand"


def test_format_denied_commands_caps_and_wrapping() -> None:
    """format_denied_commands caps entries at 20, 500 bytes per command, wrapping untrusted text."""
    # 25 denials
    many = [Denial(step=i, tool="RunCommand", command=f"command_{i} " * 50) for i in range(25)]
    formatted = format_denied_commands(many, max_entries=20, max_command_bytes=500)
    assert len(formatted) == 20

    first = formatted[0]
    assert first["step"] == 0
    assert first["tool"] == "RunCommand"
    assert "<untrusted_text" in first["command"]
    unwrapped = unwrap_untrusted_text(first["command"])
    assert len(unwrapped.encode("utf-8")) <= 500


def test_derive_next_actions_blocked_by_permission() -> None:
    """derive_next_actions for blocked_by_permission uses step N and advises file tools."""
    denials = [{"step": 8, "tool": "RunCommand", "command": None, "raw_line": "..."}]
    actions = derive_next_actions(
        JobState.NEEDS_REVIEW,
        review_reason="blocked_by_permission",
        denied_commands=denials,
    )
    expected_msg = (
        "agy was halted running a shell tool (step 8); the command text is not available. "
        "Resume with guidance to use file tools only, or ask the user."
    )
    assert actions[0] == expected_msg

    # Fallback to step N when denied_commands is empty
    fallback_actions = derive_next_actions(
        JobState.NEEDS_REVIEW,
        review_reason="blocked_by_permission",
        denied_commands=[],
    )
    assert "step N" in fallback_actions[0]


def test_envelope_and_job_serialization_with_denied_commands(tmp_path: Path) -> None:
    """Job and StandardEnvelope preserve denied_commands field through serialization."""
    denials = [{"step": 6, "tool": "RunCommand", "command": None, "raw_line": "raw line"}]
    job = Job(
        job_id="00000000-0000-0000-0000-000000000001",
        title="Test Job",
        repo_path=tmp_path / "repo",
        branch="agy/test-0001",
        base_commit="base_sha",
        state=JobState.NEEDS_REVIEW,
        review_reason="blocked_by_permission",
        denied_commands=denials,
        tokens=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
    )

    data = job.to_dict()
    assert "denied_commands" in data
    assert data["denied_commands"] == denials

    restored = Job.from_dict(data)
    assert restored.denied_commands == denials

    envelope = build_standard_envelope(job)
    env_dict = envelope.to_dict()
    assert env_dict["denied_commands"] == denials
    assert "step 6" in env_dict["next_actions"][0]


def test_extract_subagent_denial_from_cli_log() -> None:
    """Subagent soft-denial lines are parsed with tool='subagent' and subagent_id."""
    raw_log = (
        "I1025 13:14:05.277886     536 subagent_manager.go:670] "
        "addFromDiff: print mode, soft-denying: 55c62dde-8307-484a-bc8a-4e954192ba01 step 8\n"
    )
    denials = extract_denials_from_cli_log(raw_log)
    assert len(denials) == 1
    d = denials[0]
    assert d.step == 8
    assert d.tool == "subagent"
    assert d.subagent_id == "55c62dde-8307-484a-bc8a-4e954192ba01"
    assert d.to_dict()["subagent_id"] == "55c62dde-8307-484a-bc8a-4e954192ba01"


def test_derive_next_actions_known_command() -> None:
    """When denied command text is known, next_actions names the exact untrusted command."""
    cmd_text = '<untrusted_text id="abc">cat README.md</untrusted_text id="abc">'
    denials = [{"step": 6, "tool": "RunCommand", "command": cmd_text}]

    actions = derive_next_actions(
        JobState.NEEDS_REVIEW,
        review_reason="blocked_by_permission",
        denied_commands=denials,
    )
    expected = f"agy tried to run: {cmd_text}. Approve by resuming with guidance, or tell the user."
    assert actions[0] == expected

    actions_no_progress = derive_next_actions(
        JobState.NEEDS_REVIEW,
        review_reason="denied_without_progress",
        denied_commands=denials,
    )
    assert actions_no_progress[0] == expected
