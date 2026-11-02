"""Unit tests for configuration loading and validation per SPEC section 18."""

from pathlib import Path

import pytest

from agy_mcp.config import ConfigError, load_config


def test_missing_config_returns_defaults(tmp_path: Path):
    """When config.toml does not exist, default Config is returned."""
    cfg = load_config(path=tmp_path / "nonexistent.toml")
    assert cfg.max_concurrent_jobs == 2
    assert cfg.max_minutes_cap == 360
    assert cfg.daily_jobs_cap == 20
    assert cfg.model_allowlist == [
        "gemini-3.8-flash",
        "gemini-3.8-flash-low",
        "gemini-3.8-flash-medium",
        "gemini-3.8-flash-high",
    ]
    assert cfg.verify_allowlist == [
        "pytest",
        "ruff",
        "npm test",
        "cargo test",
        "go test",
        "node --test",
    ]
    assert cfg.trust_agents_dir is False
    assert cfg.require_clean_source is False
    assert cfg.checkpoint_commit_minutes == 10
    assert cfg.auto_fix_attempts == 2
    assert cfg.worktree_setup_seconds == 300
    assert cfg.job_retention_days == 14
    assert cfg.max_state_disk_gb == 20.0
    assert cfg.min_free_gb == 2.0
    assert cfg.agy_home == "~/.gemini/antigravity-cli"
    assert cfg.unknown_keys == []


def test_valid_custom_config(tmp_path: Path):
    """Custom configuration values are loaded accurately."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
max_concurrent_jobs = 4
max_minutes_cap = 180
daily_jobs_cap = 50
allowed_workdir_roots = ["/srv/repos", "/home/user/code"]
model_allowlist = ["gemini-3.8-flash-high"]
verify_allowlist = ["pytest -q", "cargo test"]
trust_agents_dir = true
require_clean_source = true
checkpoint_commit_minutes = 15
auto_fix_attempts = 1
worktree_setup_seconds = 120
job_retention_days = 7
max_state_disk_gb = 50.5
min_free_gb = 10.0
agy_home = "/custom/agy/home"
"""
    )
    cfg = load_config(path=config_file)
    assert cfg.max_concurrent_jobs == 4
    assert cfg.max_minutes_cap == 180
    assert cfg.daily_jobs_cap == 50
    assert cfg.allowed_workdir_roots == ["/srv/repos", "/home/user/code"]
    assert cfg.model_allowlist == ["gemini-3.8-flash-high"]
    assert cfg.verify_allowlist == ["pytest -q", "cargo test"]
    assert cfg.trust_agents_dir is True
    assert cfg.require_clean_source is True
    assert cfg.checkpoint_commit_minutes == 15
    assert cfg.auto_fix_attempts == 1
    assert cfg.worktree_setup_seconds == 120
    assert cfg.job_retention_days == 7
    assert cfg.max_state_disk_gb == 50.5
    assert cfg.min_free_gb == 10.0
    assert cfg.agy_home == "/custom/agy/home"
    assert cfg.unknown_keys == []


def test_int_for_float_is_coerced(tmp_path: Path):
    """Integer values for float fields are accepted and converted to float."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
max_state_disk_gb = 30
min_free_gb = 5
"""
    )
    cfg = load_config(path=config_file)
    assert isinstance(cfg.max_state_disk_gb, float)
    assert cfg.max_state_disk_gb == 30.0
    assert isinstance(cfg.min_free_gb, float)
    assert cfg.min_free_gb == 5.0


def test_unknown_keys_collected(tmp_path: Path):
    """Unknown keys are tracked in unknown_keys without raising an error."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
max_concurrent_jobs = 3
future_option = "enabled"
extra_flag = 42
"""
    )
    cfg = load_config(path=config_file)
    assert cfg.max_concurrent_jobs == 3
    assert set(cfg.unknown_keys) == {"future_option", "extra_flag"}


def test_injectable_base_config_dir(tmp_path: Path):
    """load_config resolves config from base_config_dir correctly."""
    conf_dir = tmp_path / "agy-mcp"
    conf_dir.mkdir(parents=True)
    (conf_dir / "config.toml").write_text("max_concurrent_jobs = 5\n")

    cfg = load_config(base_config_dir=tmp_path)
    assert cfg.max_concurrent_jobs == 5


def test_xdg_config_home_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """load_config respects XDG_CONFIG_HOME if path is None."""
    fake_config_home = tmp_path / "my_config"
    target_dir = fake_config_home / "agy-mcp"
    target_dir.mkdir(parents=True)
    (target_dir / "config.toml").write_text("daily_jobs_cap = 99\n")

    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_config_home))
    cfg = load_config()
    assert cfg.daily_jobs_cap == 99


@pytest.mark.parametrize(
    "content,err_substr",
    [
        ("max_concurrent_jobs = 0", "must be >= 1"),
        ("max_concurrent_jobs = -2", "must be >= 1"),
        ("max_concurrent_jobs = 'two'", "Expected int"),
        ("max_concurrent_jobs = true", "Expected int"),
        ("max_minutes_cap = 0", "must be >= 1"),
        ("daily_jobs_cap = 0", "must be >= 1"),
        ("checkpoint_commit_minutes = 0", "must be >= 1"),
        ("auto_fix_attempts = -1", "must be >= 0"),
        ("worktree_setup_seconds = 0", "must be >= 1"),
        ("job_retention_days = 0", "must be >= 1"),
        ("max_state_disk_gb = 0.0", "must be > 0.0"),
        ("max_state_disk_gb = -1.0", "must be > 0.0"),
        ("min_free_gb = -0.1", "must be >= 0.0"),
        ("trust_agents_dir = 'true'", "Expected bool"),
        ("trust_agents_dir = 1", "Expected bool"),
        ("require_clean_source = 0", "Expected bool"),
        ("model_allowlist = []", "must not be empty"),
        ("model_allowlist = 'gemini-3.8-flash-low'", "Expected list of strings"),
        ("model_allowlist = [123]", "Expected list of strings"),
        ("verify_allowlist = 42", "Expected list of strings"),
        ("allowed_workdir_roots = 'not_a_list'", "Expected list of strings"),
        ("agy_home = ''", "Expected non-empty string"),
        ("agy_home = 123", "Expected non-empty string"),
    ],
)
def test_validation_errors(tmp_path: Path, content: str, err_substr: str):
    """Validation properly rejects invalid types and out-of-range values."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(content)
    with pytest.raises(ConfigError, match=err_substr):
        load_config(path=config_file)


def test_malformed_toml_syntax(tmp_path: Path):
    """Malformed TOML syntax raises ConfigError."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("invalid_toml = [[[")
    with pytest.raises(ConfigError, match="Failed to read or parse TOML config"):
        load_config(path=config_file)
