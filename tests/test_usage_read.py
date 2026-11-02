"""Tests for reading agy `/usage` output and masking the account email."""

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from agy_mcp.usage import UsageCache, _kill_usage_process, get_usage

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        content = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    rparen = content.rfind(")")
    return rparen == -1 or content[rparen + 2] not in ("Z", "X")

# Built from parts so the repository never contains an unmasked account email literal.
UNMASKED_LOCAL = "fake" + ".quota"
UNMASKED_DOMAIN = "example" + ".invalid"
UNMASKED_EMAIL = f"{UNMASKED_LOCAL}@{UNMASKED_DOMAIN}"
MASKED_EMAIL = "fa***@example.invalid"


def _read(tmp_path: Path, scenario: str, **kwargs) -> dict:
    cache = kwargs.pop("cache", UsageCache())
    return get_usage(
        FAKE_AGY,
        home=tmp_path,
        cache=cache,
        env_overrides={"FAKE_AGY_SCENARIO": scenario},
        **kwargs,
    )


def test_get_usage_real_fixture_and_mask(tmp_path: Path) -> None:
    """get_usage parses the real fixture and returns only a masked account email."""
    reading = _read(tmp_path, "usage_real", now_monotonic=1000.0)

    assert reading["available"] is True
    assert reading["cached"] is False
    assert reading["account"]["masked_email"] == MASKED_EMAIL
    assert reading["account"]["auth_method"] == "consumer"
    assert len(reading["groups"]) == 2
    assert reading["groups"][0]["name"] == "Gemini Models"

    dump = json.dumps(reading)
    assert UNMASKED_EMAIL not in dump
    assert UNMASKED_LOCAL not in dump
    assert MASKED_EMAIL in dump


def test_get_usage_cache_hit_and_expiry(tmp_path: Path) -> None:
    """The reading is cached in memory for 60 seconds and then refreshed."""
    cache = UsageCache()
    first = _read(tmp_path, "usage_real", cache=cache, now_monotonic=1000.0)
    assert first["cached"] is False

    second = _read(tmp_path, "usage_real", cache=cache, now_monotonic=1030.0)
    assert second["cached"] is True
    assert second["groups"] == first["groups"]

    expired = _read(tmp_path, "usage_real", cache=cache, now_monotonic=1061.0)
    assert expired["cached"] is False

    cache.clear()
    cleared = _read(tmp_path, "usage_real", cache=cache, now_monotonic=1062.0)
    assert cleared["cached"] is False


def test_get_usage_zero_variant(tmp_path: Path) -> None:
    """A 0% scenario reports zero remaining fractions."""
    reading = _read(tmp_path, "usage_zero", now_monotonic=1000.0)
    assert reading["available"] is True
    for group in reading["groups"]:
        for bucket in group["buckets"]:
            assert bucket["remaining_fraction"] == 0.0


def test_get_usage_garbled_variant(tmp_path: Path) -> None:
    """Unrecognized output fails soft with unrecognized_format."""
    reading = _read(tmp_path, "usage_garbled", now_monotonic=1000.0)
    assert reading["available"] is False
    assert reading["reason"] == "unrecognized_format"
    assert reading["groups"] == []


def test_get_usage_empty_variant(tmp_path: Path) -> None:
    """Empty output fails soft with unrecognized_format."""
    reading = _read(tmp_path, "usage_empty", now_monotonic=1000.0)
    assert reading["available"] is False
    assert reading["reason"] == "unrecognized_format"


def test_get_usage_auth_required(tmp_path: Path) -> None:
    """A login-required response is detected from the /usage output."""
    reading = _read(
        tmp_path,
        "usage_auth_required",
        now_monotonic=1000.0,
        auth_error_patterns=["not logged in"],
    )
    assert reading["available"] is False
    assert reading["reason"] == "auth_required"

    without_patterns = _read(tmp_path, "usage_auth_required", now_monotonic=1001.0)
    assert without_patterns["available"] is False
    assert without_patterns["reason"] == "command_failed"


def test_get_usage_timeout_kills_process_group(tmp_path: Path) -> None:
    """A hanging /usage invocation is killed and reported as timeout."""
    started = time.monotonic()
    reading = _read(tmp_path, "usage_hang", now_monotonic=1000.0, timeout_seconds=0.5)
    elapsed = time.monotonic() - started

    assert reading["available"] is False
    assert reading["reason"] == "timeout"
    assert elapsed < 10.0


def test_get_usage_missing_binary_fails_soft(tmp_path: Path) -> None:
    """A missing binary returns unavailable instead of raising."""
    reading = get_usage(
        tmp_path / "does-not-exist",
        home=tmp_path,
        cache=UsageCache(),
        now_monotonic=1000.0,
    )
    assert reading["available"] is False
    assert reading["reason"] == "unavailable"


def test_get_usage_runs_in_fresh_temp_dir_outside_repo(tmp_path: Path) -> None:
    """The /usage invocation starts in a fresh empty directory, never the repo."""
    record = tmp_path / "record.jsonl"
    usage_record = tmp_path / "record.jsonl.usage"
    cache = UsageCache()
    # The fake records its cwd when FAKE_AGY_RECORD is present in its env.
    reading = get_usage(
        FAKE_AGY,
        home=tmp_path,
        cache=cache,
        now_monotonic=1000.0,
        env_overrides={
            "FAKE_AGY_SCENARIO": "usage_real",
            "FAKE_AGY_RECORD": str(record),
        },
    )
    assert reading["available"] is True
    recorded = json.loads(usage_record.read_text().splitlines()[-1])
    argv = recorded["argv"]
    assert argv[-1] == "/usage"
    assert any(a.startswith("/") and a.endswith("usage.log") for a in argv)
    cwd = Path(recorded["cwd"])
    assert cwd.name.startswith("agy-usage-")
    assert not (cwd / ".git").exists()
    assert Path(tempfile.gettempdir()).resolve() in cwd.parents


def test_kill_usage_process_always_sigkills_surviving_child(tmp_path: Path) -> None:
    """After SIGTERM and wait, the whole group gets SIGKILL even if the leader exited."""
    script = tmp_path / "leader_exits_child_survives.py"
    script.write_text(
        "import os, subprocess, sys\n"
        "from pathlib import Path\n"
        "child = subprocess.Popen([\n"
        "    sys.executable,\n"
        "    '-c',\n"
        "    (\n"
        "        'import signal,sys,time\\n'\n"
        "        'signal.signal(signal.SIGTERM, signal.SIG_IGN)\\n'\n"
        "        'open(sys.argv[1], \"w\").write(\"ready\")\\n'\n"
        "        'time.sleep(60)\\n'\n"
        "    ),\n"
        "    sys.argv[2],\n"
        "])\n"
        "Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8')\n"
        "os._exit(0)\n",
        encoding="utf-8",
    )
    pid_file = tmp_path / "child.pid"
    ready_file = tmp_path / "child.ready"
    proc = subprocess.Popen(
        [sys.executable, str(script), str(pid_file), str(ready_file)],
        start_new_session=True,
    )

    child_pid = 0
    try:
        deadline = time.time() + 10.0
        while time.time() < deadline and not (pid_file.is_file() and ready_file.is_file()):
            time.sleep(0.02)
        assert pid_file.is_file()
        assert ready_file.is_file(), "child did not install its SIGTERM handler in time"
        child_pid = int(pid_file.read_text(encoding="utf-8").strip())

        proc.wait(timeout=10.0)
        assert _pid_alive(child_pid), "the ignored-SIGTERM child should outlive its leader"

        _kill_usage_process(proc)

        deadline = time.time() + 5.0
        while time.time() < deadline and _pid_alive(child_pid):
            time.sleep(0.05)
        assert not _pid_alive(child_pid), "surviving child outlived _kill_usage_process"
    finally:
        if child_pid and _pid_alive(child_pid):
            try:
                os.kill(child_pid, 9)
            except OSError:
                pass


def test_get_usage_does_not_cache_unavailable_reading(tmp_path: Path) -> None:
    """An unavailable reading is never cached; the next call runs /usage again."""
    cache = UsageCache()
    first = _read(tmp_path, "usage_garbled", cache=cache, now_monotonic=1000.0)
    assert first["available"] is False

    second = _read(tmp_path, "usage_real", cache=cache, now_monotonic=1001.0)
    assert second["available"] is True
    assert second["cached"] is False


def _make_oversize_usage_binary(tmp_path: Path) -> Path:
    """Create a fake agy that floods one /usage stream with 1 MB + 1 byte."""
    binary = tmp_path / "oversize_usage_agy"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "from pathlib import Path\n"
        "payload = 'x' * (1024 * 1024 + 1)\n"
        "mode = os.environ.get('FAKE_USAGE_OVERSIZE', '')\n"
        "if mode == 'stdout':\n"
        "    sys.stdout.write(payload)\n"
        "    sys.exit(0)\n"
        "if mode == 'stderr':\n"
        "    sys.stderr.write(payload)\n"
        "    sys.exit(0)\n"
        "log_file = sys.argv[sys.argv.index('--log-file') + 1]\n"
        "Path(log_file).write_text(payload, encoding='utf-8')\n"
        "sys.stdout.write('{\"status\":\"SUCCESS\",\"response\":\"\",\"command\":{\"data\":{}}}')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return binary


@pytest.mark.parametrize("stream", ["stdout", "stderr", "log"])
def test_get_usage_oversize_stream_is_rejected(tmp_path: Path, stream: str) -> None:
    """stdout, stderr and the temp log are each capped at 1 MB with output_too_large."""
    binary = _make_oversize_usage_binary(tmp_path)
    reading = get_usage(
        binary,
        home=tmp_path,
        cache=UsageCache(),
        now_monotonic=1000.0,
        env_overrides={"FAKE_USAGE_OVERSIZE": stream},
    )
    assert reading["available"] is False
    assert reading["reason"] == "output_too_large"
