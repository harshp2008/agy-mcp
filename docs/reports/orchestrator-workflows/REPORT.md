# Orchestrator workflow selection: report

- **Branch:** `exp/orchestrator-workflows`, based on `main` at `5380b95`
  ("Merge branch 'exp/agymcp-fixes-2'"). Nothing was merged into `main`.
- **Time:** 2026-10-27, 01:32-01:46 IST.
- **Scope:** make the standing orchestrator block tell the orchestrator how to
  choose between GATED, MICRO, RESEARCH and DIRECT, keep that block small, and
  put the worked examples in `docs/ORCHESTRATION.md` instead.

## What changed, file by file

### `installer/agents_block.md` (34 -> 43 lines, +9 net)

Added one section between "When to delegate" and "Running a job":

- `## Choosing a workflow`: write `docs/orchestrator/PLAN.md` first, its first
  line names the workflow and why; an ambiguous goal with nobody watching takes
  the most reasonable reading, says which, continues.
- **GATED** (default when tests can check the result): contract and
  `tests/acceptance/` first, one big job with "make tests/acceptance pass,
  never modify tests/acceptance, add your own tests", longest `agy_wait` and no
  polling, gate order (`git diff <base> -- tests/acceptance` empty, review, read
  the diff, merge into `integration/<topic>`, run acceptance + full suite), no
  trust in the job's success claim, at most one fix job, optional
  tests/hardening job gated the same way.
- **MICRO**: 3-5 chunks, one job each; review, diff, merge, full suite and
  `agy_cleanup` after each.
- **RESEARCH**: orchestrator owns design/interfaces/synthesis; only
  self-contained sub-topics delegated; claims `VERIFIED` only with a source,
  otherwise `ASSUMED`.
- **DIRECT**: tiny work, no agy.
- **Always**: self-contained prompt (goal, files, constraints, 3-10 step
  checklist, verify command, commit subject <= 72 characters), `agy_usage`
  before every start, handover below the quota floor logged in
  `docs/orchestrator/DECISIONS.md`, odd states handled with
  `agy_status`/`agy_resume`/`agy_cancel`, no digging into agy-mcp's source,
  config or state directories, no re-reading large files or polling.

All existing rules were kept. The only edited existing text is the checklist
range in "Running a job": `3-8` -> `3-10`, so the new all-jobs rule and the
`agy_start` rule agree.

### `installer/install.sh`

The guidance is embedded verbatim in the `agy_mcp_write_agents_guidance`
heredoc. The heredoc body was replaced from the markdown file, so the two copies
are byte-identical (checked with a script; see `sync-check.txt`). `bash -n`
passes. The installer tests already compare the generated dsh and generic blocks
with `installer/agents_block.md`.

### `docs/ORCHESTRATION.md`

New final section `## Choosing a workflow` (48 lines) with the ordered decision
rule (GATED / RESEARCH / MICRO / DIRECT), the ambiguous-goal rule, and a worked
examples table for the three clipsync runs:

| Run | Workflow | Jobs | Steps | Approx. cost | Weekly points |
| --- | --- | --- | --- | --- | --- |
| 1 | RESEARCH, then prototype chunks | 5 small | 82 | ~$0.12 | 1.73 |
| 2 | GATED | 1 build + 1 fix | 46 | ~$0.04 | 0.57 |
| 3 | MICRO | 4 | 49 | ~$0.05 | 1.60 |

The section records that run 2's gate caught a `connect()` deadlock the job
itself missed, that run 3 added extra tests and hardening, and that each run is
a single sample, not a controlled comparison. It does not claim GATED is always
cheaper.

### `docs/DECISIONS.md`

Added **D79** (the next free number; D78 was last) with the rationale for a
short block (it is injected into every session and replayed on every step, so
examples would be paid for repeatedly), the workflow rules, the 34 -> 43 line
budget, the test names, and the evidence location. No existing decision was
edited.

### `tests/test_installer_chapter.py` (+31 lines, 4 tests)

- `test_agents_block_names_the_four_workflows` - the `## Choosing a workflow`
  section exists, all four names `GATED`, `MICRO`, `RESEARCH`, `DIRECT` appear,
  and the block names `docs/orchestrator/PLAN.md` and the first-line rule.
- `test_agents_block_protects_acceptance_tests` - `tests/acceptance/`,
  `never modify tests/acceptance`, the
  `git diff <base> -- tests/acceptance` gate, and
  "never trust the job's claim of success".
- `test_agents_block_states_the_commit_subject_limit` -
  `commit subject <= 72 characters` and `3-10 step checklist`.
- `test_agents_block_fits_the_workflow_budget` - the block is at most 62 lines.

## Metrics

| Metric | Before | After |
| --- | --- | --- |
| `installer/agents_block.md` lines | 34 | 43 |
| Net growth | - | +9 (limit: +28) |
| Test cap | 62 | 43 (passes) |

Raw counts are in `block-counts.txt`.

## Tests run

All commands were run from the repository root
`~/Documents/GITHUB PROJECTS/agy-mcp` with `.venv/bin/python`.

1. Baseline collection before any change:
   `pytest --collect-only -q` -> `656 tests collected`.
2. Targeted: `pytest tests/test_installer_chapter.py -q -k "agents_block"`
   -> `13 passed, 31 deselected`, saved in `targeted-tests.txt`.
3. New tests collected: `pytest tests/test_installer_chapter.py
   --collect-only -q -k "names_the_four_workflows or
   protects_acceptance_tests or commit_subject_limit or workflow_budget"`
   -> the four tests in `new-test-cases.txt`.
4. Full suite on the final code tree: `pytest -q` ->
   `660 passed in 223.64s (0:03:43)`, exit 0, saved in
   `final-full-suite.txt`. (An earlier run was `660 passed in 215.55s`, saved in
   `full-suite.txt`, before a one-line correction to the D79 line count. A
   second rerun was discarded because the suite's repository-cleanliness guard
   caught the report files being written into the tree during the run; it was
   redone with a clean tree and is the authoritative run above.)

The 656 existing tests still pass; the 4 added tests bring the total to 660.

## Uncertainties and judgement calls

- The three clipsync numbers are the owner-supplied figures from the request.
  They were not re-measured in this session and cannot be: the real `agy` was
  not run and `~/.gemini` was not touched. They are labelled owner-reported and
  single-sample in `docs/ORCHESTRATION.md`.
- The `3-8` -> `3-10` checklist change edits an existing rule. It is the minimum
  needed to avoid two contradictory checklist limits; no existing safety rule
  was removed.
- `docs/CODEMAP.md` was **not** regenerated: no module under `src/agy_mcp/` was
  added, removed or changed, and `tests/test_codemap_fresh.py` passed in the
  full suite. The AST baseline was **not** re-saved for the same reason (no
  executable source change); no check demanded it.
- The real installer was **not** run against the real home. The installer tests
  use temporary HOMEs and fake wheels, as required. The copy in
  `installer/install.sh` is byte-identical to the markdown file, so the block
  tested is the block installed.
- No file under `~/.dsh`, `~/.config/ai-stack`, `~/.local/state/agy-mcp`,
  `~/.gemini` or `~/.ssh` was touched. The account email does not appear
  unmasked in any file added or changed here.

## Commands for the owner

1. Run the tests:

   ```bash
   cd "~/Documents/GITHUB PROJECTS/agy-mcp"
   .venv/bin/python -m pytest -q
   ```

2. Merge into `main` when satisfied (this branch has not touched `main`):

   ```bash
   cd "~/Documents/GITHUB PROJECTS/agy-mcp"
   git switch main
   git merge --no-ff exp/orchestrator-workflows
   ```

3. Push the new block into the real `~/.dsh/AGENTS.md`, from a **normal
   terminal**, not from inside a dsh session (the installer refuses inside
   dsh):

   ```bash
   cd "~/Documents/GITHUB PROJECTS/agy-mcp"
   ./installer/install.sh --client dsh --upgrade
   ```

   The installer writes the block between the `ai-stack agymcp` markers and
   backs up `AGENTS.md` to `.bak`. If it offers to restart dsh, accept, or
   restart dsh yourself.

## Evidence files

All under `docs/reports/orchestrator-workflows/`:

- `block-counts.txt` - before/after `wc -l` and `git diff --numstat`.
- `sync-check.txt` - embedded copy equality and `bash -n`.
- `targeted-tests.txt` - the 13 targeted installer tests.
- `new-test-cases.txt` - the four new test ids.
- `full-suite.txt`, `final-full-suite.txt` - the two full runs.
- `change.diff` - the complete diff of the code/docs/test changes.
- `commit1.txt` - the commit stat.
