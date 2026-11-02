"""Pure shared helpers: slugs, environment, truncation, redaction, prompts.

Owns the child-process environment allowlist, byte-capped reads/truncation,
secret redaction, hashing and the standing-prompt formatting. It must never
perform network or process work, never import models at module load (the
dependency is lazy), and never pass a parent environment variable through
except the explicitly allowlisted keys.
"""

import hashlib
import os
import re
from pathlib import Path
from typing import Optional


def derive_branch_slug(title: str, max_length: int = 40) -> str:
    """Generate a clean git branch slug from a title string.

    Per SPEC section 9:
        slug = re.sub(r'[^a-z0-9-]+', '-', title.lower())[:40].strip('-') or 'job'
    """
    cleaned = re.sub(r"[^a-z0-9-]+", "-", title.lower())[:max_length].strip("-")
    return cleaned or "job"


def sanitize_absolute_paths(
    text: str,
    worktree_path: Optional[Path],
    home: Optional[Path],
) -> str:
    """Replace the job worktree path and home directory with placeholders.

    Real agy responses embed absolute ``file://`` links (smoke finding 9). Only
    whole path prefixes are replaced: a sibling such as ``<worktree>-backup`` or
    ``<worktree>2`` merely starts with the same text and must stay untouched.
    The worktree is replaced first so a worktree under HOME wins.
    """
    if not text:
        return text

    for value, replacement in ((worktree_path, "<worktree>"), (home, "~")):
        if value is None:
            continue
        prefix = str(value)
        # Degenerate roots would rewrite the entire document.
        if not prefix or prefix in ("/", "."):
            continue
        pattern = re.compile(re.escape(prefix) + r"(?![\w.-])")
        text = pattern.sub(replacement, text)
    return text


def env_allowlist(
    job_tmp_dir: Path,
    home: Optional[Path] = None,
    path: Optional[str] = None,
    disable_hooks: bool = True,
) -> dict[str, str]:
    """Construct the sanitized environment for child processes per SPEC section 10.

    Passes ONLY HOME, PATH, LANG=C.UTF-8, TMPDIR, and hardened GIT_CONFIG overrides.
    Never leaks parent process secrets or API keys.

    ``disable_hooks`` keeps the historical default (``core.hooksPath=/dev/null``)
    for every git invocation that cannot run a hook. The wrapper-owned commit path
    passes ``False`` so a repository's own commit-msg/pre-commit hooks are honoured
    instead of bypassed (no ``--no-verify``, no hooks override).
    """
    resolved_home = str(home or Path.home())
    resolved_path = path or os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")

    overrides: list[tuple[str, str]] = [("core.fsmonitor", "false")]
    if disable_hooks:
        overrides.append(("core.hooksPath", "/dev/null"))
    overrides.append(("core.pager", "cat"))

    # Build the dict from scratch (never copy os.environ): HOME/PATH/LANG/TMPDIR
    # plus git overrides are the only variables a child is allowed to inherit.
    env = {
        "HOME": resolved_home,
        "PATH": resolved_path,
        "LANG": "C.UTF-8",
        "TMPDIR": str(job_tmp_dir),
        "GIT_CONFIG_COUNT": str(len(overrides)),
    }
    for index, (key, value) in enumerate(overrides):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


def truncate_tail(text: str, max_bytes: int, notice: str = "\n[... truncated]") -> str:
    """Truncate text to at most max_bytes, keeping the head and discarding the tail.

    Safely aligns to UTF-8 code point boundaries to prevent character corruption.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text

    notice_bytes = notice.encode("utf-8")
    if len(notice_bytes) >= max_bytes:
        # Fallback if notice itself exceeds max_bytes
        return notice_bytes[:max_bytes].decode("utf-8", errors="ignore")

    target_bytes = max_bytes - len(notice_bytes)
    # Slicing at target_bytes and decoding with errors="ignore" safely drops
    # any broken multi-byte sequence at the slice boundary.
    head_bytes = encoded[:target_bytes]
    decoded_head = head_bytes.decode("utf-8", errors="ignore")
    return f"{decoded_head}{notice}"


def truncate_head(text: str, max_bytes: int, notice: str = "[truncated ...] \n") -> str:
    """Truncate text to at most max_bytes, discarding the head and keeping the tail.

    Safely aligns to UTF-8 code point boundaries to prevent character corruption.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text

    notice_bytes = notice.encode("utf-8")
    if len(notice_bytes) >= max_bytes:
        return notice_bytes[-max_bytes:].decode("utf-8", errors="ignore")

    target_bytes = max_bytes - len(notice_bytes)
    tail_bytes = encoded[-target_bytes:]
    decoded_tail = tail_bytes.decode("utf-8", errors="ignore")
    return f"{notice}{decoded_tail}"


def read_file_tail(
    path: Path,
    max_bytes: int,
    notice: str = "[TRUNCATED: output exceeded size limit]\n",
) -> str:
    """Read at most the trailing max_bytes of a file safely, prepending notice if truncated."""
    p = Path(path)
    if not p.is_file():
        return ""
    try:
        size = p.stat().st_size
        with open(p, "rb") as f:
            if size > max_bytes:
                notice_bytes = len(notice.encode("utf-8"))
                read_bytes = max(0, max_bytes - notice_bytes)
                f.seek(size - read_bytes)
                raw = f.read(read_bytes)
                return f"{notice}{raw.decode('utf-8', errors='replace')}"
            raw = f.read(max_bytes)
            return raw.decode("utf-8", errors="replace")
    except OSError:
        return ""


def read_file_since(
    path: Path,
    offset: int,
    max_bytes: int,
    notice: str = "[TRUNCATED: output exceeded size limit]\n",
) -> str:
    """Read at most ``max_bytes`` of a file from ``offset`` (generation-scoped).

    The runner appends stdout/stderr across resumed generations, so a resumed
    run must not parse the previous generation's payload. A file shorter than
    the offset (replaced/truncated) is read from byte 0. When this generation's
    bytes exceed ``max_bytes``, only its tail is returned (the final JSON lives
    at the end) with a truncation notice.
    """
    p = Path(path)
    if not p.is_file():
        return ""
    try:
        size = p.stat().st_size
        start = max(0, int(offset))
        if size <= start:
            return ""
        with open(p, "rb") as f:
            if size - start > max_bytes:
                notice_bytes = len(notice.encode("utf-8"))
                read_bytes = max(0, max_bytes - notice_bytes)
                start = max(start, size - read_bytes)
                f.seek(start)
                raw = f.read(read_bytes)
                return f"{notice}{raw.decode('utf-8', errors='replace')}"
            f.seek(start)
            raw = f.read(max_bytes)
            return raw.decode("utf-8", errors="replace")
    except OSError:
        return ""


# Regex patterns for sensitive credentials in logs
_RE_API_KEY = re.compile(r"AIza[0-9A-Za-z-_]{35}")
_RE_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9_\-\.\~]+", re.IGNORECASE)
_RE_GITHUB_PAT = re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}")
_RE_GITHUB_PAT_NEW = re.compile(r"github_pat_[A-Za-z0-9_]{82}")
_RE_KEY_ASSIGNMENT = re.compile(
    r"(?i)\b(password|secret|token|api_key|apikey)\b\s*([:=])\s*['\"]?(?!\[REDACTED)[^\s'\"]+['\"]?"
)
_RE_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]+ PRIVATE KEY-----.*?-----END [A-Z ]+ PRIVATE KEY-----",
    re.DOTALL,
)


def redact_log_text(text: str) -> str:
    """Redact known secret patterns, tokens, and credentials from log text."""
    # Redact the largest/multiline patterns first so a private key is not
    # partially rewritten by the narrower token regexes.
    redacted = _RE_PRIVATE_KEY.sub("[REDACTED_PRIVATE_KEY]", text)
    redacted = _RE_API_KEY.sub("[REDACTED_API_KEY]", redacted)
    redacted = _RE_BEARER.sub("Bearer [REDACTED]", redacted)
    redacted = _RE_GITHUB_PAT.sub("[REDACTED_TOKEN]", redacted)
    redacted = _RE_GITHUB_PAT_NEW.sub("[REDACTED_TOKEN]", redacted)
    redacted = _RE_KEY_ASSIGNMENT.sub(r"\1\2[REDACTED]", redacted)
    return redacted


def sha256_file(path: Path) -> str:
    """Calculate the SHA-256 hexadecimal digest of a file."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Calculate the SHA-256 hexadecimal digest of bytes in memory."""
    return hashlib.sha256(data).hexdigest()


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def strict_uuid(value: object) -> Optional[str]:
    """Return the canonical lowercase UUID string, or None for anything else.

    Conversation ids come from agent-influenced logs, so a loose token such as
    "using" must never be accepted. Only a full 36-character UUID survives.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not _UUID_RE.match(candidate):
        return None
    return candidate.lower()


def load_preamble() -> str:
    """Load the standing prompt preamble per SPEC section 14."""
    import importlib.resources

    return (
        importlib.resources.files("agy_mcp")
        .joinpath("preamble.md")
        .read_text(encoding="utf-8")
        .strip()
    )


def format_fresh_prompt(task_text: str) -> str:
    """Format full prompt for fresh job: preamble before task text under header."""
    preamble = load_preamble()
    return f"{preamble}\n\n## Task\n{task_text.strip()}"


def format_resume_prompt(
    answer_text: str,
    is_trusted: bool = False,
    state_block: Optional[str] = None,
) -> str:
    """Format full prompt for resume job: preamble, optional state block, answer.

    The state block is wrapper-built and already contains untrusted-wrapped
    values; it is inserted once, outside the guidance/answer block. The preamble
    still appears exactly once (D62).
    """
    # Lazy import: models imports util at module load, so a top-level import here
    # would create a cycle.
    from agy_mcp.models import wrap_untrusted_text

    preamble = load_preamble()
    block = f"{state_block.strip()}\n\n" if state_block and state_block.strip() else ""
    if is_trusted:
        body = answer_text.strip()
        if body.startswith(preamble):
            return body
        return f"{preamble}\n\n{block}## Wrapper Guidance\n{body}"
    wrapped_answer = wrap_untrusted_text(answer_text.strip())
    return f"{preamble}\n\n{block}## User Response\n{wrapped_answer}"
