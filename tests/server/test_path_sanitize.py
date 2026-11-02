"""Smoke-fix item 5: strip absolute job paths from the agy response text.

Finding 9: real stdout embeds ``file:///tmp/<state>/jobs/<id>/work/...`` links
and the wrapper passed them through to the orchestrator. The response that
reaches the envelope must say ``<worktree>`` and ``~`` instead, without altering
any other text.
"""

from pathlib import Path

from agy_mcp.models import Job, build_result_response, unwrap_untrusted_text
from agy_mcp.states import JobState
from agy_mcp.util import sanitize_absolute_paths

WORKTREE = Path("/tmp/agy-state/jobs/8f3c843a/work")
HOME = Path("/home/smoke-user")


def test_file_uri_worktree_path_is_replaced() -> None:
    """A file:// URI keeps its shape and only the worktree prefix changes."""
    text = (
        "Updated [`slugify`](file:///tmp/agy-state/jobs/8f3c843a/work/slug/__init__.py"
        "#L8-L19) and [tests](file:///tmp/agy-state/jobs/8f3c843a/work/tests/test_slug.py)"
    )
    out = sanitize_absolute_paths(text, WORKTREE, HOME)
    assert out == (
        "Updated [`slugify`](file://<worktree>/slug/__init__.py"
        "#L8-L19) and [tests](file://<worktree>/tests/test_slug.py)"
    )


def test_plain_absolute_path_is_replaced() -> None:
    """A bare worktree path (with or without a trailing file) becomes <worktree>."""
    text = f"edited {WORKTREE}/slug/__init__.py and also {WORKTREE}"
    assert sanitize_absolute_paths(text, WORKTREE, HOME) == (
        "edited <worktree>/slug/__init__.py and also <worktree>"
    )


def test_home_path_is_replaced() -> None:
    """Home becomes ~ in plain text and inside file:// URIs."""
    text = f"see {HOME}/agy-smoke-repo/README.md and file://{HOME}/agy-smoke-repo/README.md"
    assert sanitize_absolute_paths(text, WORKTREE, HOME) == (
        "see ~/agy-smoke-repo/README.md and file://~/agy-smoke-repo/README.md"
    )


def test_text_without_paths_is_unchanged() -> None:
    """Text with no matching path is returned byte-for-byte."""
    text = "Summary\n\n- done\n- no paths here\n\n_even underscores and dots._\n"
    assert sanitize_absolute_paths(text, WORKTREE, HOME) == text


def test_prefix_of_a_different_directory_is_not_altered() -> None:
    """A sibling directory that merely starts with the prefix must stay intact."""
    siblings = [
        f"{WORKTREE}-backup/file.py",
        f"{WORKTREE}2/file.py",
        f"{WORKTREE}.old/file.py",
        f"{HOME}2/notes.txt",
    ]
    text = "\n".join(siblings)
    out = sanitize_absolute_paths(text, WORKTREE, HOME)
    assert out == text
    assert "<worktree>" not in out


def test_worktree_under_home_is_replaced_as_worktree() -> None:
    """The worktree prefix wins over a broader home prefix."""
    home = Path("/home/smoke-user")
    worktree = home / "state" / "jobs" / "abc" / "work"
    text = f"edit {worktree}/a.py or {home}/other.py"
    assert sanitize_absolute_paths(text, worktree, home) == (
        "edit <worktree>/a.py or ~/other.py"
    )


def test_none_inputs_are_safe() -> None:
    """Missing worktree/home never raises and never rewrites text."""
    assert sanitize_absolute_paths("plain text", None, None) == "plain text"
    assert sanitize_absolute_paths("", WORKTREE, HOME) == ""


def test_envelope_response_is_sanitized_inside_untrusted_wrapper(tmp_path: Path) -> None:
    """build_result_response sanitizes before wrapping the untrusted text."""
    job = Job(
        job_id="00000000-0000-0000-0000-000000000005",
        title="Paths",
        repo_path=tmp_path / "repo",
        branch="agy/paths-0005",
        base_commit="base_sha",
        state=JobState.DONE,
        worktree_path=WORKTREE,
    )
    raw = f"Edited {WORKTREE}/slug/__init__.py from {HOME}/agy-smoke-repo."

    resp = build_result_response(
        job=job,
        raw_response=raw,
        raw_diff_stat="",
        raw_commits=[],
        audit_events_tail=[],
        home=HOME,
    )

    assert resp.response.startswith('<untrusted_text id="')
    body = unwrap_untrusted_text(resp.response)
    assert str(WORKTREE) not in body
    assert str(HOME) not in body
    assert "<worktree>" in body
    assert "~" in body
