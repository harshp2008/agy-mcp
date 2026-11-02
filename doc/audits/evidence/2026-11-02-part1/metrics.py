#!/usr/bin/env python3
"""Read-only complexity/metrics collector for the agy-mcp code-structure audit.

Reads Python files, emits JSON on stdout. Never writes to the repo.
Usage: env -u PYTHONPATH .venv/bin/python ~/agy-review/audit/metrics.py <paths...>
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

BRANCH_NODES = (
    ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler,
    ast.With, ast.AsyncWith, ast.IfExp, ast.Match,
)
NEST_NODES = (
    ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith,
    ast.Try, ast.Match, ast.ExceptHandler,
)


def max_depth(node: ast.AST, depth: int = 0) -> int:
    """Max nesting depth of control-flow constructs inside node."""
    best = depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child is not node:
            continue  # separate function: measured on its own
        add = 1 if isinstance(child, NEST_NODES) else 0
        best = max(best, max_depth(child, depth + add))
    return best


def branch_count(node: ast.AST) -> int:
    """Cyclomatic-ish: decision points inside a function."""
    n = 0
    for child in ast.walk(node):
        if isinstance(child, BRANCH_NODES):
            n += 1
        elif isinstance(child, ast.BoolOp):
            n += len(child.values) - 1
        elif isinstance(child, ast.comprehension):
            n += 1 + len(child.ifs)
        elif isinstance(child, ast.Assert):
            n += 1
    return n


def has_docstring(node) -> bool:
    return ast.get_docstring(node) is not None


def parse(path: Path) -> dict:
    src = path.read_text(encoding="utf-8", errors="replace")
    lines = src.splitlines()
    tree = ast.parse(src)
    funcs, classes = [], []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append(node)
        elif isinstance(node, ast.ClassDef):
            classes.append(node)
    all_funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    details = []
    for f in all_funcs:
        length = (f.end_lineno or f.lineno) - f.lineno + 1
        details.append({
            "name": f.name,
            "line": f.lineno,
            "length": length,
            "depth": max_depth(f),
            "branches": branch_count(f),
            "doc": has_docstring(f),
            "top_level": f in funcs,
        })
    details.sort(key=lambda d: (-d["branches"], -d["length"]))
    doc_funcs = sum(1 for d in details if d["doc"])
    return {
        "path": str(path),
        "lines": len(lines),
        "code_lines": sum(1 for ln in lines if ln.strip() and not ln.strip().startswith("#")),
        "comment_lines": sum(1 for ln in lines if ln.strip().startswith("#")),
        "module_doc": ast.get_docstring(tree) is not None,
        "top_funcs": len(funcs),
        "classes": len(classes),
        "funcs_total": len(details),
        "funcs_doc": doc_funcs,
        "doc_pct": round(100.0 * doc_funcs / len(details), 1) if details else 0.0,
        "imports": [
            (n.module or "") if isinstance(n, ast.ImportFrom) else ",".join(a.name for a in n.names)
            for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
        ],
        "functions": details,
    }


def main(argv: list[str]) -> int:
    out = []
    for arg in argv:
        root = Path(arg)
        files = sorted(root.rglob("*.py")) if root.is_dir() else [root]
        for p in files:
            try:
                out.append(parse(p))
            except SyntaxError as exc:  # pragma: no cover
                out.append({"path": str(p), "error": str(exc)})
    out.sort(key=lambda d: -d.get("lines", 0))
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
