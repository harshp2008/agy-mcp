#!/usr/bin/env python3
"""Check the ``doc/`` tree: links, Sources, coverage and generated tables.

The default mode is a gate: it prints every finding and exits 1. ``--report``
prints the same findings and exits 0, which is useful while a migration is in
progress.

Checks:

1. every relative markdown link resolves from the page that contains it;
2. every non-index page under ``doc/`` has a ``## Sources`` section;
3. every repository path in a Sources section exists;
4. every error code literal in ``src/agy_mcp/`` appears in
   ``doc/reference/errors.md``;
5. every environment variable and config key read from the code appears in its
   reference page;
6. every MCP tool, ``job.json`` field and audit event appears in its reference
   page, and every generated block is fresh.

Only the standard library is used, so the check runs before the project is
installed (it imports the sibling ``gen_reference`` renderer directly).

Usage::

    .venv/bin/python scripts/check_docs.py            # gate: exit 1 on findings
    .venv/bin/python scripts/check_docs.py --report    # report: always exit 0
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import gen_reference  # noqa: E402

REFERENCE = ROOT / "doc" / "reference"
DOC = ROOT / "doc"

# Hidden directories, the virtualenv, caches and the raw evidence store are not
# documentation sources; docs/reports/ is 4 MB of logs and is excluded until its
# archive decision.
_SKIP_PARTS = {
    ".git",
    ".venv",
    ".pytest-basetemp",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "reports",
}
_ROOT_FILES = {
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "CHANGELOG.md",
    "pyproject.toml",
}

_LINK_RE = re.compile(
    r"!?\[[^\]]*\]\(\s*(<[^>]*>|[^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\s*\)"
)
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_SOURCES_HEADING_RE = re.compile(r"^##\s+Sources\s*$")
_H2_RE = re.compile(r"^##\s+")
_LINE_SUFFIX_RE = re.compile(r":\d+(?:-\d+)?$")
_PATH_PREFIXES = (
    "src/",
    "tests/",
    "scripts/",
    "doc/",
    "docs/",
    "installer/",
    ".github/",
)
_HISTORICAL_SOURCES = {
    f"docs/{name}.md"
    for name in ("QUESTIONS", "CHANGELOG_SPEC", "SCAFFOLD_NOTES")
}
# Raw reviewer findings committed under evidence/ preserve reviewer text verbatim
# and do not carry documentation Sources footers.
_SOURCES_EXEMPT_PAGES = {
    "doc/audits/evidence/2026-11-cleanup-jobs/opus/FINDINGS.md",
    "doc/audits/evidence/2026-11-cleanup-jobs/gemini2/FINDINGS.md",
    "doc/audits/evidence/2026-11-cleanup-jobs/gemini3/FINDINGS.md",
    "doc/audits/evidence/2026-11-cleanup-jobs/gemini4/FINDINGS.md",
}


def markdown_files() -> list[Path]:
    """Return every tracked-style markdown file worth checking, sorted."""
    files: list[Path] = []
    for path in sorted(ROOT.rglob("*.md")):
        relative = path.relative_to(ROOT)
        if any(part in _SKIP_PARTS for part in relative.parts):
            continue
        if any(part.startswith(".") for part in relative.parts[:-1]):
            continue
        files.append(path)
    return files


def doc_pages(files: list[Path] | None = None) -> list[Path]:
    """Return the non-index pages under ``doc/`` (README indexes are exempt)."""
    pages = files if files is not None else markdown_files()
    return [
        page
        for page in pages
        if DOC in page.parents and page.name != "README.md"
    ]


def _line_number(text: str, offset: int) -> int:
    return text[:offset].count("\n") + 1


def _relative(page: Path) -> Path:
    """Return a page path relative to the repo root, or absolute outside it."""
    try:
        return page.relative_to(ROOT)
    except ValueError:
        return page


def broken_links(files: list[Path] | None = None) -> list[str]:
    """Return one finding per relative link whose target does not exist."""
    findings: list[str] = []
    for page in files if files is not None else markdown_files():
        text = page.read_text(encoding="utf-8", errors="replace")
        for match in _LINK_RE.finditer(text):
            target = match.group(1).strip()
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1].strip()
            if not target or target.startswith("#") or _SCHEME_RE.match(target):
                continue
            if target.startswith("/"):
                continue
            candidate = target.split("#", 1)[0].split("?", 1)[0]
            if not candidate:
                continue
            resolved = page.parent / candidate
            if not resolved.exists():
                findings.append(
                    f"broken link: {_relative(page)}:"
                    f"{_line_number(text, match.start())} -> {target}"
                )
    return findings


def _sources_section(text: str) -> str | None:
    """Return the body of the first real ``## Sources`` section, or None.

    Headings inside fenced code blocks are documentation examples (the page
    template shows a ``## Sources`` block), not sections, so the scan tracks
    fence state and ignores them.
    """
    lines = text.splitlines()
    in_fence = False
    start: int | None = None
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if start is None:
            if _SOURCES_HEADING_RE.match(line):
                start = index + 1
        elif _H2_RE.match(line):
            return "\n".join(lines[start:index])
    if start is not None:
        return "\n".join(lines[start:])
    return None


def pages_missing_sources(files: list[Path] | None = None) -> list[str]:
    """Return one finding per non-index ``doc/`` page without ``## Sources``."""
    findings: list[str] = []
    pages = doc_pages(files) if files is None else files
    for page in pages:
        if _relative(page).as_posix() in _SOURCES_EXEMPT_PAGES:
            continue
        text = page.read_text(encoding="utf-8", errors="replace")
        if _sources_section(text) is None:
            findings.append(f"missing Sources: {_relative(page)}")
    return findings


def _looks_like_repo_path(token: str) -> bool:
    if token in _ROOT_FILES:
        return True
    return token.startswith(_PATH_PREFIXES)


def bad_source_paths(files: list[Path] | None = None) -> list[str]:
    """Return one finding per Sources entry that is not an existing repo path."""
    findings: list[str] = []
    pages = doc_pages(files) if files is None else files
    for page in pages:
        text = page.read_text(encoding="utf-8", errors="replace")
        section = _sources_section(text)
        if section is None:
            continue
        tokens = re.findall(r"`([^`]+)`", section)
        path_tokens = 0
        for token in tokens:
            token = token.strip().rstrip("),;")
            if not token or " " in token or token.startswith("~"):
                continue
            if "://" in token:
                continue
            token = _LINE_SUFFIX_RE.sub("", token)
            if not _looks_like_repo_path(token):
                continue
            path_tokens += 1
            if token in _HISTORICAL_SOURCES:
                continue
            if not (ROOT / token).exists():
                findings.append(
                    f"missing source path: {_relative(page)} -> {token}"
                )
        if path_tokens == 0:
            findings.append(
                f"Sources has no repository path: {_relative(page)}"
            )
    return findings


def _missing_from(page: Path, names: list[str], label: str) -> list[str]:
    if not page.is_file():
        return [f"missing page: {_relative(page)}"]
    text = page.read_text(encoding="utf-8", errors="replace")
    return [
        f"{label} not documented: {name} ({_relative(page)})"
        for name in names
        if f"`{name}`" not in text
    ]


def missing_error_codes() -> list[str]:
    """Every stable error code must appear in the errors reference."""
    return _missing_from(
        REFERENCE / "errors.md",
        list(gen_reference.error_code_sources()),
        "error code",
    )


def missing_env_vars() -> list[str]:
    """Every production/installer variable must appear in the environment page."""
    return _missing_from(
        REFERENCE / "environment.md",
        list(gen_reference.env_var_sources()),
        "environment variable",
    )


def missing_config_keys() -> list[str]:
    """Every config key must appear in the config-keys page."""
    return _missing_from(
        REFERENCE / "config-keys.md",
        [row["name"] for row in gen_reference.config_fields()],
        "config key",
    )


def missing_tools() -> list[str]:
    """Every registered MCP tool must appear in the tools page."""
    return _missing_from(
        REFERENCE / "mcp-tools.md",
        [row["name"] for row in gen_reference.tool_rows()],
        "MCP tool",
    )


def missing_job_fields() -> list[str]:
    """Every persisted ``job.json`` field must appear in the state page."""
    return _missing_from(
        REFERENCE / "state-layout.md",
        [row["name"] for row in gen_reference.job_fields()],
        "job field",
    )


def missing_events() -> list[str]:
    """Every audit event must appear in the log-formats page."""
    return _missing_from(
        REFERENCE / "log-formats.md",
        list(gen_reference.audit_events()),
        "audit event",
    )


def collect_findings() -> list[str]:
    """Run every check and return a flat, sorted list of findings."""
    files = markdown_files()
    pages = doc_pages(files)
    findings: list[str] = []
    findings.extend(broken_links(files))
    findings.extend(pages_missing_sources(pages))
    findings.extend(bad_source_paths(pages))
    findings.extend(missing_error_codes())
    findings.extend(missing_env_vars())
    findings.extend(missing_config_keys())
    findings.extend(missing_tools())
    findings.extend(missing_job_fields())
    findings.extend(missing_events())
    findings.extend(gen_reference.check_all())
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--report",
        action="store_true",
        help="print findings and exit 0 (the migration/report mode)",
    )
    args = parser.parse_args(argv)

    findings = collect_findings()
    if findings:
        print(f"docs check: {len(findings)} finding(s)")
        for finding in findings:
            print(f"- {finding}")
        return 0 if args.report else 1

    count = len(markdown_files())
    print(f"docs check: OK ({count} markdown files checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
