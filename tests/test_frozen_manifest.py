"""Verify the frozen cleanup test files against ``tests/frozen.sha256``.

The cleanup_jobs round files and the differential harness are *frozen*: their
content is the regression evidence for the destructive cleaner, so it must not
change. The freeze used to live only in their docstrings (F6); this manifest
makes it machine-checkable, so an accidental edit fails here loudly instead of
silently weakening 887 cases.

The manifest covers the files matched by the frozen glob (so deleting an entry
also fails) plus ``tests/test_cleanup_fixtures.py``. Update it only when Harsh
explicitly unfreezes a file -- never to make this test green.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "tests" / "frozen.sha256"
FROZEN_GLOB = "test_cleanup_jobs*.py"
FROZEN_EXTRA = ("test_cleanup_fixtures.py",)
FROZEN_ORACLE = ("cleanup/test_git_oracle_differential.py",)
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def _expected_paths() -> set[str]:
    """Return every repo-relative path that the manifest must cover."""
    tests_dir = REPO_ROOT / "tests"
    names = [p.relative_to(tests_dir).as_posix() for p in tests_dir.rglob(FROZEN_GLOB)]
    names += list(FROZEN_EXTRA) + list(FROZEN_ORACLE)
    return {f"tests/{name}" for name in names}


def _parse_manifest() -> dict[str, str]:
    """Return ``{repo-relative path: sha256}`` parsed from the manifest."""
    text = MANIFEST_PATH.read_text(encoding="utf-8")
    entries: dict[str, str] = {}
    problems: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        # sha256sum format is exactly "<64 hex><two spaces><path>".
        parts = raw.split("  ")
        if len(parts) != 2 or not _DIGEST_RE.match(parts[0]) or not parts[1]:
            problems.append(f"line {lineno}: expected '<sha256>  <path>', got {raw!r}")
            continue
        digest, rel = parts
        if rel in entries:
            problems.append(f"line {lineno}: duplicate entry for {rel}")
            continue
        entries[rel] = digest
    assert not problems, (
        "tests/frozen.sha256 is malformed:\n  " + "\n  ".join(problems)
    )
    return entries


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_manifest_covers_exactly_the_frozen_files() -> None:
    """The manifest and the frozen file set must agree in both directions."""
    entries = _parse_manifest()
    expected = _expected_paths()
    missing = sorted(expected - set(entries))
    unexpected = sorted(set(entries) - expected)
    problems = []
    if missing:
        problems.append("frozen files missing from tests/frozen.sha256: " + ", ".join(missing))
    if unexpected:
        problems.append(
            "tests/frozen.sha256 lists files that are not frozen: " + ", ".join(unexpected)
        )
    assert not problems, (
        "\n".join(problems)
        + "\nIf a file was intentionally frozen/unfrozen, update tests/frozen.sha256 "
        "in the same commit and record the decision."
    )


def test_every_frozen_file_matches_its_manifest_hash() -> None:
    """Every frozen file on disk must still have the digest committed in the manifest."""
    entries = _parse_manifest()
    mismatches: list[str] = []
    for rel in sorted(entries):
        path = REPO_ROOT / rel
        if not path.is_file():
            mismatches.append(f"{rel}: missing (manifest expects {entries[rel]})")
            continue
        actual = _sha256(path)
        if actual != entries[rel]:
            mismatches.append(f"{rel}:\n    expected {entries[rel]}\n    actual   {actual}")
    assert not mismatches, (
        "Frozen test files changed; they must not be edited.\n"
        "Revert the edit (or ask Harsh to unfreeze the file) -- do not update the "
        "manifest to match your edit.\n  " + "\n  ".join(mismatches)
    )
