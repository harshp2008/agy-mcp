# Verification and Auto-Fix Loop Review

This document provides a comprehensive analysis of the verification and auto-fix loop in `agy-mcp` based on the codebase implementation per SPEC sections 13, 14, and 18, and decisions D52 and D53.

---

## 1. Execution Sequence & Timing

- **Invocation Point:** `src/agy_mcp/supervisor.py:JobSupervisor._finalize_terminated_process` (lines 280–310).
- **Timing:** Verification runs **ONLY after** the `agy` process has completely terminated (`self.runner.poll(self.handle)` returns an exit code).
- **Pre-Conditions:**
  1. The parsed status must be `DONE` (`parsed.state == JobState.DONE`).
  2. The post-completion host security audit must be clean (`not post_findings.has_fatal`).
  3. A verification command must be configured on the job (`self.job.verify` is not None).
- **Frequency:** Verification does **NOT** run at each commit during an active turn; it runs strictly once at the end of each turn after `agy` finishes execution and before the safety-net commit.

---

## 2. Execution Environment: CWD, Environment, and PATH

- **Implementation:** `src/agy_mcp/verify.py:run_verify` (lines 55–134).
- **Working Directory (cwd):** `worktree_path.resolve()` — the job's dedicated git worktree (`<job_dir>/work`).
- **Environment (env):** Sanitized environment constructed by `src/agy_mcp/util.py:env_allowlist(job_tmp_dir=tmp_path, home=home)`:
  - `HOME`: User home directory (or isolated test `fake_home`).
  - `PATH`: Sanitized system PATH (`os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")`).
  - `LANG`: `C.UTF-8`.
  - `TMPDIR`: Dedicated scratch directory (`<job_tmp_dir>` or `agy_verify_tmp_*`).
  - Git configuration overrides: `GIT_CONFIG_COUNT=3`, `core.fsmonitor=false`, `core.hooksPath=/dev/null`, `core.pager=cat`.
  - Process isolation: `start_new_session=True` creates a distinct POSIX process group.

---

## 3. Command Resolution & PATH in MCP Environment

- **Direct Resolution:** When `verify` is `"pytest"`, `subprocess.Popen` looks up `pytest` using the sanitized `PATH` passed in `env`.
- **MCP Server Context (venv PATH):**
  - When the MCP server is launched by an orchestrator such as DeepSeek Host (`dsh`) according to `docs/DSH_SETUP.md`, the client passes standard host PATH:
    `"PATH": "/usr/local/bin:/usr/bin:/bin:/home/user/.local/bin"`
  - In this configuration, the virtual environment directory (`/path/to/venv/bin`) is not in standard system PATH. Invoking bare `"pytest"` fails with `FileNotFoundError: [Errno 2] No such file or directory: 'pytest'` (rc=127).
- **Safe Resolution via Interpreter Command (D53):**
  - The configuration allows naming an absolute interpreter-based verify command:
    `["/path/to/venv/bin/python", "-m", "pytest"]` (or string `"/path/to/venv/bin/python -m pytest"`).
  - This directly resolves the python interpreter binary and invokes pytest via `-m`, completely avoiding system PATH reliance.

---

## 4. Allowlist Validation (`is_verify_allowed`)

- **Implementation:** `src/agy_mcp/verify.py:is_verify_allowed` (lines 27–75).
- **Timing:** Checked synchronously at initial start in `src/agy_mcp/manager.py:JobManager.start` before worktree provisioning.
- **Matching Rules:**
  1. Exact prefix matching of parsed command tokens (`shlex.split`).
  2. Entries in `verify_allowlist` may be strings (`"pytest"`, `"/path/to/venv/bin/python -m pytest"`) or token lists (`["/path/to/venv/bin/python", "-m", "pytest"]`).
  3. Direct shell execution is strictly blocked: binaries named `sh`, `bash`, `zsh`, `csh`, `tcsh`, `dash`, or `ksh` are rejected immediately.
  4. Arbitrary code flags (e.g. `python -c ...`), non-allowlisted modules, or raw script executions without an allowlisted prefix are rejected.

---

## 5. Timeout & Output Cap

- **Timeout:** Enforced at 900 seconds (15 minutes) in `src/agy_mcp/supervisor.py:287` via `run_verify(..., timeout_seconds=900)`.
  - On `subprocess.TimeoutExpired`, `run_verify` sends `SIGTERM` to the process group (`os.killpg(proc.pid, signal.SIGTERM)`), followed by `SIGKILL` after 2.0s if needed.
  - Returns exit code `124`.
- **Output Cap:** Captured stdout and stderr are truncated using `src/agy_mcp/util.py:truncate_head(raw_output, MAX_VERIFY_TAIL_BYTES)`:
  - Capped at `8192` bytes (8 KB), preserving the trailing failure output.

---

## 6. Auto-Fix Resume Prompt Formatting

- **Implementation:** `src/agy_mcp/verify.py:format_fix_prompt` (lines 135–185).
- **Exact Text Template:**

```markdown
<standing_preamble>

Verification failed with the following output:
Failing tests: <test_names_if_parseable>

<untrusted_text id="<nonce>">
<first_2000_bytes_of_failure_tail>
</untrusted_text id="<nonce>">

Change code, not tests, unless the test itself is wrong (and then say so in the commit message).
Please analyze the error, fix the issues, and write a commit message to .agy/COMMIT_MSG.
```

- **Prompt Elements:**
  1. Standing preamble with updated Rule 6.
  2. Failing test names extracted via regex from test runner output (e.g. `FAILED tests/test_calc.py::test_mul`).
  3. First 2000 bytes of failure tail delimited inside `<untrusted_text>...</untrusted_text>` tags.
  4. Mandatory instruction: `"Change code, not tests, unless the test itself is wrong (and then say so in the commit message)."`

---

## 7. Auto-Fix Attempt Counter & State Transitions

- **Attempt Counter:** Maintained in `JobManager._auto_fix_counts: dict[str, int]`.
  - Maximum attempts configured by `config.auto_fix_attempts` (default: `2`).
  - Checked in `src/agy_mcp/manager.py:JobManager._run_supervisor`.
  - Incremented in `_handle_verification_auto_fix`.
- **State Outcomes:**
  - **Success (`rc == 0`):** Job transitions to `JobState.DONE`. Worktree commits dirty changes via safety-net commit.
  - **Failure (`rc != 0` and `attempts < auto_fix_attempts`):** Supervisor transitions job to `JobState.NEEDS_REVIEW` with `review_reason="verify_failed"`. Manager detects this in `_run_supervisor`, increments attempt counter, and resumes `agy` with `format_fix_prompt`, transitioning to `JobState.RUNNING`.
  - **Exhausted Attempts (`attempts >= auto_fix_attempts`):** Job remains in terminal `JobState.NEEDS_REVIEW` with `review_reason="verify_failed"`. The failure tail is saved to `verify_tail.txt` and attached to `agy_result.response` wrapped as `<untrusted_text>`.
  - **Verify Timeout (`rc == 124`):** Job transitions to `JobState.NEEDS_REVIEW` with `review_reason="verify_timeout"`. Auto-fix is not re-attempted for timeouts.
  - **Executable Not Found (`rc == 127`):** Returns clear error message `"Executable not found: ..."` in `verify_tail`, transitions cleanly to `needs_review(reason="verify_failed")` without crashing the supervisor or MCP server.

---

## 8. Unclear or Untested Areas

1. **Non-Python Test Runners:** Regex for extracting failing test names primarily targets `pytest`, `cargo test`, and standard `FAILED <test>` formats. Custom test runners with non-standard failure lines fall back to omitting the `Failing tests:` line while still including the untrusted-wrapped failure tail.
2. **Subprocess Concurrency:** Simultaneous verification runs across multiple parallel jobs share host CPU resources. Timeout remains 900s per job.
