"""Guard: a new agy spawn or ``/usage`` probe cannot bypass ``build_account_env``.

Regression guard for the live bug (human test 2026-11-03): the scheduler read
``/usage`` for a non-default account without the account env, so agy used the
desktop keyring and authenticated as the primary login. A static scan keeps the
next spawn path honest instead of relying on review.

Rules:
1. Every ``get_usage``/``read_usage_once`` call in ``src/`` passes ``account=``.
2. Every function that builds agy argv (``build_argv``) also builds the child
   env with ``build_account_env``.
3. Every process spawn passes an explicit env; a spawn whose code mentions an
   agy binary either calls ``build_account_env`` or receives ``env`` as a
   parameter (a generic runner the caller owns, like ``LocalProcessRunner``).
"""

import ast
from pathlib import Path
from typing import NamedTuple

SRC = Path(__file__).resolve().parents[1] / "src" / "agy_mcp"

USAGE_CALLS = {"get_usage", "read_usage_once"}
SPAWN_CALLS = {
    "subprocess.Popen",
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "os.posix_spawn",
    "os.posix_spawnp",
    "os.execv",
    "os.execve",
    "os.execvp",
    "os.execvpe",
}
AGY_MARKERS = ("agy", "build_argv")


class Violation(NamedTuple):
    file: str
    function: str
    lineno: int
    rule: str


def _dotted_name(node: ast.AST) -> str | None:
    """Return ``module.attr`` for a Name/Attribute chain, else None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _call_name(node: ast.Call) -> str | None:
    """Return the unqualified callee name (``usage_utils.get_usage`` → ``get_usage``)."""
    dotted = _dotted_name(node.func)
    return dotted.rsplit(".", 1)[-1] if dotted else None


def _spawn_argv_text(
    call: ast.Call, node: ast.FunctionDef | ast.AsyncFunctionDef
) -> str:
    """Best-effort source of a spawn's argv, including a local list variable."""
    parts: list[str] = []
    if call.args:
        first = call.args[0]
        parts.append(ast.unparse(first))
        if isinstance(first, ast.Name):
            for child in ast.walk(node):
                if isinstance(child, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == first.id
                    for target in child.targets
                ):
                    parts.append(ast.unparse(child.value))
    return "\n".join(parts)


def _function_params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    params = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    return {param.arg for param in params}


class SpawnScanner(ast.NodeVisitor):
    """Collect spawn/usage violations and the functions that contain them."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.functions: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        self.violations: list[Violation] = []
        self.spawns_seen = 0

    def _check_functions(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._check_function(node)

    def _check_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        calls = [child for child in ast.walk(node) if isinstance(child, ast.Call)]
        params = _function_params(node)

        builds_argv = any(_call_name(call) == "build_argv" for call in calls)
        builds_env = any(_call_name(call) == "build_account_env" for call in calls)
        if builds_argv and not builds_env:
            self.violations.append(
                Violation(
                    self.filename,
                    node.name,
                    node.lineno,
                    "agy argv is built without build_account_env",
                )
            )

        for call in calls:
            name = _call_name(call)
            if name in USAGE_CALLS and not any(
                kw.arg == "account" for kw in call.keywords
            ):
                self.violations.append(
                    Violation(
                        self.filename,
                        node.name,
                        call.lineno,
                        f"{name}(...) does not pass account=",
                    )
                )
            if _dotted_name(call.func) not in SPAWN_CALLS:
                continue
            self.spawns_seen += 1
            env_kw = next((kw for kw in call.keywords if kw.arg == "env"), None)
            if env_kw is None:
                self.violations.append(
                    Violation(
                        self.filename,
                        node.name,
                        call.lineno,
                        "process spawn without an explicit env=",
                    )
                )
                continue
            argv_text = _spawn_argv_text(call, node)
            if not any(marker in argv_text for marker in AGY_MARKERS):
                continue
            delegated = isinstance(env_kw.value, ast.Name) and env_kw.value.id in params
            if not builds_env and not delegated:
                self.violations.append(
                    Violation(
                        self.filename,
                        node.name,
                        call.lineno,
                        "agy spawn env is not build_account_env nor a caller env",
                    )
                )

    def scan(self, tree: ast.Module) -> list[Violation]:
        self._check_functions(tree)
        return self.violations


def scan_tree() -> tuple[list[Violation], int]:
    """Scan every production module and return (violations, spawn calls seen)."""
    violations: list[Violation] = []
    spawns = 0
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        scanner = SpawnScanner(str(path.relative_to(SRC.parent.parent)))
        violations.extend(scanner.scan(tree))
        spawns += scanner.spawns_seen
    return violations, spawns


def test_every_agy_spawn_uses_the_account_env_builder() -> None:
    violations, spawns = scan_tree()
    assert spawns >= 2, "the scan found no process spawns; the guard is not running"
    assert not violations, "\n".join(
        f"{v.file}:{v.lineno} {v.function}: {v.rule}" for v in violations
    )


def test_the_scanner_flags_a_leaky_usage_call_and_spawn() -> None:
    """The guard is not vacuous: a synthetic leak must produce both violations."""
    source = """
import subprocess
from agy_mcp.util import env_allowlist

def leaky(agy_binary, account, tmp_dir):
    get_usage(agy_binary, home=account.home)
    argv = [str(agy_binary), "-p", "/usage"]
    subprocess.Popen(argv, env=env_allowlist(job_tmp_dir=tmp_dir, home=account.home))
"""
    tree = ast.parse(source)
    rules = {violation.rule for violation in SpawnScanner("synthetic.py").scan(tree)}
    assert "get_usage(...) does not pass account=" in rules
    assert "agy spawn env is not build_account_env nor a caller env" in rules


def test_the_guard_detects_the_known_spawn_sites() -> None:
    """A vacuous scan would pass the guard; pin the two known spawns explicitly."""
    usage_source = (SRC / "usage.py").read_text(encoding="utf-8")
    runner_source = (SRC / "runner.py").read_text(encoding="utf-8")
    assert "build_account_env(account, tmp_dir)" in usage_source
    assert "env = env_allowlist(job_tmp_dir=tmp_dir, home=home)" in usage_source
    assert "def launch(" in runner_source
    assert "env: dict[str, str]," in runner_source
