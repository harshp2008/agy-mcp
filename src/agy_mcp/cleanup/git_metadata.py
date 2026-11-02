"""Strict readers and validators for git metadata text files (SPEC §23, R2/R5/R10).

Everything here is a pure function over paths: `.git` pointers, `HEAD`,
`config`, `packed-refs`, `commondir`, `gitdir`, `locked`, and the directory
shape of a real git dir. The cleaner's policy lives in ``worktree_status``;
this module only answers "is this valid git metadata, and why not".
"""

from __future__ import annotations

import re
import stat
import unicodedata
from pathlib import Path
from typing import Optional

MAX_GIT_METADATA_SIZE: int = 1024 * 1024  # 1 MiB limit for git metadata files


_HEX_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")


_CONFIG_SECTION_RE = re.compile(r"^\[([a-zA-Z0-9_\-\.]+)(?:\s+\"([^\"]*)\")?\]$")


_CONFIG_VAR_RE = re.compile(r"^([a-zA-Z0-9_\-]+)\s*(?:=\s*(.*))?$")


def _read_git_metadata_file(
    file_path: Path,
    file_label: str,
    *,
    allow_empty: bool = False,
    multiline: bool = False,
    max_size: int = MAX_GIT_METADATA_SIZE,
) -> tuple[Optional[str], Optional[str]]:
    """Read and validate a git metadata text file per SPEC §23 (R2, R5, R10).

    Returns (content, None) on success, or (None, failure_reason) on failure.
    Validates:
    - Must be a regular file or symlink resolving to a regular file (R5, R10).
    - Checks stat() before opening so FIFOs, sockets, and device files can never block (R10, R11).
    - Dangling symlinks or non-regular files return a failure reason.
    - File size is capped to max_size (default 1 MiB) to prevent OOM/hang (R10, R11).
    - If allow_empty is False, empty file returns a failure reason.
    - Decodes with strict UTF-8 (no silent BOM stripping via utf-8-sig) (R2, R10).
    - Must not contain UTF-8 BOM (\\xef\\xbb\\xbf or \\ufeff) (R2, R10).
    - Must not contain NUL byte (\\x00) (R2, R10).
    - For single-line files (multiline=False): at most one trailing LF or CRLF is stripped;
      no other control characters allowed in the body (R2, R10).
    - For multi-line files (multiline=True): returns text stripped of trailing whitespace,
      with no NUL or BOM, strict UTF-8 (R10).
    """
    try:
        # Check stat() before opening to guarantee we never block on FIFOs, sockets, or devices
        if file_path.is_symlink() and not file_path.exists():
            return None, f"{file_label} is invalid: dangling symlink: {file_path}"
        try:
            st = file_path.stat()
        except OSError as exc:
            return None, f"{file_label} is invalid ({exc}): {file_path}"

        if not stat.S_ISREG(st.st_mode):
            return None, f"{file_label} is invalid: not a regular file: {file_path}"

        if st.st_size > max_size:
            return (
                None,
                f"{file_label} is invalid: exceeds size limit ({st.st_size} > {max_size}): "
                f"{file_path}",
            )

        if st.st_size == 0:
            if allow_empty:
                return "", None
            return None, f"{file_label} is invalid: empty file: {file_path}"

        raw_bytes = file_path.read_bytes()
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"{file_label} unreadable ({exc}): {file_path}"

    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        return None, f"{file_label} is invalid: contains UTF-8 BOM: {file_path}"
    if b"\x00" in raw_bytes:
        return None, f"{file_label} is invalid: contains NUL byte: {file_path}"

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, f"{file_label} unreadable ({exc}): {file_path}"

    if "\ufeff" in text:
        return None, f"{file_label} is invalid: contains UTF-8 BOM: {file_path}"

    if multiline:
        return text, None

    if text.endswith("\r\n"):
        body = text[:-2]
    elif text.endswith("\n"):
        body = text[:-1]
    else:
        body = text

    # Check for any control character in body (code < 32 or C1 controls or category Cc)
    for ch in body:
        code = ord(ch)
        if code < 32 or (0x7F <= code <= 0x9F) or unicodedata.category(ch) == "Cc":
            return (
                None,
                f"{file_label} is invalid: contains control character (code {code}): {file_path}",
            )

    if body != body.strip():
        return (
            None,
            f"{file_label} is invalid: unstripped whitespace in single-line file: {file_path}",
        )

    if not allow_empty and not body:
        return None, f"{file_label} is invalid: empty path: {file_path}"

    return body, None


def _validate_git_head_file(head_path: Path, label: str) -> tuple[bool, Optional[str]]:
    """Validate git HEAD file per SPEC §23 (R10).

    HEAD must be a regular file, strict UTF-8, no BOM, no NUL, no control chars,
    and must match 'ref: refs/<name>' or a 40/64-char hex object id.
    """
    raw_content, err = _read_git_metadata_file(
        head_path, label, allow_empty=False, multiline=False
    )
    if err is not None:
        return False, err

    # Validate HEAD format: symbolic ref or detached hex object id
    if raw_content.startswith("ref: refs/"):
        ref_part = raw_content[len("ref: refs/") :].strip()
        if not ref_part or any(c.isspace() for c in ref_part):
            return (
                False,
                f"{label} is invalid: malformed symbolic ref: {head_path}",
            )
        return True, None

    if _HEX_SHA_RE.match(raw_content):
        return True, None

    return (
        False,
        f"{label} is invalid: neither a symbolic ref ('ref: refs/...') nor a valid hex object id: "
        f"{head_path}",
    )


def _validate_git_config_file(config_path: Path, label: str) -> tuple[bool, Optional[str]]:
    """Validate git config file per SPEC §23 (R10).

    Must be regular file, strict UTF-8, no BOM, no NUL, and syntax parseable by git.
    Empty config is valid (git defaults applied).
    """
    text, err = _read_git_metadata_file(
        config_path, label, allow_empty=True, multiline=True
    )
    if err is not None:
        return False, err

    in_section = False
    for line_num, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue

        if line.startswith("["):
            m = _CONFIG_SECTION_RE.match(line)
            if not m:
                return (
                    False,
                    f"{label} is invalid: malformed section header on line {line_num}: "
                    f"{config_path}",
                )
            in_section = True
            continue

        # Variables outside of a section cause git error: 'key does not contain a section'
        # or 'fatal: bad config line N'
        if not in_section:
            return (
                False,
                f"{label} is invalid: variable outside section on line {line_num}: {config_path}",
            )

        m = _CONFIG_VAR_RE.match(line)
        if not m:
            return (
                False,
                f"{label} is invalid: bad config line {line_num}: {config_path}",
            )

        var_key = m.group(1).lower()
        var_val = m.group(2)
        if var_key == "repositoryformatversion":
            if var_val is None or not var_val.strip().isdigit():
                return (
                    False,
                    f"{label} is invalid: bad numeric value for repositoryformatversion on "
                    f"line {line_num}: {config_path}",
                )
        if var_key in (
            "bare",
            "filemode",
            "logallrefupdates",
            "ignorecase",
            "precomposeunicode",
        ):
            if var_val is not None and not var_val.strip():
                return (
                    False,
                    f"{label} is invalid: empty boolean value for {var_key} on "
                    f"line {line_num}: {config_path}",
                )

    return True, None


def _validate_git_packed_refs_file(
    packed_refs_path: Path, label: str
) -> tuple[bool, Optional[str]]:
    """Validate git packed-refs file per SPEC §23 (R10).

    Must be regular file, strict UTF-8, no BOM, no NUL.
    Empty file is valid.
    Non-empty file must start with '# pack-refs with:', followed by comments,
    refs ('<sha> refs/...'), or peeled refs ('^<sha>').
    """
    text, err = _read_git_metadata_file(
        packed_refs_path, label, allow_empty=True, multiline=True
    )
    if err is not None:
        return False, err

    if text and not (text.endswith("\n") or text.endswith("\r\n")):
        return (
            False,
            f"{label} is invalid: unterminated line: {packed_refs_path}",
        )

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return True, None

    if not lines[0].startswith("# pack-refs with:"):
        return (
            False,
            f"{label} is invalid: missing '# pack-refs with:' header: {packed_refs_path}",
        )

    for line_num, line in enumerate(lines[1:], start=2):
        if line.startswith("#"):
            continue
        if line.startswith("^"):
            if not _HEX_SHA_RE.match(line[1:]):
                return (
                    False,
                    f"{label} is invalid: bad peeled ref on line {line_num}: {packed_refs_path}",
                )
            continue
        parts = line.split(" ", 1)
        if len(parts) != 2:
            return (
                False,
                f"{label} is invalid: unexpected line {line_num}: {packed_refs_path}",
            )
        sha, ref = parts
        if not _HEX_SHA_RE.match(sha) or not ref.startswith("refs/"):
            return (
                False,
                f"{label} is invalid: malformed ref line {line_num}: {packed_refs_path}",
            )

    return True, None


_GIT_DIR_MARKERS: tuple[str, ...] = (
    "worktrees",
    "objects",
    "refs",
    "config",
    "packed-refs",
    "commondir",
)


def _has_git_dir_markers(path: Path) -> bool:
    """Return True if path contains any git directory markers (file, dir, or symlink)."""
    for marker in _GIT_DIR_MARKERS:
        target = path / marker
        try:
            if target.is_symlink() or target.exists():
                return True
        except OSError:
            return True
    return False


def _validate_git_dir(dir_path: Path, dir_label: str) -> tuple[bool, Optional[str]]:
    """Validate that dir_path exists, is a directory, and is readable (R7)."""
    try:
        if dir_path.is_symlink() and not dir_path.exists():
            return False, f"{dir_label} is invalid: dangling symlink: {dir_path}"
        if not dir_path.is_dir():
            return False, f"{dir_label} is invalid: not a directory: {dir_path}"
        _ = list(dir_path.iterdir())
        return True, None
    except (OSError, UnicodeDecodeError) as exc:
        return False, f"{dir_label} unreadable ({exc}): {dir_path}"


def _validate_full_git_dir(gitdir: Path, label: str) -> tuple[bool, Optional[str]]:
    """Validate that gitdir has required files and dirs for a working repository (R7, R10).

    Required:
    - HEAD: regular file, valid ref or hex sha (R1, R7, R10)
    - objects: directory, readable (R7)
    - refs: directory, readable (R7)
    - config: regular file, valid syntax (R7, R10)
    Optional:
    - packed-refs: if present, must be valid syntax (R10)
    """
    ok, err = _validate_git_dir(gitdir, label)
    if not ok:
        return False, err

    ok, err = _validate_git_head_file(gitdir / "HEAD", f"{label} HEAD")
    if not ok:
        return False, err

    ok, err = _validate_git_dir(gitdir / "objects", f"{label} objects")
    if not ok:
        return False, err

    ok, err = _validate_git_dir(gitdir / "refs", f"{label} refs")
    if not ok:
        return False, err

    config_path = gitdir / "config"
    if not (config_path.exists() or config_path.is_symlink()):
        return False, f"{label} is not a git directory: missing config file: {config_path}"
    ok, err = _validate_git_config_file(config_path, f"{label} config")
    if not ok:
        return False, err

    packed_refs = gitdir / "packed-refs"
    if packed_refs.exists() or packed_refs.is_symlink():
        ok, err = _validate_git_packed_refs_file(packed_refs, f"{label} packed-refs")
        if not ok:
            return False, err

    return True, None
