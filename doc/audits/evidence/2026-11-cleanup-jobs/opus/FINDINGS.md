# CONFIRMED FINDINGS — Opus Adversarial Review of cleanup_jobs.py

## F1: Bare-repo HEAD not-a-file → FAIL-OPEN (3 variants)

**Root cause:** `_check_worktree_status` line 176-179: when `repo_path/.git` is absent,
the code gates bare-repo worktree scanning on `(repo_path / "HEAD").is_file()`. If HEAD
is absent, a directory, or a dangling symlink, `is_file()` returns False and the entire
`candidate_wt_roots` list stays empty. The function falls through to line 439
`return (False, None)` — marking the worktree as NOT registered. But the `worktrees/`
directory with valid entries still exists on disk, and git itself cannot read the repo
(`returncode=128`). Per the differential oracle definition, `is_candidate=True` when
`git_works=False` is FAIL-OPEN.

**Variants confirmed:**
| Mutation | git_works | cleaner says | Verdict |
|---|---|---|---|
| HEAD replaced with directory | False | candidate (deletable) | FAIL-OPEN |
| HEAD replaced with dangling symlink | False | candidate (deletable) | FAIL-OPEN |
| HEAD deleted entirely | False | candidate (deletable) | FAIL-OPEN |

**Not affected (correctly fail-closed):**
- HEAD → empty file (is_file()=True, content passes)
- HEAD → symlink to valid file (is_file()=True through symlink)
- HEAD → chmod 000 (is_file()=True, OSError caught)
- HEAD → file with BOM (is_file()=True, content rejected)
- HEAD → file with NUL (is_file()=True, content rejected)

**Severity:** FAIL-OPEN

**Repro scripts:**
- `~/agy-review/opus/attack_01_bare_repo_head_not_file.py` (HEAD → directory)
- `~/agy-review/opus/attack_04_bare_head_variations.py` (all variants)
- `~/agy-review/opus/attack_06_bare_head_delete_full.py` (full end-to-end with --yes delete)

**Repro command (HEAD deleted variant):**
```bash
cd ~/Documents/GITHUB\ PROJECTS/agy-mcp
env -u PYTHONPATH .venv/bin/python ~/agy-review/opus/attack_06_bare_head_delete_full.py
```

**Confirmed with --yes:** Job record was actually deleted from a scratch store.

**SPEC violation:** D95 R1 says "Any directory followed into git metadata ... must
look like a git directory: it must contain a regular file named HEAD." But the bare
repo path never gets followed because the code bails out before checking worktrees/.

**Not covered by existing tests:** The differential harness (tests/test_cleanup_jobs_differential.py)
targets `common_worktrees_entry_gitdir`, `common_worktrees_entry_commondir`,
`common_worktrees_entry_locked`, `common_worktrees_entry_dir`, `repo_path_dot_git_file`,
and `gitdir_commondir`. None target the bare repo's own `HEAD` file. Layout D uses a
bare repo but only mutates files INSIDE `.git/worktrees/` entries, not the bare repo's
own HEAD.

---


## F2: Bare repo with .git directory created inside → FAIL-OPEN

**Root cause:** `_check_worktree_status` L175-312 checks `repo_path/.git` with
an if/elif chain: `.git` directory (L302) takes priority over the bare-repo
detection (L176-179). When a `.git` directory exists inside a bare repo (unusual
but possible — e.g., nested init, manual mkdir), the code enters the `.git`-is-a-
directory branch and looks for worktree entries inside `.git/worktrees/`. But the
actual worktree entries are in `bare_repo/worktrees/` (the bare repo root), not
in `.git/worktrees/`. The code finds no entries and returns `(False, None)`.

Meanwhile, `git -C bare.git worktree list` correctly ignores the `.git` subdirectory
and still sees the worktree as registered.

**Severity:** FAIL-OPEN (git_works=True, git_registered=True, cleaner says candidate)

**Repro script:** `~/agy-review/opus/attack_15b_bare_dotgit_shadow.py`

**Repro command:**
```bash
cd ~/Documents/GITHUB\ PROJECTS/agy-mcp
env -u PYTHONPATH .venv/bin/python ~/agy-review/opus/attack_15b_bare_dotgit_shadow.py
```

**Not covered by existing tests:** The differential harness Layout D uses a bare repo
but never creates a `.git` directory inside it. This is a separate root cause from F1.

---

## F1 addendum: HEAD → FIFO on bare repo → FAIL-OPEN

Same root cause as F1 (bare repo HEAD not a file). `is_file()` returns False for
FIFOs. Confirmed in attack_15_bare_edge_cases.py.

---

