# Platform support: what runs where, and what still assumes Linux

Status: current
Audience: users, maintainers, contributors

Only Linux has actually been run. Every macOS and Windows verdict below comes
from reading the code (and the CPython/POSIX interface it calls), not from
executing the suite on those systems. The platform claim is deliberately narrow:
**Linux (tested), macOS (untested), Windows via WSL (untested)**.

## Verdict summary

| Area | Linux | macOS | Native Windows | Windows via WSL |
|---|---|---|---|---|
| Server start (`agy-mcp`, `python -m agy_mcp`) | works | works | **blocked** (`fcntl` import) | works |
| Job launch and process-group supervision | works | works | **blocked** (`getpgid`/`killpg`) | works |
| Cancellation and timeout kill | works | works | **blocked** (no process groups) | works |
| Restart/crash recovery of a running job | works | **needs change** (no `/proc`) | **blocked** | works |
| State-directory concurrency lock | works | works | **blocked** (`fcntl`) | works |
| Hardened `.agy` read/write (`dir_fd`, `O_NOFOLLOW`) | works | works (weaker `/proc` check) | **needs change** | works |
| `0600`/`0700` confidentiality of state files | works | works | **needs change** (ACLs) | works |
| Installer (`installer/install.sh`) | works | works, with one mode-preserving fix | **blocked** (bash, venv layout) | works |
| Developer scripts | works | works except `watch_orphans.py` | mostly works, not tested | works |
| Fake `agy` test double | works | works | **blocked** (`select` on pipes) | works |
| Pytest suite | works | **needs change** | **blocked** | works |

"Blocked" means the code path cannot run at all without a port. "Needs change"
means it runs but a documented guarantee is weaker or a test/feature does not
work as intended. "Works" here means the APIs used are portable to that
platform; only Linux was executed.

## Method and limits

The audit was read-only. It grepped `src/`, `scripts/`, `installer/` and
`tests/` for `fcntl`, `os.killpg`, `os.getpgid`, `setsid`,
`start_new_session`, `signal.SIGKILL`/`SIGSTOP`, `os.geteuid`, `dir_fd`,
`O_NOFOLLOW`, `O_DIRECTORY`, `os.fchmod`, `/proc`, symlink handling,
hard-coded absolute paths, bash-isms and GNU-only commands, plus the fake `agy`
and the XDG/`~/.local/bin` assumptions. No source file was changed.

`start_new_session=True` is documented as POSIX-only; CPython's Windows
`_execute_child` accepts it as `unused_start_new_session`, so it is silently
ignored rather than rejected. That is why process-group isolation cannot be
assumed on Windows even before `killpg` is called.

## 1. File locking: `fcntl`

| Site | Mechanism |
|---|---|
| `src/agy_mcp/jobs.py:10` | top-level `import fcntl` |
| `src/agy_mcp/jobs.py:257,261` | `flock(LOCK_EX/LOCK_UN)` for the job lock |
| `src/agy_mcp/jobs.py:407,427` | concurrency lock acquisition/release |
| `src/agy_mcp/jobs.py:444,486` | daily-cap lock |
| `src/agy_mcp/usage.py:12` | top-level `import fcntl` |
| `src/agy_mcp/usage.py:864,869` | usage-log lock |

- **Linux / macOS:** works. `fcntl.flock` is POSIX and macOS provides it.
- **Native Windows:** blocked. `fcntl` does not exist, and because
  `server.py` imports `jobs.py`, the console entry point fails before it can
  serve a request. A port needs `msvcrt.locking`, `portalocker`, or a lock-file
  design; this is a core change, not a shim.
- **WSL:** works (it is Linux).

## 2. Process groups and signals

| Site | Mechanism |
|---|---|
| `src/agy_mcp/runner.py:249` | `start_new_session=True` for `agy` |
| `src/agy_mcp/runner.py:258` | `os.getpgid(pid)` (falls back to `pid`) |
| `src/agy_mcp/runner.py:313,323,332` | `killpg(SIGTERM)`, liveness `killpg(0)`, `killpg(SIGKILL)` |
| `src/agy_mcp/verify.py:601` | `start_new_session=True` for the verify command |
| `src/agy_mcp/verify.py:475,495,503` | `killpg(0)`, `killpg(SIGTERM)`, `killpg(SIGKILL)` |
| `src/agy_mcp/usage.py:631,635,645` | `getpgid`, `SIGTERM`, then always `SIGKILL` |
| `src/agy_mcp/usage.py:701` | `start_new_session=True` for the `/usage` probe |
| `src/agy_mcp/supervisor.py:272,278` | `SIGTERM` then `SIGKILL` to the group |
| `src/agy_mcp/manager.py:1253` | `os.killpg(job.pgid, 15)` |
| `src/agy_mcp/cleanup/store_io.py:150` | `killpg(pgid, 0)` liveness check |

- **Linux / macOS:** works. The whole design of "killing a job takes every
  descendant with it" is POSIX process groups, and both provide `getpgid`,
  `killpg`, `setsid` and the two signals.
- **Native Windows:** blocked. `os.getpgid`/`os.killpg` do not exist and
  `SIGKILL` is not a Windows signal. A port needs Job Objects
  (`CreateJobObject`/`TerminateJobObject`) or `taskkill /T`, plus a separate
  POSIX-signal substitute. `tests/test_runner.py:346`,
  `tests/test_usage_read.py:199` and `tests/test_cleanup_jobs_fixes9.py:551`
  encode the same assumption.
- **WSL:** works.

## 3. `/proc` (Linux-only)

| Site | Use |
|---|---|
| `src/agy_mcp/runner.py:62-105` | parse `/proc/<pid>/stat` fields, including starttime |
| `src/agy_mcp/runner.py:265` | non-`/proc` fallback uses `time.time()`, which never matches later |
| `src/agy_mcp/runner.py:293` | `poll()` for a process not in memory reads `/proc`; any error means "dead" |
| `src/agy_mcp/recovery.py:32-54` | `check_process_liveness` is `/proc`-only |
| `src/agy_mcp/recovery.py:127` | adoption of a running job after restart depends on it |
| `src/agy_mcp/scratchpad.py:225-236` | `/proc/self/fd` verifies an opened fd is inside `.agy` |
| `src/agy_mcp/cleanup/store_io.py:88-96` | `/proc/sys/kernel/pid_max` for plausibility checks |
| `tests/conftest.py:137-142` | resolves `dir_fd` through `/proc/self/fd` |
| `tests/test_runner.py:415`, `tests/test_usage_read.py:25`, `tests/test_wrapper_verify_commit_fix.py:75`, `tests/test_watcher_needs_review_resume.py:22`, `tests/verify/test_verify.py:264` | tests read `/proc/<pid>` directly |

- **Linux:** works; PID-reuse protection and restart adoption are real.
- **macOS:** needs change. There is no `/proc`. Launch works, and in-process
  supervision works, but after a server restart `check_process_liveness`
  returns false, so a still-running `agy` is not adopted and may keep running
  after the job is finalized. `scratchpad._fd_is_inside` returns true when
  `/proc/self/fd` is unreadable, so the symlink-confinement check becomes
  best-effort only (the `O_NOFOLLOW` opens still apply). The `pid_max` fallback
  is 4,194,304 instead of macOS's smaller value, which is fail-open for
  plausibility checks but not a crash. A port needs `psutil`, `ps -o lstart=` or
  a recorded launch marker.
- **Native Windows:** blocked for the same paths (no `/proc`; the test helpers
  and the scratchpad check fail), and restart adoption does not exist.
- **WSL:** works.

## 4. `dir_fd`, `O_NOFOLLOW`, `O_DIRECTORY`, `os.fchmod`

| Site | Mechanism |
|---|---|
| `src/agy_mcp/scratchpad.py:156,159` | `O_NOFOLLOW` write plus `os.fchmod` |
| `src/agy_mcp/scratchpad.py:218,263,276` | open `.agy` as a directory, then open by basename with `dir_fd=` |
| `src/agy_mcp/watcher.py:104,187` | `O_NOFOLLOW` reads of `COMMIT_MSG` |
| `src/agy_mcp/watcher.py:259` | `O_DIRECTORY` to `fsync` a directory |
| `src/agy_mcp/worktree.py:560` | `O_NOFOLLOW` read of the agy permission config |

- **Linux:** works.
- **macOS:** works. `openat`, `O_NOFOLLOW`, `O_DIRECTORY` and `fchmod` exist;
  the `/proc/self/fd` component degrades as described above.
- **Native Windows:** needs change. `os.fchmod` does not exist, `dir_fd` is not
  supported for `os.open`, and `O_NOFOLLOW`/`O_DIRECTORY` are absent. These are
  the hardened agent-file paths, so a port needs reparse-point checks and
  Windows ACLs rather than a compatibility shim.
- **WSL:** works.

## 5. `0600`/`0700` file modes

The wrapper deliberately sets private modes: `src/agy_mcp/jobs.py:51,59,88,104,146,150,253,399`,
`src/agy_mcp/server.py:516-518`, `src/agy_mcp/worktree.py:308`,
`src/agy_mcp/scratchpad.py:159,344`, `src/agy_mcp/usage.py:675,833,865`,
`src/agy_mcp/watcher.py:278`.

- **Linux / macOS:** works; the state dir is `0700` and state files are `0600`.
- **Native Windows:** needs change. `os.chmod` only toggles the read-only
  attribute; group/other bits have no meaning. The "private state" guarantee
  needs an ACL design (`icacls` or `pywin32` security APIs) before it can be
  claimed on Windows.
- **WSL:** works.

## 6. Symlink and `.git` assumptions

- `src/agy_mcp/worktree.py:91-102` requires the repository's `.git` to be a
  directory and refuses linked worktrees and submodules as `repo_path`.
- `src/agy_mcp/worktree.py:303-306`, `src/agy_mcp/scratchpad.py:176,257-281`
  and `src/agy_mcp/watcher.py:52-58,93-100,434-544` refuse to follow a
  symlinked `.agy`, `COMMIT_MSG`, or scratch file.
- `src/agy_mcp/transcript.py:109-133` rejects symlinked components between the
  agy transcript and `<agy_home>/brain`.
- `src/agy_mcp/cleanup/jobs_cli.py:774-780` and
  `src/agy_mcp/cleanup/store_io.py:294-318` refuse symlinked store paths and
  symlinks during deletion.
- `src/agy_mcp/audit.py:393-397` reads the worktree `.git` pointer file, which
  is the normal layout for a git worktree.

- **Linux / macOS:** works. Unix symlinks and `lstat` behave as assumed.
- **Native Windows:** needs change. Creating symlinks needs Developer Mode or
  SeCreateSymbolicLinkPrivilege, junctions and case-insensitive paths change
  what "inside this directory" means, and `.git` pointer files are read
  differently on Windows. The refusals would still fail closed, but the tests
  that create symlinks would need platform guards.
- **WSL:** works.

## 7. Hard-coded POSIX paths

| Site | Value | Note |
|---|---|---|
| `src/agy_mcp/gitops.py:276` | `Path("/tmp")` | fallback tmp dir when none is supplied |
| `src/agy_mcp/util.py:71` | `"/usr/local/bin:/usr/bin:/bin"` | PATH default, `:` separator |
| `src/agy_mcp/util.py:75` | `"/dev/null"` | git `core.hooksPath` override |
| `src/agy_mcp/config.py:83` | `"~/.gemini/antigravity-cli"` | agy home default |
| `src/agy_mcp/jobs.py:29-33` | `XDG_STATE_HOME` else `~/.local/state` | state root |
| `src/agy_mcp/config.py:316-317` | `XDG_CONFIG_HOME` else `~/.config` | config root |
| `installer/install.sh:219` | `"$HOME/.local/bin/agy"` | agy fallback lookup |
| `installer/install.sh:655` | `/run/systemd/system` | systemd-user restart guard |
| `scripts/watch_orphans.py:28` | `"~/agy-mcp-venv/bin/pytest"` | default `PYTEST_BIN` |

- **Linux:** works.
- **macOS:** works. `/tmp`, `/dev/null` and the XDG fallbacks are valid; the
  systemd guard is simply false and the installer prints the "restart dsh
  yourself" note.
- **Native Windows:** needs change. `/tmp` and `/dev/null` are not valid, PATH
  uses `;`, XDG variables are non-standard, and the venv layout is
  `Scripts/python.exe` rather than `bin/python`.
- **WSL:** works.

## 8. The bash installer

`installer/install.sh` is bash, not POSIX sh, and the generated `run.sh` is
bash too. The bash-isms and external commands it relies on:

| Site | Construct |
|---|---|
| `installer/install.sh:1` | `#!/usr/bin/env bash` |
| `installer/install.sh:45,1021` | `${BASH_SOURCE[0]}` |
| `installer/install.sh:70-104,123-137,207-286,333-397,555-1014` | `[[ ... ]]` tests |
| `installer/install.sh:352-353,365` | `local -a`, `read -r -a` (arrays) |
| `installer/install.sh:108-111` | `compgen -G` |
| `installer/install.sh:328-336` | generated `#!/usr/bin/env bash` `run.sh`, `source`, `bin/python`, `bin/agy-mcp` |
| `installer/install.sh:608,714` | `chmod --reference` (GNU-only, with a `chmod 644` fallback) |
| `installer/install.sh:610,631,640` | `cp --`, `cp -p --` |
| `installer/install.sh:179,586,633,642` | `mktemp` with an X-suffix template |
| `installer/install.sh:655-657` | `systemctl --user` (guarded by `/run/systemd/system`) |

I also checked for **GNU-only commands that are absent everywhere in this
tree**: `mapfile`, `readlink -f`, `sed -i`, `stat -c`, `date -d`, `xargs -d`,
`sort -V`, `getopt -o`, `install -d` returned no matches. `sed` and `awk` are
used only with portable expressions.

- **Linux:** works; this is the only path exercised by the tests.
- **macOS:** needs one change. bash 3.2 is enough for `[[ ]]`, arrays,
  `read -a` and `compgen -G`, and the systemd guard is skipped, so install
  should work. `chmod --reference` does not exist on macOS; the fallback sets
  the rewritten dsh file to `644` instead of preserving its mode. Keep the
  fallback, but copy `stat` permissions or write a temp file with the original
  mode. Untested.
- **Native Windows:** blocked. There is no bash, `bin/`/`lib/python*` venv
  globs do not match, and the generated `run.sh` is a POSIX script. This needs
  a separate PowerShell/cmd installer.
- **WSL:** works (same Linux path).

## 9. Developer scripts

- `scripts/check_docs.py`, `check_moved_code.py`, `check_no_behavior_change.py`,
  `check_publishable.py`, `gen_codemap_index.py`, `gen_reference.py` are pure
  standard library plus `git`; the code uses `pathlib` and `subprocess`.
  **macOS:** works. **Native Windows:** mostly works when invoked as
  `python scripts/...`, but their help text and the gates document use
  `.venv/bin/python`. Not tested.
- `scripts/watch_orphans.py:12` shells out to `pgrep -af`, which is
  Linux/procps syntax (macOS `pgrep` has no `-a`), and
  `scripts/watch_orphans.py:72` calls `os.kill(pid, 9)`. **macOS:** needs
  change. **Native Windows:** blocked. This is a developer-only tool.

## 10. The fake `agy` test double

`tests/fake_agy/agy` is a 1,300-line Python executable:

| Site | Dependency |
|---|---|
| `tests/fake_agy/agy:1` | `#!/usr/bin/env python3` |
| `tests/fake_agy/agy:19,1106` | `signal.signal(SIGTERM, ...)` |
| `tests/fake_agy/agy:1122` | `os.kill(os.getpid(), SIGABRT)` |
| `tests/fake_agy/agy:1265` | `select.select([sys.stdin], ...)` on a pipe |

- **Linux / macOS:** works. `select` on pipe file descriptors and POSIX
  signals are supported.
- **Native Windows:** blocked. `select` only accepts sockets on Windows, so
  the stdin loop cannot work as written.
- **WSL:** works.

## 11. Test-suite assumptions

- `os.geteuid` guards root-only permission tests:
  `tests/test_cleanup_jobs_fixes.py:386,611`,
  `tests/test_cleanup_jobs_fixes2.py:265,539`,
  `tests/test_cleanup_jobs_fixes3.py:313,627,645,663`,
  `tests/test_cleanup_jobs_fixes5.py:405`,
  `tests/test_cleaner_fixes.py:162,389,462`,
  `tests/test_cleanup_jobs.py:842`,
  `tests/cleanup/test_git_oracle_differential.py:999,1167,1218`.
  **Native Windows:** `os.geteuid` does not exist; the `skipif` expression is
  evaluated at import and aborts collection.
- `/proc` readers listed in section 3 fail on macOS and Windows.
- `tests/test_cleanup_jobs_fixes9.py:529,651` uses `os.fork`; POSIX-only.
- `tests/test_installer_chapter.py:542` runs the installer through `bash`, and
  `:137-144` stubs `systemctl`; `:715,730` use `PATH=/usr/bin:/bin`.
- `tests/conftest.py:160-286` installs the `dir_fd` guard, which resolves
  through `/proc/self/fd` and is therefore inert on macOS and unsupported on
  Windows.
- `tests/test_cleanup_modules_structural_guard.py:228` pins the
  `/proc/sys/kernel/pid_max` fallback text.

- **Linux:** works; this is the only platform CI runs
  (`.github/workflows/ci.yml`, `ubuntu-latest`).
- **macOS:** needs change: add skips or a `ps`-based liveness helper, and fix
  the `geteuid`-free skip markers.
- **Native Windows:** blocked (`os.geteuid`, `os.fork`, `bash`, `/proc`).
- **WSL:** works.

## Effort estimate

| Target | Effort | What it covers |
|---|---|---|
| Document-only "untested" claim (current) | done | README/classifier say Linux tested, macOS and WSL untested |
| Make the pytest suite pass on macOS | 2–4 engineer-days | replace `/proc` liveness with `psutil`/`ps`, guard the `/proc` tests, add a macOS CI job, verify the installer's mode fallback |
| Run CI on macOS | 0.5–1 engineer-day | one `macos-latest` job after the suite is portable |
| Verify Windows via WSL | 0.5–1 engineer-day | one WSL job; no source change expected |
| Native Windows support | 3–5 engineer-weeks | replace `fcntl`, process groups and `/proc`; `dir_fd`/`O_NOFOLLOW`/`fchmod` with Windows APIs; ACL-based permissions; PowerShell installer; port the test double and suite |

## Recommended support statement

> **Supported: Linux (tested).** macOS is untested: the installer and job
> launch are expected to work, but restart recovery and the hardened file
> checks are not validated. Windows is supported only through WSL (untested),
> which follows the same POSIX path. Native Windows is not supported. CI runs
> on Linux only.

Do not add a `macos` or `win32` platform classifier until the corresponding CI
job exists. The `pyproject.toml` classifier therefore names only
`Operating System :: POSIX :: Linux`.

## Sources

- `src/agy_mcp/jobs.py` — `fcntl` locks, state modes and atomic writes.
- `src/agy_mcp/usage.py` — `fcntl` usage lock and `/usage` process-group kill.
- `src/agy_mcp/runner.py` — `start_new_session`, `getpgid`/`killpg` and `/proc` start-time reads.
- `src/agy_mcp/verify.py` — verify subprocess process group and timeout kill.
- `src/agy_mcp/supervisor.py` — SIGTERM/SIGKILL supervision.
- `src/agy_mcp/manager.py` — cancellation kill.
- `src/agy_mcp/recovery.py` — `/proc`-only restart adoption.
- `src/agy_mcp/scratchpad.py` — `dir_fd`, `O_NOFOLLOW`, `fchmod` and `/proc/self/fd`.
- `src/agy_mcp/watcher.py` — `O_NOFOLLOW`/`O_DIRECTORY` message handling.
- `src/agy_mcp/worktree.py` — `.git`/`.agy` symlink refusals and `O_NOFOLLOW` reads.
- `src/agy_mcp/transcript.py` — symlink-checked transcript read.
- `src/agy_mcp/audit.py` — worktree `.git` pointer and host-path snapshots.
- `src/agy_mcp/config.py` — XDG config and agy-home defaults.
- `src/agy_mcp/gitops.py` — `/tmp` fallback.
- `src/agy_mcp/util.py` — PATH and `/dev/null` defaults.
- `src/agy_mcp/server.py` — state-dir mode and the import chain into `jobs.py`.
- `src/agy_mcp/cleanup/store_io.py` — `pid_max`, `killpg` and symlink-safe deletion.
- `src/agy_mcp/cleanup/jobs_cli.py` — store symlink refusals.
- `installer/install.sh` — bash, GNU `chmod --reference`, systemd guard and venv layout.
- `tests/fake_agy/agy` — `select` on stdin, signals and `SIGABRT`.
- `tests/conftest.py` — `/proc/self/fd` guard and temp HOME isolation.
- `tests/test_installer_chapter.py` — bash invocation and systemctl stub.
- `tests/test_cleanup_jobs_fixes9.py` — `os.fork` and process-group checks.
- `tests/test_runner.py` — `/proc` and SIGKILL assertions.
- `scripts/watch_orphans.py` — `pgrep -af` and `os.kill`.
- `.github/workflows/ci.yml` — `ubuntu-latest` is the only runner.
- `pyproject.toml` — the Linux-only classifier and Python floor.
