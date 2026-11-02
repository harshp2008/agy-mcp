"""Confined read of agy's session transcript for denied-call detail (D50).

Owns the only code path allowed to open an agy transcript: strict UUID input,
no ``..``, no symlinked path components, confinement under ``<agy_home>/brain``,
a 5 MB size gate with a 2 MB tail read, and untrusted wrapping of every string.
It must never read any other ``.gemini`` path or raise into the supervisor.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from agy_mcp.jobs import append_audit_event
from agy_mcp.models import truncate_tail, wrap_untrusted_text

UUID_REGEX = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

MAX_TRANSCRIPT_FILE_SIZE = 5 * 1024 * 1024  # 5 MB cap
TAIL_READ_SIZE = 2 * 1024 * 1024  # 2 MB tail-read
MAX_ENTRIES = 20
MAX_FIELD_BYTES = 500


@dataclass
class DeniedCall:
    """Record of a tool call extracted from agy session transcript."""

    step: int
    tool: str
    command: Optional[str] = None
    path: Optional[str] = None
    summary: Optional[str] = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the call; command/path/summary are already untrusted-wrapped."""
        return {
            "step": self.step,
            "tool": self.tool,
            "command": self.command,
            "path": self.path,
            "summary": self.summary,
        }


def cli_step_to_transcript_step(cli_step: int) -> int:
    """Map a 1-based cli.log confirmation step number N to its 0-based transcript step_index N - 1.

    Empirical Evidence (from docs/reports/smoke-fixes-2/analysis.txt):
    - S1: cli.log line 258 reports step 8
      ('Print mode: soft-denying tool confirmation "RunCommand" at step 8').
      In transcript 3681aaa8-4f93-46d6-8d1c-3ca36637ad52.jsonl, the denied run_command tool call
      is at step_index 7 (8 - 1 = 7).
    - S3: cli.log line 241 reports step 6
      ('Print mode: soft-denying tool confirmation "RunCommand" at step 6').
      In transcript 9d4181aa-13a5-4ff0-87ac-38c1db0a99b3.jsonl, the denied run_command tool call
      is at step_index 5 (6 - 1 = 5).
    - H2: cli.log line 273 reports step 8 for subagent 55c62dde-8307-484a-bc8a-4e954192ba01.
      In transcript 55c62dde-8307-484a-bc8a-4e954192ba01.jsonl, the denied view_file tool call
      is at step_index 7 (8 - 1 = 7).
    """
    return cli_step - 1


def _unquote_once(val: Any) -> str:
    """Unquote one level of JSON string-quoting when val is a JSON-quoted string."""
    if not isinstance(val, str):
        val = str(val) if val is not None else ""
    s = val.strip()
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        try:
            unquoted = json.loads(s)
            if isinstance(unquoted, str):
                return unquoted
        except Exception:
            pass
    return val


def read_denied_calls(
    agy_home: Path,
    conversation_id: str,
    steps: list[int],
    job_dir: Optional[Path] = None,
) -> list[DeniedCall]:
    """Read denied tool calls from an agy session transcript.

    Opens ONLY <agy_home>/brain/<conversation_id>/.system_generated/logs/transcript.jsonl.
    Never raises an exception; logs an audit event on failure and returns [].
    """
    try:
        # 1. UUID safety validation
        if not conversation_id or not UUID_REGEX.match(conversation_id.strip()):
            _log_unreadable(job_dir, "invalid_uuid", conversation_id)
            return []

        cid = conversation_id.strip().lower()
        if ".." in cid:
            _log_unreadable(job_dir, "path_escape", cid)
            return []

        base_brain = (Path(agy_home) / "brain").resolve()
        transcript_file = base_brain / cid / ".system_generated" / "logs" / "transcript.jsonl"

        # 2. Path confinement and symlink check
        # Check components between transcript_file and base_brain for symlinks
        # A symlinked intermediate directory would let a crafted conversation
        # escape the brain root even if the final resolved path looks plausible.
        cur = transcript_file
        while cur != base_brain and cur != cur.parent:
            if cur.is_symlink():
                _log_unreadable(job_dir, "symlink_detected", cid)
                return []
            cur = cur.parent

        try:
            resolved = transcript_file.resolve()
        except Exception:
            _log_unreadable(job_dir, "path_resolution_error", cid)
            return []

        if not resolved.is_relative_to(base_brain):
            _log_unreadable(job_dir, "path_escape", cid)
            return []

        if not transcript_file.is_file():
            _log_unreadable(job_dir, "file_not_found", cid)
            return []

        # 3. Size check and reading (refuse files over 5 MB -> tail read last 2 MB)
        st = transcript_file.stat()
        size = st.st_size

        with open(transcript_file, "rb") as f:
            if size > MAX_TRANSCRIPT_FILE_SIZE:
                f.seek(size - TAIL_READ_SIZE)
                raw = f.read(TAIL_READ_SIZE)
                text = raw.decode("utf-8", errors="replace")
                lines = text.splitlines()
                if lines:
                    # Drop partial first line from tail seek
                    # Seeking into the middle of a multi-byte/line boundary would
                    # otherwise yield one bogus JSON line.
                    lines = lines[1:]
            else:
                raw = f.read()
                text = raw.decode("utf-8", errors="replace")
                lines = text.splitlines()

        # 4. Target step mapping
        target_map = {cli_step_to_transcript_step(s): s for s in steps if isinstance(s, int)}

        results: list[DeniedCall] = []

        # 5. Line-by-line parsing
        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                item = json.loads(line_str)
            except Exception:
                continue

            if not isinstance(item, dict):
                continue

            step_idx = item.get("step_index")
            if not isinstance(step_idx, int):
                continue

            # If steps filter provided, check match
            if target_map and step_idx not in target_map:
                continue

            cli_step = target_map.get(step_idx, step_idx + 1)
            tool_calls = item.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue

            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue

                tool_name = str(tc.get("name", "") or "")
                raw_args = tc.get("args")
                args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}

                command: Optional[str] = None
                path: Optional[str] = None

                tool_lower = tool_name.lower()
                if tool_lower in ("run_command", "runcommand"):
                    raw_cmd = args.get("CommandLine") or args.get("command")
                    if raw_cmd is not None:
                        unq_cmd = _unquote_once(raw_cmd)
                        capped = truncate_tail(unq_cmd, MAX_FIELD_BYTES)
                        # The command is agent-controlled text; always wrap it so
                        # the orchestrator sees it as data (D39/D50).
                        command = wrap_untrusted_text(capped)
                elif any(k in tool_lower for k in ("read_file", "view_file", "view")):
                    raw_path = args.get("Path") or args.get("AbsolutePath") or args.get("path")
                    if raw_path is not None:
                        unq_path = _unquote_once(raw_path)
                        capped = truncate_tail(unq_path, MAX_FIELD_BYTES)
                        path = wrap_untrusted_text(capped)
                elif any(k in tool_lower for k in ("write_file", "edit_file", "create_file")):
                    raw_path = args.get("Path") or args.get("path")
                    if raw_path is not None:
                        unq_path = _unquote_once(raw_path)
                        capped = truncate_tail(unq_path, MAX_FIELD_BYTES)
                        path = wrap_untrusted_text(capped)

                raw_summary = args.get("toolSummary") or args.get("summary")
                summary: Optional[str] = None
                if raw_summary is not None:
                    summary = truncate_tail(_unquote_once(raw_summary), MAX_FIELD_BYTES)

                results.append(
                    DeniedCall(
                        step=cli_step,
                        tool=tool_name,
                        command=command,
                        path=path,
                        summary=summary,
                    )
                )

                if len(results) >= MAX_ENTRIES:
                    return results

        return results

    except Exception as exc:
        _log_unreadable(job_dir, f"exception: {exc}", conversation_id)
        return []


def _log_unreadable(
    job_dir: Optional[Path],
    reason: str,
    conversation_id: Optional[str] = None,
) -> None:
    """Record a transcript_unreadable audit event if job_dir is provided."""
    if job_dir and Path(job_dir).is_dir():
        try:
            append_audit_event(
                job_dir=Path(job_dir),
                event="transcript_unreadable",
                detail={
                    "reason": reason,
                    "conversation_id": conversation_id or "",
                },
            )
        except Exception:
            pass
