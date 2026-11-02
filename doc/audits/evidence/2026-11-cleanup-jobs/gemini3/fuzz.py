#!/usr/bin/env python3
"""Seeded parallel differential fuzzer for agy_mcp.cleanup_jobs against real git oracle.

Reviews feat/cleanup-jobs @ 25c2098 for FAIL-OPEN, CRASH, HANG, and wrong exit codes.
"""

from __future__ import annotations

import argparse
import io
import json
import multiprocessing as mp
import os
import random
import shutil
import stat
import subprocess
import sys
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

REPO_ROOT = Path("~/Documents/GITHUB PROJECTS/agy-mcp")
PYTHON_EXE = REPO_ROOT / ".venv" / "bin" / "python"
SCRATCH_BASE = Path("~/agy-review/gemini3/scratch")
FINDINGS_PATH = Path("~/agy-review/gemini3/FINDINGS.md")

LAYOUTS = [
    "normal",
    "linked_wt",
    "bare",
    "bare_plus",
    "submodule",
    "custom_wt",
    "two_jobs",
]


def _make_writable(path: Path) -> None:
    """Recursively reset permissions to allow deletion of chmod 000 files."""
    if not path.exists() and not path.is_symlink():
        return
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
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


def safe_cleanup(path: Path) -> None:
    """Safely remove a directory tree even with restricted permissions or fifos."""
    _make_writable(path)
    shutil.rmtree(path, ignore_errors=True)


def get_git_env(home_dir: Path) -> dict[str, str]:
    """Isolated git environment."""
    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_AUTHOR_NAME"] = "Fuzz Author"
    env["GIT_AUTHOR_EMAIL"] = "fuzz@example.com"
    env["GIT_COMMITTER_NAME"] = "Fuzz Committer"
    env["GIT_COMMITTER_EMAIL"] = "fuzz@example.com"
    return env


def run_git(repo: Path, args: list[str], env: dict[str, str], timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    """Execute git command with strict timeout."""
    cmd = ["git", "-C", str(repo)] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        errors="replace",
    )


def write_job_record(
    store_dir: Path,
    job_id: str,
    repo_path: Path,
    *,
    worktree_path: Optional[Path] = None,
    state: str = "done",
    pid: Optional[int] = None,
    days_old: float = 10.0,
    title: str = "Fuzz test job",
) -> Path:
    """Create a store/jobs/<job_id> record with job.json."""
    job_dir = store_dir / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    ts = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    data: dict[str, Any] = {
        "job_id": job_id,
        "repo_path": str(repo_path),
        "state": state,
        "pid": pid,
        "created_at": ts,
        "updated_at": ts,
        "title": title,
    }
    if worktree_path is not None:
        data["worktree_path"] = str(worktree_path)
    (job_dir / "job.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    return job_dir


# --------------------------------------------------------------------------- #
# Layout builders
# --------------------------------------------------------------------------- #

@dataclass
class LayoutInfo:
    repo_path: Path
    job_id: str
    job_dir: Path
    store_dir: Path
    work_path: Path
    custom_wt_path: Optional[Path] = None
    extra_job_ids: list[str] = None

    def __post_init__(self):
        if self.extra_job_ids is None:
            self.extra_job_ids = []


def build_layout(layout: str, base_dir: Path, env: dict[str, str], rng: random.Random) -> LayoutInfo:
    store_dir = base_dir / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "jobs").mkdir(parents=True, exist_ok=True)

    job_id = str(uuid.UUID(int=rng.getrandbits(128), version=4))
    # 40% chance: worktree is pruned / unlinked at job level (so cleaner must rely on repo inspection)
    unlink_work_git = (rng.random() < 0.4)

    if layout == "normal":
        repo_path = base_dir / "repo"
        subprocess.run(["git", "init", "-q", str(repo_path)], env=env, check=True, timeout=10.0)
        run_git(repo_path, ["commit", "--allow-empty", "-m", "init", "-q"], env=env)
        job_dir = write_job_record(store_dir, job_id, repo_path)
        work_path = job_dir / "work"
        run_git(repo_path, ["worktree", "add", "-q", str(work_path)], env=env)
        if unlink_work_git:
            (work_path / ".git").unlink(missing_ok=True)
            # In half of unlinked cases, also prune from git
            if rng.random() < 0.5:
                run_git(repo_path, ["worktree", "prune"], env=env)
        return LayoutInfo(repo_path, job_id, job_dir, store_dir, work_path)

    elif layout == "linked_wt":
        main_repo = base_dir / "main_repo"
        subprocess.run(["git", "init", "-q", str(main_repo)], env=env, check=True, timeout=10.0)
        run_git(main_repo, ["commit", "--allow-empty", "-m", "init", "-q"], env=env)
        repo_path = base_dir / "linked_repo"
        run_git(main_repo, ["worktree", "add", "-q", str(repo_path)], env=env)
        job_dir = write_job_record(store_dir, job_id, repo_path)
        work_path = job_dir / "work"
        run_git(repo_path, ["worktree", "add", "-q", str(work_path)], env=env)
        if unlink_work_git:
            (work_path / ".git").unlink(missing_ok=True)
            if rng.random() < 0.5:
                run_git(repo_path, ["worktree", "prune"], env=env)
        return LayoutInfo(repo_path, job_id, job_dir, store_dir, work_path)

    elif layout == "bare":
        repo_path = base_dir / "bare.git"
        subprocess.run(["git", "init", "--bare", "-q", str(repo_path)], env=env, check=True, timeout=10.0)
        tree = subprocess.run(["git", "--git-dir", str(repo_path), "write-tree"], env=env, capture_output=True, text=True, check=True, timeout=10.0).stdout.strip()
        commit = subprocess.run(["git", "--git-dir", str(repo_path), "commit-tree", tree, "-m", "init"], env=env, capture_output=True, text=True, check=True, timeout=10.0).stdout.strip()
        subprocess.run(["git", "--git-dir", str(repo_path), "update-ref", "refs/heads/main", commit], env=env, check=True, timeout=10.0)
        subprocess.run(["git", "--git-dir", str(repo_path), "symbolic-ref", "HEAD", "refs/heads/main"], env=env, check=True, timeout=10.0)
        job_dir = write_job_record(store_dir, job_id, repo_path)
        work_path = job_dir / "work"
        run_git(repo_path, ["worktree", "add", "-q", str(work_path)], env=env)
        if unlink_work_git:
            (work_path / ".git").unlink(missing_ok=True)
            if rng.random() < 0.5:
                run_git(repo_path, ["worktree", "prune"], env=env)
        return LayoutInfo(repo_path, job_id, job_dir, store_dir, work_path)

    elif layout == "bare_plus":
        repo_path = base_dir / "bare_plus.git"
        subprocess.run(["git", "init", "--bare", "-q", str(repo_path)], env=env, check=True, timeout=10.0)
        tree = subprocess.run(["git", "--git-dir", str(repo_path), "write-tree"], env=env, capture_output=True, text=True, check=True, timeout=10.0).stdout.strip()
        commit = subprocess.run(["git", "--git-dir", str(repo_path), "commit-tree", tree, "-m", "init"], env=env, capture_output=True, text=True, check=True, timeout=10.0).stdout.strip()
        subprocess.run(["git", "--git-dir", str(repo_path), "update-ref", "refs/heads/main", commit], env=env, check=True, timeout=10.0)
        subprocess.run(["git", "--git-dir", str(repo_path), "symbolic-ref", "HEAD", "refs/heads/main"], env=env, check=True, timeout=10.0)
        job_dir = write_job_record(store_dir, job_id, repo_path)
        work_path = job_dir / "work"
        run_git(repo_path, ["worktree", "add", "-q", str(work_path)], env=env)
        if rng.random() < 0.5:
            fake_dotgit = repo_path / ".git"
            fake_dotgit.mkdir(exist_ok=True)
            (fake_dotgit / "config").write_text("[core]\n", encoding="utf-8")
        if unlink_work_git:
            (work_path / ".git").unlink(missing_ok=True)
            if rng.random() < 0.5:
                run_git(repo_path, ["worktree", "prune"], env=env)
        return LayoutInfo(repo_path, job_id, job_dir, store_dir, work_path)

    elif layout == "submodule":
        sub_src = base_dir / "sub_src"
        subprocess.run(["git", "init", "-q", str(sub_src)], env=env, check=True, timeout=10.0)
        run_git(sub_src, ["commit", "--allow-empty", "-m", "init", "-q"], env=env)
        super_repo = base_dir / "super"
        subprocess.run(["git", "init", "-q", str(super_repo)], env=env, check=True, timeout=10.0)
        run_git(super_repo, ["commit", "--allow-empty", "-m", "init", "-q"], env=env)
        subprocess.run(["git", "-C", str(super_repo), "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub_src), "sub"], env=env, check=True, timeout=10.0)
        run_git(super_repo, ["commit", "-m", "sub", "-q"], env=env)
        repo_path = super_repo / "sub"
        job_dir = write_job_record(store_dir, job_id, repo_path)
        work_path = job_dir / "work"
        run_git(repo_path, ["worktree", "add", "-q", str(work_path)], env=env)
        if unlink_work_git:
            (work_path / ".git").unlink(missing_ok=True)
            if rng.random() < 0.5:
                run_git(repo_path, ["worktree", "prune"], env=env)
        return LayoutInfo(repo_path, job_id, job_dir, store_dir, work_path)

    elif layout == "custom_wt":
        repo_path = base_dir / "repo"
        subprocess.run(["git", "init", "-q", str(repo_path)], env=env, check=True, timeout=10.0)
        run_git(repo_path, ["commit", "--allow-empty", "-m", "init", "-q"], env=env)
        custom_wt = base_dir / "custom_worktree"
        run_git(repo_path, ["worktree", "add", "-q", str(custom_wt)], env=env)
        job_dir = write_job_record(store_dir, job_id, repo_path, worktree_path=custom_wt)
        work_path = custom_wt
        if unlink_work_git:
            (custom_wt / ".git").unlink(missing_ok=True)
            if rng.random() < 0.5:
                run_git(repo_path, ["worktree", "prune"], env=env)
        return LayoutInfo(repo_path, job_id, job_dir, store_dir, work_path, custom_wt_path=custom_wt)

    elif layout == "two_jobs":
        repo_path = base_dir / "repo"
        subprocess.run(["git", "init", "-q", str(repo_path)], env=env, check=True, timeout=10.0)
        run_git(repo_path, ["commit", "--allow-empty", "-m", "init", "-q"], env=env)
        # Job 1
        job_dir = write_job_record(store_dir, job_id, repo_path)
        work_path = job_dir / "work"
        run_git(repo_path, ["worktree", "add", "-q", str(work_path)], env=env)
        # Job 2
        job_id_2 = str(uuid.UUID(int=rng.getrandbits(128), version=4))
        job_dir_2 = write_job_record(store_dir, job_id_2, repo_path)
        work_path_2 = job_dir_2 / "work"
        run_git(repo_path, ["worktree", "add", "-q", str(work_path_2)], env=env)
        if unlink_work_git:
            (work_path / ".git").unlink(missing_ok=True)
        return LayoutInfo(repo_path, job_id, job_dir, store_dir, work_path, extra_job_ids=[job_id_2])

    else:
        raise ValueError(f"Unknown layout: {layout}")


# --------------------------------------------------------------------------- #
# Mutation Pool
# --------------------------------------------------------------------------- #

def collect_mutation_targets(info: LayoutInfo) -> list[Path]:
    """Find all files and directories in git metadata and job directory eligible for mutation."""
    targets: set[Path] = set()

    def scan(p: Path, max_depth: int = 4):
        if not p.exists() and not p.is_symlink():
            return
        targets.add(p)
        if max_depth <= 0:
            return
        try:
            if p.is_dir() and not p.is_symlink():
                for c in p.iterdir():
                    scan(c, max_depth - 1)
        except OSError:
            pass

    # Git metadata
    scan(info.repo_path)
    # Common gitdir if linked worktree
    dotgit = info.repo_path / ".git"
    if dotgit.is_file():
        try:
            txt = dotgit.read_text(encoding="utf-8", errors="replace").strip()
            if txt.startswith("gitdir:"):
                target = Path(txt[len("gitdir:"):].strip())
                if not target.is_absolute():
                    target = info.repo_path / target
                scan(target.resolve())
                commondir = target / "commondir"
                if commondir.is_file():
                    comm_txt = commondir.read_text(encoding="utf-8", errors="replace").strip()
                    comm_path = Path(comm_txt) if os.path.isabs(comm_txt) else target / comm_txt
                    scan(comm_path.resolve())
        except Exception:
            pass

    # Job dir
    scan(info.job_dir)

    # Custom worktree
    if info.custom_wt_path:
        scan(info.custom_wt_path)

    # Do not mutate the repo root directory itself (so repo path exists), but anything inside it
    targets.discard(info.repo_path)
    targets.discard(info.store_dir)
    targets.discard(info.store_dir / "jobs")
    return sorted(list(targets))


def apply_mutation(target: Path, mutation_type: str, all_targets: list[Path], rng: random.Random) -> str:
    """Apply a mutation to target path. Returns description."""
    try:
        if target.is_fifo():
            return f"skip_fifo({target.name})"

        if mutation_type == "delete":
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
            return f"delete({target.name})"

        elif mutation_type == "replace_with_dir":
            if target.is_dir() and not target.is_symlink():
                pass
            else:
                target.unlink(missing_ok=True)
                target.mkdir(parents=True, exist_ok=True)
            return f"replace_with_dir({target.name})"

        elif mutation_type == "replace_with_file":
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
            target.write_bytes(b"dummy replacement file content\n")
            return f"replace_with_file({target.name})"

        elif mutation_type == "replace_with_dangling_symlink":
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
            target.symlink_to("/nonexistent/target/path/12345")
            return f"replace_with_dangling_symlink({target.name})"

        elif mutation_type == "replace_with_symlink_to_valid":
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
            target.symlink_to(Path("/dev/null"))
            return f"replace_with_symlink_to_valid({target.name})"

        elif mutation_type == "replace_with_fifo":
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
            try:
                os.mkfifo(target)
                return f"replace_with_fifo({target.name})"
            except OSError:
                target.write_bytes(b"fifo_fallback")
                return f"replace_with_file_fallback({target.name})"

        elif mutation_type == "truncate":
            if target.is_file() and not target.is_symlink():
                target.write_bytes(b"")
                return f"truncate({target.name})"

        elif mutation_type == "append_garbage":
            if target.is_file() and not target.is_symlink():
                data = target.read_bytes()
                target.write_bytes(data + b"\n# GARBAGE_INJECTION\nextra line 99999\n")
                return f"append_garbage({target.name})"

        elif mutation_type == "prepend_bom":
            if target.is_file() and not target.is_symlink():
                data = target.read_bytes()
                target.write_bytes(b"\xef\xbb\xbf" + data)
                return f"prepend_bom({target.name})"

        elif mutation_type == "crlf":
            if target.is_file() and not target.is_symlink():
                data = target.read_bytes()
                target.write_bytes(data.replace(b"\n", b"\r\n"))
                return f"crlf({target.name})"

        elif mutation_type == "trailing_spaces_newlines":
            if target.is_file() and not target.is_symlink():
                data = target.read_bytes()
                target.write_bytes(data + b"   \r\n\t  \n\n")
                return f"trailing_spaces_newlines({target.name})"

        elif mutation_type == "nul_byte":
            if target.is_file() and not target.is_symlink():
                data = target.read_bytes()
                idx = rng.randint(0, len(data))
                target.write_bytes(data[:idx] + b"\x00" + data[idx:])
                return f"nul_byte({target.name})"

        elif mutation_type == "non_utf8_bytes":
            if target.is_file() and not target.is_symlink():
                data = target.read_bytes()
                idx = rng.randint(0, len(data))
                target.write_bytes(data[:idx] + b"\xff\xfe\x80\x90" + data[idx:])
                return f"non_utf8_bytes({target.name})"

        elif mutation_type == "rewrite_rel_abs":
            if target.is_file() and not target.is_symlink():
                txt = target.read_text(encoding="utf-8", errors="replace").strip()
                if txt.startswith("gitdir:"):
                    raw = txt[len("gitdir:"):].strip()
                    p = Path(raw)
                    if p.is_absolute():
                        new_txt = f"gitdir: ./{p.name}\n"
                    else:
                        new_txt = f"gitdir: {(target.parent / p).resolve()}\n"
                    target.write_text(new_txt, encoding="utf-8")
                    return f"rewrite_rel_abs({target.name})"
                elif target.name == "commondir":
                    p = Path(txt)
                    if p.is_absolute():
                        new_txt = f"./{p.name}\n"
                    else:
                        new_txt = f"{(target.parent / p).resolve()}\n"
                    target.write_text(new_txt, encoding="utf-8")
                    return f"rewrite_rel_abs({target.name})"

        elif mutation_type == "swap_two_files":
            if target.is_file() and not target.is_symlink():
                other_files = [p for p in all_targets if p.is_file() and not p.is_symlink() and not p.is_fifo() and p != target]
                if other_files:
                    other = rng.choice(other_files)
                    d1 = target.read_bytes()
                    d2 = other.read_bytes()
                    target.write_bytes(d2)
                    other.write_bytes(d1)
                    return f"swap_two_files({target.name} <-> {other.name})"

        elif mutation_type == "point_gitdir_elsewhere":
            if target.name == "gitdir" and target.is_file() and not target.is_symlink():
                other_dir = target.parent.parent / "fake_wt_dir"
                target.write_text(f"{other_dir}\n", encoding="utf-8")
                return f"point_gitdir_elsewhere({target.name} -> {other_dir.name})"

        elif mutation_type == "duplicate_worktrees_entry":
            if "worktrees" in target.parts and target.is_dir() and target.parent.name == "worktrees":
                dup_dir = target.parent / f"{target.name}_dup"
                shutil.copytree(target, dup_dir, dirs_exist_ok=True)
                return f"duplicate_worktrees_entry({target.name} -> {dup_dir.name})"

        elif mutation_type == "rename_worktrees_entry":
            if "worktrees" in target.parts and target.is_dir() and target.parent.name == "worktrees":
                renamed = target.parent / f"{target.name}_renamed"
                target.rename(renamed)
                return f"rename_worktrees_entry({target.name} -> {renamed.name})"

        elif mutation_type == "chmod_000":
            os.chmod(target, 0o000)
            return f"chmod_000({target.name})"

        elif mutation_type == "job_json_duplicate_key":
            if target.name == "job.json" and target.is_file() and not target.is_symlink():
                txt = target.read_text(encoding="utf-8", errors="replace")
                mutated = txt.replace('"state": "done"', '"state": "done",\n  "state": "done"')
                target.write_text(mutated, encoding="utf-8")
                return f"job_json_duplicate_key({target.name})"

        elif mutation_type == "job_json_type_change":
            if target.name == "job.json" and target.is_file() and not target.is_symlink():
                try:
                    data = json.loads(target.read_text(encoding="utf-8"))
                    field = rng.choice(["state", "repo_path", "pid", "worktree_path", "created_at"])
                    val_type = rng.choice(["null", "int", "list", "dict", "bool"])
                    vals = {"null": None, "int": 12345, "list": ["a", "b"], "dict": {"k": "v"}, "bool": True}
                    data[field] = vals[val_type]
                    target.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    return f"job_json_type_change({field}->{val_type})"
                except Exception:
                    pass

        elif mutation_type == "job_json_unicode_lookalike":
            if target.name == "job.json" and target.is_file() and not target.is_symlink():
                txt = target.read_text(encoding="utf-8", errors="replace")
                mutated = txt.replace("a", "\u0430")
                target.write_text(mutated, encoding="utf-8")
                return f"job_json_unicode_lookalike({target.name})"

        elif mutation_type == "job_json_huge_value":
            if target.name == "job.json" and target.is_file() and not target.is_symlink():
                try:
                    data = json.loads(target.read_text(encoding="utf-8"))
                    data["title"] = "X" * (1024 * 1024 + 10)  # > 1 MiB
                    target.write_text(json.dumps(data), encoding="utf-8")
                    return f"job_json_huge_value({target.name})"
                except Exception:
                    pass

    except Exception as exc:
        return f"mutation_err({mutation_type}:{exc})"

    return f"mutation_noop({mutation_type})"


MUTATION_CHOICES = [
    "delete",
    "replace_with_dir",
    "replace_with_file",
    "replace_with_dangling_symlink",
    "replace_with_symlink_to_valid",
    "replace_with_fifo",
    "truncate",
    "append_garbage",
    "prepend_bom",
    "crlf",
    "trailing_spaces_newlines",
    "nul_byte",
    "non_utf8_bytes",
    "rewrite_rel_abs",
    "swap_two_files",
    "point_gitdir_elsewhere",
    "duplicate_worktrees_entry",
    "rename_worktrees_entry",
    "chmod_000",
    "job_json_duplicate_key",
    "job_json_type_change",
    "job_json_unicode_lookalike",
    "job_json_huge_value",
]


# --------------------------------------------------------------------------- #
# Oracle and Differential Evaluation
# --------------------------------------------------------------------------- #

@dataclass
class OracleResult:
    git_works: bool
    git_registered: bool
    git_details: str
    cleaner_rc: int
    cleaner_out: str
    cleaner_err: str
    cleaner_crashed: bool
    cleaner_candidate: bool
    verdict: str  # PASS, FAIL-OPEN, CRASH, HANG, WRONG-EXIT-CODE
    reason: str


def evaluate_oracle(
    info: LayoutInfo,
    env: dict[str, str],
    timeout: float = 10.0,
) -> OracleResult:
    # 1. Run git status & git worktree list
    git_works = True
    git_registered = False
    git_details = ""

    try:
        status_res = run_git(info.repo_path, ["status", "--porcelain"], env=env, timeout=5.0)
        wt_res = run_git(info.repo_path, ["worktree", "list", "--porcelain"], env=env, timeout=5.0)
        if status_res.returncode != 0 or wt_res.returncode != 0:
            git_works = False
            git_details = f"status_rc={status_res.returncode} wt_rc={wt_res.returncode} stderr={status_res.stderr.strip()} {wt_res.stderr.strip()}"
        else:
            git_details = wt_res.stdout
            # Check if job worktree is listed
            work_res = str(info.work_path.resolve(strict=False))
            for line in wt_res.stdout.splitlines():
                if line.startswith("worktree "):
                    wt_raw = line[len("worktree "):].strip()
                    try:
                        wt_p = str(Path(wt_raw).resolve(strict=False))
                        if wt_p == work_res or wt_p == str(info.job_dir.resolve(strict=False)):
                            git_registered = True
                            break
                    except Exception:
                        pass
    except subprocess.TimeoutExpired:
        git_works = False
        git_details = "git command timed out"
    except Exception as exc:
        git_works = False
        git_details = f"git error: {exc}"

    # 2. Run cleaner CLI in dry-run mode (project mode)
    cmd = [
        str(PYTHON_EXE),
        "-m",
        "agy_mcp.cleanup_jobs",
        "--store",
        str(info.store_dir),
        "--project",
        str(info.repo_path),
        "--older-than-days",
        "0",
        "--json",
    ]

    cleaner_crashed = False
    cleaner_candidate = False
    cleaner_rc = -1
    cleaner_out = ""
    cleaner_err = ""

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            errors="replace",
        )
        cleaner_rc = proc.returncode
        cleaner_out = proc.stdout
        cleaner_err = proc.stderr

        if cleaner_rc not in (0, 1, 2) or "Traceback" in cleaner_err:
            cleaner_crashed = True

        if cleaner_rc == 0 and cleaner_out.strip():
            try:
                report = json.loads(cleaner_out)
                cand_ids = [c["job_id"] for c in report.get("candidates", [])]
                if info.job_id in cand_ids:
                    cleaner_candidate = True
            except json.JSONDecodeError:
                cleaner_crashed = True

    except subprocess.TimeoutExpired:
        return OracleResult(
            git_works=git_works,
            git_registered=git_registered,
            git_details=git_details,
            cleaner_rc=-1,
            cleaner_out="",
            cleaner_err="TimeoutExpired",
            cleaner_crashed=False,
            cleaner_candidate=False,
            verdict="HANG",
            reason="Cleaner timed out (>10s)",
        )
    except Exception as exc:
        cleaner_crashed = True
        cleaner_err = str(exc)

    # Also test --job mode if project mode didn't crash
    if not cleaner_crashed and cleaner_rc == 0:
        cmd_job = [
            str(PYTHON_EXE),
            "-m",
            "agy_mcp.cleanup_jobs",
            "--store",
            str(info.store_dir),
            "--job",
            str(info.job_id),
            "--older-than-days",
            "0",
            "--json",
        ]
        try:
            proc_job = subprocess.run(
                cmd_job,
                capture_output=True,
                text=True,
                env=env,
                timeout=timeout,
                errors="replace",
            )
            if proc_job.returncode not in (0, 1, 2) or "Traceback" in proc_job.stderr:
                return OracleResult(
                    git_works=git_works,
                    git_registered=git_registered,
                    git_details=git_details,
                    cleaner_rc=proc_job.returncode,
                    cleaner_out=proc_job.stdout,
                    cleaner_err=proc_job.stderr,
                    cleaner_crashed=True,
                    cleaner_candidate=cleaner_candidate,
                    verdict="CRASH",
                    reason=f"--job mode crashed with rc={proc_job.returncode} or traceback",
                )
            if proc_job.returncode == 0 and proc_job.stdout.strip():
                try:
                    rep_job = json.loads(proc_job.stdout)
                    job_cands = [c["job_id"] for c in rep_job.get("candidates", [])]
                    if info.job_id in job_cands:
                        cleaner_candidate = True
                except json.JSONDecodeError:
                    return OracleResult(
                        git_works=git_works,
                        git_registered=git_registered,
                        git_details=git_details,
                        cleaner_rc=proc_job.returncode,
                        cleaner_out=proc_job.stdout,
                        cleaner_err=proc_job.stderr,
                        cleaner_crashed=True,
                        cleaner_candidate=cleaner_candidate,
                        verdict="CRASH",
                        reason="--job mode output invalid JSON",
                    )
        except subprocess.TimeoutExpired:
            return OracleResult(
                git_works=git_works,
                git_registered=git_registered,
                git_details=git_details,
                cleaner_rc=-1,
                cleaner_out="",
                cleaner_err="TimeoutExpired",
                cleaner_crashed=False,
                cleaner_candidate=False,
                verdict="HANG",
                reason="Cleaner --job mode timed out (>10s)",
            )

    # 3. Verdict
    if cleaner_crashed:
        return OracleResult(
            git_works=git_works,
            git_registered=git_registered,
            git_details=git_details,
            cleaner_rc=cleaner_rc,
            cleaner_out=cleaner_out,
            cleaner_err=cleaner_err,
            cleaner_crashed=True,
            cleaner_candidate=cleaner_candidate,
            verdict="CRASH",
            reason=f"Cleaner crashed with rc={cleaner_rc} or traceback in stderr",
        )

    if cleaner_candidate:
        if not git_works:
            return OracleResult(
                git_works=git_works,
                git_registered=git_registered,
                git_details=git_details,
                cleaner_rc=cleaner_rc,
                cleaner_out=cleaner_out,
                cleaner_err=cleaner_err,
                cleaner_crashed=False,
                cleaner_candidate=True,
                verdict="FAIL-OPEN",
                reason=f"Cleaner says DELETABLE but git cannot read repo ({git_details})",
            )
        if git_registered:
            return OracleResult(
                git_works=git_works,
                git_registered=git_registered,
                git_details=git_details,
                cleaner_rc=cleaner_rc,
                cleaner_out=cleaner_out,
                cleaner_err=cleaner_err,
                cleaner_crashed=False,
                cleaner_candidate=True,
                verdict="FAIL-OPEN",
                reason="Cleaner says DELETABLE but git still lists worktree as registered",
            )

    return OracleResult(
        git_works=git_works,
        git_registered=git_registered,
        git_details=git_details,
        cleaner_rc=cleaner_rc,
        cleaner_out=cleaner_out,
        cleaner_err=cleaner_err,
        cleaner_crashed=False,
        cleaner_candidate=cleaner_candidate,
        verdict="PASS",
        reason="OK (fail-closed or correctly handled)",
    )


def _worker_task(args: tuple[int, int, str, Path]) -> Optional[dict[str, Any]]:
    iteration, seed, layout_name, base_scratch = args
    rng = random.Random(seed)
    iter_dir = base_scratch / f"worker_{os.getpid()}_iter_{iteration}"
    safe_cleanup(iter_dir)
    iter_dir.mkdir(parents=True, exist_ok=True)
    home_dir = iter_dir / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    env = get_git_env(home_dir)

    try:
        info = build_layout(layout_name, iter_dir, env, rng)
        targets = collect_mutation_targets(info)
        if not targets:
            safe_cleanup(iter_dir)
            return None

        num_mutations = min(rng.randint(1, 4), len(targets))
        chosen_targets = rng.sample(targets, num_mutations)
        applied_muts = []
        for target in chosen_targets:
            m_type = rng.choice(MUTATION_CHOICES)
            desc = apply_mutation(target, m_type, targets, rng)
            try:
                rel = str(target.relative_to(iter_dir))
            except Exception:
                rel = str(target)
            applied_muts.append(f"{rel}:{desc}")

        res = evaluate_oracle(info, env)
        if res.verdict != "PASS":
            return {
                "iteration": iteration,
                "seed": seed,
                "layout": layout_name,
                "mutations": applied_muts,
                "verdict": res.verdict,
                "reason": res.reason,
                "git_works": res.git_works,
                "git_registered": res.git_registered,
                "git_details": res.git_details,
                "cleaner_rc": res.cleaner_rc,
                "cleaner_out": res.cleaner_out,
                "cleaner_err": res.cleaner_err,
            }
        return None
    except Exception as exc:
        return {
            "iteration": iteration,
            "seed": seed,
            "layout": layout_name,
            "mutations": ["exception_in_worker"],
            "verdict": "CRASH",
            "reason": f"Worker crashed: {exc}\n{traceback.format_exc()}",
            "git_works": False,
            "git_registered": False,
            "git_details": "",
            "cleaner_rc": -1,
            "cleaner_out": "",
            "cleaner_err": str(exc),
        }
    finally:
        safe_cleanup(iter_dir)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=2100)
    parser.add_argument("--seed", type=int, default=12345)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    SCRATCH_BASE.mkdir(parents=True, exist_ok=True)
    print(f"Starting parallel differential fuzzer: {args.iterations} iterations across {len(LAYOUTS)} layouts, {args.workers} workers, seed {args.seed}")
    master_rng = random.Random(args.seed)

    layout_counts = {l: 0 for l in LAYOUTS}
    tasks = []
    for i in range(args.iterations):
        layout = LAYOUTS[i % len(LAYOUTS)]
        layout_counts[layout] += 1
        iter_seed = master_rng.randint(0, 2**31 - 1)
        tasks.append((i, iter_seed, layout, SCRATCH_BASE))

    findings: list[dict[str, Any]] = []
    t_start = time.time()

    with mp.Pool(processes=args.workers) as pool:
        for idx, res in enumerate(pool.imap_unordered(_worker_task, tasks, chunksize=10)):
            if res is not None:
                findings.append(res)
                print(f"\n[!] FINDING at iter {res['iteration']} ({res['layout']}): {res['verdict']} - {res['reason']}")

            if (idx + 1) % 100 == 0 or idx + 1 == args.iterations:
                elapsed = time.time() - t_start
                rate = (idx + 1) / elapsed
                print(f"[{idx + 1}/{args.iterations}] ({rate:.1f} iter/s) Findings: {len(findings)}", end="\r")

    print(f"\nCompleted {args.iterations} iterations in {time.time() - t_start:.2f}s")
    print(f"Counts per layout: {layout_counts}")
    print(f"Total findings flagged: {len(findings)}")

    if findings:
        for f in findings:
            print(f"- {f['verdict']} [{f['layout']}]: {f['reason']}")
            print(f"  Seed: {f['seed']}, Mutations: {f['mutations']}")

        with open(FINDINGS_PATH, "a", encoding="utf-8") as out:
            out.write("\n\n## Differential Fuzzer Run Findings\n\n")
            for f in findings:
                out.write(f"### {f['verdict']}: Iteration {f['iteration']} ({f['layout']})\n")
                out.write(f"- **Seed:** {f['seed']}\n")
                out.write(f"- **Mutations:** {f['mutations']}\n")
                out.write(f"- **Reason:** {f['reason']}\n")
                out.write(f"- **Cleaner output:** {f['cleaner_out'][:200]}\n")
                out.write(f"- **Cleaner stderr:** {f['cleaner_err'][:200]}\n\n")


if __name__ == "__main__":
    main()
