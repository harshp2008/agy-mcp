"""Pytest test suite configuration and fixtures for agy-mcp."""

import os
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

# 0. Deep-input *generation* only: tests/test_cleanup_jobs_fixes9.py builds
# 1100-level nested dicts and serializes them with json.dumps(), which needs
# more than the default 1000 interpreter frames. Production code must never
# change the process-wide recursion limit (SPEC §23), so the test session
# raises it here; the cleaners under test always run in subprocesses at the
# default limit and must reject deep input iteratively.
if sys.getrecursionlimit() < 5000:
    sys.setrecursionlimit(5000)

# 1. Capture the operator's real home/state path at conftest import time,
# before anything redirects HOME. The sentinel makes a module reload (or an
# xdist worker that inherits an already-redirected HOME) keep the original.
_CAPTURED_REAL_STATE_ENV = "_AGY_MCP_CAPTURED_REAL_LIVE_STATE"
_captured_real_state = os.environ.get(_CAPTURED_REAL_STATE_ENV)
if _captured_real_state:
    _REAL_LIVE_STATE_DIR = Path(_captured_real_state)
else:
    _REAL_LIVE_STATE_DIR = Path.home() / ".local" / "state" / "agy-mcp"
    os.environ[_CAPTURED_REAL_STATE_ENV] = str(_REAL_LIVE_STATE_DIR)

# The checkout under test. When the suite runs inside an agy worktree that lives
# under the live store (jobs/<id>/work), writes to the repo itself are not state
# writes; exempt the repo subtree so pytest can write its own cache/pyc there.
_REPO_ROOT = Path(__file__).resolve().parent.parent

# 2. At conftest import time (before pytest imports any test module), create a
# bootstrap temp home and point HOME, XDG_STATE_HOME and AGY_MCP_STATE_DIR at it.
_BOOTSTRAP_DIR = tempfile.TemporaryDirectory(prefix="agy_conftest_bootstrap_")
_BOOTSTRAP_HOME = Path(_BOOTSTRAP_DIR.name) / "home"
_BOOTSTRAP_XDG = Path(_BOOTSTRAP_DIR.name) / "xdg-state"
_BOOTSTRAP_STATE_ENV = _BOOTSTRAP_XDG / "agy-mcp"
_BOOTSTRAP_HOME.mkdir(parents=True, exist_ok=True)
_BOOTSTRAP_XDG.mkdir(parents=True, exist_ok=True)
_BOOTSTRAP_STATE_ENV.mkdir(parents=True, exist_ok=True)

os.environ["HOME"] = str(_BOOTSTRAP_HOME)
os.environ["XDG_STATE_HOME"] = str(_BOOTSTRAP_XDG)
os.environ["AGY_MCP_STATE_DIR"] = str(_BOOTSTRAP_STATE_ENV)

# Track spawned process IDs explicitly per T2
SPAWNED_PIDS: set[int] = set()

_GUARD_ACTIVE = True


@dataclass(frozen=True)
class IsolatedStatePaths:
    """Session paths used to keep tests away from the real live state dir."""

    home: Path
    xdg_state: Path
    state_env: Path
    real_live_state: Path


def _is_under_live_state(path: object) -> bool:
    """True when ``path`` is the real agy-mcp state dir or inside it."""
    if not isinstance(path, (str, bytes, os.PathLike)):
        return False
    try:
        candidate = Path(os.fsdecode(path)).resolve(strict=False)
        live = _REAL_LIVE_STATE_DIR.resolve(strict=False)
    except (OSError, ValueError):
        return False
    # A worktree under the live store is the repository under test, not job
    # state. This is a no-op when the checkout lives outside the store.
    if candidate == _REPO_ROOT or _REPO_ROOT in candidate.parents:
        return False
    return candidate == live or live in candidate.parents


def _is_write_open(mode: object, flags: object) -> bool:
    """Classify the mode/flags of an ``open`` audit event as a write."""
    if isinstance(mode, str):
        return any(ch in mode for ch in "wax+")
    if isinstance(flags, int):
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
        return bool(flags & write_flags)
    return False


def _live_state_write_guard(event: str, args: tuple[object, ...]) -> None:
    """Audit hook: refuse any in-process write under the real live state dir.

    This is a guard, not a sandbox: it makes a leaking test fail loudly at the
    exact write instead of silently polluting the operator's live state (the
    40 ghost queued jobs in docs/reports/stale-jobs-report.md).
    """
    if not _GUARD_ACTIVE:
        return
    if event == "open":
        path, mode, flags = args[0], args[1], args[2]
        if _is_write_open(mode, flags) and _is_under_live_state(path):
            raise RuntimeError(
                "BLOCKED by test isolation guard: attempted write under the live "
                f"agy-mcp state dir: {path}"
            )
        return
    mutating_events = {
        "os.mkdir",
        "os.rmdir",
        "os.remove",
        "os.rename",
        "os.replace",
        "os.link",
        "os.symlink",
        "os.chmod",
        "os.chown",
        "os.utime",
        "os.truncate",
    }
    if event in mutating_events:
        for path in args[:2]:
            if _is_under_live_state(path):
                raise RuntimeError(
                    "BLOCKED by test isolation guard: attempted mutating operation "
                    f"under the live agy-mcp state dir: {path}"
                )


# Install audit hook at conftest import time
sys.addaudithook(_live_state_write_guard)


def _resolve_dir_fd_target(dir_fd: object, name: object) -> Path | None:
    """Resolve (dir_fd, relative name) through /proc, or None if impossible."""
    if not isinstance(dir_fd, int):
        return None
    try:
        base = Path(f"/proc/self/fd/{dir_fd}").resolve(strict=False)
        return (base / os.fsdecode(name)).resolve(strict=False)
    except (OSError, ValueError):
        return None


def _reject_live_dir_fd(dir_fd: object, name: object, operation: str) -> None:
    """Audit hooks do not expose dir_fd for relative paths; wrappers do."""
    if not _GUARD_ACTIVE:
        return
    target = _resolve_dir_fd_target(dir_fd, name)
    if target is not None and _is_under_live_state(target):
        raise RuntimeError(
            "BLOCKED by test isolation guard: attempted "
            f"{operation} via dir_fd under the live agy-mcp state dir: {target}"
        )


def _install_dir_fd_guard() -> None:
    """Close the dir_fd-relative gap the sys.audit path arguments leave open.

    sys.audit reports only the relative name for calls such as
    ``os.open(name, ..., dir_fd=fd)``, so the hook above cannot tell which
    directory a write is aimed at. These wrappers resolve the fd through
    /proc/self/fd before delegating. They only intercept dir_fd calls; the
    common absolute-path case stays on the audit hook.
    """
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC

    original_open = os.open

    def guarded_open(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is not None and (flags & write_flags):
            _reject_live_dir_fd(dir_fd, path, "write-open")
        return original_open(path, flags, mode, dir_fd=dir_fd)

    original_unlink = os.unlink

    def guarded_unlink(path, *, dir_fd=None):
        if dir_fd is not None:
            _reject_live_dir_fd(dir_fd, path, "unlink")
        return original_unlink(path, dir_fd=dir_fd)

    original_rmdir = os.rmdir

    def guarded_rmdir(path, *, dir_fd=None):
        if dir_fd is not None:
            _reject_live_dir_fd(dir_fd, path, "rmdir")
        return original_rmdir(path, dir_fd=dir_fd)

    original_mkdir = os.mkdir

    def guarded_mkdir(path, mode=0o777, *, dir_fd=None):
        if dir_fd is not None:
            _reject_live_dir_fd(dir_fd, path, "mkdir")
        return original_mkdir(path, mode, dir_fd=dir_fd)

    original_chmod = os.chmod

    def guarded_chmod(path, mode, *, dir_fd=None, follow_symlinks=True):
        if dir_fd is not None:
            _reject_live_dir_fd(dir_fd, path, "chmod")
        return original_chmod(
            path, mode, dir_fd=dir_fd, follow_symlinks=follow_symlinks
        )

    original_chown = os.chown

    def guarded_chown(path, uid, gid, *, dir_fd=None, follow_symlinks=True):
        if dir_fd is not None:
            _reject_live_dir_fd(dir_fd, path, "chown")
        return original_chown(
            path, uid, gid, dir_fd=dir_fd, follow_symlinks=follow_symlinks
        )

    original_utime = os.utime

    def guarded_utime(path, times=None, *, ns=None, dir_fd=None, follow_symlinks=True):
        if dir_fd is not None:
            _reject_live_dir_fd(dir_fd, path, "utime")
        if ns is not None:
            return original_utime(
                path, times, ns=ns, dir_fd=dir_fd, follow_symlinks=follow_symlinks
            )
        return original_utime(
            path, times, dir_fd=dir_fd, follow_symlinks=follow_symlinks
        )

    original_symlink = os.symlink

    def guarded_symlink(src, dst, target_is_directory=False, *, dir_fd=None):
        if dir_fd is not None:
            _reject_live_dir_fd(dir_fd, dst, "symlink")
        return original_symlink(
            src, dst, target_is_directory=target_is_directory, dir_fd=dir_fd
        )

    original_link = os.link

    def guarded_link(src, dst, *, src_dir_fd=None, dst_dir_fd=None, follow_symlinks=True):
        if dst_dir_fd is not None:
            _reject_live_dir_fd(dst_dir_fd, dst, "link")
        return original_link(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    original_rename = os.rename

    def guarded_rename(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        if src_dir_fd is not None:
            _reject_live_dir_fd(src_dir_fd, src, "rename source")
        if dst_dir_fd is not None:
            _reject_live_dir_fd(dst_dir_fd, dst, "rename destination")
        return original_rename(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    original_replace = os.replace

    def guarded_replace(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        if src_dir_fd is not None:
            _reject_live_dir_fd(src_dir_fd, src, "replace source")
        if dst_dir_fd is not None:
            _reject_live_dir_fd(dst_dir_fd, dst, "replace destination")
        return original_replace(
            src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd
        )

    os.open = guarded_open
    os.unlink = guarded_unlink
    os.remove = guarded_unlink
    os.rmdir = guarded_rmdir
    os.mkdir = guarded_mkdir
    os.chmod = guarded_chmod
    os.chown = guarded_chown
    os.utime = guarded_utime
    os.symlink = guarded_symlink
    os.link = guarded_link
    os.rename = guarded_rename
    os.replace = guarded_replace


_install_dir_fd_guard()

# 3. Install an in-process resolution guard before test modules are imported
import agy_mcp.jobs  # noqa: E402
import agy_mcp.manager  # noqa: E402
from agy_mcp.runner import LocalProcessRunner  # noqa: E402
from tests.helpers.make_repo import get_safe_git_env  # noqa: E402

_orig_get_base_state_dir = agy_mcp.jobs.get_base_state_dir


def _guarded_get_base_state_dir(base_state_dir: Path | None = None) -> Path:
    res = _orig_get_base_state_dir(base_state_dir)
    if _is_under_live_state(res):
        raise RuntimeError(
            f"Access to live agy-mcp state directory is forbidden in tests: {res}"
        )
    return res


agy_mcp.jobs.get_base_state_dir = _guarded_get_base_state_dir
agy_mcp.manager.get_base_state_dir = _guarded_get_base_state_dir
for mod_name, mod in list(sys.modules.items()):
    if mod_name.startswith("agy_mcp") and hasattr(mod, "get_base_state_dir"):
        setattr(mod, "get_base_state_dir", _guarded_get_base_state_dir)


@pytest.fixture(scope="session", autouse=True)
def isolated_state(tmp_path_factory: pytest.TempPathFactory) -> IsolatedStatePaths:
    """Keep HOME, XDG_STATE_HOME and AGY_MCP_STATE_DIR off the live state dir.

    Session-scoped and autouse, so even a test that forgets an explicit
    ``base_state_dir`` cannot resolve ``~/.local/state/agy-mcp``. The audit hook
    additionally fails any in-process attempt to write there.
    """
    base = tmp_path_factory.mktemp("agy_state_isolation")
    home = base / "home"
    xdg_state = base / "xdg-state"
    state_env = xdg_state / "agy-mcp"
    home.mkdir()
    xdg_state.mkdir()
    state_env.mkdir()

    old_env = {
        key: os.environ.get(key)
        for key in ("HOME", "XDG_STATE_HOME", "AGY_MCP_STATE_DIR")
    }
    os.environ["HOME"] = str(home)
    os.environ["XDG_STATE_HOME"] = str(xdg_state)
    # Not read by agy-mcp today, but the installer/run.sh exports it; keep the
    # whole family of state resolution paths pointed at the session temp dir.
    os.environ["AGY_MCP_STATE_DIR"] = str(state_env)

    paths = IsolatedStatePaths(
        home=home,
        xdg_state=xdg_state,
        state_env=state_env,
        real_live_state=_REAL_LIVE_STATE_DIR,
    )
    try:
        yield paths
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

_orig_launch = LocalProcessRunner.launch


def _tracked_launch(self, *args, **kwargs):
    handle = _orig_launch(self, *args, **kwargs)
    if hasattr(handle, "pid") and handle.pid:
        SPAWNED_PIDS.add(handle.pid)
    return handle


LocalProcessRunner.launch = _tracked_launch

_orig_popen_init = subprocess.Popen.__init__


def _tracked_popen_init(self, *args, **kwargs):
    _orig_popen_init(self, *args, **kwargs)
    if hasattr(self, "pid") and self.pid:
        SPAWNED_PIDS.add(self.pid)


subprocess.Popen.__init__ = _tracked_popen_init


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    """Provide an isolated HOME directory for test execution."""
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    return home_dir


@pytest.fixture
def safe_git_env(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Provide a sanitized environment dictionary and set environment variables."""
    env = get_safe_git_env(fake_home)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return env


def is_pid_alive(pid: int) -> bool:
    """Check if process is truly alive (not a zombie or reaped)."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    stat_file = Path(f"/proc/{pid}/stat")
    if stat_file.is_file():
        try:
            content = stat_file.read_text()
            idx = content.rfind(")")
            if idx != -1 and idx + 2 < len(content):
                state_char = content[idx + 2]
                if state_char in ("Z", "X"):
                    return False
        except OSError:
            return False
    return True


def pytest_sessionstart(session: pytest.Session) -> None:
    """Record initial git working tree status at the start of the test session."""
    repo_root = Path(__file__).parent.parent.resolve()
    res = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    session.config._initial_git_status = res.stdout


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Verify no orphaned processes remain and no tracked/untracked repo files changed."""
    import time

    # 1. Clean up only explicitly tracked processes spawned during test execution
    time.sleep(0.05)
    lingering_pids = []
    for pid in list(SPAWNED_PIDS):
        if is_pid_alive(pid):
            lingering_pids.append(pid)
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    if lingering_pids:
        time.sleep(0.05)
        unreaped = [p for p in lingering_pids if is_pid_alive(p)]
        if unreaped:
            pytest.fail(
                f"Lingering test-spawned processes detected after test session: {unreaped}"
            )

    # 2. Verify git status has not changed / no new untracked or modified repo files
    repo_root = Path(__file__).parent.parent.resolve()
    res = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    current_status = res.stdout
    initial_status = getattr(session.config, "_initial_git_status", "")

    # Check for any new or modified files introduced by tests
    initial_lines = set(initial_status.splitlines())
    current_lines = set(current_status.splitlines())
    introduced = current_lines - initial_lines
    if introduced:
        pytest.fail(
            "Test execution left untracked or modified repository files:\n"
            + "\n".join(sorted(introduced))
        )
