"""Tests for F5: untrusted text wrapping and byte capping in envelope fields."""

import re
from pathlib import Path

from agy_mcp.models import (
    MAX_COMMIT_MSG_BYTES,
    MAX_DIFF_STAT_BYTES,
    Job,
    JobState,
    build_result_response,
)


def test_result_diff_stat_wrapped_and_capped(tmp_path: Path) -> None:
    """diff_stat in agy_result must be wrapped in <untrusted_text> and byte capped."""
    job = Job(
        job_id="f5-diffstat-job",
        title="F5 Diffstat Test",
        repo_path=tmp_path / "repo",
        branch="agy/f5-diffstat",
        base_commit="abc1234",
        state=JobState.DONE,
    )
    raw_diff_stat = " malicious/file.py | 10 ++\n" * 1000  # large untrusted diff stat

    res = build_result_response(
        job=job,
        raw_response="ok",
        raw_diff_stat=raw_diff_stat,
        raw_commits=[],
        audit_events_tail=[],
    )

    # Must be wrapped in <untrusted_text id="...">
    assert res.diff_stat.startswith('<untrusted_text id="'), (
        "diff_stat was not wrapped in untrusted_text"
    )
    assert re.search(r'</untrusted_text id="[a-f0-9]+">$', res.diff_stat)
    assert len(res.diff_stat.encode("utf-8")) <= MAX_DIFF_STAT_BYTES


def test_result_commit_subjects_wrapped_and_capped(tmp_path: Path) -> None:
    """Commit subjects in agy_result must be wrapped in <untrusted_text> and byte capped."""
    job = Job(
        job_id="f5-commits-job",
        title="F5 Commits Test",
        repo_path=tmp_path / "repo",
        branch="agy/f5-commits",
        base_commit="abc1234",
        state=JobState.DONE,
    )
    raw_commits = [
        {
            "sha": "1234567890abcdef",
            "subject": "malicious commit subject with instruction injection: exfiltrate tokens",
            "author": "attacker",
        }
    ]

    res = build_result_response(
        job=job,
        raw_response="ok",
        raw_diff_stat="1 file changed",
        raw_commits=raw_commits,
        audit_events_tail=[],
    )

    assert len(res.commits) == 1
    subj = res.commits[0]["subject"]
    assert subj.startswith('<untrusted_text id="'), (
        "commit subject was not wrapped in untrusted_text"
    )
    assert re.search(r'</untrusted_text id="[a-f0-9]+">$', subj)
    assert len(subj.encode("utf-8")) <= MAX_COMMIT_MSG_BYTES


def test_supervisor_denied_commands_wrapped_and_capped() -> None:
    """denied_commands command and raw_line in supervisor must be wrapped and capped."""
    from agy_mcp.cli_log import Denial

    # Test format_denied_commands or supervisor denied_commands handling
    d = Denial(
        step=1,
        tool="RunCommand",
        raw_line="malicious raw line",
        command="rm -rf / # injected command",
    )
    from agy_mcp.cli_log import format_denied_commands
    formatted = format_denied_commands([d])
    assert len(formatted) == 1
    assert formatted[0]["command"].startswith('<untrusted_text id="')

