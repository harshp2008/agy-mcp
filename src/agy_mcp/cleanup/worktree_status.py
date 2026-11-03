"""Worktree registration decision for one job record (SPEC §23, R1/R3/R6-R8).

``_check_worktree_status`` is moved here whole from ``cleanup_jobs``: the
473-line union scan is the fail-closed core of the cleaner and is deliberately
not split in this run (audit D1 is a later change).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from agy_mcp.cleanup.git_metadata import (
    _has_git_dir_markers,
    _read_git_metadata_file,
    _validate_full_git_dir,
    _validate_git_head_file,
)
from agy_mcp.cleanup.store_io import normalize_repo_path


def _check_worktree_status(job_dir: Path, data: dict[str, Any]) -> tuple[bool, Optional[str]]:
    """Return (is_registered, reason) indicating git worktree registration status.

    Checks:
    1. Local work/ directory contains a .git file or directory.
    2. Explicit worktree_path/.git exists.
    3. Target repository .git directory or file (linked worktree commondir, submodule)
       lists job_dir, work_dir, or worktree_path in its worktrees registrations (R8).
    4. Validates repository files and dirs for working repo (HEAD, objects, refs,
       config, commondir, gitdir) and fails closed if corrupted or missing (R7).
    5. If worktree registrations directory exists but cannot be read (e.g. PermissionError),
       fails closed: returns (True, "worktree registrations directory unreadable (...)").

    Why the union: a registration can be recorded in the job's own work/.git, in
    the explicit worktree_path, in the repository's .git/worktrees, or in the
    worktrees directory of a bare repository that is itself the repo_path (a bare
    repo may also contain a .git directory pointing elsewhere). The function
    gathers every candidate root and accepts a match in ANY of them instead of
    letting one layout shadow another. The asymmetry is deliberate: a false
    "registered" only keeps a row (safe), while a false "not registered" would
    let run_cleanup delete a record whose worktree still exists -- the fail-open
    the differential harness exists to catch, which is why this function is so
    much longer than the happy path suggests.
    """
    work_dir = job_dir / "work"
    dot_git = work_dir / ".git"
    try:
        if dot_git.exists() or dot_git.is_file() or dot_git.is_dir():
            return (
                True,
                "worktree registered (run 'git worktree prune' in repository to unregister)",
            )
    except OSError:
        return (
            True,
            "worktree registered (run 'git worktree prune' in repository to unregister)",
        )

    worktree_path_val = data.get("worktree_path")
    if worktree_path_val is not None:
        if not isinstance(worktree_path_val, (str, Path)):
            return (
                True,
                (
                    "worktree registrations unreadable "
                    f"(invalid worktree_path type: {type(worktree_path_val).__name__})"
                ),
            )
        wt_str = str(worktree_path_val).strip()
        if "\x00" in wt_str:
            return (
                True,
                "worktree registrations unreadable (null byte in worktree_path)",
            )
        try:
            wt_git = Path(os.path.expanduser(wt_str)) / ".git"
            if wt_git.exists() or wt_git.is_file() or wt_git.is_dir():
                return (
                    True,
                    "worktree registered (run 'git worktree prune' in repository to unregister)",
                )
        except (OSError, ValueError):
            return (
                True,
                "worktree registered (run 'git worktree prune' in repository to unregister)",
            )
        if not os.path.isabs(os.path.expanduser(wt_str)):
            try:
                wt_rel_git = (job_dir / os.path.expanduser(wt_str)) / ".git"
                if wt_rel_git.exists() or wt_rel_git.is_file() or wt_rel_git.is_dir():
                    return (
                        True,
                        (
                            "worktree registered (run 'git worktree prune' "
                            "in repository to unregister)"
                        ),
                    )
            except (OSError, ValueError):
                return (
                    True,
                    "worktree registered (run 'git worktree prune' in repository to unregister)",
                )

    repo_path_val = data.get("repo_path")
    if isinstance(repo_path_val, str) and repo_path_val.strip():
        repo_path: Optional[Path] = None
        if "\x00" not in repo_path_val:
            try:
                repo_path = Path(os.path.expanduser(repo_path_val)).resolve(strict=False)
            except (ValueError, OSError):
                repo_path = None

        if repo_path is not None:
            candidate_wt_roots: list[Path] = []
            try:
                git_dir = repo_path / ".git"

                # 1. Inspect repo_path/.git if present (pointer file, directory, or invalid)
                if git_dir.is_symlink() and not git_dir.exists():
                    # Dangling symlink (R3, R5)
                    return (
                        True,
                        (
                            "worktree registrations unreadable "
                            f"(.git is a dangling symlink: {git_dir})"
                        ),
                    )
                elif git_dir.is_file():
                    # Linked worktree or submodule: follow gitdir and commondir
                    raw_content, err = _read_git_metadata_file(git_dir, ".git file")
                    if err is not None:
                        return True, f"worktree registrations unreadable ({err})"

                    if not raw_content.startswith("gitdir:"):
                        return (
                            True,
                            (
                                "worktree registrations unreadable "
                                "(.git file is invalid: missing 'gitdir:' pointer)"
                            ),
                        )
                    raw_gitdir = raw_content[len("gitdir:") :].strip()
                    if not raw_gitdir:
                        return (
                            True,
                            (
                                "worktree registrations unreadable "
                                "(.git file is invalid: empty gitdir pointer)"
                            ),
                        )
                    if os.path.isabs(raw_gitdir):
                        actual_gitdir = Path(raw_gitdir).resolve(strict=False)
                    else:
                        actual_gitdir = (repo_path / raw_gitdir).resolve(strict=False)

                    if not actual_gitdir.exists():
                        return (
                            True,
                            (
                                "worktree registrations unreadable "
                                f"(.git file target missing: {actual_gitdir})"
                            ),
                        )
                    if not actual_gitdir.is_dir():
                        return (
                            True,
                            (
                                "worktree registrations unreadable "
                                f"(.git file target is not a directory: {actual_gitdir})"
                            ),
                        )

                    commondir_file = actual_gitdir / "commondir"
                    if commondir_file.exists() or commondir_file.is_symlink():
                        # Linked worktree private dir: requires HEAD, gitdir, commondir (R7, R10)
                        ok, err = _validate_git_head_file(
                            actual_gitdir / "HEAD", ".git file target HEAD"
                        )
                        if not ok:
                            return (
                                True,
                                (
                                    "worktree registrations unreadable "
                                    f"(.git file target is not a git directory: {actual_gitdir})"
                                ),
                            )
                        _, err = _read_git_metadata_file(
                            actual_gitdir / "gitdir", ".git file target gitdir"
                        )
                        if err is not None:
                            return (
                                True,
                                (
                                    "worktree registrations unreadable "
                                    f"(.git file target is not a git directory: {actual_gitdir})"
                                ),
                            )

                        if commondir_file.is_symlink() and not commondir_file.exists():
                            return (
                                True,
                                (
                                    "worktree registrations unreadable "
                                    f"(commondir file is invalid: dangling symlink: "
                                    f"{commondir_file})"
                                ),
                            )
                        raw_commondir, err = _read_git_metadata_file(
                            commondir_file, "commondir file"
                        )
                        if err is not None:
                            return True, f"worktree registrations unreadable ({err})"

                        if os.path.isabs(raw_commondir):
                            common_gitdir = Path(raw_commondir).resolve(strict=False)
                        else:
                            common_gitdir = (actual_gitdir / raw_commondir).resolve(strict=False)

                        if not common_gitdir.exists():
                            return (
                                True,
                                (
                                    "worktree registrations unreadable "
                                    f"(commondir file is invalid: target missing: "
                                    f"{common_gitdir})"
                                ),
                            )
                        if not common_gitdir.is_dir():
                            return (
                                True,
                                (
                                    "worktree registrations unreadable "
                                    f"(commondir file is invalid: target is not a directory: "
                                    f"{common_gitdir})"
                                ),
                            )
                        ok, err = _validate_full_git_dir(common_gitdir, "commondir target")
                        if not ok:
                            return (
                                True,
                                (
                                    "worktree registrations unreadable "
                                    f"(commondir target is not a git directory: "
                                    f"{common_gitdir})"
                                ),
                            )
                        if (common_gitdir / "worktrees") not in candidate_wt_roots:
                            candidate_wt_roots.append(common_gitdir / "worktrees")
                        if (actual_gitdir / "worktrees") not in candidate_wt_roots:
                            candidate_wt_roots.append(actual_gitdir / "worktrees")
                    else:
                        # commondir missing: must be a standalone git directory / submodule (R7)
                        is_full, _ = _validate_full_git_dir(
                            actual_gitdir, ".git file target"
                        )
                        if is_full:
                            if (actual_gitdir / "worktrees") not in candidate_wt_roots:
                                candidate_wt_roots.append(actual_gitdir / "worktrees")
                        else:
                            return (
                                True,
                                (
                                    "worktree registrations unreadable "
                                    f"(.git file target is not a git directory: {actual_gitdir})"
                                ),
                            )

                elif git_dir.is_dir():
                    # Normal git repository or bare repo containing .git
                    if not (git_dir / "HEAD").is_file():
                        return (
                            True,
                            (
                                "worktree registrations unreadable "
                                f"(.git is not a git directory: {git_dir})"
                            ),
                        )
                    has_bare_internals = any(
                        (repo_path / m).is_dir() or (repo_path / m).is_symlink()
                        for m in ("worktrees", "objects", "refs")
                    )
                    if not has_bare_internals:
                        # Normal git repository: validate full git directory structure (R7)
                        ok, err = _validate_full_git_dir(git_dir, ".git directory")
                        if not ok:
                            return (
                                True,
                                (
                                    "worktree registrations unreadable "
                                    f"(.git is not a git directory: {git_dir})"
                                ),
                            )
                        if (git_dir / "worktrees") not in candidate_wt_roots:
                            candidate_wt_roots.append(git_dir / "worktrees")
                elif git_dir.exists():
                    return (
                        True,
                        (
                            "worktree registrations unreadable "
                            f"(.git is invalid: not a regular file or directory: {git_dir})"
                        ),
                    )

                # 2. R6 (a, b), R7: Bare-repo detection and union.
                # Never let one branch shadow the other.
                if not git_dir.exists() and not git_dir.is_symlink():
                    # When .git is absent or not a pointer/dir:
                    # Check if repo_path has ANY git directory markers.
                    if _has_git_dir_markers(repo_path):
                        ok, err = _validate_full_git_dir(repo_path, "bare repository")
                        if not ok:
                            return (
                                True,
                                (
                                    "worktree registrations unreadable "
                                    f"(repo_path is not a git directory: {repo_path})"
                                ),
                            )
                        if (repo_path / "worktrees") not in candidate_wt_roots:
                            candidate_wt_roots.append(repo_path / "worktrees")
                else:
                    # When .git is a pointer or directory: repo_path may ALSO be a bare repo
                    # (e.g. F2: bare repo containing a .git directory inside it).
                    has_bare_internals = any(
                        (repo_path / m).is_dir() or (repo_path / m).is_symlink()
                        for m in ("worktrees", "objects", "refs")
                    )
                    if (repo_path / "HEAD").is_file() and _has_git_dir_markers(repo_path):
                        ok, err = _validate_full_git_dir(repo_path, "bare repository")
                        if ok:
                            if (repo_path / "worktrees") not in candidate_wt_roots:
                                candidate_wt_roots.append(repo_path / "worktrees")
                        else:
                            return (
                                True,
                                (
                                    "worktree registrations unreadable "
                                    f"(repo_path is not a git directory: {repo_path})"
                                ),
                            )
                    elif (has_bare_internals or _has_git_dir_markers(repo_path)) and not (
                        repo_path / "HEAD"
                    ).is_file():
                        return (
                            True,
                            (
                                "worktree registrations unreadable "
                                f"(repo_path is not a git directory: {repo_path})"
                            ),
                        )
            except (OSError, UnicodeDecodeError) as exc:
                return True, f"worktree registrations directory unreadable ({exc})"
            except Exception as exc:
                return True, f"worktree registrations unreadable ({exc})"

            job_dir_res = job_dir.resolve(strict=False)
            work_dir_res = (job_dir / "work").resolve(strict=False)

            owned_paths: list[Path] = [
                job_dir_res,
                work_dir_res,
                work_dir_res / ".git",
            ]

            if isinstance(worktree_path_val, (str, Path)) and str(worktree_path_val).strip():
                wt_str = str(worktree_path_val).strip()
                if "\x00" in wt_str:
                    return (
                        True,
                        "worktree registrations unreadable (null byte in worktree_path)",
                    )
                try:
                    wt_norm = Path(normalize_repo_path(wt_str))
                    owned_paths.append(wt_norm)
                    owned_paths.append(wt_norm / ".git")
                    if not os.path.isabs(os.path.expanduser(wt_str)):
                        wt_rel_job = (job_dir / os.path.expanduser(wt_str)).resolve(strict=False)
                        owned_paths.append(wt_rel_job)
                        owned_paths.append(wt_rel_job / ".git")
                except (ValueError, OSError) as exc:
                    return True, f"worktree registrations unreadable ({exc})"

            for wt_root in candidate_wt_roots:
                try:
                    if not wt_root.exists() or not wt_root.is_dir():
                        continue
                    try:
                        entries = sorted(wt_root.iterdir(), key=lambda p: p.name)
                    except (OSError, UnicodeDecodeError) as exc:
                        return True, f"worktree registrations directory unreadable ({exc})"

                    unreadable_entry_reason: Optional[str] = None
                    for entry in entries:
                        if entry.is_dir():
                            gitdir_file = entry / "gitdir"
                            try:
                                if gitdir_file.is_symlink() and not gitdir_file.exists():
                                    if unreadable_entry_reason is None:
                                        unreadable_entry_reason = (
                                            "worktree registrations unreadable "
                                            f"({entry.name}: gitdir is a dangling symlink: "
                                            f"{gitdir_file})"
                                        )
                                elif gitdir_file.exists():
                                    raw_entry, err = _read_git_metadata_file(
                                        gitdir_file, f"{entry.name}: gitdir file"
                                    )
                                    if err is not None:
                                        if unreadable_entry_reason is None:
                                            unreadable_entry_reason = (
                                                f"worktree registrations unreadable ({err})"
                                            )
                                    else:
                                        target = raw_entry
                                        if os.path.isabs(target):
                                            target_path = Path(target).resolve(strict=False)
                                        else:
                                            target_path = (entry / target).resolve(strict=False)

                                        target_parent = (
                                            target_path.parent
                                            if target_path.name == ".git"
                                            else None
                                        )
                                        is_match = False
                                        for p in owned_paths:
                                            if target_path == p or p in target_path.parents:
                                                is_match = True
                                                break
                                            if target_parent is not None and target_parent == p:
                                                is_match = True
                                                break

                                        if is_match:
                                            return (
                                                True,
                                                (
                                                    "worktree registered (run 'git worktree prune' "
                                                    "in repository to unregister)"
                                                ),
                                            )
                            except (OSError, UnicodeDecodeError) as exc:
                                if unreadable_entry_reason is None:
                                    unreadable_entry_reason = (
                                        f"worktree registrations unreadable ({entry.name}: {exc})"
                                    )

                            # Validate entry commondir if present (R2, R5)
                            entry_commondir = entry / "commondir"
                            try:
                                if entry_commondir.is_symlink() and not entry_commondir.exists():
                                    if unreadable_entry_reason is None:
                                        unreadable_entry_reason = (
                                            "worktree registrations unreadable "
                                            f"({entry.name}: commondir is a dangling symlink: "
                                            f"{entry_commondir})"
                                        )
                                elif entry_commondir.exists():
                                    _, err = _read_git_metadata_file(
                                        entry_commondir, f"{entry.name}: commondir file"
                                    )
                                    if err is not None and unreadable_entry_reason is None:
                                        unreadable_entry_reason = (
                                            f"worktree registrations unreadable ({err})"
                                        )
                            except (OSError, UnicodeDecodeError) as exc:
                                if unreadable_entry_reason is None:
                                    unreadable_entry_reason = (
                                        f"worktree registrations unreadable ({entry.name}: {exc})"
                                    )

                            # Validate entry locked if present (R2, R5)
                            entry_locked = entry / "locked"
                            try:
                                if entry_locked.is_symlink() and not entry_locked.exists():
                                    if unreadable_entry_reason is None:
                                        unreadable_entry_reason = (
                                            "worktree registrations unreadable "
                                            f"({entry.name}: locked is a dangling symlink: "
                                            f"{entry_locked})"
                                        )
                                elif entry_locked.exists():
                                    _, err = _read_git_metadata_file(
                                        entry_locked,
                                        f"{entry.name}: locked file",
                                        allow_empty=True,
                                    )
                                    if err is not None and unreadable_entry_reason is None:
                                        unreadable_entry_reason = (
                                            f"worktree registrations unreadable ({err})"
                                        )
                            except (OSError, UnicodeDecodeError) as exc:
                                if unreadable_entry_reason is None:
                                    unreadable_entry_reason = (
                                        f"worktree registrations unreadable ({entry.name}: {exc})"
                                    )
                    if unreadable_entry_reason is not None:
                        return True, unreadable_entry_reason
                except (OSError, UnicodeDecodeError) as exc:
                    return True, f"worktree registrations directory unreadable ({exc})"
                except Exception as exc:
                    return True, f"worktree registrations unreadable ({exc})"

    return False, None
