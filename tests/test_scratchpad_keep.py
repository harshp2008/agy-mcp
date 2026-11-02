"""ITEM 7: keep the scratchpad under <jobdir>/scratchpad/ after cleanup."""

from pathlib import Path

from agy_mcp import scratchpad
from agy_mcp.config import Config
from agy_mcp.jobs import get_job, get_job_dir
from agy_mcp.manager import JobManager
from agy_mcp.models import JobState
from agy_mcp.runner import LocalProcessRunner
from tests.helpers.make_repo import make_repo

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"


def _setup(tmp_path: Path) -> tuple[JobManager, Path]:
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


def test_scratchpad_copies_survive_cleanup(tmp_path: Path) -> None:
    manager, repo_dir = _setup(tmp_path)
    env = manager.start(
        prompt="Perform task __SCENARIO:plan_complete__",
        repo_path=str(repo_dir),
        title="Keep Scratchpad",
    )
    res = manager.wait(env.job_id, seconds=15)
    assert res.state == JobState.DONE

    job_dir = get_job_dir(env.job_id, tmp_path / "state")
    keep_dir = job_dir / "scratchpad"
    assert keep_dir.is_dir()
    for name in scratchpad.SCRATCHPAD_FILES:
        assert (keep_dir / name).is_file(), f"missing retained {name}"
    retained_plan = (keep_dir / "PLAN.md").read_text(encoding="utf-8")
    assert "- [x] 1." in retained_plan

    # Envelope exposes names only (F26).
    assert res.scratchpad == list(scratchpad.SCRATCHPAD_FILES)
    for name in res.scratchpad:
        assert "/" not in name and name

    before = {name: (keep_dir / name).read_bytes() for name in scratchpad.SCRATCHPAD_FILES}
    job = get_job(env.job_id, tmp_path / "state")
    worktree = job.worktree_path
    assert worktree is not None and worktree.is_dir()
    manager.cleanup(env.job_id, remove_worktree=True)
    assert not worktree.exists(), "cleanup did not remove the worktree"
    for name in scratchpad.SCRATCHPAD_FILES:
        assert (keep_dir / name).is_file(), f"cleanup deleted retained {name}"
        assert (keep_dir / name).read_bytes() == before[name]


def test_symlinked_source_skipped_with_warning(tmp_path: Path) -> None:
    manager, repo_dir = _setup(tmp_path)
    env = manager.start(
        prompt="Perform task __SCENARIO:scratchpad_symlink__",
        repo_path=str(repo_dir),
        title="Symlink Scratchpad",
    )
    res = manager.wait(env.job_id, seconds=15)
    assert "scratchpad_file_skipped:NOTES.md" in res.warnings
    assert "scratchpad_file_skipped:PLAN.md" not in res.warnings

    keep_dir = get_job_dir(env.job_id, tmp_path / "state") / "scratchpad"
    assert not (keep_dir / "NOTES.md").exists()
    assert (keep_dir / "PLAN.md").is_file()
    assert (keep_dir / "LOG.md").is_file()
    # The symlink target must never be copied into the retained directory.
    for path in keep_dir.iterdir():
        assert "DO-NOT-COPY-SECRET" not in path.read_text(encoding="utf-8", errors="replace")


def test_oversized_source_is_skipped(tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    (wt / ".agy").mkdir(parents=True)
    (wt / ".agy" / "PLAN.md").write_text("- [ ] 1. one\n", encoding="utf-8")
    (wt / ".agy" / "NOTES.md").write_bytes(b"x" * (scratchpad.MAX_NOTES_BYTES + 1))
    job_dir = tmp_path / "job"
    job_dir.mkdir()

    copied, skipped = scratchpad.copy_to_jobdir(wt, job_dir)
    assert "PLAN.md" in copied
    assert "NOTES.md" in skipped
    assert not (job_dir / "scratchpad" / "NOTES.md").exists()


def test_copy_uses_hardened_reads(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "PLAN.md").write_text("- [x] 1. secret\n", encoding="utf-8")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".agy").symlink_to(outside, target_is_directory=True)
    job_dir = tmp_path / "job"
    job_dir.mkdir()

    copied, skipped = scratchpad.copy_to_jobdir(wt, job_dir)
    assert copied == []
    assert set(skipped) == set(scratchpad.SCRATCHPAD_FILES)
    assert not (job_dir / "scratchpad" / "PLAN.md").exists()
