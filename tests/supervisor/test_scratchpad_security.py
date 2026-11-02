"""Security tests for hardened reads of agent-written ``.agy/*`` files (ITEM 2/7).

The agent controls ``.agy`` and everything in it. The wrapper must never follow
a symlink out of the worktree, never read a non-regular file, never read past
its cap, and never treat binary garbage as text.
"""

import os
from pathlib import Path

from agy_mcp import scratchpad


def _worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    (wt / ".agy").mkdir(parents=True)
    return wt


def test_reads_regular_seeded_file(tmp_path: Path) -> None:
    wt = _worktree(tmp_path)
    (wt / ".agy" / "PLAN.md").write_text("- [ ] 1. do it\n", encoding="utf-8")
    result = scratchpad.read_scratchpad_file(wt, "PLAN.md", scratchpad.MAX_PLAN_BYTES)
    assert result.ok
    assert result.text == "- [ ] 1. do it\n"


def test_symlinked_file_is_refused(tmp_path: Path) -> None:
    wt = _worktree(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")
    (wt / ".agy" / "NOTES.md").symlink_to(secret)
    result = scratchpad.read_scratchpad_file(wt, "NOTES.md", scratchpad.MAX_NOTES_BYTES)
    assert not result.ok
    assert result.reason == "symlink"
    assert "TOP-SECRET" not in (result.text or "")


def test_symlinked_agy_directory_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "PLAN.md").write_text("- [x] 1. secret\n", encoding="utf-8")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".agy").symlink_to(outside, target_is_directory=True)

    result = scratchpad.read_scratchpad_file(wt, "PLAN.md", scratchpad.MAX_PLAN_BYTES)
    assert not result.ok
    assert result.reason == "symlink"


def test_oversized_file_is_refused(tmp_path: Path) -> None:
    wt = _worktree(tmp_path)
    (wt / ".agy" / "PLAN.md").write_bytes(b"- [ ] 1. " + b"x" * (scratchpad.MAX_PLAN_BYTES + 10))
    result = scratchpad.read_scratchpad_file(wt, "PLAN.md", scratchpad.MAX_PLAN_BYTES)
    assert not result.ok
    assert result.reason == "oversized"


def test_binary_garbage_is_refused(tmp_path: Path) -> None:
    wt = _worktree(tmp_path)
    (wt / ".agy" / "NOTES.md").write_bytes(b"normal text\x00\x01\x02binary")
    result = scratchpad.read_scratchpad_file(wt, "NOTES.md", scratchpad.MAX_NOTES_BYTES)
    assert not result.ok
    assert result.reason == "binary"


def test_directory_instead_of_file_is_refused(tmp_path: Path) -> None:
    wt = _worktree(tmp_path)
    (wt / ".agy" / "LOG.md").mkdir()
    result = scratchpad.read_scratchpad_file(wt, "LOG.md", scratchpad.MAX_LOG_BYTES)
    assert not result.ok
    assert result.reason == "not_regular"


def test_unknown_name_is_refused(tmp_path: Path) -> None:
    wt = _worktree(tmp_path)
    for name in ("../AGENTS.md", "secret.txt", "PLAN.md/../../x"):
        result = scratchpad.read_scratchpad_file(wt, name, scratchpad.MAX_PLAN_BYTES)
        assert not result.ok
        assert result.reason == "invalid_name"


def test_fd_confinement_guard() -> None:
    """The /proc fd check must reject a descriptor outside the .agy directory."""
    expected = Path("/tmp/expected-agy-dir")
    fd = os.open("/", os.O_RDONLY)
    try:
        assert scratchpad._fd_is_inside(fd, expected) is False
    finally:
        os.close(fd)
