# CONFIRMED FINDINGS — Gemini 3.8 Flash Adversarial Review of cleanup_jobs.py

### Finding: FAIL-OPEN in ~/agy-review/gemini2/test_finding_commondir_deleted.py

- **Repro script:** `~/agy-review/gemini2/test_finding_commondir_deleted.py`
- **Command:** `env -u PYTHONPATH .venv/bin/python -m agy_mcp.cleanup_jobs --store ~/agy-review/gemini2/scratch_commondir_repro/store --project ~/agy-review/gemini2/scratch_commondir_repro/wt1 --older-than-days 0 --yes`
- **Severity:** FAIL-OPEN
- **Cleaner output:** candidates=['00000000-0000-0000-0000-000000000001'], deleted_ids=['00000000-0000-0000-0000-000000000001'] (job dir deleted=True)
- **Git output:** git -C wt1 returned 128: fatal: not a git repository: ~/agy-review/gemini2/scratch_commondir_repro/main_repo/.git/worktrees/wt1; git -C main_repo lists work2 registered=True
- **Details:** When repo_path is a linked worktree (repo_path/.git is a pointer file) and actual_gitdir/commondir is deleted, git cannot read the repository from repo_path (fatal: not a git repository, rc=128), and main_repo still lists the worktree as registered. However, cleanup_jobs.py line 276 only inspects commondir if commondir_file.exists(). When absent, candidate_wt_roots remains missing common_gitdir/worktrees, so _check_worktree_status falls through to (False, None) and classifies the job as candidate (deletable). With --yes, the job record is actually deleted from the store.

---

### Finding: FAIL-OPEN in ~/agy-review/gemini2/test_finding_worktree_path_unregistered.py

- **Repro script:** `~/agy-review/gemini2/test_finding_worktree_path_unregistered.py`
- **Command:** `env -u PYTHONPATH .venv/bin/python -m agy_mcp.cleanup_jobs --store ~/agy-review/gemini2/scratch_wt_path_repro/store --project ~/agy-review/gemini2/scratch_wt_path_repro/repo --older-than-days 0 --yes`
- **Severity:** FAIL-OPEN
- **Cleaner output:** candidates=['00000000-0000-0000-0000-000000000001'], deleted_ids=['00000000-0000-0000-0000-000000000001'] (job dir deleted=True)
- **Git output:** git -C repo lists worktree as registered (~/agy-review/gemini2/scratch_wt_path_repro/custom_worktree)
- **Details:** When job.json specifies a custom worktree_path and worktree_path/.git is unlinked before 'git worktree prune' is run, real git continues to list the worktree as registered in repo/.git/worktrees. However, _check_worktree_status lines 422-427 only checks if target_path matches job_dir or job_dir/work (or their parents). It completely fails to check if target_path matches the job's worktree_path. As a result, cleaner returns is_registered=False, classifies the job record as a candidate, and under --yes deletes it while git still lists the worktree.

---

### Finding: FAIL-OPEN in ~/agy-review/gemini2/test_finding_commondir_deleted.py

- **Repro script:** `~/agy-review/gemini2/test_finding_commondir_deleted.py`
- **Command:** `env -u PYTHONPATH .venv/bin/python -m agy_mcp.cleanup_jobs --store ~/agy-review/gemini2/scratch_commondir_repro/store --project ~/agy-review/gemini2/scratch_commondir_repro/wt1 --older-than-days 0 --yes`
- **Severity:** FAIL-OPEN
- **Cleaner output:** candidates=['00000000-0000-0000-0000-000000000001'], deleted_ids=['00000000-0000-0000-0000-000000000001'] (job dir deleted=True)
- **Git output:** git -C wt1 returned 128: fatal: not a git repository: ~/agy-review/gemini2/scratch_commondir_repro/main_repo/.git/worktrees/wt1; git -C main_repo lists work2 registered=True
- **Details:** When repo_path is a linked worktree (repo_path/.git is a pointer file) and actual_gitdir/commondir is deleted, git cannot read the repository from repo_path (fatal: not a git repository, rc=128), and main_repo still lists the worktree as registered. However, cleanup_jobs.py line 276 only inspects commondir if commondir_file.exists(). When absent, candidate_wt_roots remains missing common_gitdir/worktrees, so _check_worktree_status falls through to (False, None) and classifies the job as candidate (deletable). With --yes, the job record is actually deleted from the store.

---

### Finding: FAIL-OPEN in ~/agy-review/gemini2/test_finding_worktree_path_unregistered.py

- **Repro script:** `~/agy-review/gemini2/test_finding_worktree_path_unregistered.py`
- **Command:** `env -u PYTHONPATH .venv/bin/python -m agy_mcp.cleanup_jobs --store ~/agy-review/gemini2/scratch_wt_path_repro/store --project ~/agy-review/gemini2/scratch_wt_path_repro/repo --older-than-days 0 --yes`
- **Severity:** FAIL-OPEN
- **Cleaner output:** candidates=['00000000-0000-0000-0000-000000000001'], deleted_ids=['00000000-0000-0000-0000-000000000001'] (job dir deleted=True)
- **Git output:** git -C repo lists worktree as registered (~/agy-review/gemini2/scratch_wt_path_repro/custom_worktree)
- **Details:** When job.json specifies a custom worktree_path and worktree_path/.git is unlinked before 'git worktree prune' is run, real git continues to list the worktree as registered in repo/.git/worktrees. However, _check_worktree_status lines 422-427 only checks if target_path matches job_dir or job_dir/work (or their parents). It completely fails to check if target_path matches the job's worktree_path. As a result, cleaner returns is_registered=False, classifies the job record as a candidate, and under --yes deletes it while git still lists the worktree.

---

