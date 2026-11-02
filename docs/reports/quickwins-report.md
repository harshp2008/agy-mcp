# Quickwins report: additive `agy_status` fields and `node --test` allowlist

- Branch: `feat/status-fields-and-allowlist` (based on `main` at `be649e9`; **not merged**).
- Frozen acceptance freeze commit: `2e8bfff`.
- Final gated commit: `a5e0a4d` (the report commit that follows is docs-only).
- Date: 2026-10-31 (Asia/Calcutta).

## What changed

### Task 1 — additive `agy_status` fields

`agy_status` now returns four additive fields (no renames, no removals; every
pre-existing status key is still present and the MCP tool-description snapshot
is unchanged):

- `conversation_id`: `Job.conversation_id` (string or `null`).
- `conversation_id_source`: `Job.conversation_id_source`
  (`"early"` / `"finalized"` / `null`).
- `runs`: a projection of `Job.runs[]` with exactly
  `{index, started_at, ended_at, wall_clock_seconds, outcome}` per entry.
  `index` is the 0-based position in `Job.runs[]`; the internal
  `run_generation`, `agy_duration_seconds` and `tokens` fields and prompt text
  are omitted.
- `wall_clock_seconds`: sum of the wrapper-measured run wall clocks; `null`
  when no run has finished (empty `runs`, or no row has a wall clock).

New run rows written by `supervisor._record_run` and
`recovery.reconcile_on_startup` carry `started_at` (ISO 8601 UTC), `ended_at`
(ISO 8601 UTC) and `outcome` (the job state at run end). Jobs and run rows
persisted before these fields existed load and project `null`/`[]` without
crashing. The value comes from the wrapper's existing
`_current_run_seconds`/`_record_run` measurement, never agy's
`duration_seconds` (conversation age). Recovery never treats
`Job.start_time` (`/proc/<pid>/stat` clock ticks) as a Unix epoch.

Files: `src/agy_mcp/models.py`, `manager.py`, `supervisor.py`, `recovery.py`;
`SPEC.md`, `CHANGELOG.md`, `docs/DECISIONS.md` (D84), `docs/CODEMAP.md`.

### Task 2 — `node --test` verify allowlist

The default `verify_allowlist` gains exactly one entry, `"node --test"`
(appended after `"go test"`). Accepted grammar:

```
node --test [<relative-path> ...]
```

Each `<relative-path>` must be a non-empty token that:

- does not start with `-` (this rejects `--` and every node flag, including
  `-e`/`--eval`, `-p`/`--print`, `-r`/`--require`, `--import`, `--loader`,
  `--test-reporter`, `--test-name-pattern`, unknown flags, and flags after a
  would-be `--`);
- contains none of the shell metacharacters `;` `&` `|` (backtick) `$` `(` `)`
  `<` `>` newline CR backslash double quote single quote;
- is relative (absolute paths rejected), has no `..` path component, and
  resolves inside the boundary after symlink resolution — the job worktree once
  it exists, otherwise (at `agy_start`, before provisioning) the repository
  root;
- may contain glob characters `*`, `**`, `?`, `[`, `]` and refers to a file or
  directory (existence is not required).

Rejected: empty/whitespace argv, `node` alone, `node test`,
`node --test=...` (glued), `/usr/bin/node --test`, any extra flag, empty
argument tokens, and anything outside the grammar above.
`--test-reporter` and `--test-name-pattern` are deliberately **not** added:
the wrapper does not need formatted or filtered output and rejecting them
keeps the surface minimal. Rejections use the existing `is_verify_allowed` /
`verify_rejection_message` path (`error.code=verify_not_allowed`), whose
message lists accepted forms (including `node --test`). The five existing
entries behave exactly as before.

Files: `src/agy_mcp/config.py`, `src/agy_mcp/verify.py`; `SPEC.md` section 13
and the configuration table, `docs/DECISIONS.md` (D84), `CHANGELOG.md`.

### Task 3 — reports

`docs/reports/stale-jobs-report.md` and
`docs/reports/agy-capability-inventory.md` were committed unchanged as
`7164800` after scanning for credentials, emails and other personal data (none
found; they contain local paths and job metadata only).

## Tests and gate evidence

- Baseline before any change: **713 passed** (`env -u PYTHONPATH .venv/bin/python -m pytest -q`, 228.39 s).
- New frozen tests: **55** (`tests/test_quickwins_status_fields.py` 5,
  `tests/test_quickwins_node_allowlist.py` 50). `tests/test_config.py` also had
  its one default-allowlist expectation updated **in the freeze commit**; it is
  frozen from then on.
- Total suite on the final tree: **768 collected / 768 passed**.

Frozen files (sha256 must stay fixed after `2e8bfff`):

| file | sha256 |
|---|---|
| `tests/test_quickwins_status_fields.py` | `c063b0e56665fc2134be5566a73d730eafbf57347f2478dce1ada428869d8d68` |
| `tests/test_quickwins_node_allowlist.py` | `c956c0842f903a92314c3812b921f5d95778b04b3fa4bada39d0ed0cee624cf2` |
| `tests/test_config.py` | `04ad3307f9e558c420b00e2173d003e6e01744171a602b009b4c79305277023d` |

Gate on the final gated commit (`a5e0a4d`):

- `git diff --name-only 2e8bfff -- tests` → empty.
- `git diff --name-only 2e8bfff..HEAD -- tests` → empty.
- New tests: **ALL 20 RUNS PASSED**
  (`gate-run.mjs --repeat 20 -- env -u PYTHONPATH .venv/bin/python -m pytest
  tests/test_quickwins_status_fields.py tests/test_quickwins_node_allowlist.py
  tests/test_config.py -q`).
- Full suite: **ALL 5 RUNS PASSED** (`gate-run.mjs --repeat 5 -- env -u
  PYTHONPATH .venv/bin/python -m pytest -q`; 5 × ~234-238 s).
- `.venv/bin/ruff check src tests scripts` → clean.
- Changed files since the freeze: `CHANGELOG.md`, `SPEC.md`,
  `docs/CODEMAP.md`, `docs/DECISIONS.md`,
  `src/agy_mcp/{config,manager,models,recovery,supervisor,verify}.py` — all in
  the allowed scope (`src/agy_mcp/**`, `docs/**`, `SPEC.md`, `CHANGELOG.md`);
  no `tests/**` change.

History: the first full-suite gate failed on 2 tests that compare the MCP tool
descriptions to a snapshot; the job had edited the optional `agy_status`
docstring. `ad8344a` reverts that docstring, so tests stayed untouched and the
snapshot still matches. `a557de0` reverts an out-of-scope `usage.py` cleanup
workaround the job added only to appease the test harness's live-state guard
(see below). `a5e0a4d` fixes the confirmed review findings.

## agy job

- One implementation job: `6d9be49b-5aac-41a7-a5cf-8a73b4ee4521`, branch
  `agy/status-fields-node---test-allowlist-f537d9ff`, base `2e8bfff`,
  head `58f6dac`; merged into the feature branch at `cb819af`.
- Wrapper-reported wall clock: **1407.91 s** total (two model turns:
  implementation plus one verify-driven resume).
- Wrapper `quota_estimate` (aggregate for the job):
  `{tokens: 809853, weekly_pct_points: 0.6074, five_hour_pct_points: 2.1866,
  weekly_per_1k_tokens: 0.00075, five_hour_per_1k_tokens: 0.0027,
  estimated: true}`.
- Wrapper `usage_cost` (measured): `{five_hour_pct_points: 8.67,
  weekly_pct_points: 2.48, measured: true, concurrent_jobs: 1}`.
- Tokens: 704041 input, 105812 output, 70388 thinking, 21943057 cache-read,
  total 809853, 2 turns.
- Wrapper verify attempts: 2. The first failed **only** because the job
  worktree lives under `~/.local/state/agy-mcp/jobs/...`, so the tests'
  session-wide live-state guard saw the throwaway `TMPDIR/usage.log` as a live
  state write (an environment false positive; the same frozen test passes from
  the main checkout). The job's second attempt appended an out-of-scope
  `usage.py` cleanup change; I reverted it and the frozen tests then passed
  20/20 and the full suite 5/5 from the main checkout without it.
- Rounds used: **1 agy job, 0 additional agy repair rounds** (2 self-repair
  commits + 1 review-fix commit). The per-run split of the wrapper's wall clock
  is not exposed by the live (pre-change) server; the values above are the
  wrapper's aggregate for the job.

## Fresh review

Two fresh, read-only reviewers were used (neither saw the agy self-report or
the gate verdict). The first returned 6 defects; the second independently
returned the same low-severity `wall_clock_seconds` finding and nothing else.
All 6 were reproduced by running the reviewer's checks, then fixed in
`a5e0a4d`:

| # | sev | finding | check result before fix | fix |
|---|---|---|---|---|
| 1 | MED | `verify.py` node validation ignored the `repo_path` passed by `manager.start`; at start (`worktree_path=None`) a repo symlink escape was accepted | `is_verify_allowed("node --test link/evil.mjs", allow, repo_path=repo)` → `True` | node validator now uses the worktree when present, else `repo_path`, and resolves symlinks; check now `False`, control path still `True` |
| 2 | MED | `recovery.py` used `/proc` clock-tick `start_time` as a Unix epoch, giving recovered legacy runs a 1970 `started_at` | `datetime.fromtimestamp(12345).year` → 1970; recovered row showed 1970 | fallback is `created_at` (or now); reproduced recovery now gives 2026 |
| 3 | LOW | `manager.py` returned `0.0` for `wall_clock_seconds` when run rows exist but none has a clock | status payload → `0.0` | returns `None`; reproduced → `None` |
| 4 | LOW | 101-char line in `recovery.py` broke ruff E501 | ruff exit 1 | ruff clean |
| 5 | LOW | SPEC config table still advertised the 5-entry default | `sed -n '753p' SPEC.md \| grep -c 'node --test'` → 0 | now 1 |
| 6 | LOW | SPEC `job.json.runs[]` lists still omitted `started_at`/`ended_at`/`outcome` | grep → 0 | both lists updated |

Also fixed while checking the docs: the inline-code metacharacter list
contained a literal backtick that broke the Markdown code span in `SPEC.md`
and D84.

Unconfirmed findings: none from either reviewer. The second reviewer's only
finding was already fixed.

## Not verified / limitations

- The new `agy_status` fields are implemented in source but the running MCP
  server (started before this work) was **not restarted**, so they are not live
  until Harsh restarts the server.
- `node --test` execution was smoke-checked once end-to-end with a throwaway
  Node test (`run_verify` → rc 0, `status=passed`); the frozen tests exercise
  the allowlist grammar, not a real Node project suite.
- Residual symlink TOCTOU for a fresh job: the command is validated before the
  worktree exists (repository boundary) and on resume (worktree boundary), but
  it is not re-validated immediately before `run_verify`. A symlink created by
  the agent *after* start and named in the pre-approved relative path is
  therefore not re-resolved at execution time. The reviewer's concrete check
  (a repo symlink present at start) is fixed; this narrower agent-created case
  remains architecture-level and is flagged for Harsh.
- The frozen live-run test accepts any terminal state for the fake-agy success
  scenario (`done`/`needs_review`/`failed`/`cancelled`) while requiring a
  recorded run; it is not a strict "the job must end done" assertion.

## Merge commands for Harsh (do not run before you decide)

```bash
cd "~/Documents/GITHUB PROJECTS/agy-mcp"
git checkout main
git merge --no-ff feat/status-fields-and-allowlist \
  -m "merge: additive agy_status fields and node --test verify allowlist"
git push origin main   # only if you want to publish
```

`main` has not moved (`git log --oneline HEAD..main` is empty), so the merge
is clean. The agy job branch is kept; its worktree can be removed with
`agy_cleanup`.
