"""A2: agy_wait, agy_review and agy_result must agree on the wrapper-owned
envelope fields (SPEC section 8: every response shares the base schema).

Before the fix, ``build_review_response`` and ``build_result_response`` copied
fields one by one and silently dropped ``plan``, ``facts``, ``agent_report``
and ``scratchpad``; a finished job therefore answered agy_wait with a populated
plan/facts block and agy_review with an empty one.
"""

from pathlib import Path

from agy_mcp.config import Config
from agy_mcp.manager import JobManager
from agy_mcp.models import JobState
from agy_mcp.runner import LocalProcessRunner
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"
SHARED_FIELDS = ("plan", "facts", "agent_report", "scratchpad", "verify", "tokens", "usage_cost")


def _manager(tmp_path: Path) -> tuple[JobManager, Path]:
    repo_dir = make_repo(tmp_path / "repo", initial_commit=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    manager = JobManager(
        config=Config(max_concurrent_jobs=2, daily_jobs_cap=5),
        runner=LocalProcessRunner(executable=FAKE_AGY),
        base_state_dir=state_dir,
        home=tmp_path,
        settle_window_seconds=0.1,
        poll_interval_seconds=0.05,
    )
    return manager, repo_dir


def test_shared_envelope_fields_equal_across_wait_review_result(tmp_path: Path) -> None:
    manager, repo_dir = _manager(tmp_path)
    env = manager.start(
        prompt="Perform task __SCENARIO:success_with_commits__",
        repo_path=str(repo_dir),
        title="A2 Review Envelope Test",
    )

    waited = manager.wait(env.job_id, seconds=10)
    assert waited.state == JobState.DONE
    review = manager.review(env.job_id)
    result = manager.result(env.job_id)

    waited_dict = waited.to_dict()
    review_dict = review.to_dict()
    result_dict = result.to_dict()

    # The fields must be populated first: comparing empty defaults would hide
    # the very bug this test pins.
    assert waited_dict["plan"]["total"] > 0, waited_dict["plan"]
    assert waited_dict["facts"], waited_dict["facts"]
    assert waited_dict["agent_report"], waited_dict["agent_report"]
    assert waited_dict["scratchpad"], waited_dict["scratchpad"]

    for field_name in SHARED_FIELDS:
        assert review_dict[field_name] == waited_dict[field_name], (
            f"agy_review {field_name!r} differs from agy_wait"
        )
        assert result_dict[field_name] == waited_dict[field_name], (
            f"agy_result {field_name!r} differs from agy_wait"
        )
