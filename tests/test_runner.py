"""Unit tests for LocalProcessRunner, argv construction, and process isolation."""

import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from agy_mcp.runner import (
    LocalProcessRunner,
    PromptTooLargeError,
    RunnerError,
    build_argv,
    read_proc_start_time,
)
from agy_mcp.util import env_allowlist

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"


def test_build_argv_flag_ordering_and_rules(tmp_path: Path) -> None:
    log_file = tmp_path / "cli.log"
    argv = build_argv(
        agy_binary=FAKE_AGY,
        prompt="Implement feature X",
        model="gemini-3.8-flash-high",
        print_timeout_seconds=300,
        log_file=log_file,
        conversation_id="11111111-1111-4111-8111-111111111111",
    )

    # In real agy, flags must come before -p, and -p must be last
    assert argv[0] == str(FAKE_AGY.resolve())
    assert "--mode" in argv
    assert "accept-edits" in argv
    assert "--output-format" in argv
    assert "json" in argv
    assert "--print-timeout" in argv
    assert "300s" in argv
    assert "--log-file" in argv
    assert str(log_file.resolve()) in argv
    assert "--model" in argv
    assert "gemini-3.8-flash-high" in argv
    assert "--conversation" in argv
    assert "11111111-1111-4111-8111-111111111111" in argv

    # -p must be the second to last element, prompt the last
    assert argv[-2] == "-p"
    assert argv[-1] == "Implement feature X"


def test_build_argv_forbidden_flags(tmp_path: Path) -> None:
    log_file = tmp_path / "cli.log"
    with pytest.raises(RunnerError) as exc:
        build_argv(
            agy_binary=FAKE_AGY,
            prompt="Task",
            model="gemini-3.8-flash-medium",
            print_timeout_seconds=60,
            log_file=log_file,
            mode="auto",
        )
    assert exc.value.code == "invalid_argv"

    with pytest.raises(RunnerError) as exc:
        build_argv(
            agy_binary=FAKE_AGY,
            prompt="Task",
            model="gemini-3.8-flash-medium",
            print_timeout_seconds=60,
            log_file=log_file,
            output_format="stream-json",
        )
    assert exc.value.code == "invalid_argv"

    with pytest.raises(RunnerError) as exc:
        build_argv(
            agy_binary=FAKE_AGY,
            prompt="Task",
            model="gemini-3.8-flash-medium",
            print_timeout_seconds=0,
            log_file=log_file,
        )
    assert exc.value.code == "invalid_argv"


def test_build_argv_prompt_size_limit(tmp_path: Path) -> None:
    log_file = tmp_path / "cli.log"
    huge_prompt = "x" * 100_001
    with pytest.raises(PromptTooLargeError):
        build_argv(
            agy_binary=FAKE_AGY,
            prompt=huge_prompt,
            model="gemini-3.8-flash-medium",
            print_timeout_seconds=60,
            log_file=log_file,
        )


def test_runner_launch_process_group_and_streams(tmp_path: Path) -> None:
    runner = LocalProcessRunner(executable=FAKE_AGY)
    stdout_file = tmp_path / "stdout.txt"
    stderr_file = tmp_path / "stderr.txt"
    log_file = tmp_path / "cli.log"
    tmp_env_dir = tmp_path / "tmp_job"
    tmp_env_dir.mkdir()

    env = env_allowlist(job_tmp_dir=tmp_env_dir, home=tmp_path)
    argv = build_argv(
        agy_binary=FAKE_AGY,
        prompt="Execute task __SCENARIO:success_simple__",
        model="gemini-3.8-flash-medium",
        print_timeout_seconds=60,
        log_file=log_file,
    )

    handle = runner.launch(
        job_id="test-job-001",
        workdir=tmp_path,
        args=argv,
        env=env,
        stdout_path=stdout_file,
        stderr_path=stderr_file,
    )

    assert handle.pid > 0
    assert handle.pgid > 0
    assert handle.start_time > 0
    assert handle.pgid == handle.pid  # New session leader

    # Wait for completion
    deadline = time.time() + 5.0
    rc = None
    while time.time() < deadline:
        rc = runner.poll(handle)
        if rc is not None:
            break
        time.sleep(0.05)

    assert rc == 0
    runner.cleanup(handle)

    # Check streams
    assert stdout_file.is_file()
    out_data = json.loads(stdout_file.read_text())
    assert out_data["status"] == "SUCCESS"


def test_runner_stdin_is_devnull(tmp_path: Path) -> None:
    runner = LocalProcessRunner(executable=FAKE_AGY)
    stdout_file = tmp_path / "stdout.txt"
    stderr_file = tmp_path / "stderr.txt"
    log_file = tmp_path / "cli.log"
    tmp_env_dir = tmp_path / "tmp_job"
    tmp_env_dir.mkdir()

    env = env_allowlist(job_tmp_dir=tmp_env_dir, home=tmp_path)
    argv = build_argv(
        agy_binary=FAKE_AGY,
        prompt="Probe stdin __SCENARIO:stdin_probe__",
        model="gemini-3.8-flash-medium",
        print_timeout_seconds=60,
        log_file=log_file,
    )

    handle = runner.launch(
        job_id="test-job-stdin",
        workdir=tmp_path,
        args=argv,
        env=env,
        stdout_path=stdout_file,
        stderr_path=stderr_file,
    )

    deadline = time.time() + 5.0
    while time.time() < deadline:
        if runner.poll(handle) is not None:
            break
        time.sleep(0.05)

    runner.cleanup(handle)
    probe_result = tmp_path / "stdin_probe.txt"
    assert probe_result.is_file()
    assert "has_input=False" in probe_result.read_text()


def test_runner_environment_scrubbing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_SECRET", "super-secret-token-12345")
    runner = LocalProcessRunner(executable=FAKE_AGY)
    stdout_file = tmp_path / "stdout.txt"
    stderr_file = tmp_path / "stderr.txt"
    log_file = tmp_path / "cli.log"
    tmp_env_dir = tmp_path / "tmp_job"
    tmp_env_dir.mkdir()

    env = env_allowlist(job_tmp_dir=tmp_env_dir, home=tmp_path)
    argv = build_argv(
        agy_binary=FAKE_AGY,
        prompt="Probe env __SCENARIO:env_probe__",
        model="gemini-3.8-flash-medium",
        print_timeout_seconds=60,
        log_file=log_file,
    )

    handle = runner.launch(
        job_id="test-job-env",
        workdir=tmp_path,
        args=argv,
        env=env,
        stdout_path=stdout_file,
        stderr_path=stderr_file,
    )

    deadline = time.time() + 5.0
    while time.time() < deadline:
        if runner.poll(handle) is not None:
            break
        time.sleep(0.05)

    runner.cleanup(handle)
    dump_file = tmp_env_dir / "env_dump.json"
    assert dump_file.is_file()
    child_env = json.loads(dump_file.read_text())

    # Child must have only allowlisted environment keys
    allowed_keys = {
        "HOME",
        "PATH",
        "LANG",
        "TMPDIR",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "GIT_CONFIG_KEY_1",
        "GIT_CONFIG_VALUE_1",
        "GIT_CONFIG_KEY_2",
        "GIT_CONFIG_VALUE_2",
    }
    assert "FAKE_SECRET" not in child_env
    for k in child_env:
        assert k in allowed_keys, f"Forbidden environment key leaked to child: {k}"


def test_runner_kill_graceful_sigterm(tmp_path: Path) -> None:
    runner = LocalProcessRunner(executable=FAKE_AGY)
    stdout_file = tmp_path / "stdout.txt"
    stderr_file = tmp_path / "stderr.txt"
    log_file = tmp_path / "cli.log"
    tmp_env_dir = tmp_path / "tmp_job"
    tmp_env_dir.mkdir()

    env = env_allowlist(job_tmp_dir=tmp_env_dir, home=tmp_path)
    argv = build_argv(
        agy_binary=FAKE_AGY,
        prompt="Hang test __SCENARIO:hang__",
        model="gemini-3.8-flash-medium",
        print_timeout_seconds=60,
        log_file=log_file,
    )

    handle = runner.launch(
        job_id="test-job-hang",
        workdir=tmp_path,
        args=argv,
        env=env,
        stdout_path=stdout_file,
        stderr_path=stderr_file,
    )

    # Process is running
    assert runner.poll(handle) is None

    # Kill with SIGTERM
    start = time.time()
    runner.kill(handle, grace_seconds=2.0)
    elapsed = time.time() - start

    assert runner.poll(handle) is not None
    # Terminated quickly by SIGTERM, did not need to wait full grace_seconds
    assert elapsed < 1.5
    runner.cleanup(handle)


def test_runner_kill_escalates_to_sigkill(tmp_path: Path) -> None:
    runner = LocalProcessRunner(executable=FAKE_AGY)
    stdout_file = tmp_path / "stdout.txt"
    stderr_file = tmp_path / "stderr.txt"
    log_file = tmp_path / "cli.log"
    tmp_env_dir = tmp_path / "tmp_job"
    tmp_env_dir.mkdir()

    env = env_allowlist(job_tmp_dir=tmp_env_dir, home=tmp_path)
    argv = build_argv(
        agy_binary=FAKE_AGY,
        prompt="Hang test __SCENARIO:hang_ignore_sigterm__",
        model="gemini-3.8-flash-medium",
        print_timeout_seconds=60,
        log_file=log_file,
    )

    handle = runner.launch(
        job_id="test-job-stubborn",
        workdir=tmp_path,
        args=argv,
        env=env,
        stdout_path=stdout_file,
        stderr_path=stderr_file,
    )

    assert runner.poll(handle) is None

    # Do not signal until the child has installed its SIGTERM handler; otherwise
    # the group can die from the default handler (-SIGTERM) instead of being
    # escalated to SIGKILL. Same readiness pattern as the usage-kill test.
    ready_file = tmp_env_dir / ".fake_agy_sigterm_ready"
    deadline = time.time() + 5.0
    while time.time() < deadline and not ready_file.is_file():
        time.sleep(0.02)
    assert ready_file.is_file(), "fake agy did not install its SIGTERM handler in time"
    assert runner.poll(handle) is None

    # Kill with short grace period
    runner.kill(handle, grace_seconds=0.3)

    rc = runner.poll(handle)
    assert rc is not None
    assert rc == -signal.SIGKILL
    runner.cleanup(handle)


def test_runner_pid_reuse_detection_via_proc_stat(tmp_path: Path) -> None:
    # Read current test process start time
    my_pid = os.getpid()
    start_time = read_proc_start_time(my_pid)
    assert start_time > 0

    runner = LocalProcessRunner(executable=FAKE_AGY)
    # Construct a handle pointing to current process pid but with an incorrect start_time
    from agy_mcp.runner import ProcessHandle

    handle = ProcessHandle(
        pid=my_pid,
        pgid=os.getpgid(my_pid),
        start_time=start_time + 99999,  # Simulated different start time (PID reused)
        job_id="test-reused",
        stdout_path=tmp_path / "out.txt",
        stderr_path=tmp_path / "err.txt",
    )

    # Poll must detect PID reuse and report terminated (not None)
    rc = runner.poll(handle)
    assert rc is not None


def test_runner_kill_grandchild_under_8s(tmp_path: Path) -> None:
    """Verify process group termination kills grandchild and completes within 8s."""
    pids_file = tmp_path / "pids.txt"
    hang_script = tmp_path / "hang_tree.py"
    hang_script.write_text(f"""#!/usr/bin/env python3
import os, sys, subprocess, signal, time
from pathlib import Path

# Ignore SIGTERM to force SIGKILL escalation
signal.signal(signal.SIGTERM, signal.SIG_IGN)

cmd = ["import signal, time", "signal.signal(signal.SIGTERM, signal.SIG_IGN)", "time.sleep(100)"]
grandchild = subprocess.Popen([sys.executable, "-c", "; ".join(cmd)])
Path({repr(str(pids_file))}).write_text(f"{{os.getpid()}}:{{grandchild.pid}}")
time.sleep(100)
""")
    hang_script.chmod(0o755)

    runner = LocalProcessRunner(executable=Path(sys.executable))
    stdout_file = tmp_path / "stdout.txt"
    stderr_file = tmp_path / "stderr.txt"
    tmp_env_dir = tmp_path / "tmp_job"
    tmp_env_dir.mkdir()
    env = env_allowlist(job_tmp_dir=tmp_env_dir, home=tmp_path)

    handle = runner.launch(
        job_id="test-hang-tree",
        workdir=tmp_path,
        args=[sys.executable, str(hang_script)],
        env=env,
        stdout_path=stdout_file,
        stderr_path=stderr_file,
    )

    # Wait for pids_file
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if pids_file.is_file():
            break
        time.sleep(0.05)

    assert pids_file.is_file()
    parent_pid_str, grandchild_pid_str = pids_file.read_text().strip().split(":")
    parent_pid = int(parent_pid_str)
    grandchild_pid = int(grandchild_pid_str)

    # Kill process group with grace_seconds=2.0
    start = time.time()
    runner.kill(handle, grace_seconds=2.0)
    kill_elapsed = time.time() - start

    assert kill_elapsed < 8.0, f"Kill time {kill_elapsed}s exceeded 8.0s limit"

    # Both parent and grandchild must be dead
    time.sleep(0.2)

    def is_pid_alive(pid: int) -> bool:
        if os.path.exists(f"/proc/{pid}"):
            try:
                stat_content = Path(f"/proc/{pid}/stat").read_text()
                state = stat_content.split()[2]
                return state not in ("Z", "X")
            except Exception:
                return False
        return False

    assert not is_pid_alive(parent_pid)
    assert not is_pid_alive(grandchild_pid)
    runner.cleanup(handle)


def test_argv_exact_fresh(tmp_path: Path) -> None:
    """Assert COMPLETE argv matches SPEC section 10 character for character for fresh job."""
    record_file = tmp_path / "fresh_record.jsonl"
    record_pointer = tmp_path / ".fake_agy_record"
    record_pointer.write_text(str(record_file))

    try:
        runner = LocalProcessRunner(executable=FAKE_AGY)
        stdout_file = tmp_path / "stdout.txt"
        stderr_file = tmp_path / "stderr.txt"
        log_file = tmp_path / "cli.log"
        tmp_env_dir = tmp_path / "tmp_job"
        tmp_env_dir.mkdir()

        env = env_allowlist(job_tmp_dir=tmp_env_dir, home=tmp_path)
        fresh_prompt = "Perform task A"
        argv = build_argv(
            agy_binary=FAKE_AGY,
            prompt=fresh_prompt,
            model="gemini-3.8-flash-medium",
            print_timeout_seconds=120,
            log_file=log_file,
        )

        handle = runner.launch(
            job_id="test-job-fresh-argv",
            workdir=tmp_path,
            args=argv,
            env=env,
            stdout_path=stdout_file,
            stderr_path=stderr_file,
        )

        deadline = time.time() + 5.0
        while time.time() < deadline:
            if runner.poll(handle) is not None:
                break
            time.sleep(0.05)
        runner.cleanup(handle)

        assert record_file.is_file()
        lines = [json.loads(line) for line in record_file.read_text().splitlines() if line.strip()]
        assert len(lines) >= 1
        recorded_argv = lines[0]

        expected_argv_prefix = [
            str(FAKE_AGY.resolve()),
            "--mode",
            "accept-edits",
            "--output-format",
            "json",
            "--print-timeout",
            "120s",
            "--log-file",
            str(log_file.resolve()),
            "--model",
            "gemini-3.8-flash-medium",
            "-p",
        ]

        # Full length check: prefix + 1 prompt argument
        assert len(recorded_argv) == len(expected_argv_prefix) + 1
        # Character-for-character check of all flags in exact order
        assert recorded_argv[:-1] == expected_argv_prefix
        # Prompt checked separately
        assert recorded_argv[-1] == fresh_prompt
    finally:
        try:
            record_pointer.unlink(missing_ok=True)
        except Exception:
            pass


def test_argv_exact_resume(tmp_path: Path) -> None:
    """Assert COMPLETE argv matches SPEC section 10 character for character for resume job."""
    record_file = tmp_path / "resume_record.jsonl"
    record_pointer = tmp_path / ".fake_agy_record"
    record_pointer.write_text(str(record_file))

    try:
        runner = LocalProcessRunner(executable=FAKE_AGY)
        stdout_file = tmp_path / "stdout.txt"
        stderr_file = tmp_path / "stderr.txt"
        log_file = tmp_path / "cli.log"
        tmp_env_dir = tmp_path / "tmp_job"
        tmp_env_dir.mkdir()

        env = env_allowlist(job_tmp_dir=tmp_env_dir, home=tmp_path)
        resume_prompt = "Resume with decision B"
        argv = build_argv(
            agy_binary=FAKE_AGY,
            prompt=resume_prompt,
            model="gemini-3.8-flash-medium",
            print_timeout_seconds=95,
            log_file=log_file,
            conversation_id="22222222-2222-4222-8222-222222222222",
        )

        handle = runner.launch(
            job_id="test-job-resume-argv",
            workdir=tmp_path,
            args=argv,
            env=env,
            stdout_path=stdout_file,
            stderr_path=stderr_file,
        )

        deadline = time.time() + 5.0
        while time.time() < deadline:
            if runner.poll(handle) is not None:
                break
            time.sleep(0.05)
        runner.cleanup(handle)

        assert record_file.is_file()
        lines = [json.loads(line) for line in record_file.read_text().splitlines() if line.strip()]
        assert len(lines) >= 1
        recorded_argv = lines[0]

        expected_argv_prefix = [
            str(FAKE_AGY.resolve()),
            "--mode",
            "accept-edits",
            "--output-format",
            "json",
            "--print-timeout",
            "95s",
            "--log-file",
            str(log_file.resolve()),
            "--model",
            "gemini-3.8-flash-medium",
            "--conversation",
            "22222222-2222-4222-8222-222222222222",
            "-p",
        ]

        # Full length check: prefix + 1 prompt argument
        assert len(recorded_argv) == len(expected_argv_prefix) + 1
        # Character-for-character check of all flags in exact order
        assert recorded_argv[:-1] == expected_argv_prefix
        # Prompt checked separately
        assert recorded_argv[-1] == resume_prompt
    finally:
        try:
            record_pointer.unlink(missing_ok=True)
        except Exception:
            pass
