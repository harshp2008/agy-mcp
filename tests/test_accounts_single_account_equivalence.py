"""No-registry behavior must stay identical to the single-account server (D102)."""

from pathlib import Path

from agy_mcp.accounts.env import build_account_env
from agy_mcp.accounts.registry import build_registry
from agy_mcp.config import Config, load_config
from agy_mcp.util import env_allowlist


class TestSingleAccountEquivalence:
    """A Config with no registry must be the pre-multi-account configuration."""

    def test_default_config_has_no_declared_accounts(self) -> None:
        config = Config()
        assert config.accounts == []
        assert config.max_accounts == 5

    def test_config_without_accounts_adds_no_unknown_keys(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            'max_concurrent_jobs = 2\nmodel_allowlist = ["gemini-3.8-flash-medium"]\n',
            encoding="utf-8",
        )
        config = load_config(path=config_file)
        assert config.accounts == []
        assert config.unknown_keys == []

    def test_missing_file_returns_the_unchanged_default(self, tmp_path: Path) -> None:
        config = load_config(path=tmp_path / "does-not-exist.toml")
        assert config == Config()

    def test_existing_defaults_are_unchanged(self) -> None:
        config = Config()
        assert config.max_concurrent_jobs == 2
        assert config.max_minutes_cap == 360
        assert config.daily_jobs_cap == 20
        assert config.agy_home == "~/.gemini/antigravity-cli"
        assert config.low_quota_five_hour_pct == 15.0
        assert config.low_quota_weekly_pct == 10.0
        assert config.usage_guard == "warn"
        assert config.quota_estimate_five_hour_per_1k_tokens == 0.011
        assert config.quota_estimate_weekly_per_1k_tokens == 0.0031
        assert config.timezone is None

    def test_registry_without_accounts_is_the_implicit_default(self, tmp_path: Path) -> None:
        registry = build_registry(Config(), tmp_path)
        assert registry.configured is False
        assert len(registry.all()) == 1
        assert registry.all()[0].name == "default"
        assert not registry.accounts

    def test_default_child_env_is_byte_identical_to_env_allowlist(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        tmp = tmp_path / "tmp"
        registry = build_registry(Config(), home)
        assert build_account_env(registry.default, tmp) == env_allowlist(
            job_tmp_dir=tmp, home=home
        )
