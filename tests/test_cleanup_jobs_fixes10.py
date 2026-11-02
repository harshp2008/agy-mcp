"""Round 11 acceptance tests for cleanup_jobs / cleanup_fixtures (D100).

Confirmed finding: an EMPTY `--store` argument is not refused. argparse's
`type=Path` normalizes `""` to `Path(".")`, so the guard
`if not str(store_path).strip()` never fires; when the process CWD happens to
be a store directory, `--store ""` silently operates on that store -- and with
`--yes` it deletes rows there. SPEC §23 requires empty/whitespace-only
`--store` to be refused with exit 2 and a single-line stderr message.

These tests fail on the round-10 code and pass after the round-11 fix. Do not
edit after the Phase 1 commit.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_TIMEOUT_SECONDS = 15


def _cli_env(home_dir: Path) -> dict[str, str]:
    """Run cleaner code with HOME confined strictly to scratch tempdir."""
    env = os.environ.copy()
    prefix = str(REPO_ROOT / "src") + os.pathsep + str(REPO_ROOT)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = prefix + (os.pathsep + existing if existing else "")
    env["HOME"] = str(home_dir)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_AUTHOR_NAME"] = "Test Author"
    env["GIT_AUTHOR_EMAIL"] = "author@example.com"
    env["GIT_COMMITTER_NAME"] = "Test Committer"
    env["GIT_COMMITTER_EMAIL"] = "committer@example.com"
    return env


def _run(
    home_dir: Path,
    *args: object,
    cwd: Path = REPO_ROOT,
    expect: int | None = None,
    timeout: float = CLI_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Execute cleanup_jobs CLI entrypoint from cwd and optionally assert the exit code."""
    cmd = [sys.executable, "-m", "agy_mcp.cleanup_jobs", *[str(a) for a in args]]
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        env=_cli_env(home_dir),
        capture_output=True,
        text=True,
        timeout=timeout,
        errors="replace",
    )
    if expect is not None:
        assert proc.returncode == expect, (
            f"expected rc {expect}, got {proc.returncode}\n"
            f"cmd: {cmd}\ncwd: {cwd}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def _run_fixtures(
    home_dir: Path,
    *args: object,
    cwd: Path = REPO_ROOT,
    expect: int | None = None,
    timeout: float = CLI_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Execute cleanup_fixtures CLI entrypoint from cwd and optionally assert the exit code."""
    cmd = [sys.executable, "-m", "agy_mcp.cleanup_fixtures", *[str(a) for a in args]]
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        env=_cli_env(home_dir),
        capture_output=True,
        text=True,
        timeout=timeout,
        errors="replace",
    )
    if expect is not None:
        assert proc.returncode == expect, (
            f"expected rc {expect}, got {proc.returncode}\n"
            f"cmd: {cmd}\ncwd: {cwd}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def _old_time(days: float = 10.0) -> datetime:
    """A timestamp in the past, older than the default 7-day threshold."""
    return datetime.now(timezone.utc) - timedelta(days=days)


def _write_job(
    store: Path,
    *,
    job_id: str | None = None,
    state: str = "done",
    repo: Path | str | None = None,
    updated_at: datetime | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Write a valid job directory and job.json inside the store."""
    jid = job_id or str(uuid.uuid4())
    job_dir = store / "jobs" / jid
    job_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    created = updated_at or now
    data: dict[str, Any] = {
        "schema_version": 1,
        "job_id": jid,
        "title": "Round 11 Cleanup Test Job",
        "repo_path": str(repo) if repo is not None else "/nonexistent/repo",
        "branch": f"agy/cleanup-{jid[:8]}",
        "state": state,
        "created_at": created.isoformat(),
        "updated_at": created.isoformat(),
    }
    if extra:
        data.update(extra)
    (job_dir / "job.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    return jid


def _make_store(tmp_path: Path, repo: Path | str) -> Path:
    store = tmp_path / "store"
    (store / "jobs").mkdir(parents=True)
    return store


# ==============================================================================
# R17: Empty --store refusal (both CLIs, both spellings, from a store CWD)
# ==============================================================================


@pytest.mark.parametrize("store_args", [["--store", ""], ["--store="]])
def test_r17_empty_store_refused_cleanup_jobs_outside_store(
    tmp_path: Path, store_args: list[str]
) -> None:
    """R17: empty --store is refused with exit 2 and an explicit empty-path message."""
    store = _make_store(tmp_path, tmp_path / "repo")
    proc = _run(tmp_path, *store_args, "--list", expect=2)
    assert "Error:" in proc.stderr
    assert "empty or whitespace" in proc.stderr.lower(), proc.stderr
    assert "Traceback" not in proc.stderr
    # The refusal must happen before any store is touched.
    assert "Project:" not in proc.stdout
    # Leave the fixture store untouched for pytest cleanup.
    assert store.is_dir()


@pytest.mark.parametrize("store_args", [["--store", ""], ["--store="]])
def test_r17_empty_store_refused_cleanup_fixtures_outside_store(
    tmp_path: Path, store_args: list[str]
) -> None:
    """R17: empty --store is refused with exit 2 and an explicit empty-path message."""
    _make_store(tmp_path, "/tmp/pytest-of-testuser/pytest-1/test_r17_0/repo")
    proc = _run_fixtures(tmp_path, *store_args, expect=2)
    assert "Error:" in proc.stderr
    assert "empty or whitespace" in proc.stderr.lower(), proc.stderr
    assert "Traceback" not in proc.stderr


@pytest.mark.parametrize("store_args", [["--store", ""], ["--store="]])
def test_r17_empty_store_refused_cleanup_jobs_from_store_cwd(
    tmp_path: Path, store_args: list[str]
) -> None:
    """R17: empty --store run from inside a store must NOT silently scan that store."""
    store = _make_store(tmp_path, tmp_path / "repo")
    _write_job(store, repo=tmp_path / "repo", updated_at=_old_time())
    proc = _run(tmp_path, *store_args, "--list", cwd=store, expect=2)
    assert "Error:" in proc.stderr
    assert "empty or whitespace" in proc.stderr.lower(), proc.stderr
    assert "Project:" not in proc.stdout, proc.stdout


@pytest.mark.parametrize("store_args", [["--store", ""], ["--store="]])
def test_r17_empty_store_refused_cleanup_fixtures_from_store_cwd(
    tmp_path: Path, store_args: list[str]
) -> None:
    """R17: empty --store run from inside a fixtures store must NOT scan it silently."""
    pytest_repo = "/tmp/pytest-of-testuser/pytest-1/test_r17_1/repo"
    store = _make_store(tmp_path, pytest_repo)
    _write_job(store, repo=pytest_repo, updated_at=_old_time())
    proc = _run_fixtures(tmp_path, *store_args, cwd=store, expect=2)
    assert "Error:" in proc.stderr
    assert "empty or whitespace" in proc.stderr.lower(), proc.stderr
    assert "Candidates (1)" not in proc.stdout, proc.stdout


def test_r17_empty_store_yes_never_deletes_cleanup_jobs_from_store_cwd(tmp_path: Path) -> None:
    """R17: `--store "" --yes` from a store CWD must refuse and delete nothing."""
    store = _make_store(tmp_path, tmp_path / "repo")
    repo = tmp_path / "repo"
    repo.mkdir()
    jid = _write_job(store, repo=repo, updated_at=_old_time())
    proc = _run(tmp_path, "--store", "", "--project", repo, "--yes", cwd=store, expect=2)
    assert "Error:" in proc.stderr
    assert (store / "jobs" / jid).is_dir(), "empty --store must not delete from the CWD store"
    assert not list((store / "jobs").glob(".cleanup-manifest-*")), "no manifest may be written"


def test_r17_empty_store_yes_never_deletes_cleanup_fixtures_from_store_cwd(tmp_path: Path) -> None:
    """R17: `--store "" --yes` from a fixtures store CWD must refuse and delete nothing."""
    pytest_repo = "/tmp/pytest-of-testuser/pytest-1/test_r17_2/repo"
    store = _make_store(tmp_path, pytest_repo)
    jid = _write_job(store, repo=pytest_repo, updated_at=_old_time())
    proc = _run_fixtures(tmp_path, "--store", "", "--yes", cwd=store, expect=2)
    assert "Error:" in proc.stderr
    assert (store / "jobs" / jid).is_dir(), "empty --store must not delete from the CWD store"
    assert not list((store / "jobs").glob(".cleanup-manifest-*")), "no manifest may be written"


# ==============================================================================
# Controls: whitespace still refused; explicit relative "." store still works
# ==============================================================================


def test_r17_whitespace_store_still_refused_both_clis(tmp_path: Path) -> None:
    """Control: whitespace-only --store refusal is preserved in both CLIs."""
    _make_store(tmp_path, "/tmp/pytest-of-testuser/pytest-1/test_r17_3/repo")
    proc1 = _run(tmp_path, "--store", "   ", "--list", expect=2)
    assert "empty or whitespace" in proc1.stderr.lower()
    proc2 = _run_fixtures(tmp_path, "--store", "   ", expect=2)
    assert "empty or whitespace" in proc2.stderr.lower()


def test_r17_explicit_dot_store_still_works(tmp_path: Path) -> None:
    """Control: an EXPLICIT `--store .` from the store directory keeps working."""
    store = _make_store(tmp_path, tmp_path / "repo")
    _write_job(store, repo=tmp_path / "repo", updated_at=_old_time())
    proc = _run(tmp_path, "--store", ".", "--list", cwd=store, expect=0)
    assert "Project:" in proc.stdout
    proc_fix = _run_fixtures(tmp_path, "--store", ".",
                             cwd=_make_fixture_store(tmp_path), expect=0)
    assert "Candidates (1):" in proc_fix.stdout


def _make_fixture_store(home: Path) -> Path:
    """Create a *second* fixture store under home and return its directory."""
    pytest_repo = "/tmp/pytest-of-testuser/pytest-1/test_r17_4/repo"
    store = home / "store_fixtures"
    (store / "jobs").mkdir(parents=True, exist_ok=True)
    _write_job(store, repo=pytest_repo, updated_at=_old_time())
    return store
