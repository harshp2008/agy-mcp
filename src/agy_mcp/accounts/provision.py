"""Provision and probe an isolated account home for the account CLI (D117).

Owns the filesystem side of ``agy-mcp account add``: create the 0700 home, prove
the fresh home cannot see the host keyring, hand the terminal to a human login,
verify the resulting login, and place the policy/settings files agy and the
wrapper expect. It must never read or copy a token/keyring/cookie file, never
automate a login, and never overwrite an existing policy or settings file.
"""

from __future__ import annotations

import getpass
import json
import os
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from agy_mcp.accounts.env import build_account_env
from agy_mcp.accounts.errors import AccountError
from agy_mcp.accounts.registry import AccountConfig
from agy_mcp.config import AGY_PERMISSION_CONFIG_REL
from agy_mcp.usage import (
    MAX_USAGE_STREAM_BYTES,
    USAGE_PROMPT,
    mask_email,
    matches_patterns,
    parse_auth_line,
    parse_usage_output,
    read_usage_once,
)

DEFAULT_ACCOUNTS_ROOT = Path("~/.local/share/agy-accounts")
ADD_PROBE_TIMEOUT_SECONDS = 45.0
AUTH_REQUIRED_PATTERN = "Authentication required"
PROBE_TERMINATE_GRACE_SECONDS = 2.0
SETTINGS_REL = Path(".gemini") / "antigravity-cli" / "settings.json"

LoginRunner = Callable[[AccountConfig, Path, str], int]


@dataclass(frozen=True)
class ProvisionPaths:
    """Files the add flow may place inside one account home."""

    policy: Optional[Path]
    settings: Optional[Path]


def accounts_root() -> Path:
    """Return the default agy-accounts root with ``~`` expanded."""
    return Path(os.path.expanduser(str(DEFAULT_ACCOUNTS_ROOT)))


def default_home_value(name: str) -> str:
    """Return the portable config value for an account's default home."""
    return f"{DEFAULT_ACCOUNTS_ROOT}/{name}"


def _lstat(path: Path) -> Optional[os.stat_result]:
    try:
        return os.lstat(str(path))
    except OSError:
        return None


def ensure_account_home(home: Path) -> bool:
    """Create ``home`` as a private directory, or validate an existing one.

    Returns True when this call created it. A symlink, a non-directory or an
    unreadable path is refused with ``account_home_invalid``. An existing
    directory is tightened to 0700 rather than rejected, so a re-run can resume.
    """
    info = _lstat(home)
    created = False
    if info is not None:
        if stat.S_ISLNK(info.st_mode):
            raise AccountError(
                f"account home '{home}' is a symlink; refusing to use it",
                code="account_home_invalid",
            )
        if not stat.S_ISDIR(info.st_mode):
            raise AccountError(
                f"account home '{home}' exists but is not a directory",
                code="account_home_invalid",
            )
    else:
        try:
            home.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as exc:
            raise AccountError(
                f"cannot create account home '{home}': {exc}",
                code="account_cli_provision_failed",
            ) from exc
        created = True
    try:
        os.chmod(home, 0o700)
    except OSError as exc:
        raise AccountError(
            f"cannot set account home mode 0700: {exc}",
            code="account_cli_provision_failed",
        ) from exc
    info = _lstat(home)
    if info is None or not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
        raise AccountError(
            f"account home '{home}' is not a private directory (mode 0700)",
            code="account_home_permissions",
        )
    return created


def home_is_empty(home: Path) -> bool:
    """True when the home has no entries (the only safe case for a fresh probe)."""
    try:
        return not any(home.iterdir())
    except OSError:
        return False


def _probe_unavailable(reason: str, account: Optional[dict] = None) -> dict:
    """Return an unavailable reading without ever carrying probe output."""
    return {
        "available": False,
        "reason": reason,
        "groups": [],
        "account": account or {"masked_email": None, "auth_method": None},
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _terminate_probe_group(proc: "subprocess.Popen[bytes]") -> None:
    """Stop the whole probe process group (SIGTERM, then SIGKILL after 2 s).

    The unauthenticated agy process keeps running while it waits for its own
    60 s login timeout. The isolation proof is already decided when the marker
    appears, so this function must not let that wait outlive the probe.
    """
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        pgid = proc.pid
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=PROBE_TERMINATE_GRACE_SECONDS)
    except Exception:
        pass
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=PROBE_TERMINATE_GRACE_SECONDS)
    except Exception:
        pass


def _probe_usage_stream(
    account: AccountConfig,
    agy_binary: str,
    timeout_seconds: float,
) -> dict:
    """Run the isolated ``/usage`` probe and stop as soon as auth is required.

    Real agy prints ``Authentication required`` on a fresh home and then keeps
    running for its own 60 s login timeout. Waiting for exit would time out even
    though the isolation fact is already known. This reads merged stdout/stderr
    incrementally, records a boolean when the marker appears, and terminates the
    process group immediately. Output after the marker (which can carry OAuth
    state) is discarded and never logged or returned.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="agy-usage-"))
    try:
        try:
            os.chmod(tmp_dir, 0o700)
        except OSError:
            pass
        log_file = tmp_dir / "usage.log"
        env = build_account_env(account, tmp_dir)
        argv = [
            str(agy_binary),
            "--log-file",
            str(log_file),
            "--output-format",
            "json",
            "-p",
            USAGE_PROMPT,
        ]
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(tmp_dir),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except (OSError, ValueError):
            return _probe_unavailable("unavailable")

        marker = AUTH_REQUIRED_PATTERN.encode("utf-8")
        chunks: list[bytes] = []
        seen_marker = threading.Event()

        def _read_output() -> None:
            fd = proc.stdout.fileno() if proc.stdout is not None else -1
            if fd < 0:
                return
            tail = b""
            while True:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    return
                if not chunk:
                    return
                chunks.append(chunk)
                scan = tail + chunk
                if marker in scan:
                    seen_marker.set()
                # Keep enough of the previous chunk to match a marker split
                # across two os.read boundaries.
                tail = scan[-(len(marker) - 1):] if len(marker) > 1 else b""

        reader = threading.Thread(
            target=_read_output, name="agy-probe-reader", daemon=True
        )
        reader.start()

        deadline = time.monotonic() + timeout_seconds
        early = False
        timed_out = False
        while True:
            if seen_marker.is_set():
                early = True
                break
            if proc.poll() is not None:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            seen_marker.wait(min(0.05, remaining))

        if early or timed_out:
            _terminate_probe_group(proc)
        reader.join(timeout=PROBE_TERMINATE_GRACE_SECONDS)
        combined = b"".join(chunks)

        if timed_out:
            return _probe_unavailable("timeout")
        if len(combined) > MAX_USAGE_STREAM_BYTES:
            return _probe_unavailable("output_too_large")
        if early:
            # The marker alone is the proof. Do not inspect, store or return
            # anything that followed it: it can contain an OAuth state URL.
            return _probe_unavailable("auth_required")

        combined_text = combined.decode("utf-8", errors="replace")
        log_text = ""
        if log_file.is_file():
            try:
                if log_file.stat().st_size <= MAX_USAGE_STREAM_BYTES:
                    log_text = log_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                log_text = ""
        email, auth_method = parse_auth_line(log_text)
        account_info = {
            "masked_email": mask_email(email) if email else None,
            "auth_method": auth_method or None,
        }
        combined_all = "\n".join([combined_text, log_text])
        if matches_patterns(combined_all, [AUTH_REQUIRED_PATTERN]):
            return _probe_unavailable("auth_required", account_info)
        if proc.returncode != 0:
            return _probe_unavailable("command_failed", account_info)
        reading = parse_usage_output(combined_text)
        if not reading.get("available"):
            return _probe_unavailable(
                str(reading.get("reason") or "unrecognized_format"), account_info
            )
        reading["account"] = account_info
        reading["fetched_at"] = datetime.now(timezone.utc).isoformat()
        return reading
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def probe_isolation(
    account: AccountConfig,
    agy_binary: str,
    timeout_seconds: float = ADD_PROBE_TIMEOUT_SECONDS,
) -> dict:
    """Prove the home is isolated: require an auth-required answer and no quota.

    Runs the same isolated ``/usage`` the doctor and scheduler use, but ends as
    soon as the auth-required marker is printed. Quota rows for a home that was
    never logged in mean the keyring leaked; the caller must abort before
    logging in. Returns the unavailable reading so the caller can quote its
    reason.
    """
    reading = _probe_usage_stream(account, agy_binary, timeout_seconds)
    if reading.get("available") is True:
        raise AccountError(
            "the isolated /usage returned quota rows for a home that was never "
            "logged in; the keyring is not isolated, so no login was started",
            code="account_cli_isolation_leak",
        )
    groups = reading.get("groups")
    if reading.get("reason") != "auth_required" or (isinstance(groups, list) and groups):
        reason = str(reading.get("reason") or "unknown")
        raise AccountError(
            "could not prove the home is isolated: the isolated /usage did not "
            f"report 'Authentication required' (reason: {reason})",
            code="account_cli_isolation_unproven",
        )
    return reading


def run_interactive_login(account: AccountConfig, home: Path, agy_binary: str) -> int:
    """Hand the terminal to an isolated ``agy`` so the human can sign in.

    stdout/stdin/stderr are inherited, so the browser flow and any prompt belong
    to the human. The wrapper never supplies credentials and never parses or
    stores this session. Returns agy's exit status.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="agy-login-"))
    try:
        os.chmod(tmp_dir, 0o700)
        env = build_account_env(account, tmp_dir)
        try:
            proc = subprocess.run(
                [agy_binary],
                cwd=str(home),
                env=env,
                check=False,
            )
        except OSError as exc:
            raise AccountError(
                f"cannot launch the interactive agy login: {exc}",
                code="account_cli_login_failed",
            ) from exc
        return int(proc.returncode)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _default_isatty() -> bool:
    import sys

    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def _default_prompt_email() -> str:
    return getpass.getpass("Expected account email (hidden input, never stored): ")


def require_interactive_terminal(isatty: Callable[[], bool]) -> None:
    """Refuse ``add --yes`` unless both stdin and stdout are terminals."""
    if not isatty():
        raise AccountError(
            "`account add` needs an interactive terminal on stdin and stdout; "
            "it is a human login and cannot run from an agent",
            code="account_cli_non_tty",
        )


def verify_login(
    account: AccountConfig,
    agy_binary: str,
    timeout_seconds: float = ADD_PROBE_TIMEOUT_SECONDS,
) -> dict:
    """Verify the post-login isolated ``/usage`` and its identity digest.

    The full observed address stays inside :func:`read_usage_once`; this function
    returns the reading whose account object carries only the masked form and the
    SHA-256 digest. A missing identity or missing quota rows is a failure: the
    login did not produce something the scheduler can use.
    """
    reading = read_usage_once(
        agy_binary,
        account=account,
        timeout_seconds=timeout_seconds,
        auth_error_patterns=[AUTH_REQUIRED_PATTERN],
    )
    if reading.get("available") is not True:
        reason = str(reading.get("reason") or "unknown")
        raise AccountError(
            f"the isolated /usage is not usable after login (reason: {reason})",
            code="account_cli_login_failed",
        )
    account_info = reading.get("account")
    if not isinstance(account_info, dict) or not isinstance(
        account_info.get("identity_sha256"), str
    ):
        raise AccountError(
            "agy did not report the signed-in account; cannot record an identity",
            code="account_identity_unknown",
        )
    return reading


def read_expected_email(prompt: Callable[[], str]) -> str:
    """Ask for the expected address with hidden input and validate its shape."""
    try:
        value = prompt()
    except (EOFError, KeyboardInterrupt) as exc:
        raise AccountError(
            "no expected email was entered; nothing was written",
            code="account_cli_expected_email_required",
        ) from exc
    email = (value or "").strip()
    local, _, domain = email.rpartition("@")
    if not local or not domain or email.count("@") != 1 or any(ch.isspace() for ch in email):
        raise AccountError(
            "the expected email is not a valid address; nothing was written",
            code="account_cli_expected_email_required",
        )
    return email


def _write_bytes(fd: int, data: bytes) -> None:
    """Write every byte to ``fd`` (os.write may be short)."""
    offset = 0
    while offset < len(data):
        offset += os.write(fd, data[offset:])


def _mkdir_private(path: Path) -> None:
    """Create missing directories one level at a time with mode 0700.

    ``Path.mkdir(mode=...)`` applies the mode to the leaf only; the parents keep
    the ambient umask. An account home is 0700 already, but its inner
    ``.gemini`` directories should not be group-readable even if it is not.
    """
    missing: list[Path] = []
    current = path
    while not current.exists() and current != current.parent:
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
            os.chmod(directory, 0o700)
        except OSError as exc:
            raise AccountError(
                f"cannot create '{directory}': {exc}",
                code="account_cli_provision_failed",
            ) from exc


def _assert_no_symlink(path: Path, home: Path) -> None:
    """Refuse a path whose parent chain inside ``home`` contains a symlink."""
    try:
        relative = path.relative_to(home)
    except ValueError as exc:
        raise AccountError(
            f"refusing to write outside the account home: '{path}'",
            code="account_cli_provision_failed",
        ) from exc
    current = home
    for part in relative.parts[:-1]:
        current = current / part
        info = _lstat(current)
        if info is not None and stat.S_ISLNK(info.st_mode):
            raise AccountError(
                f"refusing to follow a symlink inside the account home: '{current}'",
                code="account_cli_provision_failed",
            )


def copy_permission_policy(primary_home: Path, account_home: Path) -> Optional[Path]:
    """Copy the primary policy into the account home once, mode 0600.

    Returns None when the primary has no policy. An existing destination is kept
    untouched: the doctor still reports drift, and the CLI never silently
    weakens or rewrites a file the account already has.
    """
    source = primary_home / AGY_PERMISSION_CONFIG_REL
    try:
        if not source.is_file():
            return None
    except OSError:
        return None
    destination = account_home / AGY_PERMISSION_CONFIG_REL
    _assert_no_symlink(destination, account_home)
    if _lstat(destination) is not None:
        return destination
    _mkdir_private(destination.parent)
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise AccountError(
            f"cannot read the primary permission policy: {exc}",
            code="account_cli_provision_failed",
        ) from exc
    try:
        fd = os.open(
            str(destination),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            _write_bytes(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(destination, 0o600)
    except OSError as exc:
        raise AccountError(
            f"cannot write the account permission policy: {exc}",
            code="account_cli_provision_failed",
        ) from exc
    return destination


def write_settings(account_home: Path, allowed_workdir_roots: list[str]) -> Optional[Path]:
    """Write a minimal settings.json with trusted workspaces, mode 0600.

    The file is created only when absent; an existing file is never overwritten
    because it may carry the user's hand-tuned CLI settings.
    """
    destination = account_home / SETTINGS_REL
    _assert_no_symlink(destination, account_home)
    if _lstat(destination) is not None:
        return destination
    _mkdir_private(destination.parent)
    content = json.dumps(
        {"trustedWorkspaces": list(allowed_workdir_roots)}, indent=2
    ) + "\n"
    try:
        fd = os.open(
            str(destination),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            _write_bytes(fd, content.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(destination, 0o600)
    except OSError as exc:
        raise AccountError(
            f"cannot write the account settings.json: {exc}",
            code="account_cli_provision_failed",
        ) from exc
    return destination


def provision_files(
    account_home: Path,
    primary_home: Path,
    allowed_workdir_roots: list[str],
) -> ProvisionPaths:
    """Place the policy and settings files, returning what already existed too."""
    policy = copy_permission_policy(primary_home, account_home)
    settings = write_settings(account_home, allowed_workdir_roots)
    return ProvisionPaths(policy=policy, settings=settings)
