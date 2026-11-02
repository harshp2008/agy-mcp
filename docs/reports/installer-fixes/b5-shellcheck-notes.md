# B5 shellcheck result

Shellcheck **is available**: `shellcheck-py 0.11.0` installed in a throwaway
venv at `/tmp/sc` (never the project venv). Raw output is in
`b5-shellcheck-run.txt`.

Findings: four SC2016 *info* messages (lines 306, 308, 310, 312), all on
`printf '...'` lines that emit the generated `run.sh` body. Those single-quoted
strings are intentional: the installer writes the literal text `$HERE`,
`$AGY_MCP_AGY_BINARY`, `$AGY_MCP_CONFIG` and `$AGY_MCP_STATE_DIR` into run.sh so
the *generated* script expands them. Nothing is expanded at install time, which
is exactly what the code wants.

No real findings, so no fix commit was made: silencing SC2016 with a disable
directive would only change comments, and changing the quoting would break the
generated run.sh. The script keeps exit code 1 for these info-level notes.
