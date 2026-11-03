"""Move one conversation database between two account stores, safely (D111).

Owns the per-conversation ``flock``, the source/destination path rules, the
SQLite header and WAL checks, and the atomic copy. It must never copy a keyring
entry, token, settings file or brain directory, never delete the source, and
never run an account's agy: the caller resumes the same conversation id after
this module has put the file in the target store.
"""

import fcntl
import hashlib
import os
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from agy_mcp.accounts.errors import AccountError
from agy_mcp.util import strict_uuid

CONVERSATIONS_REL = Path(".gemini") / "antigravity-cli" / "conversations"
SQLITE_HEADER = b"SQLite format 3\x00"
_CHUNK = 1024 * 1024


def _validate_id(conversation_id: Optional[str]) -> str:
    cid = strict_uuid(conversation_id)
    if cid is None:
        raise AccountError(
            "a conversation transfer needs a valid UUID conversation id",
            code="account_transfer_invalid_id",
        )
    return cid


def conversation_db_path(home: Path, conversation_id: Optional[str]) -> Path:
    """Return ``<home>/.gemini/antigravity-cli/conversations/<id>.db``.

    The id is re-validated as a strict UUID so it can never introduce a path
    component; a non-UUID is refused instead of being joined into a path.
    """
    cid = _validate_id(conversation_id)
    return Path(home) / CONVERSATIONS_REL / f"{cid}.db"


@contextmanager
def conversation_lock(
    state_dir: Path, conversation_id: Optional[str], blocking: bool = False
) -> Iterator[Path]:
    """Hold the per-conversation lock for the duration of a copy.

    A second process/thread that tries the same id without ``blocking`` raises
    ``account_transfer_locked`` instead of racing the copy. The lock file is
    0600 under ``<state>/accounts/locks``; it carries no data.
    """
    cid = _validate_id(conversation_id)
    lock_dir = Path(state_dir) / "accounts" / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(lock_dir, 0o700)
    lock_path = lock_dir / f"conversation-{cid}.lock"
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(fd, flags)
        except OSError as exc:
            raise AccountError(
                "this conversation is already being transferred",
                code="account_transfer_locked",
            ) from exc
        yield lock_path
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


@dataclass(frozen=True)
class TransferResult:
    """Outcome of a successful conversation copy."""

    conversation_id: str
    source: Path
    target: Path
    size_bytes: int
    reused: bool


def _header_ok(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(len(SQLITE_HEADER)) == SQLITE_HEADER
    except OSError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _wal_path(path: Path) -> Path:
    return path.with_name(path.name + "-wal")


def transfer_conversation(
    source_home: Path,
    target_home: Path,
    conversation_id: Optional[str],
    state_dir: Path,
) -> TransferResult:
    """Copy a conversation db into the target store under the conversation lock.

    Refuses (with a stable code the caller turns into a summary handoff) when the
    source is missing, has a non-empty ``-wal`` sidecar, is not a SQLite file, or
    the target already holds a different file for the same id. An identical
    target file is reused. The source copy is never modified or deleted.
    """
    cid = _validate_id(conversation_id)
    with conversation_lock(state_dir, cid):
        source = conversation_db_path(source_home, cid)
        if not source.is_file():
            raise AccountError(
                "the conversation database is missing on the source account",
                code="account_transfer_source_missing",
            )
        if not _header_ok(source):
            raise AccountError(
                "the conversation database is not a valid SQLite file",
                code="account_transfer_corrupt",
            )
        wal = _wal_path(source)
        if wal.is_file():
            try:
                wal_size = wal.stat().st_size
            except OSError:
                wal_size = 0
            if wal_size > 0:
                raise AccountError(
                    "the conversation database still has a non-empty WAL; refusing a "
                    "partial copy",
                    code="account_transfer_incomplete",
                )

        target = conversation_db_path(target_home, cid)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(target.parent, 0o700)
        if target.is_file():
            if _sha256(target) == _sha256(source):
                return TransferResult(
                    conversation_id=cid,
                    source=source,
                    target=target,
                    size_bytes=target.stat().st_size,
                    reused=True,
                )
            raise AccountError(
                "the target account already has a different conversation with this id",
                code="account_transfer_conflict",
            )

        tmp = target.with_name(f"{target.name}.tmp.{os.getpid()}")
        try:
            shutil.copyfile(source, tmp)
            os.chmod(tmp, 0o600)
            with open(tmp, "rb") as handle:
                os.fsync(handle.fileno())
            if not _header_ok(tmp):
                raise AccountError(
                    "the copied conversation database failed its header check",
                    code="account_transfer_corrupt",
                )
            os.replace(tmp, target)
        finally:
            if tmp.exists():
                tmp.unlink()

        return TransferResult(
            conversation_id=cid,
            source=source,
            target=target,
            size_bytes=target.stat().st_size,
            reused=False,
        )
