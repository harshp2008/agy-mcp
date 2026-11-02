"""Acceptance tests for 7 cleaner review findings.

FROZEN tests committed before implementation.
Do not edit src/ or fix anything in this chat.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from agy_mcp.cleanup_fixtures import run_cleanup

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_TIMEOUT_SECONDS = 120


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _cli_env() -> dict[str, str]:
    """Run the worktree's code, not an editable install from another checkout."""
    env = os.environ.copy()
    prefix = str(REPO_ROOT / "src") + os.pathsep + str(REPO_ROOT)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = prefix + (os.pathsep + existing if existing else "")
    return env


def _run(*args: object, expect: int = 0) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "agy_mcp.cleanup_fixtures", *[str(a) for a in args]]
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=_cli_env(),
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT_SECONDS,
    )
    assert proc.returncode == expect, (
        f"expected rc {expect}, got {proc.returncode}\n"
        f"cmd: {cmd}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return proc


def _run_json(*args: object, expect: int = 0) -> dict[str, Any]:
    proc = _run("--json", *args, expect=expect)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"--json did not print valid JSON: {exc}\n{proc.stdout}") from exc


@pytest.fixture
def store() -> Path:
    """Create a temporary store inside session HOME for testing."""
    root = Path(os.environ["HOME"]) / "cleaner-fixes-tests" / uuid.uuid4().hex
    (root / "jobs").mkdir(parents=True)
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _old_time() -> datetime:
    """A timestamp 3 days in the past."""
    return datetime.now(timezone.utc) - timedelta(days=3)


def _pytest_repo(n: int = 1) -> Path:
    """A repository path matching the pytest temp directory whitelist signature."""
    return (
        Path(tempfile.gettempdir())
        / "pytest-of-cleanerfixes"
        / f"pytest-{n}"
        / "test_fixture0"
        / "repo"
    )


def _real_repo() -> Path:
    """A repository path outside tempdir representing a real user repo."""
    return Path(os.environ["HOME"]) / "Documents" / "real-project"


def _write_fixture_job(
    store: Path,
    *,
    job_id: str | None = None,
    state: str = "failed",
    repo: Path | None = None,
    updated_at: datetime | None = None,
    created_at: datetime | None = None,
    pid: int | None = None,
    conversation_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    jid = job_id or str(uuid.uuid4())
    job_dir = store / "jobs" / jid
    job_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    created = created_at or now
    updated = updated_at or created
    data: dict[str, Any] = {
        "schema_version": 1,
        "job_id": jid,
        "title": "Cleaner Fixes Fixture",
        "repo_path": str(repo if repo is not None else _pytest_repo()),
        "branch": f"agy/fixture-{jid[:8]}",
        "base_commit": "0" * 40,
        "head_commit": "",
        "state": state,
        "model": "gemini-3.8-flash-medium",
        "model_resolved": None,
        "max_minutes": 240,
        "verify": None,
        "verify_timeout_seconds": 900,
        "conversation_id": conversation_id,
        "pid": pid,
        "pgid": None,
        "start_time": None,
        "worktree_path": str(job_dir / "work"),
        "commit_msg_generation": 0,
        "review_reason": None,
        "source_dirty": False,
        "recovered": False,
        "created_at": created.isoformat(),
        "updated_at": updated.isoformat(),
    }
    if extra:
        data.update(extra)
    (job_dir / "job.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    return jid


def _ids(result: dict[str, Any], bucket: str) -> set[str]:
    return {entry["job_id"] for entry in result.get(bucket, [])}


# --------------------------------------------------------------------------- #
# Finding 1: Unreadable kept job directory fails closed
# --------------------------------------------------------------------------- #


def test_unreadable_kept_job_dir_fails_closed_dry_run_and_yes(store: Path) -> None:
    """Finding 1: Kept job directory with mode 0o000 causes fail-closed edge scan.

    All candidates must be kept, the reason must be stated, no traceback on stderr,
    dry run exits 0, and --yes deletes nothing.
    """
    if os.geteuid() == 0:
        pytest.skip("chmod permission denial unsupported as root")

    cand_id = _write_fixture_job(store, state="failed", updated_at=_old_time())

    kept_uuid = str(uuid.uuid4())
    kept_dir = store / "jobs" / kept_uuid
    kept_dir.mkdir(parents=True)
    job_file = kept_dir / "job.json"
    job_file.write_text(json.dumps({"job_id": kept_uuid, "state": "done"}), encoding="utf-8")
    os.chmod(kept_dir, 0o000)

    try:
        # Dry run: must exit 0, no traceback, fail closed (cand_id kept with reason)
        proc_dry = _run("--json", "--store", store, expect=0)
        assert "Traceback" not in proc_dry.stderr
        dry = json.loads(proc_dry.stdout)
        assert cand_id not in _ids(dry, "candidates"), (
            "candidate must not be in candidates when kept job dir is unreadable"
        )
        assert cand_id in _ids(dry, "kept"), "candidate must be kept (fail closed)"
        assert dry["candidate_count"] == 0
        cand_entry = next(entry for entry in dry["kept"] if entry["job_id"] == cand_id)
        assert cand_entry.get("reasons"), "reason must be stated for kept candidate"

        # Opt-in deletion (--yes): must exit 0, no traceback, and delete nothing
        proc_yes = _run("--yes", "--json", "--store", store, expect=0)
        assert "Traceback" not in proc_yes.stderr
        yes_res = json.loads(proc_yes.stdout)
        assert yes_res["deleted_count"] == 0
        assert yes_res["deleted_ids"] == []
        assert (store / "jobs" / cand_id).is_dir(), "candidate must not be deleted on disk"
    finally:
        os.chmod(kept_dir, 0o755)


# --------------------------------------------------------------------------- #
# Finding 2: UUID as a dict KEY
# --------------------------------------------------------------------------- #


def test_uuid_as_dict_key_in_kept_job_protects_candidate(store: Path) -> None:
    """Finding 2: Candidate UUID appearing only as a KEY in kept job.json is protected."""
    cand_direct = _write_fixture_job(store, state="failed", updated_at=_old_time())
    cand_nested = _write_fixture_job(store, state="failed", updated_at=_old_time())
    cand_unrelated = _write_fixture_job(store, state="failed", updated_at=_old_time())

    # Kept job has cand_direct as a top-level extra key and cand_nested deeply nested as a key
    _write_fixture_job(
        store,
        state="done",
        repo=_real_repo(),
        updated_at=_old_time(),
        extra={
            "direct_map": {cand_direct: "direct_ref"},
            "deep": {
                "level1": {
                    "level2": {
                        cand_nested: {"status": "nested_ref"}
                    }
                }
            },
        },
    )

    # Dry run: cand_direct and cand_nested must be kept with reasons; cand_unrelated is a candidate
    dry = _run_json("--store", store, expect=0)
    assert cand_direct not in _ids(dry, "candidates"), "cand_direct must be protected"
    assert cand_direct in _ids(dry, "kept")
    direct_entry = next(entry for entry in dry["kept"] if entry["job_id"] == cand_direct)
    assert direct_entry.get("reasons"), "reason must be stated for cand_direct"

    assert cand_nested not in _ids(dry, "candidates"), "cand_nested must be protected"
    assert cand_nested in _ids(dry, "kept")
    nested_entry = next(entry for entry in dry["kept"] if entry["job_id"] == cand_nested)
    assert nested_entry.get("reasons"), "reason must be stated for cand_nested"

    assert cand_unrelated in _ids(dry, "candidates")

    # Deletion: cand_direct and cand_nested are kept; cand_unrelated is deleted
    del_res = _run_json("--yes", "--store", store, expect=0)
    assert cand_direct not in del_res["deleted_ids"]
    assert cand_nested not in del_res["deleted_ids"]
    assert cand_unrelated in del_res["deleted_ids"]
    assert (store / "jobs" / cand_direct).is_dir()
    assert (store / "jobs" / cand_nested).is_dir()
    assert not (store / "jobs" / cand_unrelated).exists()


# --------------------------------------------------------------------------- #
# Finding 3: UUID inside a longer string
# --------------------------------------------------------------------------- #


def test_uuid_substring_in_kept_job_string_protects_candidate(store: Path) -> None:
    """Finding 3: Candidate UUID appearing as a substring inside a kept string protects it.

    Includes negative control: kept record mentioning an unrelated UUID does not protect candidate.
    """
    cand_sub = _write_fixture_job(store, state="failed", updated_at=_old_time())
    cand_ctrl = _write_fixture_job(store, state="failed", updated_at=_old_time())

    unrelated_uuid = str(uuid.uuid4())
    # Kept job 1 mentions cand_sub inside a branch string and title string
    _write_fixture_job(
        store,
        state="done",
        repo=_real_repo(),
        updated_at=_old_time(),
        extra={
            "title": f"Follow-up for {cand_sub} changes",
            "branch": f"agy/merged-fix-{cand_sub}-v2",
        },
    )
    # Kept job 2 mentions unrelated_uuid (negative control; does not mention cand_ctrl)
    _write_fixture_job(
        store,
        state="done",
        repo=_real_repo(),
        updated_at=_old_time(),
        extra={
            "title": f"Follow-up for {unrelated_uuid}",
            "branch": f"agy/merged-fix-{unrelated_uuid}",
        },
    )

    # Dry run: cand_sub protected; cand_ctrl not protected
    dry = _run_json("--store", store, expect=0)
    assert cand_sub not in _ids(dry, "candidates"), "cand_sub must be protected by substring match"
    assert cand_sub in _ids(dry, "kept")
    sub_entry = next(entry for entry in dry["kept"] if entry["job_id"] == cand_sub)
    assert sub_entry.get("reasons"), "reason must be stated for cand_sub"

    assert cand_ctrl in _ids(dry, "candidates"), "cand_ctrl must remain candidate"
    assert cand_ctrl not in _ids(dry, "kept")

    # Deletion: cand_sub preserved; cand_ctrl deleted
    del_res = _run_json("--yes", "--store", store, expect=0)
    assert cand_sub not in del_res["deleted_ids"]
    assert cand_ctrl in del_res["deleted_ids"]
    assert (store / "jobs" / cand_sub).is_dir()
    assert not (store / "jobs" / cand_ctrl).exists()


# --------------------------------------------------------------------------- #
# Finding 4: Re-validation right before each deletion
# --------------------------------------------------------------------------- #


def test_revalidation_before_delete_skips_mutated_row(store: Path) -> None:
    """Finding 4: run_cleanup accepts before_delete and skips re-classified rows."""
    cand_mutated = _write_fixture_job(store, state="failed", updated_at=_old_time())
    cand_normal = _write_fixture_job(store, state="failed", updated_at=_old_time())

    def before_delete_cb(job_id: str) -> None:
        if job_id == cand_mutated:
            job_file = store / "jobs" / cand_mutated / "job.json"
            data = json.loads(job_file.read_text(encoding="utf-8"))
            data["state"] = "running"
            data["pid"] = os.getpid()
            job_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    result = run_cleanup(store, dry_run=False, before_delete=before_delete_cb)

    assert (store / "jobs" / cand_mutated).is_dir(), "mutated row must remain on disk"
    assert not (store / "jobs" / cand_normal).exists(), "normal candidate must be deleted"

    assert cand_mutated not in result["deleted_ids"]
    assert cand_normal in result["deleted_ids"]
    assert result["deleted_count"] == 1

    # Reported in output as skipped with a reason
    skipped_or_kept = {
        entry["job_id"]: entry.get("reasons", entry.get("reason", []))
        for entry in result.get("skipped", []) + result.get("kept", [])
        if isinstance(entry, dict) and "job_id" in entry
    }
    assert cand_mutated in skipped_or_kept, "mutated row must be reported in kept or skipped"
    assert skipped_or_kept[cand_mutated], f"mutated row {cand_mutated} must have a reason"

    manifest_path = Path(result["manifest"])
    assert manifest_path.is_file()
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert cand_mutated not in manifest_data["removed_ids"]
    assert cand_normal in manifest_data["removed_ids"]


# --------------------------------------------------------------------------- #
# Finding 5: Honest manifest
# --------------------------------------------------------------------------- #


def test_manifest_planned_before_deletion_and_completed_after(store: Path) -> None:
    """Finding 5 (completed): manifest is written before deletion as planned, then completed."""
    cand1 = _write_fixture_job(store, state="failed", updated_at=_old_time())
    cand2 = _write_fixture_job(store, state="cancelled", updated_at=_old_time())

    planned_manifests: list[dict[str, Any]] = []

    def before_delete_hook(job_id: str) -> None:
        manifest_files = list((store / "jobs").glob(".cleanup-manifest-*.json"))
        if manifest_files and not planned_manifests:
            content = json.loads(manifest_files[0].read_text(encoding="utf-8"))
            planned_manifests.append(content)

    result = run_cleanup(store, dry_run=False, before_delete=before_delete_hook)

    # Manifest before deletion
    assert len(planned_manifests) == 1, "manifest must exist before deletion"
    planned = planned_manifests[0]
    assert planned.get("status") == "planned"
    assert set(planned.get("planned_ids", [])) == {cand1, cand2}
    assert all(isinstance(x, str) for x in planned["planned_ids"])

    # Manifest after completion
    manifest_path = Path(result["manifest"])
    assert manifest_path.is_file()
    completed = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert completed.get("status") == "completed"
    assert set(completed.get("removed_ids", [])) == {cand1, cand2}
    assert completed.get("failed_ids") == []
    assert all(isinstance(x, str) for x in completed["removed_ids"])
    assert all(isinstance(x, str) for x in completed["failed_ids"])


def test_manifest_partial_on_failed_deletion_and_cli_exits_nonzero(store: Path) -> None:
    """Finding 5 (partial): failed deletion never in removed_ids, CLI exits non-zero."""
    if os.geteuid() == 0:
        pytest.skip("chmod permission denial unsupported as root")

    # Use ordered UUIDs so cand_fail is sandwiched between cand_first and cand_last
    cand_first = _write_fixture_job(
        store,
        job_id="00000000-0000-4000-8000-000000000001",
        state="failed",
        updated_at=_old_time(),
    )
    cand_fail = _write_fixture_job(
        store,
        job_id="77777777-7777-4777-8777-777777777777",
        state="failed",
        updated_at=_old_time(),
    )
    cand_last = _write_fixture_job(
        store,
        job_id="ffffffff-ffff-4fff-8fff-ffffffffffff",
        state="failed",
        updated_at=_old_time(),
    )

    # Make cand_fail have an unwritable subdirectory to force deletion failure
    fail_sub = store / "jobs" / cand_fail / "unwritable"
    fail_sub.mkdir()
    (fail_sub / "nested.txt").write_text("protected", encoding="utf-8")
    os.chmod(fail_sub, 0o555)

    try:
        cmd = [sys.executable, "-m", "agy_mcp.cleanup_fixtures", "--yes", "--store", str(store)]
        proc = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            env=_cli_env(),
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT_SECONDS,
        )
        assert proc.returncode != 0, f"partial cleanup must exit non-zero, got {proc.returncode}"
        combined_output = proc.stdout + proc.stderr
        assert cand_fail in combined_output, "CLI output must print which ids failed"

        # Other rows must still be deleted
        assert not (store / "jobs" / cand_first).exists(), "cand_first must be deleted"
        assert not (store / "jobs" / cand_last).exists(), "cand_last must be deleted"
        # Failed row must still be on disk
        assert (store / "jobs" / cand_fail).is_dir(), "cand_fail must still be on disk"

        # Manifest must reflect partial status
        manifest_files = list((store / "jobs").glob(".cleanup-manifest-*.json"))
        assert len(manifest_files) == 1, "manifest file must exist"
        manifest = json.loads(manifest_files[0].read_text(encoding="utf-8"))

        assert manifest.get("status") == "partial"
        assert cand_fail not in manifest.get("removed_ids", [])
        assert set(manifest.get("removed_ids", [])) == {cand_first, cand_last}
        assert cand_fail in manifest.get("failed_ids", [])
        assert cand_first not in manifest.get("failed_ids", [])
        assert cand_last not in manifest.get("failed_ids", [])
        assert all(isinstance(x, str) for x in manifest["removed_ids"])
        assert all(isinstance(x, str) for x in manifest["failed_ids"])
    finally:
        os.chmod(fail_sub, 0o755)


# --------------------------------------------------------------------------- #
# Finding 6: Unsearchable ancestor of --store
# --------------------------------------------------------------------------- #


def test_unsearchable_ancestor_refused_exit_2_no_traceback() -> None:
    """Finding 6: An unsearchable ancestor of --store must exit 2 with no traceback."""
    if os.geteuid() == 0:
        pytest.skip("chmod permission denial unsupported as root")

    base = Path(os.environ["HOME"]) / "cleaner-ancestor-test" / uuid.uuid4().hex
    base.mkdir(parents=True, exist_ok=True)
    unsearchable = base / "no_search"
    unsearchable.mkdir()
    store = unsearchable / "store"
    (store / "jobs").mkdir(parents=True)
    _write_fixture_job(store, state="failed", updated_at=_old_time())
    os.chmod(unsearchable, 0o000)

    try:
        proc = _run("--store", store, expect=2)
        assert "Traceback" not in proc.stderr
        assert "Traceback" not in proc.stdout
        err = (proc.stderr + proc.stdout).lower()
        assert any(
            term in err
            for term in (
                "permission",
                "ancestor",
                "cannot access",
                "unreadable",
                "search",
                "error",
            )
        ), f"expected clear refusal message, got:\n{proc.stderr}"
    finally:
        os.chmod(unsearchable, 0o755)
        shutil.rmtree(base, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Finding 7: --json output under --yes preserves candidates and counts
# --------------------------------------------------------------------------- #


def test_json_output_under_yes_preserves_candidates_and_counts(store: Path) -> None:
    """Finding 7: --json output under --yes preserves candidates list and candidate_count."""
    cand1 = _write_fixture_job(store, state="failed", updated_at=_old_time())
    cand2 = _write_fixture_job(store, state="cancelled", updated_at=_old_time())

    result = _run_json("--yes", "--store", store, expect=0)

    assert result["dry_run"] is False

    # Candidates list still lists every candidate found before deletion
    candidate_ids = {c["job_id"] for c in result.get("candidates", [])}
    assert candidate_ids == {cand1, cand2}
    assert result["candidate_count"] == len(result["candidates"])
    assert result["candidate_count"] == 2

    # deleted_ids and deleted_count describe what was removed
    assert set(result.get("deleted_ids", [])) == {cand1, cand2}
    assert result["deleted_count"] == len(result["deleted_ids"])
    assert result["deleted_count"] == 2

    # Filesystem check: actually deleted
    assert not (store / "jobs" / cand1).exists()
    assert not (store / "jobs" / cand2).exists()
