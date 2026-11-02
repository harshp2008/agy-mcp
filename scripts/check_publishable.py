#!/usr/bin/env python3
"""Publish-hygiene gate: fail when tracked text files contain data unsafe to publish.

The gate scans every text file returned by ``git ls-files`` and reports:

* an email address whose domain is not allowlisted;
* an absolute ``/home/<name>`` or ``/Users/<name>`` path whose name is not a
  documented placeholder;
* a hostname under ``.ts.net``;
* a credential-shaped string (Google API key, OpenAI-style key, GitHub token,
  AWS access key id, or a PEM private-key block).

Repository-specific exceptions live in ``scripts/publishable-allowlist.txt`` so
this source file contains no personal literal: it names only the public
placeholder domains and names below. Findings never echo a full email address
or a credential.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ALLOWED_EMAIL_DOMAINS = frozenset(
    {
        "example.com",
        "example.org",
        "b.com",
        "test.local",
        "local.invalid",
        "users.noreply.github.com",
    }
)
ALLOWED_HOME_NAMES = frozenset({"user", "repo", "other", "smoke-user", "harmful"})
ALLOWLIST_PATH = Path("scripts") / "publishable-allowlist.txt"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Require a plausible username start (a dotfile such as /home/.config is a
# truncated path, not a home directory name).
_HOME_RE = re.compile(r"/(?:home|Users)/([A-Za-z0-9_][A-Za-z0-9_.\-]*)")
_TS_NET_RE = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9.\-]*\.ts\.net\b", re.IGNORECASE)
_CREDENTIAL_RES = (
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("openai-style-key", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b")),
    ("github-token", re.compile(r"\bghp_[A-Za-z0-9]{30,}\b")),
    ("aws-access-key-id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "private-key-block",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]{0,4096}?"
            r"-----END [A-Z0-9 ]*PRIVATE KEY-----"
        ),
    ),
)


class AllowlistError(Exception):
    """The allowlist file is malformed or an entry has no justification."""


@dataclass(frozen=True)
class Finding:
    """One publish-hygiene problem at a path and line."""

    path: str
    line: int
    category: str
    detail: str

    def format(self) -> str:
        """Return the stable single-line report form."""
        return f"{self.path}:{self.line}: {self.category}: {self.detail}"


def load_allowlist(path: Path) -> tuple[set[str], set[str]]:
    """Return (credential-exempt paths, extra email domains) from the allowlist.

    Each non-comment line is ``<kind>:<value>  # <justification>`` where kind is
    ``credential`` (a repo-relative path whose credential-shaped strings are
    fake test data) or ``email-domain`` (an extra accepted domain, with suffix
    matching). An entry without a ``#`` justification is an error: the file is
    an audit trail, not a silent bypass.
    """
    credential_paths: set[str] = set()
    email_domains: set[str] = set()
    if not path.is_file():
        return credential_paths, email_domains
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        body, _, justification = line.partition("#")
        body = body.strip()
        if not justification.strip():
            raise AllowlistError(f"{path}:{number}: entry needs a '# justification'")
        kind, separator, value = body.partition(":")
        value = value.strip()
        if not separator or not value:
            raise AllowlistError(f"{path}:{number}: expected '<kind>:<value>  # reason'")
        if kind == "credential":
            credential_paths.add(value)
        elif kind == "email-domain":
            email_domains.add(value.lower())
        else:
            raise AllowlistError(f"{path}:{number}: unknown allowlist kind {kind!r}")
    return credential_paths, email_domains


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _domain_allowed(domain: str, extra_domains: set[str]) -> bool:
    for allowed in ALLOWED_EMAIL_DOMAINS | extra_domains:
        if domain == allowed or domain.endswith("." + allowed):
            return True
    return False


def scan_text(
    relative_path: str,
    text: str,
    extra_email_domains: set[str],
    credential_exempt_paths: set[str],
) -> list[Finding]:
    """Return every finding in one text file."""
    findings: list[Finding] = []

    for match in _EMAIL_RE.finditer(text):
        domain = match.group(0).rsplit("@", 1)[1].lower()
        if not _domain_allowed(domain, extra_email_domains):
            findings.append(
                Finding(
                    relative_path,
                    _line_of(text, match.start()),
                    "email",
                    f"domain {domain!r} is not allowlisted",
                )
            )

    for match in _HOME_RE.finditer(text):
        name = match.group(1).rstrip(".-")
        if name not in ALLOWED_HOME_NAMES:
            findings.append(
                Finding(
                    relative_path,
                    _line_of(text, match.start()),
                    "home-path",
                    f"absolute home path {match.group(0)!r} is not an allowlisted placeholder",
                )
            )

    for match in _TS_NET_RE.finditer(text):
        findings.append(
            Finding(
                relative_path,
                _line_of(text, match.start()),
                "tailscale-hostname",
                f"hostname {match.group(0)!r} is under .ts.net",
            )
        )

    if relative_path not in credential_exempt_paths:
        for name, pattern in _CREDENTIAL_RES:
            for match in pattern.finditer(text):
                findings.append(
                    Finding(
                        relative_path,
                        _line_of(text, match.start()),
                        "credential",
                        f"string looks like a {name} (value not printed)",
                    )
                )

    return findings


def tracked_text_files(root: Path) -> list[tuple[str, str]]:
    """Return (repo-relative path, text) for each tracked non-binary file."""
    process = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        message = process.stderr.decode("utf-8", "replace").strip()
        raise SystemExit(f"check_publishable: git ls-files failed in {root}: {message}")
    paths = [entry for entry in process.stdout.decode("utf-8", "surrogateescape").split("\0") if entry]

    files: list[tuple[str, str]] = []
    for relative in paths:
        try:
            data = (root / relative).read_bytes()
        except OSError:
            continue
        if b"\0" in data:
            continue
        files.append((relative, data.decode("utf-8", "replace")))
    return files


def main(argv: list[str] | None = None) -> int:
    """Run the gate. Return 0 when clean, 1 on findings, 2 on configuration errors."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="repository root (default: current directory)")
    parser.add_argument(
        "--allowlist",
        default=None,
        help=f"allowlist file (default: <root>/{ALLOWLIST_PATH.as_posix()})",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    allowlist = (
        Path(args.allowlist).resolve() if args.allowlist else root / ALLOWLIST_PATH
    )
    try:
        credential_paths, extra_domains = load_allowlist(allowlist)
    except AllowlistError as error:
        print(f"check_publishable: {error}", file=sys.stderr)
        return 2

    findings: list[Finding] = []
    files = tracked_text_files(root)
    for relative, text in files:
        findings.extend(scan_text(relative, text, extra_domains, credential_paths))

    for finding in findings:
        print(finding.format())

    if findings:
        print(f"publish hygiene: FAIL ({len(findings)} finding(s) in {len(files)} text files)")
        return 1
    print(f"publish hygiene: OK ({len(files)} tracked text files checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
