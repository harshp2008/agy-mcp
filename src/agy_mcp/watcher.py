"""The `.agy/COMMIT_MSG` watcher and wrapper-owned commit pipeline (SPEC §11).

Owns the settle window, message validation, pre-commit audit gate, consume/
stage/commit sequence, checkpoint and safety-net entry points and their
generation markers. It must never commit while a fatal audit finding stands,
never stage `.agy/**`, and never let a malformed message loop forever (two
failures escalate to needs_review).
"""

import errno
import logging
import os
import stat
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from agy_mcp.audit import AuditSnapshot, audit, take_snapshot
from agy_mcp.gitops import (
    GitError,
    checkpoint_commit,
    commit_with_retry,
    get_files_modified,
    is_worktree_dirty,
    merge_skipped_files,
    run_git,
    safety_net_commit,
    stage_changes,
)
from agy_mcp.jobs import append_audit_event, get_job_dir, save_job, transition_job_state
from agy_mcp.models import Job
from agy_mcp.states import JobState
from agy_mcp.util import sha256_file

logger = logging.getLogger(__name__)

MAX_COMMIT_MSG_BYTES = 64 * 1024  # 64 KB explicit size cap per F16
MAX_DISCARDED_COMMIT_MSGS = 20  # D66: bounded number of .invalid.<n> files
MAX_SUBJECT_CHARS = 72


def _is_real_agy_dir(agy_dir: Path, worktree_path: Path) -> bool:
    """Return True only for a real ``.agy`` directory directly under the worktree.

    ``.agy`` is agent-controlled, so a symlink there must never be followed.
    ``lstat`` inspects the entry itself; ``realpath`` then confirms it is the
    worktree's own ``.agy`` directory rather than a link or a sibling.
    """
    try:
        entry = os.lstat(agy_dir)
    except OSError:
        return False
    if not stat.S_ISDIR(entry.st_mode):
        return False
    try:
        return os.path.realpath(agy_dir) == os.path.realpath(worktree_path / ".agy")
    except OSError:
        return False


def _next_invalid_msg_slot(agy_dir: Path) -> int:
    """Return the first free ``COMMIT_MSG.invalid.<n>`` slot, capped at 20.

    An entry that exists (including a symlink) counts as occupied. At the cap
    the caller renames onto slot 20; nothing is unlinked as a separate step.
    """
    for n in range(1, MAX_DISCARDED_COMMIT_MSGS + 1):
        try:
            os.lstat(agy_dir / f"COMMIT_MSG.invalid.{n}")
        except FileNotFoundError:
            return n
        except OSError:
            continue
    return MAX_DISCARDED_COMMIT_MSGS


def validate_commit_message(msg_path: Path) -> tuple[bool, Optional[str]]:
    """Validate that commit message is non-empty, UTF-8, subject <= 72 chars, no control chars.

    Rules from SPEC.md Section 11 & F12:
    - Non-empty (whitespace-only rejected)
    - Valid UTF-8 encoding
    - First line (subject) <= 72 characters (multibyte characters count as 1 character)
    - No control characters except tab (9), newline (10), or CRLF (13+10)
    - Rejects NUL bytes (0) and standalone carriage returns (13)
    - Rejects a symlinked, non-regular, or oversized file without following it
    """
    # lstat first: opening a FIFO would block forever waiting for a writer,
    # and a symlink must be rejected without ever being followed.
    try:
        entry = os.lstat(msg_path)
    except FileNotFoundError:
        return False, "Commit message file not found"
    except OSError as e:
        return False, f"Failed to stat commit message: {e}"
    if stat.S_ISLNK(entry.st_mode):
        return False, "Commit message is a symbolic link"
    if not stat.S_ISREG(entry.st_mode):
        return False, "Commit message is not a regular file"

    try:
        fd = os.open(msg_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return False, "Commit message file not found"
    except OSError as e:
        if e.errno == errno.ELOOP:
            return False, "Commit message is a symbolic link"
        return False, f"Failed to read commit message: {e}"

    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return False, "Commit message is not a regular file"
        if st.st_size > MAX_COMMIT_MSG_BYTES:
            return False, f"Commit message exceeds maximum size of {MAX_COMMIT_MSG_BYTES} bytes"
        chunks: list[bytes] = []
        remaining = MAX_COMMIT_MSG_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw_bytes = b"".join(chunks)
    except OSError as e:
        return False, f"Failed to read commit message: {e}"
    finally:
        os.close(fd)

    if len(raw_bytes) > MAX_COMMIT_MSG_BYTES:
        return False, f"Commit message exceeds maximum size of {MAX_COMMIT_MSG_BYTES} bytes"

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return False, "Commit message is not valid UTF-8"

    if "\x00" in text or b"\x00" in raw_bytes:
        return False, "Commit message contains NUL byte"

    return _validate_message_text(text)


def _validate_message_text(text: str) -> tuple[bool, Optional[str]]:
    """Validate decoded message text (subject length and control characters)."""
    stripped = text.strip()
    if not stripped:
        return False, "Commit message is empty"

    lines = text.splitlines()
    first_line = lines[0] if lines else ""
    if len(first_line) > MAX_SUBJECT_CHARS:
        return False, (
            f"First line of commit message exceeds {MAX_SUBJECT_CHARS} characters "
            f"({len(first_line)} chars)"
        )

    # Check for illegal control characters (allow newline \n, tab \t, and CRLF \r\n)
    normalized = text.replace("\r\n", "\n")
    for ch in normalized:
        code = ord(ch)
        if (code < 32 and code not in (9, 10)) or code == 127:
            return False, f"Commit message contains illegal control character (ASCII {code})"

    return True, None


def _read_commit_message(msg_path: Path) -> tuple[Optional[bytes], Optional[str], Optional[str]]:
    """Read a regular, non-symlinked message without following links.

    Returns ``(raw_bytes, text, error)``; ``text`` is None when the bytes are not
    valid UTF-8 (the raw bytes are still returned for evidence preservation).
    """
    try:
        entry = os.lstat(msg_path)
    except FileNotFoundError:
        return None, None, "Commit message file not found"
    except OSError as e:
        return None, None, f"Failed to stat commit message: {e}"
    if stat.S_ISLNK(entry.st_mode):
        return None, None, "Commit message is a symbolic link"
    if not stat.S_ISREG(entry.st_mode):
        return None, None, "Commit message is not a regular file"
    try:
        fd = os.open(msg_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as e:
        if e.errno == errno.ELOOP:
            return None, None, "Commit message is a symbolic link"
        return None, None, f"Failed to read commit message: {e}"
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None, None, "Commit message is not a regular file"
        if st.st_size > MAX_COMMIT_MSG_BYTES:
            return (
                None,
                None,
                f"Commit message exceeds maximum size of {MAX_COMMIT_MSG_BYTES} bytes",
            )
        chunks: list[bytes] = []
        remaining = MAX_COMMIT_MSG_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    except OSError as e:
        return None, None, f"Failed to read commit message: {e}"
    finally:
        os.close(fd)
    if len(raw) > MAX_COMMIT_MSG_BYTES:
        return (
            None,
            None,
            f"Commit message exceeds maximum size of {MAX_COMMIT_MSG_BYTES} bytes",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw, None, "Commit message is not valid UTF-8"
    if "\x00" in text:
        return raw, text, "Commit message contains NUL byte"
    return raw, text, None


def _truncate_subject_at_word(subject: str, limit: int = MAX_SUBJECT_CHARS) -> str:
    """Truncate to ``limit`` characters at the last whitespace boundary."""
    if len(subject) <= limit:
        return subject
    cut = subject[:limit]
    boundary = max(cut.rfind(" "), cut.rfind("\t"))
    if boundary > 0:
        return cut[:boundary].rstrip()
    return cut.rstrip()


def _first_line_has_control_char(first_line: str) -> bool:
    """True for NUL/CR/other control chars; tab is the only allowed control."""
    return any((ord(ch) < 32 and ch != "\t") or ord(ch) == 127 for ch in first_line)


def _write_all(fd: int, data: bytes) -> None:
    """Write every byte, tolerating short ``os.write`` returns (F6)."""
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("os.write returned 0 while writing commit-message evidence")
        view = view[written:]


def _fsync_parent_dir(path: Path) -> None:
    """fsync a directory so a just-created/renamed entry is durable (F6)."""
    try:
        dfd = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(dfd)
    except OSError:
        pass
    finally:
        os.close(dfd)


def _atomic_replace_text(msg_path: Path, text: str) -> None:
    """Write a repaired message atomically beside the original, mode 0600."""
    fd, tmp_name = tempfile.mkstemp(prefix=".COMMIT_MSG.repair.", dir=str(msg_path.parent))
    try:
        _write_all(fd, text.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(tmp_name, 0o600)
    os.replace(tmp_name, msg_path)
    _fsync_parent_dir(msg_path.parent)


def repair_overlong_commit_subject(
    msg_path: Path,
) -> tuple[bool, Optional[int], Optional[str]]:
    """Repair only an over-long subject, leaving every other rule strict.

    Returns ``(repaired, original_subject_length, error)``. The full original
    first line is moved to the top of the body; the new first line is truncated
    at a word boundary to <= 72 characters.

    Line splitting matches ``validate_commit_message`` exactly: CRLF is
    normalized first and ``splitlines``/first-line length are derived from that
    text. A message the validator already accepts is never rewritten or logged
    (F2). A first line containing a bare CR or any other control character is
    rejected, not repaired.
    """
    raw, text, error = _read_commit_message(msg_path)
    if error or text is None:
        return False, None, error

    # If the validator already accepts it (for example 72 characters plus CRLF),
    # there is nothing to repair and the file must stay byte-identical.
    already_valid, _valid_err = _validate_message_text(text)
    if already_valid:
        return False, None, None

    # Same normalization as the validator; a lone CR is deliberately kept so a
    # control character in the first line is rejected instead of "fixed" into
    # a CRLF by the rewrite.
    normalized = text.replace("\r\n", "\n")
    lines = normalized.split("\n")
    first_line = lines[0]
    if _first_line_has_control_char(first_line):
        return False, None, "Commit message first line contains a control character"
    if len(first_line) <= MAX_SUBJECT_CHARS:
        return False, None, None
    new_subject = _truncate_subject_at_word(first_line)
    if not new_subject:
        return False, None, None
    remainder = "\n".join(lines[1:])
    new_text = f"{new_subject}\n\n{first_line}\n"
    if remainder.strip():
        new_text += "\n" + remainder
    valid, new_error = _validate_message_text(new_text)
    if not valid:
        return False, None, new_error
    try:
        _atomic_replace_text(msg_path, new_text)
    except OSError as e:
        return False, None, f"Failed to repair commit message: {e}"
    return True, len(first_line), None


def _preserve_rejected_message(
    msg_path: Path, job_dir: Path
) -> tuple[Optional[str], Optional[str]]:
    """Copy a rejected regular message into the job dir before worktree cleanup.

    Returns ``(evidence_file_name, repr_of_first_line)``. Symlinks and
    non-regular files are never followed, so they yield no copy.
    """
    raw, _text, _error = _read_commit_message(msg_path)
    if raw is None:
        return None, None
    display = raw.decode("utf-8", errors="replace")
    first_line = display.splitlines()[0] if display.splitlines() else ""
    first_line_repr = repr(first_line)[:120]
    name = "rejected_COMMIT_MSG.txt"
    try:
        job_dir.mkdir(parents=True, exist_ok=True)
        target = job_dir / name
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            _write_all(fd, raw)
            os.fsync(fd)
        finally:
            os.close(fd)
        _fsync_parent_dir(job_dir)
    except OSError:
        return None, first_line_repr
    return name, first_line_repr


def _actionable_commit_error(code: str, cause: str, next_action: str) -> dict[str, str]:
    """Build the one-line-cause/one-line-next error stored on a needs_review job."""
    return {
        "code": code,
        "message": f"Cause: {cause} Next: {next_action}",
        "cause": cause,
        "next_action": next_action,
    }


class CommitWatcher:
    """Watches .agy/COMMIT_MSG with settle windows, validates, and manages commits."""

    def __init__(
        self,
        job: Job,
        on_audit_violation: Optional[Callable[[str], None]] = None,
        checkpoint_interval_seconds: int = 600,
        baseline_snapshot: Optional[AuditSnapshot] = None,
        home: Optional[Path] = None,
        base_state_dir: Optional[Path] = None,
        settle_window_seconds: float = 5.0,
        commit_lock: Optional[threading.Lock] = None,
        is_process_alive: Optional[Callable[[], bool]] = None,
        before_state_transition: Optional[Callable[[JobState], None]] = None,
    ) -> None:
        self.job = job
        self.on_audit_violation = on_audit_violation
        # Called immediately before this watcher persists a needs_review state.
        # The supervisor uses it to append the usage row first (D68/C4).
        self.before_state_transition = before_state_transition
        self.checkpoint_interval_seconds = checkpoint_interval_seconds
        self.home = home
        self.base_state_dir = base_state_dir
        self.baseline_snapshot = baseline_snapshot or take_snapshot(job, home=home)
        self.settle_window_seconds = settle_window_seconds
        self.commit_lock = commit_lock or threading.Lock()
        self.is_process_alive = is_process_alive

        # Stability tracking: (sha256, size, mtime_ns)
        self.last_seen_state: Optional[tuple[str, int, int]] = None
        self.state_stable_since: Optional[float] = None
        self.validation_failure_count: int = 0
        self.last_checkpoint_time: Optional[float] = None

    def _notify_before_state_transition(self) -> None:
        """Call the supervisor hook before persisting needs_review (D68/C4)."""
        if self.before_state_transition is not None:
            self.before_state_transition(JobState.NEEDS_REVIEW)

    def tick(self, now: Optional[float] = None) -> Optional[str]:
        """Execute a single polling tick with settle window, validation, and commit pipeline."""
        if self.job.state != JobState.RUNNING:
            return None

        current_time = time.time() if now is None else now
        if self.last_checkpoint_time is None:
            self.last_checkpoint_time = current_time

        wt_path = self.job.worktree_path.resolve() if self.job.worktree_path else None
        if not wt_path or not wt_path.exists():
            return None

        commit_msg_path = wt_path / ".agy" / "COMMIT_MSG"

        # 1. Inspect COMMIT_MSG existence and stability.
        # .agy and its files are agent-controlled: lstat the entry itself and
        # never follow a symlinked .agy parent or a symlinked COMMIT_MSG.
        agy_dir = wt_path / ".agy"
        if agy_dir.is_symlink():
            self.last_seen_state = None
            self.state_stable_since = None
        else:
            try:
                st = os.lstat(commit_msg_path)
            except OSError:
                self.last_seen_state = None
                self.state_stable_since = None
            else:
                try:
                    if stat.S_ISREG(st.st_mode):
                        sha = sha256_file(commit_msg_path)
                    elif stat.S_ISLNK(st.st_mode):
                        sha = "symlink"
                    else:
                        sha = "non-regular"
                    curr_state = (sha, st.st_size, st.st_mtime_ns)

                    if curr_state == self.last_seen_state:
                        if self.state_stable_since is None:
                            self.state_stable_since = current_time
                    else:
                        self.last_seen_state = curr_state
                        self.state_stable_since = current_time

                    # Check if stable for >= settle_window_seconds
                    # agy may write COMMIT_MSG in several chunks; only a stable
                    # (sha, size, mtime_ns) triple is safe to consume.
                    if (
                        self.state_stable_since is not None
                        and (current_time - self.state_stable_since) >= self.settle_window_seconds
                    ):
                        commit_sha = self._process_settled_commit_msg(commit_msg_path, wt_path)
                        if commit_sha:
                            return commit_sha
                except OSError:
                    pass

        # 2. Checkpoint commit timer (every checkpoint_interval_seconds if dirty)
        if (current_time - self.last_checkpoint_time) >= self.checkpoint_interval_seconds:
            self.last_checkpoint_time = current_time
            if is_worktree_dirty(wt_path):
                # Run audit gate prior to checkpoint commit
                findings = audit(self.job, self.baseline_snapshot, home=self.home)
                if not findings.has_fatal:
                    try:
                        with self.commit_lock:
                            return checkpoint_commit(
                                wt_path,
                                is_process_alive_func=self.is_process_alive,
                                job=self.job,
                            )
                    except GitError as exc:
                        hook_output = (exc.stderr or exc.stdout or str(exc)).strip()
                        hook_line = (
                            hook_output.splitlines()[0][:300]
                            if hook_output
                            else "git commit failed"
                        )
                        cause = f"timed checkpoint commit was rejected: {hook_line}"
                        next_action = (
                            "fix the repository commit hook or its message rule, then "
                            "resume the job; the dirty worktree is still on disk"
                        )
                        self.job.error = _actionable_commit_error(
                            "invalid_commit_message", cause, next_action
                        )
                        append_audit_event(
                            get_job_dir(self.job.job_id, self.base_state_dir),
                            event="checkpoint_rejected",
                            detail={
                                "stderr": hook_output[-1000:],
                                "cause": cause,
                                "next_action": next_action,
                            },
                        )
                        self._notify_before_state_transition()
                        transition_job_state(
                            self.job,
                            JobState.NEEDS_REVIEW,
                            event_trigger="Checkpoint commit rejected by the repository",
                            detail={
                                "review_reason": "invalid_commit_message",
                                "cause": cause,
                                "next_action": next_action,
                            },
                            base_state_dir=self.base_state_dir,
                        )
                        return None

        return None

    def _discard_invalid_commit_msg(self, wt_path: Path) -> tuple[str, Optional[str]]:
        """Rename the invalid ``COMMIT_MSG`` entry without following symlinks (D66).

        Returns a short audit code and the new basename (or ``None`` when the
        action was refused). ``os.replace`` acts on the directory entry itself,
        so a symlinked message is renamed as a link and its target is untouched.
        The 20-file cap renames onto the last slot instead of deleting.
        """
        agy_dir = wt_path / ".agy"
        msg_path = agy_dir / "COMMIT_MSG"
        if not _is_real_agy_dir(agy_dir, wt_path):
            return "refused_symlinked_agy_dir", None
        try:
            entry = os.lstat(msg_path)
        except FileNotFoundError:
            return "missing_entry", None
        except OSError:
            return "lstat_failed", None
        if not (stat.S_ISREG(entry.st_mode) or stat.S_ISLNK(entry.st_mode)):
            return "refused_non_regular_entry", None
        slot = _next_invalid_msg_slot(agy_dir)
        target = agy_dir / f"COMMIT_MSG.invalid.{slot}"
        try:
            os.replace(msg_path, target)
        except OSError:
            return "rename_failed", None
        return "invalid_message", target.name

    def _process_settled_commit_msg(self, commit_msg_path: Path, wt_path: Path) -> Optional[str]:
        """Validate, audit, consume, stage, and commit settled COMMIT_MSG."""
        # The parent must be the worktree's real .agy directory; a symlinked
        # .agy is never followed for validation, rename, or a commit message.
        if not _is_real_agy_dir(wt_path / ".agy", wt_path):
            self.last_seen_state = None
            self.state_stable_since = None
            return None

        job_dir = get_job_dir(self.job.job_id, self.base_state_dir)

        # 1. Repair an over-long subject instead of rejecting. The validator
        # itself still rejects >72 characters; repair runs first and rewrites
        # the message only when every other rule already passes.
        repaired, original_len, _repair_err = repair_overlong_commit_subject(commit_msg_path)
        if repaired:
            append_audit_event(
                job_dir,
                event="commit_msg_repaired",
                detail={"original_len": original_len},
            )

        # 2. Validate commit message
        valid, err = validate_commit_message(commit_msg_path)
        if not valid:
            self.last_seen_state = None
            self.state_stable_since = None
            if self.validation_failure_count == 0:
                self.validation_failure_count = 1
                return None
            else:
                # Second failure sets needs_review(invalid_commit_message).
                # The one-retry allowance avoids killing a job for a transient
                # partial write while still bounding a persistently bad message.
                # Preserve a wrapper-owned copy before the entry is renamed:
                # worktree cleanup would otherwise destroy the evidence.
                evidence_file, first_line_repr = _preserve_rejected_message(
                    commit_msg_path, job_dir
                )
                # The invalid entry is discarded (D66) so a resumed
                # generation finds no pending message to re-validate.
                try:
                    code, file_name = self._discard_invalid_commit_msg(wt_path)
                except Exception:
                    code, file_name = "discard_failed", None
                    logger.warning("commit_msg_discard_failed")
                reason_text = (err or "unknown validation failure")[:500]
                cause = f".agy/COMMIT_MSG was rejected twice: {reason_text}"
                next_action = (
                    "write a non-empty single-line UTF-8 subject of at most 72 "
                    "characters with no NUL/control characters to .agy/COMMIT_MSG, "
                    "then resume the job"
                )
                self.job.error = _actionable_commit_error(
                    "invalid_commit_message", cause, next_action
                )
                if first_line_repr is not None:
                    self.job.error["first_line_repr"] = first_line_repr
                if evidence_file is not None:
                    self.job.error["evidence_file"] = evidence_file
                try:
                    # Keep this event to the historical code/file pair: message
                    # text and validator detail stay in job.error and the state
                    # transition, never in the audit trail (privacy regression
                    # pinned by tests/test_stale_commit_msg.py).
                    append_audit_event(
                        get_job_dir(self.job.job_id, self.base_state_dir),
                        event="commit_msg_discarded",
                        detail={"code": code, "file": file_name},
                    )
                except OSError:
                    logger.warning("commit_msg_discarded_event_failed")
                self._notify_before_state_transition()
                transition_job_state(
                    self.job,
                    JobState.NEEDS_REVIEW,
                    event_trigger="Malformed .agy/COMMIT_MSG after retries",
                    detail={
                        "review_reason": "invalid_commit_message",
                        "cause": cause,
                        "next_action": next_action,
                    },
                    base_state_dir=self.base_state_dir,
                )
                return None

        self.validation_failure_count = 0

        # 2. Audit gate first
        # The audit runs before the COMMIT_MSG is consumed, so a fatal violation
        # cannot be "cleaned up" by a commit that removes its evidence.
        findings = audit(self.job, self.baseline_snapshot, home=self.home)
        if findings.has_fatal:
            self._notify_before_state_transition()
            transition_job_state(
                self.job,
                JobState.NEEDS_REVIEW,
                event_trigger=(
                    "Fatal audit violation, host path modified, or instruction file altered"
                ),
                detail={"review_reason": findings.fatal_reason},
                base_state_dir=self.base_state_dir,
            )
            if self.on_audit_violation and findings.fatal_reason:
                self.on_audit_violation(findings.fatal_reason)
            return None

        with self.commit_lock:
            # 3. Consume step: atomically rename .agy/COMMIT_MSG -> .agy/COMMIT_MSG.seen.<n>
            generation = self.job.commit_msg_generation + 1
            seen_path = wt_path / ".agy" / f"COMMIT_MSG.seen.{generation}"
            os.replace(commit_msg_path, seen_path)

            # Clear stability tracking
            self.last_seen_state = None
            self.state_stable_since = None

            # 4. Stage changes
            staging = stage_changes(wt_path, is_process_alive_func=self.is_process_alive)
            if staging.skipped_files:
                self.job.skipped_files = merge_skipped_files(
                    self.job.skipped_files, staging.skipped_files
                )
            if staging.sensitive_partial_commit:
                self._notify_before_state_transition()
                transition_job_state(
                    self.job,
                    JobState.NEEDS_REVIEW,
                    event_trigger="Sensitive tracked file modified; partial commit",
                    detail={"review_reason": "partial_commit"},
                    base_state_dir=self.base_state_dir,
                )

            # 4b. Check if anything was staged
            diff_res = run_git(["diff", "--cached", "--quiet"], wt_path)
            if diff_res.returncode == 0:
                # Commit skipped: nothing staged
                self.job.commit_msg_generation = generation
                save_job(self.job, self.base_state_dir)
                job_dir = get_job_dir(self.job.job_id, self.base_state_dir)
                first_line = (
                    seen_path.read_text(encoding="utf-8").splitlines()[0]
                    if seen_path.is_file()
                    else ""
                )
                append_audit_event(
                    job_dir,
                    event="commit_skipped_no_changes",
                    detail={"subject": first_line, "generation": generation},
                )
                try:
                    (wt_path / ".agy" / f"COMMIT_MSG.done.{generation}").touch()
                except OSError:
                    pass
                return None

            # 5. Commit execution. Repository hooks now run for wrapper commits
            # (D80); a hook rejection is actionable feedback, not a tick error.
            try:
                commit_sha = commit_with_retry(
                    wt_path,
                    msg_file=seen_path,
                    is_process_alive_func=self.is_process_alive,
                )
            except GitError as exc:
                hook_output = (exc.stderr or exc.stdout or str(exc)).strip()
                hook_line = (
                    hook_output.splitlines()[0][:300] if hook_output else "git commit failed"
                )
                cause = f"git commit was rejected: {hook_line}"
                next_action = (
                    "read the hook output named above; make the commit message comply or fix "
                    f"the failing hook, then resume the job (message kept as {seen_path.name})"
                )
                self.job.error = _actionable_commit_error(
                    "invalid_commit_message", cause, next_action
                )
                job_dir = get_job_dir(self.job.job_id, self.base_state_dir)
                append_audit_event(
                    job_dir,
                    event="commit_rejected",
                    detail={
                        "generation": generation,
                        "stderr": hook_output[-1000:],
                        "cause": cause,
                        "next_action": next_action,
                    },
                )
                self._notify_before_state_transition()
                transition_job_state(
                    self.job,
                    JobState.NEEDS_REVIEW,
                    event_trigger="Repository commit hook rejected the commit",
                    detail={
                        "review_reason": "invalid_commit_message",
                        "cause": cause,
                        "next_action": next_action,
                    },
                    base_state_dir=self.base_state_dir,
                )
                return None
            if not commit_sha:
                return None

            # 6. Update job state and event log
            self.job.head_commit = commit_sha
            self.job.commits_count += 1
            self.job.commit_msg_generation = generation
            if self.job.base_commit:
                self.job.files_modified = get_files_modified(
                    wt_path, self.job.base_commit, commit_sha, cap=200
                )
            save_job(self.job, self.base_state_dir)

            job_dir = get_job_dir(self.job.job_id, self.base_state_dir)
            first_line = seen_path.read_text(encoding="utf-8").splitlines()[0]
            append_audit_event(
                job_dir,
                event="commit",
                detail={"sha": commit_sha, "subject": first_line, "generation": generation},
            )
            try:
                (wt_path / ".agy" / f"COMMIT_MSG.done.{generation}").touch()
            except OSError:
                pass
            return commit_sha

    def run_checkpoint_commit(self, reason: str = "checkpoint") -> Optional[str]:
        """Create a timed or pre-termination fallback checkpoint commit."""
        # F05: No commit while state is needs_review(security)
        if self.job.state == JobState.NEEDS_REVIEW and self.job.review_reason in (
            "audit_violation",
            "host_path_modified",
            "instruction_file_modified",
        ):
            return None

        wt_path = self.job.worktree_path.resolve() if self.job.worktree_path else None
        if not wt_path or not wt_path.exists():
            return None

        findings = audit(self.job, self.baseline_snapshot, home=self.home)
        if findings.has_fatal:
            return None

        with self.commit_lock:
            return checkpoint_commit(
                wt_path, is_process_alive_func=self.is_process_alive, job=self.job
            )

    def run_safety_net_commit(self, title: str, summary: str = "") -> Optional[str]:
        """Create safety-net commit on clean normal termination."""
        # F05: No commit after an audit violation or while state is needs_review(security)
        if self.job.state == JobState.NEEDS_REVIEW and self.job.review_reason in (
            "audit_violation",
            "host_path_modified",
            "instruction_file_modified",
        ):
            return None

        wt_path = self.job.worktree_path.resolve() if self.job.worktree_path else None
        if not wt_path or not wt_path.exists():
            return None

        findings = audit(self.job, self.baseline_snapshot, home=self.home)
        if findings.has_fatal:
            return None

        job_dir = (
            get_job_dir(self.job.job_id, self.base_state_dir)
            if self.base_state_dir
            else None
        )
        with self.commit_lock:
            return safety_net_commit(
                wt_path, title=title, audit_passed=True, job_dir=job_dir, job=self.job
            )

    def final_commit(self, reason: str) -> Optional[str]:
        """Execute pre-kill or completion commit according to job outcome."""
        # F05: Suppress all commits if in security needs_review state
        # This early guard is repeated by the callers; keeping it here ensures no
        # future caller can accidentally commit after a security finding.
        if self.job.state == JobState.NEEDS_REVIEW and self.job.review_reason in (
            "audit_violation",
            "host_path_modified",
            "instruction_file_modified",
        ):
            return None

        wt_path = self.job.worktree_path.resolve() if self.job.worktree_path else None
        if not wt_path or not wt_path.exists():
            return None

        findings = audit(self.job, self.baseline_snapshot, home=self.home)
        if findings.has_fatal:
            # Under fatal audit violation, commits are strictly suppressed
            return None

        if reason == "done":
            return self.run_safety_net_commit(title=self.job.title)
        elif reason in ("timeout", "cancelled", "failed"):
            return self.run_checkpoint_commit(reason=reason)

        return None
