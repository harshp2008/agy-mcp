#!/usr/bin/env python3
"""Guard against accidental behavior changes while editing docs/comments.

This script parses every ``.py`` file under ``src/agy_mcp/`` with :mod:`ast`,
removes every docstring (module, class, function and async function), and
hashes the resulting AST dump.  Because line numbers, formatting, comments and
docstrings are excluded from the hash, comment-only edits leave it unchanged;
any edit that changes executable structure (constants, control flow, names,
arguments, ...) changes it.

Usage::

    # Snapshot the current tree.
    .venv/bin/python scripts/check_no_behavior_change.py \
        --save docs/reports/docs-codemap/ast-baseline.json

    # Compare the current tree against a snapshot (exit 1 on any change).
    .venv/bin/python scripts/check_no_behavior_change.py \
        --compare docs/reports/docs-codemap/ast-baseline.json

Only the standard library is used so this runs with the project venv.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "agy_mcp"

_DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _is_docstring(node: ast.stmt) -> bool:
    """Return True for an expression statement holding a bare string literal."""
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def strip_docstrings(tree: ast.AST) -> ast.AST:
    """Remove the first docstring expression from every scope in *tree*.

    The mutation happens in place; the same tree is returned for convenience.
    A scope whose body would become empty is left with an empty ``body`` list,
    which ``ast.dump`` renders just fine.  ``ast.parse`` is only called on real
    source files, where an empty body is impossible to write, so this can only
    happen after we removed a docstring that was the sole statement -- which
    cannot occur in valid Python either.  Keeping the empty list is therefore a
    formality.
    """
    for node in ast.walk(tree):
        if isinstance(node, _DOCSTRING_OWNERS):
            body = getattr(node, "body", None)
            if body and _is_docstring(body[0]):
                node.body = body[1:]
    return tree


def hash_source(source: str, filename: str) -> str:
    """Return the SHA-256 of the docstring-free AST dump for *source*."""
    tree = ast.parse(source, filename=filename)
    tree = strip_docstrings(tree)
    dumped = ast.dump(tree, include_attributes=False)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()


def collect_hashes() -> dict[str, str]:
    """Hash every Python file under ``src/agy_mcp/`` (sorted, repo-relative)."""
    hashes: dict[str, str] = {}
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        hashes[rel] = hash_source(path.read_text(encoding="utf-8"), rel)
    return hashes


def save_snapshot(destination: Path) -> int:
    hashes = collect_hashes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(hashes, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for rel, digest in hashes.items():
        print(f"{digest}  {rel}")
    print(f"\nsaved {len(hashes)} hashes to {destination}")
    return 0


def compare_snapshot(baseline_path: Path) -> int:
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"error: baseline not found: {baseline_path}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"error: baseline is not valid JSON: {exc}", file=sys.stderr)
        return 2

    current = collect_hashes()
    changed: list[str] = []
    for rel in sorted(set(baseline) | set(current)):
        if rel not in current:
            changed.append(f"REMOVED {rel}")
        elif rel not in baseline:
            changed.append(f"ADDED   {rel}")
        elif baseline[rel] != current[rel]:
            changed.append(f"CHANGED {rel}")
        else:
            print(f"same    {rel}")

    if changed:
        print("\nBEHAVIOR CHANGE DETECTED:")
        for line in changed:
            print(f"  {line}")
        return 1

    print(f"\nOK: {len(current)} files match the AST baseline (no behavior change)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--save", metavar="FILE", help="write an AST-hash snapshot")
    group.add_argument("--compare", metavar="FILE", help="compare against a snapshot")
    args = parser.parse_args(argv)

    if args.save:
        return save_snapshot(Path(args.save))
    try:
        return compare_snapshot(Path(args.compare))
    except SyntaxError as exc:
        print(f"error: could not parse {exc.filename}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
