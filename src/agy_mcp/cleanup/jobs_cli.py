"""Job-record cleaner CLI: classify -> protect -> report -> delete (SPEC §23).

The user-facing ``python -m agy_mcp.cleanup_jobs`` implementation. The old
module path is kept as a re-export shim; the public surface is unchanged.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from agy_mcp.cleanup.store_io import (
    JobJsonProblem,
    _apply_edge_protection,
    _check_pgid_status,
    _check_pid_status,
    _extract_strings,
    _has_symlinks,
    _is_canonical_uuid,
    _parse_iso_ts,
    _safe_delete_job_dir,
    _write_manifest,
    load_job_json,
    normalize_repo_path,
)
from agy_mcp.cleanup.worktree_status import _check_worktree_status


def _classify_job_for_cleanup(
    child: Path,
    store_path: Path,
    *,
    check_age: bool,
    older_than_days: float,
    now: datetime,
) -> tuple[bool, list[str], Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """Classify a single job folder for cleanup eligibility.

    Returns (is_candidate, reasons, candidate_info_dict, parsed_job_data).
    """
    c_name = child.name
    try:
        if child.is_symlink():
            return False, ["job directory is a symlink"], None, None

        if not child.is_dir():
            return False, ["not a directory"], None, None

        if not _is_canonical_uuid(c_name):
            return False, ["folder name is not a canonical UUID"], None, None

        if _has_symlinks(child):
            return False, ["contains symlinks inside job directory"], None, None

        job_file = child / "job.json"
        if job_file.is_symlink():
            return False, ["job.json is a symlink"], None, None

        if not job_file.is_file():
            return False, ["job.json is missing or not a regular file"], None, None

        data, failure = load_job_json(job_file)
        if failure is not None:
            if failure.problem is JobJsonProblem.TOO_LARGE:
                return False, ["job.json exceeds 1 MiB limit"], None, None
            return (
                False,
                [f"job directory or job.json unreadable: {failure.detail}"],
                None,
                None,
            )
        if not isinstance(data, dict):
            return False, ["job.json root is not an object"], None, None

        if data.get("job_id") != c_name:
            return (
                False,
                [f"job.json job_id ({data.get('job_id')!r}) does not match directory name"],
                None,
                None,
            )

        state = data.get("state")
        if state not in ("done", "failed", "cancelled"):
            return False, [f"state {state!r} is not terminal (done, failed, cancelled)"], None, None

        is_wt, wt_reason = _check_worktree_status(child, data)
        if is_wt:
            return (
                False,
                [
                    wt_reason
                    or "worktree registered (run 'git worktree prune' in repository to unregister)"
                ],
                None,
                None,
            )

        pid = data.get("pid")
        if pid is not None:
            should_keep, pid_reason = _check_pid_status(pid)
            if should_keep:
                return False, [pid_reason or f"ambiguous pid: {pid!r}"], None, None

        pgid = data.get("pgid")
        if pgid is not None:
            should_keep, pgid_reason = _check_pgid_status(pgid)
            if should_keep:
                return False, [pgid_reason or f"ambiguous pgid: {pgid!r}"], None, None

        age_days = 0.0
        ts = None
        updated_raw = data.get("updated_at")
        if updated_raw is not None:
            ts = _parse_iso_ts(updated_raw)
            if ts is None:
                return False, ["updated_at is not parseable timezone-aware ISO 8601"], None, None
        else:
            ts = _parse_iso_ts(data.get("created_at"))
            if ts is None:
                return False, ["missing or unparseable timezone-aware created_at"], None, None

        if ts is not None:
            age_seconds = (now - ts.astimezone(timezone.utc)).total_seconds()
            age_days = age_seconds / 86400.0
            if check_age:
                min_age_seconds = older_than_days * 86400.0
                if age_seconds < min_age_seconds:
                    return (
                        False,
                        [
                            f"job age ({age_days:.2f} days) is less than "
                            f"older-than-days ({older_than_days} days)"
                        ],
                        None,
                        None,
                    )

        cand_info = {
            "job_id": c_name,
            "title": str(data.get("title", "")),
            "state": str(state),
            "repo_path": str(data.get("repo_path") or ""),
            "age_days": round(age_days, 2),
            "reasons": [
                f"terminal state ({state}), no live process, no registered worktree"
            ],
        }
        return True, cand_info["reasons"], cand_info, data
    except OSError as exc:
        return False, [f"job directory or job.json unreadable: {exc}"], None, None
    except json.JSONDecodeError:
        return False, ["job.json is not valid JSON"], None, None
    except ValueError as exc:
        return False, [f"job.json is not valid JSON: {exc}"], None, None
    except Exception as exc:
        return False, [f"unhandled error classifying job: {exc}"], None, None


def _collect_kept_strings(
    store_jobs: Path,
    candidate_ids: set[str],
) -> tuple[Optional[list[str]], Optional[str]]:
    """Collect all strings from kept job records in store_jobs for edge protection.

    Returns (strings_list, None) on success, or (None, failure_reason) if any
    kept job record is corrupt, non-object, unreadable, or cannot be accessed.
    """
    strings: list[str] = []
    try:
        children = sorted(store_jobs.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        return None, f"store/jobs directory unreadable: {exc}"

    for child in children:
        c_name = child.name
        if c_name.startswith(".cleanup-manifest-"):
            continue
        if c_name in candidate_ids:
            continue
        try:
            if child.is_symlink() or not child.is_dir():
                continue
            if not _is_canonical_uuid(c_name):
                continue
            child_path = str(child.resolve())
            job_file = child / "job.json"
            if job_file.is_symlink() or not job_file.is_file():
                return (
                    None,
                    f"store contains unreadable or corrupt kept job record ({c_name}) at "
                    f"{child_path}; left untouched; inspect or remove manually; "
                    "keeping all candidates fail-closed",
                )
            data, failure = load_job_json(job_file)
            if failure is not None:
                if failure.problem is JobJsonProblem.TOO_LARGE:
                    return (
                        None,
                        f"store contains oversized kept job record ({c_name}) at "
                        f"{child_path}; left untouched; inspect or remove manually; "
                        "keeping all candidates fail-closed",
                    )
                return (
                    None,
                    f"store contains unreadable or corrupt kept job record ({c_name}) at "
                    f"{child_path}: {failure.detail}; left untouched; inspect or remove "
                    "manually; keeping all candidates fail-closed",
                )
            if not isinstance(data, dict):
                return (
                    None,
                    f"store contains non-object kept job record ({c_name}) at "
                    f"{child_path}; left untouched; inspect or remove manually; "
                    "keeping all candidates fail-closed",
                )
            strings.extend(_extract_strings(data))
        except (OSError, json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
            child_path = str(child.resolve())
            return (
                None,
                f"store contains unreadable or corrupt kept job record ({c_name}) at "
                f"{child_path}: {exc}; left untouched; inspect or remove manually; "
                "keeping all candidates fail-closed",
            )
        except Exception as exc:
            child_path = str(child.resolve())
            return (
                None,
                f"store contains unreadable or corrupt kept job record ({c_name}) at "
                f"{child_path}: {exc}; left untouched; inspect or remove manually; "
                "keeping all candidates fail-closed",
            )
    return strings, None


def run_cleanup(
    store_path: Path | str,
    *,
    project: Optional[Path | str] = None,
    job_ids: Optional[list[str]] = None,
    dry_run: bool = True,
    older_than_days: float = 7.0,
    before_delete: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Execute job record cleanup or dry-run scan against store_path."""
    if project is not None and job_ids is not None:
        raise ValueError("project and job_ids are mutually exclusive")
    if project is None and job_ids is None:
        raise ValueError("Exactly one of project or job_ids must be specified")
    if not math.isfinite(older_than_days) or older_than_days < 0:
        raise ValueError("older_than_days must be finite and non-negative")

    if job_ids is not None:
        job_ids = list(dict.fromkeys(job_ids))

    store_path_obj = Path(store_path)
    store_jobs = store_path_obj / "jobs"
    if not store_jobs.is_dir():
        raise ValueError(f"store/jobs is missing or not a directory: {store_jobs}")

    now = datetime.now(timezone.utc)
    candidates: dict[str, dict[str, Any]] = {}
    cand_raw_data: dict[str, dict[str, Any]] = {}
    kept: dict[str, list[str]] = {}

    if project is not None:
        mode = "project"
        target_project: Optional[str] = normalize_repo_path(project)
        check_age = True

        children = sorted(store_jobs.iterdir(), key=lambda p: p.name)
        for child in children:
            if child.name.startswith(".cleanup-manifest-"):
                continue

            # Skip symlink row directories or unreadable/non-dir entries for project matching
            if child.is_symlink() or not child.is_dir() or not _is_canonical_uuid(child.name):
                continue

            job_file = child / "job.json"
            if job_file.is_symlink() or not job_file.is_file():
                continue

            try:
                data, failure = load_job_json(job_file)
                if failure is not None:
                    if failure.problem in (
                        JobJsonProblem.NOT_REGULAR,
                        JobJsonProblem.TOO_LARGE,
                    ):
                        kept[child.name] = ["job.json invalid or exceeds 1 MiB limit"]
                    else:
                        kept[child.name] = [
                            f"job.json unreadable or corrupt: {failure.detail}"
                        ]
                    continue
                if not isinstance(data, dict):
                    kept[child.name] = ["job.json root is not an object"]
                    continue
            except Exception as exc:
                kept[child.name] = [f"job.json unreadable or corrupt: {exc}"]
                continue

            repo_path_val = data.get("repo_path")
            norm_repo = None
            if (
                isinstance(repo_path_val, str)
                and repo_path_val.strip()
                and "\x00" not in repo_path_val
            ):
                try:
                    norm_repo = normalize_repo_path(repo_path_val)
                except (ValueError, OSError):
                    norm_repo = None

            if norm_repo is None:
                kept[child.name] = ["repo_path invalid"]
                continue

            if norm_repo != target_project:
                continue

            is_cand, reasons, cand_info, raw_data = _classify_job_for_cleanup(
                child,
                store_path_obj,
                check_age=check_age,
                older_than_days=older_than_days,
                now=now,
            )
            if is_cand and cand_info is not None:
                candidates[child.name] = cand_info
                if raw_data is not None:
                    cand_raw_data[child.name] = raw_data
            else:
                kept[child.name] = reasons
    else:
        mode = "job"
        target_project = None
        check_age = False

        # The mode-count check in main() guarantees exactly one mode and the
        # project branch returned above, so job_ids cannot be None here. The
        # assert only narrows the Optional for the type checker; under -O it
        # vanishes and the loop below raises TypeError instead of ever deleting
        # rows the caller did not name (D8).
        assert job_ids is not None
        for jid in job_ids:
            if not _is_canonical_uuid(jid):
                raise ValueError(f"malformed UUID: {jid}")
            child = store_jobs / jid
            if not child.exists() or not child.is_dir() or child.is_symlink():
                raise ValueError(f"job id not found in store: {jid}")

            is_cand, reasons, cand_info, raw_data = _classify_job_for_cleanup(
                child,
                store_path_obj,
                check_age=check_age,
                older_than_days=older_than_days,
                now=now,
            )
            if is_cand and cand_info is not None:
                candidates[jid] = cand_info
                if raw_data is not None:
                    cand_raw_data[jid] = raw_data
            else:
                kept[jid] = reasons

    # Edge protection from kept jobs:
    # Any candidate referenced as key or substring in any kept job's job.json is protected.
    # If any kept job record is corrupt or unreadable, fail closed and keep all candidates.
    kept_strings, fail_reason = _collect_kept_strings(
        store_jobs, candidate_ids=set(candidates.keys())
    )
    if fail_reason is not None:
        for cid in sorted(candidates.keys()):
            kept[cid] = [fail_reason]
        candidates.clear()
        kept_text = ""
    else:
        # No failure reason means the collector returned a list; this narrows the
        # Optional. Under -O the assert vanishes and the join below raises
        # TypeError rather than silently protecting the wrong (empty) set (D8).
        assert kept_strings is not None
        kept_text = "\0".join(kept_strings)

    def _candidate_strings(cid: str) -> list[str]:
        # Reuse the record parsed during classification instead of re-reading it:
        # that parse already passed the same cap and duplicate-key checks.
        c_data = cand_raw_data.get(cid)
        if c_data is None:
            return []
        return _extract_strings(c_data)

    kept_text = _apply_edge_protection(
        candidates, kept, kept_text, strings_for=_candidate_strings
    )

    candidates_list = [candidates[cid] for cid in sorted(candidates.keys())]
    kept_list = [{"job_id": kid, "reasons": kept[kid]} for kid in sorted(kept.keys())]

    deleted_ids: list[str] = []
    failed_ids: list[str] = []
    skipped: list[dict[str, Any]] = []
    manifest_path_str: Optional[str] = None

    if not dry_run and candidates:
        planned_ids = sorted(candidates.keys())
        ts_str = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        manifest_target = store_jobs / f".cleanup-manifest-{ts_str}.json"
        planned_manifest: dict[str, Any] = {
            "status": "planned",
            "mode": mode,
            "planned_ids": planned_ids,
        }
        if mode == "project":
            planned_manifest["project"] = str(target_project)
        _write_manifest(store_jobs, manifest_target, planned_manifest)
        manifest_path_str = str(manifest_target.resolve())

        for idx, cid in enumerate(planned_ids):
            if before_delete is not None:
                before_delete(cid)

            c_dir = store_jobs / cid
            if not c_dir.exists() or not c_dir.is_dir() or c_dir.is_symlink():
                failed_ids.append(cid)
                skipped.append({"job_id": cid, "reasons": ["job directory missing or symlink"]})
                continue

            # 1. Re-check classification
            is_cand, reasons, _, _ = _classify_job_for_cleanup(
                c_dir,
                store_path_obj,
                check_age=check_age,
                older_than_days=older_than_days,
                now=now,
            )
            if not is_cand:
                failed_ids.append(cid)
                skipped.append({"job_id": cid, "reasons": reasons})
                continue

            # 2. In project mode, re-check repo_path (Requirement 1)
            if mode == "project":
                c_file = c_dir / "job.json"
                try:
                    c_data, failure = load_job_json(c_file)
                    if failure is not None:
                        if failure.problem in (
                            JobJsonProblem.NOT_REGULAR,
                            JobJsonProblem.TOO_LARGE,
                        ):
                            reason = "job.json invalid or exceeds 1 MiB limit"
                        else:
                            reason = f"job.json unreadable: {failure.detail}"
                        failed_ids.append(cid)
                        skipped.append({"job_id": cid, "reasons": [reason]})
                        continue
                    c_repo = c_data.get("repo_path")
                    if (
                        not isinstance(c_repo, str)
                        or not c_repo.strip()
                        or "\x00" in c_repo
                    ):
                        failed_ids.append(cid)
                        skipped.append(
                            {"job_id": cid, "reasons": ["repo_path invalid or missing"]}
                        )
                        continue
                    try:
                        norm_c_repo = normalize_repo_path(c_repo)
                    except (ValueError, OSError):
                        norm_c_repo = None
                    if norm_c_repo != target_project:
                        failed_ids.append(cid)
                        skipped.append(
                            {
                                "job_id": cid,
                                "reasons": [
                                    f"repo_path {c_repo!r} does not match target project "
                                    f"{target_project!r}"
                                ],
                            }
                        )
                        continue
                except Exception as exc:
                    failed_ids.append(cid)
                    skipped.append(
                        {"job_id": cid, "reasons": [f"job.json unreadable: {exc}"]}
                    )
                    continue

            # 3. Recompute edge protection from current on-disk records of all kept rows.
            # Kept rows are all jobs in store_jobs not in planned_ids[idx:]
            # (which includes original kept jobs, plus any previous candidates from
            # planned_ids[:idx] that failed or were skipped and remain on disk).
            dyn_kept_strings, blocker_reason = _collect_kept_strings(
                store_jobs, candidate_ids=set(planned_ids[idx:])
            )
            if blocker_reason is not None:
                failed_ids.append(cid)
                skipped.append({"job_id": cid, "reasons": [blocker_reason]})
                for rem_id in planned_ids[idx + 1 :]:
                    if rem_id not in failed_ids:
                        failed_ids.append(rem_id)
                    if not any(s.get("job_id") == rem_id for s in skipped):
                        skipped.append({"job_id": rem_id, "reasons": [blocker_reason]})
                break

            # Same narrowing as above: a None here would contradict the
            # blocker_reason contract, so the assert only satisfies the type
            # checker. Under -O it vanishes and the join below raises TypeError
            # instead of deleting a row it failed to protect (D8).
            assert dyn_kept_strings is not None

            if cid in "\0".join(dyn_kept_strings):
                failed_ids.append(cid)
                skipped.append(
                    {"job_id": cid, "reasons": ["referenced by kept job (edge protection)"]}
                )
                continue

            # 4. Safely delete
            try:
                _safe_delete_job_dir(c_dir, cid, store_jobs)
                deleted_ids.append(cid)
            except Exception as exc:
                failed_ids.append(cid)
                skipped.append({"job_id": cid, "reasons": [f"deletion failed: {exc}"]})

    # In --job mode:
    # - In dry-run mode, only ineligible requested IDs (not candidates) appear
    #   in failed_ids and skipped.
    # - In real mode (--yes), any requested ID not deleted appears in failed_ids and skipped.
    if mode == "job" and job_ids:
        seen = candidates if dry_run else set(deleted_ids)
        for jid in job_ids:
            if jid not in seen:
                if jid not in failed_ids:
                    failed_ids.append(jid)
                if not any(s.get("job_id") == jid for s in skipped):
                    reasons = kept.get(jid, ["ineligible job"])
                    skipped.append({"job_id": jid, "reasons": reasons})

    if not dry_run and manifest_path_str:
        manifest_status = "partial" if failed_ids else "completed"
        completed_manifest: dict[str, Any] = {
            "status": manifest_status,
            "mode": mode,
            "planned_ids": planned_ids,
            "removed_ids": deleted_ids,
            "failed_ids": failed_ids,
        }
        if mode == "project":
            completed_manifest["project"] = str(target_project)
        _write_manifest(store_jobs, Path(manifest_path_str), completed_manifest)

    warnings_list = [fail_reason] if fail_reason is not None else []

    return {
        "store": str(store_path_obj.resolve()),
        "mode": mode,
        "project": str(target_project) if mode == "project" else None,
        "dry_run": dry_run,
        "older_than_days": older_than_days,
        "candidates": candidates_list,
        "kept": kept_list,
        "skipped": skipped,
        "candidate_count": len(candidates_list),
        "kept_count": len(kept_list),
        "skipped_count": len(skipped),
        "deleted_ids": deleted_ids,
        "deleted_count": len(deleted_ids),
        "failed_ids": failed_ids,
        "manifest": manifest_path_str,
        "warnings": warnings_list,
    }


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse CLI command line arguments."""
    parser = argparse.ArgumentParser(
        prog="agy_mcp.cleanup_jobs",
        description="Safe, dry-run-first job record cleaner for agy-mcp stores.",
    )
    parser.add_argument(
        "--store",
        required=True,
        type=str,
        help="Path to the agy-mcp state store directory.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        default=False,
        help="Read-only mode: list projects, counts by state, and eligible counts.",
    )
    parser.add_argument(
        "--project",
        type=str,
        default=None,
        help="Target project path to clean up stale job records for.",
    )
    parser.add_argument(
        "--job",
        dest="jobs",
        action="append",
        default=None,
        help=(
            "Explicit job ID to remove (repeatable). Note: symlinked job folders exit 2 "
            "by design; Unicode paths are not normalized."
        ),
    )
    parser.add_argument(
        "--older-than-days",
        type=float,
        default=7.0,
        help="Minimum age in days for job records to be candidates in project mode (default: 7.0).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        default=False,
        help="Enable deletion of confirmed candidate jobs (default: dry run).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output result as JSON only.",
    )
    return parser.parse_args(argv)


def _list_projects(
    store_path: Path, older_than_days: float
) -> tuple[list[dict[str, Any]], list[str]]:
    """Inspect store and return project summaries with state/eligible counts and warnings."""
    store_jobs = store_path / "jobs"
    projects_map: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []

    for child in sorted(store_jobs.iterdir(), key=lambda p: p.name):
        if child.name.startswith(".cleanup-manifest-"):
            continue
        if child.is_symlink() or not child.is_dir() or not _is_canonical_uuid(child.name):
            continue

        c_name = child.name
        child_path = str(child.resolve())
        job_file = child / "job.json"
        if job_file.is_symlink() or not job_file.is_file():
            warnings.append(
                f"store contains unreadable or corrupt job record ({c_name}) at {child_path}; "
                "left untouched; inspect or remove manually"
            )
            continue

        try:
            data, failure = load_job_json(job_file)
            if failure is not None:
                if failure.problem is JobJsonProblem.TOO_LARGE:
                    warnings.append(
                        f"store contains oversized job record ({c_name}) at {child_path}; "
                        "left untouched; inspect or remove manually"
                    )
                else:
                    warnings.append(
                        f"store contains unreadable or corrupt job record ({c_name}) at "
                        f"{child_path}: {failure.detail}; left untouched; inspect or remove "
                        "manually"
                    )
                continue
            if not isinstance(data, dict):
                warnings.append(
                    f"store contains non-object job record ({c_name}) at {child_path}; "
                    "left untouched; inspect or remove manually"
                )
                continue
        except Exception as exc:
            warnings.append(
                f"store contains unreadable or corrupt job record ({c_name}) at "
                f"{child_path}: {exc}; left untouched; inspect or remove manually"
            )
            continue

        repo_val = data.get("repo_path")
        norm_repo = None
        if isinstance(repo_val, str) and repo_val.strip() and "\x00" not in repo_val:
            try:
                norm_repo = normalize_repo_path(repo_val)
            except (ValueError, OSError):
                norm_repo = None

        if norm_repo is None:
            norm_repo = "(unknown)"

        if norm_repo not in projects_map:
            projects_map[norm_repo] = {
                "repo_path": norm_repo,
                "total_jobs": 0,
                "eligible_count": 0,
                "states": {},
            }

        entry = projects_map[norm_repo]
        entry["total_jobs"] += 1
        state_str = str(data.get("state", "unknown"))
        entry["states"][state_str] = entry["states"].get(state_str, 0) + 1

    for norm_repo, entry in projects_map.items():
        if norm_repo == "(unknown)":
            entry["eligible_count"] = 0
        else:
            res = run_cleanup(
                store_path,
                project=norm_repo,
                dry_run=True,
                older_than_days=older_than_days,
            )
            entry["eligible_count"] = res["candidate_count"]

    return [projects_map[k] for k in sorted(projects_map.keys())], warnings


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point for python -m agy_mcp.cleanup_jobs."""
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return exc.code

    modes_count = sum([bool(args.list), args.project is not None, bool(args.jobs)])
    if modes_count == 0:
        sys.stderr.write("Error: one of --list, --project, or --job mode is required\n")
        return 2
    if modes_count > 1:
        sys.stderr.write(
            "Error: --list, --project, and --job are mutually exclusive modes\n"
        )
        return 2

    if args.project is not None and not args.project.strip():
        sys.stderr.write("Error: --project path cannot be empty or whitespace\n")
        return 2

    try:
        val = float(args.older_than_days)
        if not math.isfinite(val) or val < 0:
            sys.stderr.write("Error: --older-than-days must be finite and non-negative\n")
            return 2
    except ValueError:
        sys.stderr.write("Error: --older-than-days must be a valid float\n")
        return 2

    # argparse's type=Path would normalize "" to Path(".") before this check,
    # silently turning an empty argument into the current directory (D100).
    store_raw = args.store
    if not str(store_raw).strip():
        sys.stderr.write("Error: --store path cannot be empty or whitespace\n")
        return 2
    store_path = Path(store_raw)
    try:
        abs_store = Path(os.path.abspath(store_path))

        if (
            store_path.is_symlink()
            or abs_store.is_symlink()
            or any(p.is_symlink() for p in abs_store.parents)
            or abs_store.resolve(strict=False) != abs_store
        ):
            sys.stderr.write(
                f"Error: store path or ancestor directory is a symlink: {store_path}\n"
            )
            return 2

        if not store_path.exists():
            sys.stderr.write(f"Error: store path does not exist: {store_path}\n")
            return 2

        if not store_path.is_dir():
            sys.stderr.write(f"Error: store path is not a directory: {store_path}\n")
            return 2

        store_jobs = store_path / "jobs"
        if store_jobs.is_symlink():
            sys.stderr.write(f"Error: store/jobs is a symlink: {store_jobs}\n")
            return 2

        if not store_jobs.exists() or not store_jobs.is_dir():
            sys.stderr.write(f"Error: store/jobs is missing or not a directory: {store_jobs}\n")
            return 2

        try:
            next(store_jobs.iterdir(), None)
        except OSError as exc:
            sys.stderr.write(f"Error: store/jobs directory unreadable: {exc}\n")
            return 2

        home_env = os.environ.get("HOME")
        if not home_env:
            sys.stderr.write("Error: HOME environment variable is not set\n")
            return 2

        resolved_home = Path(home_env).resolve(strict=False)
        resolved_store = store_path.resolve(strict=False)
        if resolved_home not in resolved_store.parents:
            sys.stderr.write(
                f"Error: resolved store {resolved_store} is not contained inside "
                f"HOME {resolved_home}\n"
            )
            return 2
    except OSError as exc:
        sys.stderr.write(f"Error: cannot access store path or ancestor directory: {exc}\n")
        return 2

    # 1. --list mode (read-only, disk bit-for-bit unmodified)
    if args.list:
        try:
            projects, warnings = _list_projects(store_path, args.older_than_days)
        except OSError as exc:
            sys.stderr.write(f"Error: store/jobs directory unreadable: {exc}\n")
            return 2
        if args.json:
            sys.stdout.write(
                json.dumps(
                    {
                        "store": str(store_path.resolve()),
                        "projects": projects,
                        "warnings": warnings,
                    },
                    indent=2,
                )
                + "\n"
            )
        else:
            write = sys.stdout.write
            write(f"Store: {store_path.resolve()}\n")
            if warnings:
                for w in warnings:
                    write(f"Warning: {w}\n")
            if not projects:
                write("No projects found.\n")
            for p in projects:
                write(f"Project: {p['repo_path']}\n")
                write(
                    f"  Total jobs: {p['total_jobs']}, Eligible for cleanup: "
                    f"{p['eligible_count']}\n"
                )
                write("  States:\n")
                for s, c in sorted(p["states"].items()):
                    write(f"    {s}: {c}\n")
        return 0

    # 2. Pre-flight checks for explicit --job IDs
    if args.jobs:
        args.jobs = list(dict.fromkeys(args.jobs))
        for jid in args.jobs:
            if not jid.strip() or not _is_canonical_uuid(jid):
                sys.stderr.write(f"Error: malformed UUID: {jid}\n")
                return 2
            try:
                jdir = store_jobs / jid
                if not jdir.exists() or not jdir.is_dir() or jdir.is_symlink():
                    sys.stderr.write(f"Error: job id not found in store: {jid}\n")
                    return 2
            except OSError as exc:
                sys.stderr.write(f"Error: store/jobs directory unreadable: {exc}\n")
                return 2

    # 3. Execute cleanup / scan
    try:
        report = run_cleanup(
            store_path,
            project=args.project,
            job_ids=args.jobs,
            dry_run=not args.yes,
            older_than_days=args.older_than_days,
        )
    except OSError as exc:
        sys.stderr.write(f"Error: store/jobs directory unreadable: {exc}\n")
        return 2
    except Exception as exc:
        sys.stderr.write(f"Unexpected internal error: {exc}\n")
        return 1

    # 4. Output results
    if args.json:
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
    else:
        write = sys.stdout.write
        write(f"Store: {report['store']}\n")
        write(f"Dry run: {report['dry_run']}\n")
        if report.get("project"):
            write(f"Project: {report['project']}\n")
        if report["mode"] == "project":
            write(f"Older than (days): {report['older_than_days']}\n")

        if report.get("warnings"):
            for w in report["warnings"]:
                write(f"Warning: {w}\n")

        if report["candidate_count"] == 0 and report["kept_count"] == 0:
            write("No jobs for project found.\n")

        write(f"Candidates ({report['candidate_count']}):\n")
        for c in report["candidates"]:
            write(f"  {c['job_id']} [{c['state']}] {c['repo_path']}\n")
            for r in c.get("reasons", []):
                write(f"    - {r}\n")

        write(f"Kept ({report['kept_count']}):\n")
        for k in report["kept"]:
            write(f"  {k['job_id']}\n")
            for r in k.get("reasons", []):
                write(f"    - {r}\n")

        has_worktree_kept = any(
            any("worktree" in r.lower() for r in k.get("reasons", []))
            for k in report["kept"]
        )
        if has_worktree_kept:
            write(
                "Suggestion: Run 'git worktree prune' in the repository to clean up "
                "registered worktrees.\n"
            )

        if not report["dry_run"]:
            write(f"Deleted: {report['deleted_count']}\n")
            if report.get("skipped"):
                write(f"Skipped/Failed ({report['skipped_count']}):\n")
                for s in report["skipped"]:
                    write(f"  {s['job_id']}\n")
                    for r in s.get("reasons", []):
                        write(f"    - {r}\n")
            if report.get("manifest"):
                write(f"Manifest: {report['manifest']}\n")

    # 5. Determine exit code
    if not args.yes:
        return 0

    if args.jobs:
        if report["candidate_count"] == 0:
            return 2
        deleted_count = report["deleted_count"]
        total_requested = len(args.jobs)
        if deleted_count < total_requested or report.get("failed_ids"):
            return 1
        return 0

    if report.get("failed_ids"):
        return 1

    return 0
