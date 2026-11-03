"""Conversation-db transfer and summary-handoff prompt (D111)."""

from pathlib import Path

import pytest

from agy_mcp.accounts.errors import AccountError
from agy_mcp.accounts.handoff import build_handoff_prompt
from agy_mcp.accounts.transfer import (
    SQLITE_HEADER,
    conversation_db_path,
    conversation_lock,
    transfer_conversation,
)

CID = "b9c7c56e-ac18-4d63-80af-caafb9328e44"


def _db(home: Path, payload: bytes = b"fake conversation\n") -> Path:
    home.mkdir(parents=True, exist_ok=True)
    home.chmod(0o700)
    path = conversation_db_path(home, CID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(SQLITE_HEADER + payload)
    path.chmod(0o600)
    return path


def _code(exc: pytest.ExceptionInfo[AccountError]) -> str:
    return exc.value.code


def test_db_path_is_uuid_validated(tmp_path: Path) -> None:
    with pytest.raises(AccountError) as exc:
        conversation_db_path(tmp_path, "../../etc/passwd")
    assert _code(exc) == "account_transfer_invalid_id"


def test_transfer_copies_and_keeps_the_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    original = _db(source)
    result = transfer_conversation(source, target, CID, tmp_path / "state")
    assert result.reused is False
    assert result.size_bytes == original.stat().st_size
    assert result.target.is_file()
    assert result.source.is_file()
    assert result.target.read_bytes() == original.read_bytes()
    assert (result.target.stat().st_mode & 0o777) == 0o600


def test_identical_target_is_reused(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    _db(source)
    first = transfer_conversation(source, target, CID, tmp_path / "state")
    second = transfer_conversation(source, target, CID, tmp_path / "state")
    assert first.reused is False
    assert second.reused is True
    assert second.target.read_bytes() == first.source.read_bytes()


def test_conflicting_target_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    _db(source, b"one\n")
    _db(target, b"two\n")
    with pytest.raises(AccountError) as exc:
        transfer_conversation(source, target, CID, tmp_path / "state")
    assert _code(exc) == "account_transfer_conflict"


def test_missing_source_is_refused(tmp_path: Path) -> None:
    (tmp_path / "target").mkdir()
    with pytest.raises(AccountError) as exc:
        transfer_conversation(tmp_path / "source", tmp_path / "target", CID, tmp_path / "state")
    assert _code(exc) == "account_transfer_source_missing"


def test_corrupt_source_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "source"
    path = _db(source)
    path.write_bytes(b"not sqlite\n")
    with pytest.raises(AccountError) as exc:
        transfer_conversation(source, tmp_path / "target", CID, tmp_path / "state")
    assert _code(exc) == "account_transfer_corrupt"


def test_non_empty_wal_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "source"
    path = _db(source)
    path.with_name(path.name + "-wal").write_bytes(b"pending")
    with pytest.raises(AccountError) as exc:
        transfer_conversation(source, tmp_path / "target", CID, tmp_path / "state")
    assert _code(exc) == "account_transfer_incomplete"


def test_conversation_lock_prevents_a_concurrent_copy(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _db(source)
    state = tmp_path / "state"
    with conversation_lock(state, CID):
        with pytest.raises(AccountError) as exc:
            transfer_conversation(source, tmp_path / "target", CID, state)
    assert _code(exc) == "account_transfer_locked"


def test_lock_dir_is_private(tmp_path: Path) -> None:
    state = tmp_path / "state"
    with conversation_lock(state, CID) as lock_path:
        assert lock_path.is_file()
        assert (lock_path.stat().st_mode & 0o777) == 0o600
        assert ((state / "accounts" / "locks").stat().st_mode & 0o777) == 0o700


def test_handoff_prompt_is_bounded_and_fenced() -> None:
    prompt = build_handoff_prompt(
        title="Fix the widget",
        original_prompt="Make the widget retry.",
        branch="agy/fix-widget-1234",
        worktree="/tmp/work",
        base_commit="abc123",
        head_commit="def456",
        plan_done=["1 of 3 plan items completed"],
        plan_remaining=["Wire the retry", "Add a test"],
        git_log="def456 fix: retry",
        diff_stat=" src/widget.py | 10 ++++++",
        last_report="Tests pass (an agent claim).",
        verify="pytest -q",
        from_account="account-a",
        to_account="account-b",
    )
    assert "Fix the widget" in prompt
    assert "agy/fix-widget-1234" in prompt
    assert "account-b" in prompt
    assert "<untrusted_text>" in prompt
    assert prompt.count("<untrusted_text>") == prompt.count("</untrusted_text>")
    assert len(prompt) <= 16000


def test_handoff_prompt_caps_a_huge_report() -> None:
    prompt = build_handoff_prompt(
        title="T",
        original_prompt="x" * 100_000,
        branch="b",
        worktree=None,
        base_commit="a",
        head_commit="",
        plan_done=[],
        plan_remaining=[],
        git_log="",
        diff_stat="",
        last_report="y" * 100_000,
        verify=None,
        from_account="a",
        to_account="b",
    )
    assert len(prompt) <= 16000
    assert "[... truncated]" in prompt

