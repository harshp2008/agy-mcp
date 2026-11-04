"""Conservative line-span edits of ``config.toml`` for the account CLI (D117).

Owns the file-lock/backup/atomic-write path and the text surgery that adds,
removes or flips exactly one block without re-serializing the whole file. A
general TOML round-trip library would rewrite comments and unrelated keys, so
this module deliberately edits line spans and then re-validates the result with
the real loader. It must never write without a timestamped backup, never write
without the file lock, and never print a full email from an unrelated block.
"""

from __future__ import annotations

import fcntl
import os
import re
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from agy_mcp.accounts.errors import AccountError

MARKER_PREFIX = "# agy-mcp:account "
_MARKER_RE = re.compile(r"^#\s*agy-mcp:account\s+([a-z0-9][a-z0-9-]{0,31})\s+(begin|end)\s*$")
_HEADER_RE = re.compile(r"^\[\[?\s*([A-Za-z0-9_.-]+)\s*\]\]?\s*(?:#.*)?$")
_ACCOUNTS_HEADER_RE = re.compile(r"^\[\[\s*accounts\s*\]\]\s*(?:#.*)?$")
_DEFAULT_HEADER_RE = re.compile(r"^\[\s*default_account\s*\]\s*(?:#.*)?$")
_ENABLED_LINE_RE = re.compile(r"^(\s*enabled\s*=\s*)(true|false)(\s*(?:#.*)?)$")
_NAME_LINE_RE = re.compile(r"^\s*name\s*=")
_HOME_LINE_RE = re.compile(r"^\s*home\s*=")
# A dotted/inline definition of the default table cannot be edited safely line
# by line; the CLI refuses instead of rewriting it.
_DOTTED_DEFAULT_RE = re.compile(r"^\s*default_account\s*[.=]")


@dataclass(frozen=True)
class AccountBlock:
    """One located ``[[accounts]]`` block.

    ``start`` is the character offset of the first line of the span (the begin
    marker for a CLI-written block, otherwise the ``[[accounts]]`` header) and
    ``end`` is the offset one past its last character. ``marked`` says the span
    includes the ``# agy-mcp:account <name> begin/end`` marker pair.
    """

    name: str
    start: int
    end: int
    marked: bool

    def text(self, text: str) -> str:
        """Return this span's exact substring."""
        return text[self.start : self.end]


@dataclass(frozen=True)
class WriteResult:
    """What a config write did (or would do, for a dry run)."""

    path: Path
    backup_path: Path
    changed: bool
    dry_run: bool


@dataclass(frozen=True)
class _Line:
    start: int
    end: int
    body: str


def resolve_config_path(explicit: Optional[Path | str] = None) -> Path:
    """Return the config.toml path the loader would use.

    Mirrors :func:`agy_mcp.config.load_config`: an explicit path wins, then
    ``$XDG_CONFIG_HOME/agy-mcp/config.toml``, then
    ``~/.config/agy-mcp/config.toml``. It does not create or read the file.
    """
    if explicit is not None:
        return Path(explicit)
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config) if xdg_config else Path.home() / ".config"
    return base / "agy-mcp" / "config.toml"


def read_config_text(path: Path) -> str:
    """Read a config file as text, treating a missing file as an empty config."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise AccountError(
            f"cannot read config.toml: {exc}",
            code="account_cli_config_error",
        ) from exc


def _lines(text: str) -> list[_Line]:
    lines: list[_Line] = []
    offset = 0
    for raw in text.splitlines(keepends=True):
        body = raw[:-1] if raw.endswith("\n") else raw
        if body.endswith("\r"):
            body = body[:-1]
        lines.append(_Line(start=offset, end=offset + len(raw), body=body))
        offset += len(raw)
    return lines


def _table_headers(text: str) -> list[int]:
    """Return line indexes that start a TOML table header.

    A conservative scanner: a header-looking line inside a multi-line string is
    not a header. Blocks the CLI writes are simple, but a hand-written entry may
    contain a multi-line array, so the scanner tracks triple-quoted strings.
    """
    headers: list[int] = []
    in_multiline: Optional[str] = None
    for index, line in enumerate(_lines(text)):
        body = line.body
        if in_multiline is not None:
            if body.count(in_multiline) % 2 == 1:
                in_multiline = None
            continue
        stripped = body.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for token in ('"""', "'''"):
            if body.count(token) % 2 == 1:
                in_multiline = token
                break
        if in_multiline is not None:
            continue
        if stripped.startswith("[") and _HEADER_RE.match(stripped):
            headers.append(index)
    return headers


def _span_end(lines: list[_Line], headers: list[int], header_index: int) -> int:
    """Return the offset one past the table that starts at ``header_index``."""
    following = [header for header in headers if header > header_index]
    if following:
        return lines[following[0]].start
    return lines[-1].end


def _parse_block_body(text: str, end: int, header_index: int) -> dict[str, Any]:
    """Parse the key/value body of one ``[[accounts]]`` block with tomllib."""
    lines = _lines(text)
    header_line = lines[header_index]
    body = text[header_line.end : end]
    try:
        parsed = tomllib.loads(body)
    except tomllib.TOMLDecodeError as exc:
        raise AccountError(
            f"cannot parse the [[accounts]] block: {exc}",
            code="account_cli_block_corrupt",
        ) from exc
    if not isinstance(parsed, dict):
        raise AccountError(
            "the [[accounts]] block body is not a TOML table",
            code="account_cli_block_corrupt",
        )
    return parsed


def _marked_spans(lines: list[_Line]) -> list[AccountBlock]:
    """Return every complete begin/end marker span, refusing corrupt pairs."""
    begins: dict[str, int] = {}
    spans: list[AccountBlock] = []
    for index, line in enumerate(lines):
        match = _MARKER_RE.match(line.body.strip())
        if not match:
            continue
        name, kind = match.group(1), match.group(2)
        if kind == "begin":
            if name in begins:
                raise AccountError(
                    f"ambiguous config: more than one begin marker for account '{name}'",
                    code="account_cli_block_ambiguous",
                )
            begins[name] = index
        else:
            if name not in begins:
                raise AccountError(
                    f"corrupt config: end marker for account '{name}' has no begin",
                    code="account_cli_block_corrupt",
                )
            begin_index = begins.pop(name)
            start_offset = lines[begin_index].start
            end_offset = line.end
            expected_begin = f"{MARKER_PREFIX}{name} begin"
            if lines[begin_index].body.strip() != expected_begin:
                raise AccountError(
                    f"corrupt config: unexpected marker form for account '{name}'",
                    code="account_cli_block_corrupt",
                )
            spans.append(
                AccountBlock(name=name, start=start_offset, end=end_offset, marked=True)
            )
    if begins:
        names = ", ".join(sorted(begins))
        raise AccountError(
            f"corrupt config: begin marker without an end for account(s): {names}",
            code="account_cli_block_corrupt",
        )
    return spans


def _marked_payload(span: AccountBlock, lines: list[_Line]) -> tuple[int, int]:
    """Return the header line index and the header's character start for a span."""
    for index, line in enumerate(lines):
        if line.start < span.start or line.start >= span.end:
            continue
        if _ACCOUNTS_HEADER_RE.match(line.body.strip()):
            return index, line.start
    raise AccountError(
        f"corrupt config: marker block for account '{span.name}' has no [[accounts]] header",
        code="account_cli_block_corrupt",
    )


def find_account_block(text: str, name: str) -> Optional[AccountBlock]:
    """Locate the single ``[[accounts]]`` block for ``name``, or return None.

    Marker blocks (written by this CLI) take precedence; a hand-written block is
    found by parsing its ``name`` key. More than one matching block, or a marker
    whose payload names a different account, raises
    ``account_cli_block_ambiguous``/``account_cli_block_corrupt`` rather than
    editing the wrong span.
    """
    lines = _lines(text)
    marked = _marked_spans(lines)
    for span in marked:
        if span.name != name:
            continue
        header_index, _ = _marked_payload(span, lines)
        body = _parse_block_body(text, span.end, header_index)
        if body.get("name") != name:
            raise AccountError(
                f"corrupt config: marker says account '{name}' but the block says "
                f"'{body.get('name')}'",
                code="account_cli_block_ambiguous",
            )
        return span

    headers = _table_headers(text)
    matches: list[AccountBlock] = []
    for header_index in headers:
        line = lines[header_index]
        if not _ACCOUNTS_HEADER_RE.match(line.body.strip()):
            continue
        span = AccountBlock(
            name=name,
            start=line.start,
            end=_span_end(lines, headers, header_index),
            marked=False,
        )
        for marked_span in marked:
            if span.start >= marked_span.start and span.start < marked_span.end:
                break
        else:
            body = _parse_block_body(text, span.end, header_index)
            if body.get("name") == name:
                matches.append(span)
    if len(matches) > 1:
        raise AccountError(
            f"ambiguous config: more than one [[accounts]] block declares '{name}'",
            code="account_cli_block_ambiguous",
        )
    return matches[0] if matches else None


def remove_account_block(text: str, name: str) -> tuple[str, AccountBlock]:
    """Remove exactly one account block and return the new text and its span.

    Only the span's own characters are removed; every other byte (comments,
    blank lines, unrelated keys) is preserved. A missing block raises
    ``account_cli_block_not_found``.
    """
    span = find_account_block(text, name)
    if span is None:
        raise AccountError(
            f"account '{name}' has no [[accounts]] block in config.toml",
            code="account_cli_block_not_found",
        )
    return text[: span.start] + text[span.end :], span


def append_account_block(text: str, block: str) -> str:
    """Append a rendered block, adding one separating blank line when needed."""
    prefix = text
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    if prefix and not prefix.endswith("\n\n"):
        prefix += "\n"
    return prefix + block


def replace_account_block(text: str, block: str, span: AccountBlock) -> str:
    """Replace one block span with ``block`` verbatim."""
    return text[: span.start] + block + text[span.end :]


def _line_offsets(text: str, start: int, end: int) -> list[_Line]:
    return [line for line in _lines(text) if line.start >= start and line.start < end]


def _replace_enabled_line(line: str, enabled: bool) -> str:
    match = _ENABLED_LINE_RE.match(line)
    if match is None:
        raise AccountError(
            "config line changed while editing 'enabled'",
            code="account_cli_config_changed",
        )
    return f"{match.group(1)}{'true' if enabled else 'false'}{match.group(3)}"


def _set_enabled_in_span(
    text: str, span: AccountBlock, enabled: bool, *, header_fallback: bool = False
) -> str:
    """Flip one span's top-level ``enabled`` value, inserting the line if absent."""
    lines = _line_offsets(text, span.start, span.end)
    enabled_lines = [line for line in lines if _ENABLED_LINE_RE.match(line.body)]
    if len(enabled_lines) > 1:
        raise AccountError(
            "config block has more than one 'enabled' line",
            code="account_cli_block_corrupt",
        )
    if enabled_lines:
        line = enabled_lines[0]
        raw = text[line.start : line.end]
        newline = "\r\n" if raw.endswith("\r\n") else ("\n" if raw.endswith("\n") else "")
        new_line = _replace_enabled_line(line.body, enabled) + newline
        return text[: line.start] + new_line + text[line.end :]

    anchor = None
    for line in lines:
        if _HOME_LINE_RE.match(line.body):
            anchor = line
    if anchor is None:
        for line in lines:
            if _NAME_LINE_RE.match(line.body):
                anchor = line
                break
    if anchor is None and header_fallback:
        # An empty [default_account] table has no key to anchor against; append
        # the key directly after its header.
        anchor = lines[0]
    if anchor is None:
        raise AccountError(
            "config block has no 'home' line to anchor the 'enabled' key",
            code="account_cli_block_corrupt",
        )
    indent = " " * (len(anchor.body) - len(anchor.body.lstrip()))
    newline = "\r\n" if text[anchor.start : anchor.end].endswith("\r\n") else "\n"
    inserted = f"{indent}enabled = {'true' if enabled else 'false'}{newline}"
    return text[: anchor.end] + inserted + text[anchor.end :]


def set_account_enabled(text: str, name: str, enabled: bool) -> str:
    """Flip one declared account's ``enabled`` flag, preserving every other byte."""
    span = find_account_block(text, name)
    if span is None:
        raise AccountError(
            f"account '{name}' has no [[accounts]] block in config.toml",
            code="account_cli_block_not_found",
        )
    return _set_enabled_in_span(text, span, enabled)


def set_default_enabled(text: str, enabled: bool) -> str:
    """Set ``[default_account] enabled``, creating the table when it is absent."""
    lines = _lines(text)
    headers = _table_headers(text)
    default_headers = [
        index for index in headers if _DEFAULT_HEADER_RE.match(lines[index].body.strip())
    ]
    if len(default_headers) > 1:
        raise AccountError(
            "ambiguous config: more than one [default_account] table",
            code="account_cli_block_ambiguous",
        )
    if default_headers:
        header_index = default_headers[0]
        line = lines[header_index]
        span = AccountBlock(
            name="default",
            start=line.start,
            end=_span_end(lines, headers, header_index),
            marked=False,
        )
        return _set_enabled_in_span(text, span, enabled, header_fallback=True)

    if any(_DOTTED_DEFAULT_RE.match(line.body) for line in lines):
        raise AccountError(
            "config defines [default_account] with dotted/inline syntax; edit it by hand",
            code="account_cli_block_ambiguous",
        )
    block = f"[default_account]\nenabled = {'true' if enabled else 'false'}\n"
    return append_account_block(text, block)


def render_account_block(
    name: str,
    home: str,
    pool: Optional[str] = None,
    max_concurrent_jobs: Optional[int] = None,
    expected_email_sha256: Optional[str] = None,
    enabled: bool = True,
) -> str:
    """Render one CLI-owned marker block in the documented key order."""
    lines = [
        f"{MARKER_PREFIX}{name} begin",
        "[[accounts]]",
        f"name = {_toml_string(name)}",
        f"home = {_toml_string(home)}",
    ]
    if pool is not None:
        lines.append(f"pool = {_toml_string(pool)}")
    lines.append(f"enabled = {'true' if enabled else 'false'}")
    if max_concurrent_jobs is not None:
        lines.append(f"max_concurrent_jobs = {int(max_concurrent_jobs)}")
    if expected_email_sha256 is not None:
        lines.append(f"expected_email_sha256 = {_toml_string(expected_email_sha256)}")
    lines.append(f"# agy-mcp:account {name} end")
    return "\n".join(lines) + "\n"


def _toml_string(value: str) -> str:
    """Quote one TOML basic string without adding a dependency."""
    escapes = {
        "\\": "\\\\",
        '"': '\\"',
        "\b": "\\b",
        "\t": "\\t",
        "\n": "\\n",
        "\f": "\\f",
        "\r": "\\r",
    }
    out: list[str] = ['"']
    for char in value:
        if char in escapes:
            out.append(escapes[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def validate_config_text(text: str) -> None:
    """Re-validate edited text with tomllib and the real config schema.

    Imported lazily so this module stays importable by tests that only exercise
    text surgery, and so a config.py change cannot create an import cycle at
    module load.
    """
    from agy_mcp.config import _validate_config_dict

    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise AccountError(
            f"edited config.toml is not valid TOML: {exc}",
            code="account_cli_config_error",
        ) from exc
    if not isinstance(raw, dict):
        raise AccountError(
            "edited config.toml root is not a table",
            code="account_cli_config_error",
        )
    try:
        _validate_config_dict(raw)
    except ValueError as exc:
        raise AccountError(
            f"edited config.toml does not validate: {exc}",
            code="account_cli_config_error",
        ) from exc


def _backup_path(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return path.with_name(f"{path.name}.{stamp}.bak")


def _write_all(fd: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        offset += os.write(fd, data[offset:])


def _fsync_dir(path: Path) -> None:
    try:
        dir_fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def write_config_text(
    path: Path,
    new_text: str,
    *,
    expected_text: str,
    dry_run: bool,
    file_mode: int = 0o600,
) -> WriteResult:
    """Atomically replace ``config.toml`` after backing it up under a file lock.

    ``expected_text`` is what the caller read before editing; if the file changed
    in the meantime the write is refused with ``account_cli_config_changed``
    rather than clobbering another edit. A dry run validates the text and returns
    the backup path it *would* write, without touching the filesystem.
    """
    validate_config_text(new_text)
    if dry_run:
        return WriteResult(path=path, backup_path=_backup_path(path), changed=True, dry_run=True)

    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise AccountError(
            f"cannot create the config directory: {exc}",
            code="account_cli_write_failed",
        ) from exc

    lock_path = path.with_name(f".{path.name}.lock")
    try:
        lock_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise AccountError(
            f"cannot open the config lock: {exc}",
            code="account_cli_write_failed",
        ) from exc
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            if path.exists():
                current = path.read_text(encoding="utf-8")
            else:
                current = ""
            if current != expected_text:
                raise AccountError(
                    "config.toml changed since it was read; re-run the command",
                    code="account_cli_config_changed",
                )
            backup = _backup_path(path)
            try:
                backup_fd = os.open(
                    str(backup),
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                try:
                    _write_all(backup_fd, current.encode("utf-8"))
                    os.fsync(backup_fd)
                finally:
                    os.close(backup_fd)
            except OSError as exc:
                raise AccountError(
                    f"cannot write the config backup: {exc}",
                    code="account_cli_write_failed",
                ) from exc

            temp_fd, temp_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
            )
            try:
                _write_all(temp_fd, new_text.encode("utf-8"))
                os.fsync(temp_fd)
                os.fchmod(temp_fd, file_mode)
            finally:
                os.close(temp_fd)
            try:
                os.replace(temp_name, path)
            except OSError:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
                raise
            _fsync_dir(path.parent)
            return WriteResult(path=path, backup_path=backup, changed=True, dry_run=False)
        except OSError as exc:
            raise AccountError(
                f"cannot write config.toml: {exc}",
                code="account_cli_write_failed",
            ) from exc
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


