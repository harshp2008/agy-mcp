# Cleanup manifests and `--json` payloads

Status: current
Audience: operators, integrators, contributors

Both cleaners write an **ids-only** manifest in real mode (`--yes`) and only
when there is at least one planned candidate. The file is a crash-safe record of
what was planned and what happened; it never contains a repository path, a
prompt, a title or a diff.

## Manifest file

- Location: `<store>/jobs/.cleanup-manifest-<UTC>.json`.
- Name: the UTC timestamp from `datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")`.
- Write: JSON with `indent=2` and a trailing newline, temp file in the same
  directory, `fsync`, `chmod 0600`, `os.replace`.
- Two writes: a `planned` manifest before the first deletion, then a
  `completed`/`partial` manifest after the loop.
- The cleaners skip `.cleanup-manifest-*` entries while scanning, so a manifest
  can never be classified as a job.
- A dry run writes no manifest (`report["manifest"]` is `null`).

## `cleanup_jobs` manifest

Planned:

```json
{
  "status": "planned",
  "mode": "project",
  "planned_ids": ["<job-id>", "<job-id>"],
  "project": "~/code/widget"
}
```

Completed or partial:

```json
{
  "status": "completed",
  "mode": "job",
  "planned_ids": ["<job-id>"],
  "removed_ids": ["<job-id>"],
  "failed_ids": []
}
```

| Field | Type | When |
|---|---|---|
| `status` | `"planned"`, `"completed"`, `"partial"` | `planned` before deletion; `partial` when any `failed_ids`; `completed` otherwise. |
| `mode` | `"project"` or `"job"` | The selected mode. |
| `planned_ids` | `list[str]` | Both writes: every candidate, sorted. |
| `removed_ids` | `list[str]` | Completed write only: directories actually deleted. |
| `failed_ids` | `list[str]` | Completed write only: candidates that were not deleted (re-check failure, edge protection, deletion error, or a requested `--job` id that was not deleted). |
| `project` | `str` | Project mode only: the normalized target project path. |

## `cleanup_fixtures` manifest

Planned:

```json
{
  "status": "planned",
  "planned_ids": ["<job-id>"]
}
```

Completed or partial:

```json
{
  "status": "completed",
  "removed_ids": ["<job-id>"],
  "failed_ids": []
}
```

The fixture cleaner's completed manifest has **no** `mode`, `project` or
`planned_ids` field: its schema is `status`, `removed_ids`, `failed_ids`. The
planned manifest has only `status` and `planned_ids`. This is the current code
shape, not a typo.

## `cleanup_jobs --json` (project/job mode)

The JSON is the `run_cleanup` return object:

| Field | Type | Meaning |
|---|---|---|
| `store` | `str` | Resolved store path. |
| `mode` | `"project"` or `"job"` | Selected mode. |
| `project` | `str` or `null` | Normalized target in project mode; `null` in job mode. |
| `dry_run` | `bool` | `true` unless `--yes`. |
| `older_than_days` | `float` | Age threshold used in project mode. |
| `candidates` | `list[object]` | Each: `job_id`, `title`, `state`, `repo_path`, `age_days`, `reasons`. |
| `kept` | `list[object]` | Each: `job_id`, `reasons`. |
| `skipped` | `list[object]` | Rows not deleted: real mode, plus ineligible requested ids in a `--job` dry run. Each: `job_id`, `reasons`. |
| `candidate_count` | `int` | `len(candidates)`. |
| `kept_count` | `int` | `len(kept)`. |
| `skipped_count` | `int` | `len(skipped)`. |
| `deleted_ids` | `list[str]` | Deleted job ids (empty in a dry run). |
| `deleted_count` | `int` | `len(deleted_ids)`. |
| `failed_ids` | `list[str]` | Candidates that were not deleted; a `--job` dry run also lists requested ids that were not candidates. |
| `manifest` | `str` or `null` | Manifest path in real mode; `null` in a dry run. |
| `warnings` | `list[str]` | Fail-closed store warnings (for example an unreadable kept record). |

`--list --json` has a different shape:

```json
{
  "store": "~/.../agy-mcp",
  "projects": [
    {"repo_path": "~/code/widget", "total_jobs": 4, "eligible_count": 2,
     "states": {"done": 3, "failed": 1}}
  ],
  "warnings": []
}
```

`projects` is sorted by `repo_path`; `eligible_count` is a dry-run candidate
count for that project; `states` counts jobs by state string.

## `cleanup_fixtures --json`

| Field | Type | Meaning |
|---|---|---|
| `store` | `str` | Resolved store path. |
| `dry_run` | `bool` | `true` unless `--yes`. |
| `min_age_days` | `float` | Age threshold used. |
| `candidates` | `list[object]` | Each: `job_id`, `title`, `state`, `repo_path`, `reasons`. |
| `kept` | `list[object]` | Each: `job_id`, `reasons`. |
| `skipped` | `list[object]` | Real mode only: `job_id`, `reasons`. |
| `candidate_count`, `kept_count`, `skipped_count` | `int` | List lengths. |
| `deleted_ids` | `list[str]` | Deleted job ids (empty in a dry run). |
| `deleted_count` | `int` | `len(deleted_ids)`. |
| `failed_ids` | `list[str]` | Candidates that were not deleted. |
| `manifest` | `str` or `null` | Manifest path in real mode; `null` in a dry run. |

## Sources

- `src/agy_mcp/cleanup/store_io.py` — `_write_manifest` and
  `_safe_delete_job_dir` (the deletion the manifest records).
- `src/agy_mcp/cleanup/fixtures_cli.py` — the fixture `run_cleanup` return
  object and the planned/completed manifest dicts.
- `src/agy_mcp/cleanup/jobs_cli.py` — the job `run_cleanup` return object, the
  `planned`/`completed` manifest dicts and `_list_projects`.
- `doc/design/spec.md` — §22–§23, the normative cleaner contracts.
- `doc/audits/2026-11-02-part2c-coverage.md` — §10, the manifest/JSON inventory
  (COV-09).
