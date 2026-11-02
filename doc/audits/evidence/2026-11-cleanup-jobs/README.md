# Evidence — Cleanup-Jobs Adversarial Review (2026-11)

Status: current
Audience: maintainers, contributors

This directory contains the curated evidence artifacts from the five-reviewer
adversarial audit of the `cleanup_jobs` CLI (`src/agy_mcp/cleanup_jobs.py` and
`src/agy_mcp/cleanup_fixtures.py`) conducted in November 2026.

## Scope and Fixture Retention Policy

The original review environments under `~/agy-review/{opus,gemini2,gemini3,gemini4,ds}`
contained approximately 16 MB of test workspaces, scratch directories, bare repositories,
worktree hierarchies, symlink loops, and intentionally unreadable `mode-000` permission
subtrees (e.g., `opus/scratch_02/repo/.git`, `opus/scratch_07/repo/.git/worktrees`,
`gemini2/scratch_area1/bare.git/worktrees`, `gemini3/scratch/.../refs`, and
`gemini4/store_unreadable_fixtures/jobs`).

Per MIG-05, Phase 0 decision 4, and repository evidence rules:
1. Multi-megabyte fixture directories, active git repositories, and unreadable subtrees
   are excluded from git tracking to prevent history bloat and permission errors.
2. Only `FINDINGS.md` review reports and standalone reproduction / fuzz scripts
   (each under 200 KB) are committed here.
3. All personal filesystem paths have been normalized to `~/...` and personal account
   identifiers have been sanitized per AGENTS.md rules.

## Inventory of Evidence Artifacts

| Source Dir | File | What it is | Finding / Decision | Related Frozen Test |
|---|---|---|---|---|
| `opus` | `FINDINGS.md` | Opus review report of confirmed findings F1 (bare HEAD not-a-file fail-open) and F2 (.git shadow in bare repo) | D96 (R6a, R6b, R6c) | `tests/test_cleanup_jobs_fixes6.py` |
| `opus` | `attack_01_bare_repo_head_not_file.py` | Repro script for bare-repo HEAD missing or directory fail-open mutation variants | D96 (R6b) | `tests/test_cleanup_jobs_fixes6.py` |
| `opus` | `attack_02_head_mutations.py` | Repro script mutating HEAD in bare repos (dangling symlink, invalid type) | D96 (R6b) | `tests/test_cleanup_jobs_fixes6.py` |
| `opus` | `attack_03_gitdir_different_path.py` | Repro script testing worktree gitdir pointers pointing to alternate paths | D90, D94 | `tests/test_cleanup_jobs_fixes5.py` |
| `opus` | `attack_04_bare_head_variations.py` | Repro script testing bare repo HEAD variations (empty file, invalid symbolic ref) | D96, D98 | `tests/test_cleanup_jobs_fixes6.py`, `tests/test_cleanup_jobs_fixes8.py` |
| `opus` | `attack_05_edge_cases.py` | Repro script for edge cases in worktree registration detection | D95, D96 | `tests/test_cleanup_jobs_fixes6.py` |
| `opus` | `attack_06_bare_head_delete_full.py` | Repro script demonstrating full deletion fail-open when bare HEAD is deleted | D96 (R6b) | `tests/test_cleanup_jobs_fixes6.py` |
| `opus` | `attack_07_repo_internals.py` | Repro script testing bare repo internal markers (`objects`, `refs`) | D96 (R6b) | `tests/test_cleanup_jobs_fixes6.py` |
| `opus` | `attack_08_jobjson_types.py` | Repro script testing various malformed and unexpected types in `job.json` fields | D90, D92 | `tests/test_cleanup_jobs_fixes.py`, `tests/test_cleanup_jobs_fixes3.py` |
| `opus` | `attack_08d_investigate.py` | Diagnostic repro script investigating `job.json` field type edge cases | D90, D92 | `tests/test_cleanup_jobs_fixes3.py` |
| `opus` | `attack_09_symlinks.py` | Repro script testing symlinked job directories, worktrees, and git directories | D89, D95 | `tests/test_cleanup_jobs.py`, `tests/test_cleanup_jobs_differential.py` |
| `opus` | `attack_10_toctou.py` | Repro script testing TOCTOU mutation between scan and deletion phases | D90, D91, D92 | `tests/test_cleanup_jobs_fixes.py`, `tests/test_cleanup_jobs_fixes2.py` |
| `opus` | `attack_11_json_paths.py` | Repro script testing `job.json` `repo_path` and `worktree_path` parsing | D90, D97 | `tests/test_cleanup_jobs_fixes.py`, `tests/test_cleanup_jobs_fixes7.py` |
| `opus` | `attack_12_nested_worktrees.py` | Repro script testing nested worktree hierarchies | D96, D97 | `tests/test_cleanup_jobs_fixes6.py`, `tests/test_cleanup_jobs_fixes7.py` |
| `opus` | `attack_13_crlf_whitespace.py` | Repro script testing CRLF line endings and whitespace in git metadata | D93, D95, D98 | `tests/test_cleanup_jobs_fixes4.py`, `tests/test_cleanup_jobs_fixes8.py` |
| `opus` | `attack_14_path_traversal.py` | Repro script testing path traversal sequences in `repo_path` and worktree entries | D89, D90 | `tests/test_cleanup_jobs.py` |
| `opus` | `attack_15_bare_edge_cases.py` | Repro script testing bare repository marker detection edge cases | D96 (R6b) | `tests/test_cleanup_jobs_fixes6.py` |
| `opus` | `attack_15b_bare_dotgit_shadow.py` | Repro script for F2 bare repo with internal `.git` directory shadowing worktrees | D96 (R6a, R6c) | `tests/test_cleanup_jobs_fixes6.py` |
| `opus` | `attack_16_delete_escape.py` | Repro script testing candidate deletion escaping store boundaries | D89, D90 | `tests/test_cleanup_jobs.py` |
| `opus` | `harness.py` | Opus attack execution harness automating attack scenarios | D96 | `tests/test_cleanup_jobs_fixes6.py` |
| `gemini2` | `FINDINGS.md` | Gemini 2 review report of confirmed findings: commondir deleted fail-open and `worktree_path` unregistered fail-open | D97 (R7, R8) | `tests/test_cleanup_jobs_fixes7.py` |
| `gemini2` | `harness.py` | Gemini 2 attack suite execution harness | D97 | `tests/test_cleanup_jobs_fixes7.py` |
| `gemini2` | `test_area11_store_git.py` | Repro script for git repositories located inside the state store | D89, D90 | `tests/test_cleanup_jobs.py` |
| `gemini2` | `test_area12_toctou.py` | Repro script for concurrent state modification during cleanup | D91, D92 | `tests/test_cleanup_jobs_fixes2.py` |
| `gemini2` | `test_area14_odd_ids.py` | Repro script for non-UUID job directory names | D89, D90 | `tests/test_cleanup_jobs.py` |
| `gemini2` | `test_area15_list_corrupt.py` | Repro script for corrupt job records encountered during `--list` | D91, D93 | `tests/test_cleanup_jobs_fixes2.py`, `tests/test_cleanup_jobs_fixes4.py` |
| `gemini2` | `test_area19_concurrent_cleaners.py` | Repro script testing concurrent cleaner CLI invocations | D89 | `tests/test_cleanup_jobs.py` |
| `gemini2` | `test_area1_bare_internals.py` | Repro script for bare repository internal file structures | D96 | `tests/test_cleanup_jobs_fixes6.py` |
| `gemini2` | `test_area2_worktree_entries.py` | Repro script testing corrupted worktree entry metadata | D94, D95 | `tests/test_cleanup_jobs_fixes5.py` |
| `gemini2` | `test_area3_symlink_loops.py` | Repro script for recursive symlink loops in worktree and store paths | D89, D95 | `tests/test_cleanup_jobs.py` |
| `gemini2` | `test_area4_symlinks.py` | Repro script for dangling and directory symlinks | D95 | `tests/test_cleanup_jobs_fixes.py` |
| `gemini2` | `test_area7_nested_and_submodules.py` | Repro script for linked worktrees and submodule checkouts | D97 | `tests/test_cleanup_jobs_fixes7.py` |
| `gemini2` | `test_area9_shared_wt.py` | Repro script for worktrees shared across jobs | D90, D97 | `tests/test_cleanup_jobs_fixes.py`, `tests/test_cleanup_jobs_fixes7.py` |
| `gemini2` | `test_commondir_missing.py` | Repro script testing missing commondir pointer file | D97 (R7) | `tests/test_cleanup_jobs_fixes7.py` |
| `gemini2` | `test_finding_commondir_deleted.py` | Repro script for Bug A (deleted commondir fail-open) | D97 (R7) | `tests/test_cleanup_jobs_fixes7.py` |
| `gemini2` | `test_finding_worktree_path_unregistered.py` | Repro script for Bug B (custom `worktree_path` registration match omission) | D97 (R8) | `tests/test_cleanup_jobs_fixes7.py` |
| `gemini2` | `test_submodule_internals.py` | Repro script for submodule `.git` pointer and `.git/modules` validation | D97 (R7) | `tests/test_cleanup_jobs_fixes7.py` |
| `gemini3` | `FINDINGS.md` | Gemini 3 review report of confirmed finding F3-1 (`HEAD`/`config` with NUL or UTF-8 BOM causes fail-open deletion) | D98 (R10) | `tests/test_cleanup_jobs_fixes8.py` |
| `gemini3` | `fuzz.py` | Multi-process adversarial fuzzing harness testing permutations of git metadata and store records | D98 (R10, R11) | `tests/test_cleanup_jobs_fixes8.py` |
| `gemini3` | `repro_config_nul.py` | Repro script for git `config` file containing NUL bytes causing fail-open deletion | D98 (R10) | `tests/test_cleanup_jobs_fixes8.py` |
| `gemini3` | `repro_f3_git_metadata_nul_bom.py` | Repro script for Finding F3-1 (`HEAD` and `config` with UTF-8 BOM or NUL bytes) | D98 (R10) | `tests/test_cleanup_jobs_fixes8.py` |
| `gemini3` | `repro_head_nul.py` | Repro script for git `HEAD` file containing NUL bytes causing fail-open deletion | D98 (R10) | `tests/test_cleanup_jobs_fixes8.py` |
| `gemini3` | `test_concurrency_and_seam.py` | Repro script testing concurrency and `before_delete` hook seams | D91, D98 | `tests/test_cleanup_jobs_fixes2.py` |
| `gemini4` | `FINDINGS.md` | Gemini 4 review report of confirmed findings: unhandled `RecursionError` in `_extract_strings`, duplicate keys in `cleanup_fixtures.py`, and unreadable store crash | D99 (R13, R14) | `tests/test_cleanup_jobs_fixes9.py` |
| `gemini4` | `fuzz_nongit.py` | Adversarial fuzzing harness for non-git parameters (JSON payloads, depth bombs, store permissions) | D99 (R13, R14, R15) | `tests/test_cleanup_jobs_fixes9.py` |
| `gemini4` | `repro_depth_bomb.py` | Repro script for deeply nested JSON payload hitting recursion limit in `_extract_strings` | D99 (R13) | `tests/test_cleanup_jobs_fixes9.py` |
| `gemini4` | `repro_duplicate_key_fixture.py` | Repro script for duplicate key retention bypass in `cleanup_fixtures.py` | D99 (R14) | `tests/test_cleanup_jobs_fixes9.py` |
| `gemini4` | `repro_unreadable_store_fixtures.py` | Repro script for unreadable store handling in `cleanup_fixtures.py` | D99 (R14) | `tests/test_cleanup_jobs_fixes9.py` |
| `ds` | `depth_child.py` | Probe child process executing pre-delete recheck under default recursion limit | D100 (R18) | `tests/test_cleanup_jobs_fixes10.py` |
| `ds` | `depth_proof.py` | Proof script for D100 R18 demonstrating 100,000-level recursion protection at default recursion limit | D100 (R18) | `tests/test_cleanup_jobs_fixes10.py` |
| `ds` | `diff_fixtures.txt` | Diff output showing changes to `src/agy_mcp/cleanup_fixtures.py` during review rounds | D99, D100 | `tests/test_cleanup_fixtures.py` |
| `ds` | `diff_src.txt` | Diff output showing changes to `src/agy_mcp/cleanup_jobs.py` during review rounds | D90–D100 | `tests/test_cleanup_jobs_fixes*.py` |
| `ds` | `final_full_suite.txt` | Test execution log of full test suite passing after review fixes | D100, D101 | `tests/frozen.sha256` |
| `ds` | `final_quick.txt` | Test execution log of ruff, guard, and fixes6-10 + differential tests passing | D100 | `tests/test_cleanup_jobs_differential.py` |
| `ds` | `pgid_probe.py` | Repro script probing process group `pgid` liveness validation | D99 (R16) | `tests/test_cleanup_jobs_fixes9.py` |
| `ds` | `probe_empty_store.py` | Repro script demonstrating `--store ""` silent CWD normalization and deletion hole | D100 (R17) | `tests/test_cleanup_jobs_fixes10.py` |
| `ds` | `probe_stop_all.py` | Repro script testing stop-all fail-closed pre-delete rule when kept record is mutated | D91, D92 | `tests/test_cleanup_jobs_fixes2.py`, `tests/test_cleanup_jobs_fixes3.py` |
