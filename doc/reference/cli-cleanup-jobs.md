# `agy_mcp.cleanup_jobs` CLI

Status: current
Audience: operators, integrators

Safe, dry-run-first job-record cleaner for an agy-mcp state store. It removes
stale `<store>/jobs/<uuid>/` records; it never deletes git branches,
repositories, worktrees or agy conversation records. The full eligibility rules
are normative in `doc/design/spec.md` §23; this page is the flag, mode, refusal, output and
exit-code reference.

```bash
env -u PYTHONPATH .venv/bin/python -m agy_mcp.cleanup_jobs \
  --store ~/.local/state/agy-mcp [--list | --project PATH | --job ID ...] \
  [--older-than-days 7.0] [--yes] [--json]
```

## Flags

| Flag | Required | Default | Meaning |
|---|---|---|---|
| `--store PATH` | yes | — | State store to inspect. It must exist, be a directory, resolve without any symlinked component, and be inside `HOME`. |
| `--list` | one mode | off | Read-only project/state/eligible-count report. `--yes` is ignored in this mode. |
| `--project PATH` | one mode | — | Clean stale records whose `repo_path` normalizes to `PATH`. |
| `--job ID` | one mode | — | Clean one explicit job id (canonical UUID; repeatable). The age requirement is waived in this mode. |
| `--older-than-days FLOAT` | no | `7.0` | Minimum age for a project-mode candidate. Must be finite and non-negative. |
| `--yes` | no | off | Actually delete. Without it the run is a dry run. |
| `--json` | no | off | Print a JSON object only (see [manifests.md](manifests.md)). |

Exactly one of `--list`, `--project` and `--job` is required. None or more than
one exits 2 before touching the store.

## Modes

- **`--list`** opens the store read-only and prints the resolved store, any
  warnings, then per project: `repo_path`, total jobs, eligible-for-cleanup
  count and a state histogram. `--json` emits `{store, projects, warnings}`.
- **`--project`** scans every terminal job in the store, keeps the ones whose
  `repo_path` does not normalize to the target, and treats the rest as
  candidates subject to `--older-than-days` and the eligibility/edge-protection
  rules. A candidate is re-classified and edge-protected again immediately
  before deletion.
- **`--job`** considers only the named ids. A malformed UUID or an id that is
  not in the store is refused with exit 2; an id that is present but ineligible
  is kept and reported. In real mode with no candidate, the run exits 2.

The default is a dry run: it prints candidates, kept rows and the reasons, and
changes nothing on disk (not even the manifest).

## Sample output

Shape only: ids and paths are placeholders, and the store line is the resolved
path the tool prints.

```text
Store: <state-root>
Dry run: True
Project: <project>
Older than (days): 7.0
Candidates (2):
  <job-id> [done] <project>
    - terminal state (done), no live process, no registered worktree
Kept (1):
  <job-id>
    - job age (0.20 days) is less than older-than-days (7.0 days)
```

In real mode the output adds `Deleted: N`, `Skipped/Failed (N)` and
`Manifest: <path>`. `--json` replaces the text with the object in
[manifests.md](manifests.md).

## Refusals (exit 2)

The store preflight is fail-closed:

- missing, non-directory, or any symlinked component in the store path or its
  ancestors;
- `jobs/` missing, not a directory, symlinked, or unreadable;
- `HOME` unset, or the resolved store not inside `HOME`;
- empty/whitespace `--store`, `--project` or `--job`;
- no mode, or more than one mode;
- `--older-than-days` non-finite or negative;
- malformed `--job` UUID, or a `--job` id not present in the store.

An unreadable or corrupt **kept** record makes the run fail closed: it warns and
does not delete the candidates that record could protect. A candidate that
fails the pre-delete re-check is reported in `failed_ids`/`skipped` and the run
exits 1 in real mode.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success: any `--list`, any dry run, or a `--yes` run with no failures. |
| `1` | A `--yes` run left at least one requested candidate undeleted (partial cleanup), or an unexpected internal error after the store was opened. |
| `2` | A refusal/bad-usage case above. |

See [exit-codes.md](exit-codes.md) for the other entry points.

## Sources

- `src/agy_mcp/cleanup/jobs_cli.py` — `parse_args`, `main`, `run_cleanup`,
  `_list_projects` and `_classify_job_for_cleanup` (the implementation; the
  `src/agy_mcp/cleanup_jobs.py` module path is a re-export shim).
- `src/agy_mcp/cleanup/worktree_status.py` — the worktree-registration
  decision called by `_classify_job_for_cleanup`.
- `src/agy_mcp/cleanup/git_metadata.py` — the strict git metadata readers.
- `src/agy_mcp/cleanup/store_io.py` — the shared `_write_manifest`,
  `_safe_delete_job_dir`, `_check_pid_status`, `_check_pgid_status`,
  `load_job_json` and `normalize_repo_path`.
- `tests/test_cleanup_jobs.py` and `tests/test_cleanup_jobs_fixes2.py` —
  the pinned cleaner behaviour.
- `tests/cleanup/test_git_oracle_differential.py` — the git-oracle differential suite.
- `doc/design/spec.md` — §23, the normative eligibility and refusal rules.
- `doc/reference/manifests.md` — the manifest and `--json` schemas.
