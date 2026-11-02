"""ITEM 2: seeded scratchpad files and the optional ``checklist`` parameter."""

import re
from pathlib import Path

import pytest

from agy_mcp import scratchpad
from agy_mcp.config import Config
from agy_mcp.jobs import get_job
from agy_mcp.manager import JobManager, ManagerError
from agy_mcp.runner import LocalProcessRunner
from agy_mcp.worktree import create_worktree, provision_metadata_dir
from tests.helpers.make_repo import get_safe_git_env, make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"


def _make_manager(tmp_path: Path) -> JobManager:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    return JobManager(
        config=Config(max_concurrent_jobs=2, daily_jobs_cap=5),
        runner=LocalProcessRunner(executable=FAKE_AGY),
        base_state_dir=state_dir,
        home=tmp_path,
        settle_window_seconds=0.1,
        poll_interval_seconds=0.05,
    )


def _provision(tmp_path: Path, checklist=None):
    repo = tmp_path / "repo"
    make_repo(repo, initial_commit=True, env=get_safe_git_env(tmp_path))
    job_dir = tmp_path / "jobs" / "job_seed"
    job_dir.mkdir(parents=True)
    wt_path, _branch, _sha = create_worktree(repo_path=repo, job_dir=job_dir, title="Seed Test")
    provision_metadata_dir(wt_path, "Seed Test", checklist)
    return wt_path / ".agy"


def test_seed_defaults(tmp_path: Path) -> None:
    """All four files are seeded; PLAN is a numbered checkbox list with the defaults."""
    agy_dir = _provision(tmp_path)

    plan = (agy_dir / "PLAN.md").read_text(encoding="utf-8")
    assert "- [ ] 1. Complete the task described in the prompt" in plan
    assert "- [ ] 2. Fill in .agy/REPORT.md honestly" in plan
    assert re.search(r"^- \[ \] 1\. ", plan, re.MULTILINE)

    notes = (agy_dir / "NOTES.md").read_text(encoding="utf-8")
    assert "working memory; keep decisions and findings here" in notes

    log = (agy_dir / "LOG.md").read_text(encoding="utf-8")
    assert log.startswith("# LOG")
    assert log.strip() == "# LOG"

    report = (agy_dir / "REPORT.md").read_text(encoding="utf-8")
    for section in ("Done", "Not done / skipped", "Open issues", "Files changed", "How verified"):
        assert f"## {section}" in report, f"REPORT.md missing section {section!r}"

    for name, cap in (
        ("PLAN.md", scratchpad.MAX_PLAN_BYTES),
        ("NOTES.md", scratchpad.MAX_NOTES_BYTES),
        ("LOG.md", scratchpad.MAX_LOG_BYTES),
        ("REPORT.md", scratchpad.MAX_REPORT_BYTES),
    ):
        assert (agy_dir / name).stat().st_size <= cap


def test_seed_custom_checklist_numbered_and_sanitized(tmp_path: Path) -> None:
    """A supplied checklist becomes a numbered list and newlines are collapsed."""
    agy_dir = _provision(
        tmp_path,
        ["Read the spec\nand the tests", "Implement   the\nchange", "Run checks"],
    )
    plan = (agy_dir / "PLAN.md").read_text(encoding="utf-8")
    assert "- [ ] 1. Read the spec and the tests" in plan
    assert "- [ ] 2. Implement the change" in plan
    assert "- [ ] 3. Run checks" in plan
    assert plan.count("- [ ]") == 3
    assert "Complete the task described in the prompt" not in plan


def test_checklist_threaded_through_manager_start(tmp_path: Path) -> None:
    """``JobManager.start(checklist=...)`` seeds the worktree PLAN.md before launch."""
    manager = _make_manager(tmp_path)
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    env = manager.start(
        prompt="Perform task __SCENARIO:crash_rc1__",
        repo_path=str(repo_dir),
        title="Checklist Thread",
        checklist=["alpha step", "beta step"],
    )
    manager.wait(env.job_id, seconds=10)
    job = get_job(env.job_id, tmp_path / "state")
    assert job.worktree_path is not None
    plan = (job.worktree_path / ".agy" / "PLAN.md").read_text(encoding="utf-8")
    assert "- [ ] 1. alpha step" in plan
    assert "- [ ] 2. beta step" in plan


def test_checklist_too_many_items_rejected(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    with pytest.raises(ManagerError) as exc:
        manager.start(
            prompt="task",
            repo_path=str(repo_dir),
            title="Too Many",
            checklist=[f"item {i}" for i in range(31)],
        )
    assert exc.value.code == "checklist_too_long"


def test_checklist_item_too_long_rejected(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    with pytest.raises(ManagerError) as exc:
        manager.start(
            prompt="task",
            repo_path=str(repo_dir),
            title="Too Long",
            checklist=["x" * 201],
        )
    assert exc.value.code == "checklist_item_too_long"


def test_checklist_non_string_rejected(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir, initial_commit=True)
    with pytest.raises(ManagerError) as exc:
        manager.start(
            prompt="task",
            repo_path=str(repo_dir),
            title="Wrong Type",
            checklist=["ok", 3],  # type: ignore[list-item]
        )
    assert exc.value.code == "invalid_checklist"
