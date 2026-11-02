"""Smoke-fix item 4: real stdout token fields reach the envelope.

The real JOB 1 stdout (masked evidence, REPORT.md finding 5) is:
``"duration_seconds":327.739397336,"num_turns":1,"usage":{"input_tokens":221140,
"output_tokens":31011,"thinking_tokens":24252,"cache_read_tokens":833792,
"total_tokens":252151}``. The wrapper previously dropped thinking/cache/num_turns.
"""

from pathlib import Path

from agy_mcp.models import Job, TokenUsage, build_standard_envelope
from agy_mcp.parse_result import parse_agy_output
from agy_mcp.states import JobState

# Synthetic payload built from the real masked stdout fields above.
REAL_STDOUT = (
    '{"status":"SUCCESS","response":"done","conversation_id":"11111111-1111-4111-8111-111111111111",'
    '"duration_seconds":327.739397336,"num_turns":1,'
    '"usage":{"input_tokens":221140,"output_tokens":31011,'
    '"thinking_tokens":24252,"cache_read_tokens":833792,"total_tokens":252151}}'
)
WITHOUT_OPTIONAL = (
    '{"status":"SUCCESS","response":"done","conversation_id":"22222222-2222-4222-8222-222222222222",'
    '"usage":{"input_tokens":10,"output_tokens":5,"total_tokens":15}}'
)


def _job(tmp_path: Path, tokens: TokenUsage) -> Job:
    return Job(
        job_id="00000000-0000-0000-0000-000000000004",
        title="Tokens",
        repo_path=tmp_path / "repo",
        branch="agy/tokens-0004",
        base_commit="base_sha",
        state=JobState.DONE,
        tokens=tokens,
    )


def test_parse_real_stdout_carries_optional_token_fields() -> None:
    """The real stdout sample yields thinking, cache-read and num_turns."""
    res = parse_agy_output(REAL_STDOUT, "", 0, commits_count=1)

    assert res.state == JobState.DONE
    assert res.tokens.input_tokens == 221140
    assert res.tokens.output_tokens == 31011
    assert res.tokens.total_tokens == 252151
    assert res.tokens.thinking_tokens == 24252
    assert res.tokens.cache_read_tokens == 833792
    assert res.tokens.num_turns == 1


def test_parse_payload_without_optional_fields_sets_none() -> None:
    """A payload with only input/output/total leaves the new fields null."""
    res = parse_agy_output(WITHOUT_OPTIONAL, "", 0, commits_count=1)

    assert res.state == JobState.DONE
    assert res.tokens.input_tokens == 10
    assert res.tokens.total_tokens == 15
    assert res.tokens.thinking_tokens is None
    assert res.tokens.cache_read_tokens is None
    assert res.tokens.num_turns is None


def test_token_usage_from_dict_optional_fields() -> None:
    """TokenUsage.from_dict reads the optional keys or leaves them None."""
    full = TokenUsage.from_dict(
        {
            "input_tokens": 221140,
            "output_tokens": 31011,
            "thinking_tokens": 24252,
            "cache_read_tokens": 833792,
            "total_tokens": 252151,
        }
    )
    assert full.thinking_tokens == 24252
    assert full.cache_read_tokens == 833792
    assert full.num_turns is None  # num_turns is a top-level stdout field

    legacy = TokenUsage.from_dict({"input_tokens": 1, "output_tokens": 2, "total_tokens": 3})
    assert legacy.thinking_tokens is None
    assert legacy.cache_read_tokens is None
    assert legacy.num_turns is None


def test_envelope_tokens_carry_optional_fields(tmp_path: Path) -> None:
    """The standard envelope tokens dict exposes all six counters."""
    tokens = TokenUsage(
        input_tokens=221140,
        output_tokens=31011,
        total_tokens=252151,
        thinking_tokens=24252,
        cache_read_tokens=833792,
        num_turns=1,
    )
    env = build_standard_envelope(_job(tmp_path, tokens)).to_dict()

    assert env["tokens"] == {
        "input_tokens": 221140,
        "output_tokens": 31011,
        "total_tokens": 252151,
        "thinking_tokens": 24252,
        "cache_read_tokens": 833792,
        "num_turns": 1,
    }


def test_existing_envelope_stays_backward_compatible(tmp_path: Path) -> None:
    """An old payload keeps its three counters and gains explicit nulls."""
    tokens = TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)
    job = _job(tmp_path, tokens)

    env = build_standard_envelope(job).to_dict()
    assert env["tokens"]["input_tokens"] == 10
    assert env["tokens"]["output_tokens"] == 5
    assert env["tokens"]["total_tokens"] == 15
    assert env["tokens"]["thinking_tokens"] is None
    assert env["tokens"]["cache_read_tokens"] is None
    assert env["tokens"]["num_turns"] is None

    restored = Job.from_dict(job.to_dict())
    assert restored.tokens.total_tokens == 15
    assert restored.tokens.thinking_tokens is None
