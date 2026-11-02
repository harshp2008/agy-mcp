# Stale agy-mcp jobs: diagnosis and cleanup
- Generated: `2026-10-29T19:36:34.836752+00:00` (UTC).
- Scope: read-only diagnosis of the live state dir; the only writes were the job.json backup and this report. No agy model job was run; `~/.gemini` was not read or modified.
- Cleanup operation used: `JobManager.cancel()` (the exact function behind `agy_cancel`, `server.py:334`) on an explicit ID list.

## 1. Diagnosis (snapshot before cleanup, from the backup)
Counts are from the 19:20:02Z backup; ages are measured at the 19:34Z cleanup, after every queued record passed the 2 h rule (332 job.json total):

| State | Count | Age range (h) | Live process | Repo ownership |
|---|---:|---|---:|---|
| failed | 272 | 70.7-87.0 | 0 | pytest temp: 272 |
| queued | 40 | 2.1-2.7 | 0 | pytest temp: 40 |
| done | 15 | 35.9-84.6 | 0 | clipsync-lab: 13, agy-scratch: 2 |
| needs_review | 5 | 4.7-74.6 | 0 | clipsync-lab: 5 |

- All 40 queued records had `pid=null`; no job in any state had a live process. The 5 needs_review jobs had dead pids.
- Repo ownership overall: **312 pytest temp repos** (`/tmp/pytest-of-<user>/pytest-*/.../repo`), **18 clipsync-lab**, **2 agy-scratch**.
- Five oldest records (all terminal test records, ~70-87 h):

| updated_at (UTC) | state | job | title | repo |
|---|---|---|---|---|
| 2026-10-26T04:36:09.543162+00:00 | failed | `885a15eb-c6d9-4e63-ad74-df7e6a36eb0f` | F9 Resume Verify Test | `/tmp/pytest-of-<user>/pytest-1085/test_resume_revalidates_verify0/repo` |
| 2026-10-26T04:36:09.551423+00:00 | failed | `b20da6a7-326a-4701-b739-7ca2927fc085` | F2 Corrupt Snapshot Test | `/tmp/pytest-of-<user>/pytest-1085/test_corrupt_or_missing_snapsh0/repo` |
| 2026-10-26T04:36:09.557924+00:00 | failed | `998e10e5-33b5-487c-a7a6-740703054d54` | F2 Recovery Test | `/tmp/pytest-of-<user>/pytest-1085/test_recovery_uses_persisted_b0/repo` |
| 2026-10-26T04:36:09.563110+00:00 | failed | `e00a3eab-c0ee-4b48-bff6-33420f7cbd49` | F2 Review Test | `/tmp/pytest-of-<user>/pytest-1085/test_review_uses_persisted_bas0/repo` |
| 2026-10-26T04:36:09.568217+00:00 | failed | `0790799c-2850-4d2b-81d6-ce6373b88a15` | F9 Resume Verify Test | `/tmp/pytest-of-<user>/pytest-1083/test_resume_revalidates_verify0/repo` |

Five needs_review jobs (all real clipsync-lab work; left untouched):

| job | title | reason | age h |
|---|---|---|---:|
| `0f813431-5738-4c63-a168-f0cfda1db90b` | phase2b LAN addresses and discovery | `verify_timeout` | 23.1 |
| `14186961-f528-4fdb-8cfd-39c8ec800e73` | meshcore groups: isolated multi-group hub, relay role, E2E keys | `invalid_commit_message` | 24.5 |
| `16f0bd05-9bd0-4e82-8ec2-3e574a51bf1f` | state channel implementation | `verify_timeout` | 4.7 |
| `4e1b3b79-ede2-486d-b2a9-4fb68639caed` | meshcore phase 2a: elected hub over existing relay | `invalid_commit_message` | 34.4 |
| `cecc329b-972b-4b5e-b318-44b444c00927` | Implement network relay server + transport (WebSocket) | `invalid_commit_message` | 74.6 |

## 2. Concurrency counting and reapers
- Active = `{queued, running}` plus the legacy `starting` value: `jobs.py:249-261` (`count_active_jobs`). The limit is `max_concurrent_jobs`, default **2** (`config.py:35`); enforced under `concurrency_lock` in `manager.py:355-362` (start) and `manager.py:1409-1416` (resume). The live config does not override it.
- No periodic reaper exists for queued/needs_review/needs_decision:
  - `recovery.reconcile_on_startup()` runs **only at server startup** (`server.py:571`); it would mark `queued -> failed` (`recovery.py:297-312`). The live server started 2026-10-27 02:24:08, before these records were created, and was not restarted.
  - `cleanup_retained_jobs()` (`jobs.py:513-576`) runs from `agy_list`/reconcile, but only moves **terminal** (`done/failed/cancelled`) job dirs older than `job_retention_days` (default 14) to `trash/`; it never selects queued/running/needs_review/needs_decision.
  - No code transitions needs_review/needs_decision by age; they simply do not count as active.

## 3. Safety of `agy_cancel` and `agy_cleanup`
### `agy_cancel` (`server.py:321-339`) -> `JobManager.cancel` (`manager.py:1165-1224`)
- `queued`: validates `queued -> cancelled` (`states.py:59-65`), writes `job.json` and appends `audit.jsonl` via `transition_job_state`/`save_job` (`jobs.py:264-320`, `jobs.py:172-177`). Then, **only if `worktree_path` is a directory**, it runs `git worktree remove --force`, `git worktree prune`, and `git branch -D <branch>` in `job.repo_path` (`manager.py:1178-1189`); errors are swallowed. It does not delete the job directory, job.json, audit, prompt, logs, snapshot, or any worktree that does not exist.
- `running`: terminates the supervisor/process group, pre-kill checkpoint commit, then `cancelled` (`manager.py:1190-1221`).
- Any other state: no state change; returns the envelope.
- **Conversation store:** the queued path has no call to `audit`, `transcript`, `usage`, or any `~/.gemini` path. Its call graph is `get_job` (job dir) -> transition/save (state dir) -> optional `run_git` in `job.repo_path`. `transcript.py` is the only module allowed to read agy transcripts (D50) and is not imported here. **SAFE for queued records with `pid=null`.**
### `agy_cleanup` (`server.py:414-442`) -> `JobManager.cleanup` (`manager.py:1570-1603`) -> `cleanup_worktree` (`worktree.py:426-483`)
- Running/queued require `force=True`; force calls `cancel` first (`manager.py:1580-1589`).
- Removes only `<jobdir>/work` if it exists (`remove_worktree`, `worktree.py:353-380`; `git worktree remove --force`/prune) and, with `delete_branch=True`, deletes the job branch via `git branch -D` after an unmerged-reachability gate (`worktree.py:426-476`).
- It **does not delete the job directory or job.json**, so it cannot remove records or free concurrency slots by itself. It also has no agy-conversation path. SAFE regarding conversations, but not a state-cleanup operation.

## 4. Server process and state-write serialization
- Running MCP server: **PID 1099171**, ppid **1099105** (`node ... dsh web --no-open --trusted-host <tailnet-host>`), started **Tue Oct 27 02:24:07 2026**, args `python -m agy_mcp --agy-binary ~/.local/bin/agy --config ~/.config/agy-mcp/config.toml --state-dir ~/.local/state/agy-mcp`. It is the only agy_mcp process, parented by the single `dsh web`; sessions under that DSH share this one server (VERIFIED one process; direct proof that every client connects to it is UNKNOWN).
- `save_job()` is atomic: same-directory tempfile, fsync, `os.replace`, parent fsync (`jobs.py:42-73`, `jobs.py:172-177`). VERIFIED.
- `transition_job_state()` itself takes no file lock; `save_job` has no cross-process lock. Some callers hold in-memory job/commit locks (`manager.cancel`, `resume`, watcher), others call `save_job` directly (supervisor paths). With one server process this is last-writer-wins between threads; cross-process serialization is NOT implemented (UNKNOWN/unsafe for a second writer). The concurrency `flock` (`jobs.py:226-246`) protects slot counting and job creation only.

## 5. Proposed rules (not implemented)
- **Active definition:** count a job as active iff it is `running` and `pid + start_time` match a live process, or it is `queued` with `updated_at` within a short provisioning grace (e.g. 5-10 min) and a supervisor/launch attempt exists. Orphaned `queued` records with `pid=null` and no recent update do not count.
- **Dead-process reaper:** on `agy_list`, `agy_start`, and a periodic timer, scan `running` jobs whose pid/start_time is dead and finalize them exactly as startup recovery does; scan `queued` jobs older than the grace with no live process and transition them to `failed(recovered)` with an audit event. Run under the concurrency flock; keep it idempotent.
- **Abandoned review/decision expiry:** after a configurable `abandoned_review_days` (e.g. 14), mark `needs_review`/`needs_decision` as `failed`/`expired` (or archive metadata to `trash/`) without deleting work products; never delete user worktrees or conversation data automatically. Surface a warning in `agy_list` first.

## 6. Backup
- `~/agy-state-backup-20261030-005002` — **332** `job.json` files copied as `<backup>/<job_id>/job.json`, plus `MANIFEST.txt`. Only `job.json` metadata was copied: no logs, prompts, cli.log, or auth secrets/tokens. Verified count: `332`.

## 7. Candidates and dry run
Candidate rules: process dead/no pid, last activity >2 h, test-like title or `/tmp/pytest-of-<user>/` repo, repo not clipsync-lab/agy-mcp. All **40** active records matched; none were excluded.

| job id | title | state | age h | repo | worktree exists | branch exists |
|---|---|---|---:|---|---:|---:|
| `085ae40f-5519-40b4-99fb-aa742bb5d5da` | F2 Review Test | queued | 2.69 | `/tmp/pytest-of-<user>/pytest-2175/test_review_uses_persisted_bas0/repo` | False | no |
| `785907e1-d5ac-4d7b-a954-c67b8e2dd72c` | F2 Recovery Test | queued | 2.69 | `/tmp/pytest-of-<user>/pytest-2175/test_recovery_uses_persisted_b0/repo` | False | no |
| `7a785c8a-e615-412e-b407-04cbb66c68d4` | F2 Corrupt Snapshot Test | queued | 2.69 | `/tmp/pytest-of-<user>/pytest-2175/test_corrupt_or_missing_snapsh0/repo` | False | no |
| `1f4b4b72-0fa7-4f56-a4a0-05e2a98284bb` | F9 Resume Verify Test | queued | 2.68 | `/tmp/pytest-of-<user>/pytest-2175/test_resume_revalidates_verify0/repo` | False | no |
| `80600429-ef1b-46a0-9054-e9212b4a8b19` | F2 Review Test | queued | 2.59 | `/tmp/pytest-of-<user>/pytest-2180/test_review_uses_persisted_bas0/repo` | False | no |
| `3c3802be-2967-4ecb-9f3e-51724908d669` | F2 Recovery Test | queued | 2.59 | `/tmp/pytest-of-<user>/pytest-2180/test_recovery_uses_persisted_b0/repo` | False | no |
| `f4da6a94-7a0a-4c66-a5cf-ade8982e72c5` | F2 Corrupt Snapshot Test | queued | 2.59 | `/tmp/pytest-of-<user>/pytest-2180/test_corrupt_or_missing_snapsh0/repo` | False | no |
| `5e00bdc6-5a0e-4fb3-a48e-387e06bbb85e` | F9 Resume Verify Test | queued | 2.58 | `/tmp/pytest-of-<user>/pytest-2180/test_resume_revalidates_verify0/repo` | False | no |
| `788e1ad5-24c1-4819-94c3-c1296036b6c9` | F2 Review Test | queued | 2.50 | `/tmp/pytest-of-<user>/pytest-2192/test_review_uses_persisted_bas0/repo` | False | no |
| `de4b13f3-3fce-41e8-8427-50fe2d0b9258` | F2 Recovery Test | queued | 2.50 | `/tmp/pytest-of-<user>/pytest-2192/test_recovery_uses_persisted_b0/repo` | False | no |
| `41c5c80e-71ef-4d03-af04-4d055a8db031` | F2 Corrupt Snapshot Test | queued | 2.50 | `/tmp/pytest-of-<user>/pytest-2192/test_corrupt_or_missing_snapsh0/repo` | False | no |
| `cca236bf-0d62-4827-8aca-3c8865fbb3c2` | F9 Resume Verify Test | queued | 2.50 | `/tmp/pytest-of-<user>/pytest-2192/test_resume_revalidates_verify0/repo` | False | no |
| `72be7fa6-733b-4cda-aabc-760bc93f4ce6` | F2 Review Test | queued | 2.44 | `/tmp/pytest-of-<user>/pytest-2201/test_review_uses_persisted_bas0/repo` | False | no |
| `e829ee90-8855-4175-8f13-e990dcb30dc5` | F2 Recovery Test | queued | 2.44 | `/tmp/pytest-of-<user>/pytest-2201/test_recovery_uses_persisted_b0/repo` | False | no |
| `83b28604-ad87-4064-983b-6fb298061dcb` | F2 Corrupt Snapshot Test | queued | 2.44 | `/tmp/pytest-of-<user>/pytest-2201/test_corrupt_or_missing_snapsh0/repo` | False | no |
| `4a80f9f9-a459-48cb-9104-8e94f68dfa4c` | F9 Resume Verify Test | queued | 2.43 | `/tmp/pytest-of-<user>/pytest-2201/test_resume_revalidates_verify0/repo` | False | no |
| `e6e2bffa-8c25-4729-99e9-509d3679ebfc` | F2 Review Test | queued | 2.36 | `/tmp/pytest-of-<user>/pytest-2216/test_review_uses_persisted_bas0/repo` | False | no |
| `d5929318-e778-49cd-9762-fabf78b87ecc` | F2 Recovery Test | queued | 2.36 | `/tmp/pytest-of-<user>/pytest-2216/test_recovery_uses_persisted_b0/repo` | False | no |
| `79e09033-6e52-43b8-a8dc-aecba9ae6cab` | F2 Corrupt Snapshot Test | queued | 2.36 | `/tmp/pytest-of-<user>/pytest-2216/test_corrupt_or_missing_snapsh0/repo` | False | no |
| `92ce930c-7e79-4760-af57-9694465d2284` | F9 Resume Verify Test | queued | 2.36 | `/tmp/pytest-of-<user>/pytest-2216/test_resume_revalidates_verify0/repo` | False | no |
| `97eba395-7ff7-4b48-aa92-5672197d6029` | F2 Review Test | queued | 2.32 | `/tmp/pytest-of-<user>/pytest-2225/test_review_uses_persisted_bas0/repo` | False | no |
| `3555ff15-78cf-42dd-9b33-8b985aeb3cb5` | F2 Recovery Test | queued | 2.32 | `/tmp/pytest-of-<user>/pytest-2225/test_recovery_uses_persisted_b0/repo` | False | no |
| `02791998-5ea4-43d7-a70d-5cbcae98043e` | F2 Corrupt Snapshot Test | queued | 2.32 | `/tmp/pytest-of-<user>/pytest-2225/test_corrupt_or_missing_snapsh0/repo` | False | no |
| `a661f03f-9d12-4cb8-b765-9fa157d537fa` | F9 Resume Verify Test | queued | 2.32 | `/tmp/pytest-of-<user>/pytest-2225/test_resume_revalidates_verify0/repo` | False | no |
| `dbe52453-00e9-4d5a-80db-9f4ba9e05154` | F2 Review Test | queued | 2.26 | `/tmp/pytest-of-<user>/pytest-2234/test_review_uses_persisted_bas0/repo` | False | no |
| `146f6b77-bb2f-4ae0-91e9-dab1b1ef32ea` | F2 Recovery Test | queued | 2.26 | `/tmp/pytest-of-<user>/pytest-2234/test_recovery_uses_persisted_b0/repo` | False | no |
| `44ad0704-5e75-46a6-99ba-3d1011360704` | F2 Corrupt Snapshot Test | queued | 2.26 | `/tmp/pytest-of-<user>/pytest-2234/test_corrupt_or_missing_snapsh0/repo` | False | no |
| `e9716068-7c96-4a26-9f4d-06b7935cd460` | F9 Resume Verify Test | queued | 2.25 | `/tmp/pytest-of-<user>/pytest-2234/test_resume_revalidates_verify0/repo` | False | no |
| `411ae1d3-fe4f-4c1f-8c87-2cf96caa9404` | F2 Review Test | queued | 2.19 | `/tmp/pytest-of-<user>/pytest-2235/test_review_uses_persisted_bas0/repo` | False | no |
| `3ab79336-b367-4d32-9e0d-f65cadd3e040` | F2 Recovery Test | queued | 2.19 | `/tmp/pytest-of-<user>/pytest-2235/test_recovery_uses_persisted_b0/repo` | False | no |
| `8408c0c3-47ba-4e82-baea-e8a090012545` | F2 Corrupt Snapshot Test | queued | 2.19 | `/tmp/pytest-of-<user>/pytest-2235/test_corrupt_or_missing_snapsh0/repo` | False | no |
| `d5326435-dcfc-4d63-b062-ad9ab427ba7c` | F9 Resume Verify Test | queued | 2.18 | `/tmp/pytest-of-<user>/pytest-2235/test_resume_revalidates_verify0/repo` | False | no |
| `871cf337-74f8-4890-8905-37e9f15879e2` | F2 Review Test | queued | 2.12 | `/tmp/pytest-of-<user>/pytest-2236/test_review_uses_persisted_bas0/repo` | False | no |
| `81a5de1e-a5e9-4b16-83df-86f55309758b` | F2 Recovery Test | queued | 2.12 | `/tmp/pytest-of-<user>/pytest-2236/test_recovery_uses_persisted_b0/repo` | False | no |
| `2334de76-1e5f-4218-b329-cfa2ba26d139` | F2 Corrupt Snapshot Test | queued | 2.12 | `/tmp/pytest-of-<user>/pytest-2236/test_corrupt_or_missing_snapsh0/repo` | False | no |
| `612a4755-3ce9-4e86-9259-8a03dd4cbd38` | F9 Resume Verify Test | queued | 2.12 | `/tmp/pytest-of-<user>/pytest-2236/test_resume_revalidates_verify0/repo` | False | no |
| `6298e945-caa3-4d46-98c3-0182761c8ef9` | F2 Review Test | queued | 2.06 | `/tmp/pytest-of-<user>/pytest-2237/test_review_uses_persisted_bas0/repo` | False | no |
| `18eabd3c-026c-4cc6-8186-ac0f185ca28b` | F2 Recovery Test | queued | 2.06 | `/tmp/pytest-of-<user>/pytest-2237/test_recovery_uses_persisted_b0/repo` | False | no |
| `d73ef12a-e2fd-4d03-95dc-5a0e137de9e5` | F2 Corrupt Snapshot Test | queued | 2.06 | `/tmp/pytest-of-<user>/pytest-2237/test_corrupt_or_missing_snapsh0/repo` | False | no |
| `2d9d0ef1-8c2a-4962-b07d-615fa9d1a48b` | F9 Resume Verify Test | queued | 2.05 | `/tmp/pytest-of-<user>/pytest-2237/test_resume_revalidates_verify0/repo` | False | no |

Dry run: `cancel` performs `queued -> cancelled` plus an audit event. Because every candidate had `worktree_path` missing, the `git worktree remove`/`branch -D` block (`manager.py:1178-1189`) would not execute. It removes **no files, worktrees or branches**; the job directory and logs remain. No candidate was real or ambiguous; all were test titles in pytest temp repos.

## 8. Cleanup result
- Invoked `JobManager.cancel()` once per explicit candidate (40 calls), not a bulk mode. 40/40 succeeded; 0 skipped.
- Before: failed 272, queued 40, done 15, needs_review 5; `total_active_jobs=40`; live processes 0.
- After: failed 272, cancelled 40, done 15, needs_review 5; `total_active_jobs=0` (`agy_list` after cleanup); live processes 0.
- Disk usage before 12,209,019 bytes; after 12,215,659 bytes. The 40 job directories remain (cancel does not delete them); retention will prune them after 14 days.

Left untouched:

- 5 `needs_review` clipsync-lab jobs (real work; review/merge is the owner's decision): `0f813431` verify_timeout, `cecc329b` invalid_commit_message, `16f0bd05` verify_timeout, `14186961` invalid_commit_message, `4e1b3b79` invalid_commit_message. One has the only remaining worktree (`0f813431`, 1.1 MB).
- 15 `done` jobs: 13 clipsync-lab, 2 `~/agy-scratch`; excluded by rule (d).
- 272 `failed` pytest-temp jobs: terminal, no worktree, no branch; `cancel` is a no-op and `cleanup` removes nothing, so they were not candidate actions. They will be retention-pruned when older than 14 days (current max ~87 h).
- One drifted worktree reported by `agy_list`: `0f813431` (clipsync-lab needs_review); not touched.

## 9. Root cause of the jam
- Four tests create a job without `base_state_dir`: `tests/test_f2.py:36`, `tests/test_f2.py:73`, `tests/test_f2.py:121`, `tests/test_f9.py:62`. `create_job` defaults `base_state_dir=None` (`jobs.py:111-122`) and `get_job_dir` then defaults to the **live** `~/.local/state/agy-mcp` (`jobs.py:28-39`). The test then uses its tmp `state_dir` for `get_job_dir`/`save_job`, so the live copy is never advanced past `queued`; it has `pid=null`, no worktree, and remains forever.
- Ten pytest sessions × 4 jobs = **40 leaked queued records**. They count toward the limit of 2 (`count_active_jobs`), so real starts fail with `Active jobs (40) reached concurrency limit (2)`.
- Nothing reaped them: the server started before they existed, startup reconciliation is the only `queued` reaper, and retention covers only terminal jobs >14 days.

## 10. Unverified
- Whether any other client connects to PID 1099171; only one process was observed.
- Cross-process write serialization (no file lock exists; assumed one writer).
- Exact future effect of changing the active definition; not implemented in this run.
