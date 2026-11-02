"""Hardened git execution and the wrapper-owned commit pipeline (SPEC §10, §11).

Owns every ``git`` invocation, the junk/sensitive staging filters, commit
retries, checkpoint/safety-net commits and the read-only git data used by
review. It must never let ``agy`` run modifying git commands, must never stage
``.agy/**`` or blacklisted files, and must never rewrite history or push.
"""

import os
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from agy_mcp.util import env_allowlist, truncate_tail

# Sensitive file blacklist patterns per SPEC section 11 & D6
SENSITIVE_FILENAME_EXACT = {".netrc", ".npmrc", ".pypirc"}
SENSITIVE_EXTENSIONS = (".pem", ".key", ".p12", ".pfx", ".jks", ".ppk")
SENSITIVE_PREFIXES = ("id_rsa", "id_ed25519")
SENSITIVE_ALLOWED_EXCEPTIONS = {".env.example", ".env.sample"}
POLICY_FILENAMES = {".gitignore", ".gitattributes", ".gitmodules"}


class GitError(RuntimeError):
    """Raised when a git command exits with non-zero status."""

    def __init__(self, returncode: int, stderr: str, stdout: str, args: list[str]):
        msg = f"Git command failed (rc={returncode}): {' '.join(args)}\n{stderr}"
        super().__init__(msg)
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout
        self.cmd_args = args

    @property
    def args_list(self) -> list[str]:
        """Return the argv that failed (compatibility alias)."""
        return self.cmd_args

    def __str__(self) -> str:
        cmd_str = " ".join(self.cmd_args)
        return f"Git command failed (rc={self.returncode}): {cmd_str}\n{self.stderr}"


@dataclass
class GitResult:
    """Result of a git command execution."""

    returncode: int
    stdout: str
    stderr: str
    args: list[str]


@dataclass
class StagingResult:
    """Outcome of worktree staging."""

    staged_files: list[str]
    skipped_files: list[Any]
    partial_commit: bool
    policy_files: list[str] = field(default_factory=list)
    sensitive_partial_commit: bool = False


class SkippedFile(dict):
    """Represents a skipped file entry with path and reason, backward-compatible with str."""

    def __init__(self, path: str, reason: str):
        super().__init__(path=path, reason=reason)

    @property
    def path(self) -> str:
        """Return the skipped file's repo-relative path."""
        return self["path"]

    @property
    def reason(self) -> str:
        """Return why the file was skipped (sensitive/build_artifact/policy_file)."""
        return self["reason"]

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, str):
            return self["path"] == other
        if isinstance(other, dict):
            return (
                self.get("path") == other.get("path")
                and self.get("reason") == other.get("reason")
            )
        return super().__eq__(other)

    def __hash__(self) -> int:
        return hash(self["path"])

    def __str__(self) -> str:
        return self["path"]

    def __repr__(self) -> str:
        return f"SkippedFile(path={self['path']!r}, reason={self['reason']!r})"


def merge_skipped_files(
    existing: list[Any],
    new_skipped: list[Any],
    max_items: int = 50,
) -> list[Any]:
    """Merge and deduplicate skipped file entries preserving path and reason up to max_items."""
    seen_paths = set()
    result = []
    for item in (existing or []) + (new_skipped or []):
        if isinstance(item, dict):
            p = item.get("path")
            reason = item.get("reason", "unknown")
            sf = item if isinstance(item, SkippedFile) else SkippedFile(str(p), str(reason))
        else:
            p = str(item)
            sf = SkippedFile(p, "skipped")
        if p and p not in seen_paths and len(result) < max_items:
            seen_paths.add(p)
            result.append(sf)
    return result


# Default junk exclusion pathspecs and patterns per SPEC section 11, D54 & F10
DEFAULT_JUNK_PATHSPECS = [
    ":(exclude,glob)**/__pycache__/**",
    ":(exclude,glob)**/__pycache__",
    ":(exclude,glob)**/*.pyc",
    ":(exclude,glob)**/*.pyo",
    ":(exclude,glob)**/.pytest_cache/**",
    ":(exclude,glob)**/.pytest_cache",
    ":(exclude,glob)**/.ruff_cache/**",
    ":(exclude,glob)**/.ruff_cache",
    ":(exclude,glob)**/.mypy_cache/**",
    ":(exclude,glob)**/.mypy_cache",
    ":(exclude,glob)**/.tox/**",
    ":(exclude,glob)**/.tox",
    ":(exclude,glob)**/*.egg-info/**",
    ":(exclude,glob)**/*.egg-info",
    ":(exclude,glob)**/.coverage",
    ":(exclude,glob)**/.coverage.*",
    ":(exclude,glob)**/htmlcov/**",
    ":(exclude,glob)**/htmlcov",
    ":(exclude,glob)**/junit.xml",
    ":(exclude,glob)**/coverage.xml",
    ":(exclude,glob)**/.hypothesis/**",
    ":(exclude,glob)**/.hypothesis",
    ":(exclude,glob)**/.nox/**",
    ":(exclude,glob)**/.nox",
    ":(exclude,glob)**/target/**",
    ":(exclude,glob)**/target",
    ":(exclude,glob)**/node_modules/**",
    ":(exclude,glob)**/node_modules",
]


def is_build_artifact(rel_path: str) -> bool:
    """Check if relative path matches junk exclusion list per SPEC 11, D54 & F10.

    Matches Python caches, coverage/test artifacts (.coverage, htmlcov, junit.xml),
    target/, and node_modules/.
    """
    clean_path = rel_path.strip()
    while clean_path.startswith("./"):
        clean_path = clean_path[2:]
    clean_path = clean_path.lstrip("/")
    if not clean_path:
        return False

    p = Path(clean_path)
    parts_lower = [part.lower() for part in p.parts]
    filename_lower = p.name.lower()

    junk_dirs = {
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".tox",
        ".nox",
        ".hypothesis",
        "htmlcov",
        "target",
        "node_modules",
    }
    if any(part in junk_dirs or part.endswith(".egg-info") for part in parts_lower):
        return True

    if filename_lower.endswith((".pyc", ".pyo")):
        return True

    if (
        filename_lower in {".coverage", "coverage.xml", "junit.xml"}
        or filename_lower.startswith(".coverage.")
    ):
        return True

    return False


def is_sensitive_path(rel_path: str) -> bool:
    """Check if a relative path matches the SPEC section 11 sensitive file blacklist."""
    clean_path = rel_path.strip()
    while clean_path.startswith("./"):
        clean_path = clean_path[2:]
    clean_path = clean_path.lstrip("/")
    if not clean_path:
        return False

    path_obj = Path(clean_path)
    lower_path = clean_path.lower()
    parts_lower = [p.lower() for p in path_obj.parts]
    filename_lower = path_obj.name.lower()

    # Allowed exceptions take precedence
    if filename_lower in SENSITIVE_ALLOWED_EXCEPTIONS:
        return False
    if filename_lower in POLICY_FILENAMES:
        return False

    # Instruction files: AGENTS.md, GEMINI.md anywhere in path per SPEC 12 & F7
    if filename_lower in {"agents.md", "gemini.md"}:
        return True

    # .agents directory contents anywhere in path per SPEC 12 & F7
    if any(p == ".agents" for p in parts_lower):
        return True

    # .agy directory contents anywhere in path
    if any(p == ".agy" for p in parts_lower):
        return True

    # .ssh/** and .gnupg/** anywhere in path
    if any(p in (".ssh", ".gnupg") for p in parts_lower):
        return True

    # .docker/config.json
    if lower_path == ".docker/config.json" or lower_path.endswith("/.docker/config.json"):
        return True

    # .env and .env.* (including nested paths like a/b/.env, A/B/.ENV)
    if filename_lower == ".env" or filename_lower.startswith(".env."):
        return True

    # Sensitive key prefixes (id_rsa, id_ed25519)
    if any(filename_lower.startswith(pfx) for pfx in SENSITIVE_PREFIXES):
        return True

    # Sensitive extensions
    if any(filename_lower.endswith(ext) for ext in SENSITIVE_EXTENSIONS):
        return True

    # Exact credential files
    if filename_lower in SENSITIVE_FILENAME_EXACT:
        return True

    return False


def get_hardened_git_env(
    worktree_tmp_dir: Optional[Path] = None,
    home: Optional[Path] = None,
    extra_env: Optional[dict[str, str]] = None,
    disable_hooks: bool = True,
) -> dict[str, str]:
    """Construct hardened environment with GIT_CONFIG_* overrides per SPEC section 10.

    ``disable_hooks=False`` is used only by the wrapper-owned commit path, which
    must let the repository's commit-msg/pre-commit hooks run (D80).
    """
    tmp = worktree_tmp_dir or Path("/tmp")
    env = env_allowlist(job_tmp_dir=tmp, home=home, disable_hooks=disable_hooks)
    if extra_env:
        env.update(extra_env)
    return env


def run_git(
    args: list[str],
    cwd: Path,
    *,
    env: Optional[dict[str, str]] = None,
    timeout: float = 60.0,
    check: bool = False,
    max_output_bytes: int = 1024 * 1024,
) -> GitResult:
    """Execute a git command using argument list and hardened environment."""
    resolved_cwd = Path(cwd).resolve(strict=True)
    git_env = env or get_hardened_git_env(worktree_tmp_dir=resolved_cwd.parent / "tmp")

    cmd = ["git", "-C", str(resolved_cwd)] + args
    try:
        proc = subprocess.run(
            cmd,
            env=git_env,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise GitError(
            -1, f"Git command timed out after {timeout}s: {' '.join(cmd)}", "", cmd
        ) from e

    stdout_str = truncate_tail(
        proc.stdout.decode("utf-8", errors="replace"), max_output_bytes
    )
    stderr_str = truncate_tail(
        proc.stderr.decode("utf-8", errors="replace"), max_output_bytes
    )

    result = GitResult(
        returncode=proc.returncode,
        stdout=stdout_str,
        stderr=stderr_str,
        args=cmd,
    )

    if check and proc.returncode != 0:
        raise GitError(proc.returncode, stderr_str, stdout_str, cmd)

    return result


def _resolve_index_lock_path(worktree_path: Path) -> Optional[Path]:
    """Locate the active index.lock file for a worktree or linked repository."""
    dot_git = worktree_path / ".git"
    if dot_git.is_file():
        # Linked worktree: .git is a text pointer file "gitdir: <path>"
        try:
            content = dot_git.read_text(encoding="utf-8").strip()
            if content.startswith("gitdir:"):
                gitdir = Path(content.split("gitdir:", 1)[1].strip())
                if not gitdir.is_absolute():
                    gitdir = (worktree_path / gitdir).resolve()
                return gitdir / "index.lock"
        except Exception:
            return None
    elif dot_git.is_dir():
        return dot_git / "index.lock"
    return None


def execute_with_index_lock_retry(
    action: Callable[[], GitResult],
    worktree_path: Path,
    max_attempts: int = 10,
    initial_backoff: float = 0.1,
    max_backoff: float = 5.0,
    sleep_func: Callable[[float], None] = time.sleep,
    is_process_alive_func: Optional[Callable[[], bool]] = None,
    now_func: Callable[[], float] = time.time,
) -> GitResult:
    """Execute a git action retrying if index.lock is contested per SPEC section 11 & F10."""
    backoff = initial_backoff
    last_res: Optional[GitResult] = None

    for attempt in range(max_attempts):
        res = action()
        last_res = res
        if res.returncode == 0:
            return res

        # Check for index.lock contention in stderr
        is_lock_error = "index.lock" in res.stderr or "Unable to create" in res.stderr
        if not is_lock_error:
            return res

        # Inspect index.lock on disk
        lock_path = _resolve_index_lock_path(worktree_path)
        if lock_path and lock_path.exists():
            try:
                mtime = lock_path.stat().st_mtime
                age_seconds = now_func() - mtime
                # Never delete locks younger than 5 minutes (300s)
                # A fresh lock may belong to a legitimate concurrent git; only a
                # stale lock plus a confirmed-dead agent is safe to unlink.
                if age_seconds > 300:
                    process_alive = (
                        is_process_alive_func() if is_process_alive_func is not None else True
                    )
                    if not process_alive:
                        lock_path.unlink(missing_ok=True)
            except OSError:
                pass

        if attempt < max_attempts - 1:
            sleep_func(backoff)
            backoff = min(backoff * 2.0, max_backoff)

    return last_res if last_res is not None else GitResult(-1, "", "Exhausted retry attempts", [])


def parse_porcelain_z(raw_bytes: bytes | str) -> list[tuple[str, str, Optional[str]]]:
    """Parse git status --porcelain=v1 -z raw output into (status_code, path, orig_path)."""
    if isinstance(raw_bytes, str):
        raw_bytes = raw_bytes.encode("utf-8", errors="surrogateescape")
    entries: list[tuple[str, str, Optional[str]]] = []
    if not raw_bytes:
        return entries

    tokens = raw_bytes.split(b"\x00")
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not token:
            i += 1
            continue

        status_code = token[:2].decode("ascii", errors="replace")
        path_str = token[3:].decode("utf-8", errors="replace")

        # For renames or copies (R or C), next token is the orig_path
        orig_path = None
        if status_code.startswith(("R", "C")):
            if i + 1 < len(tokens):
                orig_path = tokens[i + 1].decode("utf-8", errors="replace")
                i += 1

        entries.append((status_code, path_str, orig_path))
        i += 1

    return entries


def stage_changes(
    worktree_path: Path,
    enable_pathspecs: bool = True,
    enable_python_filter: bool = True,
    is_process_alive_func: Optional[Callable[[], bool]] = None,
) -> StagingResult:
    """Stage modified/untracked files with pathspec and Python filters per SPEC 11 & D54."""
    resolved_path = Path(worktree_path).resolve(strict=True)

    # 1. Initial git add with pathspec exclusion for .agy and junk retrying on index.lock
    # The .agy tree is wrapper metadata; excluding it here (and again in Python)
    # guarantees the agent's commit message/staging area never enters the index.
    add_args = ["add", "-A", "--", ".", ":(exclude,top).agy"]
    if enable_pathspecs:
        add_args.extend(DEFAULT_JUNK_PATHSPECS)

    def do_add() -> GitResult:
        """Run the staging ``git add``; retries are owned by the caller."""
        return run_git(add_args, resolved_path, check=False)

    execute_with_index_lock_retry(
        do_add,
        resolved_path,
        is_process_alive_func=is_process_alive_func,
    )

    # 2. Inspect status via porcelain -z to enforce sensitive blacklist and junk filter
    status_res = run_git(["status", "--porcelain=v1", "-z"], resolved_path, timeout=30.0)
    if status_res.returncode != 0:
        # Abort staging and unstage any staged files to prevent leaking secrets into index
        # Fail closed: a status failure leaves the index in an unknown state, so
        # reset before raising rather than committing whatever was staged.
        run_git(["reset"], resolved_path, check=False)
        raise GitError(status_res.returncode, status_res.stderr, status_res.stdout, status_res.args)

    status_entries = parse_porcelain_z(status_res.stdout)
    staged_files: list[str] = []
    skipped_files: list[Any] = []
    policy_files: list[str] = []
    partial_commit = False
    sensitive_partial_commit = False

    for status_code, path_str, orig_path in status_entries:
        index_status = status_code[0]
        target_path = path_str

        # Check if file is a policy file (.gitignore, .gitattributes, .gitmodules)
        target_name = Path(target_path).name.lower()
        orig_name = Path(orig_path).name.lower() if orig_path else None
        is_policy = (target_name in POLICY_FILENAMES) or (orig_name in POLICY_FILENAMES)

        if is_policy:
            policy_files.append(target_path)
            if len(skipped_files) < 50:
                skipped_files.append(SkippedFile(target_path, "policy_file"))
            partial_commit = True
            is_tracked = (
                run_git(["cat-file", "-e", f"HEAD:{target_path}"], resolved_path).returncode
                == 0
            )
            if is_tracked:
                run_git(["reset", "-q", "HEAD", "--", target_path], resolved_path)
            else:
                run_git(["rm", "--cached", "--quiet", "-f", "--", target_path], resolved_path)
            continue

        is_sensitive = is_sensitive_path(target_path) or (
            orig_path and is_sensitive_path(orig_path)
        )

        if is_sensitive:
            if len(skipped_files) < 50:
                skipped_files.append(SkippedFile(target_path, "sensitive"))

            # Check if this file was already tracked in the source repository
            is_tracked = (
                run_git(["cat-file", "-e", f"HEAD:{target_path}"], resolved_path).returncode
                == 0
            )
            if not is_tracked and orig_path:
                is_tracked = (
                    run_git(["cat-file", "-e", f"HEAD:{orig_path}"], resolved_path).returncode
                    == 0
                )

            if is_tracked:
                # If a tracked file matches blacklist and was modified, flag partial commit
                partial_commit = True
                sensitive_partial_commit = True
                # Reset staged changes for tracked file back to HEAD
                run_git(["reset", "-q", "HEAD", "--", target_path], resolved_path)
            else:
                # Untracked sensitive file: remove from index
                run_git(["rm", "--cached", "--quiet", "-f", "--", target_path], resolved_path)
            continue

        is_junk = enable_python_filter and (
            is_build_artifact(target_path) or (orig_path and is_build_artifact(orig_path))
        )
        if is_junk:
            is_tracked_in_head = (
                run_git(["cat-file", "-e", f"HEAD:{target_path}"], resolved_path).returncode == 0
            )
            if is_tracked_in_head:
                # Junk present from the start (repo tracked .pyc) is untouched by filter
                # (never rewrite history). Keep it staged if it really changed so
                # the repository's own tracked state is preserved.
                if index_status not in (" ", "?", "!"):
                    staged_files.append(target_path)
            else:
                # Untracked junk file: unstage if staged and add to skipped_files
                if len(skipped_files) < 50:
                    skipped_files.append(SkippedFile(target_path, "build_artifact"))
                if index_status not in (" ", "?", "!"):
                    run_git(["rm", "--cached", "--quiet", "-f", "--", target_path], resolved_path)
            continue

        if index_status not in (" ", "?", "!"):
            staged_files.append(target_path)

    # De-duplicate skipped_files preserving order
    seen_paths = set()
    unique_skipped = []
    for sf in skipped_files:
        p = sf.get("path") if isinstance(sf, dict) else str(sf)
        if p not in seen_paths:
            seen_paths.add(p)
            unique_skipped.append(sf)

    return StagingResult(
        staged_files=sorted(list(set(staged_files))),
        skipped_files=unique_skipped[:50],
        partial_commit=partial_commit,
        policy_files=sorted(list(set(policy_files))),
        sensitive_partial_commit=sensitive_partial_commit,
    )


def commit_with_retry(
    worktree_path: Path,
    msg_file: Path,
    max_attempts: int = 10,
    backoff_seconds: float = 0.1,
    sleep_func: Callable[[float], None] = time.sleep,
    is_process_alive_func: Optional[Callable[[], bool]] = None,
    now_func: Callable[[], float] = time.time,
    run_hooks: bool = True,
) -> str:
    """Commit staged changes using -F <msg_file>, retrying on index.lock collisions.

    ``run_hooks`` defaults to True: the repository's commit-msg/pre-commit hooks
    run exactly as they would for a manual ``git commit``. The wrapper never
    passes ``--no-verify`` and no longer overrides ``core.hooksPath`` for this
    command (D80). A hook rejection surfaces as :class:`GitError` with the hook's
    own stderr, which callers turn into an actionable needs_review message.
    """
    resolved_path = Path(worktree_path).resolve(strict=True)
    resolved_msg = Path(msg_file).resolve(strict=True)
    commit_env = get_hardened_git_env(
        worktree_tmp_dir=resolved_path.parent / "tmp",
        disable_hooks=not run_hooks,
    )

    def do_commit() -> GitResult:
        """Run ``git commit -F`` with the hardening flags repeated inline."""
        return run_git(
            [
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.pager=cat",
                "commit",
                "-F",
                str(resolved_msg),
            ],
            resolved_path,
            env=commit_env,
        )

    res = execute_with_index_lock_retry(
        action=do_commit,
        worktree_path=resolved_path,
        max_attempts=max_attempts,
        initial_backoff=backoff_seconds,
        sleep_func=sleep_func,
        is_process_alive_func=is_process_alive_func,
        now_func=now_func,
    )

    if res.returncode != 0:
        if "nothing to commit" in res.stdout or "nothing to commit" in res.stderr:
            return None
        raise GitError(res.returncode, res.stderr, res.stdout, res.args)

    # Return new HEAD commit hash
    sha_res = run_git(["rev-parse", "HEAD"], resolved_path, check=True)
    return sha_res.stdout.strip()


def is_worktree_dirty(worktree_path: Path) -> bool:
    """Check whether worktree has uncommitted modifications outside .agy and junk."""
    resolved_path = Path(worktree_path).resolve(strict=True)
    status_res = run_git(
        [
            "status",
            "--porcelain=v1",
            "-z",
            "--",
            ".",
            ":(exclude,top).agy",
        ]
        + DEFAULT_JUNK_PATHSPECS,
        resolved_path,
        timeout=30.0,
    )
    if status_res.returncode != 0:
        raise GitError(status_res.returncode, status_res.stderr, status_res.stdout, status_res.args)
    entries = parse_porcelain_z(status_res.stdout)
    real_entries = []
    for code, path_str, orig_path in entries:
        if not is_build_artifact(path_str) and not (orig_path and is_build_artifact(orig_path)):
            real_entries.append((code, path_str, orig_path))
    return len(real_entries) > 0


MAX_WRAPPER_SUBJECT_CHARS = 72
WRAPPER_SUBJECT_FALLBACK = "agy: automated commit"


def sanitize_commit_subject(subject: str, max_length: int = MAX_WRAPPER_SUBJECT_CHARS) -> str:
    """Turn arbitrary wrapper/agent text into a subject every commit hook accepts.

    Rules: one line, printable ASCII only, no control characters, no trailing
    period or whitespace, at most ``max_length`` characters (72 by default).
    Non-ASCII letters are transliterated (``café`` -> ``cafe``) and anything that
    cannot be transliterated is dropped rather than replaced with a byte that a
    hook or a terminal may reject.
    """
    text = (subject or "").replace("\r\n", "\n").replace("\r", "\n")
    first_line = text.split("\n", 1)[0]
    try:
        ascii_line = unicodedata.normalize("NFKD", first_line).encode("ascii", "ignore").decode()
    except (UnicodeError, ValueError):
        ascii_line = ""
    cleaned = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in ascii_line)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # A trailing period is the most common commit-msg hook rejection; the
    # separator characters are equally pointless at the end of a subject.
    cleaned = cleaned.rstrip(" .,;:-").strip()
    if not cleaned:
        return WRAPPER_SUBJECT_FALLBACK
    if len(cleaned) > max_length:
        cut = cleaned[:max_length].rstrip()
        if " " in cut and len(cut) > 24:
            cut = cut[: cut.rfind(" ")].rstrip()
        cut = cut.rstrip(" .,;:-").strip()
        cleaned = cut or cleaned[:max_length].rstrip(" .,;:-")
    return cleaned


def sanitize_commit_body(body: str) -> str:
    """Normalize a wrapper-generated commit body: LF endings, no control characters."""
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n")
    # The body may legitimately contain non-ASCII file names; only control
    # characters are removed (the ASCII rule applies to the subject only).
    cleaned = "".join(
        ch if ch == "\n" or (ord(ch) >= 32 and ord(ch) != 127) else " " for ch in text
    )
    return "\n".join(line.rstrip() for line in cleaned.split("\n")).strip()


def build_wrapper_commit_text(subject: str, body: str = "") -> str:
    """Build a wrapper-owned commit message that satisfies the watcher validator.

    Wrapper messages are the checkpoint and safety-net commits. They must always
    pass ``watcher.validate_commit_message`` and any hook that enforces its rules,
    so the subject goes through :func:`sanitize_commit_subject` and the body is
    normalized as well.
    """
    safe_subject = sanitize_commit_subject(subject)
    safe_body = sanitize_commit_body(body)
    if safe_body:
        return f"{safe_subject}\n\n{safe_body}\n"
    return f"{safe_subject}\n"


def commit_message_problem(text: str) -> Optional[str]:
    """Return the first wrapper-message rule violation, mirroring the watcher.

    Kept local to gitops so a generated message can be checked before ``git
    commit`` without importing the commit watcher (which imports this module).
    """
    if not text.strip():
        return "Commit message is empty"
    lines = text.splitlines()
    first_line = lines[0] if lines else ""
    if not first_line:
        return "Commit message subject is empty"
    if len(first_line) > MAX_WRAPPER_SUBJECT_CHARS:
        return (
            f"First line of commit message exceeds {MAX_WRAPPER_SUBJECT_CHARS} "
            f"characters ({len(first_line)} chars)"
        )
    if not first_line.isascii():
        return "Commit message subject is not ASCII"
    normalized = text.replace("\r\n", "\n")
    for ch in normalized:
        code = ord(ch)
        if (code < 32 and code not in (9, 10)) or code == 127:
            return f"Commit message contains illegal control character (ASCII {code})"
    return None


def write_wrapper_commit_msg(
    target_dir: Path,
    prefix: str,
    subject: str,
    body: str = "",
) -> Path:
    """Write a sanitized wrapper commit message, falling back if it ever fails.

    Returns the temporary message path; the caller is responsible for unlinking.
    The fallback is unreachable with the current sanitizer and exists so a future
    edit can never hand an invalid subject to ``git commit``.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    tmp_msg = target_dir / f"{prefix}.{os.getpid()}"
    text = build_wrapper_commit_text(subject, body)
    problem = commit_message_problem(text)
    if problem:
        text = build_wrapper_commit_text(WRAPPER_SUBJECT_FALLBACK, body)
    tmp_msg.write_text(text, encoding="utf-8")
    return tmp_msg


def checkpoint_commit(
    worktree_path: Path,
    changed_paths: Optional[list[str]] = None,
    msg_file_dir: Optional[Path] = None,
    is_process_alive_func: Optional[Callable[[], bool]] = None,
    job: Optional[Any] = None,
) -> Optional[str]:
    """Execute an automatic fallback checkpoint commit if worktree is dirty."""
    if not is_worktree_dirty(worktree_path):
        return None

    staging = stage_changes(worktree_path, is_process_alive_func=is_process_alive_func)
    if job is not None and staging.skipped_files:
        job.skipped_files = merge_skipped_files(job.skipped_files, staging.skipped_files)
    if not staging.staged_files:
        return None

    # Seconds precision and a trailing Z keep the subject short (36 chars) and
    # ASCII; the old ISO form was equally valid but noisier for hook rules.
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    paths_list = changed_paths or staging.staged_files
    paths_summary = ", ".join(paths_list[:10])
    if len(paths_list) > 10:
        paths_summary += f" (+{len(paths_list) - 10} more)"

    target_dir = msg_file_dir or worktree_path / ".agy"
    tmp_msg = write_wrapper_commit_msg(
        target_dir,
        "CHECKPOINT_MSG",
        f"agy: checkpoint {ts}",
        f"Changed files: {paths_summary}",
    )
    try:
        return commit_with_retry(
            worktree_path,
            tmp_msg,
            is_process_alive_func=is_process_alive_func,
        )
    finally:
        tmp_msg.unlink(missing_ok=True)


def find_most_recent_commit_msg(worktree_path: Path) -> Optional[str]:
    """Find the first line of the most recent agy COMMIT_MSG if available."""
    agy_dir = worktree_path / ".agy"
    if not agy_dir.is_dir():
        return None

    active_msg = agy_dir / "COMMIT_MSG"
    if active_msg.is_file():
        try:
            content = active_msg.read_text(encoding="utf-8").strip()
            if content:
                first_line = content.splitlines()[0].strip()
                if first_line:
                    return first_line
        except Exception:
            pass

    seen_files: list[tuple[int, Path]] = []
    for f in agy_dir.glob("COMMIT_MSG.seen.*"):
        try:
            suffix = f.name.rsplit(".", 1)[-1]
            n = int(suffix)
            seen_files.append((n, f))
        except (ValueError, IndexError):
            continue

    seen_files.sort(key=lambda x: x[0], reverse=True)
    for _, seen_file in seen_files:
        try:
            content = seen_file.read_text(encoding="utf-8").strip()
            if content:
                first_line = content.splitlines()[0].strip()
                if first_line:
                    return first_line
        except Exception:
            continue

    return None


def safety_net_commit(
    worktree_path: Path,
    title: str = "",
    audit_passed: bool = True,
    msg_file_dir: Optional[Path] = None,
    job_dir: Optional[Path] = None,
    job: Optional[Any] = None,
) -> Optional[str]:
    """Execute final safety-net commit on done state per SPEC section 11, F05 & D54."""
    if not audit_passed:
        return None

    # Check if any uncommitted changes exist in worktree outside .agy
    resolved_path = Path(worktree_path).resolve(strict=True)
    status_res = run_git(
        [
            "status",
            "--porcelain=v1",
            "-z",
            "--",
            ".",
            ":(exclude,top).agy",
        ],
        resolved_path,
        timeout=30.0,
    )
    if status_res.returncode != 0:
        raise GitError(status_res.returncode, status_res.stderr, status_res.stdout, status_res.args)
    raw_entries = parse_porcelain_z(status_res.stdout)
    if not raw_entries:
        return None

    staging = stage_changes(worktree_path)
    if job is not None and staging.skipped_files:
        job.skipped_files = merge_skipped_files(job.skipped_files, staging.skipped_files)
    if not staging.staged_files:
        # All remaining uncommitted changes were build artifacts or sensitive/policy files
        if job_dir is not None:
            from agy_mcp.jobs import append_audit_event

            skipped_list = [
                f.get("path") if isinstance(f, dict) else str(f)
                for f in staging.skipped_files[:50]
            ]
            append_audit_event(
                job_dir,
                event="safety_net_skipped_only_artifacts",
                detail={"skipped_files": skipped_list},
            )
        return None

    recent_msg = find_most_recent_commit_msg(resolved_path)
    if recent_msg:
        commit_subject = sanitize_commit_subject(recent_msg)
    else:
        commit_subject = sanitize_commit_subject(f"agy: {title}")

    target_dir = msg_file_dir or worktree_path / ".agy"
    tmp_msg = write_wrapper_commit_msg(
        target_dir,
        "SAFETY_NET_MSG",
        commit_subject,
        "Final automated safety-net commit.",
    )
    try:
        return commit_with_retry(worktree_path, tmp_msg)
    finally:
        tmp_msg.unlink(missing_ok=True)


def get_merge_base(worktree_path: Path, base_commit: str, head_commit: str = "HEAD") -> str:
    """Return common merge-base ancestor between base_commit and head_commit."""
    res = run_git(["merge-base", base_commit, head_commit], worktree_path, check=True)
    return res.stdout.strip()


def get_ahead_behind(
    worktree_path: Path,
    base_commit: str,
    head_commit: str = "HEAD",
) -> tuple[int, int]:
    """Return (ahead, behind) commit counts relative to base_commit."""
    res = run_git(
        ["rev-list", "--left-right", "--count", f"{base_commit}...{head_commit}"],
        worktree_path,
        check=True,
    )
    tokens = res.stdout.strip().split()
    if len(tokens) >= 2:
        behind = int(tokens[0])
        ahead = int(tokens[1])
        return ahead, behind
    return 0, 0


def has_base_moved(
    source_repo_path: Path,
    base_commit: str,
    target_ref: str = "HEAD",
) -> bool:
    """Check whether source repository HEAD has moved past the job's initial base_commit."""
    res = run_git(["rev-parse", target_ref], source_repo_path, check=True)
    current_head = res.stdout.strip()
    return current_head != base_commit


def get_diff_stat(
    worktree_path: Path,
    base_commit: str,
    head_commit: str = "HEAD",
    max_bytes: int = 8192,
) -> str:
    """Return byte-capped git diff --stat base..head."""
    res = run_git(["diff", "--stat", f"{base_commit}..{head_commit}"], worktree_path)
    return truncate_tail(res.stdout, max_bytes)


def get_commits_list(
    worktree_path: Path,
    base_commit: str,
    head_commit: str = "HEAD",
    max_count: int = 50,
    max_subject_bytes: int = 1024,
) -> list[dict[str, Any]]:
    """Return up to max_count commits between base_commit and head_commit."""
    # Use formatted output: %H%x00%an%x00%aI%x00%s%x00
    res = run_git(
        [
            "log",
            f"--max-count={max_count}",
            "--format=%H%x00%an%x00%aI%x00%s",
            f"{base_commit}..{head_commit}",
        ],
        worktree_path,
    )

    commits: list[dict[str, Any]] = []
    lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
    for line in lines:
        parts = line.split("\x00")
        if len(parts) >= 4:
            sha, author, timestamp, subject = parts[0], parts[1], parts[2], parts[3]
            commits.append(
                {
                    "sha": sha,
                    "author": author,
                    "timestamp": timestamp,
                    "subject": truncate_tail(subject, max_subject_bytes),
                }
            )
    return commits


def get_files_modified(
    repo_or_worktree_path: Path,
    base_commit: str,
    target_ref: str = "HEAD",
    cap: int = 200,
) -> list[str]:
    """Return list of files modified between base_commit and target_ref, capped at cap entries."""
    if not base_commit or not target_ref:
        return []
    resolved = Path(repo_or_worktree_path).resolve()
    if not resolved.exists():
        return []

    res = run_git(
        ["diff", "--name-only", f"{base_commit}..{target_ref}"],
        resolved,
        check=False,
    )
    if res.returncode != 0:
        return []

    lines = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
    return lines[:cap]


def take_git_config_snapshot(worktree_path: Path) -> dict[str, str]:
    """Capture resolved git config snapshot for local/worktree repository."""
    res = run_git(["config", "--local", "--list"], worktree_path)
    snapshot: dict[str, str] = {}
    for line in res.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            snapshot[k.strip()] = v.strip()
    return snapshot


def diff_git_config_snapshots(
    baseline: dict[str, str],
    current: dict[str, str],
) -> dict[str, tuple[Optional[str], Optional[str]]]:
    """Compare baseline and current git config snapshots, returning mutated keys."""
    diffs: dict[str, tuple[Optional[str], Optional[str]]] = {}
    all_keys = set(baseline.keys()) | set(current.keys())
    for k in sorted(all_keys):
        v_base = baseline.get(k)
        v_curr = current.get(k)
        if v_base != v_curr:
            diffs[k] = (v_base, v_curr)
    return diffs
