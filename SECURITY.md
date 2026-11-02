# Security policy

**Status: pre-release.** `agy-mcp` is not an operating-system sandbox: the
`agy` agent it launches runs as your local user with your permissions. The
wrapper provides branch/worktree isolation, environment secret sanitization and
tamper detection, not containment. See the safety notes in
[README.md](README.md).

## Reporting a vulnerability

Report vulnerabilities **privately** through GitHub Security Advisories:

<https://github.com/harshp2008/agy-mcp/security/advisories/new>

Do not open a public issue for a suspected vulnerability, and do not include
secrets, tokens or personal data in a report. No email address is used for
security reports.

Please include:

- the affected version or commit;
- a minimal reproduction (a test using the fake `agy` at
  `tests/fake_agy/agy` is ideal — never run the real CLI against live data);
- the impact you believe it has;
- any suggested fix.

## In scope

- Escape or bypass of the wrapper's own controls: worktree and branch
  isolation, environment sanitization, host-path tamper detection, repository
  path validation, the security audit gates.
- State-file permissions, credential or secret leakage into logs, job records
  or envelopes.
- Command injection in commands the wrapper builds from job or repository data.

## Out of scope

- The absence of an OS sandbox, which is documented and planned for a later
  release.
- Vulnerabilities in the Google Antigravity CLI or the model behind it; report
  those to the vendor.
- Denial of service caused by an agent that is already running with your
  permissions, unless it bypasses a documented wrapper control.
