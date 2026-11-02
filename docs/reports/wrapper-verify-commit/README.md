# Evidence: wrapper verify timeout and commit-subject fixes (D80)

Environment: 2026-10-29, repo `agy-mcp`, branch `fix/wrapper-verify-commit`.
Commands were run from the repository root with `env -u PYTHONPATH .venv/bin/python -m pytest`.

## Files

| File | What it is |
|---|---|
| `full-run-1.txt` .. `full-run-5.txt` | Five sequential full-suite runs. Each is `674 passed` (exit 0). |
| `regression-tests.txt` | `tests/test_wrapper_verify_commit_fix.py` (14 tests): sanitizer/validator, checkpoint and safety-net subjects, a real commit-msg hook (rejects raw, accepts sanitized), hook-rejection error text, process-group kill with a live grandchild, timeout-vs-failure status, pytest import-mismatch excerpt, config validation, per-job timeout, old envelope keys. |
| `smoke.txt` | Throwaway temp-repo smoke with the stub agy (`tests/fake_agy/agy`): one quick job (done, verify passed) and one hanging verify (`needs_review/verify_timeout`, process group killed, grandchild gone). No real agy and no clipsync-lab worktree were used. |

## Notes

- The full-suite runs overlapped an unrelated clipsync-lab gate (another session) for part of the time, so wall times vary (230-250 s). No test failure was left unexplained: the one transient failure observed before the five green runs was the pre-existing fixed-`sleep(0.5)` in `test_server_e2e_sigterm_does_not_kill_running_agy`; it was replaced with a bounded poll (test-only change) and repeated three times green.
- `tests/test_f17.py` was updated because `verify_tail.txt` is now the annotated D80 diagnostic block rather than the raw tail byte-for-byte; it still asserts the raw failing line is present and that the audit copy is redacted.
- `tests/fixtures/tools_schema.json` was regenerated for the new optional `verify_timeout_seconds` argument of `agy_start`.
