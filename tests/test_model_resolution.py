"""Smoke-fix item 2: unqualified model alias, allowlist and resolved model.

The real CLI accepts ``gemini-3.8-flash`` and silently resolves it to
``gemini-3.8-flash-high``. The masked evidence lines below are copied verbatim
from ``docs/reports/real-smoke-1/job{1,2}-*/cli-log-tail.txt``.
"""

import json
from pathlib import Path

from agy_mcp.cli_log import extract_model_resolved
from agy_mcp.config import Config
from agy_mcp.manager import JobManager
from agy_mcp.models import Job
from agy_mcp.runner import LocalProcessRunner
from agy_mcp.states import JobState
from agy_mcp.usage import group_for_model
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"

# docs/reports/real-smoke-1/job1-slugify-punctuation/cli-log-tail.txt line 152
JOB1_ALIAS_LINE = (
    'I1026 03:06:52.040021       1 model_resolver.go:155] '
    'model alias "gemini-3.8-flash" resolved to "gemini-3.8-flash-high"'
)
# docs/reports/real-smoke-1/job2-slug-docs/cli-log-tail.txt line 128
JOB2_ALIAS_LINE = (
    'I1026 03:13:20.687744       1 model_resolver.go:155] '
    'model alias "gemini-3.8-flash" resolved to "gemini-3.8-flash-high"'
)


def _setup_manager(tmp_path: Path) -> tuple[JobManager, Path]:
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    manager = JobManager(
        config=Config(max_concurrent_jobs=2, daily_jobs_cap=5),
        runner=LocalProcessRunner(executable=FAKE_AGY),
        base_state_dir=state_dir,
        home=tmp_path,
        settle_window_seconds=0.1,
        poll_interval_seconds=0.05,
    )
    return manager, repo_dir


def test_parse_real_masked_model_alias_lines() -> None:
    """Both real masked alias lines resolve to the high variant."""
    for line in (JOB1_ALIAS_LINE, JOB2_ALIAS_LINE):
        assert extract_model_resolved(line) == "gemini-3.8-flash-high"
    assert extract_model_resolved(f"{JOB1_ALIAS_LINE}\n{JOB2_ALIAS_LINE}") == (
        "gemini-3.8-flash-high"
    )


def test_absent_model_alias_line_is_none() -> None:
    """No alias line (or a decoy resolution message) yields None."""
    assert extract_model_resolved("") is None
    decoy = (
        "I1026 03:06:49.391144       1 resolver.go:85] "
        "Model ID gemini-3.8-flash not in local config, defaulting to CCPA"
    )
    assert extract_model_resolved(decoy) is None
    assert extract_model_resolved('model alias "a" not resolved to "b"') is None


def test_default_allowlist_contains_unqualified_flash() -> None:
    """The default allowlist accepts the real alias and keeps the variants."""
    allowlist = Config().model_allowlist
    assert "gemini-3.8-flash" in allowlist
    assert {
        "gemini-3.8-flash-low",
        "gemini-3.8-flash-medium",
        "gemini-3.8-flash-high",
    } <= set(allowlist)


def test_usage_group_mapping_works_for_resolved_name() -> None:
    """The resolved variant still maps to the Gemini usage group."""
    assert group_for_model("gemini-3.8-flash") == "Gemini Models"
    assert group_for_model("gemini-3.8-flash-high") == "Gemini Models"


def test_job_json_roundtrips_model_resolved(tmp_path: Path) -> None:
    """A resolved model survives Job serialization and deserialization."""
    job = Job(
        job_id="00000000-0000-0000-0000-000000000002",
        title="Resolved model",
        repo_path=tmp_path / "repo",
        branch="agy/resolved-0002",
        base_commit="base_sha",
        state=JobState.DONE,
    )
    job.model_resolved = "gemini-3.8-flash-high"
    restored = Job.from_dict(job.to_dict())
    assert restored.model_resolved == "gemini-3.8-flash-high"


def test_envelope_and_usage_jsonl_carry_resolved_model(tmp_path: Path) -> None:
    """End to end: cli.log alias -> job.model_resolved -> envelope + usage.jsonl."""
    manager, repo_dir = _setup_manager(tmp_path)
    env = manager.start(
        prompt="Alias task __SCENARIO:success_model_alias__",
        repo_path=str(repo_dir),
        title="Alias Job",
        model="gemini-3.8-flash",
    )
    res = manager.wait(env.job_id, seconds=8)

    assert res.state == JobState.DONE
    assert res.model_resolved == "gemini-3.8-flash-high"

    usage_path = tmp_path / "state" / "usage.jsonl"
    records = [json.loads(line) for line in usage_path.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["model"] == "gemini-3.8-flash"
    assert records[-1]["model_resolved"] == "gemini-3.8-flash-high"


def test_absent_alias_is_null_in_envelope_and_usage(tmp_path: Path) -> None:
    """Without an alias line both surfaces carry null, never an invented value."""
    manager, repo_dir = _setup_manager(tmp_path)
    env = manager.start(
        prompt="No alias task __SCENARIO:success_simple__",
        repo_path=str(repo_dir),
        title="No Alias Job",
    )
    res = manager.wait(env.job_id, seconds=8)

    assert res.model_resolved is None
    usage_path = tmp_path / "state" / "usage.jsonl"
    records = [json.loads(line) for line in usage_path.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["model_resolved"] is None
