"""Compare the error envelope's serialized payload to a pre-refactor fixture.

``tests/fixtures/error_envelope_before.json`` was captured from the hand-built
``make_error_envelope`` BEFORE wave 1.5 rebuilt it from ``StandardEnvelope``
defaults. The refactor is behavior-preserving only if the payload -- including
key order, the three-key ``tokens`` sub-dict and the 14-key ``verify`` sub-dict
-- is byte-identical for representative errors.
"""

import json
from pathlib import Path

import pytest

from agy_mcp.models import StandardEnvelope
from agy_mcp.server import _ERROR_ENVELOPE_OMITTED, make_error_envelope

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "error_envelope_before.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
CASES = FIXTURE["cases"]
PAYLOADS = FIXTURE["payloads"]


def _kwargs(case: dict) -> dict:
    return {k: case[k] for k in ("code", "message", "job_id", "title") if k in case}


def test_fixture_covers_representative_errors() -> None:
    """Guard the fixture: several distinct code/path combinations are pinned."""
    assert len(CASES) == len(PAYLOADS) >= 5
    assert {"invalid_input", "internal_error", "repo_not_found"} <= {
        case["code"] for case in CASES
    }


@pytest.mark.parametrize("index", range(len(CASES)))
def test_payload_matches_pre_refactor_output(index: int) -> None:
    """The rebuilt envelope must equal the old dict exactly."""
    assert make_error_envelope(**_kwargs(CASES[index])) == PAYLOADS[index]


@pytest.mark.parametrize("index", range(len(CASES)))
def test_serialized_payload_is_byte_identical(index: int) -> None:
    """Same key order and same nested shapes => same JSON bytes."""
    actual = make_error_envelope(**_kwargs(CASES[index]))
    assert list(actual) == list(PAYLOADS[index])
    assert json.dumps(actual) == json.dumps(PAYLOADS[index])


def test_every_envelope_field_is_present_or_explicitly_omitted() -> None:
    """A new StandardEnvelope field must be a conscious error-envelope decision."""
    payload = make_error_envelope(code="x", message="y")
    omitted = set(StandardEnvelope.__dataclass_fields__) - set(payload)
    assert omitted == set(_ERROR_ENVELOPE_OMITTED)
    assert len(payload) == 17
    assert list(payload["tokens"]) == ["input_tokens", "output_tokens", "total_tokens"]
    assert len(payload["verify"]) == 14
