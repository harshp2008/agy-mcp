# `agy_mcp.cleanup_fixtures` CLI

Status: current
Audience: operators, contributors

Safe, dry-run-first cleaner for **pytest fixture rows** in an agy-mcp state
store. It recognises the exact shape pytest leaves behind (`/tmp/pytest-of-*/`
`pytest-<n>/test_...<m>/...`) and refuses everything else. For real stale job
records use [`cleanup_jobs`](cli-cleanup-jobs.md) instead; this tool is the
narrower one.

```bash
env -u PYTHONPATH .venv/bin/python -m agy_mcp.cleanup_fixtures \
  --store ~/.local/state/agy-mcp [--min-age-days 1.0] [--yes] [--json]
```

## Flags

| Flag | Required | Default | Meaning |
|---|---|---|---|
| `--store PATH` | yes | — | State store to inspect. It must exist, be a directory, resolve without any symlinked component, and be inside `HOME`. |
| `--min-age-days FLOAT` | no | `1.0` | Minimum age (days) for a fixture candidate. Must be finite and non-negative. |
| `--yes` | no | off | Actually delete. Without it the run is a dry run. |
| `--json` | no | off | Print the JSON object from [manifests.md](manifests.md). |

There is no project or job selector: the candidate set is every fixture-shaped
terminal record in the store.

## Candidate signature

A job directory is a candidate only when **all** of these hold (the classifier
returns the first failing reason and keeps the row otherwise):

1. the directory is not a symlink, is a directory, and its name is a canonical
   lowercase UUID;
2. no symlink exists anywhere inside it;
3. `job.json` is a regular file (not a symlink), at most 1 MiB, valid JSON, an
   object, and its `job_id` equals the directory name;
4. `state` is terminal: `done`, `failed` or `cancelled`;
5. `repo_path` is under the system tempdir and contains a `pytest-of-*`
   component, then a `pytest-<digits>` component, then a `test_...<n>`
   component with at least one component after it; it must not share the
   store's own pytest basetemp;
6. any `pid`/`pgid` is absent or confirmed dead (a live or ambiguous process
   keeps the row);
7. `updated_at` (falling back to `created_at`) is timezone-aware ISO 8601, and
   the age is at least `--min-age-days`.

In real mode each candidate is re-classified immediately before deletion, and
`_safe_delete_job_dir` deletes bottom-up without following symlinks, refusing a
path that resolves outside the job directory.

## When not to use it

- On a store that contains real jobs. It will keep them (the pytest signature
  will not match), but it also scans every directory and writes a warning-free
  report; `cleanup_jobs` is the tool with the real eligibility rules.
- To delete a job you can name. There is no `--job` flag; use `cleanup_jobs`.
- To clean a store outside `HOME` or reached through a symlink: it refuses.

## Sample output

Shape only: the store line is the resolved path the tool prints, and ids/paths
are placeholders.

```text
Store: <state-root>
Dry run: True
Min age (days): 1.0
Candidates (1):
  <job-id> [done] /tmp/pytest-of-<user>/pytest-3/test_cleanup_0/repo
    - fixture signature match: terminal state, pytest temp repo, no live process, age > min_age_days
Kept (1):
  <job-id>
    - repo_path <path> is not under temp dir /tmp
```

Real mode adds `Deleted: N`, `Skipped/Failed (N)` and `Manifest: <path>`.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Any dry run; a `--yes` run with no failures. |
| `1` | A `--yes` run with any `failed_ids`, or an unexpected internal error after the store was opened. |
| `2` | A refusal/bad-usage case: empty store, store/ancestor symlink, missing/non-directory store, missing/unreadable `jobs/`, `HOME` unset or store outside it, non-finite/negative `--min-age-days`, argparse usage error. |

See [exit-codes.md](exit-codes.md) for the other entry points.

## Sources

- `src/agy_mcp/cleanup/fixtures_cli.py` — `parse_args`, `main`, `run_cleanup`,
  `_classify_job`, `_is_pytest_temp_repo` and `_pytest_basetemp` (the
  implementation; the `src/agy_mcp/cleanup_fixtures.py` module path is a
  re-export shim).
- `src/agy_mcp/cleanup/store_io.py` — the shared `_check_pid_status`,
  `_check_pgid_status`, `_safe_delete_job_dir` and `_write_manifest`.
- `tests/test_cleanup_fixtures.py` — the fixture contract.
- `doc/design/spec.md` — §22, the normative fixture-cleaner contract.
- `doc/reference/manifests.md` — the manifest and `--json` schemas.
