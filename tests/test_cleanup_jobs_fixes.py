"""Acceptance tests for 9 cleanup_jobs review findings.

FROZEN tests committed before implementation.
Do not edit src/ or fix anything in this chat.

Test coverage for the 9 review findings:
1. test_project_recheck_before_delete
   Project re-check before delete: in --project mode, run_cleanup with before_delete
   callback rewriting candidate's repo_path skips that row and deletes others.
2. test_edge_recheck_before_delete
   Edge re-check before delete: candidate mutated to running with title referencing
   second candidate keeps both candidates.
3. test_relative_gitdir_detected
   Relative gitdir: worktree registered with relative gitdir in .git/worktrees/<entry>/gitdir
   is detected and kept.
4. test_linked_worktree_repo_common_gitdir
   Linked-worktree repo: project repo whose .git is a file with commondir detects worktrees
   registered in common git dir even when work/.git is deleted.
   test_worktree_registration_unreadable_fails_closed
   Fail closed when repo's worktrees directory is unreadable.
5. test_duplicate_job_ids_deduplicated
   Duplicate --job IDs deduplicated, exits 0, manifest completed, failed_ids empty.
6. test_mixed_job_run_partial_manifest_and_reasons
   Mixed --job run with eligible and ineligible (running) ID exits 1, manifest partial,
   removed_ids has eligible, failed_ids has ineligible, reasons reported.
7. test_list_excludes_edge_protected_candidate
   --list consistency: edge-protected candidate excluded from eligible_count.
   test_list_consistency_with_project_mode
   General test: --list eligible_count per project equals --project candidate count.
8. test_fail_closed_on_corrupt_kept_record_project_mode
   test_fail_closed_on_non_object_kept_record
   test_fail_closed_on_unreadable_kept_record_job_mode
   Fail closed on unreadable/corrupt/non-object kept records in --project and --job modes.
9. test_invalid_repo_path_not_matched_and_listed_unknown
   Invalid repo_path (123, list, null, missing) never matches --project, kept with reason,
   --list reports under "(unknown)" with eligible_count 0, deletable in --job mode.

Not covered on purpose (document only):
- Symlinked job folder in --job mode exits 2 (SPEC §23/D89 already enforces symlink rejection).
- Unicode normalization (filesystem-dependent path normalization for non-ASCII repo paths).
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
    root = Path(os.environ["HOME"]) / "cleanup-jobs-fixes-tests" / uuid.uuid4().hex
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
        "title": "Cleanup Fixes Test Job",
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
# Finding 1: Project re-check before delete
# --------------------------------------------------------------------------- #


def test_project_recheck_before_delete(store: Path) -> None:
    """In --project mode, mutating a candidate's repo_path skips that row while deleting others."""
    repo_a = Path(os.environ["HOME"]) / "proj_recheck_a"
    repo_b = Path(os.environ["HOME"]) / "proj_recheck_b"
    repo_a.mkdir(parents=True, exist_ok=True)
    repo_b.mkdir(parents=True, exist_ok=True)

    cand1 = _write_job(store, repo=repo_a, state="done", updated_at=_old_time())
    cand2 = _write_job(store, repo=repo_a, state="done", updated_at=_old_time())

    def cb(cid: str) -> None:
        if cid == cand1:
            jfile = store / "jobs" / cand1 / "job.json"
            data = json.loads(jfile.read_text(encoding="utf-8"))
            data["repo_path"] = str(repo_b)
            jfile.write_text(json.dumps(data, indent=2), encoding="utf-8")

    res = run_cleanup(store, project=repo_a, dry_run=False, before_delete=cb)

    assert (
        store / "jobs" / cand1
    ).is_dir(), "cand1 repo changed to different project; must be skipped"
    assert not (store / "jobs" / cand2).exists(), "cand2 unchanged; must be deleted"
    assert cand1 not in res.get("deleted_ids", [])
    assert cand2 in res.get("deleted_ids", [])

    skipped_entries = res.get("skipped", [])
    assert any(
        s.get("job_id") == cand1
        and any("repo" in r.lower() or "project" in r.lower() for r in s.get("reasons", []))
        for s in skipped_entries
    ), f"cand1 must be reported in skipped with project reason: {skipped_entries}"

    assert res.get("manifest") is not None
    manifest_data = json.loads(Path(res["manifest"]).read_text(encoding="utf-8"))
    assert cand1 not in manifest_data.get("removed_ids", [])
    assert cand2 in manifest_data.get("removed_ids", [])


# --------------------------------------------------------------------------- #
# Finding 2: Edge re-check before delete
# --------------------------------------------------------------------------- #


def test_edge_recheck_before_delete(store: Path) -> None:
    """When candidate A mutates to running and references candidate B, B must also be kept."""
    repo = Path(os.environ["HOME"]) / "edge_recheck_proj"
    repo.mkdir(parents=True, exist_ok=True)

    id_a = "00000000-0000-4000-8000-000000000001"
    id_b = "00000000-0000-4000-8000-000000000002"
    _write_job(store, job_id=id_a, repo=repo, state="done", updated_at=_old_time())
    _write_job(store, job_id=id_b, repo=repo, state="done", updated_at=_old_time())

    def cb(cid: str) -> None:
        if cid == id_a:
            jfile = store / "jobs" / id_a / "job.json"
            data = json.loads(jfile.read_text(encoding="utf-8"))
            data["state"] = "running"
            data["title"] = f"Supervising job {id_b}"
            jfile.write_text(json.dumps(data, indent=2), encoding="utf-8")

    res = run_cleanup(store, project=repo, dry_run=False, before_delete=cb)

    assert (store / "jobs" / id_a).is_dir(), "id_a mutated to running; must stay on disk"
    assert (
        store / "jobs" / id_b
    ).is_dir(), "id_b referenced by kept record id_a; must ALSO be kept"
    assert id_a not in res.get("deleted_ids", [])
    assert id_b not in res.get("deleted_ids", [])

    all_reasons: list[str] = []
    for entry in res.get("skipped", []) + res.get("kept", []):
        if entry.get("job_id") == id_b:
            all_reasons.extend(entry.get("reasons", []))
    assert any(
        "reference" in r.lower() or "kept" in r.lower() or "edge" in r.lower() for r in all_reasons
    ), f"id_b must state edge protection skip reason: {res}"


# --------------------------------------------------------------------------- #
# Finding 3: Relative gitdir
# --------------------------------------------------------------------------- #


def test_relative_gitdir_detected(store: Path, tmp_path: Path) -> None:
    """Registered worktree with relative gitdir in .git/worktrees/<entry>/gitdir is detected."""
    repo_path = make_repo(tmp_path / "relative_gitdir_repo")
    jid = _write_job(store, repo=repo_path, state="done", updated_at=_old_time())
    worktree_path = store / "jobs" / jid / "work"

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

    wt_entries = list((repo_path / ".git" / "worktrees").iterdir())
    assert len(wt_entries) == 1
    entry = wt_entries[0]
    gitdir_file = entry / "gitdir"
    assert gitdir_file.is_file()

    # Rewrite gitdir to relative path (as written by git for relocated worktrees)
    rel_gitdir = os.path.relpath(worktree_path / ".git", entry)
    gitdir_file.write_text(rel_gitdir + "\n", encoding="utf-8")

    # Remove work/.git so detection must resolve entry's relative gitdir file
    (worktree_path / ".git").unlink()

    dry = _run_json("--store", store, "--project", repo_path, expect=0)
    assert jid in _ids(dry, "kept"), f"jid {jid} should be kept with reason: {dry}"
    assert jid not in _ids(dry, "candidates")
    entry_report = next(e for e in dry["kept"] if e["job_id"] == jid)
    assert any("worktree" in r.lower() for r in entry_report.get("reasons", []))

    _run("--store", store, "--project", repo_path, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), "row with relative gitdir must NOT be deleted"


# --------------------------------------------------------------------------- #
# Finding 4: Linked-worktree or submodule repo
# --------------------------------------------------------------------------- #


def test_linked_worktree_repo_common_gitdir(store: Path, tmp_path: Path) -> None:
    """Project repo that is a linked worktree (.git file) detects worktrees in common gitdir."""
    main_repo = make_repo(tmp_path / "main_repo")
    linked_repo = tmp_path / "linked_repo"
    subprocess.run(
        [
            "git",
            "-C",
            str(main_repo),
            "worktree",
            "add",
            str(linked_repo),
            "-b",
            "linked-proj",
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
    # Remove work/.git so detection must check the common git dir
    (worktree_path / ".git").unlink()

    dry = _run_json("--store", store, "--project", linked_repo, expect=0)
    assert jid in _ids(dry, "kept"), f"job {jid} must be kept as worktree registered: {dry}"
    assert jid not in _ids(dry, "candidates")
    entry_report = next(e for e in dry["kept"] if e["job_id"] == jid)
    assert any("worktree" in r.lower() for r in entry_report.get("reasons", []))

    _run("--store", store, "--project", linked_repo, "--yes", expect=0)
    assert (store / "jobs" / jid).is_dir(), "job in linked worktree repo must NOT be deleted"


def test_worktree_registration_unreadable_fails_closed(store: Path, tmp_path: Path) -> None:
    """If project repo exists but worktree registrations cannot be read, row is kept fail-closed."""
    if os.geteuid() == 0:
        pytest.skip("cannot test chmod-based denial as root")

    repo_path = make_repo(tmp_path / "unreadable_wt_repo")
    jid = _write_job(store, repo=repo_path, state="done", updated_at=_old_time())
    wt_dir = repo_path / ".git" / "worktrees"
    wt_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(wt_dir, 0o000)

    try:
        dry = _run_json("--store", store, "--project", repo_path, expect=0)
        assert jid in _ids(dry, "kept"), f"job {jid} must be kept when worktrees unreadable: {dry}"
        assert jid not in _ids(dry, "candidates")
        entry_report = next(e for e in dry["kept"] if e["job_id"] == jid)
        assert any(
            "worktree" in r.lower() or "unreadable" in r.lower()
            for r in entry_report.get("reasons", [])
        )

        _run("--store", store, "--project", repo_path, "--yes", expect=0)
        assert (
            store / "jobs" / jid
        ).is_dir(), "candidate must NOT be deleted when worktrees dir unreadable"
    finally:
        os.chmod(wt_dir, 0o755)


# --------------------------------------------------------------------------- #
# Finding 5: Duplicate --job ids
# --------------------------------------------------------------------------- #


def test_duplicate_job_ids_deduplicated(store: Path) -> None:
    """Duplicate --job ID is deduplicated; exits 0, manifest completed, failed_ids empty."""
    jid = _write_job(store, state="done", updated_at=_old_time())

    _run("--store", store, "--job", jid, "--job", jid, "--yes", expect=0)
    assert not (store / "jobs" / jid).exists(), "job must be deleted"

    manifest_files = list((store / "jobs").glob(".cleanup-manifest-*.json"))
    assert len(manifest_files) == 1, f"expected 1 manifest file, got {manifest_files}"
    manifest_data = json.loads(manifest_files[0].read_text(encoding="utf-8"))

    assert (
        manifest_data.get("status") == "completed"
    ), f"manifest status must be completed: {manifest_data}"
    assert manifest_data.get("failed_ids") == [], f"failed_ids must be empty: {manifest_data}"
    assert manifest_data.get("removed_ids") == [
        jid
    ], f"removed_ids must list jid once: {manifest_data}"


# --------------------------------------------------------------------------- #
# Finding 6: Mixed --job run
# --------------------------------------------------------------------------- #


def test_mixed_job_run_partial_manifest_and_reasons(store: Path) -> None:
    """Mixed --job run (1 eligible, 1 running) exits 1, manifest partial, reasons reported."""
    eligible_id = _write_job(store, state="done", updated_at=_old_time())
    ineligible_id = _write_job(store, state="running", updated_at=_old_time())

    res = _run_json(
        "--store",
        store,
        "--job",
        eligible_id,
        "--job",
        ineligible_id,
        "--yes",
        expect=1,
    )

    assert not (store / "jobs" / eligible_id).exists(), "eligible job must be deleted"
    assert (store / "jobs" / ineligible_id).is_dir(), "ineligible job must be kept"

    assert eligible_id in res.get("deleted_ids", [])
    assert ineligible_id in res.get(
        "failed_ids", []
    ), f"failed_ids must contain ineligible_id: {res}"

    reasons_found: list[str] = []
    for entry in res.get("skipped", []) + res.get("kept", []):
        if entry.get("job_id") == ineligible_id:
            reasons_found.extend(entry.get("reasons", []))
    assert any("running" in r.lower() or "terminal" in r.lower() for r in reasons_found), (
        f"reason for ineligible id not named in json: {res}"
    )

    assert res.get("manifest") is not None
    manifest_data = json.loads(Path(res["manifest"]).read_text(encoding="utf-8"))
    assert manifest_data.get("status") == "partial"
    assert eligible_id in manifest_data.get("removed_ids", [])
    assert ineligible_id in manifest_data.get("failed_ids", [])

    # Human output run with fresh IDs
    el2 = _write_job(store, state="done", updated_at=_old_time())
    inel2 = _write_job(store, state="running", updated_at=_old_time())
    proc = _run("--store", store, "--job", el2, "--job", inel2, "--yes", expect=1)
    assert inel2 in proc.stdout
    assert "running" in proc.stdout.lower() or "terminal" in proc.stdout.lower()


# --------------------------------------------------------------------------- #
# Finding 7: --list consistency
# --------------------------------------------------------------------------- #


def test_list_excludes_edge_protected_candidate(store: Path) -> None:
    """A candidate protected by a follow-up edge from a kept row must NOT count in --list."""
    repo = Path(os.environ["HOME"]) / "list_edge_proj"
    repo.mkdir(parents=True, exist_ok=True)

    cand_protected = _write_job(store, repo=repo, state="done", updated_at=_old_time())
    _write_job(
        store,
        repo=repo,
        state="running",
        updated_at=_recent_time(),
        extra={"title": f"Follow-up edge to {cand_protected}"},
    )

    list_res = _run_json("--store", store, "--list", expect=0)
    norm_repo = str(repo.resolve())
    proj_entry = next((p for p in list_res["projects"] if p["repo_path"] == norm_repo), None)
    assert proj_entry is not None, f"project {norm_repo} not found in --list: {list_res}"
    assert proj_entry["eligible_count"] == 0, (
        f"edge-protected candidate must NOT count in eligible_count: {proj_entry}"
    )


def test_list_consistency_with_project_mode(store: Path, tmp_path: Path) -> None:
    """For mixed stores, --list eligible_count equals candidates count reported by --project."""
    repo1 = make_repo(tmp_path / "mixed_repo_1")
    repo2 = make_repo(tmp_path / "mixed_repo_2")

    for repo in (repo1, repo2):
        # 1. Eligible job (done, 10 days old)
        _write_job(store, repo=repo, state="done", updated_at=_old_time(10))

        # 2. Too recent job (done, 2 days old)
        _write_job(store, repo=repo, state="done", updated_at=_recent_time(2))

        # 3. Non-terminal job (running, 10 days old)
        running_id = _write_job(store, repo=repo, state="running", updated_at=_old_time(10))

        # 4. Edge-protected job (done, 10 days old, referenced in running job)
        edge_prot_id = _write_job(store, repo=repo, state="done", updated_at=_old_time(10))
        r_file = store / "jobs" / running_id / "job.json"
        r_data = json.loads(r_file.read_text(encoding="utf-8"))
        r_data["title"] = f"Depends on {edge_prot_id}"
        r_file.write_text(json.dumps(r_data, indent=2), encoding="utf-8")

        # 5. Worktree-registered job (done, 10 days old, work/.git present)
        wt_job_id = _write_job(store, repo=repo, state="done", updated_at=_old_time(10))
        wt_git = store / "jobs" / wt_job_id / "work" / ".git"
        wt_git.parent.mkdir(parents=True, exist_ok=True)
        wt_git.write_text("gitdir: /fake/path\n", encoding="utf-8")

    list_res = _run_json("--store", store, "--list", expect=0)
    for p in list_res["projects"]:
        proj_path = p["repo_path"]
        proj_res = _run_json("--store", store, "--project", proj_path, expect=0)
        assert p["eligible_count"] == len(proj_res["candidates"]), (
            f"Mismatch for project {proj_path}: --list eligible_count={p['eligible_count']}, "
            f"but --project reports {len(proj_res['candidates'])} candidates"
        )


# --------------------------------------------------------------------------- #
# Finding 8: Fail closed on unreadable kept records
# --------------------------------------------------------------------------- #


def test_fail_closed_on_corrupt_kept_record_project_mode(store: Path) -> None:
    """In --project mode, if any kept row is corrupt, all candidates kept, nothing deleted."""
    repo = Path(os.environ["HOME"]) / "fail_closed_proj"
    repo.mkdir(parents=True, exist_ok=True)

    cand = _write_job(store, repo=repo, state="done", updated_at=_old_time())

    corrupt_id = str(uuid.uuid4())
    corrupt_dir = store / "jobs" / corrupt_id
    corrupt_dir.mkdir(parents=True)
    (corrupt_dir / "job.json").write_text("{broken: json[", encoding="utf-8")

    dry = _run_json("--store", store, "--project", repo, expect=0)
    assert (
        cand not in _ids(dry, "candidates")
    ), "candidate must NOT be deletable when kept record corrupt"
    assert cand in _ids(dry, "kept"), "candidate must be kept when kept record corrupt"
    entry = next(e for e in dry["kept"] if e["job_id"] == cand)
    assert any(
        "corrupt" in r.lower() or "unreadable" in r.lower() for r in entry.get("reasons", [])
    )

    proc = _run("--store", store, "--project", repo, "--yes", expect=0)
    assert "traceback" not in proc.stderr.lower()
    assert (store / "jobs" / cand).is_dir(), "candidate must NOT be deleted with --yes"


def test_fail_closed_on_non_object_kept_record(store: Path) -> None:
    """If any kept row's job.json root is a non-object, fail closed and keep all candidates."""
    repo = Path(os.environ["HOME"]) / "non_obj_proj"
    repo.mkdir(parents=True, exist_ok=True)

    cand = _write_job(store, repo=repo, state="done", updated_at=_old_time())

    non_obj_id = str(uuid.uuid4())
    non_obj_dir = store / "jobs" / non_obj_id
    non_obj_dir.mkdir(parents=True)
    (non_obj_dir / "job.json").write_text('["not", "a", "dict"]\n', encoding="utf-8")

    dry = _run_json("--store", store, "--project", repo, expect=0)
    assert cand not in _ids(dry, "candidates")
    assert cand in _ids(dry, "kept")

    _run("--store", store, "--project", repo, "--yes", expect=0)
    assert (
        store / "jobs" / cand
    ).is_dir(), "candidate must NOT be deleted when kept record non-object"


def test_fail_closed_on_unreadable_kept_record_job_mode(store: Path) -> None:
    """In --job mode, if any kept row's job.json is chmod 0o000, all candidates kept, exit 0."""
    if os.geteuid() == 0:
        pytest.skip("cannot test chmod-based denial as root")

    cand = _write_job(store, state="done", updated_at=_old_time())

    unreadable_id = str(uuid.uuid4())
    unreadable_dir = store / "jobs" / unreadable_id
    unreadable_dir.mkdir(parents=True)
    unreadable_file = unreadable_dir / "job.json"
    unreadable_file.write_text("{}", encoding="utf-8")
    os.chmod(unreadable_file, 0o000)

    try:
        dry = _run_json("--store", store, "--job", cand, expect=0)
        assert (
            cand not in _ids(dry, "candidates")
        ), "candidate must NOT be deletable when kept record unreadable"
        assert cand in _ids(dry, "kept")
        entry = next(e for e in dry["kept"] if e["job_id"] == cand)
        assert any(
            "unreadable" in r.lower() or "corrupt" in r.lower() for r in entry.get("reasons", [])
        )

        proc = _run("--store", store, "--job", cand, "--yes", expect=2)
        assert "traceback" not in proc.stderr.lower()
        assert (store / "jobs" / cand).is_dir(), "candidate must NOT be deleted with --yes"
    finally:
        os.chmod(unreadable_file, 0o600)


# --------------------------------------------------------------------------- #
# Finding 9: Invalid repo_path
# --------------------------------------------------------------------------- #


def test_invalid_repo_path_not_matched_and_listed_unknown(store: Path) -> None:
    """Invalid repo_path (123, list, null, missing) never matches --project, listed as unknown."""
    job_int = _write_job(store, state="done", updated_at=_old_time(), extra={"repo_path": 123})
    job_list = _write_job(
        store, state="done", updated_at=_old_time(), extra={"repo_path": ["some", "path"]}
    )
    job_null = _write_job(store, state="done", updated_at=_old_time(), extra={"repo_path": None})
    job_missing = _write_job(store, state="done", updated_at=_old_time())
    m_file = store / "jobs" / job_missing / "job.json"
    m_data = json.loads(m_file.read_text(encoding="utf-8"))
    del m_data["repo_path"]
    m_file.write_text(json.dumps(m_data, indent=2), encoding="utf-8")

    # 1. Matching --project "123" must NEVER match job_int
    res_123 = _run_json("--store", store, "--project", "123", expect=0)
    assert job_int not in _ids(res_123, "candidates")

    # 2. --project with real project never matches any invalid repo_path rows
    some_proj = Path(os.environ["HOME"]) / "some_proj"
    some_proj.mkdir(parents=True, exist_ok=True)
    res_proj = _run_json("--store", store, "--project", some_proj, expect=0)
    assert job_int not in _ids(res_proj, "candidates")
    assert job_list not in _ids(res_proj, "candidates")
    assert job_null not in _ids(res_proj, "candidates")
    assert job_missing not in _ids(res_proj, "candidates")

    # 3. --list reports under "(unknown)" project with eligible_count 0
    list_res = _run_json("--store", store, "--list", expect=0)
    unknown_entry = next((p for p in list_res["projects"] if p["repo_path"] == "(unknown)"), None)
    assert unknown_entry is not None, f"'(unknown)' project missing from --list: {list_res}"
    assert unknown_entry["eligible_count"] == 0, f"eligible_count must be 0: {unknown_entry}"
    assert (
        unknown_entry["total_jobs"] >= 4
    ), f"total_jobs should include all invalid rows: {unknown_entry}"

    # 4. In explicit --job mode, repo_path is ignored and otherwise eligible rows are deletable
    _run("--store", store, "--job", job_int, "--job", job_null, "--yes", expect=0)
    assert not (
        store / "jobs" / job_int
    ).exists(), "job with integer repo_path must be deletable in --job mode"
    assert not (
        store / "jobs" / job_null
    ).exists(), "job with null repo_path must be deletable in --job mode"
