# Reference index

Status: current
Audience: operators, integrators, contributors

Exhaustive lookup pages for the facts an operator or integrator needs. Each page
narrows one question and names its `## Sources`; the pages with a generated
block are refreshed from the code and checked by `scripts/check_docs.py`, so a
new error code, key, variable, tool, event or persisted field cannot drift
silently.

| Page | Answers | Source of truth | Generated block |
|---|---|---|---|
| [errors.md](errors.md) | Every `error.code`: meaning, who raises it, how to recover. | `manager.py`, `runner.py`, `worktree.py`, `server.py` | error codes |
| [environment.md](environment.md) | The 16 variables read by the server and the installer. | `os.environ` reads, `installer/install.sh` | variables |
| [exit-codes.md](exit-codes.md) | Exit statuses for the five entry points. | `server.py`, both cleaners, `install.sh`, `scripts/` | none |
| [config-keys.md](config-keys.md) | The 27 config keys: type, default, validator, effect. | `Config` dataclass, `_validate_config_dict` | keys/types/defaults |
| [mcp-tools.md](mcp-tools.md) | The 10 MCP tools: parameters, returns, errors, examples. | `server.py`, `manager.py`, `tools_schema.json` | parameters |
| [state-layout.md](state-layout.md) | State paths, per-job files, permissions, the 55 `job.json` fields. | `jobs.py`, `models.py`, `manager.py` | `job.json` fields |
| [log-formats.md](log-formats.md) | `audit.jsonl` events, `usage.jsonl` rows, `server.log`, `cli.log`. | `jobs.py`, `usage.py`, `server.py`, `cli_log.py` | audit events |
| [manifests.md](manifests.md) | Cleanup manifest and `--json` schemas for both cleaners. | `cleanup_jobs.py`, `cleanup_fixtures.py` | none |
| [cli-cleanup-jobs.md](cli-cleanup-jobs.md) | The job-record cleaner: flags, modes, refusals, exits, JSON. | `cleanup_jobs.py`, SPEC §23 | none |
| [cli-cleanup-fixtures.md](cli-cleanup-fixtures.md) | The fixture cleaner: signature, flags, refusals, exits, JSON. | `cleanup_fixtures.py`, SPEC §22 | none |
| [cli-scripts.md](cli-scripts.md) | The nine `scripts/` helpers: flags, writes, exits, safety. | `scripts/*.py` | none |

## How the generated blocks work

```bash
.venv/bin/python scripts/gen_reference.py --check
.venv/bin/python scripts/gen_reference.py --write
```

Each generated block sits between `<!-- BEGIN GENERATED: <key> -->` and
`<!-- END GENERATED: <key> -->`. `--write` replaces only that block; the prose
around it is hand-written. `--check` exits 1 when a block is stale, and
`scripts/check_docs.py` treats a stale block as a finding. The renderers read:

- `src/agy_mcp/*.py` for `code="..."`, `code: str = "..."`, `code = "..."`,
  `os.environ` reads and `event="..."` names;
- `src/agy_mcp/config.py` (`Config`) for config keys, types and defaults;
- `src/agy_mcp/models.py` (`Job`) for the persisted fields;
- `tests/fixtures/tools_schema.json` for the MCP tool parameters;
- `installer/install.sh` for the `AGY_MCP_*` inputs.

## Writing a new reference page

Use [the page template](../developing/writing-docs.md): title, status, audience,
the lookup, a `## Sources` footer. A new mechanical column gets a renderer in
`scripts/gen_reference.py` and a marker in the page; a new hand-written fact
gets a row and an update to the relevant source file. Never edit a generated
block by hand.

## Sources

- `doc/developing/writing-docs.md` — the page template and the mandatory
  Sources rule.
- `doc/developing/gates.md` — the exact docs-check commands.
- `scripts/gen_reference.py` — the generated blocks and their renderers.
- `scripts/check_docs.py` — the freshness, link, Sources and coverage checks.
- `doc/audits/2026-11-02-part2c-coverage.md` — the coverage gap list these
  pages close (COV-01–COV-10).
- `doc/audits/2026-11-02-part2d-migration.md` — §E.7, the checks the checker
  implements.
