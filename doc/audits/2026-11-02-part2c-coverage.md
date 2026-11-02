# Part 2c — Coverage matrix: "everything has to be documented"

Status: current

**Repository:** `agy-mcp` @ `main` = `a7ed98aa502b1a5ed84551aff7aa1c705156dedf`
**Mode:** read-only. **Companions:** `part2a-inventory.md`, `part2b-target-structure.md`,
`part2d-migration.md`, `part2-index.md`.

Status legend: **D** documented (a reader can find the contract at the place
named), **P** partial (named but not fully specified, or only in a historical
file), **N** not documented anywhere in prose. "Where" is the exact place.
The matrix is about **documentation coverage**, not whether the code is correct.

Method: `ast` walk of `src/agy_mcp/*.py` for public definitions; `argparse`
sources for flags; `grep` for error codes and env reads; `SPEC.md` §8/§18/§22/§23
and `docs/CODEMAP.md` as the reference set. Commands are quoted at each table.

**Path shorthand:** a bare module name (`manager.py`) means
`src/agy_mcp/manager.py`; a bare document name (`DSH_SETUP.md`) means
`docs/DSH_SETUP.md`; `install.sh` means `installer/install.sh`.

---

## 0. Summary

| Group | Items | D | P | N | Main gap |
|---|---:|---:|---:|---:|---|
| Modules (`src/agy_mcp/*.py`) | 24 | 24 | 0 | 0 | — (CODEMAP is complete) |
| Public API definitions that matter | 98 | 88 | 10 | 0 | cli-log anchor mechanism; symbols not named in SPEC |
| MCP tools | 10 | 0 | 10 | 0 | no per-tool error/example reference |
| CLI flags (agy-mcp, both cleaners, scripts) | 26 | 11 | 15 | 0 | server flags only in README; script flags only in `--help` |
| Config keys | 27 | 26 | 0 | 1 | `timezone` missing from SPEC §18 |
| Environment variables | 16 | 11 | 2 | 3 | `XDG_STATE_HOME`; 2 installer vars |
| File/state paths | 24 | 14 | 8 | 2 | `daily_cap.*`; state-root/config `XDG` halves |
| Exit codes (5 entry points) | 5 | 2 | 3 | 0 | no single table |
| Error codes | 40 | 9 | 6 | 25 | 25 codes appear in no prose doc |
| Manifest/log formats | 9 | 4 | 4 | 1 | audit event names; `daily_cap.json` |
| **Total** | **279** | **189 (68%)** | **58 (21%)** | **32 (11%)** | error codes are 78% of the N |

A second way to read the same data: **the server contract is well covered**
(modules, tool parameters, state machine, config defaults, most job files);
**the operator surface is not** (error codes, environment, exit codes,
standalone CLI pages, log/event catalogues).

---

## 1. Modules

Source: `ls src/agy_mcp/*.py | wc -l` → 24. All 24 have a `###` section in
`docs/CODEMAP.md` with Purpose / Public API / Imports / Called by / Tests /
Implements. All 24 have a module docstring (`metrics-src.json`: module docs
24/24). Status is **D** for every row; the "Where" column gives the SPEC
section in addition to CODEMAP.

| Module | Lines | What it owns | SPEC | Status |
|---|---:|---|---|---|
| `__init__.py` | 9 | package version and "never start agy at import" contract | §1 | D |
| `__main__.py` | 14 | `python -m agy_mcp` delegation | §3 | D |
| `audit.py` | 653 | snapshots, host-path classification, fatal findings | §12 | D |
| `cleanup_fixtures.py` | 758 | fixture-row lister/cleaner CLI | §22 | D |
| `cleanup_jobs.py` | 1,810 | job-record lister/cleaner CLI | §23 | D |
| `cli_log.py` | 302 | cli.log readers, denial/model/conversation extraction | §15 | D (anchor fields P) |
| `config.py` | 335 | config dataclass, validation, TOML load | §18 | D (`timezone` N) |
| `gitops.py` | 1,049 | hardened git, staging, commits, review queries | §10–§13 | D |
| `jobs.py` | 585 | persistence, state gate, caps, retention | §7, §16, §17 | D |
| `manager.py` | 1,737 | validation, lifecycle, tool assembly, recovery | §8, §9, §16 | D |
| `models.py` | 1,077 | envelopes, Job, parsing records, next actions | §8, §15 | D |
| `parse_result.py` | 346 | stdout JSON extraction, precedence | §15 | D |
| `recovery.py` | 356 | liveness and startup reconciliation | §16 | D |
| `runner.py` | 361 | argv, `/proc`, spawn, kill | §6, §10 | D |
| `scratchpad.py` | 454 | `.agy` seeding/reading, plan, report, state block | §9, §14 | D |
| `server.py` | 615 | FastMCP transport, 10 tools, CLI, bootstrap | §8 | D |
| `states.py` | 180 | JobState + 16-row transition table | §7 | P (16 of 20 rows) |
| `supervisor.py` | 1,622 | deadline, audit timer, tick, finalization, verify | §10, §13, §15 | D |
| `transcript.py` | 263 | confined denied-call reader | §15 | D |
| `usage.py` | 917 | `/usage` parse/read/cache, cost, estimate | §17 | D |
| `util.py` | 297 | env allowlist, redaction, prompts, UUID, tails | §10, §14 | D |
| `verify.py` | 808 | allowlist, flag/path policy, run, diagnosis | §13 | D |
| `watcher.py` | 859 | settle window, message validation, commits | §11 | D |
| `worktree.py` | 705 | preflight, branch, worktree, provisioning, cleanup | §9 | D |

**Modules: 24/24 documented. No action.**

---

## 2. Public functions that matter

Full `ast` scan: 234 public definitions (module-level functions + public
methods). Symbol-name check against SPEC + CODEMAP prose:

```
$ env -u PYTHONPATH .venv/bin/python - <<'EOF'
# for each public def: name in SPEC? name in CODEMAP prose (generated block removed)?
...
EOF
public defs 234
Counter({'partial': 167, 'documented': 57, 'none': 10})
```

"Named in one place" (167) is mostly CODEMAP's `Public API:` bullet, which is
one line and is enough for a maintainer. The 10 named in neither are:

`cli_log.capture_cli_log_anchor`, `cli_log.cli_log_generation_start`,
`cli_log.read_cli_log_since`, `cli_log.extract_resuming_conversation_id_from_cli_log`,
`gitops.GitError.args_list`, `models.get_plan_envelope`, `models.build_facts`,
`parse_result.extract_stdout_payload`, `usage.compute_quota_estimate`,
`util.read_file_since`.

Six of the ten have their **behaviour** documented elsewhere
(`get_plan_envelope`/`build_facts` → SPEC §8 `plan`/`facts`;
`extract_stdout_payload` → §15; `compute_quota_estimate` → §17 D83/D85;
`extract_resuming_conversation_id_from_cli_log` → D81 precedence (c);
`GitError.args_list`/`read_file_since` are internal details). The remaining
three implement the cli.log generation anchor (`cli_log_offset`,
`cli_log_prefix_sha256`), which is **not** in any prose doc (see §7, paths).

The 98 cross-module entry points I checked explicitly (all have a CODEMAP
bullet; 38 are also named in SPEC):

| Module | Key public API | SPEC also names | Status |
|---|---|---|---|
| `server.py` | `main`, `setup_server`, the 10 `agy_*` tools | 10 tools, not `main` | D |
| `manager.py` | `start`, `resume`, `cancel`, `status`, `wait`, `result`, `review`, `cleanup`, `list`, `usage`, `reconcile`, `shutdown` | all but `shutdown` | D |
| `supervisor.py` | `tick`, `run_loop`, `stop`, `terminate_group`, `select_conversation_id` | `tick` | D |
| `watcher.py` | `tick`, `validate_commit_message`, `repair_overlong_commit_subject`, `run_checkpoint_commit`, `run_safety_net_commit` | `tick` | D |
| `gitops.py` | `stage_changes`, `commit_with_retry`, `checkpoint_commit`, `safety_net_commit`, `sanitize_commit_subject`, `get_merge_base`, `get_ahead_behind`, `get_diff_stat`, `get_commits_list`, `take_git_config_snapshot` | 2 | D |
| `audit.py` | `audit`, `take_snapshot`, `check_host_path_snapshot`, `classify_host_path`, `run_security_audit` | 1 | D |
| `verify.py` | `is_verify_allowed`, `run_verify`, `format_fix_prompt`, `diagnose_verify_output` | 0 | D |
| `usage.py` | `get_usage`, `parse_usage_payload`, `compute_usage_cost`, `compute_quota_estimate`, `mask_email` | 1 | D/P |
| `worktree.py` | `preflight`, `create_worktree`, `provision_metadata_dir`, `cleanup_worktree`, `count_unmerged_commits`, `inspect_permission_rules` | 1 | D |
| `jobs.py` | `transition_job_state`, `create_job`, `save_job`, `get_job`, `list_jobs`, `check_and_increment_daily_cap`, `prune_retained_jobs` | 0 | D |
| `models.py` | `build_standard_envelope`, `build_result_response`, `build_review_response`, `derive_next_actions`, `wrap_untrusted_text` | 1 | D |
| `recovery.py` | `reconcile_on_startup`, `check_process_liveness` | 0 | D |
| `cleanup_jobs.py` | `run_cleanup`, `main` | both | D |
| `cleanup_fixtures.py` | `run_cleanup`, `main` | both | D |
| `util.py` | `load_preamble`, `format_resume_prompt`, `env_allowlist`, `sanitize_absolute_paths`, `strict_uuid` | 0 | D |
| `parse_result.py` | `parse_agy_output` | 0 | D |
| `cli_log.py` | `extract_denials_from_cli_log`, `extract_model_resolved` | 0 | D |
| `scratchpad.py` | `seed_scratchpad`, `parse_plan_text`, `read_scratchpad_file` | 0 | D |
| `transcript.py` | `read_denied_calls` | 0 | D |
| `runner.py` | `LocalProcessRunner.launch/poll/kill`, `build_argv` | 3 | D |

**Public API: 10 partial symbol gaps, 0 unknown behaviour.** The only real
documentation gap is the cli.log generation anchor.

---

## 3. MCP tools

Source: `server.py:193-470`; snapshot `tests/fixtures/tools_schema.json`
(10 entries, pinned by `tests/test_server_unit.py:91` and
`tests/test_server_e2e.py:70`). SPEC §8 documents parameters and returns for
all ten.

| Tool | Params documented | Return documented | Error codes documented | Example | Status |
|---|---|---|---|---|---|
| `agy_start` | D (§8.1) | D | P (only `low_quota`, `cap_exceeded`, `verify_not_allowed` in prose) | N | P |
| `agy_status` | D (§8.2) | D (additive fields) | P | N | P |
| `agy_wait` | D (§8.3) | D | P (`job_finalizing`) | N | P |
| `agy_result` | D (§8.4) | D | P | N | P |
| `agy_cancel` | D (§8.5) | D | N | N | P |
| `agy_list` | D (§8.6) | D | N | N | P |
| `agy_resume` | D (§8.7) | D | P (`invalid_state_for_resume`) | N | P |
| `agy_review` | D (§8.8) | D | P (`audit_unavailable`) | N | P |
| `agy_cleanup` | D (§8.9) | D | P (`unmerged_commits`, `worktree_dirty`) | N | P |
| `agy_usage` | D (§8.10) | D | P (`unavailable` reasons) | N | P |

**MCP tools: 10/10 parameters and returns; 0/10 have a complete error table or
an example.** This is the single highest-value reference page to generate from
`tools_schema.json` + the `make_error_envelope` call sites.

---

## 4. CLI flags

### 4.1 `agy-mcp` (`server.py:474-501`, `parse_args`)

| Flag | Default | Documented at | Status |
|---|---|---|---|
| `--agy-binary PATH` | `agy` on PATH | `README.md:90`, `DSH_SETUP.md:56` | P (not in SPEC, no reference page) |
| `--state-dir PATH` | `$XDG_STATE_HOME/agy-mcp` else `~/.local/state/agy-mcp` | `README.md:91`, `DSH_SETUP.md:57` | P (XDG half missing) |
| `--config PATH` | `$XDG_CONFIG_HOME/agy-mcp/config.toml` else `~/.config/agy-mcp/config.toml` | `README.md:92`, `DSH_SETUP.md:58` | P |
| `--version` | — | `README.md:93`, `DSH_SETUP.md:47` | P |

### 4.2 `agy_mcp.cleanup_jobs` (`cleanup_jobs.py:1405-1450`)

| Flag | Default | Documented at | Status |
|---|---|---|---|
| `--store PATH` (required) | — | SPEC §23 | D |
| `--list` | off | SPEC §23 (including the `--yes`-ignored rule) | D |
| `--project PATH` | — | SPEC §23 | D |
| `--job ID` (repeatable) | — | SPEC §23 | D |
| `--older-than-days FLOAT` | `7.0` | SPEC §23 | D |
| `--yes` | off (dry run) | SPEC §23 | D |
| `--json` | off | SPEC §23 | D |

### 4.3 `agy_mcp.cleanup_fixtures` (`cleanup_fixtures.py:450-477`)

| Flag | Default | Documented at | Status |
|---|---|---|---|
| `--store PATH` (required) | — | SPEC §22 | D |
| `--yes` | off (dry run) | SPEC §22 | D |
| `--min-age-days FLOAT` | `1.0` | SPEC §22 | D |
| `--json` | off | SPEC §22 | D |

### 4.4 `scripts/` (9 scripts)

| Script | Flags | Documented at | Status |
|---|---|---|---|
| `check_no_behavior_change.py` | `--save FILE`, `--compare FILE` | module docstring + `--help` | P |
| `gen_codemap_index.py` | `--write`, `--check`, `--stdout` | module docstring + `--help` | P |
| `real_smoke.py` | `--job-name`, `--title`, `--prompt`, `--verify`, `--checklist` (repeat), `--prior-weekly-spend` | `--help` only | P |
| `smoke_real.py` | none | docstring | N |
| `smoke_fixes_real.py` | none | docstring | N |
| `run_f1.py`, `run_f2.py` | none | docstring | N |
| `run_mutations.py` | none | docstring | N |
| `watch_orphans.py` | none | docstring | N |

**CLI flags: 26 tracked, 11 D, 15 P, 0 N.**

---

## 5. Configuration keys

Source: `config.py:35-85` (dataclass), `:94-122` (`known_keys`), `:124-289`
(validation). SPEC §18 table has 26 of the 27 keys; `timezone` is the gap.

| Key | Type | Default | Documented at | Status |
|---|---|---|---|---|
| `max_concurrent_jobs` | int | 2 | SPEC §18 | D |
| `max_minutes_cap` | int | 360 | SPEC §18 | D |
| `daily_jobs_cap` | int | 20 | SPEC §18 | D |
| `allowed_workdir_roots` | list[str] | [] | SPEC §18 | D |
| `model_allowlist` | list[str] | 4 gemini aliases | SPEC §18 | D |
| `verify_allowlist` | list[str\|list] | 6 commands | SPEC §18 | D |
| `trust_agents_dir` | bool | false | SPEC §18 | D |
| `require_clean_source` | bool | false | SPEC §18 | D |
| `checkpoint_commit_minutes` | int | 10 | SPEC §18 | D |
| `verify_timeout_seconds` | int | 900 | SPEC §18 | D |
| `auto_fix_attempts` | int | 2 | SPEC §18 | D |
| `auto_resume_on_denial` | int | 1 | SPEC §18 | D |
| `plan_nudge` | int | 1 | SPEC §18 | D |
| `claim_phrases` | list[str] | 3 phrases | SPEC §18 | D |
| `worktree_setup_seconds` | int | 300 | SPEC §18 | D |
| `job_retention_days` | int | 14 | SPEC §18 | D |
| `max_state_disk_gb` | float | 20.0 | SPEC §18 | D |
| `min_free_gb` | float | 2.0 | SPEC §18 | D |
| `agy_home` | str | `~/.gemini/antigravity-cli` | SPEC §18 | D |
| `usage_guard` | str | `warn` | SPEC §18 | D |
| `low_quota_five_hour_pct` | float | 15.0 | SPEC §18 | D |
| `low_quota_weekly_pct` | float | 10.0 | SPEC §18 | D |
| `quota_estimate_weekly_per_1k_tokens` | float | 0.0031 | SPEC §18 | D |
| `quota_estimate_five_hour_per_1k_tokens` | float | 0.011 | SPEC §18 | D |
| `quota_error_patterns` | list[str] | [] | SPEC §18 | D |
| `auth_error_patterns` | list[str] | [] | SPEC §18 | D |
| `timezone` | str\|null | null (local) | **nowhere in the table**; mentioned in §17 prose | **N** |

Note: `unknown_keys` is not a config key (it is a load-time report field).
Ranges (e.g. `auto_resume_on_denial` 0-3, `verify_timeout_seconds` 1-86400) are
in the table only in prose, not as a separate column — that is why the group is
"D", not "complete reference".

**Config: 26 D, 1 N.**

---

## 6. Environment variables

Production code reads only four environment variables directly:

```
$ grep -rn 'os.environ\|getenv' src/agy_mcp/*.py
cleanup_fixtures.py:692:        home_env = os.environ.get("HOME")
cleanup_jobs.py:1641:        home_env = os.environ.get("HOME")
config.py:316:            xdg_config = os.environ.get("XDG_CONFIG_HOME")
jobs.py:32:    xdg_state = os.environ.get("XDG_STATE_HOME")
util.py:71:    resolved_path = path or os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
```

| Variable | Read by | Effect | Documented at | Status |
|---|---|---|---|---|
| `HOME` | `util.env_allowlist`, cleaners, host-path audit | child env, `$HOME` containment, snapshot paths | SPEC §10 (env), §9 (preflight) | D |
| `PATH` | `util.env_allowlist` | child PATH (fallback `/usr/local/bin:/usr/bin:/bin`) | SPEC §10 | D |
| `XDG_CONFIG_HOME` | `config.load_config` | config dir when `--config` absent | SPEC §20 (installer only) | P |
| `XDG_STATE_HOME` | `jobs.get_base_state_dir` | state root when `--state-dir` absent | **nowhere** | **N** |
| `AGY_MCP_CLIENT` | installer | `dsh`/`generic` | help + README | D |
| `AGY_MCP_DRY_RUN` | installer | print-only mode | help + README | D |
| `AGY_MCP_SRC` | installer | source tree to install | help + README | D |
| `AGY_MCP_EDITABLE` | installer | force editable | help + README | D |
| `AGY_MCP_UPGRADE` | installer | force reinstall | help + README | D |
| `AGY_MCP_PYTHON` | installer | interpreter | help | D |
| `AGY_MCP_PIP_ARGS` | installer | extra pip args | help | D |
| `AGY_MCP_NO_RESTART` | installer | skip dsh restart | help + README | D |
| `AGY_MCP_AGY_BINARY` | installer | agy path | help | D |
| `AGY_MCP_CONFIG_PATH` | installer | config path override | **nowhere** | **N** |
| `AGY_MCP_STATE_DIR` | installer/run.sh, tests | state dir override | DECISIONS/test docs only | P |
| `AGY_MCP_ALLOW_INSIDE_DSH` | installer | bypass the inside-dsh refusal | **nowhere** | **N** |

Test-only (not operator surface): `FAKE_AGY_SCENARIO`, `FAKE_AGY_RECORD`,
`FAKE_AGY_CLI_LOG_MODE`, `FAKE_AGY_IGNORE_CONVERSATION`,
`FAKE_AGY_FRESH_UUID_SEED`, `FAKE_AGY_HOME`, `AGY_HOME` — all documented in
`tests/fake_agy/README.md` (D).

Wrapper-set child variables (`LANG`, `TMPDIR`, `GIT_CONFIG_COUNT`,
`GIT_CONFIG_KEY_0..2`, `GIT_CONFIG_VALUE_0..2`, `PYTHONDONTWRITEBYTECODE`,
`PYTEST_ADDOPTS`) are documented in SPEC §10 and §13 (D).

Installer input variables: **12 read, 9 in `--help`, 6 in README** (INV-23).

**Env: 11 D, 2 P, 3 N.**

---

## 7. File and state paths

Source: `SPEC.md` §10/§11/§13/§17/§22/§23 and `grep` of
`jobs.py`/`worktree.py`/`manager.py`/`watcher.py`/`verify.py`.

| Path | Contents | Documented at | Status |
|---|---|---|---|
| `<state>/` (`$XDG_STATE_HOME/agy-mcp` or `~/.local/state/agy-mcp`) | all state | README §91, SPEC §10 (no XDG) | P |
| `~/.config/agy-mcp/config.toml` (or `$XDG_CONFIG_HOME`) | config | SPEC §18 (no XDG) | P |
| `<state>/jobs/<id>/` | one job | SPEC §10 | D |
| `.../job.json` | 55-field record | SPEC §16 (41 of 55 fields) | P |
| `.../audit.jsonl` | append-only events | SPEC §17 (4 examples) | P |
| `.../prompt.txt` | submitted prompt | SPEC §10 | D |
| `.../stdout.txt`, `stderr.txt` | process streams | SPEC §10 | D |
| `.../cli.log` | agy internal log | SPEC §10/§16 | P (format undocumented) |
| `.../work/` | git worktree | SPEC §9/§10 | D |
| `.../scratchpad/` | retained PLAN/NOTES/LOG/REPORT | SPEC §9 (D62) | D |
| `.../tmp/` | child TMPDIR | SPEC §10 | D |
| `.../snapshot.json` | audit baseline | SPEC §12 | D |
| `.../verify_tail.txt` | verify diagnosis | SPEC §13 | D |
| `.../rejected_COMMIT_MSG.txt` | rejected message evidence | SPEC §11 (D82) | D |
| `<state>/usage.jsonl` | completion metrics | SPEC §17 | D |
| `<state>/server.log` | server log | CODEMAP only | P |
| `<state>/concurrency.lock` | job-count flock | SPEC §10 | P |
| `<state>/daily_cap.json` | per-day counter | **nowhere** | **N** |
| `<state>/daily_cap.lock` | daily-cap flock | **nowhere** | **N** |
| `<state>/trash/<id>_<ts>/` | retained deleted jobs | SPEC §17 | P |
| `<state>/jobs/.cleanup-manifest-<ts>.json` | cleaner manifest | SPEC §22/§23 | D |
| `<jobdir>/work/.agy/{PLAN,NOTES,LOG,REPORT}.md` | agent scratchpad | SPEC §9 | D |
| `<jobdir>/work/.agy/COMMIT_MSG[.seen.N,.invalid.N,.done.N]` | commit protocol | SPEC §11 | D |

`server.log` format is not described; `daily_cap.json` has no documented
schema (`{"date": "...", "count": n}`, written by `jobs.py:369-411`).

**Paths: 14 D, 8 P, 2 N.**

---

## 8. Exit codes

| Entry point | Codes | Documented at | Status |
|---|---|---|---|
| `agy-mcp` | 0 normal; 1 config error; 2 bad/missing binary | 1: SPEC §18; 2: README:90, DSH_SETUP:56; 0: implicit | P |
| `python -m agy_mcp.cleanup_jobs` | 0 success; 1 partial deletion; 2 refusal | SPEC §23 | D |
| `python -m agy_mcp.cleanup_fixtures` | 0 success; 1 internal/partial; 2 refusal | SPEC §22 | D |
| `installer/install.sh` | 0 success; 1 any `die` | not stated in `--help` | P |
| `scripts/*` | ad hoc 0/1/2 | `--help`/docstring only | P |

**Exit codes: 2 D, 3 P.**

---

## 9. Error codes (40)

Source: `grep -rhoE 'code="[a-z_]+"' src/agy_mcp/*.py` plus the base codes in
exception classes. "Where" searched `SPEC.md`, `README.md`, every `docs/*.md`
and `docs/orchestrator/*.md`.

| # | Code | Source | Where | Status |
|---|---|---|---|---|
| 1 | `audit_violation` | `manager.py` | SPEC, docs | D |
| 2 | `cannot_resume_security_review` | `manager.py` | — | **N** |
| 3 | `cap_exceeded` | `manager.py` | SPEC, docs | D |
| 4 | `checklist_item_too_long` | `manager.py` | — | **N** |
| 5 | `checklist_too_long` | `manager.py` | — | **N** |
| 6 | `cleanup_failed` | `manager.py` | — | **N** |
| 7 | `concurrency_limit_exceeded` | `manager.py` | — | **N** |
| 8 | `forbidden_flag` | `runner.py` | docs only | P |
| 9 | `home_prohibited` | `worktree.py` | — | **N** |
| 10 | `insufficient_disk_space` | `manager.py`, `worktree.py` | — | **N** |
| 11 | `invalid_argv` | `runner.py` | — | **N** |
| 12 | `invalid_checklist` | `manager.py` | — | **N** |
| 13 | `invalid_git_dir` | `worktree.py` | — | **N** |
| 14 | `invalid_job_id` | `manager.py` | docs only | P |
| 15 | `invalid_repo_state` | `worktree.py` | SPEC | D |
| 16 | `invalid_state_for_resume` | `manager.py` | SPEC, docs | D |
| 17 | `invalid_verify_timeout` | `manager.py` | — | **N** |
| 18 | `job_finalizing` | `manager.py` | SPEC, docs | D |
| 19 | `job_is_running` | `manager.py` | — | **N** |
| 20 | `linked_worktree_prohibited` | `worktree.py` | — | **N** |
| 21 | `low_quota` | `manager.py` | SPEC, docs | D |
| 22 | `manager_error` | `manager.py` | docs only | P |
| 23 | `max_minutes_over_cap` | `manager.py` | — | **N** |
| 24 | `missing_prompt` | `manager.py` | — | **N** |
| 25 | `model_not_allowed` | `manager.py` | — | **N** |
| 26 | `not_a_directory` | `worktree.py` | — | **N** |
| 27 | `not_a_git_repo` | `worktree.py` | — | **N** |
| 28 | `path_outside_allowed_roots` | `worktree.py` | — | **N** |
| 29 | `path_outside_home` | `worktree.py` | — | **N** |
| 30 | `prompt_too_large` | `runner.py` | docs only | P |
| 31 | `repo_not_found` | `worktree.py` | — | **N** |
| 32 | `runner_error` | `runner.py` | — | **N** |
| 33 | `source_dirty` | `worktree.py` | SPEC, docs | D |
| 34 | `state_dir_prohibited` | `worktree.py` | — | **N** |
| 35 | `state_disk_full` | `manager.py` | — | **N** |
| 36 | `unborn_head` | `worktree.py` | docs only | P |
| 37 | `unmerged_commits` | `manager.py` | docs only | P |
| 38 | `untrusted_agents_dir` | `worktree.py` | — | **N** |
| 39 | `verify_not_allowed` | `manager.py` | SPEC, docs | D |
| 40 | `worktree_dirty` | `manager.py` | SPEC, docs | D |

Counts: **9 D, 6 P, 25 N.** The 25 N codes cover every preflight refusal
(path outside home/allowed roots, not a git repo, no HEAD, linked worktree,
dirty source), most input validation (`missing_prompt`, `model_not_allowed`,
`invalid_checklist`, `checklist_too_long`), and the cleanup/resume guards
(`cannot_resume_security_review`, `job_is_running`, `cleanup_failed`). These
are exactly the errors an orchestrator must handle, which makes this the most
costly gap in the matrix.

---

## 10. Manifest and log formats

| Format | Fields | Documented at | Status |
|---|---|---|---|
| `job.json` | 55 fields | SPEC §16 names 41; 7 in no prose doc (`cli_log_offset`, `cli_log_prefix_sha256`, `usage_at_launch`, `plan_nudge_count`, `audit_status`, `audit_reason`, `scratchpad_files`) | P |
| `audit.jsonl` | `{ts,event,detail}`; ≥25 distinct event names | SPEC §17 shows `job_created`, `commit`, `audit_violation`, `cleanup_error` only | P |
| `usage.jsonl` | 10 fields | SPEC §17 lists all 10 | D |
| `daily_cap.json` | `{date,count}` | **nowhere** | **N** |
| Cleanup manifest (planned/completed) | mode, project, planned_ids, removed_ids, failed_ids | SPEC §22/§23 (no standalone schema page) | D |
| `cli.log` | agy's own log; wrapper consumes 5 line shapes | shapes inline in `cli_log.py`, not a doc | P |
| `server.log` | Python logging format | CODEMAP only | P |
| `verify_tail.txt` | command, timeout, result, diagnosis, ~40 lines | SPEC §13 | D |
| `tools_schema.json` | 10 tools, JSON Schema | test-pinned, not described in prose | D |

**Formats: 4 D, 4 P, 1 N.**

---

## 11. The gap list (what to write first)

Ranked by damage per hour:

1. **`doc/reference/errors.md`** — 40 codes, 25 missing. Generate the table from
   `code="..."` plus the `make_error_envelope` call sites, then hand-write the
   next action for each. This is the page an orchestrator needs when a call
   fails.
2. **`doc/reference/mcp-tools.md`** — per-tool errors and one example each.
   Generate parameters from `tests/fixtures/tools_schema.json`; add the error
   list by reading each manager method's `ManagerError` raises.
3. **`doc/reference/environment.md`** — four production variables (two missing)
   + twelve installer variables (three missing) + the wrapper-set child env.
4. **`doc/reference/exit-codes.md`** — five entry points on one page.
5. **`doc/reference/config-keys.md`** — add `timezone`; add a range/validator
   column from `_validate_config_dict`.
6. **`doc/reference/state-layout.md`** — 7 `job.json` fields, `daily_cap.*`,
   `server.log`, the cli.log anchor fields.
7. **`doc/reference/log-formats.md`** — audit event catalogue (≥25 names) and
   `usage.jsonl` rows.
8. **`doc/reference/cli-*.md`** — three CLI pages; the cleaners are already
   well specified in SPEC, but they live inside a 1,036-line contract.
9. **`doc/project/test-matrix.md`** — regenerate; the current file is 23
   references out of date (INV-16).
10. **`doc/developing/scripts.md`** — nine scripts, three with flags.

---

## 12. Findings

| ID | Sev | Finding | Evidence | Proposed action | Risk | Effort |
|---|---|---|---|---|---|---|
| COV-01 | H | 25 of 40 error codes appear in no prose document; 31 of 40 are absent from SPEC | §9 table; `grep` command in the header | Write `doc/reference/errors.md`; generate the code list and hand-write next actions | Wrong next-action text is worse than none; review each | M |
| COV-02 | M | `job.json` has 55 fields; 14 are absent from SPEC and 7 from every prose doc | §7; AST field scan | `doc/reference/state-layout.md` with a generated field table | Some fields are internal; mark them "internal, may change" | M |
| COV-03 | M | The two cleaner CLIs have no standalone page; their contract is 25 KB inside SPEC §22–§23 | §4.2–4.3; §22/§23 char counts | `doc/reference/cli-cleanup-jobs.md`, `cli-cleanup-fixtures.md` | Low; content already exists | M |
| COV-04 | M | Environment variables are split: 4 production (2 un/under-documented), 12 installer (3 undocumented), 6 test-only | §6 | `doc/reference/environment.md`; one table with scope | Low | S |
| COV-05 | M | No single exit-code page; server exit 1 is only in SPEC §18, exit 2 only in README/DSH_SETUP | §8 | `doc/reference/exit-codes.md` | Low | S |
| COV-06 | M | The config reference omits `timezone` and has no range/validator column | §5; key-diff script | Add the row and a generated validator column | Low | S |
| COV-07 | M | No per-tool MCP reference: parameters/returns are in SPEC §8, but 0/10 tools have an error table or example | §3 | `doc/reference/mcp-tools.md` generated from `tools_schema.json` + manager errors | Medium: examples must be real, not invented | M |
| COV-08 | L | `audit.jsonl` has ≥25 event names; SPEC shows 4 | §10; `event="..."` scan | Event catalogue in `doc/reference/log-formats.md` | Low | S |
| COV-09 | L | Cleanup manifest and `--json` schemas have no standalone reference page | §10 | `doc/reference/manifests.md` | Low | S |
| COV-10 | L | Nine scripts have no reference page; three have flags documented only in `--help` | §4.4 | `doc/developing/scripts.md` | Low | S |

**Part 2c counts: 10 findings — 1 high, 6 medium, 3 low.**

## Sources

- `src/agy_mcp/`
- `tests/fixtures/tools_schema.json`
- `installer/install.sh`
- `doc/design/spec.md`
- `doc/project/code-layout.md`
