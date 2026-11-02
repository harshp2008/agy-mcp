"""Differential review test harness for cleanup_jobs against real git ground truth.

Ports the 390 git layout differential mutation cases and 18 job.json integrity cases
from the review harness into an isolated pytest test suite.

Policy:
- If cleaner decides "candidate" (deletable) while git (after the mutation) still lists
  the job's worktree registration, OR git itself can no longer read the repository
  (works=False), that is FAIL-OPEN. The test fails immediately.
- If cleaner decides "kept" (not deletable) while git does not list the worktree
  (e.g., git cannot read the corrupted worktree entry or considers it invalid), that is
  an OVER-BLOCK. Over-blocking is allowed by safety policy (fail-closed) and passes.
- Any crash (unhandled exception or traceback in run_cleanup or CLI dry run) is a CRASH
  and fails the test.
- Kept neighbor job.json corruption (BOM, NUL, truncation, extra bytes, duplicate keys,
  oversized, symlink, chmod 000, directory) must block cleanup store-wide with a warning.
- Candidate's own job.json corruption must keep the candidate fail-closed.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

from agy_mcp.cleanup_jobs import (
    _check_worktree_status,
    run_cleanup,
)
from agy_mcp.cleanup_jobs import main as cli_main
from tests.helpers.make_repo import get_safe_git_env


def _make_tree_writable(path: Path) -> None:
    """Recursively reset permissions to allow pytest cleanup of temporary directories."""
    if not path.exists() and not path.is_symlink():
        return
    for root, dirs, files in os.walk(path, topdown=False):
        for fname in files:
            p = Path(root) / fname
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
        for dname in dirs:
            p = Path(root) / dname
            try:
                os.chmod(p, 0o700)
            except OSError:
                pass
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def check_git_registered(
    repo_path: Path, work_path: Path, candidate_entry_dirs: list[Path] | None = None
) -> tuple[bool, bool, str]:
    """Check git ground truth via 'git -C <repo_path> worktree list --porcelain'.

    Returns (git_works, is_registered, output_text).
    """
    res = subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        errors="replace",
        env=get_safe_git_env(repo_path.parent),
    )
    if res.returncode != 0:
        return False, False, f"git returned {res.returncode}: {res.stderr.strip()}"

    work_resolved = str(work_path.resolve(strict=False))
    found = False
    for line in res.stdout.splitlines():
        if line.startswith("worktree "):
            wt_raw = line[len("worktree ") :].strip()
            wt_clean = wt_raw.lstrip("\ufeff")
            if wt_clean == str(work_path):
                found = True
                break
            try:
                wt_res = str(Path(wt_clean).resolve(strict=False))
                if wt_res == work_resolved:
                    found = True
                    break
            except Exception:
                pass
            try:
                wt_res2 = str((repo_path / wt_clean).resolve(strict=False))
                if wt_res2 == work_resolved:
                    found = True
                    break
            except Exception:
                pass
            if candidate_entry_dirs:
                for cdir in candidate_entry_dirs:
                    try:
                        wt_res3 = str((cdir / wt_clean).resolve(strict=False))
                        if wt_res3 == work_resolved:
                            found = True
                            break
                    except Exception:
                        pass
                if found:
                    break
    return True, found, res.stdout


def _run_cli_dry_run(store_path: Path, repo_path: Path) -> tuple[bool, str]:
    """Invoke CLI dry run in-process and return (crashed, detail_message)."""
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        cli_main(
            [
                "--store",
                str(store_path),
                "--project",
                str(repo_path),
                "--older-than-days",
                "0",
            ]
        )
        err = sys.stderr.getvalue()
        if "Traceback" in err:
            return True, f"CLI stderr contained traceback: {err}"
        return False, err
    except Exception as exc:
        return True, f"CLI raised unhandled exception: {exc}\n{traceback.format_exc()}"
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr


def _backup_file(p: Path) -> tuple[Callable[[], None], Optional[bytes]]:
    is_symlink = p.is_symlink()
    stat_mode = p.lstat().st_mode
    link_target = os.readlink(p) if is_symlink else None
    data = p.read_bytes() if (not is_symlink and p.is_file()) else None

    def restore() -> None:
        try:
            os.chmod(p, 0o700)
        except OSError:
            pass
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists() or p.is_symlink():
            p.unlink()
        if is_symlink:
            assert link_target is not None
            p.symlink_to(link_target)
        elif data is not None:
            p.write_bytes(data)
        try:
            os.chmod(p, stat_mode)
        except OSError:
            pass

    return restore, data


def _get_file_mutations() -> dict[str, Callable[[Path], tuple[Callable[[], None], str]]]:
    def mut_append_comment(p: Path) -> tuple[Callable[[], None], str]:
        restore, data = _backup_file(p)
        assert data is not None
        p.write_bytes(data + b"\n# extra comment line\n")
        return restore, "appended comment line"

    def mut_prepend_blank(p: Path) -> tuple[Callable[[], None], str]:
        restore, data = _backup_file(p)
        assert data is not None
        p.write_bytes(b"\n" + data)
        return restore, "prepended blank line"

    def mut_trailing_space(p: Path) -> tuple[Callable[[], None], str]:
        restore, data = _backup_file(p)
        assert data is not None
        lines = data.splitlines(keepends=True)
        p.write_bytes(b"".join(line.rstrip(b"\r\n") + b" \n" for line in lines))
        return restore, "added trailing space"

    def mut_crlf(p: Path) -> tuple[Callable[[], None], str]:
        restore, data = _backup_file(p)
        assert data is not None
        p.write_bytes(data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        return restore, "CRLF line endings"

    def mut_lone_cr(p: Path) -> tuple[Callable[[], None], str]:
        restore, data = _backup_file(p)
        assert data is not None
        p.write_bytes(data.replace(b"\r\n", b"\n").replace(b"\n", b"\r"))
        return restore, "lone CR endings"

    def mut_bom(p: Path) -> tuple[Callable[[], None], str]:
        restore, data = _backup_file(p)
        assert data is not None
        p.write_bytes(b"\xef\xbb\xbf" + data)
        return restore, "prepended UTF-8 BOM"

    def mut_truncate_half(p: Path) -> tuple[Callable[[], None], str]:
        restore, data = _backup_file(p)
        assert data is not None
        p.write_bytes(data[: len(data) // 2])
        return restore, "truncated to half"

    def mut_empty(p: Path) -> tuple[Callable[[], None], str]:
        restore, data = _backup_file(p)
        p.write_bytes(b"")
        return restore, "empty file (0 bytes)"

    def mut_garbage_bytes(p: Path) -> tuple[Callable[[], None], str]:
        restore, data = _backup_file(p)
        p.write_bytes(b"\xff\xfe\x80\x90garbage\xff\xee")
        return restore, "non-UTF-8 garbage bytes"

    def mut_swap_dir(p: Path) -> tuple[Callable[[], None], str]:
        restore, data = _backup_file(p)
        p.unlink()
        p.mkdir()
        return restore, "swapped file for directory"

    def mut_swap_symlink_real(p: Path) -> tuple[Callable[[], None], str]:
        restore, data = _backup_file(p)
        assert data is not None
        backup_path = p.with_name(p.name + ".real_target")
        backup_path.write_bytes(data)
        p.unlink()
        p.symlink_to(backup_path.name)

        def full_restore() -> None:
            try:
                if backup_path.exists() or backup_path.is_symlink():
                    backup_path.unlink()
            except OSError:
                pass
            restore()

        return full_restore, "swapped for symlink to real file"

    def mut_swap_symlink_dangling(p: Path) -> tuple[Callable[[], None], str]:
        restore, data = _backup_file(p)
        p.unlink()
        p.symlink_to("nonexistent_target_dangling_12345")
        return restore, "swapped for dangling symlink"

    def mut_chmod_000(p: Path) -> tuple[Callable[[], None], str]:
        restore, data = _backup_file(p)
        os.chmod(p, 0o000)
        return restore, "chmod 000"

    def mut_relative_vs_abs(p: Path) -> tuple[Callable[[], None], str]:
        restore, data = _backup_file(p)
        assert data is not None
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines(keepends=True)
        new_lines: list[bytes] = []
        for line in lines:
            prefix = ""
            path_part = line.strip()
            if path_part.startswith("gitdir:"):
                prefix = "gitdir: "
                path_part = path_part[len("gitdir:") :].strip()
            if path_part and os.path.isabs(path_part):
                try:
                    rel = os.path.relpath(path_part, str(p.parent))
                    new_lines.append(f"{prefix}{rel}\n".encode("utf-8"))
                    continue
                except Exception:
                    pass
            elif path_part:
                abs_p = str((p.parent / path_part).resolve(strict=False))
                new_lines.append(f"{prefix}{abs_p}\n".encode("utf-8"))
                continue
            new_lines.append(line.encode("utf-8"))
        p.write_bytes(b"".join(new_lines))
        return restore, "toggled relative vs absolute path"

    def mut_dotdot_segments(p: Path) -> tuple[Callable[[], None], str]:
        restore, data = _backup_file(p)
        assert data is not None
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines(keepends=True)
        new_lines: list[bytes] = []
        for line in lines:
            prefix = ""
            path_part = line.strip()
            if path_part.startswith("gitdir:"):
                prefix = "gitdir: "
                path_part = path_part[len("gitdir:") :].strip()
            if "/" in path_part:
                parts = path_part.split("/")
                if len(parts) >= 2:
                    parts.insert(-1, "sub/..")
                    path_part = "/".join(parts)
            new_lines.append(f"{prefix}{path_part}\n".encode("utf-8"))
        p.write_bytes(b"".join(new_lines))
        return restore, "added dotdot segments (..)"

    def mut_uppercase_component(p: Path) -> tuple[Callable[[], None], str]:
        restore, data = _backup_file(p)
        assert data is not None
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines(keepends=True)
        new_lines: list[bytes] = []
        for line in lines:
            prefix = ""
            path_part = line.strip()
            if path_part.startswith("gitdir:"):
                prefix = "gitdir: "
                path_part = path_part[len("gitdir:") :].strip()
            if "work" in path_part:
                path_part = path_part.replace("work", "WORK")
            elif "repo" in path_part:
                path_part = path_part.replace("repo", "REPO")
            new_lines.append(f"{prefix}{path_part}\n".encode("utf-8"))
        p.write_bytes(b"".join(new_lines))
        return restore, "upper-cased path component"

    def mut_very_long_path(p: Path) -> tuple[Callable[[], None], str]:
        restore, data = _backup_file(p)
        assert data is not None
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines(keepends=True)
        new_lines: list[bytes] = []
        for line in lines:
            path_part = line.strip()
            new_lines.append((path_part + "/" + "a" * 4000 + "\n").encode("utf-8"))
        p.write_bytes(b"".join(new_lines))
        return restore, "added very long path segment"

    def mut_trailing_slash(p: Path) -> tuple[Callable[[], None], str]:
        restore, data = _backup_file(p)
        assert data is not None
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines(keepends=True)
        new_lines: list[bytes] = []
        for line in lines:
            path_part = line.strip()
            new_lines.append((path_part + "/\n").encode("utf-8"))
        p.write_bytes(b"".join(new_lines))
        return restore, "added trailing slash"

    def mut_nul(p: Path) -> tuple[Callable[[], None], str]:
        restore, data = _backup_file(p)
        assert data is not None
        mid = len(data) // 2
        p.write_bytes(data[:mid] + b"\x00" + data[mid:])
        return restore, "inserted NUL byte"

    return {
        "append_comment": mut_append_comment,
        "prepend_blank": mut_prepend_blank,
        "trailing_space": mut_trailing_space,
        "crlf": mut_crlf,
        "lone_cr": mut_lone_cr,
        "bom": mut_bom,
        "truncate_half": mut_truncate_half,
        "empty": mut_empty,
        "garbage_bytes": mut_garbage_bytes,
        "swap_dir": mut_swap_dir,
        "swap_symlink_real": mut_swap_symlink_real,
        "swap_symlink_dangling": mut_swap_symlink_dangling,
        "chmod_000": mut_chmod_000,
        "relative_vs_abs": mut_relative_vs_abs,
        "dotdot_segments": mut_dotdot_segments,
        "uppercase_component": mut_uppercase_component,
        "very_long_path": mut_very_long_path,
        "trailing_slash": mut_trailing_slash,
        "nul": mut_nul,
    }


def _get_dir_mutations() -> dict[str, Callable[[Path], tuple[Callable[[], None], str]]]:
    def mut_entry_chmod_000(d: Path) -> tuple[Callable[[], None], str]:
        os.chmod(d, 0o000)

        def restore() -> None:
            try:
                os.chmod(d, 0o700)
            except OSError:
                pass

        return restore, "chmod 000 on entry directory"

    def mut_entry_rename(d: Path) -> tuple[Callable[[], None], str]:
        renamed = d.with_name(d.name + "_renamed")
        d.rename(renamed)

        def restore() -> None:
            try:
                if renamed.exists():
                    renamed.rename(d)
            except OSError:
                pass

        return restore, "renamed entry directory"

    def mut_entry_swap_file(d: Path) -> tuple[Callable[[], None], str]:
        temp_copy = d.with_name(d.name + "_dir_copy")
        shutil.copytree(d, temp_copy)
        _make_tree_writable(d)
        shutil.rmtree(d)
        d.write_text("regular file instead of dir\n", encoding="utf-8")

        def restore() -> None:
            try:
                if d.is_file():
                    d.unlink()
                if temp_copy.exists():
                    temp_copy.rename(d)
            except OSError:
                pass

        return restore, "swapped directory for regular file"

    def mut_entry_swap_symlink_real(d: Path) -> tuple[Callable[[], None], str]:
        temp_copy = d.with_name(d.name + "_real_copy")
        d.rename(temp_copy)
        d.symlink_to(temp_copy.name)

        def restore() -> None:
            try:
                if d.is_symlink():
                    d.unlink()
                if temp_copy.exists():
                    temp_copy.rename(d)
            except OSError:
                pass

        return restore, "swapped directory for symlink to real"

    def mut_entry_swap_symlink_dangling(d: Path) -> tuple[Callable[[], None], str]:
        temp_copy = d.with_name(d.name + "_temp_save")
        d.rename(temp_copy)
        d.symlink_to("nonexistent_dir_dangling")

        def restore() -> None:
            try:
                if d.is_symlink():
                    d.unlink()
                if temp_copy.exists():
                    temp_copy.rename(d)
            except OSError:
                pass

        return restore, "swapped directory for dangling symlink"

    def mut_entry_empty_dir(d: Path) -> tuple[Callable[[], None], str]:
        temp_copy = d.with_name(d.name + "_temp_contents")
        shutil.copytree(d, temp_copy)
        for child in list(d.iterdir()):
            if child.is_dir():
                _make_tree_writable(child)
                shutil.rmtree(child)
            else:
                child.unlink()

        def restore() -> None:
            try:
                _make_tree_writable(d)
                shutil.rmtree(d)
                temp_copy.rename(d)
            except OSError:
                pass

        return restore, "emptied entry directory"

    def mut_entry_add_garbage_file(d: Path) -> tuple[Callable[[], None], str]:
        g_file = d / "garbage_extra_file"
        g_file.write_bytes(b"\xff\xfe\xaa\xbb")

        def restore() -> None:
            try:
                if g_file.exists():
                    g_file.unlink()
            except OSError:
                pass

        return restore, "added non-utf8 garbage file in entry directory"

    def mut_entry_uppercase_dir(d: Path) -> tuple[Callable[[], None], str]:
        renamed = d.with_name(d.name.upper() + "_UPPER")
        d.rename(renamed)

        def restore() -> None:
            try:
                if renamed.exists():
                    renamed.rename(d)
            except OSError:
                pass

        return restore, "upper-cased entry directory name"

    return {
        "entry_chmod_000": mut_entry_chmod_000,
        "entry_rename": mut_entry_rename,
        "swap_file": mut_entry_swap_file,
        "swap_symlink_real": mut_entry_swap_symlink_real,
        "swap_symlink_dangling": mut_entry_swap_symlink_dangling,
        "empty_dir": mut_entry_empty_dir,
        "add_garbage_file": mut_entry_add_garbage_file,
        "uppercase_dir": mut_entry_uppercase_dir,
    }


FILE_MUTATIONS = _get_file_mutations()
DIR_MUTATIONS = _get_dir_mutations()


def _setup_job(root: Path, repo_path: Path) -> tuple[Path, Path, str]:
    store_path = root / "store"
    store_jobs = store_path / "jobs"
    store_jobs.mkdir(parents=True, exist_ok=True)
    job_id = "00000000-0000-0000-0000-000000000001"
    job_dir = store_jobs / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    job_data = {
        "job_id": job_id,
        "title": "differential testing job",
        "state": "done",
        "repo_path": str(repo_path),
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2020-01-01T00:00:00Z",
    }
    (job_dir / "job.json").write_text(json.dumps(job_data, indent=2), encoding="utf-8")
    return store_path, job_dir, job_id


def build_layout_a(root: Path) -> dict[str, Any]:
    """Layout A: repo_path is a normal repository."""
    git_env = get_safe_git_env(root)
    repo = root / "repo_a"
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, env=git_env)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        env=git_env,
    )
    store_path, job_dir, job_id = _setup_job(root, repo)
    work = job_dir / "work"

    subprocess.run(
        ["git", "worktree", "add", str(work), "-b", "wt_a"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        env=git_env,
    )
    (work / ".git").unlink()

    entry_dir = repo / ".git" / "worktrees" / "work"
    return {
        "repo_path": repo,
        "job_dir": job_dir,
        "job_id": job_id,
        "store_path": store_path,
        "targets": {
            "common_worktrees_entry_gitdir": entry_dir / "gitdir",
            "common_worktrees_entry_commondir": entry_dir / "commondir",
            "common_worktrees_entry_locked": entry_dir / "locked",
            "common_worktrees_entry_dir": entry_dir,
        },
    }


def build_layout_b(root: Path) -> dict[str, Any]:
    """Layout B: repo_path is itself a linked worktree (.git file with commondir)."""
    git_env = get_safe_git_env(root)
    base_repo = root / "base_b"
    subprocess.run(["git", "init", str(base_repo)], check=True, capture_output=True, env=git_env)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=str(base_repo),
        check=True,
        capture_output=True,
        env=git_env,
    )

    repo = root / "repo_b"
    subprocess.run(
        ["git", "worktree", "add", str(repo), "-b", "repo_b_branch"],
        cwd=str(base_repo),
        check=True,
        capture_output=True,
        env=git_env,
    )

    store_path, job_dir, job_id = _setup_job(root, repo)
    work = job_dir / "work"
    subprocess.run(
        ["git", "worktree", "add", str(work), "-b", "wt_b"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        env=git_env,
    )
    (work / ".git").unlink()

    repo_git_file = repo / ".git"
    repo_gitdir = base_repo / ".git" / "worktrees" / "repo_b"
    entry_dir = base_repo / ".git" / "worktrees" / "work"

    return {
        "repo_path": repo,
        "job_dir": job_dir,
        "job_id": job_id,
        "store_path": store_path,
        "targets": {
            "repo_path_dot_git_file": repo_git_file,
            "gitdir_commondir": repo_gitdir / "commondir",
            "common_worktrees_entry_gitdir": entry_dir / "gitdir",
            "common_worktrees_entry_commondir": entry_dir / "commondir",
            "common_worktrees_entry_locked": entry_dir / "locked",
            "common_worktrees_entry_dir": entry_dir,
        },
    }


def build_layout_c(root: Path) -> dict[str, Any]:
    """Layout C: repo_path is a submodule checkout (.git file pointing into .git/modules/...)."""
    git_env = get_safe_git_env(root)
    sub_origin = root / "sub_origin"
    subprocess.run(["git", "init", str(sub_origin)], check=True, capture_output=True, env=git_env)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=str(sub_origin),
        check=True,
        capture_output=True,
        env=git_env,
    )

    super_repo = root / "super_c"
    subprocess.run(["git", "init", str(super_repo)], check=True, capture_output=True, env=git_env)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=str(super_repo),
        check=True,
        capture_output=True,
        env=git_env,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            str(sub_origin),
            "sub",
        ],
        cwd=str(super_repo),
        check=True,
        capture_output=True,
        env=git_env,
    )
    subprocess.run(
        ["git", "commit", "-m", "add submodule"],
        cwd=str(super_repo),
        check=True,
        capture_output=True,
        env=git_env,
    )

    repo = super_repo / "sub"
    store_path, job_dir, job_id = _setup_job(root, repo)
    work = job_dir / "work"
    subprocess.run(
        ["git", "worktree", "add", str(work), "-b", "wt_c"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        env=git_env,
    )
    (work / ".git").unlink()

    repo_git_file = repo / ".git"
    modules_dir = super_repo / ".git" / "modules" / "sub"
    entry_dir = modules_dir / "worktrees" / "work"

    return {
        "repo_path": repo,
        "job_dir": job_dir,
        "job_id": job_id,
        "store_path": store_path,
        "targets": {
            "repo_path_dot_git_file": repo_git_file,
            "common_worktrees_entry_gitdir": entry_dir / "gitdir",
            "common_worktrees_entry_commondir": entry_dir / "commondir",
            "common_worktrees_entry_locked": entry_dir / "locked",
            "common_worktrees_entry_dir": entry_dir,
        },
    }


def build_layout_d(root: Path) -> dict[str, Any]:
    """Layout D: repo_path is a bare-repo-backed worktree."""
    git_env = get_safe_git_env(root)
    bare_repo = root / "bare_d.git"
    subprocess.run(
        ["git", "init", "--bare", str(bare_repo)], check=True, capture_output=True, env=git_env
    )
    tree = subprocess.run(
        ["git", "write-tree"],
        cwd=str(bare_repo),
        check=True,
        capture_output=True,
        text=True,
        env=git_env,
    ).stdout.strip()
    commit = subprocess.run(
        ["git", "commit-tree", tree, "-m", "init"],
        cwd=str(bare_repo),
        check=True,
        capture_output=True,
        text=True,
        env=git_env,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/heads/main", commit],
        cwd=str(bare_repo),
        check=True,
        capture_output=True,
        env=git_env,
    )

    repo = root / "repo_d"
    subprocess.run(
        ["git", "worktree", "add", str(repo), "main"],
        cwd=str(bare_repo),
        check=True,
        capture_output=True,
        env=git_env,
    )

    store_path, job_dir, job_id = _setup_job(root, repo)
    work = job_dir / "work"
    subprocess.run(
        ["git", "worktree", "add", str(work), "-b", "wt_d"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        env=git_env,
    )
    (work / ".git").unlink()

    repo_git_file = repo / ".git"
    repo_gitdir = bare_repo / "worktrees" / "repo_d"
    entry_dir = bare_repo / "worktrees" / "work"

    return {
        "repo_path": repo,
        "job_dir": job_dir,
        "job_id": job_id,
        "store_path": store_path,
        "targets": {
            "repo_path_dot_git_file": repo_git_file,
            "gitdir_commondir": repo_gitdir / "commondir",
            "common_worktrees_entry_gitdir": entry_dir / "gitdir",
            "common_worktrees_entry_commondir": entry_dir / "commondir",
            "common_worktrees_entry_locked": entry_dir / "locked",
            "common_worktrees_entry_dir": entry_dir,
        },
    }


def build_layout_e(root: Path) -> dict[str, Any]:
    """Layout E: relative paths and moved repo (git worktree repair situation)."""
    git_env = get_safe_git_env(root)
    repo = root / "repo_e"
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, env=git_env)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        env=git_env,
    )

    store_path, job_dir, job_id = _setup_job(root, repo)
    work = job_dir / "work"
    subprocess.run(
        ["git", "worktree", "add", str(work), "-b", "wt_e"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        env=git_env,
    )
    (work / ".git").unlink()

    entry_dir = repo / ".git" / "worktrees" / "work"
    gitdir_file = entry_dir / "gitdir"
    rel_target = os.path.relpath(str(work / ".git"), str(entry_dir))
    gitdir_file.write_text(rel_target + "\n", encoding="utf-8")

    return {
        "repo_path": repo,
        "job_dir": job_dir,
        "job_id": job_id,
        "store_path": store_path,
        "targets": {
            "common_worktrees_entry_gitdir": entry_dir / "gitdir",
            "common_worktrees_entry_commondir": entry_dir / "commondir",
            "common_worktrees_entry_locked": entry_dir / "locked",
            "common_worktrees_entry_dir": entry_dir,
        },
    }


def build_layout_f(root: Path) -> dict[str, Any]:
    """Layout F: locked worktree and prunable one (worktree deleted, entry locked)."""
    git_env = get_safe_git_env(root)
    repo = root / "repo_f"
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, env=git_env)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        env=git_env,
    )

    store_path, job_dir, job_id = _setup_job(root, repo)
    work = job_dir / "work"
    subprocess.run(
        ["git", "worktree", "add", str(work), "-b", "wt_f"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        env=git_env,
    )
    subprocess.run(
        [
            "git",
            "worktree",
            "lock",
            str(work),
            "--reason",
            "locked_for_differential_test",
        ],
        cwd=str(repo),
        check=True,
        capture_output=True,
        env=git_env,
    )
    _make_tree_writable(work)
    shutil.rmtree(work)

    entry_dir = repo / ".git" / "worktrees" / "work"
    return {
        "repo_path": repo,
        "job_dir": job_dir,
        "job_id": job_id,
        "store_path": store_path,
        "targets": {
            "common_worktrees_entry_gitdir": entry_dir / "gitdir",
            "common_worktrees_entry_commondir": entry_dir / "commondir",
            "common_worktrees_entry_locked": entry_dir / "locked",
            "common_worktrees_entry_dir": entry_dir,
        },
    }


LAYOUT_BUILDERS: dict[str, Callable[[Path], dict[str, Any]]] = {
    "Layout A": build_layout_a,
    "Layout B": build_layout_b,
    "Layout C": build_layout_c,
    "Layout D": build_layout_d,
    "Layout E": build_layout_e,
    "Layout F": build_layout_f,
}


def _generate_case_params() -> list[tuple[str, str, str]]:
    """Generate the static list of 390 (layout, target, mutation) test parameter tuples."""
    layout_targets: dict[str, list[tuple[str, bool]]] = {
        "Layout A": [
            ("common_worktrees_entry_gitdir", False),
            ("common_worktrees_entry_commondir", False),
            ("common_worktrees_entry_dir", True),
        ],
        "Layout B": [
            ("repo_path_dot_git_file", False),
            ("gitdir_commondir", False),
            ("common_worktrees_entry_gitdir", False),
            ("common_worktrees_entry_commondir", False),
            ("common_worktrees_entry_dir", True),
        ],
        "Layout C": [
            ("repo_path_dot_git_file", False),
            ("common_worktrees_entry_gitdir", False),
            ("common_worktrees_entry_commondir", False),
            ("common_worktrees_entry_dir", True),
        ],
        "Layout D": [
            ("repo_path_dot_git_file", False),
            ("gitdir_commondir", False),
            ("common_worktrees_entry_gitdir", False),
            ("common_worktrees_entry_commondir", False),
            ("common_worktrees_entry_dir", True),
        ],
        "Layout E": [
            ("common_worktrees_entry_gitdir", False),
            ("common_worktrees_entry_commondir", False),
            ("common_worktrees_entry_dir", True),
        ],
        "Layout F": [
            ("common_worktrees_entry_gitdir", False),
            ("common_worktrees_entry_commondir", False),
            ("common_worktrees_entry_locked", False),
            ("common_worktrees_entry_dir", True),
        ],
    }
    cases: list[tuple[str, str, str]] = []
    for l_name, targets in layout_targets.items():
        for t_name, is_dir in targets:
            mut_names = list(DIR_MUTATIONS.keys()) if is_dir else list(FILE_MUTATIONS.keys())
            for m_name in mut_names:
                cases.append((l_name, t_name, m_name))
    return cases


CASE_PARAMS = _generate_case_params()


# --------------------------------------------------------------------------- #
# Baseline Tests: Guard validity of layout setups
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("layout_name", list(LAYOUT_BUILDERS.keys()))
def test_layout_baseline(layout_name: str, tmp_path: Path) -> None:
    """Verify that unmutated layout registers worktree and cleaner keeps the row."""
    builder = LAYOUT_BUILDERS[layout_name]
    try:
        info = builder(tmp_path / "baseline")
    except subprocess.CalledProcessError as exc:
        pytest.skip(f"Installed git cannot build {layout_name}: {exc}")

    repo_path = info["repo_path"]
    job_dir = info["job_dir"]
    work_path = job_dir / "work"
    store_path = info["store_path"]
    targets = info["targets"]
    entry_dirs = (
        [targets["common_worktrees_entry_dir"]] if "common_worktrees_entry_dir" in targets else []
    )

    git_works_base, git_reg_base, git_out = check_git_registered(repo_path, work_path, entry_dirs)
    assert git_works_base and git_reg_base, (
        f"Base git ground truth failed for {layout_name}: works={git_works_base}, "
        f"reg={git_reg_base}, git_out={git_out}"
    )

    base_status, base_reason = _check_worktree_status(job_dir, {"repo_path": str(repo_path)})
    assert base_status is True, (
        f"Base cleaner status is not registered for {layout_name}: {base_reason}"
    )
    assert "worktree registered" in (base_reason or "")

    res = run_cleanup(store_path, project=str(repo_path), dry_run=True, older_than_days=0.0)
    cand_ids = [c["job_id"] for c in res.get("candidates", [])]
    assert info["job_id"] not in cand_ids, (
        f"Base unmutated {layout_name} job unexpectedly classified as candidate!"
    )

    cli_crashed, cli_err = _run_cli_dry_run(store_path, repo_path)
    assert not cli_crashed, f"Base unmutated CLI crashed for {layout_name}: {cli_err}"


# --------------------------------------------------------------------------- #
# Parametrized Differential Layout Mutation Tests (390 cases)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "layout_name,target_name,mut_name",
    CASE_PARAMS,
    ids=[f"{lay_n}-{tgt_n}-{mut_n}" for lay_n, tgt_n, mut_n in CASE_PARAMS],
)
def test_differential_layout_mutation(
    layout_name: str, target_name: str, mut_name: str, tmp_path: Path
) -> None:
    """Compare agy-mcp cleaner decision against real git ground truth under mutation."""
    if ("chmod" in mut_name) and os.geteuid() == 0:
        pytest.skip("chmod 000 has no effect as root")

    builder = LAYOUT_BUILDERS[layout_name]
    layout_root = tmp_path / "layout"
    try:
        info = builder(layout_root)
    except subprocess.CalledProcessError as exc:
        pytest.skip(f"Installed git cannot build {layout_name}: {exc}")

    repo_path = info["repo_path"]
    job_dir = info["job_dir"]
    work_path = job_dir / "work"
    store_path = info["store_path"]
    job_id = info["job_id"]
    targets = info["targets"]
    entry_dirs = (
        [targets["common_worktrees_entry_dir"]] if "common_worktrees_entry_dir" in targets else []
    )

    target_path = targets.get(target_name)
    assert target_path is not None and target_path.exists(), (
        f"Target {target_name} does not exist in {layout_name}"
    )

    is_dir = target_path.is_dir()
    mut_func = DIR_MUTATIONS[mut_name] if is_dir else FILE_MUTATIONS[mut_name]

    restore_cb: Optional[Callable[[], None]] = None
    mut_desc = ""
    try:
        try:
            restore_cb, mut_desc = mut_func(target_path)
        except Exception as exc:
            pytest.fail(f"Mutation setup failed for {layout_name} {target_name} {mut_name}: {exc}")

        git_works_after, git_reg_after, git_out = check_git_registered(
            repo_path, work_path, entry_dirs
        )

        cleaner_crashed = False
        cleaner_exc_str = ""
        res: dict[str, Any] = {}
        try:
            res = run_cleanup(
                store_path,
                project=str(repo_path),
                dry_run=True,
                older_than_days=0.0,
            )
        except Exception:
            cleaner_crashed = True
            cleaner_exc_str = f"run_cleanup traceback: {traceback.format_exc()}"

        cli_crashed, cli_err = _run_cli_dry_run(store_path, repo_path)

        if cleaner_crashed or cli_crashed:
            pytest.fail(
                f"CRASH: cleaner crashed on {layout_name} {target_name} {mut_name} "
                f"({mut_desc}): cleaner_exc={cleaner_exc_str} cli_err={cli_err}"
            )

        cand_ids = [c["job_id"] for c in res.get("candidates", [])]
        is_candidate = job_id in cand_ids

        if is_candidate:
            cleaner_reasons = next(
                (c.get("reasons", []) for c in res["candidates"] if c["job_id"] == job_id),
                [],
            )
            if git_reg_after or not git_works_after:
                pytest.fail(
                    f"FAIL-OPEN: cleaner decided candidate ({cleaner_reasons}), "
                    f"but git works={git_works_after}, registered={git_reg_after} ({mut_desc})"
                )
    finally:
        if restore_cb is not None:
            try:
                restore_cb()
            except Exception:
                pass
        _make_tree_writable(tmp_path)


# --------------------------------------------------------------------------- #
# Job.json Mutation Tests: Own job.json and kept neighbor job.json
# --------------------------------------------------------------------------- #

JSON_MUTATION_NAMES = [
    "bom",
    "nul",
    "truncation",
    "extra_bytes",
    "duplicate_keys",
    "huge_size",
    "symlink",
    "chmod_000",
    "directory_instead_of_file",
]


def _apply_json_mutation(
    jf: Path,
    orig_bytes: bytes,
    mut_name: str,
    dup_id: str,
) -> tuple[Callable[[], None], str]:
    is_dir = False

    if mut_name == "bom":
        jf.write_bytes(b"\xef\xbb\xbf" + orig_bytes)
        desc = "prepended UTF-8 BOM"
    elif mut_name == "nul":
        mid = min(10, len(orig_bytes))
        jf.write_bytes(orig_bytes[:mid] + b"\x00" + orig_bytes[mid:])
        desc = "inserted NUL byte"
    elif mut_name == "truncation":
        jf.write_bytes(orig_bytes[: len(orig_bytes) // 2])
        desc = "truncated JSON half"
    elif mut_name == "extra_bytes":
        jf.write_bytes(orig_bytes + b"\nextra trailing garbage\n")
        desc = "extra bytes after JSON"
    elif mut_name == "duplicate_keys":
        jf.write_bytes(
            b'{"job_id": "' + dup_id.encode("utf-8") + b'", "job_id": "other"}\n'
        )
        desc = "duplicate keys in JSON object"
    elif mut_name == "huge_size":
        jf.write_bytes(orig_bytes + b" " * (1024 * 1024 + 500))
        desc = "oversized job.json exceeding 1 MiB limit"
    elif mut_name == "symlink":
        bak = jf.with_name("bak_job.json")
        bak.write_bytes(orig_bytes)
        jf.unlink()
        jf.symlink_to(bak.name)
        desc = "swapped job.json for symlink"
    elif mut_name == "chmod_000":
        os.chmod(jf, 0o000)
        desc = "chmod 000 on job.json"
    elif mut_name == "directory_instead_of_file":
        is_dir = True
        jf.unlink()
        jf.mkdir()
        desc = "swapped job.json for directory"
    else:
        raise ValueError(f"Unknown json mutation: {mut_name}")

    def restore() -> None:
        try:
            os.chmod(jf, 0o700)
        except OSError:
            pass
        if is_dir and jf.is_dir():
            _make_tree_writable(jf)
            shutil.rmtree(jf)
        elif jf.exists() or jf.is_symlink():
            jf.unlink()
        bak_file = jf.with_name("bak_job.json")
        if bak_file.exists():
            bak_file.unlink()
        jf.write_bytes(orig_bytes)

    return restore, desc


@pytest.mark.parametrize("mut_name", JSON_MUTATION_NAMES)
def test_own_job_json_mutation(mut_name: str, tmp_path: Path) -> None:
    """Every mutation of candidate's own job.json must keep the candidate fail-closed."""
    if mut_name == "chmod_000" and os.geteuid() == 0:
        pytest.skip("chmod 000 has no effect as root")

    store_path = tmp_path / "store"
    store_jobs = store_path / "jobs"
    store_jobs.mkdir(parents=True, exist_ok=True)

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    job_id = "11111111-1111-1111-1111-111111111111"
    job_dir = store_jobs / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    orig_data = {
        "job_id": job_id,
        "title": "candidate job",
        "state": "done",
        "repo_path": str(repo),
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2020-01-01T00:00:00Z",
    }
    jf = job_dir / "job.json"
    orig_bytes = json.dumps(orig_data, indent=2).encode("utf-8")
    jf.write_bytes(orig_bytes)

    restore_cb: Optional[Callable[[], None]] = None
    try:
        restore_cb, desc = _apply_json_mutation(jf, orig_bytes, mut_name, dup_id="other_id")
        res = run_cleanup(store_path, project=str(repo), dry_run=True, older_than_days=0.0)
        cand_ids = [c["job_id"] for c in res.get("candidates", [])]
        assert job_id not in cand_ids, (
            f"FAIL-OPEN: candidate's own job.json was corrupted ({desc}) "
            "but cleaner marked it candidate!"
        )

        cli_crashed, cli_err = _run_cli_dry_run(store_path, repo)
        assert not cli_crashed, f"CLI dry run crashed on own job.json mutation ({desc}): {cli_err}"
    finally:
        if restore_cb is not None:
            try:
                restore_cb()
            except Exception:
                pass
        _make_tree_writable(tmp_path)


@pytest.mark.parametrize("mut_name", JSON_MUTATION_NAMES)
def test_neighbor_job_json_mutation(mut_name: str, tmp_path: Path) -> None:
    """Every mutation of kept neighbor that makes it untrustworthy must block cleanup store-wide."""
    if mut_name == "chmod_000" and os.geteuid() == 0:
        pytest.skip("chmod 000 has no effect as root")

    store_path = tmp_path / "store"
    store_jobs = store_path / "jobs"
    store_jobs.mkdir(parents=True, exist_ok=True)

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    job1_id = "11111111-1111-1111-1111-111111111111"
    job1_dir = store_jobs / job1_id
    job1_dir.mkdir(parents=True, exist_ok=True)
    job1_data = {
        "job_id": job1_id,
        "title": "candidate job",
        "state": "done",
        "repo_path": str(repo),
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2020-01-01T00:00:00Z",
    }
    (job1_dir / "job.json").write_text(json.dumps(job1_data, indent=2), encoding="utf-8")

    job2_id = "22222222-2222-2222-2222-222222222222"
    job2_dir = store_jobs / job2_id
    job2_dir.mkdir(parents=True, exist_ok=True)
    job2_data = {
        "job_id": job2_id,
        "title": "kept neighbor job",
        "state": "running",
        "repo_path": str(repo),
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2020-01-01T00:00:00Z",
    }
    job2_file = job2_dir / "job.json"
    orig_j2_bytes = json.dumps(job2_data, indent=2).encode("utf-8")
    job2_file.write_bytes(orig_j2_bytes)

    restore_cb: Optional[Callable[[], None]] = None
    try:
        restore_cb, desc = _apply_json_mutation(job2_file, orig_j2_bytes, mut_name, dup_id=job2_id)
        res = run_cleanup(store_path, project=str(repo), dry_run=True, older_than_days=0.0)
        cand_ids = [c["job_id"] for c in res.get("candidates", [])]
        warnings = res.get("warnings", [])

        is_job1_candidate = job1_id in cand_ids
        assert not is_job1_candidate, (
            f"FAIL-OPEN: untrustworthy neighbor ({desc}) failed to block cleanup; "
            f"candidate {job1_id} was marked deletable"
        )
        assert len(warnings) > 0, (
            f"FAIL-OPEN: untrustworthy neighbor ({desc}) did not emit a warning"
        )

        cli_crashed, cli_err = _run_cli_dry_run(store_path, repo)
        assert not cli_crashed, (
            f"CLI dry run crashed on neighbor job.json mutation ({desc}): {cli_err}"
        )
    finally:
        if restore_cb is not None:
            try:
                restore_cb()
            except Exception:
                pass
        _make_tree_writable(tmp_path)
