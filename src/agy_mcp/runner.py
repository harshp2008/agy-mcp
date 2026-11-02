"""Process execution boundary: argv building, /proc inspection and Runner.

Owns the exact agy command line, prompt/flag guardrails, process-group creation
and SIGTERM/SIGKILL semantics, plus the ``Runner`` abstraction future isolated
runners can implement. It must never invoke a shell, never pass a forbidden
flag, and never kill a process group it did not create.
"""

import os
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Optional

from agy_mcp.util import strict_uuid

MAX_PROMPT_BYTES = 100_000

# These flags would weaken or bypass the wrapper's security model; build_argv
# rejects them even if a future caller tries to inject them.
FORBIDDEN_FLAGS = {
    "--sandbox",
    "--effort",
    "-c",
    "--dangerously-skip-permissions",
}
VALID_MODES = {"accept-edits"}
VALID_OUTPUT_FORMATS = {"json"}


class RunnerError(Exception):
    """Base exception for runner errors."""

    def __init__(self, message: str, code: str = "runner_error") -> None:
        super().__init__(message)
        self.code = code


class PromptTooLargeError(RunnerError):
    """Raised when prompt exceeds the 100,000 bytes Linux argv boundary."""

    def __init__(self, message: str = "Prompt exceeds 100000 bytes limit") -> None:
        super().__init__(message, code="prompt_too_large")


@dataclass
class ProcessHandle:
    """Handle to a running process group managed by a Runner."""

    pid: int
    pgid: int
    start_time: int
    job_id: str
    stdout_path: Path
    stderr_path: Path


def read_proc_stat_field(pid: int, field_index: int) -> int:
    """Read an integer field from /proc/<pid>/stat.

    Field numbering:
      1: pid
      2: (comm)
      3: state
      4: ppid
      5: pgrp
      ...
      22: starttime (clock ticks after boot)
    """
    stat_file = Path(f"/proc/{pid}/stat")
    if not stat_file.is_file():
        raise ProcessLookupError(f"Process {pid} does not exist in /proc")

    content = stat_file.read_text(encoding="utf-8", errors="replace")
    rparen = content.rfind(")")
    if rparen == -1:
        raise ValueError(f"Malformed /proc/{pid}/stat content")

    rest = content[rparen + 1 :].strip().split()
    # rest[0] corresponds to field 3 (state)
    target_idx = field_index - 3
    if target_idx < 0 or target_idx >= len(rest):
        raise IndexError(f"Field {field_index} out of range in /proc/{pid}/stat")

    return int(rest[target_idx])


def read_proc_start_time(pid: int) -> int:
    """Read the starttime in clock ticks for process pid from /proc/<pid>/stat."""
    return read_proc_stat_field(pid, 22)


def build_argv(
    agy_binary: str | Path,
    prompt: str,
    model: str,
    print_timeout_seconds: int,
    log_file: Path,
    conversation_id: Optional[str] = None,
    mode: str = "accept-edits",
    output_format: str = "json",
) -> list[str]:
    """Construct command-line argv for agy in strict flag order per SPEC section 10.

    Format:
      <agy_binary_abs> --mode accept-edits --output-format json --print-timeout <N>s
      --log-file <abs jobdir>/cli.log --model <model> [--conversation <id>] -p <prompt>

    Flags must precede -p <prompt>; -p is always placed last.
    """
    if mode not in VALID_MODES:
        raise RunnerError(
            f"Invalid mode '{mode}'; only modes in {VALID_MODES} are permitted in v1",
            code="invalid_argv",
        )

    if output_format not in VALID_OUTPUT_FORMATS:
        raise RunnerError(
            f"Invalid output format '{output_format}'; only formats in "
            f"{VALID_OUTPUT_FORMATS} are permitted in v1",
            code="invalid_argv",
        )

    if not model or not str(model).strip():
        raise RunnerError("Model identifier must not be empty", code="invalid_argv")

    if print_timeout_seconds < 1:
        raise RunnerError(
            f"print_timeout_seconds must be >= 1, got {print_timeout_seconds}",
            code="invalid_argv",
        )

    bin_path = Path(agy_binary)
    resolved_bin = str(bin_path.resolve()) if bin_path.is_file() else str(agy_binary)
    resolved_log = Path(log_file).resolve()

    if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
        raise PromptTooLargeError(
            f"Prompt size {len(prompt.encode('utf-8'))} bytes exceeds limit of "
            f"{MAX_PROMPT_BYTES} bytes"
        )

    argv = [
        resolved_bin,
        "--mode",
        mode,
        "--output-format",
        output_format,
        "--print-timeout",
        f"{int(print_timeout_seconds)}s",
        "--log-file",
        str(resolved_log),
        "--model",
        str(model),
    ]

    for flag in FORBIDDEN_FLAGS:
        if flag in argv:
            raise RunnerError(f"Forbidden flag '{flag}' detected in argv", code="forbidden_flag")

    # A conversation id reaches agy only as a strict UUID. A stored non-UUID
    # (the old cli.log fallback "using") must degrade to a fresh, self-contained
    # run instead of being passed through.
    valid_conversation_id = strict_uuid(conversation_id)
    if valid_conversation_id:
        argv.extend(["--conversation", valid_conversation_id])

    argv.extend(["-p", prompt])
    return argv


class Runner(ABC):
    """Abstract Base Class for job execution runners."""

    @abstractmethod
    def launch(
        self,
        job_id: str,
        workdir: Path,
        args: list[str],
        env: dict[str, str],
        stdout_path: Path,
        stderr_path: Path,
    ) -> ProcessHandle:
        """Spawn process in a new process group with file-backed streams."""
        raise NotImplementedError

    @abstractmethod
    def poll(self, handle: ProcessHandle) -> Optional[int]:
        """Return exit code if terminated, None if still running."""
        raise NotImplementedError

    @abstractmethod
    def kill(self, handle: ProcessHandle, grace_seconds: float = 3.0) -> None:
        """Send SIGTERM to process group, wait grace_seconds, then SIGKILL."""
        raise NotImplementedError

    @abstractmethod
    def cleanup(self, handle: ProcessHandle) -> None:
        """Release OS process resources."""
        raise NotImplementedError


class LocalProcessRunner(Runner):
    """Host execution runner spawning agy via subprocess in an isolated process group."""

    def __init__(self, executable: Optional[str | Path] = None) -> None:
        self.executable = str(executable) if executable else "agy"
        self._processes: dict[int, subprocess.Popen[bytes]] = {}
        self._open_files: dict[int, list[IO[object]]] = {}

    def launch(
        self,
        job_id: str,
        workdir: Path,
        args: list[str],
        env: dict[str, str],
        stdout_path: Path,
        stderr_path: Path,
    ) -> ProcessHandle:
        """Spawn agy in a new session and return the process handle."""
        # Check prompt/argument size limits
        for arg in args:
            if len(arg.encode("utf-8")) > MAX_PROMPT_BYTES:
                raise PromptTooLargeError(
                    f"Argument size {len(arg.encode('utf-8'))} bytes exceeds "
                    f"limit of {MAX_PROMPT_BYTES} bytes"
                )

        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)

        out_file = open(stdout_path, "a", encoding="utf-8")
        err_file = open(stderr_path, "a", encoding="utf-8")

        try:
            proc = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=out_file,
                stderr=err_file,
                cwd=str(workdir.resolve()),
                env=env,
                # Own session means pgid == pid (unless the child re-execs), so
                # cancellation can signal every descendant as one group.
                start_new_session=True,  # Process group isolation: pgid == pid
            )
        except Exception:
            out_file.close()
            err_file.close()
            raise

        pid = proc.pid
        try:
            pgid = os.getpgid(pid)
        except OSError:
            pgid = pid

        try:
            start_time = read_proc_start_time(pid)
        except Exception:
            # Best effort only: a non-/proc start time will never match later,
            # so recovery treats the process as dead (fail-closed) rather than
            # reattaching to an unverified PID.
            start_time = int(time.time())

        self._processes[pid] = proc
        self._open_files[pid] = [out_file, err_file]

        return ProcessHandle(
            pid=pid,
            pgid=pgid,
            start_time=start_time,
            job_id=job_id,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )

    def poll(self, handle: ProcessHandle) -> Optional[int]:
        """Return the exit code, or None while the process is still alive."""
        if handle.pid in self._processes:
            proc = self._processes[handle.pid]
            rc = proc.poll()
            if rc is not None:
                self._close_process_files(handle.pid)
            return rc

        # Handle process not in memory (e.g. across restart)
        try:
            stat_path = Path(f"/proc/{handle.pid}/stat")
            content = stat_path.read_text(encoding="utf-8", errors="replace")
            rparen = content.rfind(")")
            if rparen != -1:
                rest = content[rparen + 1 :].strip().split()
                if rest[0] in ("Z", "X"):
                    return 0
                if int(rest[22 - 3]) == handle.start_time:
                    return None
            return 0
        except (ProcessLookupError, OSError, ValueError, IndexError):
            return 0

    def kill(self, handle: ProcessHandle, grace_seconds: float = 3.0) -> None:
        """Send SIGTERM to the group, wait up to grace_seconds, then SIGKILL it."""
        if handle.pgid <= 1:
            return

        # Step 1: SIGTERM to process group (always signal pgid, even if leader exited per F11)
        try:
            os.killpg(handle.pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass

        deadline = time.time() + grace_seconds
        while time.time() < deadline:
            proc = self._processes.get(handle.pid)
            if proc is not None:
                proc.poll()
            try:
                os.killpg(handle.pgid, 0)
            except ProcessLookupError:
                break
            except (PermissionError, OSError):
                pass
            time.sleep(0.05)

        # Step 2: Escalate to SIGKILL to process group if processes remain
        try:
            os.killpg(handle.pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass

        # Final reap if tracked
        if handle.pid in self._processes:
            try:
                self._processes[handle.pid].wait(timeout=1.0)
            except Exception:
                pass
            self._close_process_files(handle.pid)

    def cleanup(self, handle: ProcessHandle) -> None:
        """Kill any survivors and release the tracked file handles."""
        # Always signal and reap process group to terminate lingering descendants (F11)
        try:
            self.kill(handle, grace_seconds=0.2)
        except Exception:
            pass
        self._close_process_files(handle.pid)
        self._processes.pop(handle.pid, None)

    def _close_process_files(self, pid: int) -> None:
        files = self._open_files.pop(pid, [])
        for f in files:
            try:
                f.flush()
                f.close()
            except Exception:
                pass
