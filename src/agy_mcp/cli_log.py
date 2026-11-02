"""Read agy's ``cli.log`` and extract soft tool-denial records (SPEC section 15).

Owns the two observed denial-line formats and the bounded, redaction-friendly
tail read used by status and finalization. It must never read host security
paths, execute ``agy``, or treat log text as trusted instructions; extracted
fields are data and command text is wrapped by callers/envelopes.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from agy_mcp.models import truncate_tail, wrap_untrusted_text
from agy_mcp.util import strict_uuid

# Real cli.logs grow without bound and are attacker-influenced; a 2 MB tail is
# enough for recent denial lines while keeping status calls cheap and bounded.
MAX_CLI_LOG_READ_BYTES = 2 * 1024 * 1024  # 2 MB cap
# "Created conversation <uuid>" is logged near the start of a run, before the
# log can grow past any reasonable tail window. 1 MB is enough for startup.
MAX_CLI_LOG_HEAD_BYTES = 1024 * 1024  # 1 MB cap
# Prefix fingerprint length used to tell an appended generation from a
# truncated/rewritten one without re-reading the whole (unbounded) log.
CLI_LOG_ANCHOR_BYTES = 4096

_UUID_PART = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# Real agy log lines are prefixed by the glog header, e.g.
#   I1025 12:03:54.313882       1 server.go:1225] Created conversation <uuid>
#   I1025 12:05:27.682767       1 session.go:156] Print mode: resuming conversation ...
# Anchoring the whole line stops agent-written text (a file listing, a reply
# echo, a fabricated "Created conversation ..." line) from being accepted.
_REAL_LOG_PREFIX = (
    r"^[IWEF]\d{4}\s+\d{2}:\d{2}:\d{2}\.\d{6}\s+\d+\s+[A-Za-z0-9_./-]+\.go:\d+\]\s+"
)
CREATED_CONVERSATION_RE = re.compile(
    _REAL_LOG_PREFIX + rf"Created conversation\s+({_UUID_PART})\b", re.MULTILINE
)
RESUMING_CONVERSATION_RE = re.compile(
    _REAL_LOG_PREFIX + rf"Print mode: resuming conversation\s+({_UUID_PART})\b",
    re.MULTILINE,
)


@dataclass
class Denial:
    """Record of a tool denial extracted from agy CLI log."""

    step: int
    tool: str
    command: Optional[str] = None
    raw_line: str = ""
    subagent_id: Optional[str] = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the envelope shape, omitting an empty subagent id."""
        d = {
            "step": self.step,
            "tool": self.tool,
            "command": self.command,
            "raw_line": self.raw_line,
        }
        if self.subagent_id:
            d["subagent_id"] = self.subagent_id
        return d


def read_cli_log_tail(log_path: Path, max_bytes: int = MAX_CLI_LOG_READ_BYTES) -> str:
    """Read at most the trailing max_bytes of a log file safely."""
    p = Path(log_path)
    if not p.is_file():
        return ""
    try:
        size = p.stat().st_size
        with open(p, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            raw = f.read(max_bytes)
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return ""


def read_cli_log_head(log_path: Path, max_bytes: int = MAX_CLI_LOG_HEAD_BYTES) -> str:
    """Read at most the leading max_bytes of a log file safely.

    Early lines such as "Created conversation <uuid>" are logged at startup and
    would be lost from a tail-only read once the log grows.
    """
    p = Path(log_path)
    if not p.is_file():
        return ""
    try:
        with open(p, "rb") as f:
            raw = f.read(max_bytes)
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return ""


def capture_cli_log_anchor(log_path: Path) -> tuple[int, Optional[str]]:
    """Record where the next generation starts in a reused ``cli.log``.

    Returns ``(size, prefix_sha256)``. The size is the append offset; the hash of
    the first ``CLI_LOG_ANCHOR_BYTES`` bytes lets a later scan tell an appended
    generation from a truncated/rewritten one even when the new file has already
    grown past the old size. A missing file anchors at 0 with an empty hash.
    """
    p = Path(log_path)
    try:
        size = p.stat().st_size if p.is_file() else 0
    except OSError:
        return 0, None
    if size == 0:
        return 0, hashlib.sha256(b"").hexdigest()
    try:
        with open(p, "rb") as f:
            prefix = f.read(min(size, CLI_LOG_ANCHOR_BYTES))
    except OSError:
        return size, None
    return size, hashlib.sha256(prefix).hexdigest()


def cli_log_generation_start(
    log_path: Path, offset: int, prefix_sha256: Optional[str] = None
) -> int:
    """Return the first byte of the current generation in a reused cli.log.

    ``offset`` is used when the log was appended (its anchored prefix is
    unchanged); byte 0 is used when the file is shorter than the anchor or its
    prefix changed, which means agy truncated/rewrote it. Returns -1 when the
    log does not exist.
    """
    p = Path(log_path)
    if not p.is_file():
        return -1
    try:
        size = p.stat().st_size
        start = max(0, int(offset))
        if size < start:
            return 0
        if start > 0 and prefix_sha256:
            prefix_len = min(start, CLI_LOG_ANCHOR_BYTES)
            with open(p, "rb") as f:
                current_prefix = f.read(prefix_len)
            if hashlib.sha256(current_prefix).hexdigest() != prefix_sha256:
                return 0
        return start
    except OSError:
        return -1


def read_cli_log_since(
    log_path: Path,
    offset: int,
    prefix_sha256: Optional[str] = None,
    max_bytes: int = MAX_CLI_LOG_HEAD_BYTES,
) -> str:
    """Read bounded bytes written after ``offset`` in a per-job ``cli.log``.

    agy is not known to append or truncate its log when a job is resumed. The
    generation start is resolved by :func:`cli_log_generation_start`; only
    ``max_bytes`` from that point are read.
    """
    p = Path(log_path)
    if not p.is_file():
        return ""
    try:
        size = p.stat().st_size
        start = cli_log_generation_start(p, offset, prefix_sha256)
        if start < 0 or size <= start:
            return ""
        with open(p, "rb") as f:
            f.seek(start)
            raw = f.read(max(0, max_bytes))
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return ""


def extract_conversation_id_from_cli_log(text: str) -> Optional[str]:
    """Extract a strict UUID from agy's two conversation log lines.

    Precedence: "Created conversation <uuid>" (fresh run) before
    "Print mode: resuming conversation <uuid>" (resumed run). Anything that is
    not a full UUID is ignored; the old loose regex could capture the word
    "using" from "Conversation using project ID: ...".
    """
    created: Optional[str] = None
    resuming: Optional[str] = None
    for line in text.splitlines():
        if created is None:
            match_created = CREATED_CONVERSATION_RE.search(line)
            if match_created:
                created = strict_uuid(match_created.group(1))
        if resuming is None:
            match_resuming = RESUMING_CONVERSATION_RE.search(line)
            if match_resuming:
                resuming = strict_uuid(match_resuming.group(1))
        if created is not None:
            break
    return created or resuming


def extract_resuming_conversation_id_from_cli_log(text: str) -> Optional[str]:
    """Return only the UUID from agy's real ``resuming conversation`` line.

    Used to decide whether a resumed generation actually continued the UUID the
    wrapper passed, so a fresh ``Created conversation`` line is deliberately not
    considered here.
    """
    for line in text.splitlines():
        match = RESUMING_CONVERSATION_RE.search(line)
        if match:
            cid = strict_uuid(match.group(1))
            if cid is not None:
                return cid
    return None


# Real masked cli.log line (smoke evidence JOB 1 line 152, JOB 2 line 128):
# I1026 03:06:52.040021       1 model_resolver.go:155] model alias
# "gemini-3.8-flash" resolved to "gemini-3.8-flash-high"
MODEL_ALIAS_RE = re.compile(r'model alias "([^"]+)" resolved to "([^"]+)"')


def extract_model_resolved(text: str) -> Optional[str]:
    """Return the model the real CLI resolved to, or None when no alias line exists.

    The CLI may log the same resolution several times; the first match wins.
    A missing or unrelated line must stay None so the envelope never invents a
    resolved model.
    """
    for line in text.splitlines():
        match = MODEL_ALIAS_RE.search(line)
        if match:
            resolved = match.group(2).strip()
            if resolved:
                return resolved
    return None


def extract_denials_from_cli_log(text: str) -> list[Denial]:
    """Extract tool denial records using the real CLI log formats."""
    denials: list[Denial] = []
    pattern_direct = re.compile(
        r'Print mode: soft-denying tool confirmation "([^"]+)" at step (\d+)'
    )
    pattern_subagent = re.compile(
        r"addFromDiff: print mode, soft-denying:\s*([0-9a-f-]+)\s+step\s+(\d+)"
    )
    for line in text.splitlines():
        match_direct = pattern_direct.search(line)
        if match_direct:
            denials.append(
                Denial(
                    step=int(match_direct.group(2)),
                    tool=match_direct.group(1),
                    command=None,
                    raw_line=line.strip(),
                )
            )
            continue
        match_subagent = pattern_subagent.search(line)
        if match_subagent:
            denials.append(
                Denial(
                    step=int(match_subagent.group(2)),
                    tool="subagent",
                    command=None,
                    raw_line=line.strip(),
                    subagent_id=match_subagent.group(1),
                )
            )
    return denials


def format_denied_commands(
    denials: list[Any],
    max_entries: int = 20,
    max_command_bytes: int = 500,
) -> list[dict[str, Any]]:
    """Cap to max_entries, 500 bytes per command, wrapping command in untrusted wrapper."""
    formatted: list[dict[str, Any]] = []
    for d in denials[:max_entries]:
        if isinstance(d, Denial):
            item = d.to_dict()
        elif hasattr(d, "to_dict"):
            item = d.to_dict()
        else:
            item = dict(d)

        cmd = item.get("command")
        if cmd is not None:
            capped = truncate_tail(str(cmd), max_command_bytes)
            item["command"] = wrap_untrusted_text(capped)
        formatted.append(item)
    return formatted
