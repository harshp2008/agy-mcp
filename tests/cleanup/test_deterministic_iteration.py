"""Regression guard: ensure deterministic directory iteration in cleanup modules."""

import ast
from pathlib import Path
from typing import NamedTuple

CLEANUP_DIR = Path(__file__).resolve().parents[2] / "src" / "agy_mcp" / "cleanup"

TARGET_METHODS = {"iterdir", "glob", "rglob"}
TARGET_FUNCS = {"listdir", "scandir"}
ALLOWED_WRAPPERS = {"sorted", "next", "any", "all", "len"}

# In-file allowlist keyed by (file, enclosing_function) with a one-line justification.
# Note: worktree_status.py MUST NEVER be allowlisted.
ALLOWLIST: dict[tuple[str, str], str] = {
    (
        "git_metadata.py",
        "_validate_git_dir",
    ): "readability probe: validates directory can be iterated without error, entries discarded",
    (
        "jobs_cli.py",
        "main",
    ): "emptiness/readability probe via next(store_jobs.iterdir(), None)",
    (
        "fixtures_cli.py",
        "main",
    ): "emptiness/readability probe via next(store_jobs.iterdir(), None)",
}


class Violation(NamedTuple):
    file: str
    function: str
    lineno: int
    call_expr: str


class IterationScanner(ast.NodeVisitor):
    """AST visitor that checks directory iteration calls for deterministic wrapping."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.scope_stack: list[str] = []
        self.parents: dict[ast.AST, ast.AST] = {}
        self.violations: list[Violation] = []

    def current_function(self) -> str:
        return self.scope_stack[-1] if self.scope_stack else "<module>"

    def visit(self, node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            self.parents[child] = node
        super().visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope_stack.append(node.name)
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.scope_stack.append(node.name)
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        is_target = False
        if isinstance(node.func, ast.Attribute) and node.func.attr in TARGET_METHODS:
            is_target = True
        elif isinstance(node.func, ast.Attribute) and node.func.attr in TARGET_FUNCS:
            is_target = True
        elif isinstance(node.func, ast.Name) and node.func.id in TARGET_FUNCS:
            is_target = True

        if is_target:
            parent = self.parents.get(node)
            is_allowed = False
            if isinstance(parent, ast.Call):
                func_name = None
                if isinstance(parent.func, ast.Name):
                    func_name = parent.func.id
                elif isinstance(parent.func, ast.Attribute):
                    func_name = parent.func.attr
                if func_name in ALLOWED_WRAPPERS:
                    is_allowed = True

            fn_name = self.current_function()
            if not is_allowed and (self.filename, fn_name) not in ALLOWLIST:
                call_src = getattr(node.func, "attr", getattr(node.func, "id", "call"))
                self.violations.append(
                    Violation(
                        file=self.filename,
                        function=fn_name,
                        lineno=node.lineno,
                        call_expr=call_src,
                    )
                )

        self.generic_visit(node)


def scan_file(file_path: Path) -> list[Violation]:
    """Scan a Python source file for unverified directory iteration."""
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(file_path))
    scanner = IterationScanner(file_path.name)
    scanner.visit(tree)
    return scanner.violations


def test_cleanup_modules_use_deterministic_iteration() -> None:
    """Every iteration call in src/agy_mcp/cleanup/ must be sorted, consumed, or allowlisted."""
    assert not any(f == "worktree_status.py" for (f, _) in ALLOWLIST), (
        "worktree_status.py must never be allowlisted"
    )

    all_violations: list[Violation] = []
    py_files = sorted(CLEANUP_DIR.glob("*.py"))
    assert py_files, f"Expected cleanup modules in {CLEANUP_DIR}"

    for py_file in py_files:
        violations = scan_file(py_file)
        all_violations.extend(violations)

    assert not all_violations, (
        f"Found {len(all_violations)} unverified iteration call(s) in cleanup: {all_violations}"
    )


def test_proof_injected_bare_iterdir_is_flagged(tmp_path: Path) -> None:
    """Verify scanner flags an injected bare list(p.iterdir()) call in a temp file."""
    proof_file = tmp_path / "injected_module.py"
    proof_file.write_text(
        "from pathlib import Path\n\n"
        "def bad_function(wt_root: Path) -> list[Path]:\n"
        "    entries = list(wt_root.iterdir())\n"
        "    return entries\n",
        encoding="utf-8",
    )

    violations = scan_file(proof_file)
    assert len(violations) == 1, f"Expected 1 violation, got {violations}"
    v = violations[0]
    assert v.file == "injected_module.py"
    assert v.function == "bad_function"
    assert v.call_expr == "iterdir"
    assert v.lineno == 4
