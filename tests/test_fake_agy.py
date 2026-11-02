"""Tests for fake agy executable CLI parsing and scenario simulation."""

import json
import os
import signal
import subprocess
from pathlib import Path

FAKE_AGY = Path(__file__).parent / "fake_agy" / "agy"


def run_fake_agy(
    tmp_path: Path,
    prompt: str,
    extra_args: list[str] | None = None,
    env: dict[str, str] | None = None,
    stdin=None,
    mode: str = "accept-edits",
    output_format: str = "json",
    print_timeout: str = "60s",
    model: str = "gemini-3.8-flash-medium",
    log_file: Path | None = None,
) -> subprocess.CompletedProcess:
    """Helper to invoke fake_agy with standard required flags."""
    log = log_file or (tmp_path / "cli.log")
    cmd = [
        str(FAKE_AGY),
        "--mode",
        mode,
        "--output-format",
        output_format,
        "--print-timeout",
        print_timeout,
        "--log-file",
        str(log.resolve()),
        "--model",
        model,
    ]
    if extra_args:
        cmd.extend(extra_args)
    cmd.extend(["-p", prompt])
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        stdin=stdin,
    )


def test_fake_agy_executable_exists() -> None:
    assert FAKE_AGY.is_file()
    assert os.access(FAKE_AGY, os.X_OK)


# ---------------------------------------------------------------------------
# Strict Rejection Tests (Part B)
# ---------------------------------------------------------------------------


def test_fake_agy_rejects_unknown_flags(tmp_path: Path) -> None:
    res = subprocess.run(
        [
            str(FAKE_AGY),
            "--mode",
            "accept-edits",
            "--output-format",
            "json",
            "--print-timeout",
            "60s",
            "--log-file",
            str((tmp_path / "cli.log").resolve()),
            "--model",
            "gemini-3.8-flash-medium",
            "--unknown-flag",
            "-p",
            "hello",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert res.returncode == 1
    assert "unknown option" in res.stderr


def test_fake_agy_rejects_effort_flag(tmp_path: Path) -> None:
    res = subprocess.run(
        [
            str(FAKE_AGY),
            "--mode",
            "accept-edits",
            "--output-format",
            "json",
            "--print-timeout",
            "60s",
            "--log-file",
            str((tmp_path / "cli.log").resolve()),
            "--model",
            "gemini-3.8-flash-medium",
            "--effort",
            "high",
            "-p",
            "hello",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert res.returncode == 1
    assert "--effort flag is not supported" in res.stderr


def test_fake_agy_rejects_mode_not_accept_edits(tmp_path: Path) -> None:
    res = run_fake_agy(tmp_path, prompt="hello", mode="headless")
    assert res.returncode == 1
    assert 'unrecognized --mode value "headless"' in res.stderr


def test_fake_agy_rejects_mode_missing(tmp_path: Path) -> None:
    res = subprocess.run(
        [
            str(FAKE_AGY),
            "--output-format",
            "json",
            "--print-timeout",
            "60s",
            "--log-file",
            str((tmp_path / "cli.log").resolve()),
            "--model",
            "gemini-3.8-flash-medium",
            "-p",
            "hello",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert res.returncode == 1
    assert "--mode is required" in res.stderr


def test_fake_agy_rejects_output_format_not_json(tmp_path: Path) -> None:
    res = run_fake_agy(tmp_path, prompt="hello", output_format="stream-json")
    assert res.returncode == 1
    assert 'unrecognized --output-format value "stream-json"' in res.stderr


def test_fake_agy_rejects_output_format_missing(tmp_path: Path) -> None:
    res = subprocess.run(
        [
            str(FAKE_AGY),
            "--mode",
            "accept-edits",
            "--print-timeout",
            "60s",
            "--log-file",
            str((tmp_path / "cli.log").resolve()),
            "--model",
            "gemini-3.8-flash-medium",
            "-p",
            "hello",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert res.returncode == 1
    assert "--output-format is required" in res.stderr


def test_fake_agy_rejects_print_timeout_missing(tmp_path: Path) -> None:
    res = subprocess.run(
        [
            str(FAKE_AGY),
            "--mode",
            "accept-edits",
            "--output-format",
            "json",
            "--log-file",
            str((tmp_path / "cli.log").resolve()),
            "--model",
            "gemini-3.8-flash-medium",
            "-p",
            "hello",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert res.returncode == 1
    assert "--print-timeout is required" in res.stderr


def test_fake_agy_rejects_print_timeout_no_unit(tmp_path: Path) -> None:
    res = run_fake_agy(tmp_path, prompt="hello", print_timeout="120")
    assert res.returncode == 1
    assert "must match ^[0-9]+[smh]$" in res.stderr


def test_fake_agy_rejects_print_timeout_invalid_format(tmp_path: Path) -> None:
    res = run_fake_agy(tmp_path, prompt="hello", print_timeout="abc")
    assert res.returncode == 1
    assert "must match ^[0-9]+[smh]$" in res.stderr


def test_fake_agy_rejects_log_file_missing(tmp_path: Path) -> None:
    res = subprocess.run(
        [
            str(FAKE_AGY),
            "--mode",
            "accept-edits",
            "--output-format",
            "json",
            "--print-timeout",
            "60s",
            "--model",
            "gemini-3.8-flash-medium",
            "-p",
            "hello",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert res.returncode == 1
    assert "--log-file is required" in res.stderr


def test_fake_agy_rejects_log_file_relative_path(tmp_path: Path) -> None:
    res = subprocess.run(
        [
            str(FAKE_AGY),
            "--mode",
            "accept-edits",
            "--output-format",
            "json",
            "--print-timeout",
            "60s",
            "--log-file",
            "cli.log",
            "--model",
            "gemini-3.8-flash-medium",
            "-p",
            "hello",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert res.returncode == 1
    assert "--log-file must be an absolute path" in res.stderr


def test_fake_agy_rejects_model_missing(tmp_path: Path) -> None:
    res = subprocess.run(
        [
            str(FAKE_AGY),
            "--mode",
            "accept-edits",
            "--output-format",
            "json",
            "--print-timeout",
            "60s",
            "--log-file",
            str((tmp_path / "cli.log").resolve()),
            "-p",
            "hello",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert res.returncode == 1
    assert "--model is required" in res.stderr


def test_fake_agy_rejects_prompt_missing(tmp_path: Path) -> None:
    res = subprocess.run(
        [
            str(FAKE_AGY),
            "--mode",
            "accept-edits",
            "--output-format",
            "json",
            "--print-timeout",
            "60s",
            "--log-file",
            str((tmp_path / "cli.log").resolve()),
            "--model",
            "gemini-3.8-flash-medium",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert res.returncode == 1
    assert "-p <prompt> is required" in res.stderr


def test_fake_agy_rejects_prompt_empty_string(tmp_path: Path) -> None:
    res = subprocess.run(
        [
            str(FAKE_AGY),
            "--mode",
            "accept-edits",
            "--output-format",
            "json",
            "--print-timeout",
            "60s",
            "--log-file",
            str((tmp_path / "cli.log").resolve()),
            "--model",
            "gemini-3.8-flash-medium",
            "-p",
            "",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert res.returncode == 1
    assert "prompt cannot be empty string" in res.stderr


def test_fake_agy_rejects_prompt_not_last(tmp_path: Path) -> None:
    res = subprocess.run(
        [
            str(FAKE_AGY),
            "--mode",
            "accept-edits",
            "--output-format",
            "json",
            "--print-timeout",
            "60s",
            "--log-file",
            str((tmp_path / "cli.log").resolve()),
            "--model",
            "gemini-3.8-flash-medium",
            "-p",
            "hello",
            "trailing_arg",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert res.returncode == 1
    assert "unexpected argument after prompt" in res.stderr


def test_fake_agy_rejects_flags_after_prompt(tmp_path: Path) -> None:
    res = subprocess.run(
        [
            str(FAKE_AGY),
            "--output-format",
            "json",
            "--print-timeout",
            "60s",
            "--log-file",
            str((tmp_path / "cli.log").resolve()),
            "--model",
            "gemini-3.8-flash-medium",
            "-p",
            "hello",
            "--mode",
            "accept-edits",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert res.returncode == 1
    assert "unexpected argument after prompt" in res.stderr


# ---------------------------------------------------------------------------
# Scenario Execution Tests
# ---------------------------------------------------------------------------


def test_fake_agy_scenario_success_simple(tmp_path: Path) -> None:
    log_file = tmp_path / "cli.log"
    res = run_fake_agy(
        tmp_path,
        prompt="Run task __SCENARIO:success_simple__",
        log_file=log_file,
    )
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert data["status"] == "SUCCESS"
    assert "usage" in data
    assert log_file.is_file()
    assert "Starting fake agy session" in log_file.read_text()


def test_fake_agy_scenario_success_with_commits(tmp_path: Path) -> None:
    res = run_fake_agy(tmp_path, prompt="Build feature __SCENARIO:success_with_commits__")
    assert res.returncode == 0
    assert (tmp_path / "src_feature.py").is_file()
    assert (tmp_path / ".agy" / "COMMIT_MSG").is_file()


def test_fake_agy_scenario_needs_decision(tmp_path: Path) -> None:
    res = run_fake_agy(tmp_path, prompt="Task with __SCENARIO:needs_decision__")
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert "NEEDS_DECISION:" in data["response"]


def test_fake_agy_scenario_denial_with_progress(tmp_path: Path) -> None:
    res = run_fake_agy(tmp_path, prompt="Task __SCENARIO:denial_with_progress__")
    assert res.returncode == 0
    assert (tmp_path / "partial.txt").is_file()
    assert "jetski: no output produced" in res.stderr
    data = json.loads(res.stdout)
    assert len(data["denied_actions"]) > 0


def test_fake_agy_scenario_denial_blocked(tmp_path: Path) -> None:
    res = run_fake_agy(tmp_path, prompt="Task __SCENARIO:denial_blocked__")
    assert res.returncode == 0
    assert "jetski: no output produced" in res.stderr
    data = json.loads(res.stdout)
    assert data["response"] == ""
    assert len(data["denied_actions"]) > 0


def test_fake_agy_scenario_timeout(tmp_path: Path) -> None:
    res = run_fake_agy(tmp_path, prompt="Task __SCENARIO:timeout__")
    assert res.returncode == 0
    assert "[agy] print timeout after 120s" in res.stderr


def test_fake_agy_scenario_crash_rc1(tmp_path: Path) -> None:
    res = run_fake_agy(tmp_path, prompt="Task __SCENARIO:crash_rc1__")
    assert res.returncode == 1
    assert "Fatal error" in res.stderr


def test_fake_agy_scenario_crash_signal(tmp_path: Path) -> None:
    res = run_fake_agy(tmp_path, prompt="Task __SCENARIO:crash_signal__")
    assert res.returncode == -signal.SIGABRT


def test_fake_agy_scenario_resume_echo(tmp_path: Path) -> None:
    res = run_fake_agy(
        tmp_path,
        prompt="Resume prompt __SCENARIO:resume_echo__",
        extra_args=["--conversation", "11111111-1111-4111-8111-111111111111"],
    )
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert data["conversation_id"] == "11111111-1111-4111-8111-111111111111"
    assert "11111111-1111-4111-8111-111111111111" in data["response"]


def test_fake_agy_scenario_env_probe(tmp_path: Path) -> None:
    env = {"TMPDIR": str(tmp_path), "PATH": "/usr/bin:/bin", "CUSTOM_VAR": "secret_token"}
    res = run_fake_agy(
        tmp_path,
        prompt="Env probe __SCENARIO:env_probe__",
        env=env,
    )
    assert res.returncode == 0
    dump_file = tmp_path / "env_dump.json"
    assert dump_file.is_file()
    dumped = json.loads(dump_file.read_text())
    assert dumped["CUSTOM_VAR"] == "secret_token"


def test_fake_agy_scenario_stdin_probe(tmp_path: Path) -> None:
    res = run_fake_agy(
        tmp_path,
        prompt="Stdin probe __SCENARIO:stdin_probe__",
        stdin=subprocess.DEVNULL,
    )
    assert res.returncode == 0
    assert (tmp_path / "stdin_probe.txt").is_file()
    assert "has_input=False" in (tmp_path / "stdin_probe.txt").read_text()


def test_fake_agy_scenario_file_trigger(tmp_path: Path) -> None:
    (tmp_path / ".fake_agy_scenario").write_text("needs_decision")
    res = run_fake_agy(tmp_path, prompt="Generic prompt with no marker")
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert "NEEDS_DECISION:" in data["response"]


# ---------------------------------------------------------------------------
# Review finding F1: cli.log mode and per-generation UUID knobs.
# ---------------------------------------------------------------------------


def _fake_env(**overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(overrides)
    return env


def test_fake_agy_can_append_or_truncate_the_cli_log(tmp_path: Path) -> None:
    log = tmp_path / "cli.log"
    log.write_text("PREVIOUS GENERATION SENTINEL\n", encoding="utf-8")

    append = run_fake_agy(
        tmp_path,
        "Simple __SCENARIO:success_simple__",
        env=_fake_env(FAKE_AGY_CLI_LOG_MODE="append"),
        log_file=log,
    )
    assert append.returncode == 0
    appended = log.read_text(encoding="utf-8")
    assert "PREVIOUS GENERATION SENTINEL" in appended
    assert "Created conversation" in appended

    truncate = run_fake_agy(
        tmp_path,
        "Simple __SCENARIO:success_simple__",
        env=_fake_env(FAKE_AGY_CLI_LOG_MODE="truncate"),
        log_file=log,
    )
    assert truncate.returncode == 0
    truncated = log.read_text(encoding="utf-8")
    assert "PREVIOUS GENERATION SENTINEL" not in truncated
    assert "Created conversation" in truncated


def test_fake_agy_returns_a_distinct_uuid_for_a_later_fresh_generation(
    tmp_path: Path,
) -> None:
    log = tmp_path / "cli.log"
    (tmp_path / ".fake_agy_fresh_uuid_seed").write_text("gen1", encoding="utf-8")
    first = run_fake_agy(
        tmp_path,
        "Simple __SCENARIO:success_simple__",
        log_file=log,
    )
    (tmp_path / ".fake_agy_fresh_uuid_seed").write_text("gen2", encoding="utf-8")
    second = run_fake_agy(
        tmp_path,
        "Simple __SCENARIO:success_simple__",
        log_file=log,
    )

    first_id = json.loads(first.stdout.strip().splitlines()[-1])["conversation_id"]
    second_id = json.loads(second.stdout.strip().splitlines()[-1])["conversation_id"]
    assert first_id != second_id


def test_fake_agy_ignore_conversation_returns_a_new_uuid(tmp_path: Path) -> None:
    log = tmp_path / "cli.log"
    (tmp_path / ".fake_agy_ignore_conversation").write_text("1", encoding="utf-8")
    passed = "11111111-1111-4111-8111-111111111111"
    res = run_fake_agy(
        tmp_path,
        "A",
        extra_args=["--conversation", passed],
        log_file=log,
    )
    reported = json.loads(res.stdout.strip().splitlines()[-1])["conversation_id"]
    assert reported != passed
    assert "Created conversation" in log.read_text(encoding="utf-8")
