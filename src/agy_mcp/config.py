"""Strict configuration schema and TOML loading (SPEC section 18).

Owns the :class:`Config` defaults, type/range/regex validation and the
fail-closed decision to reject a broken config instead of silently running on
defaults. It must never execute commands, touch the filesystem beyond reading
the selected TOML file, or invent keys that are not documented in SPEC §18.
"""

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

from agy_mcp.accounts.errors import AccountError
from agy_mcp.accounts.registry import AccountConfig, parse_accounts

# Real agy loads its shared permission config from this path under HOME. The
# literal path stays in config.py so the transcript-reading scope guard stays
# tight; worktree.inspect_permission_rules imports the constant (D63).
AGY_PERMISSION_CONFIG_REL = Path(".gemini") / "config" / "config.json"


class ConfigError(ValueError):
    """Raised when configuration contains invalid syntax, types, or values."""


@dataclass
class Config:
    """Configuration settings for agy-mcp loaded from ~/.config/agy-mcp/config.toml."""

    max_concurrent_jobs: int = 2
    max_minutes_cap: int = 360
    daily_jobs_cap: int = 20
    allowed_workdir_roots: list[str] = field(default_factory=list)
    model_allowlist: list[str] = field(
        default_factory=lambda: [
            # The real CLI accepts the unqualified alias and resolves it to the
            # high variant (masked smoke evidence); keep the explicit variants.
            "gemini-3.8-flash",
            "gemini-3.8-flash-low",
            "gemini-3.8-flash-medium",
            "gemini-3.8-flash-high",
        ]
    )
    verify_allowlist: list[str] = field(
        default_factory=lambda: [
            "pytest",
            "ruff",
            "npm test",
            "cargo test",
            "go test",
            "node --test",
        ]
    )
    trust_agents_dir: bool = False
    require_clean_source: bool = False
    checkpoint_commit_minutes: int = 10
    verify_timeout_seconds: int = 900
    auto_fix_attempts: int = 2
    auto_resume_on_denial: int = 1
    plan_nudge: int = 1
    worktree_setup_seconds: int = 300
    job_retention_days: int = 14
    max_state_disk_gb: float = 20.0
    min_free_gb: float = 2.0
    low_quota_five_hour_pct: float = 15.0
    low_quota_weekly_pct: float = 10.0
    # Token-based quota estimate coefficients (percentage points per 1k tokens).
    # Calibrated 2026-10-31 (conservative upper-envelope, 7 jobs; see
    # docs/reports/quota-calibration-data.md).
    quota_estimate_weekly_per_1k_tokens: float = 0.0031
    quota_estimate_five_hour_per_1k_tokens: float = 0.011
    usage_guard: str = "warn"
    quota_error_patterns: list[str] = field(default_factory=list)
    auth_error_patterns: list[str] = field(default_factory=list)
    claim_phrases: list[str] = field(
        default_factory=lambda: ["tests pass", "all tests passed", "verified"]
    )
    agy_home: str = "~/.gemini/antigravity-cli"
    timezone: Optional[str] = None
    # Additional accounts; the implicit "default" account always exists and is
    # not configurable, so this list is everything beyond the primary login.
    accounts: list[AccountConfig] = field(default_factory=list)
    max_accounts: int = 5
    # Multi-account scheduler thresholds (D108/D109). Heuristics, not facts.
    account_cost_tokens_per_minute: int = 35300
    account_cost_expected_minutes: int = 30
    account_cost_safety_factor: float = 1.5
    account_five_hour_floor_pct: float = 15.0
    account_full_window_margin_pct: float = 0.1
    account_burn_horizon_minutes: int = 90
    account_handoff_min_wait_minutes: int = 120
    account_usage_max_age_seconds: int = 60
    account_expires_boost_horizon_hours: int = 168
    unknown_keys: list[str] = field(default_factory=list)


def _validate_config_dict(data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validate types, ranges, and collect unknown keys from raw config dict."""
    defaults = Config()
    validated: dict[str, Any] = {}
    unknown_keys: list[str] = []

    # Validate max_accounts before the main loop: the accounts validator needs
    # it even when the TOML lists accounts before max_accounts.
    raw_max_accounts = data.get("max_accounts", defaults.max_accounts)
    if isinstance(raw_max_accounts, bool) or not isinstance(raw_max_accounts, int):
        raise ConfigError(
            f"Expected int for 'max_accounts', got {type(raw_max_accounts).__name__}: "
            f"{raw_max_accounts!r}"
        )
    if raw_max_accounts < 1 or raw_max_accounts > 32:
        raise ConfigError(f"'max_accounts' must be between 1 and 32, got {raw_max_accounts}")
    max_accounts_value = int(raw_max_accounts)

    known_keys = {
        "max_concurrent_jobs",
        "max_minutes_cap",
        "daily_jobs_cap",
        "allowed_workdir_roots",
        "model_allowlist",
        "verify_allowlist",
        "trust_agents_dir",
        "require_clean_source",
        "checkpoint_commit_minutes",
        "verify_timeout_seconds",
        "auto_fix_attempts",
        "auto_resume_on_denial",
        "plan_nudge",
        "worktree_setup_seconds",
        "job_retention_days",
        "max_state_disk_gb",
        "min_free_gb",
        "low_quota_five_hour_pct",
        "low_quota_weekly_pct",
        "quota_estimate_weekly_per_1k_tokens",
        "quota_estimate_five_hour_per_1k_tokens",
        "usage_guard",
        "quota_error_patterns",
        "auth_error_patterns",
        "claim_phrases",
        "agy_home",
        "timezone",
        "accounts",
        "max_accounts",
        "account_cost_tokens_per_minute",
        "account_cost_expected_minutes",
        "account_cost_safety_factor",
        "account_five_hour_floor_pct",
        "account_full_window_margin_pct",
        "account_burn_horizon_minutes",
        "account_handoff_min_wait_minutes",
        "account_usage_max_age_seconds",
        "account_expires_boost_horizon_hours",
    }

    for key, val in data.items():
        if key not in known_keys:
            # Unknown keys are collected, not fatal: they are reported on stderr
            # at startup so a typo is visible without bricking a working setup.
            unknown_keys.append(key)
            continue

        if key in ("trust_agents_dir", "require_clean_source"):
            if not isinstance(val, bool):
                raise ConfigError(f"Expected bool for '{key}', got {type(val).__name__}: {val!r}")
            validated[key] = val

        elif key in (
            "max_concurrent_jobs",
            "max_minutes_cap",
            "daily_jobs_cap",
            "checkpoint_commit_minutes",
            "verify_timeout_seconds",
            "auto_fix_attempts",
            "auto_resume_on_denial",
            "plan_nudge",
            "worktree_setup_seconds",
            "job_retention_days",
        ):
            # In Python, bool is a subclass of int; reject bools for int fields
            if isinstance(val, bool) or not isinstance(val, int):
                raise ConfigError(f"Expected int for '{key}', got {type(val).__name__}: {val!r}")
            if key == "auto_fix_attempts":
                if val < 0:
                    raise ConfigError(f"'{key}' must be >= 0, got {val}")
            elif key == "auto_resume_on_denial":
                if val < 0 or val > 3:
                    raise ConfigError(f"'{key}' must be between 0 and 3, got {val}")
            elif key == "plan_nudge":
                if val not in (0, 1):
                    raise ConfigError(f"'{key}' must be 0 or 1, got {val}")
            elif key == "verify_timeout_seconds":
                if val < 1 or val > 86400:
                    raise ConfigError(f"'{key}' must be between 1 and 86400, got {val}")
            else:
                if val < 1:
                    raise ConfigError(f"'{key}' must be >= 1, got {val}")
            validated[key] = val

        elif key in ("quota_error_patterns", "auth_error_patterns"):
            if not isinstance(val, list) or not all(
                isinstance(pattern, str) and pattern.strip() for pattern in val
            ):
                raise ConfigError(
                    f"Expected list of non-empty strings for '{key}', got {val!r}"
                )
            for pattern in val:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ConfigError(f"Invalid regex in '{key}': {pattern!r} ({exc})")
            validated[key] = list(val)

        elif key == "claim_phrases":
            # Literal, case-insensitive substrings, not regexes: a typo should
            # under-match (fewer warnings), never fail startup.
            if not isinstance(val, list) or not val or not all(
                isinstance(phrase, str) and phrase.strip() for phrase in val
            ):
                raise ConfigError(
                    f"Expected a non-empty list of non-empty strings for '{key}', got {val!r}"
                )
            validated[key] = list(val)

        elif key == "usage_guard":
            if not isinstance(val, str) or val.lower() not in ("off", "warn", "block"):
                raise ConfigError(
                    f"Expected one of 'off', 'warn', 'block' for '{key}', got {val!r}"
                )
            validated[key] = val.lower()

        elif key in ("low_quota_five_hour_pct", "low_quota_weekly_pct"):
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise ConfigError(
                    f"Expected number (float/int) for '{key}', got {type(val).__name__}: {val!r}"
                )
            pct_val = float(val)
            if pct_val < 0.0 or pct_val > 100.0:
                raise ConfigError(f"'{key}' must be between 0 and 100, got {pct_val}")
            validated[key] = pct_val

        elif key in (
            "quota_estimate_weekly_per_1k_tokens",
            "quota_estimate_five_hour_per_1k_tokens",
        ):
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise ConfigError(
                    f"Expected number (float/int) for '{key}', got {type(val).__name__}: {val!r}"
                )
            coefficient = float(val)
            if coefficient < 0.0:
                raise ConfigError(f"'{key}' must be >= 0, got {coefficient}")
            validated[key] = coefficient

        elif key in ("max_state_disk_gb", "min_free_gb"):
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise ConfigError(
                    f"Expected number (float/int) for '{key}', got {type(val).__name__}: {val!r}"
                )
            num_val = float(val)
            if key == "max_state_disk_gb" and num_val <= 0.0:
                raise ConfigError(f"'{key}' must be > 0.0, got {num_val}")
            if key == "min_free_gb" and num_val < 0.0:
                raise ConfigError(f"'{key}' must be >= 0.0, got {num_val}")
            validated[key] = num_val

        elif key in ("allowed_workdir_roots", "model_allowlist"):
            if not isinstance(val, list) or not all(isinstance(item, str) for item in val):
                raise ConfigError(
                    f"Expected list of strings for '{key}', got {type(val).__name__}: {val!r}"
                )
            if key == "model_allowlist" and len(val) == 0:
                raise ConfigError("'model_allowlist' must not be empty")
            validated[key] = val

        elif key == "verify_allowlist":
            if not isinstance(val, list):
                raise ConfigError(
                    f"Expected list of strings for 'verify_allowlist', "
                    f"got {type(val).__name__}: {val!r}"
                )
            import shlex

            normalized_verify_allowlist: list[str] = []
            for item in val:
                if isinstance(item, str):
                    normalized_verify_allowlist.append(item)
                elif isinstance(item, list) and all(isinstance(sub, str) for sub in item):
                    normalized_verify_allowlist.append(shlex.join(item))
                else:
                    raise ConfigError(
                        f"Expected string or list of strings in 'verify_allowlist', got {item!r}"
                    )
            validated[key] = normalized_verify_allowlist

        elif key == "max_accounts":
            validated[key] = max_accounts_value

        elif key in (
            "account_cost_tokens_per_minute",
            "account_cost_expected_minutes",
        ):
            if isinstance(val, bool) or not isinstance(val, int):
                raise ConfigError(f"Expected int for '{key}', got {type(val).__name__}: {val!r}")
            if val < 1:
                raise ConfigError(f"'{key}' must be >= 1, got {val}")
            validated[key] = val

        elif key in (
            "account_burn_horizon_minutes",
            "account_handoff_min_wait_minutes",
            "account_usage_max_age_seconds",
            "account_expires_boost_horizon_hours",
        ):
            if isinstance(val, bool) or not isinstance(val, int):
                raise ConfigError(f"Expected int for '{key}', got {type(val).__name__}: {val!r}")
            if val < 0:
                raise ConfigError(f"'{key}' must be >= 0, got {val}")
            validated[key] = val

        elif key == "account_cost_safety_factor":
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise ConfigError(
                    f"Expected number (float/int) for '{key}', got {type(val).__name__}: {val!r}"
                )
            factor = float(val)
            if factor < 1.0:
                raise ConfigError(f"'{key}' must be >= 1.0, got {factor}")
            validated[key] = factor

        elif key in (
            "account_five_hour_floor_pct",
            "account_full_window_margin_pct",
        ):
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise ConfigError(
                    f"Expected number (float/int) for '{key}', got {type(val).__name__}: {val!r}"
                )
            pct_val = float(val)
            if pct_val < 0.0 or pct_val > 100.0:
                raise ConfigError(f"'{key}' must be between 0 and 100, got {pct_val}")
            validated[key] = pct_val

        elif key == "accounts":
            try:
                validated[key] = parse_accounts(val, max_accounts_value)
            except AccountError as exc:
                # AccountError messages are already value-safe (masked email).
                raise ConfigError(f"Invalid [[accounts]] configuration: {exc.message}") from exc

        elif key == "agy_home":
            if not isinstance(val, str) or not val.strip():
                raise ConfigError(f"Expected non-empty string for '{key}', got {val!r}")
            validated[key] = val.strip()

        elif key == "timezone":
            if val is not None:
                if not isinstance(val, str) or not val.strip():
                    raise ConfigError(f"Expected string or None for '{key}', got {val!r}")
                val_str = val.strip()
                if val_str.lower() not in ("local", "system"):
                    try:
                        from zoneinfo import ZoneInfo

                        ZoneInfo(val_str)
                    except Exception as e:
                        raise ConfigError(f"Invalid timezone '{val_str}': {e}")
                validated[key] = val_str
            else:
                validated[key] = None

    # Merge with defaults last so every Config field is populated even when the
    # file only sets one key; unknown keys are returned separately.
    result: dict[str, Any] = {}
    for k in known_keys:
        result[k] = validated.get(k, getattr(defaults, k))

    return result, unknown_keys


def load_config(
    path: Optional[Path] = None,
    base_config_dir: Optional[Path] = None,
) -> Config:
    """Load configuration from TOML file or return defaults.

    Args:
        path: Direct path to config.toml. If None, resolves from base_config_dir or ~/.config.
        base_config_dir: Optional base directory to search for config.toml or agy-mcp/config.toml.

    Returns:
        Config instance with loaded and validated values, plus any unknown keys recorded.

    Raises:
        ConfigError: If the TOML is invalid or validation fails.
    """
    if path is None:
        if base_config_dir is not None:
            direct_path = base_config_dir / "config.toml"
            nested_path = base_config_dir / "agy-mcp" / "config.toml"
            path = direct_path if direct_path.exists() else nested_path
        else:
            xdg_config = os.environ.get("XDG_CONFIG_HOME")
            base = Path(xdg_config) if xdg_config else Path.home() / ".config"
            path = base / "agy-mcp" / "config.toml"

    if not path.exists():
        # A missing file is a first run, not an error (D57 item 3); only an
        # existing-but-unreadable/invalid file fails closed below.
        return Config()

    try:
        with open(path, "rb") as f:
            raw_data = tomllib.load(f)
    except Exception as e:
        raise ConfigError(f"Failed to read or parse TOML config from {path}: {e}") from e

    if not isinstance(raw_data, dict):
        raise ConfigError(f"Config root must be a TOML table/dict, got {type(raw_data).__name__}")

    validated_fields, unknown_keys = _validate_config_dict(raw_data)
    return Config(**validated_fields, unknown_keys=unknown_keys)
