# CONFIRMED FINDINGS — Adversarial Fuzz Review of cleanup_jobs.py (Round 3)

## Finding F3-1: Git repository metadata text files (`HEAD` and `config`) with NUL bytes or UTF-8 BOM cause FAIL-OPEN deletion

- **Repro script:** `~/agy-review/gemini3/repro_f3_git_metadata_nul_bom.py`
- **Command:** `env -u PYTHONPATH .venv/bin/python ~/agy-review/gemini3/repro_f3_git_metadata_nul_bom.py`
- **Severity:** FAIL-OPEN
- **Root Cause:**
  In `src/agy_mcp/cleanup_jobs.py`, `_validate_full_git_dir` (lines 173-211) validates repository structure for normal git repos, bare repos, submodule modules directories, and linked worktree commondir targets.
  However:
  1. For `HEAD` (lines 186-189), it delegates to `_validate_git_file` (lines 145-158), which only verifies that `raw = file_path.read_bytes()` is non-empty (`raw.strip()`). It never checks for UTF-8 BOM (`\xef\xbb\xbf`), NUL bytes (`\x00`), non-UTF-8 bytes, or control characters.
  2. For `config` (lines 198-210), it only checks `config_path.is_file()` and `_ = config_path.read_bytes()`. It performs zero character or syntax validation.
  
  When either `HEAD` or `config` contains NUL bytes or a UTF-8 BOM:
  - Real git fails completely: `git status` and `git worktree list` exit with status 128 (`fatal: not a git repository (or any of the parent directories): .git` for HEAD; `fatal: bad config line 1 in file .../config` for config). Real git cannot read the repository (`git_works = False`).
  - `_validate_full_git_dir` returns `(True, None)`, treating the corrupted directory as a healthy git repository.
  - When the job's worktree is unlinked, pruned, or not registered in `.git/worktrees`, `_check_worktree_status` falls through to `(False, None)`.
  - `run_cleanup` classifies the job as an eligible candidate (`is_candidate = True`).
  - Under `--yes`, the job directory is deleted from the state store while git cannot read the repo.

- **Git output:**
  - `HEAD` corrupted: `git status` & `git worktree list` returncode `128`: `fatal: not a git repository (or any of the parent directories): .git`
  - `config` corrupted: `git status` & `git worktree list` returncode `128`: `fatal: bad config line 1 in file .../config`

- **Cleaner output:**
  - Returncode: `0`
  - Output: `candidates: [<job_id>]`, `deleted_ids: [<job_id>]`, `deleted_count: 1`, `failed_ids: []`
  - Job record directory deleted from disk: `True`

- **Confirmed Variants:**
  | Variant | Target File | Mutation | Git rc | Git stderr | Cleaner Verdict |
  |---|---|---|---|---|---|
  | Normal repo config NUL | `.git/config` | Prepended `\x00\x00` | 128 | `fatal: bad config line 1 in file .git/config` | Deleted (FAIL-OPEN) |
  | Normal repo HEAD NUL | `.git/HEAD` | Prepended `\x00\x00` | 128 | `fatal: not a git repository ...` | Deleted (FAIL-OPEN) |
  | Normal repo HEAD BOM | `.git/HEAD` | Prepended `\xef\xbb\xbf` | 128 | `fatal: not a git repository ...` | Deleted (FAIL-OPEN) |
  | Bare repo config NUL | `config` | Prepended `\x00\x00` | 128 | `fatal: bad config line 1 in file ./config` | Deleted (FAIL-OPEN) |
  | Bare repo HEAD NUL | `HEAD` | Prepended `\x00\x00` | 128 | `fatal: not a git repository ...` | Deleted (FAIL-OPEN) |
  | Bare repo HEAD BOM | `HEAD` | Prepended `\xef\xbb\xbf` | 128 | `fatal: not a git repository ...` | Deleted (FAIL-OPEN) |

---
