"""Line-span config editing for the account CLI (D117)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agy_mcp.accounts import config_edit
from agy_mcp.accounts.errors import AccountError


def test_remove_hand_written_block_keeps_every_other_byte() -> None:
    text = (
        "# top comment\n"
        "max_concurrent_jobs = 2\n"
        "\n"
        "[[accounts]]\n"
        'name = "a"\n'
        'home = "/tmp/a"\n'
        "\n"
        "[[accounts]]\n"
        'name = "b"\n'
        'home = "/tmp/b"\n'
        'pool = "shared"  # inline comment\n'
        "\n"
        "[default_account]\n"
        "enabled = true  # keep this\n"
    )
    new_text, span = config_edit.remove_account_block(text, "b")
    assert span.marked is False
    assert new_text == (
        "# top comment\n"
        "max_concurrent_jobs = 2\n"
        "\n"
        "[[accounts]]\n"
        'name = "a"\n'
        'home = "/tmp/a"\n'
        "\n"
        "[default_account]\n"
        "enabled = true  # keep this\n"
    )


def test_marker_block_round_trip_and_remove() -> None:
    block = config_edit.render_account_block(
        "c",
        "~/.local/share/agy-accounts/c",
        pool="p",
        max_concurrent_jobs=2,
        expected_email_sha256="a" * 64,
    )
    text = config_edit.append_account_block("# keep\nmax_accounts = 5\n", block)
    span = config_edit.find_account_block(text, "c")
    assert span is not None and span.marked is True
    assert span.text(text) == block
    new_text, _ = config_edit.remove_account_block(text, "c")
    assert new_text == "# keep\nmax_accounts = 5\n\n"


def test_append_to_file_without_trailing_newline() -> None:
    block = config_edit.render_account_block("c", "/tmp/c")
    text = config_edit.append_account_block("max_accounts = 5", block)
    assert text.startswith("max_accounts = 5\n\n# agy-mcp:account c begin\n")
    config_edit.validate_config_text(text)


def test_marker_around_a_different_name_is_ambiguous() -> None:
    text = (
        "# agy-mcp:account b begin\n"
        "[[accounts]]\n"
        'name = "a"\n'
        'home = "/tmp/a"\n'
        "# agy-mcp:account b end\n"
    )
    with pytest.raises(AccountError) as excinfo:
        config_edit.find_account_block(text, "b")
    assert excinfo.value.code == "account_cli_block_ambiguous"


def test_begin_marker_without_end_is_corrupt() -> None:
    text = "# agy-mcp:account b begin\n[[accounts]]\n" 'name = "b"\n' 'home = "/tmp/b"\n'
    with pytest.raises(AccountError) as excinfo:
        config_edit.find_account_block(text, "b")
    assert excinfo.value.code == "account_cli_block_corrupt"


def test_two_hand_written_blocks_with_the_same_name_are_ambiguous() -> None:
    text = (
        '[[accounts]]\nname = "b"\nhome = "/tmp/b1"\n\n'
        '[[accounts]]\nname = "b"\nhome = "/tmp/b2"\n'
    )
    with pytest.raises(AccountError) as excinfo:
        config_edit.find_account_block(text, "b")
    assert excinfo.value.code == "account_cli_block_ambiguous"


def test_set_enabled_preserves_the_trailing_comment_and_newline() -> None:
    text = (
        '[[accounts]]\nname = "a"\nhome = "/tmp/a"\n'
        "enabled = true  # keep this\n"
        'pool = "p"\n'
    )
    new_text = config_edit.set_account_enabled(text, "a", False)
    assert new_text == text.replace("enabled = true  # keep this", "enabled = false  # keep this")
    config_edit.validate_config_text(new_text)


def test_set_enabled_inserts_the_key_when_absent() -> None:
    text = '[[accounts]]\nname = "a"\nhome = "/tmp/a"\n'
    new_text = config_edit.set_account_enabled(text, "a", False)
    assert new_text == (
        '[[accounts]]\nname = "a"\nhome = "/tmp/a"\nenabled = false\n'
    )
    config_edit.validate_config_text(new_text)


def test_default_table_is_created_and_flipped_without_losing_other_keys() -> None:
    text = (
        "max_accounts = 5\n"
        "\n"
        "[default_account]\n"
        'expected_email_sha256 = "' + "b" * 64 + '"\n'
        "enabled = true  # keep\n"
    )
    disabled = config_edit.set_default_enabled(text, False)
    assert "enabled = false  # keep" in disabled
    assert 'expected_email_sha256 = "' + "b" * 64 + '"' in disabled
    config_edit.validate_config_text(disabled)

    created = config_edit.set_default_enabled("max_accounts = 5\n", False)
    assert created.endswith("[default_account]\nenabled = false\n")
    config_edit.validate_config_text(created)


def test_write_is_atomic_with_a_timestamped_backup(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    original = "max_accounts = 5\n"
    path.write_text(original, encoding="utf-8")
    new_text = original + "\n[default_account]\nenabled = false\n"
    result = config_edit.write_config_text(
        path, new_text, expected_text=original, dry_run=False
    )
    assert path.read_text(encoding="utf-8") == new_text
    assert result.backup_path.exists()
    assert result.backup_path.read_text(encoding="utf-8") == original
    assert path.stat().st_mode & 0o777 == 0o600
    assert result.backup_path.stat().st_mode & 0o777 == 0o600


def test_dry_run_does_not_touch_the_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    original = "max_accounts = 5\n"
    path.write_text(original, encoding="utf-8")
    result = config_edit.write_config_text(
        path, original + "\n", expected_text=original, dry_run=True
    )
    assert result.dry_run is True
    assert path.read_text(encoding="utf-8") == original
    assert not result.backup_path.exists()


def test_a_concurrent_change_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("max_accounts = 5\n", encoding="utf-8")
    path.write_text("max_accounts = 6\n", encoding="utf-8")
    with pytest.raises(AccountError) as excinfo:
        config_edit.write_config_text(
            path,
            "max_accounts = 7\n",
            expected_text="max_accounts = 5\n",
            dry_run=False,
        )
    assert excinfo.value.code == "account_cli_config_changed"
    assert path.read_text(encoding="utf-8") == "max_accounts = 6\n"


def test_invalid_edited_text_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("max_accounts = 5\n", encoding="utf-8")
    with pytest.raises(AccountError) as excinfo:
        config_edit.write_config_text(
            path,
            "max_accounts = \n",
            expected_text="max_accounts = 5\n",
            dry_run=False,
        )
    assert excinfo.value.code == "account_cli_config_error"
    assert path.read_text(encoding="utf-8") == "max_accounts = 5\n"
