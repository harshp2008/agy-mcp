"""ITEM 5: wrapper-computed facts, the agent report, and unverified claims."""

from pathlib import Path
from unittest.mock import patch

import pytest

from agy_mcp import scratchpad
from agy_mcp.config import Config, ConfigError, load_config
from agy_mcp.manager import JobManager
from agy_mcp.models import JobState
from agy_mcp.runner import LocalProcessRunner
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"


def _setup(tmp_path: Path, config: Config | None = None) -> tuple[JobManager, Path]:
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    manager = JobManager(
        config=config or Config(max_concurrent_jobs=2, daily_jobs_cap=5),
        runner=LocalProcessRunner(executable=FAKE_AGY),
        base_state_dir=state_dir,
        home=tmp_path,
        settle_window_seconds=0.1,
        poll_interval_seconds=0.05,
    )
    return manager, repo_dir


def _start(manager: JobManager, repo_dir: Path, scenario: str, verify: str | None = None):
    return manager.start(
        prompt=f"Perform task __SCENARIO:{scenario}__",
        repo_path=str(repo_dir),
        title="Report Job",
        verify=verify,
    )


def test_clean_report_has_no_claim_warning(tmp_path: Path) -> None:
    manager, repo_dir = _setup(tmp_path)
    env = _start(manager, repo_dir, "report_clean")
    res = manager.wait(env.job_id, seconds=15)

    assert res.state == JobState.DONE
    assert "report_claims_unverified" not in res.warnings
    assert res.agent_report is not None
    assert res.agent_report.startswith("<untrusted_text")
    assert "Manual review only" in res.agent_report


def test_claim_without_verify_warns(tmp_path: Path) -> None:
    manager, repo_dir = _setup(tmp_path)
    env = _start(manager, repo_dir, "report_claim")
    res = manager.wait(env.job_id, seconds=15)

    assert res.state == JobState.DONE
    assert "report_claims_unverified" in res.warnings
    assert res.agent_report is not None


def test_claim_with_failed_verify_warns(tmp_path: Path) -> None:
    manager, repo_dir = _setup(tmp_path)
    with patch("agy_mcp.verify.run_verify", return_value=(1, "FAILED tests/test_x.py")):
        env = _start(manager, repo_dir, "report_claim", verify="pytest")
        res = manager.wait(env.job_id, seconds=15)

    assert res.state == JobState.NEEDS_REVIEW
    assert "report_claims_unverified" in res.warnings


def test_claim_with_passed_verify_does_not_warn(tmp_path: Path) -> None:
    manager, repo_dir = _setup(tmp_path)
    with patch("agy_mcp.verify.run_verify", return_value=(0, "1 passed")):
        env = _start(manager, repo_dir, "report_claim", verify="pytest")
        res = manager.wait(env.job_id, seconds=15)

    assert res.state == JobState.DONE
    assert "report_claims_unverified" not in res.warnings


def test_facts_are_computed_from_job(tmp_path: Path) -> None:
    manager, repo_dir = _setup(tmp_path)
    env = _start(manager, repo_dir, "plan_complete")
    res = manager.wait(env.job_id, seconds=15)
    facts = res.facts

    assert facts["commits_count"] == res.commits_count
    assert facts["files_modified_count"] == len(res.files_modified)
    assert facts["plan"] == {"total": 3, "done": 3, "remaining": 0}
    assert facts["audit"]["status"] == "passed"
    assert facts["verify"]["requested"] is False
    assert facts["verify"]["ran"] is False
    assert facts["denials"]["count"] == len(res.denials)
    assert facts["denials"]["commands"] == len(res.denied_commands)
    assert facts["denials"]["tools"] == len(res.denied_tools)

    # facts never contain agent text; the report is a separate field.
    assert "Fake step" not in str(facts)


def test_agent_report_is_capped_and_untrusted() -> None:
    big = "# REPORT\n\n## How verified\nAll tests passed.\n" + "x" * (10 * 1024)
    capped = scratchpad.cap_text_bytes(big, scratchpad.MAX_REPORT_BYTES)
    assert len(capped.encode("utf-8")) <= scratchpad.MAX_REPORT_BYTES
    assert scratchpad.report_claims_tests_passed(big, ["all tests passed"]) is True


@pytest.mark.parametrize(
    "text,expected",
    [
        ("All tests passed.", True),
        ("TESTS PASS", True),
        ("verified manually", True),
        ("Implemented the fix and updated docs.", False),
        ("nothing to report", False),
    ],
)
def test_claim_phrase_matching(text: str, expected: bool) -> None:
    phrases = ["tests pass", "all tests passed", "verified"]
    assert scratchpad.report_claims_tests_passed(text, phrases) is expected


def test_report_template_heading_is_not_a_claim() -> None:
    """The required 'How verified' section heading must not look like a claim."""
    assert (
        scratchpad.report_claims_tests_passed(
            scratchpad.REPORT_TEMPLATE, ["tests pass", "all tests passed", "verified"]
        )
        is False
    )


def test_claim_phrases_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('claim_phrases = ["green build"]\n', encoding="utf-8")
    assert load_config(path=config_path).claim_phrases == ["green build"]
    config_path.write_text("claim_phrases = []\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path=config_path)
