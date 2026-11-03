# Code map: every single-account assumption and the change it needs

Status: current
Audience: maintainers, contributors

Read of the production code that resolves a child `HOME`, reads or writes
`~/.gemini`, stores quota state, decides exhaustion, enforces caps and audits
host paths. Each row names the exact line, whether it assumes one account, and
the minimum change multi-account support needs. The prior recon report's nine
assumptions were checked against the code; corrections are at the end.

## Legend

- **Assumption Y** — the line hard-codes one account/home/state and must change.
- **Assumption N** — the line is account-agnostic; it only needs the right input.
- "Required change" is the Phase 3–8 change, not a proposed rewrite.

## Child environment and binary

| File:line | Role | Single-account assumption | Required change |
|---|---|---|---|
| `src/agy_mcp/util.py:54-99` | `env_allowlist` builds the child env from scratch: `HOME`, `PATH`, `LANG`, `TMPDIR`, git overrides | Y — no `DBUS_SESSION_BUS_ADDRESS`; one `home` | Add an optional isolation env (non-default `DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null`, account `HOME`); never inherit the parent bus |
| `src/agy_mcp/runner.py:97-160` | `build_argv` resolves the binary and builds agy argv | N — takes the binary as an argument | Pass the account's binary if one is ever per-account; for now one binary, many HOMEs |
| `src/agy_mcp/runner.py:206-279` | `LocalProcessRunner.launch` spawns with the supplied env | N — env is injected | No change; the manager supplies the per-account env |
| `src/agy_mcp/server.py:477-494` | `resolve_agy_binary` resolves one `agy` at startup | Y-ish — one server-wide binary | Keep: accounts differ by `HOME`, not by binary. A per-account binary is out of scope (open question) |
| `src/agy_mcp/manager.py:456` | Fresh launch builds `env_allowlist(home=self.home)` | Y — server `HOME` | Build the env from the selected account's home and isolation fields |
| `src/agy_mcp/manager.py:1498` | Resume builds the same env | Y — server `HOME` | Resume on the job's pinned account with that account's env |
| `src/agy_mcp/manager.py:126-128` | `JobManager.home` is one resolved home | Y — one home for audit, preflight, env | Keep as the *default* home; account calls pass the account home explicitly |

## `~/.gemini` reads and writes

| File:line | Role | Single-account assumption | Required change |
|---|---|---|---|
| `src/agy_mcp/config.py:24` | `AGY_PERMISSION_CONFIG_REL = .gemini/config/config.json` | Y — relative to one HOME | Resolve under each account's home; the literal stays so transcript scope stays tight |
| `src/agy_mcp/worktree.py:596-660` | `inspect_permission_rules` reads candidate config files under a `gemini_dir` | N — already takes a directory | Pass the account's home; add a strict "policy presence/parity" check, not only "dangerous rules" |
| `src/agy_mcp/transcript.py:88-130` | `read_denied_calls` opens `<agy_home>/brain/<cid>/.../transcript.jsonl` | Y — one configured `agy_home` | Use the job's account `agy_home`; the confinement check is unchanged |
| `src/agy_mcp/supervisor.py:131-180` | Supervisor stores `home`, `agy_home`, `agy_binary` | Y — one of each per job | Construct with the job's account values |
| `src/agy_mcp/supervisor.py:1325-1347` | Denial detail reads the transcript through `agy_home` | Y — configured `agy_home` | Use the account's `agy_home` |
| `src/agy_mcp/config.py:83` | `agy_home = ~/.gemini/antigravity-cli` | Y — one default | Keep as the default account; derive per account (`<home>/.gemini/antigravity-cli`) |

## State paths, caps and concurrency

| File:line | Role | Single-account assumption | Required change |
|---|---|---|---|
| `src/agy_mcp/jobs.py:28-34` | `get_base_state_dir` → `$XDG_STATE_HOME/agy-mcp` | N — one shared root | Keep one root; add per-account fields/jobs. Do not fork the whole store |
| `src/agy_mcp/jobs.py:401-402` | Daily cap files `daily_cap.lock`/`daily_cap.json` in the state root | Y — one counter for all jobs | Add per-account counters (key the file name by account), default account unchanged |
| `src/agy_mcp/jobs.py:267-281` | `count_active_jobs` counts queued/running across the store | Y — global count only | Keep global limit; add per-account counts |
| `src/agy_mcp/manager.py:352-369` | `max_concurrent_jobs` and `daily_jobs_cap` checked under one flock | Y — global limits only | Check global and per-account caps; reserve the slot and the account atomically |
| `src/agy_mcp/manager.py:1515-1530` | Resume re-checks the global concurrency limit | Y — global only | Also check the pinned account's cap |
| `src/agy_mcp/jobs.py:436-439` | Daily-cap rebuild scans the whole store | Y — one counter | Rebuild per account from the same audit logs |

## Usage, cache and quota

| File:line | Role | Single-account assumption | Required change |
|---|---|---|---|
| `src/agy_mcp/usage.py:51-64` | `mask_email` (first two + `***`) | N | Move/replace with `accounts.identity.mask_email(head=3, tail=2)`; keep the old helper as a thin alias if tests pin it |
| `src/agy_mcp/usage.py:665-753` | `read_usage_once` runs `agy ... -p /usage` with `env_allowlist(home=home)` and no D-Bus override | Y — cannot isolate a non-default account | Accept `env_overrides`/an isolation env; pass the account env |
| `src/agy_mcp/usage.py:759-806` | `get_usage` cache key is the resolved binary path | Y — account A's reading is returned for B | Include the account identity (`home` or registry name) in the key |
| `src/agy_mcp/usage.py:813-870` | `record_usage` appends to `usage.jsonl` with no account field | Y — rows cannot be attributed | Add an additive `account` (registry name) and `masked_email`; never the full email |
| `src/agy_mcp/usage.py:559-581` (supervisor) | `_detect_interruption_reason` decides `quota_exhausted` from the model's group | N — model-driven | Keep; feed it the pinned account's reading |
| `src/agy_mcp/supervisor.py:596-615` | `_final_usage_cost` counts all active jobs for concurrency | Y — global concurrency used in the cost model | Count the account's active jobs (and keep the global figure if needed) |
| `src/agy_mcp/manager.py:785-840` | `_apply_usage_guard` reads the server-wide usage | Y — one account's quota | Default-account behavior unchanged; per-account guard for a pinned account |

## Quota exhaustion and resume

| File:line | Role | Single-account assumption | Required change |
|---|---|---|---|
| `src/agy_mcp/states.py:60-180` | Transition table; `needs_review` with reason `quota_exhausted` | N — state machine is account-agnostic | Add no new state; store `resume_after` and the account on the job |
| `src/agy_mcp/supervisor.py:559-581` | Exhaustion uses `exhausted_bucket_reset` (max reset of zero buckets) | N — reading-driven | Pass the pinned account's reading; record which window binds |
| `src/agy_mcp/models.py:162,284,473-477` | `resume_after` on the envelope/job; `review_reason=quota_exhausted` | N | Keep; add the account to the envelope/status |
| `src/agy_mcp/manager.py:1379-1560` | `resume` uses the stored conversation id and one env | Y — one env, one account | Resume on the pinned account; transfer/handoff when the pause is long |
| `src/agy_mcp/manager.py:631-670` | Auto-fix and plan-nudge resume paths | Y — they call `resume` with the server env | They inherit pinning from `resume`; verify they never switch accounts |
| `src/agy_mcp/manager.py:1379-1400` | `follow_up` path | Y — same | Same account; transfer rules apply |

## Worktree preflight and audit

| File:line | Role | Single-account assumption | Required change |
|---|---|---|---|
| `src/agy_mcp/worktree.py:34-99` | `validate_repo_path`: repo must be inside `HOME` unless `allowed_workdir_roots` is set | Y — compares against the server home | Compare against a configured allowed root (the real user's project root), not the account home; or pass an explicit `allowed_roots` |
| `src/agy_mcp/worktree.py:85-88` | `path_outside_home` error | Y | New code/error for the account case; keep the old code for the default account |
| `src/agy_mcp/audit.py:180-213` | Host snapshot hashes `~/.ssh`, dotfiles and `~/.config/agy-mcp/config.toml` under one home | Y — server home | Keep auditing the invoking user's home; do not snapshot the account home (it is not the user's data root) |
| `src/agy_mcp/audit.py:227-360` | Host-path comparison | N | No change; pass the same real home |
| `src/agy_mcp/manager.py:293-314` | Dangerous-rule inspection on the server home | Y — one policy | Inspect each account home; fail/report when the account policy is missing or weaker |
| `src/agy_mcp/server.py:486-504` | Server bootstrap resolves one binary and one state dir | N | Accounts are runtime data, not a second server |

## Installer and doctor

| File:line | Role | Single-account assumption | Required change |
|---|---|---|---|
| `installer/install.sh:125-132` | One `$HOME/.config/agy-mcp/config.toml`, one state dir | Y — one instance | Keep; accounts live in the same config file. The installer never creates a login |
| `installer/install.sh:722-797` | `doctor_agymcp` checks run.sh, venv, agy, config, client blocks | Y — no account checks | Add read-only registry/home/mode/policy/email/isolation checks |

## Corrections to the prior recon

| Recon claim | What the code shows | Verdict |
|---|---|---|
| `config.py:24` assumes permissions strictly under `$HOME` | True; `AGY_PERMISSION_CONFIG_REL` is relative and `inspect_permission_rules(gemini_dir)` already accepts a directory | Correct; the change is to pass the account home and add a policy-presence check |
| `config.py:83` `agy_home` must be per-account | True; the supervisor and transcript reader take one resolved path | Correct |
| `util.py:70-81` does not set/scrub D-Bus | True; `env_allowlist` builds only HOME/PATH/LANG/TMPDIR/git | Correct; line is 54-99, not 70-81 |
| `jobs.py:401-402` one daily cap | True; one `daily_cap.json` in the state root | Correct |
| `usage.py:569,777` cache key ignores the account | True; the key is the resolved binary at 777 | Correct; 569 is the class, 777 is the key |
| `usage.py:821` `usage.jsonl` has no account tag | True; `record_usage` starts at 813 and writes the dict with no account | Correct |
| `worktree.py:64,87` HOME preflight | True; `resolve_home` is 59 and the failure is 85-88 | Correct |
| `server.py:486-504` one binary | True; `resolve_agy_binary` is 477 and bootstrap 499-504 | Correct, but not a blocker: accounts differ by `HOME` |
| Installer single-instance | True; one config path, one state dir, doctor has no account checks | Correct |

The recon missed four things this read adds:

1. **Model availability is per account.** `gemini-3.8-flash-medium` was rejected
   on account-b (`error: invalid model selection`), while `gemini-3.8-flash`
   worked (evidence: `evidence/conversation-transfer.txt`). A per-account model
   policy check is needed.
2. **A missing conversation id is silent.** `--conversation <missing>` warns on
   stderr, starts a *new* conversation with a new id and exits 0. Exit 0 does not
   prove the context continued.
3. **`read_usage_once` can hang on an unauthenticated account.** A fresh HOME
   triggers interactive OAuth and waits; only the 30 s timeout stops it. The
   scheduler must not probe unauthenticated accounts blindly.
4. **Cost accounting counts global concurrency** (`supervisor.py:596-615`), so
   per-account scheduling must decide which concurrency figure the cost model
   means.

## Sources

- `src/agy_mcp/util.py` — the child environment builder.
- `src/agy_mcp/runner.py` — argv construction and process launch.
- `src/agy_mcp/config.py` — the permission-config relative path and config defaults.
- `src/agy_mcp/worktree.py` — HOME containment and permission inspection.
- `src/agy_mcp/transcript.py` — the `<agy_home>/brain` read.
- `src/agy_mcp/jobs.py` — state root, active-job count and daily cap.
- `src/agy_mcp/manager.py` — start/resume, caps and usage guard.
- `src/agy_mcp/usage.py` — `/usage` parsing, cache key and `usage.jsonl`.
- `src/agy_mcp/supervisor.py` — exhaustion detection and final cost.
- `src/agy_mcp/models.py` — job fields and `resume_after`.
- `src/agy_mcp/audit.py` — host-path snapshot.
- `src/agy_mcp/server.py` — binary resolution and bootstrap.
- `installer/install.sh` — config/state layout and `doctor_agymcp`.
