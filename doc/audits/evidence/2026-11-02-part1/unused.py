#!/usr/bin/env python3
"""Read-only unused-symbol scanner for agy-mcp.

For every module-level function/class/constant defined under src/agy_mcp/, count
references across src/, tests/, scripts/ (Python text). Reports names whose only
occurrence is the definition itself. Never writes to the repo.

Usage: env -u PYTHONPATH .venv/bin/python ~/agy-review/audit/unused.py <repo_root>
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

SEARCH_DIRS = ("src", "tests", "scripts")


def defined_names(path: Path) -> list[tuple[str, str, int]]:
    """Return (kind, name, lineno) for module-level definitions."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(("func", node.name, node.lineno))
        elif isinstance(node, ast.ClassDef):
            out.append(("class", node.name, node.lineno))
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    out.append(("const", t.id, node.lineno))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id.isupper():
                out.append(("const", node.target.id, node.lineno))
    return out


def main(root: str) -> int:
    root_p = Path(root)
    files = []
    for d in SEARCH_DIRS:
        files.extend(sorted((root_p / d).rglob("*.py")))
    texts = {f: f.read_text(encoding="utf-8", errors="replace") for f in files}
    results = []
    for f in sorted((root_p / "src").rglob("*.py")):
        for kind, name, line in defined_names(f):
            if name.startswith("__"):
                continue
            pat = re.compile(rf"\b{re.escape(name)}\b")
            hits = {}
            for g, text in texts.items():
                n = len(pat.findall(text))
                if g == f:
                    n -= 1  # the definition itself
                if n > 0:
                    hits[str(g.relative_to(root_p))] = n
            # docstring mentions count too; report raw
            results.append({
                "module": str(f.relative_to(root_p)),
                "kind": kind,
                "name": name,
                "line": line,
                "refs": hits,
                "total_refs": sum(hits.values()),
            })
    unused = [r for r in results if r["total_refs"] == 0]
    internal_only = [
        r for r in results
        if r["total_refs"] > 0
        and all(k.startswith("src/") for k in r["refs"])
        and all(k == r["module"] for k in r["refs"])
    ]
    print(json.dumps({
        "unused": sorted(unused, key=lambda r: (r["module"], r["line"])),
        "same_module_only": sorted(internal_only, key=lambda r: (r["module"], r["line"])),
        "counts": {"defs": len(results), "unused": len(unused), "same_module_only": len(internal_only)},
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
