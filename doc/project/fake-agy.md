# Fake agy executable and simulation scenarios

Status: current
Audience: contributors, maintainers

`tests/fake_agy/agy` is a deterministic, offline mock of the Google Antigravity `agy` CLI binary. It enables complete end-to-end integration testing of the runner, watcher, supervisor, recovery, and tool loops without requiring network access, Gemini API credentials, or quota expenditure.

## CLI Signature Emulation
Matches the real `agy` CLI interface:
```bash
agy --mode accept-edits \
    --output-format json \
    --print-timeout <budget>s \
    --log-file <path> \
    --model <model> \
    [--conversation <id>] \
    -p <prompt>
```
Flags must appear before `-p <prompt>`; unknown flags, missing required flags, non-absolute `--log-file`, invalid `--print-timeout` format, empty prompts, or `--mode` other than `accept-edits` immediately exit with code 1. While the real `agy` CLI only issues a warning on unrecognized `--mode` values, the fake agy enforces `accept-edits` strictly on purpose to catch wrapper bugs early in test environments.

## Triggering Scenarios
Scenarios can be triggered via three mechanisms in order of precedence:
1. Environment variable `FAKE_AGY_SCENARIO=<name>`
2. File `.fake_agy_scenario` in cwd or `$TMPDIR`
3. Prompt marker `__SCENARIO:<name>__` or `SCENARIO: <name>`

Default scenario when unspecified: `success_simple`.

## Supported Simulation Scenarios

| Scenario | Trigger Method | Simulated stdout / stderr | Files Touched | Exit Code |
|---|---|---|---|---|
| `success_simple` | Default or `__SCENARIO:success_simple__` | Valid JSON response (`status="SUCCESS"`) | Appends to `--log-file` | 0 |
| `success_with_commits` | `__SCENARIO:success_with_commits__` | Valid JSON response with token stats | Creates `src_feature.py`, `.agy/COMMIT_MSG` | 0 |
| `needs_decision` | `__SCENARIO:needs_decision__` | Valid JSON with `NEEDS_DECISION:` line | Appends to `--log-file` | 0 |
| `denial_with_progress` | `__SCENARIO:denial_with_progress__` | JSON with `denied_actions`, stderr jetski notice | Creates `partial.txt` | 0 |
| `denial_blocked` | `__SCENARIO:denial_blocked__` | JSON with empty `response`, stderr jetski notice | None | 0 |
| `timeout` | `__SCENARIO:timeout__` | Stderr contains `[agy] print timeout after 120s` | Appends to `--log-file` | 0 |
| `hang` | `__SCENARIO:hang__` | Infinite sleep awaiting SIGTERM/SIGKILL | None | (Killed) |
| `crash_rc1` | `__SCENARIO:crash_rc1__` | Stderr fatal error traceback | None | 1 |
| `auth_error` | `__SCENARIO:auth_error__` | Stderr `authentication failed; please log in again` and matching cli.log line | None | 1 |
| `crash_signal` | `__SCENARIO:crash_signal__` | Process aborts via `SIGABRT` | None | <0 (-6) |
| `garbage_stdout` | `__SCENARIO:garbage_stdout__` | Non-JSON HTML/traceback on stdout | None | 0 |
| `huge_stdout` | `__SCENARIO:huge_stdout__` | Valid JSON containing 200 KB text | None | 0 |
| `malicious_hooks` | `__SCENARIO:malicious_hooks__` | JSON stdout; writes forbidden file | Writes `.agents/hooks.json` | 0 |
| `malicious_gitptr` | `__SCENARIO:malicious_gitptr__` | JSON stdout; tampers with `.git` | Rewrites `.git` worktree file | 0 |
| `malicious_hostwrite` | `__SCENARIO:malicious_hostwrite__` | JSON stdout; attempts write to `$HOME` | Touches `$HOME/.bashrc` | 0 |
| `slow_progress` | `__SCENARIO:slow_progress__` | JSON stdout after timed delays | Writes `slow_stage1.txt`, `.agy/COMMIT_MSG` | 0 |
| `resume_echo` | `__SCENARIO:resume_echo__` | JSON stdout reflecting `--conversation <id>` | None | 0 |
| `env_probe` | `__SCENARIO:env_probe__` | JSON stdout; dumps `os.environ` | Writes `env_dump.json` | 0 |
| `stdin_probe` | `__SCENARIO:stdin_probe__` | JSON stdout; checks non-blocking stdin | Writes `stdin_probe.txt` | 0 |

## `/usage` Mode (Item 2, D59)

`agy --log-file <abs>/usage.log --output-format json -p "/usage"` is handled without the
job-mode flags. The scenario is selected from `FAKE_AGY_SCENARIO` first, then from
`$HOME/.fake_agy_usage_scenario`, defaulting to `usage_real`. A synthetic
`applyAuthResult: email=..., authMethod=consumer` line is appended to the log so the
reader exercises email masking. `/usage` invocations append a JSON object
`{"argv": [...], "cwd": "..."}` to a `FAKE_AGY_RECORD.usage` sidecar so job argv
recordings remain a bare argv list per line.

| Usage scenario | Output | Exit code |
|---|---|---|
| `usage_real` | Committed `tests/fixtures/real_usage.json` payload | 0 |
| `usage_zero` | Fixture with 0% remaining and 0.0 fractions | 0 |
| `usage_low` | Fixture with 5% remaining and 0.05 fractions | 0 |
| `usage_garbled` | JSON with an unusable `response` and no groups | 0 |
| `usage_empty` | Empty stdout (still logs the auth line) | 0 |
| `usage_auth_required` | Stderr `not logged in to agy` | 1 |
| `usage_hang` | Sleeps forever (for timeout/process-group tests) | (killed) |

## Child-env probe and keyring simulation

Touch `$HOME/.fake_agy_env_probe` to make every spawn record the environment it
was launched with. `env-probe.json` keeps the last entry (`HOME`,
`DBUS_SESSION_BUS_ADDRESS`, `argv`, `cwd`) and `env-probe.jsonl` appends one
entry per spawn, so a test can separate the `/usage` probes from the job launch
without sleeping.

`.fake_agy_keyring_email` simulates the desktop SecretService keyring: it is
honoured only when `DBUS_SESSION_BUS_ADDRESS` is **not**
`unix:path=/dev/null`. A probe that forgets the dead address therefore reports
the keyring email, which reproduces the live leak (human test 2026-11-03) and is
caught as `account_identity_mismatch`. `.fake_agy_email` remains the account's
own address.

## Sources

- `tests/fake_agy/agy` — fake agy binary mock implementation.
- `tests/conftest.py` — pytest fixtures configuring and isolating fake agy.
- `tests/fixtures/real_usage.json` — mock usage payload fixture.
