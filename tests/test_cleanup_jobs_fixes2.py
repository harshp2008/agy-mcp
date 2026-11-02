"""Acceptance tests for 4 cleanup_jobs review findings (fix round 2).

FROZEN tests committed before implementation.
Do not edit src/ or fix anything in this chat.

Test coverage for the 4 review findings:
1. Fail closed in the pre-delete edge recheck:
   - test_predelete_edge_recheck_fail_closed_corrupt_json:
     K/job.json rewritten to invalid JSON containing C1 UUID before delete. C1 kept, failed_ids.
   - test_predelete_edge_recheck_fail_closed_non_object:
     Variant (a): K/job.json becomes a JSON non-object. C1 kept, reported in failed_ids.
   - test_predelete_edge_recheck_fail_closed_chmod_000:
     Variant (b): K/job.json is chmod 0o000. C1 kept, reported in failed_ids.
   - test_predelete_edge_recheck_two_candidates_race_stops_deletion:
     Two candidates C1 and C2, race before C1 stops deletion entirely. Neither deleted,
     both in failed_ids with same blocker reason, manifest status partial.
2. Non-UTF-8 git metadata must fail closed, not open:
   - test_non_utf8_gitdir_in_worktrees_fails_closed:
     2(a): repo/.git/worktrees/ has entry with invalid UTF-8 gitdir; job kept.
   - test_non_utf8_commondir_linked_worktree_fails_closed:
     2(b): commondir of linked-worktree repo has invalid UTF-8; job kept.
   - test_non_utf8_git_file_repo_path_fails_closed:
     2(c): repo_path/.git file has invalid UTF-8; job kept.
3. Exit code in --job mode when an eligible job cannot be deleted:
   - test_job_mode_exit_1_on_undeletable_directory:
     Eligible job with undeletable subdirectory (chmod 0o500) exits 1 with --yes,
     manifest status partial, failed_ids has id, removed_ids empty, planned_ids has id.
   - test_job_mode_exit_2_when_all_ineligible_contrast:
     Contrast case: every id is ineligible (state running); exits 2, no manifest written.
4. Over-blocking must be explainable:
   - test_overblocking_explainable_project_mode:
     4(a): valid J1 (project A) and corrupt J2 (project B); --project A human and --json
     name J2 UUID, full path to J2, say left untouched, advise inspect/remove manually; J1 kept.
   - test_overblocking_explainable_list_mode:
     4(b): --list human outputs warning naming J2 UUID; --list --json has top-level warnings
     list containing J2 UUID; eligible_count 0 for all; writes nothing, mtimes unchanged.
   - test_corrupt_record_job_mode_not_deleted:
     4(c): --job J2 --yes still exits 2 and J2 remains on disk.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from agy_mcp.cleanup_jobs import run_cleanup
from tests.helpers.make_repo import get_safe_git_env, make_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_TIMEOUT_SECONDS = 120


# --------------------------------------------------------------------------- #
# CLI test helpers
# --------------------------------------------------------------------------- #


def _cli_env() -> dict[str, str]:
    """Run the worktree's code, not an editable install from another checkout."""
    env = os.environ.copy()
    prefix = str(REPO_ROOT / "src") + os.pathsep + str(REPO_ROOT)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = prefix + (os.pathsep + existing if existing else "")
    return env


def _run(*args: object, expect: int = 0) -> subprocess.CompletedProcess[str]:
    """Execute the cleanup_jobs CLI entrypoint and assert expected exit code."""
    cmd = [sys.executable, "-m", "agy_mcp.cleanup_jobs", *[str(a) for a in args]]
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
    """Execute the cleanup_jobs CLI with --json and return parsed JSON object."""
    proc = _run("--json", *args, expect=expect)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"--json did not print valid JSON: {exc}\n{proc.stdout}") from exc


@pytest.fixture
def store() -> Path:
    """Create a temporary store inside session HOME for testing."""
    root = Path(os.environ["HOME"]) / "cleanup-jobs-fixes2-tests" / uuid.uuid4().hex
    (root / "jobs").mkdir(parents=True)
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _old_time(days: float = 10.0) -> datetime:
    """A timestamp in the past, older than the default 7-day threshold."""
    return datetime.now(timezone.utc) - timedelta(days=days)


def _recent_time(days: float = 2.0) -> datetime:
    """A timestamp in the past, fresher than the default 7-day threshold."""
    return datetime.now(timezone.utc) - timedelta(days=days)


def _write_job(
    store: Path,
    *,
    job_id: str | None = None,
    state: str = "done",
    repo: Path | str | None = None,
    updated_at: datetime | None = None,
    created_at: datetime | None = None,
    pid: int | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Write a valid job directory and job.json inside the store."""
    jid = job_id or str(uuid.uuid4())
    job_dir = store / "jobs" / jid
    job_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    created = created_at or now
    updated = updated_at or created
    repo_val = str(repo) if repo is not None else str(Path(os.environ["HOME"]) / "default-project")
    data: dict[str, Any] = {
        "schema_version": 1,
        "job_id": jid,
        "title": "Cleanup Fixes 2 Test Job",
        "repo_path": repo_val,
        "branch": f"agy/cleanup-{jid[:8]}",
        "base_commit": "0" * 40,
        "head_commit": "",
        "state": state,
        "model": "gemini-3.8-flash-medium",
        "model_resolved": None,
        "max_minutes": 240,
        "verify": None,
        "verify_timeout_seconds": 900,
        "conversation_id": None,
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
    """Extract job_id strings from a result bucket (list of dicts or list of strings)."""
    entries = result.get(bucket, [])
    if isinstance(entries, list):
        out: set[str] = set()
        for e in entries:
            if isinstance(e, dict) and "job_id" in e:
                out.add(str(e["job_id"]))
            elif isinstance(e, str):
                out.add(e)
        return out
    return set()


# --------------------------------------------------------------------------- #
# Finding 1: Fail closed in the pre-delete edge recheck
# --------------------------------------------------------------------------- #


def test_predelete_edge_recheck_fail_closed_corrupt_json(store: Path) -> None:
    """Pre-delete recheck fail-closed: kept job rewritten to invalid JSON keeping candidate."""
    repo = Path(os.environ["HOME"]) / "d1_corrupt_proj"
    repo.mkdir(parents=True, exist_ok=True)

    cand = _write_job(store, repo=repo, state="done", updated_at=_old_time())
    kept = _write_job(store, repo=repo, state="running", updated_at=_recent_time())

    def before_delete_cb(cid: str) -> None:
        if cid == cand:
            jfile = store / "jobs" / kept / "job.json"
            jfile.write_text(f"{{ corrupt: json referencing {cand}", encoding="utf-8")

    res = run_cleanup(store, project=repo, dry_run=False, before_delete=before_delete_cb)

    assert (
        store / "jobs" / cand
    ).is_dir(), "candidate C1 must remain on disk when kept record becomes corrupt"
    assert cand not in res.get("deleted_ids", []), "C1 must NOT be in deleted_ids"
    assert cand in res.get("failed_ids", []), "C1 must be reported in failed_ids"

    skipped_c1 = next((s for s in res.get("skipped", []) if s.get("job_id") == cand), None)
    assert skipped_c1 is not None, f"C1 must be reported in skipped: {res}"
    reasons = skipped_c1.get("reasons", [])
    assert any("corrupt" in r.lower() or "unreadable" in r.lower() for r in reasons), (
        f"reason must mention corrupt/unreadable kept record: {reasons}"
    )

    assert res.get("manifest") is not None
    mdata = json.loads(Path(res["manifest"]).read_text(encoding="utf-8"))
    assert mdata.get("status") == "partial", f"manifest status must be 'partial': {mdata}"
    assert cand not in mdata.get("removed_ids", []), "C1 must NOT be in manifest removed_ids"
    assert cand in mdata.get("failed_ids", []), "C1 must be in manifest failed_ids"


def test_predelete_edge_recheck_fail_closed_non_object(store: Path) -> None:
    """Pre-delete recheck fail-closed: kept job rewritten to JSON non-object keeping candidate."""
    repo = Path(os.environ["HOME"]) / "d1_nonobj_proj"
    repo.mkdir(parents=True, exist_ok=True)

    cand = _write_job(store, repo=repo, state="done", updated_at=_old_time())
    kept = _write_job(store, repo=repo, state="running", updated_at=_recent_time())

    def before_delete_cb(cid: str) -> None:
        if cid == cand:
            jfile = store / "jobs" / kept / "job.json"
            jfile.write_text(f'["not", "a", "dict", "{cand}"]\n', encoding="utf-8")

    res = run_cleanup(store, project=repo, dry_run=False, before_delete=before_delete_cb)

    assert (
        store / "jobs" / cand
    ).is_dir(), "candidate C1 must remain on disk when kept record becomes non-object"
    assert cand not in res.get("deleted_ids", []), "C1 must NOT be in deleted_ids"
    assert cand in res.get("failed_ids", []), "C1 must be reported in failed_ids"

    skipped_c1 = next((s for s in res.get("skipped", []) if s.get("job_id") == cand), None)
    assert skipped_c1 is not None, f"C1 must be reported in skipped: {res}"
    reasons = skipped_c1.get("reasons", [])
    assert any(
        "corrupt" in r.lower() or "unreadable" in r.lower() or "non-object" in r.lower()
        for r in reasons
    ), f"reason must mention corrupt/unreadable/non-object kept record: {reasons}"

    assert res.get("manifest") is not None
    mdata = json.loads(Path(res["manifest"]).read_text(encoding="utf-8"))
    assert mdata.get("status") == "partial", f"manifest status must be 'partial': {mdata}"
    assert cand not in mdata.get("removed_ids", [])
    assert cand in mdata.get("failed_ids", [])


def test_predelete_edge_recheck_fail_closed_chmod_000(store: Path) -> None:
    """Pre-delete recheck fail-closed: kept job chmod 0o000 keeping candidate."""
    if os.geteuid() == 0:
        pytest.skip("cannot test chmod-based denial as root")

    repo = Path(os.environ["HOME"]) / "d1_chmod_proj"
    repo.mkdir(parents=True, exist_ok=True)

    cand = _write_job(store, repo=repo, state="done", updated_at=_old_time())
    kept = _write_job(store, repo=repo, state="running", updated_at=_recent_time())
    k_file = store / "jobs" / kept / "job.json"

    def before_delete_cb(cid: str) -> None:
        if cid == cand:
            os.chmod(k_file, 0o000)

    try:
        res = run_cleanup(store, project=repo, dry_run=False, before_delete=before_delete_cb)
        assert (
            store / "jobs" / cand
        ).is_dir(), "candidate C1 must remain on disk when kept record becomes unreadable"
        assert cand not in res.get("deleted_ids", []), "C1 must NOT be in deleted_ids"
        assert cand in res.get("failed_ids", []), "C1 must be reported in failed_ids"

        skipped_c1 = next((s for s in res.get("skipped", []) if s.get("job_id") == cand), None)
        assert skipped_c1 is not None, f"C1 must be reported in skipped: {res}"
        reasons = skipped_c1.get("reasons", [])
        assert any("unreadable" in r.lower() or "corrupt" in r.lower() for r in reasons), (
            f"reason must mention unreadable/corrupt kept record: {reasons}"
        )

        assert res.get("manifest") is not None
        mdata = json.loads(Path(res["manifest"]).read_text(encoding="utf-8"))
        assert mdata.get("status") == "partial", f"manifest status must be 'partial': {mdata}"
        assert cand not in mdata.get("removed_ids", [])
        assert cand in mdata.get("failed_ids", [])
    finally:
        os.chmod(k_file, 0o600)


def test_predelete_edge_recheck_two_candidates_race_stops_deletion(store: Path) -> None:
    """With two candidates C1 and C2, race before C1 stops deleting both candidates."""
    repo = Path(os.environ["HOME"]) / "d1_two_cand_proj"
    repo.mkdir(parents=True, exist_ok=True)

    c1 = "00000000-0000-4000-8000-000000000001"
    c2 = "00000000-0000-4000-8000-000000000002"
    k = "00000000-0000-4000-8000-000000000009"

    _write_job(store, job_id=c1, repo=repo, state="done", updated_at=_old_time())
    _write_job(store, job_id=c2, repo=repo, state="done", updated_at=_old_time())
    _write_job(store, job_id=k, repo=repo, state="running", updated_at=_recent_time())

    def before_delete_cb(cid: str) -> None:
        if cid == c1:
            jfile = store / "jobs" / k / "job.json"
            jfile.write_text(f"{{ corrupt json referencing {c1} and {c2}", encoding="utf-8")

    res = run_cleanup(store, project=repo, dry_run=False, before_delete=before_delete_cb)

    assert (
        store / "jobs" / c1
    ).is_dir(), (
        "candidate C1 must remain on disk when pre-delete edge recheck detects corrupt kept record"
    )
    assert (
        store / "jobs" / c2
    ).is_dir(), (
        "candidate C2 must remain on disk (deletion stopped after corrupt kept record detected)"
    )

    assert c1 not in res.get("deleted_ids", []), "C1 must NOT be in deleted_ids"
    assert c2 not in res.get("deleted_ids", []), "C2 must NOT be in deleted_ids"
    assert c1 in res.get("failed_ids", []), "C1 must be in failed_ids"
    assert c2 in res.get("failed_ids", []), "C2 must be in failed_ids"

    s1 = next((s for s in res.get("skipped", []) if s.get("job_id") == c1), None)
    s2 = next((s for s in res.get("skipped", []) if s.get("job_id") == c2), None)
    assert s1 is not None, f"C1 must be in skipped: {res}"
    assert s2 is not None, f"C2 must be in skipped: {res}"

    r1 = s1.get("reasons", [])
    r2 = s2.get("reasons", [])
    assert any("corrupt" in r.lower() or "unreadable" in r.lower() for r in r1), (
        f"C1 reason must mention corrupt/unreadable: {r1}"
    )
    assert any("corrupt" in r.lower() or "unreadable" in r.lower() for r in r2), (
        f"C2 reason must mention corrupt/unreadable: {r2}"
    )

    assert res.get("manifest") is not None
    mdata = json.loads(Path(res["manifest"]).read_text(encoding="utf-8"))
    assert mdata.get("status") == "partial", f"manifest status must be 'partial': {mdata}"
    assert mdata.get("mode") == "project", f"manifest mode must be 'project': {mdata}"
    assert c1 not in mdata.get("removed_ids", [])
    assert c2 not in mdata.get("removed_ids", [])
    assert c1 in mdata.get("failed_ids", [])
    assert c2 in mdata.get("failed_ids", [])


# --------------------------------------------------------------------------- #
# Finding 2: Non-UTF-8 git metadata must fail closed, not open
# --------------------------------------------------------------------------- #


def test_non_utf8_gitdir_in_worktrees_fails_closed(store: Path, tmp_path: Path) -> None:
    """Non-UTF-8 gitdir in .git/worktrees/ fails closed: job is kept, not deleted."""
    repo_path = make_repo(tmp_path / "repo_wt_nonutf8")
    jid = _write_job(store, repo=repo_path, state="done", updated_at=_old_time())
    worktree_path = store / "jobs" / jid / "work"

    # Add real worktree via git
    subprocess.run(
        [
            "git",
            "-C",
            str(repo_path),
            "worktree",
            "add",
            str(worktree_path),
            "-b",
            f"agy/wt-{jid[:8]}",
            "HEAD",
        ],
        env=get_safe_git_env(repo_path.parent),
        check=True,
        capture_output=True,
    )
    # Remove work/.git so detection must read repo/.git/worktrees/
    (worktree_path / ".git").unlink()

    # Create entry e1 before existing entry so it is visited first
    wt_root = repo_path / ".git" / "worktrees"
    existing_entries = [p for p in wt_root.iterdir() if p.is_dir()]
    assert existing_entries, "expected git worktree entry in repo/.git/worktrees"
    e2 = existing_entries[0]

    # Recreate wt_root with e1 first, then e2
    saved_e2_gitdir = (e2 / "gitdir").read_bytes()
    shutil.rmtree(wt_root, ignore_errors=True)
    wt_root.mkdir(parents=True)

    e1 = wt_root / "e1"
    e1.mkdir()
    (e1 / "gitdir").write_bytes(b"\xff\xfe")

    e2_new = wt_root / "e2"
    e2_new.mkdir()
    (e2_new / "gitdir").write_bytes(saved_e2_gitdir)

    # Ensure e1 is visited before e2
    assert [p.name for p in wt_root.iterdir() if p.is_dir()] == ["e1", "e2"]

    # Dry-run: exit 0, job kept, reason mentions worktree registered OR unreadable registrations
    dry = _run_json("--store", store, "--project", repo_path, expect=0)
    assert jid in _ids(dry, "kept"), f"job {jid} must be kept: {dry}"
    assert jid not in _ids(dry, "candidates"), f"job {jid} must NOT be candidate: {dry}"

    kept_entry = next(e for e in dry["kept"] if e["job_id"] == jid)
    reasons = kept_entry.get("reasons", [])
    assert any("worktree" in r.lower() or "unreadable" in r.lower() for r in reasons), (
        f"reason must mention worktree registered or unreadable registrations: {reasons}"
    )

    # With --yes: nothing deleted, exit 0, no traceback
    proc = _run("--store", store, "--project", repo_path, "--yes", expect=0)
    assert "traceback" not in proc.stderr.lower()
    assert (store / "jobs" / jid).is_dir(), "job must stay on disk with --yes"


def test_non_utf8_commondir_linked_worktree_fails_closed(store: Path, tmp_path: Path) -> None:
    """Non-UTF-8 commondir in linked worktree repo fails closed: job is kept, not deleted."""
    main_repo = make_repo(tmp_path / "main_repo_2b")
    linked_repo = tmp_path / "linked_repo_2b"
    subprocess.run(
        [
            "git",
            "-C",
            str(main_repo),
            "worktree",
            "add",
            str(linked_repo),
            "-b",
            "linked-proj-2b",
            "HEAD",
        ],
        env=get_safe_git_env(main_repo.parent),
        check=True,
        capture_output=True,
    )
    assert (linked_repo / ".git").is_file(), "linked worktree .git must be a file"

    jid = _write_job(store, repo=linked_repo, state="done", updated_at=_old_time())
    worktree_path = store / "jobs" / jid / "work"

    subprocess.run(
        [
            "git",
            "-C",
            str(linked_repo),
            "worktree",
            "add",
            str(worktree_path),
            "-b",
            f"agy/wt-{jid[:8]}",
            "HEAD",
        ],
        env=get_safe_git_env(linked_repo.parent),
        check=True,
        capture_output=True,
    )
    # Remove work/.git so detection must follow gitdir -> commondir -> worktrees
    (worktree_path / ".git").unlink()

    # Find commondir file in linked_repo's gitdir
    git_file_content = (linked_repo / ".git").read_text(encoding="utf-8").strip()
    raw_gitdir = git_file_content[len("gitdir:") :].strip()
    actual_gitdir = (
        Path(raw_gitdir) if os.path.isabs(raw_gitdir) else (linked_repo / raw_gitdir).resolve()
    )
    commondir_file = actual_gitdir / "commondir"
    assert commondir_file.is_file(), "commondir file must exist in linked worktree gitdir"

    # Overwrite commondir with invalid UTF-8 bytes
    commondir_file.write_bytes(b"\xff\xfe")

    # Dry-run: exit 0, job kept, reason mentions worktree registered OR unreadable registrations
    dry = _run_json("--store", store, "--project", linked_repo, expect=0)
    assert jid in _ids(dry, "kept"), f"job {jid} must be kept: {dry}"
    assert jid not in _ids(dry, "candidates"), f"job {jid} must NOT be candidate: {dry}"

    kept_entry = next(e for e in dry["kept"] if e["job_id"] == jid)
    reasons = kept_entry.get("reasons", [])
    assert any("worktree" in r.lower() or "unreadable" in r.lower() for r in reasons), (
        f"reason must mention worktree registered or unreadable registrations: {reasons}"
    )

    # With --yes: nothing deleted, exit 0, no traceback
    proc = _run("--store", store, "--project", linked_repo, "--yes", expect=0)
    assert "traceback" not in proc.stderr.lower()
    assert (store / "jobs" / jid).is_dir(), "job must stay on disk with --yes"


def test_non_utf8_git_file_repo_path_fails_closed(store: Path, tmp_path: Path) -> None:
    """Non-UTF-8 repo_path/.git file fails closed: job is kept, not deleted."""
    repo_path = tmp_path / "repo_invalid_git_file"
    repo_path.mkdir(parents=True)
    # Write invalid UTF-8 bytes to repo_path/.git file
    (repo_path / ".git").write_bytes(b"\xff\xfe")

    jid = _write_job(store, repo=repo_path, state="done", updated_at=_old_time())

    # Dry-run: exit 0, job kept, reason mentions worktree registered OR unreadable registrations
    dry = _run_json("--store", store, "--project", repo_path, expect=0)
    assert jid in _ids(dry, "kept"), f"job {jid} must be kept: {dry}"
    assert jid not in _ids(dry, "candidates"), f"job {jid} must NOT be candidate: {dry}"

    kept_entry = next(e for e in dry["kept"] if e["job_id"] == jid)
    reasons = kept_entry.get("reasons", [])
    assert any("worktree" in r.lower() or "unreadable" in r.lower() for r in reasons), (
        f"reason must mention worktree registered or unreadable registrations: {reasons}"
    )

    # With --yes: nothing deleted, exit 0, no traceback
    proc = _run("--store", store, "--project", repo_path, "--yes", expect=0)
    assert "traceback" not in proc.stderr.lower()
    assert (store / "jobs" / jid).is_dir(), "job must stay on disk with --yes"


# --------------------------------------------------------------------------- #
# Finding 3: Exit code in --job mode when eligible job cannot be deleted
# --------------------------------------------------------------------------- #


def test_job_mode_exit_1_on_undeletable_directory(store: Path) -> None:
    """In --job mode with --yes, undeletable candidate exits 1 and manifest partial."""
    if os.geteuid() == 0:
        pytest.skip("cannot test chmod-based denial as root")

    jid = _write_job(store, state="done", updated_at=_old_time())

    # Make the job directory undeletable via a read-only subdirectory
    locked_sub = store / "jobs" / jid / "locked_sub"
    locked_sub.mkdir(parents=True, exist_ok=True)
    (locked_sub / "file.txt").write_text("locked", encoding="utf-8")
    os.chmod(locked_sub, 0o500)

    try:
        proc = _run("--store", store, "--job", jid, "--yes", expect=1)
        assert "traceback" not in proc.stderr.lower()

        # Check manifest
        manifests = list((store / "jobs").glob(".cleanup-manifest-*.json"))
        assert manifests, "Manifest must be written"
        latest_manifest = sorted(manifests)[-1]
        mdata = json.loads(latest_manifest.read_text(encoding="utf-8"))

        assert mdata.get("status") == "partial", f"manifest status must be 'partial': {mdata}"
        assert mdata.get("failed_ids") == [jid], f"failed_ids must contain {jid}: {mdata}"
        assert mdata.get("removed_ids") == [], f"removed_ids must be empty: {mdata}"
        assert mdata.get("planned_ids") == [jid], f"planned_ids must contain {jid}: {mdata}"
    finally:
        os.chmod(locked_sub, 0o700)


def test_job_mode_exit_2_when_all_ineligible_contrast(store: Path) -> None:
    """Contrast case: in --job mode with --yes, all ineligible ids exit 2 with no manifest."""
    jid = _write_job(store, state="running", updated_at=_old_time())

    proc = _run("--store", store, "--job", jid, "--yes", expect=2)
    assert "traceback" not in proc.stderr.lower()

    # No manifest written
    manifests = list((store / "jobs").glob(".cleanup-manifest-*.json"))
    assert not manifests, f"No manifest must be written when all jobs ineligible: {manifests}"
    assert (store / "jobs" / jid).is_dir(), "job must remain on disk"


# --------------------------------------------------------------------------- #
# Finding 4: Over-blocking must be explainable
# --------------------------------------------------------------------------- #


def test_overblocking_explainable_project_mode(store: Path) -> None:
    """In --project mode with corrupt kept record, output explains over-blocking with path."""
    repo_a = Path(os.environ["HOME"]) / "proj_a_explainable"
    repo_b = Path(os.environ["HOME"]) / "proj_b_corrupt"
    repo_a.mkdir(parents=True, exist_ok=True)
    repo_b.mkdir(parents=True, exist_ok=True)

    j1 = _write_job(store, repo=repo_a, state="done", updated_at=_old_time())
    j2 = "00000000-0000-4000-8000-000000000002"
    j2_dir = store / "jobs" / j2
    j2_dir.mkdir(parents=True)
    (j2_dir / "job.json").write_text("{this is corrupt json", encoding="utf-8")
    j2_path_str = str(j2_dir.resolve())

    # 1. Human output: --project A
    proc = _run("--store", store, "--project", repo_a, expect=0)
    stdout = proc.stdout
    assert j2 in stdout, f"human output must name J2 UUID ({j2}):\n{stdout}"
    assert (
        j2_path_str in stdout
    ), f"human output must name full path to J2 ({j2_path_str}):\n{stdout}"
    assert (
        "untouched" in stdout.lower()
    ), f"human output must state J2 was left untouched:\n{stdout}"
    assert (
        "inspect" in stdout.lower() or "manually" in stdout.lower()
    ), f"human output must advise user to inspect or remove manually:\n{stdout}"

    # 2. JSON output: --project A --json
    data = _run_json("--store", store, "--project", repo_a, expect=0)
    assert j1 in _ids(data, "kept"), f"J1 must be kept fail-closed: {data}"
    assert j1 not in _ids(data, "candidates"), f"J1 must NOT be candidate: {data}"

    data_dump = json.dumps(data)
    assert j2 in data_dump, f"--json output must name J2 UUID ({j2}):\n{data_dump}"
    assert (
        j2_path_str in data_dump
    ), f"--json output must name full path to J2 ({j2_path_str}):\n{data_dump}"
    assert (
        "untouched" in data_dump.lower()
    ), f"--json output must state J2 was left untouched:\n{data_dump}"
    assert (
        "inspect" in data_dump.lower() or "manually" in data_dump.lower()
    ), f"--json output must advise user to inspect or remove manually:\n{data_dump}"

    # Verify J1 and J2 remain on disk
    assert (store / "jobs" / j1).is_dir(), f"J1 ({j1}) must remain on disk"
    assert (store / "jobs" / j2).is_dir(), f"J2 ({j2}) must remain on disk"


def test_overblocking_explainable_list_mode(store: Path) -> None:
    """In --list mode with corrupt job, human output prints warning, JSON includes warnings list."""
    repo_a = Path(os.environ["HOME"]) / "proj_a_list_explainable"
    repo_b = Path(os.environ["HOME"]) / "proj_b_list_corrupt"
    repo_a.mkdir(parents=True, exist_ok=True)
    repo_b.mkdir(parents=True, exist_ok=True)

    j1 = _write_job(store, repo=repo_a, state="done", updated_at=_old_time())
    j2 = "00000000-0000-4000-8000-000000000002"
    j2_dir = store / "jobs" / j2
    j2_dir.mkdir(parents=True)
    (j2_dir / "job.json").write_text("{corrupt json", encoding="utf-8")

    # Record mtimes before running --list
    store_jobs_dir = store / "jobs"
    mtime_jobs = store_jobs_dir.stat().st_mtime_ns
    mtime_j1 = (store_jobs_dir / j1).stat().st_mtime_ns
    mtime_j2 = j2_dir.stat().st_mtime_ns

    # 1. Human output: --list
    proc = _run("--store", store, "--list", expect=0)
    stdout = proc.stdout
    assert "warning" in stdout.lower(), (
        f"--list human output must contain a warning line:\n{stdout}"
    )
    assert j2 in stdout, f"--list human output warning line must name J2 UUID ({j2}):\n{stdout}"

    # 2. JSON output: --list --json
    data = _run_json("--store", store, "--list", expect=0)
    assert (
        "warnings" in data
    ), f"--list --json must include a top-level 'warnings' list:\n{data}"
    assert isinstance(data["warnings"], list), f"'warnings' must be a list:\n{data}"
    assert any(
        j2 in str(w) for w in data["warnings"]
    ), f"--list --json 'warnings' must include J2 UUID ({j2}):\n{data['warnings']}"

    # eligible_count stays 0 for all projects
    for proj in data.get("projects", []):
        assert proj["eligible_count"] == 0, (
            f"eligible_count must stay 0 for all projects when store has corrupt job: {proj}"
        )

    # --list writes nothing: no manifest, directory mtimes unchanged
    manifests = list(store_jobs_dir.glob(".cleanup-manifest-*.json"))
    assert not manifests, f"--list must not write any manifest: {manifests}"
    assert store_jobs_dir.stat().st_mtime_ns == mtime_jobs, "store/jobs mtime must not change"
    assert (
        store_jobs_dir / j1
    ).stat().st_mtime_ns == mtime_j1, "j1 directory mtime must not change"
    assert j2_dir.stat().st_mtime_ns == mtime_j2, "j2 directory mtime must not change"


def test_corrupt_record_job_mode_not_deleted(store: Path) -> None:
    """In --job mode targeting corrupt record J2 with --yes, exits 2 and J2 remains on disk."""
    j2 = "00000000-0000-4000-8000-000000000002"
    j2_dir = store / "jobs" / j2
    j2_dir.mkdir(parents=True)
    (j2_dir / "job.json").write_text("{corrupt json", encoding="utf-8")

    proc = _run("--store", store, "--job", j2, "--yes", expect=2)
    assert "traceback" not in proc.stderr.lower()
    assert j2_dir.is_dir(), f"corrupt job {j2} must remain on disk"
    assert (j2_dir / "job.json").read_text(encoding="utf-8") == "{corrupt json"
