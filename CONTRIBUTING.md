# Contributing to agy-mcp

**Status: pre-release.** The project is developed on Linux; macOS and Windows
are untested.

Read these first:

- [doc/developing/writing-docs.md](doc/developing/writing-docs.md) — page
  template, `## Sources` contract, naming and routing rules.
- [doc/developing/gates.md](doc/developing/gates.md) — every gate command
  (pytest, ruff, documentation checks, frozen-test manifest, AST baseline) and
  which ones a change must run.
- [doc/project/code-layout.md](doc/project/code-layout.md) — module map and job
  flow.
- [doc/design/spec.md](doc/design/spec.md) — the behavior contract.

## Development setup

Python 3.12 or newer and `git` are required. From a checkout:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest -q
```

The test suite is hermetic: it uses the fake `agy` at `tests/fake_agy/agy` and
points `HOME`, `XDG_STATE_HOME` and `AGY_MCP_STATE_DIR` at temporary
directories. Never run the real `agy` CLI as part of a test, and never read or
write `~/.gemini` or `~/.ssh` while investigating.

## Change rules

- One logical change per commit; do not amend or rewrite published history.
- Add or update a test with a behavior change. Do not edit a test frozen by
  `tests/frozen.sha256` to make a failure go away.
- Documentation-only commits must not change behavior; use the AST baseline
  gate listed in `doc/developing/gates.md`.
- A design decision gets a `D<n>` entry in `doc/design/decisions.md`.
- Source changes update `doc/project/code-layout.md`
  (`scripts/gen_codemap_index.py --write`) and the generated reference blocks
  when a tool, error code, environment variable or config key changes.
- New findings and audits are pages under `doc/`, not comments or commit
  messages; see the routing table in `doc/developing/writing-docs.md`.

## Security and privacy

Do not put secrets, account emails, or personal absolute paths into commits,
fixtures, issues or logs. Report vulnerabilities privately as described in
[SECURITY.md](SECURITY.md); `scripts/check_publishable.py` enforces the
publish-hygiene rules in CI.

## License

Contributions are accepted under the Apache License 2.0; see
[LICENSE](LICENSE).
