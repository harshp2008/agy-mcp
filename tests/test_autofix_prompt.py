"""Tests for finding F20: Auto-fix prompt structure, persistent counters, and wait prompt return."""

import time
from pathlib import Path
from unittest.mock import patch

from agy_mcp.config import Config
from agy_mcp.jobs import get_job
from agy_mcp.manager import JobManager, ManagerError
from agy_mcp.models import JobState
from agy_mcp.runner import LocalProcessRunner
from agy_mcp.util import format_resume_prompt, load_preamble
from agy_mcp.verify import format_fix_prompt
from tests.helpers.make_repo import get_safe_git_env, make_repo


def _setup_manager_and_repo(
    tmp_path: Path, config: Config | None = None
) -> tuple[JobManager, Path]:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = home / "repo"
    make_repo(repo_dir, initial_commit=True, env=get_safe_git_env(home))

    fake_agy_path = Path(__file__).parent / "fake_agy" / "agy"
    runner = LocalProcessRunner(executable=str(fake_agy_path))

    cfg = config or Config()
    mgr = JobManager(
        runner=runner,
        config=cfg,
        base_state_dir=state_dir,
        home=home,
        settle_window_seconds=0.1,
    )
    return mgr, repo_dir


def test_f20_prompt_text_exact_check() -> None:
    """Prompt must have guidance outside untrusted block, tail inside, and no escaped tags."""
    preamble = load_preamble()
    tail = "FAILED tests/test_calc.py::test_add - assert 1 == 2\nTraceback error in calc.py"

    fix_body = format_fix_prompt(tail, include_preamble=False)
    full_prompt = format_resume_prompt(fix_body, is_trusted=True)

    # 1. Preamble appears exactly once
    assert full_prompt.count(preamble) == 1, (
        f"Preamble appeared {full_prompt.count(preamble)} times, expected exactly 1"
    )

    # 2. Guidance heading is trusted
    assert "## Wrapper Guidance" in full_prompt
    assert "Change code, not tests, unless the test itself is wrong" in full_prompt

    # 3. No escaped or nested untrusted tags
    assert "&lt;untrusted_text" not in full_prompt
    assert "&lt;/untrusted_text" not in full_prompt
    assert "<untrusted_text" in full_prompt
    assert "</untrusted_text" in full_prompt
    assert full_prompt.count("<untrusted_text") == 1
    assert full_prompt.count("</untrusted_text") == 1

    # 4. Tail is inside untrusted block, guidance is outside
    tag_start = full_prompt.index("<untrusted_text")
    tag_end = full_prompt.index("</untrusted_text")
    tail_pos = full_prompt.index("assert 1 == 2")
    guidance_pos = full_prompt.index("Change code, not tests")

    assert tag_start < tail_pos < tag_end, "Failure tail must be inside <untrusted_text> block"
    assert guidance_pos > tag_end, "Guidance must be outside (after) <untrusted_text> block"


def test_f20_counters_survive_simulated_restart(tmp_path: Path) -> None:
    """Auto-fix and denial-resume counters must persist in job.json and survive manager restart."""
    cfg = Config(auto_fix_attempts=2, auto_resume_on_denial=2)
    manager1, repo_dir = _setup_manager_and_repo(tmp_path, config=cfg)
    state_dir = tmp_path / "state"

    env = manager1.start(
        prompt="Fix calc __SCENARIO:verify_fail_then_fix__",
        repo_path=str(repo_dir),
        title="Auto Fix Counter Job",
        verify="pytest",
    )

    # Wait for the job to complete or perform an auto-fix cycle
    manager1.wait(env.job_id, seconds=5)

    j1 = get_job(env.job_id, state_dir)
    assert j1.auto_fix_count >= 1, f"Expected auto_fix_count >= 1, got {j1.auto_fix_count}"

    # Simulate restart by creating a new manager with empty memory
    home = tmp_path / "home"
    fake_agy_path = Path(__file__).parent / "fake_agy" / "agy"
    runner = LocalProcessRunner(executable=str(fake_agy_path))
    manager2 = JobManager(
        runner=runner,
        config=cfg,
        base_state_dir=state_dir,
        home=home,
        settle_window_seconds=0.1,
    )
    assert manager2.status(env.job_id).state is not None

    persisted_job = get_job(env.job_id, state_dir)
    assert persisted_job.auto_fix_count == j1.auto_fix_count
    assert persisted_job.auto_fix_count >= 1


def test_f20_failing_auto_resume_wait_returns_promptly(tmp_path: Path) -> None:
    """If auto-resume attempt fails, wait() must not stall for 300s and return promptly."""
    cfg = Config(auto_fix_attempts=2)
    manager, repo_dir = _setup_manager_and_repo(tmp_path, config=cfg)

    # Patch resume to fail during auto-fix
    orig_resume = manager.resume

    def mock_failing_resume(job_id: str, answer: str, is_trusted: bool = False):
        if "Change code, not tests" in answer or "Verification failed" in answer:
            raise ManagerError("Simulated resume failure", code="concurrency_limit_exceeded")
        return orig_resume(job_id, answer, is_trusted=is_trusted)

    with patch.object(manager, "resume", side_effect=mock_failing_resume):
        env = manager.start(
            prompt="Fix calc __SCENARIO:verify_always_fail__",
            repo_path=str(repo_dir),
            title="Failing Auto Resume Job",
            verify="pytest",
        )

        t0 = time.time()
        # Request a long wait; should return promptly when auto-resume fails
        res = manager.wait(env.job_id, seconds=30)
        elapsed = time.time() - t0

        assert elapsed < 5.0, f"wait() stalled for {elapsed}s on failing auto-resume"
        assert res.state in (JobState.NEEDS_REVIEW, JobState.FAILED)
