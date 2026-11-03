# Configuration keys

Status: current
Audience: operators, contributors

Configuration is a TOML file at `~/.config/agy-mcp/config.toml`, or at
`$XDG_CONFIG_HOME/agy-mcp/config.toml` when `XDG_CONFIG_HOME` is set. The
`agy-mcp --config PATH` flag and the installer's `AGY_MCP_CONFIG_PATH` override
the path. A missing file is a first run and is not an error (D57); an existing
but unreadable or invalid file fails closed with `ConfigError` and server exit
code 1 (D56). Unknown keys are **not** fatal: they are collected and printed on
stderr as a warning, so a typo is visible without bricking a working setup.

The first table is generated from the `Config` dataclass; the second is the
hand-written validator and effect, taken from `_validate_config_dict` and the
call sites. `unknown_keys` is a load-time report field, not a key, so it is not
in the table. The generated table must be refreshed when a field is added or
removed; `scripts/check_docs.py` fails until it is.

## Keys, types and defaults

<!-- BEGIN GENERATED: config-keys -->
| Key | Type | Default |
|---|---|---|
| `max_concurrent_jobs` | `int` | `2` |
| `max_minutes_cap` | `int` | `360` |
| `daily_jobs_cap` | `int` | `20` |
| `allowed_workdir_roots` | `list[str]` | `[]` |
| `model_allowlist` | `list[str]` | `["gemini-3.8-flash", "gemini-3.8-flash-low", "gemini-3.8-flash-medium", "gemini-3.8-flash-high"]` |
| `verify_allowlist` | `list[str]` | `["pytest", "ruff", "npm test", "cargo test", "go test", "node --test"]` |
| `trust_agents_dir` | `bool` | `false` |
| `require_clean_source` | `bool` | `false` |
| `checkpoint_commit_minutes` | `int` | `10` |
| `verify_timeout_seconds` | `int` | `900` |
| `auto_fix_attempts` | `int` | `2` |
| `auto_resume_on_denial` | `int` | `1` |
| `plan_nudge` | `int` | `1` |
| `worktree_setup_seconds` | `int` | `300` |
| `job_retention_days` | `int` | `14` |
| `max_state_disk_gb` | `float` | `20.0` |
| `min_free_gb` | `float` | `2.0` |
| `low_quota_five_hour_pct` | `float` | `15.0` |
| `low_quota_weekly_pct` | `float` | `10.0` |
| `quota_estimate_weekly_per_1k_tokens` | `float` | `0.0031` |
| `quota_estimate_five_hour_per_1k_tokens` | `float` | `0.011` |
| `usage_guard` | `str` | `"warn"` |
| `quota_error_patterns` | `list[str]` | `[]` |
| `auth_error_patterns` | `list[str]` | `[]` |
| `claim_phrases` | `list[str]` | `["tests pass", "all tests passed", "verified"]` |
| `agy_home` | `str` | `"~/.gemini/antigravity-cli"` |
| `timezone` | `str | null` | `null` |
| `accounts` | `list[AccountConfig]` | `[]` |
| `max_accounts` | `int` | `5` |
| `account_cost_tokens_per_minute` | `int` | `35300` |
| `account_cost_expected_minutes` | `int` | `30` |
| `account_cost_safety_factor` | `float` | `1.5` |
| `account_five_hour_floor_pct` | `float` | `15.0` |
| `account_full_window_margin_pct` | `float` | `0.1` |
| `account_burn_horizon_minutes` | `int` | `90` |
| `account_handoff_min_wait_minutes` | `int` | `120` |
| `account_usage_max_age_seconds` | `int` | `60` |
| `account_expires_boost_horizon_hours` | `int` | `168` |
<!-- END GENERATED: config-keys -->

## Validator and effect

| Key | Validator (from `config.py`) | Effect |
|---|---|---|
| `max_concurrent_jobs` | `int` (not `bool`), `>= 1` | Maximum active jobs (`queued` + `running`) across repositories; start and resume check it under `concurrency.lock`. |
| `max_minutes_cap` | `int` (not `bool`), `>= 1` | Hard ceiling on a job's cumulative minutes; `agy_start` refuses `max_minutes` above it. |
| `daily_jobs_cap` | `int` (not `bool`), `>= 1` | Maximum job starts per calendar day; exceeded -> `cap_exceeded`. The date comes from `timezone`. |
| `allowed_workdir_roots` | list of `str` | Explicit repository roots. Empty means "use `$HOME`" (with `path_outside_home` outside it). |
| `model_allowlist` | list of `str`, not empty | Permitted model identifiers for `agy_start`; a model outside it -> `model_not_allowed`. |
| `verify_allowlist` | list of `str` or list of token lists; token lists are joined with `shlex.join` | Permitted verify commands; a command outside it -> `verify_not_allowed`. |
| `trust_agents_dir` | `bool` | When `false`, a repository tracking `.agents/**` is refused (`untrusted_agents_dir`); when `true`, that content is allowed. |
| `require_clean_source` | `bool` | When `true`, a dirty source repository is refused (`source_dirty`); when `false`, it is only a `source_dirty` warning on the job. |
| `checkpoint_commit_minutes` | `int` (not `bool`), `>= 1` | Interval for the watcher's periodic checkpoint commit. |
| `verify_timeout_seconds` | `int` (not `bool`), `1..86400` | Default verification timeout; `agy_start` may override it per call (D80). |
| `auto_fix_attempts` | `int` (not `bool`), `>= 0` | Maximum automatic verification-fix resumes. |
| `auto_resume_on_denial` | `int` (not `bool`), `0..3` | Maximum automatic resumptions on a shell denial with zero commits. |
| `plan_nudge` | `int` in `{0, 1}` | `1` turns a `done` job with unticked PLAN boxes into one `needs_review(plan_incomplete)` nudge; `0` only warns. |
| `claim_phrases` | non-empty list of non-empty `str` | Case-insensitive literal phrases in `.agy/REPORT.md`; a "tests passed" claim while verify did not pass adds `report_claims_unverified`. |
| `worktree_setup_seconds` | `int` (not `bool`), `>= 1` | Timeout for `git worktree add` during provisioning. |
| `job_retention_days` | `int` (not `bool`), `>= 1` | Finished jobs older than this are moved to `<state>/trash/<id>_<ts>`. |
| `max_state_disk_gb` | number (not `bool`), `> 0.0` | Refuse a start when the state directory's total usage exceeds it (`state_disk_full`). |
| `min_free_gb` | number (not `bool`), `>= 0.0` | Refuse a start when free space on the state filesystem is below it (`insufficient_disk_space`). |
| `agy_home` | non-empty `str`, stripped | Base directory for agy storage and session transcripts; the transcript reader is confined to it. |
| `usage_guard` | `str` in `off`/`warn`/`block` (lower-cased; anything else is a `ConfigError`) | Quota gate on start/resume. Unavailable usage only warns, never blocks. |
| `low_quota_five_hour_pct` | number (not `bool`), `0..100` | Low threshold for the five-hour bucket; drives the `low` flag and `low_quota`. |
| `low_quota_weekly_pct` | number (not `bool`), `0..100` | Low threshold for the weekly bucket. |
| `quota_estimate_weekly_per_1k_tokens` | number (not `bool`), `>= 0` | Weekly quota points per 1k agy JSON tokens for `quota_estimate` (D83/D85). |
| `quota_estimate_five_hour_per_1k_tokens` | number (not `bool`), `>= 0` | Five-hour quota points per 1k agy JSON tokens for `quota_estimate`. |
| `quota_error_patterns` | list of non-empty `str`, each a valid regex | Regexes matched against stderr/cli.log tail to diagnose quota exhaustion. |
| `auth_error_patterns` | list of non-empty `str`, each a valid regex | Regexes matched against stderr, cli.log tail and `/usage` output to diagnose an expired login. Must not contain `not logged in`; a healthy job logs that line before keyring auth succeeds (D63). |
| `timezone` | `null`, or a non-empty `str`; `local`/`system` are accepted, otherwise `zoneinfo.ZoneInfo(name)` must resolve | Calendar timezone for `daily_jobs_cap` and for rebuilding `daily_cap.json` (`get_daily_date`). `null`/`local`/`system` use the system zone. It does not change job timestamps: `job.json`, `audit.jsonl` and `usage.jsonl` stay UTC. |
| `accounts` | array of tables; see [the multi-account design](../design/multi-account.md) | Additional accounts beyond the implicit `default`. Each entry needs `name` and an absolute `home`; optional `pool`, `enabled`, `max_concurrent_jobs`, `expires_at` and one of `expected_email`/`expected_email_sha256`. Invalid entries fail closed (`ConfigError`). |
| `max_accounts` | `int` (not `bool`), `1..32` | Maximum **total** accounts including the implicit `default`; `[[accounts]]` may declare at most `max_accounts - 1` entries. Exceeding it is `account_limit_exceeded`. |
| `account_cost_tokens_per_minute` | `int` (not `bool`), `>= 1` | Cost-estimate rate for a job with no history (default 35300, the median of the seven calibration jobs). |
| `account_cost_expected_minutes` | `int` (not `bool`), `>= 1` | Expected job duration used as the cost horizon; `max_minutes` is a ceiling, so the horizon is `min(max_minutes, this)`. |
| `account_cost_safety_factor` | number (not `bool`), `>= 1.0` | Both windows must exceed `estimated cost * factor` before an account is strictly eligible. |
| `account_five_hour_floor_pct` | number (not `bool`), `0..100` | Minimum five-hour remaining percentage for any start. |
| `account_full_window_margin_pct` | number (not `bool`), `0..100` | A window at or within this many percentage points of 100% remaining counts as **full**: it contributes zero expiry urgency and is never the binding window for burn mode (D115). |
| `account_burn_horizon_minutes` | `int`, `>= 0` | Background jobs may burn an account when all its binding windows are usable again within this many minutes. |
| `account_handoff_min_wait_minutes` | `int`, `>= 0` | Reserved for the conversation-transfer threshold in Phase 7. |
| `account_usage_max_age_seconds` | `int`, `>= 0` | Use the in-memory usage cache when > 0; 0 forces a fresh `/usage` read. The cache TTL itself is 60 s. |
| `account_expires_boost_horizon_hours` | `int`, `>= 0` | Window in which an account's optional `expires_at` boosts its scheduling urgency. |

## Example

```toml
# ~/.config/agy-mcp/config.toml
max_concurrent_jobs = 2
daily_jobs_cap = 20
timezone = "Asia/Calcutta"
usage_guard = "warn"
model_allowlist = ["gemini-3.8-flash-medium"]
verify_allowlist = ["pytest", "ruff"]
```

## Accounts

`[[accounts]]` is described in full in
[the multi-account design](../design/multi-account.md) and the user recipe in
[accounts](../using/accounts.md). The implicit `default` account always exists
and is not declared here, so a config with no `[[accounts]]` table is exactly the
pre-multi-account configuration.

## Sources

- `src/agy_mcp/config.py` — the `Config` dataclass, `known_keys`,
  `_validate_config_dict`, `load_config` and the timezone validation.
- `src/agy_mcp/jobs.py` — `get_daily_date`, `check_and_increment_daily_cap`,
  `rebuild_daily_cap_from_audit` (the daily-cap consumers of `timezone`).
- `src/agy_mcp/manager.py` — the start/resume checks for concurrency, caps,
  disk, model, verify and quota.
- `src/agy_mcp/usage.py` — `usage_guard`, the low thresholds and the quota
  estimate coefficients.
- `src/agy_mcp/scratchpad.py` — the plan nudge and claim-phrase behaviour.
- `src/agy_mcp/util.py` — the `agy_home` transcript confinement.
- `doc/design/spec.md` — §18, the authoritative descriptions this table summarises.
- `src/agy_mcp/accounts/registry.py` — account entry validation and the implicit default.
- `doc/design/multi-account.md` — the registry fields and their rules.
- `doc/audits/2026-11-02-part2c-coverage.md` — §5, the original 27-key inventory
  and the missing `timezone` row (COV-06); multi-account adds `accounts` and
  `max_accounts`.
