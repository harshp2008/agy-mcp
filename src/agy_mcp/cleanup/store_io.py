"""Shared store and record primitives for both cleanup CLIs (SPEC §22, §23).

Pure, side-effecting store I/O and record helpers: strict JSON loading with a
depth cap and duplicate-key rejection, canonical UUID checks, PID/PGID
liveness, timestamp parsing, the edge-protection fixpoint, atomic manifest
writing, bottom-up safe deletion, and the fail-closed ``job.json`` loader.
Both ``jobs_cli`` and ``fixtures_cli`` import these; neither imports the other.
"""

from __future__ import annotations

import errno
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional


def normalize_repo_path(path_val: str | Path) -> str:
    """Normalize a repository path string or Path object deterministically.

    Expands user home directory (~), resolves dot and parent components without
    requiring the directory to currently exist on disk (strict=False), strips
    trailing slashes, and preserves case.
    """
    path_str = str(path_val)
    if not path_str.strip():
        raise ValueError("repository path cannot be empty or whitespace")
    if "\x00" in path_str:
        raise ValueError("embedded null byte in repository path")
    expanded = os.path.expanduser(path_str)
    return str(Path(expanded).resolve(strict=False))


MAX_JSON_DEPTH: int = 128


_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _raise_on_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Dict constructor for json.loads that raises ValueError on duplicate keys (SPEC §23, R4)."""
    d: dict[str, Any] = {}
    for k, v in pairs:
        if k in d:
            raise ValueError(f"duplicate key in JSON object: {k!r}")
        d[k] = v
    return d


def _check_json_depth(obj: Any, max_depth: int = MAX_JSON_DEPTH) -> None:
    """Iteratively verify that JSON structure does not exceed max_depth."""
    stack: list[tuple[Any, int]] = [(obj, 0)]
    while stack:
        curr, depth = stack.pop()
        if depth > max_depth:
            raise ValueError(f"JSON nesting depth exceeds limit ({max_depth})")
        if isinstance(curr, dict):
            for v in curr.values():
                stack.append((v, depth + 1))
        elif isinstance(curr, list):
            for item in curr:
                stack.append((item, depth + 1))


def _safe_json_loads(content: str, max_depth: int = MAX_JSON_DEPTH) -> Any:
    """Parse JSON string, raising ValueError or JSONDecodeError on duplicate keys or depth."""
    try:
        data = json.loads(content, object_pairs_hook=_raise_on_duplicate_keys)
    except RecursionError as exc:
        raise ValueError(f"JSON nesting depth exceeds recursion limit: {exc}") from exc
    _check_json_depth(data, max_depth=max_depth)
    return data


def _is_canonical_uuid(val: str) -> bool:
    """Return True if val is a canonical lowercase UUID string."""
    return bool(_UUID_RE.fullmatch(val))


def _get_kernel_pid_max() -> int:
    """Read Linux kernel pid_max with a safe fallback of 4194304."""
    try:
        p = Path("/proc/sys/kernel/pid_max")
        if p.is_file():
            val = int(p.read_text(encoding="utf-8").strip())
            if val > 0:
                return val
    except Exception:
        pass
    return 4_194_304


def _check_pid_status(pid: Any) -> tuple[bool, Optional[str]]:
    """Check a pid value.

    Returns:
        (keep, reason)
        If keep is True, the row must be kept for the returned reason.
        If keep is False, the process is confirmed dead and not ambiguous.
    """
    if pid is None:
        return False, None
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return True, f"invalid or non-positive pid value: {pid!r}"

    pid_max = _get_kernel_pid_max()
    if pid > pid_max:
        return True, f"pid {pid} exceeds kernel pid_max ({pid_max})"

    try:
        os.kill(pid, 0)
        return True, f"process {pid} is still alive"
    except PermissionError:
        return True, f"process {pid} is still alive (permission denied)"
    except ProcessLookupError:
        return False, None
    except (OverflowError, ValueError) as exc:
        return True, f"ambiguous pid {pid}: {exc}"
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False, None
        return True, f"ambiguous pid {pid}: os.kill failed with {exc}"


def _check_pgid_status(pgid: Any) -> tuple[bool, Optional[str]]:
    """Check a pgid value mirroring _check_pid_status rules.

    Returns:
        (keep, reason)
        If keep is True, the row must be kept for the returned reason.
        If keep is False, the process group is confirmed dead and not ambiguous.
    """
    if pgid is None:
        return False, None
    if isinstance(pgid, bool) or not isinstance(pgid, int) or pgid <= 0:
        return True, f"invalid or non-positive pgid value: {pgid!r}"

    pid_max = _get_kernel_pid_max()
    if pgid > pid_max:
        return True, f"pgid {pgid} exceeds kernel pid_max ({pid_max})"

    try:
        os.killpg(pgid, 0)
        return True, f"process group {pgid} is still alive"
    except PermissionError:
        return True, f"process group {pgid} is still alive (permission denied)"
    except ProcessLookupError:
        return False, None
    except (OverflowError, ValueError) as exc:
        return True, f"ambiguous pgid {pgid}: {exc}"
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False, None
        return True, f"ambiguous pgid {pgid}: os.killpg failed with {exc}"


def _parse_iso_ts(ts_str: Any) -> Optional[datetime]:
    """Parse an ISO 8601 string, requiring timezone awareness."""
    if not isinstance(ts_str, str) or not ts_str.strip():
        return None
    s = ts_str.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            return None
        return dt
    except Exception:
        return None


def _has_symlinks(dir_path: Path) -> bool:
    """Return True if dir_path itself or any file/dir inside it is a symlink."""
    if dir_path.is_symlink():
        return True
    walk_errors: list[OSError] = []

    def _onerror(exc: OSError) -> None:
        walk_errors.append(exc)

    try:
        for root, dirs, files in os.walk(
            dir_path, topdown=True, onerror=_onerror, followlinks=False
        ):
            for name in dirs:
                p = Path(root) / name
                if p.is_symlink():
                    return True
            for name in files:
                p = Path(root) / name
                if p.is_symlink():
                    return True
    except OSError:
        return True
    # An unreadable subtree could hide a symlink; treat it as ambiguous.
    return bool(walk_errors)


def _extract_strings(obj: Any, max_depth: int = MAX_JSON_DEPTH) -> list[str]:
    """Iteratively collect all dict keys and string values from a JSON structure up to max_depth.

    Raises ValueError if nesting depth exceeds max_depth.
    """
    strings: list[str] = []
    stack: list[tuple[Any, int]] = [(obj, 0)]
    while stack:
        curr, depth = stack.pop()
        if depth > max_depth:
            raise ValueError(f"JSON nesting depth exceeds limit ({max_depth})")
        if isinstance(curr, str):
            strings.append(curr)
        elif isinstance(curr, dict):
            for k, v in curr.items():
                if isinstance(k, str):
                    strings.append(k)
                stack.append((v, depth + 1))
        elif isinstance(curr, list):
            for item in curr:
                stack.append((item, depth + 1))
    return strings


def _apply_edge_protection(
    candidates: dict[str, dict[str, Any]],
    kept: dict[str, list[str]],
    kept_text: str,
    *,
    strings_for: Callable[[str], list[str]],
) -> str:
    """Move every candidate referenced by a kept record out of ``candidates``.

    Edge protection is the last guard against deleting a job record whose id a
    kept record still points at. ``kept_text`` is the NUL-joined set of strings
    from every kept record, so a candidate id occurring anywhere in it must be
    kept. Keeping a candidate adds its own record's strings to ``kept_text``, so
    a reference chain cascades -- hence the fixpoint loop. Each iteration can
    only shrink ``candidates`` and grow ``kept``, so it terminates, and it is
    fail-closed by construction: a protected row is never re-exposed.

    ``strings_for(cid)`` returns the strings of that candidate's record. It is a
    callback because the two cleaners capture the record differently:
    ``cleanup_jobs`` reuses the data parsed during classification, while
    ``cleanup_fixtures`` re-reads the file best-effort. The callback owns any
    error handling; this loop performs none, exactly as the two copies did.

    Returns the updated ``kept_text``.
    """
    while True:
        newly_kept: list[str] = []
        for cid in sorted(candidates.keys()):
            if cid in kept_text:
                newly_kept.append(cid)
        if not newly_kept:
            return kept_text
        new_strings: list[str] = []
        for cid in newly_kept:
            candidates.pop(cid)
            kept[cid] = ["referenced by kept job"]
            new_strings.extend(strings_for(cid))
        if new_strings:
            kept_text += "\0" + "\0".join(new_strings)


def _write_manifest(store_jobs: Path, manifest_target: Path, data: dict[str, Any]) -> str:
    """Atomically write an ids-only manifest file with permissions 0600."""
    content = json.dumps(data, indent=2) + "\n"
    fd, tmp_manifest = tempfile.mkstemp(prefix=".manifest_tmp_", dir=str(store_jobs))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_manifest, 0o600)
        os.replace(tmp_manifest, manifest_target)
        return str(manifest_target.resolve())
    except Exception:
        if os.path.exists(tmp_manifest):
            try:
                os.unlink(tmp_manifest)
            except OSError:
                pass
        raise


def _safe_delete_job_dir(job_dir: Path, job_id: str, store_jobs_dir: Path) -> None:
    """Recursively delete a confirmed job directory bottom-up without following symlinks."""
    expected_root = (store_jobs_dir / job_id).resolve()
    if job_dir.is_symlink():
        raise RuntimeError(f"Refusing to delete symlinked job directory: {job_dir}")
    if job_dir.resolve() != expected_root:
        raise RuntimeError(
            f"Job directory resolved path {job_dir.resolve()} != expected {expected_root}"
        )

    for root, dirs, files in os.walk(job_dir, topdown=False, followlinks=False):
        current = Path(root).resolve()
        if current != expected_root and expected_root not in current.parents:
            raise RuntimeError(f"Safety violation: {current} escapes {expected_root}")
        for f in files:
            p = Path(root) / f
            if p.is_symlink():
                raise RuntimeError(f"Safety violation: symlink file {p} found during deletion")
            p_res = p.resolve()
            if p_res != expected_root and expected_root not in p_res.parents:
                raise RuntimeError(f"Safety violation: {p} resolves outside {expected_root}")
            p.unlink()
        for d in dirs:
            p = Path(root) / d
            if p.is_symlink():
                raise RuntimeError(f"Safety violation: symlink dir {p} found during deletion")
            p.rmdir()
    job_dir.rmdir()


# Same 1 MiB cap as jobs.MAX_JOB_FILE_BYTES. The cleaner re-reads job.json
# outside the server on every scan and immediately before every delete, so a
# corrupted or inflated record must not be able to exhaust it (audit C3a).
MAX_JOB_JSON_BYTES: int = 1024 * 1024


class JobJsonProblem(str, Enum):
    """Why :func:`load_job_json` refused a record.

    Callers own the user-facing wording: the five read sites deliberately word
    the same failure differently (kept rows, warnings, fail-closed store-wide
    messages), and those strings are part of the CLI contract.
    """

    NOT_REGULAR = "not_regular"
    TOO_LARGE = "too_large"
    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class JobJsonFailure:
    """A fail-closed job.json read; ``detail`` is the underlying error text."""

    problem: JobJsonProblem
    detail: str = ""


def load_job_json(job_file: Path) -> tuple[Any, Optional[JobJsonFailure]]:
    """Read one job.json fail-closed under the shared 1 MiB cap.

    The cleaner used to re-implement "stat -> 1 MiB cap -> read -> safe parse"
    at five sites, and two of the copies had a regular-file check the others
    lacked (audit C3a). This is the single implementation.

    It deliberately does not decide what a non-object root means: the call
    sites word that reason differently, and the pre-delete re-check must keep
    its historical AttributeError path for a job.json swapped after
    classification.

    Returns ``(document, None)`` on success, or ``(None, failure)`` when the file
    can never be trusted. ``stat()`` runs before opening so a FIFO/device file
    can never block the cleaner.
    """
    try:
        st = job_file.stat()
    except OSError as exc:
        return None, JobJsonFailure(JobJsonProblem.UNREADABLE, str(exc))
    if not stat.S_ISREG(st.st_mode):
        return None, JobJsonFailure(
            JobJsonProblem.NOT_REGULAR, "job.json is not a regular file"
        )
    if st.st_size > MAX_JOB_JSON_BYTES:
        return None, JobJsonFailure(JobJsonProblem.TOO_LARGE)
    try:
        content = job_file.read_text(encoding="utf-8")
        data = _safe_json_loads(content)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return None, JobJsonFailure(JobJsonProblem.UNREADABLE, str(exc))
    return data, None
