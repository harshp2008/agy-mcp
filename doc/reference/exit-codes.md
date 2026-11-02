# Exit codes per entry point

Status: current
Audience: operators, contributors

There are five entry points. The server does not exit because a job failed; a
job failure is an envelope state (`failed`, `needs_review`, `timeout`). The
cleaners and the installer use the exit status to say whether the command
itself succeeded.

| Entry point | 0 | 1 | 2 (or other) |
|---|---|---|---|
| `agy-mcp` server | normal run, `--help`, `--version`, clean SIGTERM | configuration error | missing/non-executable `agy`, argparse usage error |
| `python -m agy_mcp.cleanup_jobs` | success, including every dry run | partial deletion in `--yes` mode | refusal or bad usage |
| `python -m agy_mcp.cleanup_fixtures` | success, including every dry run | partial deletion in `--yes` mode | refusal or bad usage |
| `installer/install.sh` | success (`--help`, dry run, install, doctor/uninstall pass) | any fatal installer error | not used |
| `scripts/*` | script-specific | script-specific | script-specific; see the table |

## `agy-mcp` (server)

Source: `src/agy_mcp/server.py`.

| Code | When | Where |
|---|---|---|
| `0` | The stdio server ran and returned; also `--help` and `--version` (argparse). The SIGTERM handler stops supervisors and calls `os._exit(0)` after a clean shutdown. | `main`, `_sigterm_handler` |
| `1` | `ConfigError`: invalid TOML, unreadable config, wrong type, out-of-range value or an invalid `timezone`. Printed as `Configuration error: ...` on stderr. | `main` around `setup_server` |
| `2` | `--agy-binary` is not a file/executable, or no `agy` is found on `PATH`; or argparse rejects an unknown option/missing value. | `resolve_agy_binary`, `parse_args` |

The server must never write logs to stdout: stdout is the MCP JSON-RPC stream.
Logs go to stderr and `<state>/server.log`.

## `python -m agy_mcp.cleanup_jobs`

Source: `src/agy_mcp/cleanup/jobs_cli.py` (`main`; `src/agy_mcp/cleanup_jobs.py` is a re-export shim).

| Code | When |
|---|---|
| `0` | `--list` (always read-only); any dry run (no `--yes`); a `--yes` run that deleted every planned candidate and has no `failed_ids`. |
| `1` | A `--yes` run with any `failed_ids`; a `--job` run that deleted fewer than requested (or has failures); an unexpected internal error after the store was opened. |
| `2` | Bad usage: no mode or more than one of `--list`/`--project`/`--job`; empty `--project`; empty/whitespace `--store`; non-finite/negative `--older-than-days`; store or an ancestor is a symlink; store missing/not a directory; `jobs/` missing/unreadable; `HOME` unset or the store is outside `HOME`; malformed `--job` UUID; `--job` id not found; `--job` real mode where the requested id is not a candidate; an `OSError` opening the store. |

`--yes` is ignored in `--list` mode: `--list` returns before the deletion path,
so the store is bit-for-bit unmodified.

## `python -m agy_mcp.cleanup_fixtures`

Source: `src/agy_mcp/cleanup/fixtures_cli.py` (`main`; `src/agy_mcp/cleanup_fixtures.py` is a re-export shim).

| Code | When |
|---|---|
| `0` | Any dry run; a `--yes` run with no `failed_ids`. |
| `1` | A `--yes` run with any `failed_ids`; an unexpected internal error after the store was opened. |
| `2` | Bad usage: non-finite/negative `--min-age-days`; empty/whitespace `--store`; store or an ancestor is a symlink; store missing/not a directory; `jobs/` missing/unreadable; `HOME` unset or the store is outside `HOME`; an `OSError` opening the store. |

## `installer/install.sh`

Source: `installer/install.sh`.

| Code | When |
|---|---|
| `0` | `--help`/`-h`; a dry run; a successful install/upgrade; `--doctor` or `--uninstall` that passed. |
| `1` | Any `die`: missing/old Python (needs 3.10+), missing `agy` or `git`, missing `AGY_MCP_SRC`, a failed venv/pip step, an unknown `--client`, unknown option, conflicting `--doctor`/`--uninstall`, or the inside-dsh refusal. `--doctor` returns 1 when it finds any problem and 0 when healthy; `--uninstall` returns 1 on a problem and 0 on success. |

## `scripts/*`

Source: each script's `main`. These are developer/evidence tools, not part of
the MCP contract; only the first two have a deliberately stable 0/1/2 contract.

| Script | Exit semantics |
|---|---|
| `check_no_behavior_change.py` | `0` snapshot saved / AST matches; `1` behaviour change detected; `2` bad baseline, unparseable source or argparse usage error. |
| `gen_codemap_index.py` | `0` fresh, written, or printed; `1` the generated block is stale (`--check`); `2` missing `CODEMAP.md`, missing markers or usage error. |
| `watch_orphans.py` | Ad hoc: `0` when it completes its per-file runs; non-zero on a subprocess/IO failure. |

The six one-shot round drivers (`real_smoke.py`, `smoke_real.py`,
`smoke_fixes_real.py`, `run_f1.py`, `run_f2.py`, `run_mutations.py`) were
removed after their evidence rounds; their committed outputs stay under
`docs/reports/`.

## Sources

- `src/agy_mcp/server.py` — `main`, `resolve_agy_binary`, `parse_args`, the
  SIGTERM handler and the config-error path.
- `src/agy_mcp/cleanup/jobs_cli.py` — `main` and every `return 0/1/2`.
- `src/agy_mcp/cleanup/fixtures_cli.py` — `main` and every `return 0/1/2`.
- `src/agy_mcp/config.py` — `ConfigError`.
- `installer/install.sh` — `die`, `agy_mcp_usage`, `--doctor`, `--uninstall`.
- `scripts/check_no_behavior_change.py` — the snapshot/compare returns.
- `scripts/gen_codemap_index.py` — the `--write`/`--check`/`--stdout` returns.
- `scripts/watch_orphans.py` — the ad-hoc return contract.
- `doc/audits/2026-11-02-part2c-coverage.md` — §8, the five-entry-point
  inventory (COV-05).
