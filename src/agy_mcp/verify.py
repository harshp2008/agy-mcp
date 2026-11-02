"""Verification allowlist, execution and auto-fix prompt (SPEC section 13).

Owns command-token allowlisting, per-tool flag/path restrictions, shell-free
execution with the sanitized environment, the capped failure tail and the
auto-fix resume prompt. It must never invoke a shell, never run a command that
is not in the configured allowlist, and never let a flag or path escape the
worktree/repository boundaries.
"""

import os
import re
import shlex
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional

from agy_mcp.models import MAX_VERIFY_TAIL_BYTES, wrap_untrusted_text
from agy_mcp.util import env_allowlist, truncate_head, truncate_tail

DEFAULT_VERIFY_TIMEOUT_SECONDS = 900
# rc=124 mirrors coreutils `timeout`; the supervisor maps it to verify_timeout.
VERIFY_TIMEOUT_RC = 124
VERIFY_EXCERPT_LINES = 40
VERIFY_EXCERPT_LINE_CHARS = 400

FORBIDDEN_VERIFY_SHELLS = {"sh", "bash", "zsh", "csh", "tcsh", "dash", "ksh"}
# Shells would turn a single allowlisted token into arbitrary command execution,
# so they are rejected both as the command and as an allowlist entry.

PYTEST_DISALLOWED_FLAGS = {
    "-c",
    "-p",
    "-o",
    "--rootdir",
    "--confcutdir",
    "--prefix",
    "--config",
    "--config-file",
    "--override-ini",
    "--import-mode",
}
PYTEST_DISALLOWED_PREFIXES = tuple(f"{flag}=" for flag in PYTEST_DISALLOWED_FLAGS)

RUFF_DISALLOWED_FLAGS = {
    "-c",
    "--config",
    "--config-file",
}
RUFF_DISALLOWED_PREFIXES = tuple(f"{flag}=" for flag in RUFF_DISALLOWED_FLAGS)

GO_DISALLOWED_FLAGS = {
    "-exec",
    "-toolexec",
    "-overlay",
    "-modfile",
    "-C",
}
GO_DISALLOWED_PREFIXES = tuple(f"{flag}=" for flag in GO_DISALLOWED_FLAGS)


def _allowlist_entry_tokens(entry: Any) -> list[str]:
    """Normalize one allowlist entry (string or token list) to non-empty tokens."""
    if isinstance(entry, list):
        return [str(x).strip() for x in entry if str(x).strip()]
    if isinstance(entry, str):
        try:
            return shlex.split(entry.strip())
        except ValueError:
            return []
    return []


def _is_python_m_pytest(cmd_tokens: list[str]) -> bool:
    """True only for the exact ``python|python3 -m pytest`` interpreter form."""
    return (
        len(cmd_tokens) >= 3
        and cmd_tokens[0] in ("python", "python3")
        and cmd_tokens[1] == "-m"
        and cmd_tokens[2] == "pytest"
    )


def _validate_tool_args(
    tool: str,
    extra_tokens: list[str],
    worktree_path: Optional[Path],
    repo_path: Optional[Path],
) -> bool:
    """Apply the per-tool flag/path policy to the tokens after an allowlist match."""
    if tool == "pytest":
        return _validate_pytest_args(extra_tokens, worktree_path, repo_path)
    if tool == "ruff":
        return _validate_ruff_args(extra_tokens, worktree_path, repo_path)
    if tool == "cargo":
        return _validate_cargo_args(extra_tokens, worktree_path, repo_path)
    if tool == "go":
        return _validate_go_args(extra_tokens, worktree_path, repo_path)
    if tool == "npm":
        return _validate_npm_args(extra_tokens)
    if tool == "node":
        return _validate_node_test_args(extra_tokens, worktree_path, repo_path)
    return _validate_other_args(extra_tokens, worktree_path, repo_path)


def accepted_verify_forms(allowlist: list[Any]) -> list[str]:
    """Return the human-readable accepted forms, including the interpreter form.

    ``python -m pytest`` / ``python3 -m pytest`` are accepted only when the
    configured allowlist contains the pytest tool, so they are listed only then.
    """
    forms: list[str] = []
    has_pytest = False
    for entry in allowlist:
        tokens = _allowlist_entry_tokens(entry)
        if not tokens:
            continue
        forms.append(" ".join(tokens))
        if _identify_tool(tokens) == "pytest":
            has_pytest = True
    if has_pytest:
        for form in ("python -m pytest", "python3 -m pytest"):
            if form not in forms:
                forms.append(form)
    return forms


def verify_rejection_message(command_str: str, allowlist: list[Any]) -> str:
    """Build a rejection message that lists every accepted form (smoke-fix item 3)."""
    forms = accepted_verify_forms(allowlist)
    accepted = ", ".join(forms) if forms else "(none configured)"
    return (
        f"Verify command '{command_str}' is not allowed. Accepted forms: {accepted}. "
        "Per-tool flags are validated; python/python3 -m pytest is accepted when "
        "pytest is allowlisted."
    )


def _identify_tool(entry_tokens: list[str]) -> str:
    """Identify the underlying verify tool from the allowlisted entry tokens."""
    tokens_lower = [Path(t).name.lower() for t in entry_tokens]
    if len(entry_tokens) >= 2 and tokens_lower[0] == "node" and tokens_lower[1] == "--test":
        return "node"
    if "pytest" in tokens_lower:
        return "pytest"
    if "ruff" in tokens_lower:
        return "ruff"
    if "cargo" in tokens_lower:
        return "cargo"
    if "go" in tokens_lower:
        return "go"
    if "npm" in tokens_lower:
        return "npm"
    return "other"


def _is_path_outside(
    path_str: str,
    worktree_path: Optional[Path],
    repo_path: Optional[Path],
) -> bool:
    """Check whether a path resolves outside the allowable worktree and repo boundaries."""
    allowed_roots: list[Path] = []
    if worktree_path:
        try:
            allowed_roots.append(Path(worktree_path).resolve(strict=False))
        except Exception:
            pass
    if repo_path:
        try:
            allowed_roots.append(Path(repo_path).resolve(strict=False))
        except Exception:
            pass

    p = Path(path_str)
    if not allowed_roots:
        return p.is_absolute() or ".." in p.parts

    for r in allowed_roots:
        try:
            resolved = (
                p.resolve(strict=False)
                if p.is_absolute()
                else (r / p).resolve(strict=False)
            )
            if resolved == r or resolved.is_relative_to(r):
                return False
        except Exception:
            # Fail closed: an unresolvable path is treated as outside.
            pass
    return True


def _check_token_path(
    token: str,
    worktree_path: Optional[Path],
    repo_path: Optional[Path],
) -> bool:
    """Return True if token contains a path that escapes allowable roots."""
    path_to_check: Optional[str] = None
    if token.startswith("/") or token.startswith("..") or "/../" in token:
        path_to_check = token
    elif "=" in token:
        val = token.split("=", 1)[1]
        if val.startswith("/") or ".." in val:
            path_to_check = val
    if path_to_check is not None:
        return _is_path_outside(path_to_check, worktree_path, repo_path)
    return False


def _validate_pytest_args(
    extra_tokens: list[str],
    worktree_path: Optional[Path],
    repo_path: Optional[Path],
) -> bool:
    for token in extra_tokens:
        if token in PYTEST_DISALLOWED_FLAGS or token.startswith(PYTEST_DISALLOWED_PREFIXES):
            return False
        if _check_token_path(token, worktree_path, repo_path):
            return False
    return True


def _validate_ruff_args(
    extra_tokens: list[str],
    worktree_path: Optional[Path],
    repo_path: Optional[Path],
) -> bool:
    for token in extra_tokens:
        if token in RUFF_DISALLOWED_FLAGS or token.startswith(RUFF_DISALLOWED_PREFIXES):
            return False
        if _check_token_path(token, worktree_path, repo_path):
            return False
    return True


def _validate_cargo_args(
    extra_tokens: list[str],
    worktree_path: Optional[Path],
    repo_path: Optional[Path],
) -> bool:
    """Return True when the cargo extra tokens are safe to run in the worktree.

    Cargo is the one allowlisted runner whose flags can write or load files
    outside the worktree, so it needs per-flag handling instead of a token
    denylist: ``--config``/``-Z`` (config injection and unstable flags) are
    refused outright, while ``--manifest-path`` and ``--target-dir`` (both the
    ``=value`` and separate-value forms) must resolve inside the worktree/repo.
    Every other token goes through ``_check_token_path``. Fail-closed: a missing
    value, an unparsable path or an outside path rejects the command.
    """
    i = 0
    n = len(extra_tokens)
    while i < n:
        token = extra_tokens[i]
        if token == "--config" or token.startswith("--config="):
            return False
        if token == "-Z" or token.startswith("-Z"):
            return False
        if token == "--manifest-path":
            if i + 1 >= n or _is_path_outside(extra_tokens[i + 1], worktree_path, repo_path):
                return False
            i += 2
            continue
        elif token.startswith("--manifest-path="):
            val = token.split("=", 1)[1]
            if _is_path_outside(val, worktree_path, repo_path):
                return False
        elif token == "--target-dir":
            if i + 1 >= n or _is_path_outside(extra_tokens[i + 1], worktree_path, repo_path):
                return False
            i += 2
            continue
        elif token.startswith("--target-dir="):
            val = token.split("=", 1)[1]
            if _is_path_outside(val, worktree_path, repo_path):
                return False
        else:
            if _check_token_path(token, worktree_path, repo_path):
                return False
        i += 1
    return True


def _validate_go_args(
    extra_tokens: list[str],
    worktree_path: Optional[Path],
    repo_path: Optional[Path],
) -> bool:
    for token in extra_tokens:
        if token in GO_DISALLOWED_FLAGS or token.startswith(GO_DISALLOWED_PREFIXES):
            return False
        if _check_token_path(token, worktree_path, repo_path):
            return False
    return True


def _validate_npm_args(extra_tokens: list[str]) -> bool:
    return len(extra_tokens) == 0


NODE_TEST_FORBIDDEN_CHARS = {
    ";",
    "&",
    "|",
    "`",
    "$",
    "(",
    ")",
    "<",
    ">",
    "\n",
    "\r",
    "\\",
    '"',
    "'",
}


def _validate_node_test_args(
    extra_tokens: list[str],
    worktree_path: Optional[Path],
    repo_path: Optional[Path] = None,
) -> bool:
    """Validate extra arguments for `node --test` per SPEC section 13.

    ``agy_start`` validates before the worktree exists, so the repository root
    is the only available boundary there; once a worktree exists it is the only
    boundary. Either way every argument is resolved (symlinks included) and a
    token that escapes the boundary is rejected.
    """
    boundary = worktree_path if worktree_path is not None else repo_path
    for token in extra_tokens:
        if not token:
            return False
        if token.startswith("-"):
            return False
        if any(c in NODE_TEST_FORBIDDEN_CHARS for c in token):
            return False
        p = Path(token)
        if p.is_absolute() or ".." in p.parts:
            return False
        if _is_path_outside(token, worktree_path=boundary, repo_path=None):
            return False
    return True


def _validate_other_args(
    extra_tokens: list[str],
    worktree_path: Optional[Path],
    repo_path: Optional[Path],
) -> bool:
    for token in extra_tokens:
        if token in PYTEST_DISALLOWED_FLAGS or token.startswith(PYTEST_DISALLOWED_PREFIXES):
            return False
        if _check_token_path(token, worktree_path, repo_path):
            return False
    return True


def is_verify_allowed(
    command_str: str,
    allowlist: list[Any],
    worktree_path: Optional[Path] = None,
    repo_path: Optional[Path] = None,
) -> bool:
    """Validate verification command against verify_allowlist per SPEC section 13 & F9.

    Uses exact prefix matching of command tokens (shlex.split).
    The config may name standard commands ('pytest') or absolute interpreter-based commands
    such as ['/abs/path/python', '-m', 'pytest'].
    Dangerous flags and arguments escaping the workspace are strictly rejected per tool.
    """
    if not command_str or not command_str.strip():
        return False

    try:
        cmd_tokens = shlex.split(command_str.strip())
    except ValueError:
        return False

    if not cmd_tokens:
        return False

    # Block direct shell wrappers
    first_bin = Path(cmd_tokens[0]).name.lower()
    if first_bin in FORBIDDEN_VERIFY_SHELLS:
        return False

    # The canonical interpreter form for pytest is accepted under the same
    # per-tool policy; every other interpreter shape stays unlisted.
    python_pytest = _is_python_m_pytest(cmd_tokens)

    for entry in allowlist:
        entry_tokens = _allowlist_entry_tokens(entry)
        if not entry_tokens:
            continue

        # Reject entry if it starts with forbidden shell
        if Path(entry_tokens[0]).name.lower() in FORBIDDEN_VERIFY_SHELLS:
            continue

        tool = _identify_tool(entry_tokens)

        # Exact prefix match
        if len(cmd_tokens) >= len(entry_tokens) and cmd_tokens[: len(entry_tokens)] == entry_tokens:
            extra_tokens = cmd_tokens[len(entry_tokens) :]
            if _validate_tool_args(tool, extra_tokens, worktree_path, repo_path):
                return True

        # ``python -m pytest <flags>`` == ``pytest <flags>`` for an allowlisted
        # pytest entry. The leading tokens were validated exactly above, so an
        # absolute interpreter path, .venv path, -c or glued -mpytest never
        # reaches this branch.
        if python_pytest and tool == "pytest":
            if _validate_pytest_args(cmd_tokens[3:], worktree_path, repo_path):
                return True

    return False


class VerifyResult(tuple):
    """``(rc, output)`` tuple with the metadata the reviewer needs.

    It deliberately subclasses ``tuple`` so every old caller that unpacks
    ``rc, output = run_verify(...)`` keeps working unchanged.
    """

    rc: int
    output: str
    command: str
    timeout_seconds: int
    timed_out: bool
    killed_by_timeout: bool
    group_alive_after_kill: bool
    duration_seconds: float

    def __new__(
        cls,
        rc: int,
        output: str,
        *,
        command: str,
        timeout_seconds: int,
        timed_out: bool = False,
        killed_by_timeout: bool = False,
        group_alive_after_kill: bool = False,
        duration_seconds: float = 0.0,
    ) -> "VerifyResult":
        obj = super().__new__(cls, (rc, output))
        obj.rc = rc
        obj.output = output
        obj.command = command
        obj.timeout_seconds = timeout_seconds
        obj.timed_out = timed_out
        obj.killed_by_timeout = killed_by_timeout
        obj.group_alive_after_kill = group_alive_after_kill
        obj.duration_seconds = duration_seconds
        return obj

    @property
    def status(self) -> str:
        """One of ``passed``, ``timeout`` or ``failed`` for the status field."""
        if self.timed_out:
            return "timeout"
        return "passed" if self.rc == 0 else "failed"


def _process_group_alive(pgid: int) -> bool:
    """Return True while any process remains in the group (signal 0 probe)."""
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # The group exists but is not ours; treat it as alive (fail closed).
        return True


def _terminate_process_group(proc: subprocess.Popen) -> tuple[bool, bool]:
    """SIGTERM then SIGKILL the child's whole process group.

    Returns ``(killed, alive_after)``: ``killed`` says a signal was actually
    delivered to a live group, ``alive_after`` says something survived SIGKILL.
    Because the child is spawned with ``start_new_session=True`` its pid is the
    group id, so ``killpg`` takes every pytest/agy grandchild with it.
    """
    pgid = proc.pid
    killed = False
    try:
        os.killpg(pgid, signal.SIGTERM)
        killed = True
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGKILL)
            killed = True
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=2.0)
        except Exception:
            pass
    # Give reparented grandchildren a moment to be reaped; a zombie keeps the
    # group visible to killpg(0) for a few milliseconds and would produce a
    # false "orphans remain" warning.
    alive = _process_group_alive(pgid)
    deadline = time.monotonic() + 1.0
    while alive and time.monotonic() < deadline:
        time.sleep(0.05)
        alive = _process_group_alive(pgid)
    return killed, alive


def run_verify(
    command_str: str,
    worktree_path: Path,
    timeout_seconds: int = DEFAULT_VERIFY_TIMEOUT_SECONDS,
    home: Optional[Path] = None,
    job_tmp_dir: Optional[Path] = None,
    on_proc_spawn: Optional[Callable[[subprocess.Popen], None]] = None,
) -> VerifyResult:
    """Execute verification command in worktree and return a ``VerifyResult``.

    Runs with sanitized environment, dedicated scratch TMPDIR, and captures max 8 KB tail.
    On timeout the child's whole process group is killed (SIGTERM, then SIGKILL)
    and the result records ``timed_out``/``killed_by_timeout`` so a timeout is
    never confused with a command that failed by itself.
    """
    started = time.monotonic()

    def _result(
        rc: int,
        output: str,
        *,
        timed_out: bool = False,
        killed_by_timeout: bool = False,
        group_alive_after_kill: bool = False,
    ) -> VerifyResult:
        return VerifyResult(
            rc,
            output,
            command=command_str,
            timeout_seconds=int(timeout_seconds),
            timed_out=timed_out,
            killed_by_timeout=killed_by_timeout,
            group_alive_after_kill=group_alive_after_kill,
            duration_seconds=round(time.monotonic() - started, 3),
        )

    try:
        cmd_args = shlex.split(command_str.strip())
    except ValueError as e:
        return _result(1, f"Failed to parse verify command: {e}")

    if not cmd_args:
        return _result(1, "Empty verify command")

    # Dedicated scratch TMPDIR
    scratch_dir = None
    if job_tmp_dir is not None:
        tmp_path = job_tmp_dir
        tmp_path.mkdir(parents=True, exist_ok=True)
    else:
        scratch_dir = tempfile.TemporaryDirectory(prefix="agy_verify_tmp_")
        tmp_path = Path(scratch_dir.name)

    env = env_allowlist(job_tmp_dir=tmp_path, home=home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    existing_pytest_addopts = env.get("PYTEST_ADDOPTS", "").strip()
    # Verification must not leave .pytest_cache/.pyc junk in the user's worktree;
    # the cache disable is merged with any allowlisted existing opts.
    if "-p no:cacheprovider" not in existing_pytest_addopts:
        if existing_pytest_addopts:
            env["PYTEST_ADDOPTS"] = f"{existing_pytest_addopts} -p no:cacheprovider"
        else:
            env["PYTEST_ADDOPTS"] = "-p no:cacheprovider"

    timed_out = False
    killed_by_timeout = False
    group_alive_after_kill = False
    rc = 1
    timed_out_msg = ""
    try:
        out_file_path = tmp_path / "verify_output.log"
        with open(out_file_path, "wb") as out_f:
            proc = subprocess.Popen(
                cmd_args,
                cwd=str(worktree_path.resolve()),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out_f,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            if on_proc_spawn is not None:
                try:
                    on_proc_spawn(proc)
                except Exception:
                    pass
            try:
                proc.wait(timeout=timeout_seconds)
                rc = proc.returncode
            except subprocess.TimeoutExpired:
                # The process may have exited between the timeout and this
                # branch; honour its real exit code instead of inventing rc=124.
                if proc.poll() is not None:
                    rc = proc.returncode
                else:
                    timed_out = True
                    rc = VERIFY_TIMEOUT_RC
                    killed_by_timeout, group_alive_after_kill = _terminate_process_group(proc)
                    timed_out_msg = (
                        f"\nVerification command timed out after {timeout_seconds}s"
                        f"\n[wrapper] command: {command_str}"
                        f"\n[wrapper] process group {proc.pid} "
                        + (
                            "killed (SIGTERM/SIGKILL)"
                            if killed_by_timeout
                            else "exited before the kill signal"
                        )
                    )
                    if group_alive_after_kill:
                        timed_out_msg += (
                            f"\n[wrapper] WARNING: process group {proc.pid} is still alive "
                            "after SIGKILL; inspect for orphaned children"
                        )

        from agy_mcp.util import read_file_tail

        raw_output = (
            read_file_tail(
                out_file_path,
                MAX_VERIFY_TAIL_BYTES,
                notice="[TRUNCATED: output exceeded size limit] [truncated ...]\n",
            )
            + timed_out_msg
        )
    except FileNotFoundError as exc:
        rc = 127
        raw_output = f"Executable not found: {exc}"
    except Exception as exc:
        rc = 1
        raw_output = f"Verification execution error: {exc}"
    finally:
        if scratch_dir is not None:
            try:
                scratch_dir.cleanup()
            except Exception:
                pass

    return _result(
        rc,
        raw_output,
        timed_out=timed_out,
        killed_by_timeout=killed_by_timeout,
        group_alive_after_kill=group_alive_after_kill,
    )


def diagnose_verify_output(output: str) -> tuple[Optional[str], Optional[str]]:
    """Return ``(cause, next_action)`` for known verify failures.

    The pytest basename collision is a repository problem, not a wrapper one:
    the wrapper only has to surface it instead of burying it under the test
    summary. The same helper makes command-not-found actionable.
    """
    lower = (output or "").lower()
    if "import file mismatch" in lower:
        return (
            "pytest import file mismatch: two test files share the same basename "
            "(for example tests/test_x.py and tests/acceptance/test_x.py)",
            "Fix the repository: add an __init__.py to each test directory or give "
            "the test files unique basenames, then rerun verify",
        )
    if "executable not found" in lower:
        return (
            "the verify executable was not found on PATH",
            "Install it or change the verify command/allowlist, then resume the job",
        )
    if "no module named" in lower and "pytest" in lower:
        return (
            "pytest is not importable by the verify interpreter",
            "Run verify with the project interpreter (python -m pytest) or install pytest",
        )
    return None, None


def verify_tail_lines(output: str, limit: int = VERIFY_EXCERPT_LINES) -> list[str]:
    """Return the last ``limit`` non-empty-ish lines, each capped for readability."""
    lines = (output or "").splitlines()
    tail = lines[-limit:] if len(lines) > limit else lines
    capped: list[str] = []
    for line in tail:
        if len(line) > VERIFY_EXCERPT_LINE_CHARS:
            line = line[:VERIFY_EXCERPT_LINE_CHARS] + " [... line truncated]"
        capped.append(line)
    return capped


def build_verify_excerpt(result: VerifyResult) -> str:
    """Build the diagnostics-plus-last-40-lines block shown in result/envelopes."""
    header: list[str] = [
        f"[wrapper] verify command: {result.command}",
        f"[wrapper] timeout: {result.timeout_seconds}s",
    ]
    if result.timed_out:
        header.append(
            "[wrapper] result: timed out (wrapper deadline); killed by timeout="
            + ("yes" if result.killed_by_timeout else "no")
        )
    else:
        header.append(f"[wrapper] result: command exited on its own with rc={result.rc}")

    cause, next_action = diagnose_verify_output(result.output)
    if cause:
        header.append(f"[wrapper] cause: {cause}")
        header.append(f"[wrapper] next: {next_action}")

    # Diagnostics such as pytest's import-file mismatch often appear near the
    # top of the output, so quote matching lines explicitly even when they are
    # outside the last-40-lines window.
    lower = result.output.lower()
    for marker in ("import file mismatch", "hint: remove __pycache__"):
        if marker in lower:
            for line in result.output.splitlines():
                if marker in line.lower():
                    header.append(f"[wrapper] matched: {line.strip()[:VERIFY_EXCERPT_LINE_CHARS]}")
                    break

    header_text = "\n".join(header)
    body = "\n".join(verify_tail_lines(result.output))
    budget = MAX_VERIFY_TAIL_BYTES - len(header_text.encode("utf-8")) - 200
    if budget < 0:
        budget = 0
    if len(body.encode("utf-8")) > budget:
        body = truncate_head(
            body,
            budget,
            notice="[wrapper] earlier output omitted; showing the last lines\n",
        )
    return f"{header_text}\n[wrapper] last {VERIFY_EXCERPT_LINES} lines:\n{body}"


def extract_failing_tests(tail_output: str) -> list[str]:
    """Parse failing test identifiers from pytest or test runner output."""
    patterns = [
        r"^FAILED\s+([^\s:]+(?:::[^\s]+)?)",
        r"^ERROR\s+([^\s:]+(?:::[^\s]+)?)",
        r"([^\s\n]+::[^\s\n]+)\s+FAILED",
    ]
    failing: list[str] = []
    for pat in patterns:
        matches = re.findall(pat, tail_output, re.MULTILINE)
        for m in matches:
            cleaned = m.strip()
            if cleaned and cleaned not in failing:
                failing.append(cleaned)
    return failing


def format_fix_prompt(
    tail_output: str,
    preamble_path: Optional[Path] = None,
    include_preamble: bool = True,
) -> str:
    """Format the resume prompt with truncated error output and standing preamble per D53.

    Includes first 2000 bytes of failure tail wrapped as untrusted, failing test names
    if parseable, and explicit guidance to modify code rather than unit tests.
    """
    if not include_preamble:
        preamble = ""
    elif preamble_path is not None and preamble_path.is_file():
        preamble = preamble_path.read_text(encoding="utf-8").strip()
    else:
        # Default bundled preamble
        default_preamble = Path(__file__).parent / "preamble.md"
        if default_preamble.is_file():
            preamble = default_preamble.read_text(encoding="utf-8").strip()
        else:
            preamble = (
                "You are an autonomous coding assistant executing a task in this "
                "repository worktree."
            )

    failing_tests = extract_failing_tests(tail_output)
    test_info = ""
    if failing_tests:
        test_info = f"Failing tests: {', '.join(failing_tests)}\n\n"

    # First 2000 bytes of the failure tail, wrapped as untrusted
    tail_2000 = truncate_tail(tail_output.strip(), 2000)
    wrapped_tail = wrap_untrusted_text(tail_2000)

    instruction = (
        "Change code, not tests, unless the test itself is wrong "
        "(and then say so in the commit message)."
    )

    fix_body = (
        "Verification failed with the following output:\n"
        f"{test_info}"
        f"{wrapped_tail}\n\n"
        f"{instruction}\n"
        "Please analyze the error, fix the issues, and write a commit message to .agy/COMMIT_MSG."
    )

    if not include_preamble:
        return fix_body
    return f"{preamble}\n\n{fix_body}"
