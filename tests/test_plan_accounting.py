"""ITEM 4: plan accounting and the single automatic plan nudge."""

from pathlib import Path

import pytest

from agy_mcp import scratchpad
from agy_mcp.config import Config, ConfigError, load_config
from agy_mcp.jobs import get_job
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


def _start(manager: JobManager, repo_dir: Path, scenario: str, title: str = "Plan Job"):
    return manager.start(
        prompt=f"Perform task __SCENARIO:{scenario}__",
        repo_path=str(repo_dir),
        title=title,
    )


def test_config_plan_nudge_validation(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("plan_nudge = 0\n", encoding="utf-8")
    assert load_config(path=config_path).plan_nudge == 0
    config_path.write_text("plan_nudge = 1\n", encoding="utf-8")
    assert load_config(path=config_path).plan_nudge == 1
    config_path.write_text("plan_nudge = 2\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path=config_path)


def test_plan_parser_counts_ticks_and_collects_titles() -> None:
    text = "# Plan\n\n- [ ] 1. One\n- [x] 2. Two\n* [X] 3. Three\n- [ ] 4. Four\n"
    parsed = scratchpad.parse_plan_text(text)
    assert parsed["total"] == 4
    assert parsed["done"] == 2
    assert parsed["remaining"] == ["One", "Four"]


def test_all_plan_items_ticked_end_done(tmp_path: Path) -> None:
    manager, repo_dir = _setup(tmp_path)
    env = _start(manager, repo_dir, "plan_complete")
    res = manager.wait(env.job_id, seconds=15)

    assert res.state == JobState.DONE, f"state={res.state} reason={res.review_reason}"
    assert res.plan["total"] == 3
    assert res.plan["done"] == 3
    assert res.plan["remaining"] == []
    assert "plan_incomplete" not in res.warnings
    job = get_job(env.job_id, tmp_path / "state")
    assert job.plan_nudge_count == 0
    assert job.run_generation == 1


def test_partial_plan_nudges_once_then_completes(tmp_path: Path) -> None:
    manager, repo_dir = _setup(tmp_path, Config(plan_nudge=1))
    env = _start(manager, repo_dir, "plan_nudge_complete")
    res = manager.wait(env.job_id, seconds=25)

    job = get_job(env.job_id, tmp_path / "state")
    assert res.state == JobState.DONE, f"state={res.state} reason={job.review_reason}"
    assert job.plan_nudge_count == 1, "expected exactly one plan nudge"
    assert job.run_generation == 2, "expected exactly one automatic resume"
    assert res.plan["done"] == res.plan["total"]
    assert res.plan["remaining"] == []


def test_partial_plan_nudges_once_then_needs_review(tmp_path: Path) -> None:
    manager, repo_dir = _setup(tmp_path, Config(plan_nudge=1))
    env = _start(manager, repo_dir, "plan_nudge_partial")
    res = manager.wait(env.job_id, seconds=25)

    assert res.state == JobState.NEEDS_REVIEW
    job = get_job(env.job_id, tmp_path / "state")
    assert job.review_reason == "plan_incomplete"
    assert job.plan_nudge_count == 1, "the plan nudge must fire at most once"
    assert job.run_generation == 2, "expected exactly one automatic resume"
    assert len(res.plan["remaining"]) >= 1
    for wrapped in res.plan["remaining"]:
        assert wrapped.startswith("<untrusted_text")
    assert "plan_incomplete" in res.warnings

    # Still resumable: plan_incomplete is not a security reason.
    resumed = manager.resume(env.job_id, "please finish the remaining items", is_trusted=True)
    assert resumed.state in (JobState.RUNNING, JobState.NEEDS_REVIEW)
    manager.wait(env.job_id, seconds=15)


def test_partial_plan_with_nudge_disabled_stays_done(tmp_path: Path) -> None:
    manager, repo_dir = _setup(tmp_path, Config(plan_nudge=0))
    env = _start(manager, repo_dir, "plan_partial")
    res = manager.wait(env.job_id, seconds=15)

    assert res.state == JobState.DONE
    assert "plan_incomplete" in res.warnings
    assert len(res.plan["remaining"]) == 2
    job = get_job(env.job_id, tmp_path / "state")
    assert job.plan_nudge_count == 0
    assert job.run_generation == 1
