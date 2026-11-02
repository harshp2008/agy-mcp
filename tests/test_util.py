"""Unit tests for utility helpers: branch slug, env, byte truncation, and log redaction."""

from pathlib import Path

import pytest

from agy_mcp.runner import MAX_PROMPT_BYTES, PromptTooLargeError, build_argv
from agy_mcp.util import (
    derive_branch_slug,
    env_allowlist,
    format_fresh_prompt,
    format_resume_prompt,
    load_preamble,
    redact_log_text,
    sha256_bytes,
    sha256_file,
    truncate_head,
    truncate_tail,
)


def test_derive_branch_slug():
    """Branch slugs adhere strictly to SPEC section 9 regex and length caps."""
    assert (
        derive_branch_slug("Add JWT authentication middleware")
        == "add-jwt-authentication-middleware"
    )
    assert derive_branch_slug("Fix: issue #123 (critical)!") == "fix-issue-123-critical"
    assert derive_branch_slug("!@#$%^&*()") == "job"
    assert derive_branch_slug("") == "job"

    # Max length cap at 40 chars
    long_title = "a" * 50
    slug = derive_branch_slug(long_title)
    assert len(slug) == 40
    assert slug == "a" * 40

    # Ensure no leading or trailing dashes
    assert derive_branch_slug("---hello-world---") == "hello-world"


def test_env_allowlist_sanitization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Sanitized env passes only allowed variables and strips all parent secrets."""
    monkeypatch.setenv("FAKE_SECRET_KEY", "super-secret-token-12345")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-abcdef1234567890")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "AKIAIOSFODNN7EXAMPLE")

    job_tmp = tmp_path / "job_tmp"
    job_tmp.mkdir()

    env = env_allowlist(job_tmp_dir=job_tmp, home=tmp_path / "fake_home", path="/usr/bin:/bin")

    # Confirm secrets are absent
    assert "FAKE_SECRET_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env

    # Confirm exact allowed keys
    expected_keys = {
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
    assert set(env.keys()) == expected_keys

    # Confirm key values
    assert env["HOME"] == str(tmp_path / "fake_home")
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["LANG"] == "C.UTF-8"
    assert env["TMPDIR"] == str(job_tmp)
    assert env["GIT_CONFIG_COUNT"] == "3"
    assert env["GIT_CONFIG_KEY_0"] == "core.fsmonitor"
    assert env["GIT_CONFIG_VALUE_0"] == "false"
    assert env["GIT_CONFIG_KEY_1"] == "core.hooksPath"
    assert env["GIT_CONFIG_VALUE_1"] == "/dev/null"
    assert env["GIT_CONFIG_KEY_2"] == "core.pager"
    assert env["GIT_CONFIG_VALUE_2"] == "cat"


def test_truncate_tail_ascii():
    """Truncating tail preserves head and appends notice within byte limit."""
    text = "Hello World! This is a test string."
    # Short limit
    res = truncate_tail(text, max_bytes=25, notice="...[cut]")
    assert len(res.encode("utf-8")) <= 25
    assert res.endswith("...[cut]")
    assert res.startswith("Hello")


def test_truncate_tail_multibyte_boundary():
    """Truncating tail does not split 3-byte or 4-byte characters into broken UTF-8."""
    # 🚀 is 4 bytes: f0 9f 9a 80
    # € is 3 bytes: e2 82 ac
    # text: "abc🚀€def" -> 3 + 4 + 3 + 3 = 13 bytes
    text = "abc🚀€def"
    notice = "[cut]"  # 5 bytes
    # Limit = 10 bytes -> target = 5 bytes ("abc" + 2 bytes of rocket which must be discarded)
    res = truncate_tail(text, max_bytes=10, notice=notice)
    assert len(res.encode("utf-8")) <= 10
    assert res.endswith(notice)
    # The rocket should be cleanly omitted rather than producing invalid UTF-8
    assert res == "abc[cut]"


def test_truncate_head_multibyte_boundary():
    """Truncating head does not split multibyte characters at the start of tail."""
    # text: "abc🚀€def" -> notice + valid tail
    text = "abc🚀€def"
    notice = "[cut]"  # 5 bytes
    # Limit = 11 bytes -> target = 6 bytes from end: "€def" is 3+3=6 bytes
    res = truncate_head(text, max_bytes=11, notice=notice)
    assert len(res.encode("utf-8")) <= 11
    assert res.startswith(notice)
    assert res == "[cut]€def"


def test_redact_log_text():
    """Logs containing secrets are cleanly redacted."""
    raw = (
        "Error with AIzaSyD9Z876543210123456789012345678901 in call\n"
        "Authorization: Bearer my_secret_token_abc.123\n"
        "PAT token: ghp_123456789012345678901234567890123456\n"
        "apikey = 'super-secret-key-xyz'\n"
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----"
    )
    redacted = redact_log_text(raw)
    assert "AIzaSyD9Z876543210123456789012345678901" not in redacted
    assert "[REDACTED_API_KEY]" in redacted
    assert "my_secret_token_abc.123" not in redacted
    assert "Bearer [REDACTED]" in redacted
    assert "ghp_123456789012345678901234567890123456" not in redacted
    assert "[REDACTED_TOKEN]" in redacted
    assert "super-secret-key-xyz" not in redacted
    assert "apikey=[REDACTED]" in redacted
    assert "[REDACTED_PRIVATE_KEY]" in redacted


def test_sha256_helpers(tmp_path: Path):
    """File and byte hashing produce correct SHA-256 digests."""
    data = b"Hello, Antigravity!"
    expected = "755438ed5edcb334896e011681bf5dec6748c61e292c2168791ad8a4b81de3c3"

    assert sha256_bytes(data) == expected

    f = tmp_path / "test.bin"
    f.write_bytes(data)
    assert sha256_file(f) == expected


def test_load_preamble_via_importlib_resources():
    """Verify load_preamble reads preamble.md using importlib.resources."""
    preamble = load_preamble()
    assert preamble.startswith(
        "You are an autonomous coding assistant executing a task in this repository worktree."
    )
    assert ".agy/COMMIT_MSG" in preamble
    assert "NEEDS_DECISION: <question> | <options> | <recommendation>" in preamble


def test_format_fresh_prompt_spec_compliance():
    """Verify fresh prompt complies with SPEC section 14 criteria (a) through (f)."""
    task = "Build a REST API endpoint for user authentication\nwith JWT tokens."
    prompt = format_fresh_prompt(task)

    # (a) Starts with preamble
    preamble = load_preamble()
    assert prompt.startswith(preamble)

    # (b) Contains COMMIT_MSG instruction with exact path .agy/COMMIT_MSG
    assert ".agy/COMMIT_MSG" in prompt
    assert (
        "After completing each step, write a commit message to .agy/COMMIT_MSG"
        in prompt
    )

    # (c) Contains exact NEEDS_DECISION last-line format
    assert "NEEDS_DECISION: <question> | <options> | <recommendation>" in prompt

    # (d) Contains task text unmodified
    assert "## Task\n" + task in prompt

    # (f) Prompt size boundary enforcement
    assert len(prompt.encode("utf-8")) < MAX_PROMPT_BYTES
    huge_task = "x" * MAX_PROMPT_BYTES
    huge_prompt = format_fresh_prompt(huge_task)
    with pytest.raises(PromptTooLargeError):
        build_argv(
            agy_binary="agy",
            prompt=huge_prompt,
            model="gemini-3.8-flash-medium",
            print_timeout_seconds=60,
            log_file=Path("/tmp/cli.log"),
        )


def test_format_resume_prompt_spec_compliance():
    """Verify resume prompt wraps answer as untrusted text under header."""
    answer = "Proceed with option A: use HMAC-SHA256"
    prompt = format_resume_prompt(answer)

    # (a) Starts with preamble
    preamble = load_preamble()
    assert prompt.startswith(preamble)

    # (e) Resume turn contains answer wrapped as untrusted-data
    assert "## User Response\n" in prompt
    assert '<untrusted_text id="' in prompt
    assert "Proceed with option A: use HMAC-SHA256" in prompt
    assert "</untrusted_text id=" in prompt
