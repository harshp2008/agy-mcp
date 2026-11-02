## Finding 1: Unhandled RecursionError in `_extract_strings` on deeply nested candidate JSON causes unhandled traceback in `--list` and wrong exit code (exit 1) in `--project` dry-run

- **Severity:** CRASH / WRONG-EXIT-CODE
- **Repro Script:** `~/agy-review/gemini4/repro_depth_bomb.py`
- **Exact Command:**
  ```bash
  env -u PYTHONPATH .venv/bin/python ~/agy-review/gemini4/repro_depth_bomb.py
  env -u PYTHONPATH .venv/bin/python -m agy_mcp.cleanup_jobs --store ~/agy-review/gemini4/store_depth_bomb --list
  ```
- **Cleaner Output:**
  ```text
  Traceback (most recent call last):
    File "<frozen runpy>", line 198, in _run_module_as_main
    File "<frozen runpy>", line 88, in _run_code
    File "~/Documents/GITHUB PROJECTS/agy-mcp/src/agy_mcp/cleanup_jobs.py", line 1801, in <module>
      sys.exit(main())
               ^^^^^^
    File "~/Documents/GITHUB PROJECTS/agy-mcp/src/agy_mcp/cleanup_jobs.py", line 1660, in main
      projects, warnings = _list_projects(store_path, args.older_than_days)
                           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    File "~/Documents/GITHUB PROJECTS/agy-mcp/src/agy_mcp/cleanup_jobs.py", line 1537, in _list_projects
      res = run_cleanup(
            ^^^^^^^^^^^^
    File "~/Documents/GITHUB PROJECTS/agy-mcp/src/agy_mcp/cleanup_jobs.py", line 1223, in run_cleanup
      new_strings.extend(_extract_strings(c_data))
                         ^^^^^^^^^^^^^^^^^^^^^^^^
    File "~/Documents/GITHUB PROJECTS/agy-mcp/src/agy_mcp/cleanup_fixtures.py", line 204, in _extract_strings
      strings.extend(_extract_strings(v))
                     ^^^^^^^^^^^^^^^^^^^
    [Previous line repeated 991 more times]
  RecursionError: maximum recursion depth exceeded
  ```
  And under `--project /tmp/repo` (dry-run):
  ```bash
  env -u PYTHONPATH .venv/bin/python -m agy_mcp.cleanup_jobs --store ~/agy-review/gemini4/store_depth_bomb --project /tmp/repo
  ```
  Exits with status `1` and prints:
  ```text
  Unexpected internal error: maximum recursion depth exceeded
  ```
- **Expected Behavior:**
  Per SPEC §23 and D89/D92, the cleaner CLI must never crash with an unhandled traceback, and dry-run/`--list` modes must exit `0` (or `2` on usage refusal). Exit `1` is strictly reserved for partial deletions under `--yes`. When a candidate record with nested structures transitions to kept status due to edge protection, edge extraction must handle recursion iteratively or fail-closed without an unhandled traceback, and `--list` mode must catch errors from `run_cleanup` rather than letting them escape to stderr as a Python traceback.

## Finding 2: `cleanup_fixtures` returns exit code 1 instead of refusal code 2 on unreadable `store/jobs` directory

- **Severity:** WRONG-EXIT-CODE
- **Repro Script:** `~/agy-review/gemini4/repro_unreadable_store_fixtures.py`
- **Exact Command:**
  ```bash
  env -u PYTHONPATH .venv/bin/python ~/agy-review/gemini4/repro_unreadable_store_fixtures.py
  env -u PYTHONPATH .venv/bin/python -m agy_mcp.cleanup_fixtures --store ~/agy-review/gemini4/store_unreadable_fixtures
  ```
- **Cleaner Output:**
  ```text
  Unexpected internal error: [Errno 13] Permission denied: '~/agy-review/gemini4/store_unreadable_fixtures/jobs'
  ```
  Exits with status `1`.
- **Expected Behavior:**
  Per SPEC §23 and D88/D92, unreadable `store/jobs` directory is a safety refusal check and must exit with status `2` and a clean single-line error message without reporting an unexpected internal error or exiting with status `1`. While `cleanup_jobs.py` checks `next(store_jobs.iterdir(), None)` and exits `2`, `cleanup_fixtures.py` failed to include this preflight check and crashed inside `run_cleanup()`, exiting `1`.

## Finding 3: `cleanup_fixtures` uses bare `json.loads` without duplicate-key rejection, causing edge references to be silently overwritten and deleting referenced jobs under `--yes`

- **Severity:** DELETE-WRONG-JOB
- **Repro Script:** `~/agy-review/gemini4/repro_duplicate_key_fixture.py`
- **Exact Command:**
  ```bash
  env -u PYTHONPATH .venv/bin/python ~/agy-review/gemini4/repro_duplicate_key_fixture.py
  env -u PYTHONPATH .venv/bin/python -m agy_mcp.cleanup_fixtures --store ~/agy-review/gemini4/store_dup_key_fixture --yes
  ```
- **Cleaner Output:**
  ```text
  Store: ~/agy-review/gemini4/store_dup_key_fixture
  Dry run: False
  Min age (days): 1.0
  Candidates (1):
    11111111-1111-1111-1111-111111111111 [done] /tmp/pytest-of-testuser/pytest-1/test_foo0/repo
      - fixture signature match: terminal state, pytest temp repo, no live process, age > min_age_days
  Kept (1):
    22222222-2222-2222-2222-222222222222
      - state 'running' is not terminal (done, failed, cancelled)
  Deleted: 1
  Manifest: ~/agy-review/gemini4/store_dup_key_fixture/jobs/.cleanup-manifest-20261102T041013Z.json
  ```
- **Expected Behavior:**
  Per SPEC §23 line 1022 and D88 rule 4 / D89 rule 8: "Duplicate keys in any job.json read by the cleaner across all modes ... are rejected as corrupt (via json.loads with object_pairs_hook raising ValueError, R4) and fail closed through existing corrupt-record paths. If any kept job record across the store is corrupt JSON, a non-object root, or unreadable, all candidates are kept fail-closed with a stated reason."
  Because `cleanup_fixtures.py` calls bare `json.loads` instead of `_safe_json_loads`, a duplicate key in a kept job record silently overwrites the previous value in CPython. When the overwritten key contained a reference to a candidate job ID, that edge reference is completely lost, leaving the referenced job classified as eligible and resulting in its deletion under `--yes`.

