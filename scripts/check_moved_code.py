#!/usr/bin/env python3
"""Prove the wave-2a cleaner split is a pure move, not a rewrite.

The old ``cleanup_jobs.py`` and ``cleanup_fixtures.py`` source text is saved in
``tests/fixtures/old_cleanup_*.py.txt`` (captured from ``main`` before the
split); ``--from-git`` reads the same files from the current ``main`` ref
instead. Every top-level function, class and constant is compared by
``ast.dump`` (docstrings included, line numbers excluded) against the
definitions in ``src/agy_mcp/cleanup/*.py`` and against the two re-export
shims. Both sides are dumped at run time with the running interpreter, so the
gate is independent of the CPython version that produced any saved fixture.

A symbol is *moved* when exactly one new module has an identical dump, and
*kept* when the shim still defines it verbatim. Anything else is *missing*
(no definition anywhere) or *changed* (same name, different dump). The split
passes only when ``missing``, ``changed`` and ``ambiguous`` are all zero.

Usage::

    .venv/bin/python scripts/check_moved_code.py
    .venv/bin/python scripts/check_moved_code.py --from-git
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NEW_DIR = ROOT / "src" / "agy_mcp" / "cleanup"
OLD_PATHS = (
    "src/agy_mcp/cleanup_jobs.py",
    "src/agy_mcp/cleanup_fixtures.py",
)
SHIM_PATHS = OLD_PATHS
OLD_SOURCE_FIXTURES = {
    "src/agy_mcp/cleanup_jobs.py": ROOT / "tests" / "fixtures" / "old_cleanup_jobs.py.txt",
    "src/agy_mcp/cleanup_fixtures.py": (
        ROOT / "tests" / "fixtures" / "old_cleanup_fixtures.py.txt"
    ),
}


@dataclass(frozen=True)
class ExpectedEdit:
    """An intentional post-move edit to a moved cleaner symbol."""

    symbol: str
    old_snippet: str
    new_snippet: str
    reason: str


EXPECTED_EDITS: tuple[ExpectedEdit, ...] = (
    ExpectedEdit(
        symbol="_check_worktree_status",
        old_snippet="entries = list(wt_root.iterdir())",
        new_snippet="entries = sorted(wt_root.iterdir(), key=lambda p: p.name)",
        reason=(
            "Deterministic iteration order for git worktree registrations "
            "(AGY-fix-deterministic-worktree-order)"
        ),
    ),
)


def symbols(source: str) -> dict[str, str]:
    """Return ``name -> ast.dump`` for top-level defs, classes and constants.

    ``ast.dump`` excludes line numbers by default while keeping docstrings, so
    an identical dump means an identical executable body.
    """
    out: dict[str, str] = {}
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[node.name] = ast.dump(node)
        elif isinstance(node, ast.Assign):
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                out[node.targets[0].id] = ast.dump(node)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out[node.target.id] = ast.dump(node)
    return out


def old_symbols(from_git: bool = False) -> tuple[dict[str, dict[str, str]], int]:
    """Load and parse the old modules at run time after applying expected edits.

    By default the saved source text in ``tests/fixtures`` is used, so the gate
    needs no git history and no stored ``ast.dump`` output.  ``from_git`` reads
    the two files from the ``main`` ref instead.
    """
    raw_sources: dict[str, str] = {}
    for path in OLD_PATHS:
        if from_git:
            source = subprocess.check_output(
                ["git", "show", f"main:{path}"], text=True, cwd=ROOT
            )
        else:
            source = OLD_SOURCE_FIXTURES[path].read_text(encoding="utf-8")
        raw_sources[path] = source

    applied_count = 0
    for edit in EXPECTED_EDITS:
        matching_paths = [
            path for path, text in raw_sources.items() if edit.old_snippet in text
        ]
        total_occurrences = sum(
            text.count(edit.old_snippet) for text in raw_sources.values()
        )
        if total_occurrences != 1 or len(matching_paths) != 1:
            raise ValueError(
                f"Expected edit for {edit.symbol!r} old_snippet must occur exactly once across "
                f"old sources, found {total_occurrences} occurrences across {len(matching_paths)} files"
            )
        target_path = matching_paths[0]
        raw_sources[target_path] = raw_sources[target_path].replace(
            edit.old_snippet, edit.new_snippet, 1
        )
        applied_count += 1

    out: dict[str, dict[str, str]] = {}
    for path, source in raw_sources.items():
        out[path] = symbols(source)
    return out, applied_count


def new_symbols() -> dict[str, dict[str, str]]:
    """Return ``module -> symbols`` for every module of the new cleanup package."""
    return {
        path.name: symbols(path.read_text(encoding="utf-8"))
        for path in sorted(NEW_DIR.rglob("*.py"))
        if "__pycache__" not in path.parts
    }


def shim_symbols() -> dict[str, dict[str, str]]:
    """Return ``old path -> symbols`` still defined in the re-export shims."""
    return {
        path: symbols((ROOT / path).read_text(encoding="utf-8"))
        for path in SHIM_PATHS
    }


@dataclass
class Report:
    """Outcome of the move check."""

    total: int = 0
    matched: int = 0
    moved: int = 0
    kept: int = 0
    expected_edits_applied: int = 0
    missing: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when every old symbol is accounted for exactly once."""
        return not self.missing and not self.changed and not self.ambiguous


def check(from_git: bool = False) -> Report:
    """Compare the old modules with the new package and return a report."""
    old, applied_edits = old_symbols(from_git)
    new = new_symbols()
    shims = shim_symbols()
    report = Report(expected_edits_applied=applied_edits)

    for origin_path, old_symbols_map in old.items():
        for name, dump in old_symbols_map.items():
            report.total += 1
            moved_modules = [
                module for module, syms in new.items() if syms.get(name) == dump
            ]
            if len(moved_modules) == 1:
                report.matched += 1
                report.moved += 1
                continue
            if len(moved_modules) > 1:
                report.ambiguous.append(f"{origin_path}:{name} -> {moved_modules}")
                continue
            kept_shims = [
                shim for shim, syms in shims.items() if syms.get(name) == dump
            ]
            if kept_shims:
                report.matched += 1
                report.kept += 1
                continue
            same_name = sorted(
                [
                    f"{module}:{name}"
                    for module, syms in new.items()
                    if name in syms
                ]
                + [f"{shim}:{name}" for shim, syms in shims.items() if name in syms]
            )
            if same_name:
                report.changed.append(f"{origin_path}:{name} (differs in {same_name})")
            else:
                report.missing.append(f"{origin_path}:{name}")

    print(f"old symbols:            {report.total}")
    print(f"matched:                {report.matched}")
    print(f"moved:                  {report.moved}")
    print(f"kept:                   {report.kept}")
    print(f"expected edits applied: {report.expected_edits_applied}")
    print(f"missing:                {len(report.missing)}")
    print(f"changed:                {len(report.changed)}")
    print(f"ambiguous:              {len(report.ambiguous)}")
    for line in report.missing:
        print(f"  MISSING {line}")
    for line in report.changed:
        print(f"  CHANGED {line}")
    for line in report.ambiguous:
        print(f"  AMBIGUOUS {line}")
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: exit 0 only when the split is a pure move."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--from-git",
        action="store_true",
        help="read the old modules from main instead of tests/fixtures",
    )
    args = parser.parse_args(argv)
    report = check(args.from_git)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
