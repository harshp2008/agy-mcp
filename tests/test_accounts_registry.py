"""Registry parsing, validation and the implicit default account (D102)."""

import os
import stat
from pathlib import Path

import pytest

from agy_mcp.accounts.errors import AccountError
from agy_mcp.accounts.registry import (
    AccountConfig,
    AccountRegistry,
    build_registry,
    parse_accounts,
    validate_account_home,
)
from agy_mcp.config import Config, ConfigError, load_config


def _entry(name: str, home: str | None = None, **extra: object) -> dict[str, object]:
    entry: dict[str, object] = {"name": name, "home": home or f"/tmp/agy-{name}"}
    entry.update(extra)
    return entry


def _code(exc: pytest.ExceptionInfo[AccountError]) -> str:
    return exc.value.code


def test_no_accounts_means_default_only(tmp_path: Path) -> None:
    registry = build_registry(Config(), tmp_path)
    assert registry.configured is False
    assert [a.name for a in registry.all()] == ["default"]
    assert registry.default.is_default is True
    assert registry.default.expanded_home == tmp_path


def test_one_extra_account(tmp_path: Path) -> None:
    config = Config(accounts=parse_accounts([_entry("account-b")], max_accounts=5))
    registry = build_registry(config, tmp_path)
    assert [a.name for a in registry.all()] == ["default", "account-b"]
    assert registry.configured is True
    assert registry.by_name("account-b").home == "/tmp/agy-account-b"


@pytest.mark.parametrize("count", [1, 2, 4])
def test_supported_account_counts(count: int) -> None:
    raw = [_entry(f"account-{index}") for index in range(count)]
    parsed = parse_accounts(raw, max_accounts=5)
    assert len(parsed) == count
    registry = AccountRegistry(
        default=AccountConfig(name="default", home="/home/u", is_default=True),
        accounts=tuple(parsed),
    )
    assert len(registry.all()) == count + 1


def test_five_total_accounts_is_the_limit() -> None:
    raw = [_entry(f"account-{index}") for index in range(4)]
    assert len(parse_accounts(raw, max_accounts=5)) == 4
    with pytest.raises(AccountError) as exc:
        parse_accounts(raw + [_entry("account-4")], max_accounts=5)
    assert _code(exc) == "account_limit_exceeded"


def test_max_accounts_is_configurable() -> None:
    raw = [_entry(f"account-{index}") for index in range(3)]
    with pytest.raises(AccountError) as exc:
        parse_accounts(raw, max_accounts=3)
    assert _code(exc) == "account_limit_exceeded"


def test_duplicate_name() -> None:
    with pytest.raises(AccountError) as exc:
        parse_accounts([_entry("a"), _entry("a")], max_accounts=5)
    assert _code(exc) == "account_name_duplicate"


def test_default_name_is_reserved() -> None:
    with pytest.raises(AccountError) as exc:
        parse_accounts([_entry("default")], max_accounts=5)
    assert _code(exc) == "account_name_invalid"


@pytest.mark.parametrize("name", ["UPPER", "-leading", "has space", "x" * 33])
def test_bad_name(name: str) -> None:
    with pytest.raises(AccountError) as exc:
        parse_accounts([_entry(name)], max_accounts=5)
    assert _code(exc) == "account_name_invalid"


def test_empty_name_is_rejected() -> None:
    with pytest.raises(AccountError) as exc:
        parse_accounts([_entry("")], max_accounts=5)
    assert _code(exc) in {"account_name_invalid", "account_config_invalid"}


def test_relative_home_is_rejected() -> None:
    with pytest.raises(AccountError) as exc:
        parse_accounts([_entry("a", "relative/home")], max_accounts=5)
    assert _code(exc) == "account_home_invalid"


def test_tilde_home_is_absolute_after_expansion() -> None:
    parsed = parse_accounts([_entry("a", "~/agy-account-a")], max_accounts=5)
    assert parsed[0].expanded_home.is_absolute()


def test_missing_home_key() -> None:
    with pytest.raises(AccountError) as exc:
        parse_accounts([{"name": "a"}], max_accounts=5)
    assert _code(exc) == "account_config_invalid"


def test_unknown_entry_key() -> None:
    with pytest.raises(AccountError) as exc:
        parse_accounts([_entry("a", surprise=True)], max_accounts=5)
    assert _code(exc) == "account_config_invalid"


def test_non_table_entry() -> None:
    with pytest.raises(AccountError) as exc:
        parse_accounts(["not-a-table"], max_accounts=5)
    assert _code(exc) == "account_config_invalid"


def test_accounts_must_be_a_list() -> None:
    with pytest.raises(AccountError) as exc:
        parse_accounts({"name": "a"}, max_accounts=5)
    assert _code(exc) == "account_config_invalid"


def test_pool_labels_make_one_bucket() -> None:
    parsed = parse_accounts(
        [_entry("a", pool="family"), _entry("b", pool="family"), _entry("c")],
        max_accounts=5,
    )
    assert parsed[0].pool_key == parsed[1].pool_key == "family"
    assert parsed[2].pool_key == "c"


def test_bad_pool_label() -> None:
    with pytest.raises(AccountError) as exc:
        parse_accounts([_entry("a", pool="not a slug")], max_accounts=5)
    assert _code(exc) == "account_config_invalid"


def test_enabled_and_cap_validation() -> None:
    with pytest.raises(AccountError):
        parse_accounts([_entry("a", enabled="yes")], max_accounts=5)
    with pytest.raises(AccountError):
        parse_accounts([_entry("a", max_concurrent_jobs=0)], max_accounts=5)
    with pytest.raises(AccountError):
        parse_accounts([_entry("a", max_concurrent_jobs=True)], max_accounts=5)
    parsed = parse_accounts([_entry("a", max_concurrent_jobs=1, enabled=False)], max_accounts=5)
    assert parsed[0].max_concurrent_jobs == 1
    assert parsed[0].enabled is False


def test_expires_at_validation() -> None:
    parsed = parse_accounts([_entry("a", expires_at="2026-12-01T00:00:00Z")], max_accounts=5)
    assert parsed[0].expires_at == "2026-12-01T00:00:00Z"
    with pytest.raises(AccountError):
        parse_accounts([_entry("a", expires_at="not-a-date")], max_accounts=5)


def test_expected_email_forms() -> None:
    with pytest.raises(AccountError) as exc:
        parse_accounts(
            [
                _entry(
                    "a",
                    expected_email="a@example.com",
                    expected_email_sha256="0" * 64,
                )
            ],
            max_accounts=5,
        )
    assert _code(exc) == "account_config_invalid"
    with pytest.raises(AccountError):
        parse_accounts([_entry("a", expected_email_sha256="ABC")], max_accounts=5)
    with pytest.raises(AccountError):
        parse_accounts([_entry("a", expected_email="not-an-email")], max_accounts=5)
    parsed = parse_accounts([_entry("a", expected_email_sha256="a" * 64)], max_accounts=5)
    assert parsed[0].expected_email_sha256 == "a" * 64


def test_validate_home_requires_private_existing_directory(tmp_path: Path) -> None:
    home = tmp_path / "account-a"
    home.mkdir(mode=0o700)
    account = AccountConfig(name="a", home=str(home))
    validate_account_home(account)

    os.chmod(home, 0o755)
    with pytest.raises(AccountError) as exc:
        validate_account_home(account)
    assert _code(exc) == "account_home_permissions"
    os.chmod(home, 0o700)

    missing = AccountConfig(name="b", home=str(tmp_path / "missing"))
    with pytest.raises(AccountError) as exc:
        validate_account_home(missing)
    assert _code(exc) == "account_home_invalid"


def test_validate_home_skips_default(tmp_path: Path) -> None:
    default = AccountConfig(name="default", home=str(tmp_path / "nope"), is_default=True)
    validate_account_home(default)


def test_home_mode_is_checked_with_st_mode(tmp_path: Path) -> None:
    home = tmp_path / "account-a"
    home.mkdir(mode=0o700)
    assert stat.S_IMODE(home.stat().st_mode) == 0o700


def test_config_loads_accounts(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
max_accounts = 5
[[accounts]]
name = "account-b"
home = "/tmp/agy-account-b"
pool = "family"
enabled = true
expected_email_sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
""",
        encoding="utf-8",
    )
    config = load_config(path=config_file)
    assert config.max_accounts == 5
    assert len(config.accounts) == 1
    assert config.accounts[0].name == "account-b"
    assert config.unknown_keys == []


def test_config_rejects_too_many_accounts(tmp_path: Path) -> None:
    entries = "\n".join(
        f'[[accounts]]\nname = "a{i}"\nhome = "/tmp/agy-a{i}"\n' for i in range(5)
    )
    config_file = tmp_path / "config.toml"
    config_file.write_text("max_accounts = 5\n" + entries, encoding="utf-8")
    with pytest.raises(ConfigError, match="Invalid"):
        load_config(path=config_file)


def test_config_rejects_bad_account(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        '[[accounts]]\nname = "a"\nhome = "relative"\n', encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="Invalid"):
        load_config(path=config_file)


def test_bad_max_accounts(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text("max_accounts = 0\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="max_accounts"):
        load_config(path=config_file)
