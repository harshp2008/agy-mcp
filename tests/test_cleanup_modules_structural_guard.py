"""Structural guard for the extracted cleanup modules.

``tests/test_cleanup_jobs_fixes3.py`` applies the no-swallowed-exceptions AST
rule to ``src/agy_mcp/cleanup_jobs.py``, which is now a re-export shim with no
exception handlers.  This file applies the same rule to every module of the
``agy_mcp.cleanup`` package plus both shims, so the guard keeps inspecting the
code that actually runs after the wave-2a split.

The offender detection is copied verbatim from the frozen guard test; only the
scanning loop is new.  Three handlers inherited from main's
``cleanup_fixtures.py`` are recorded as known debt in ``KNOWN_DEBT`` (see
``doc/developing/gates.md``) so the guard still fails on any new offender while
the ``src/`` split stays a pure move.  ``test_guard_scanned_real_code`` pins the
number of ``except`` clauses inspected so the check cannot go vacuous again if
the code moves once more.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLEANUP_DIR = REPO_ROOT / "src" / "agy_mcp" / "cleanup"
SHIM_PATHS = (
    REPO_ROOT / "src" / "agy_mcp" / "cleanup_jobs.py",
    REPO_ROOT / "src" / "agy_mcp" / "cleanup_fixtures.py",
)

# jobs_cli.py has 22 handlers and fixtures_cli.py has 13; the whole package has
# 69 today.  The floor is deliberately a little below that: it catches a guard
# that silently stops finding code, without freezing every future refactor.
MIN_EXCEPT_CLAUSES = 65


def find_offending_handlers(source: str) -> list[tuple[int, str]]:
    """Check Python source for offending exception handlers per the structural guard rule.

    The rule: every handler for Exception, BaseException or a bare except,
    INCLUDING when the type is a tuple that contains either, must contain a
    raise, or must record a failure reason (a return or assignment that includes
    the caught exception variable or a reason string/list, or a call to something
    whose name contains fail, block, warn, reason, append or write). A handler
    whose body is only pass, continue, break, "...", a bare constant return
    (return, return None, return False, return True, return 0), or only a logging
    call is offending. Also flag any use of contextlib.suppress(Exception/BaseException)
    and a "return" inside a "finally" block.
    """
    text = source.strip()
    if text == "try/finally with return in finally":
        text = "try:\n    pass\nfinally:\n    return"
    elif text.startswith("bare "):
        text = text[5:].strip()
    if text.startswith("except"):
        text = f"try:\n    pass\n{text}"

    tree = ast.parse(text)
    flagged: list[tuple[int, str]] = []

    def is_broad_type(t: ast.AST | None) -> bool:
        if t is None:
            return True
        if isinstance(t, ast.Name) and t.id in ("Exception", "BaseException"):
            return True
        if isinstance(t, ast.Tuple):
            return any(is_broad_type(elt) for elt in t.elts)
        return False

    def is_bare_constant(val: ast.AST | None) -> bool:
        if val is None:
            return True
        if isinstance(val, ast.Constant) and val.value in (None, False, True, 0):
            return True
        return False

    def is_logging_call(call: ast.Call) -> bool:
        func = call.func
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name) and func.value.id in ("logger", "logging", "log"):
                return True
        elif isinstance(func, ast.Name) and func.id in (
            "log",
            "debug",
            "info",
            "warning",
            "error",
            "exception",
        ):
            return True
        return False

    def has_reason_recording(node_list: list[ast.stmt], exc_var: str | None) -> bool:
        for stmt in node_list:
            for n in ast.walk(stmt):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if isinstance(n, ast.Raise):
                    return True
                if isinstance(n, ast.Call):
                    if not is_logging_call(n):
                        name = ""
                        if isinstance(n.func, ast.Name):
                            name = n.func.id
                        elif isinstance(n.func, ast.Attribute):
                            name = n.func.attr
                        name = name.lower()
                        if any(
                            kw in name
                            for kw in ("fail", "block", "warn", "reason", "append", "write")
                        ):
                            return True
                if isinstance(n, (ast.Return, ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    if exc_var is not None:
                        for sub in ast.walk(n):
                            if isinstance(sub, ast.Name) and sub.id == exc_var:
                                return True
                    for sub in ast.walk(n):
                        if isinstance(sub, (ast.Constant, ast.JoinedStr)):
                            if (
                                isinstance(sub, ast.Constant)
                                and isinstance(sub.value, str)
                                and sub.value.strip()
                            ):
                                return True
                            if isinstance(sub, ast.JoinedStr):
                                return True
                        if isinstance(sub, ast.Name):
                            sub_id = sub.id.lower()
                            if any(
                                kw in sub_id for kw in ("reason", "fail", "warn", "block", "err")
                            ):
                                return True
        return False

    # 1. Flag use of contextlib.suppress(Exception/BaseException)
    for node in ast.walk(tree):
        if isinstance(node, ast.With):
            for item in node.items:
                if isinstance(item.context_expr, ast.Call):
                    f = item.context_expr.func
                    is_suppress = False
                    if isinstance(f, ast.Attribute) and f.attr == "suppress":
                        is_suppress = True
                    elif isinstance(f, ast.Name) and f.id == "suppress":
                        is_suppress = True
                    if is_suppress and any(is_broad_type(a) for a in item.context_expr.args):
                        flagged.append((node.lineno, ast.unparse(node).strip()))

        # 2. Flag return inside finally block
        if isinstance(node, ast.Try) and node.finalbody:
            for stmt in node.finalbody:
                for sub in ast.walk(stmt):
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if isinstance(sub, ast.Return):
                        flagged.append((node.lineno, ast.unparse(node).strip()))
                        break

        # 3. Check broad exception handlers
        if isinstance(node, ast.ExceptHandler) and is_broad_type(node.type):
            exc_var = node.name
            body = node.body
            if not body:
                flagged.append((node.lineno, ast.unparse(node).strip()))
            elif len(body) == 1 and isinstance(body[0], ast.Pass):
                flagged.append((node.lineno, ast.unparse(node).strip()))
            elif len(body) == 1 and isinstance(body[0], ast.Continue):
                flagged.append((node.lineno, ast.unparse(node).strip()))
            elif len(body) == 1 and isinstance(body[0], ast.Break):
                flagged.append((node.lineno, ast.unparse(node).strip()))
            elif (
                len(body) == 1
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and body[0].value.value is ...
            ):
                flagged.append((node.lineno, ast.unparse(node).strip()))
            elif (
                len(body) == 1
                and isinstance(body[0], ast.Return)
                and is_bare_constant(body[0].value)
            ):
                flagged.append((node.lineno, ast.unparse(node).strip()))
            elif (
                len(body) == 1
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Call)
                and is_logging_call(body[0].value)
            ):
                flagged.append((node.lineno, ast.unparse(node).strip()))
            elif not has_reason_recording(body, exc_var):
                flagged.append((node.lineno, ast.unparse(node).strip()))

    return flagged


def count_except_clauses(source: str) -> int:
    """Return the number of ``except`` clauses in *source*."""
    return sum(
        1 for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ExceptHandler)
    )


def scanned_sources() -> list[tuple[Path, str]]:
    """Return ``(path, source)`` for the cleanup package and both shims."""
    paths = sorted(CLEANUP_DIR.rglob("*.py")) + list(SHIM_PATHS)
    return [
        (path, path.read_text(encoding="utf-8"))
        for path in paths
        if "__pycache__" not in path.parts
    ]


# Pre-existing debt inherited verbatim from main's ``cleanup_fixtures.py``.  The
# three entries are keyed by (repo-relative file, enclosing function, handler
# source) so line-number churn and ordinary edits do not hide a new offender;
# ``test_known_debt_allowlist_is_not_stale`` fails if an entry stops matching.
KNOWN_DEBT: dict[tuple[str, str | None, str], str] = {
    (
        "src/agy_mcp/cleanup/fixtures_cli.py",
        "_candidate_strings",
        "except Exception:\n    return []",
    ): "swallows an unreadable/invalid kept job.json and silently protects fewer strings",
    (
        "src/agy_mcp/cleanup/store_io.py",
        "_get_kernel_pid_max",
        "except Exception:\n    pass",
    ): "swallows a /proc/sys/kernel/pid_max read error and falls back to 4194304",
    (
        "src/agy_mcp/cleanup/store_io.py",
        "_parse_iso_ts",
        "except Exception:\n    return None",
    ): "swallows an ISO-8601 parse error and treats the timestamp as absent",
}


def _enclosing_function(tree: ast.AST, line: int, code: str) -> str | None:
    """Return the name of the innermost function around the node at *line*."""
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent

    node = next(
        (
            n
            for n in ast.walk(tree)
            if getattr(n, "lineno", None) == line and ast.unparse(n).strip() == code
        ),
        None,
    )
    if node is None:
        return None
    parent = parents.get(id(node))
    while parent is not None and not isinstance(
        parent, (ast.FunctionDef, ast.AsyncFunctionDef)
    ):
        parent = parents.get(id(parent))
    return parent.name if parent is not None else None


def offender_keys(path: Path, source: str) -> list[tuple[str, str | None, str]]:
    """Return the stable allowlist key for every offender found in *source*."""
    rel = path.relative_to(REPO_ROOT).as_posix()
    tree = ast.parse(source)
    return [
        (rel, _enclosing_function(tree, line, code), code)
        for line, code in find_offending_handlers(source)
    ]


def test_no_swallowed_exceptions_in_cleanup_modules() -> None:
    """No broad handler in the cleanup package or the shims may swallow an error."""
    new_offenders: list[str] = []
    for path, source in scanned_sources():
        for key in offender_keys(path, source):
            if key not in KNOWN_DEBT:
                new_offenders.append(f"{key[0]}::{key[1]}: {key[2]}")

    assert not new_offenders, (
        f"Found {len(new_offenders)} new offending exception handler(s) in the "
        "cleanup modules (add to src or document as debt):\n"
        + "\n".join(f"  {line}" for line in new_offenders)
    )


def test_known_debt_allowlist_is_not_stale() -> None:
    """Every documented debt entry must still match a real handler."""
    seen: set[tuple[str, str | None, str]] = set()
    for path, source in scanned_sources():
        seen.update(offender_keys(path, source))

    stale = sorted(set(KNOWN_DEBT) - seen)
    assert not stale, "documented debt no longer matches an offender:\n" + "\n".join(
        f"  {key[0]}::{key[1]}: {key[2]}" for key in stale
    )


def test_guard_scanned_real_code() -> None:
    """The guard must have inspected the real package, not an empty file list."""
    sources = scanned_sources()
    package_sources = [src for path, src in sources if path.parent == CLEANUP_DIR]
    total = sum(count_except_clauses(src) for src in package_sources)

    assert len(package_sources) >= 6
    assert total >= MIN_EXCEPT_CLAUSES, (
        f"guard scanned only {total} except clauses in {CLEANUP_DIR}, "
        f"expected at least {MIN_EXCEPT_CLAUSES}"
    )


def test_guard_flags_swallowed_exception_in_tmp_copy(tmp_path: Path) -> None:
    """A copy of a real module with ``except Exception: pass`` must be flagged."""
    module = CLEANUP_DIR / "jobs_cli.py"
    copy = tmp_path / "jobs_cli_copy.py"
    copy.write_text(
        module.read_text(encoding="utf-8") + "\n\ntry:\n    pass\nexcept Exception:\n    pass\n",
        encoding="utf-8",
    )

    flagged = find_offending_handlers(copy.read_text(encoding="utf-8"))

    assert flagged, "guard failed to flag an injected `except Exception: pass`"
    assert any("except Exception" in code for _, code in flagged)
