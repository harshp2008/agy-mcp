"""Tests for finding F26: path leaks in envelopes and error messages."""

from pathlib import Path

import pytest

from agy_mcp.audit import (
    audit,
    take_snapshot,
)
from agy_mcp.jobs import create_job, get_job
from agy_mcp.server import _format_error
from tests.helpers.make_repo import get_safe_git_env, make_repo


def test_get_job_does_not_leak_absolute_path_on_missing_job(tmp_path: Path) -> None:
    """get_job error must not leak absolute state or file paths."""
    state_dir = tmp_path / "custom_state"
    state_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(FileNotFoundError) as exc_info:
        get_job("nonexistent_job_123", base_state_dir=state_dir)

    err_msg = str(exc_info.value)
    # Must not contain absolute directory path
    assert str(state_dir) not in err_msg
    assert "/custom_state" not in err_msg
    assert err_msg == "Job file not found for job_id 'nonexistent_job_123'"


def test_format_error_sanitizes_paths_in_error_envelopes(tmp_path: Path) -> None:
    """_format_error must strip host paths and state directories from envelopes."""
    home = tmp_path / "home" / "harshuser"
    home.mkdir(parents=True, exist_ok=True)
    state_dir = home / ".gemini" / "antigravity"

    # Simulated FileNotFoundError with absolute host path
    exc_path = f"{state_dir}/jobs/job-xyz/job.json"
    exc = FileNotFoundError(f"[Errno 2] No such file or directory: '{exc_path}'")
    envelope = _format_error(exc, tool_name="agy_status", job_id="job-xyz")

    assert envelope["error"]["code"] in ("invalid_input", "not_found")
    msg = envelope["error"]["message"]
    assert str(home) not in msg
    assert str(state_dir) not in msg
    assert "/harshuser" not in msg


def test_audit_findings_do_not_leak_absolute_home_paths(tmp_path: Path) -> None:
    """Audit findings for host-sensitive paths must report ~/-relative paths."""
    home = tmp_path / "home" / "alice"
    ssh_dir = home / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    id_rsa = ssh_dir / "id_rsa"
    id_rsa.write_text("dummy key")

    repo_dir = home / "repo"
    make_repo(repo_dir, initial_commit=True, env=get_safe_git_env(home))

    state_dir = tmp_path / "state"
    job = create_job(
        repo_path=repo_dir,
        title="Host Audit Job",
        prompt="p",
        model="gemini-3.8-flash",
        max_minutes=10,
        base_state_dir=state_dir,
    )
    job.worktree_path = repo_dir

    snapshot = take_snapshot(job, home=home)

    # Mutate id_rsa
    id_rsa.write_text("tampered key")

    findings = audit(job, snapshot, home=home)
    assert findings.has_fatal
    for item in findings.items:
        # Neither path nor detail should leak absolute home directory
        assert str(home) not in item.path
        assert str(home) not in item.detail
        if "id_rsa" in item.path:
            assert item.path.startswith("~/.ssh") or item.path.startswith(".ssh")
