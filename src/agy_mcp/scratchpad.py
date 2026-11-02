"""Wrapper-owned ``.agy`` scratchpad: seed files plus hardened untrusted readers.

The agent owns ``.agy/PLAN.md``, ``NOTES.md``, ``LOG.md`` and ``REPORT.md``.
Everything it writes there is untrusted data: it may contain shell syntax,
prompt-injection text or a symlink to a secret. This module therefore seeds the
files at launch and reads them back with no-follow, regular-file-only,
worktree-confined, size-capped opens.

It must never execute, eval or interpolate agent text, and it must never follow
a symlink. All callers receive plain strings or a reasoned skip record.
"""

import errno
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

MAX_CHECKLIST_ITEMS = 30
MAX_CHECKLIST_ITEM_CHARS = 200

# Per-file caps. PLAN/NOTES/LOG are capped on read and on the retained copy;
# REPORT is smaller because its text is returned inside the result envelope.
MAX_PLAN_BYTES = 64 * 1024
MAX_NOTES_BYTES = 64 * 1024
MAX_LOG_BYTES = 64 * 1024
MAX_REPORT_READ_BYTES = 64 * 1024
MAX_REPORT_BYTES = 4 * 1024
MAX_STATE_NOTES_BYTES = 2 * 1024
MAX_PLAN_ITEM_TITLE_CHARS = 200

SCRATCHPAD_FILES: tuple[str, ...] = ("PLAN.md", "NOTES.md", "LOG.md", "REPORT.md")

DEFAULT_CHECKLIST: tuple[str, ...] = (
    "Complete the task described in the prompt",
    "Fill in .agy/REPORT.md honestly",
)

NOTES_HEADER = "working memory; keep decisions and findings here"

REPORT_TEMPLATE = """# REPORT

## Done

## Not done / skipped
none

## Open issues
none

## Files changed

## How verified
"""


@dataclass(frozen=True)
class ScratchFile:
    """Result of a hardened scratchpad read.

    ``text`` is ``None`` when the file was refused or missing; ``reason`` then
    names why (a short stable code, never a host path).
    """

    name: str
    text: Optional[str] = None
    reason: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.text is not None


_PLAN_ITEM_RE = re.compile(r"^\s*[-*+]\s*\[([ xX])\]\s*(.*?)\s*$")
_PLAN_NUMBER_PREFIX_RE = re.compile(r"^\d+[.)]\s*")


def _single_line(value: object) -> str:
    """Collapse any whitespace (including newlines) into single spaces."""
    return " ".join(str(value).split())


def _cap_bytes(text: str, max_bytes: int) -> str:
    """Truncate ``text`` to at most ``max_bytes`` UTF-8 bytes."""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    return raw[:max_bytes].decode("utf-8", errors="ignore")


def cap_text_bytes(text: str, max_bytes: int) -> str:
    """Public byte-cap helper for envelope fields built from agent text."""
    return _cap_bytes(text, max_bytes)


def tail_text_bytes(text: str, max_bytes: int) -> str:
    """Keep only the last ``max_bytes`` UTF-8 bytes of ``text``."""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    return raw[-max_bytes:].decode("utf-8", errors="ignore")


def report_claims_tests_passed(text: str, phrases: list[str]) -> bool:
    """True when ``text`` contains any configured claim phrase (case-insensitive).

    Matching is literal substring matching, never regex: phrases come from
    config and agent text is untrusted, so a catastrophic regex is impossible.
    Markdown heading lines are ignored because the REPORT.md template's own
    section title is "How verified"; only body prose can be a claim.
    """
    body = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    ).casefold()
    return any(
        phrase.casefold() in body
        for phrase in phrases
        if isinstance(phrase, str) and phrase.strip()
    )


def normalize_checklist(checklist: Optional[list[str]]) -> list[str]:
    """Sanitize checklist items to single lines, dropping empty entries.

    Length/count limits are enforced by the caller (``JobManager.start``) so it
    can return a coded error instead of silently truncating.
    """
    if not checklist:
        return []
    items: list[str] = []
    for raw in checklist:
        item = _single_line(raw)
        if item:
            items.append(item)
    return items


def build_plan_content(title: str, checklist: Optional[list[str]] = None) -> str:
    """Build the numbered checkbox PLAN.md body (SPEC section 9, D62)."""
    items = normalize_checklist(checklist) or list(DEFAULT_CHECKLIST)
    lines = [f"# Plan: {_single_line(title)}", ""]
    for index, item in enumerate(items, start=1):
        lines.append(f"- [ ] {index}. {item}")
    return _cap_bytes("\n".join(lines) + "\n", MAX_PLAN_BYTES)


def write_metadata_file(path: Path, content: str, mode: int = 0o600) -> None:
    """Write one wrapper metadata file without following a symlink at ``path``.

    An agent can replace ``.agy/<name>`` (or ``.agy`` itself) with a symlink to
    a secret. Metadata writes must therefore open with ``O_NOFOLLOW`` and fail
    closed instead of writing through the link.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, mode)
    try:
        os.fchmod(fd, mode)
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)


def seed_scratchpad(
    agy_dir: Path,
    title: str,
    checklist: Optional[list[str]] = None,
) -> None:
    """Seed PLAN/NOTES/LOG/REPORT inside an existing ``.agy`` directory.

    The directory itself is created and chmod'd by the caller; this function
    only writes the four capped files and refuses a symlinked directory.
    """
    agy_dir = Path(agy_dir)
    if agy_dir.is_symlink():
        raise RuntimeError("Refusing to seed .agy: it is a symlink")

    write_metadata_file(agy_dir / "PLAN.md", build_plan_content(title, checklist))
    write_metadata_file(
        agy_dir / "NOTES.md",
        _cap_bytes(f"# NOTES\n\n{NOTES_HEADER}\n", MAX_NOTES_BYTES),
    )
    write_metadata_file(
        agy_dir / "LOG.md",
        _cap_bytes("# LOG\n\n", MAX_LOG_BYTES),
    )
    write_metadata_file(
        agy_dir / "REPORT.md",
        _cap_bytes(REPORT_TEMPLATE, MAX_REPORT_BYTES),
    )


def parse_plan_text(text: str) -> dict[str, object]:
    """Count plan checkboxes and collect unticked titles.

    Returns ``{"total": int, "done": int, "remaining": [str, ...]}``. Titles
    are capped and kept raw; envelope builders wrap them as untrusted text.
    """
    total = 0
    done = 0
    remaining: list[str] = []
    for line in text.splitlines():
        match = _PLAN_ITEM_RE.match(line)
        if not match:
            continue
        total += 1
        if match.group(1).lower() == "x":
            done += 1
            continue
        title = _PLAN_NUMBER_PREFIX_RE.sub("", match.group(2)).strip()
        remaining.append(_cap_bytes(title, MAX_PLAN_ITEM_TITLE_CHARS))
    return {"total": total, "done": done, "remaining": remaining}


def _no_follow_dir(agy_dir: Path) -> int:
    """Open ``.agy`` as a directory without following a symlink."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(agy_dir, flags)


def _fd_is_inside(fd: int, expected_dir: Path) -> bool:
    """Best-effort check that an open fd resolves inside ``expected_dir``.

    ``/proc/self/fd`` is Linux-specific; when it is unavailable the caller has
    already opened ``.agy`` with O_NOFOLLOW and the file by a single basename,
    so the confinement guarantee still holds.
    """
    proc_path = Path(f"/proc/self/fd/{fd}")
    try:
        resolved = Path(os.readlink(proc_path)).resolve(strict=True)
    except OSError:
        return True
    try:
        return resolved == expected_dir.resolve(strict=True) or resolved.is_relative_to(
            expected_dir.resolve(strict=True)
        )
    except OSError:
        return False


def read_scratchpad_file(
    worktree_path: Path,
    name: str,
    max_bytes: int,
) -> ScratchFile:
    """Read one agent-written ``.agy`` file under the security rule from SPEC §9.

    Refuses (returning a short reason) when ``name`` is not one of the four
    known files, the worktree or ``.agy`` is missing, ``.agy`` or the file is a
    symlink, the target is not a regular file, the file is larger than
    ``max_bytes``, it resolves outside ``.agy``, or it contains NUL bytes.
    """
    if name not in SCRATCHPAD_FILES:
        return ScratchFile(name, reason="invalid_name")
    try:
        worktree = Path(worktree_path).resolve(strict=True)
    except (OSError, RuntimeError):
        return ScratchFile(name, reason="worktree_missing")
    agy_dir = worktree / ".agy"

    try:
        dir_fd = _no_follow_dir(agy_dir)
    except FileNotFoundError:
        return ScratchFile(name, reason="missing")
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            return ScratchFile(name, reason="symlink")
        return ScratchFile(name, reason="unreadable")

    try:
        if not _fd_is_inside(dir_fd, agy_dir):
            return ScratchFile(name, reason="outside")
        open_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            file_fd = os.open(name, open_flags, dir_fd=dir_fd)
        except FileNotFoundError:
            return ScratchFile(name, reason="missing")
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                return ScratchFile(name, reason="symlink")
            return ScratchFile(name, reason="unreadable")

        try:
            stat_result = os.fstat(file_fd)
            if not stat.S_ISREG(stat_result.st_mode):
                return ScratchFile(name, reason="not_regular")
            if not _fd_is_inside(file_fd, agy_dir):
                return ScratchFile(name, reason="outside")
            if stat_result.st_size > max_bytes:
                return ScratchFile(name, reason="oversized")
            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining > 0:
                chunk = os.read(file_fd, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > max_bytes:
                return ScratchFile(name, reason="oversized")
        finally:
            os.close(file_fd)
    finally:
        os.close(dir_fd)

    if b"\x00" in data:
        return ScratchFile(name, reason="binary")
    return ScratchFile(name, text=data.decode("utf-8", errors="replace"))


def read_plan(worktree_path: Path) -> dict[str, object]:
    """Read PLAN.md under the security rule and return ``{total, done, remaining}``.

    A missing, symlinked, oversized or binary PLAN.md yields an empty plan
    rather than raising; the launch side already treats provisioning failure as
    fatal, and a later tamper must not crash finalization.
    """
    result = read_scratchpad_file(worktree_path, "PLAN.md", MAX_PLAN_BYTES)
    if result.text is None:
        return {"total": 0, "done": 0, "remaining": []}
    return parse_plan_text(result.text)


_COPY_LIMITS: dict[str, int] = {
    "PLAN.md": MAX_PLAN_BYTES,
    "NOTES.md": MAX_NOTES_BYTES,
    "LOG.md": MAX_LOG_BYTES,
    "REPORT.md": MAX_REPORT_READ_BYTES,
}


def copy_to_jobdir(worktree_path: Path, job_dir: Path) -> tuple[list[str], list[str]]:
    """Retain the four scratchpad files under ``<job_dir>/scratchpad/`` (D62).

    Uses the same hardened reads as every other ``.agy`` access: a symlinked,
    oversized, binary or missing source is skipped (reported by name only) and
    never copied. Returns ``(copied_names, skipped_names)``; no absolute paths.
    """
    dest = Path(job_dir) / "scratchpad"
    try:
        dest.mkdir(parents=True, exist_ok=True)
        os.chmod(dest, 0o700)
    except OSError:
        return [], list(SCRATCHPAD_FILES)

    copied: list[str] = []
    skipped: list[str] = []
    for name in SCRATCHPAD_FILES:
        result = read_scratchpad_file(worktree_path, name, _COPY_LIMITS[name])
        if result.text is None:
            skipped.append(name)
            continue
        try:
            write_metadata_file(dest / name, result.text)
        except OSError:
            skipped.append(name)
            continue
        copied.append(name)
    return copied, skipped


def format_plan_nudge(remaining: list[str]) -> str:
    """Build the trusted plan-nudge guidance with untrusted titles wrapped (D62)."""
    from agy_mcp.models import wrap_untrusted_text

    titles = [
        wrap_untrusted_text(_single_line(title)[:MAX_PLAN_ITEM_TITLE_CHARS])
        for title in list(remaining)[:30]
    ]
    joined = "\n".join(titles)
    return (
        f"{len(remaining)} plan items are unticked:\n{joined}\n"
        "Finish them, or explain in REPORT.md under 'Not done / skipped'."
    )


def format_state_block(
    commits: list[str],
    verify_result: str,
    remaining: list[str],
    notes: str,
    original_task_summary: Optional[str] = None,
    head_commit: Optional[str] = None,
    files_changed: Optional[list[str]] = None,
) -> str:
    """Build the trusted resume "state of the world" block (SPEC §14, D62).

    Headings are wrapper-authored and trusted. Every agent-derived value
    (commit subjects, plan titles, notes, changed paths) is individually wrapped
    as untrusted text, so nothing inside the block can be mistaken for an
    instruction.

    The optional context fields make a resume prompt self-contained even when no
    conversation id is available: the original task summary, current HEAD and
    the files already changed are restated by the wrapper.
    """
    from agy_mcp.models import wrap_untrusted_text

    context: list[str] = []
    if original_task_summary:
        context.append(
            "Original task summary: "
            + wrap_untrusted_text(_single_line(original_task_summary)[:300])
        )
    if head_commit:
        context.append(f"Current HEAD: {_single_line(head_commit)[:64]}")
    if files_changed is not None:
        changed = [str(path) for path in files_changed if str(path).strip()]
        context.append(f"Files changed: {len(changed)}")
        if changed:
            context.append(
                "\n".join(
                    wrap_untrusted_text(_single_line(path)[:300])
                    for path in changed[:20]
                )
            )

    lines: list[str] = []
    if context:
        lines.append("## Job Context (wrapper-provided)")
        lines.extend(context)
        lines.append("")
    lines.extend(["## Current State (wrapper-provided)", f"Commits so far: {len(commits)}"])
    if commits:
        lines.append(
            "\n".join(
                wrap_untrusted_text(_single_line(subject)[:300])
                for subject in list(commits)[:10]
            )
        )
    else:
        lines.append("none")

    lines.append(f"Last verification: {_single_line(verify_result) or 'not run'}")
    lines.append(f"Remaining plan items: {len(remaining)}")
    if remaining:
        lines.append(
            "\n".join(
                wrap_untrusted_text(_single_line(title)[:MAX_PLAN_ITEM_TITLE_CHARS])
                for title in list(remaining)[:30]
            )
        )
    else:
        lines.append("none")

    lines.append("Your notes so far (last 2 KB):")
    if notes:
        lines.append(wrap_untrusted_text(tail_text_bytes(notes, MAX_STATE_NOTES_BYTES)))
    else:
        lines.append("none")
    return "\n".join(lines)
