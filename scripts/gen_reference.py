#!/usr/bin/env python3
"""Generate the mechanical tables inside ``doc/reference/``.

Hand-written prose and the "meaning / next action" columns live in the pages;
only the rows that can be derived from the code are generated, between a pair
of markers::

    <!-- BEGIN GENERATED: error-codes -->
    ...
    <!-- END GENERATED: error-codes -->

Usage::

    .venv/bin/python scripts/gen_reference.py --write
    .venv/bin/python scripts/gen_reference.py --check   # exit 1 if stale

``scripts/check_docs.py`` imports this module and fails when a generated block
is stale, so a new error code, config key, tool or audit event cannot land
without its reference table being refreshed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import types
import typing
from dataclasses import MISSING, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "agy_mcp"
DOC = ROOT / "doc" / "reference"
TOOLS_SCHEMA = ROOT / "tests" / "fixtures" / "tools_schema.json"
INSTALLER = ROOT / "installer" / "install.sh"

if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

BEGIN = "<!-- BEGIN GENERATED: {key} -->"
END = "<!-- END GENERATED: {key} -->"

# ``code="x"``, ``code: str = "x"`` and the server fallbacks ``code = "x"``.
_CODE_ASSIGN = re.compile(r'\bcode(?:\s*:\s*str)?\s*=\s*"([a-z_]+)"')
_ENV_READ = re.compile(r'os\.environ(?:\.get)?\(\s*"([A-Z0-9_]+)"')
_INSTALLER_ENV = re.compile(r"\$\{(AGY_MCP_[A-Z0-9_]+):-")
_EVENT_NAME = re.compile(r'event="([a-z0-9_]+)"')


def _py_files() -> list[Path]:
    """Return every package module in a stable order.

    Recursive (``rglob``) so a subpackage module is scanned too; the cleanup
    CLIs moved into ``agy_mcp/cleanup/`` and a flat ``glob("*.py")`` silently
    dropped their ``HOME`` reads from the generated environment table.
    """
    return sorted(
        path for path in SRC.rglob("*.py") if "__pycache__" not in path.parts
    )


def error_code_sources() -> dict[str, list[str]]:
    """Return ``code -> module names`` for every stable error code literal."""
    found: dict[str, set[str]] = {}
    for path in _py_files():
        text = path.read_text(encoding="utf-8")
        for match in _CODE_ASSIGN.finditer(text):
            found.setdefault(match.group(1), set()).add(path.name)
    return {code: sorted(names) for code, names in sorted(found.items())}


def env_var_sources() -> dict[str, dict[str, object]]:
    """Return the environment variables read by production code and the installer."""
    found: dict[str, dict[str, set[str]]] = {}
    for path in _py_files():
        text = path.read_text(encoding="utf-8")
        for match in _ENV_READ.finditer(text):
            entry = found.setdefault(match.group(1), {"scope": {"production"}, "files": set()})
            entry["files"].add(path.name)
    installer_text = INSTALLER.read_text(encoding="utf-8")
    for match in _INSTALLER_ENV.finditer(installer_text):
        entry = found.setdefault(match.group(1), {"scope": {"installer"}, "files": set()})
        entry["scope"].add("installer")
        entry["files"].add("installer/install.sh")
    return {name: found[name] for name in sorted(found)}


def config_fields() -> list[dict[str, str]]:
    """Return one row per user-settable key in :class:`agy_mcp.config.Config`.

    ``unknown_keys`` is a load-time report field, not a config key (SPEC §18),
    so it is excluded; the result is 27 keys including ``timezone``.
    """
    from agy_mcp.config import Config

    rows: list[dict[str, str]] = []
    for field_info in fields(Config):
        if field_info.name == "unknown_keys":
            continue
        rows.append(
            {
                "name": field_info.name,
                "type": _type_label(field_info.type),
                "default": _default_label(field_info),
            }
        )
    return rows


def job_fields() -> list[dict[str, str]]:
    """Return one row per persisted ``job.json`` field."""
    from agy_mcp.models import Job

    return [
        {"name": field_info.name, "type": _type_label(field_info.type)}
        for field_info in fields(Job)
    ]


def tool_rows() -> list[dict[str, str]]:
    """Return one row per MCP tool from the pinned ``tools_schema.json``."""
    data = json.loads(TOOLS_SCHEMA.read_text(encoding="utf-8"))
    rows: list[dict[str, str]] = []
    for tool in data:
        schema = tool.get("inputSchema", {})
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        rendered: list[str] = []
        for name, spec in properties.items():
            rendered.append(_render_parameter(name, spec, name in required))
        rows.append(
            {
                "name": tool["name"],
                "parameters": ", ".join(rendered) if rendered else "none",
            }
        )
    return rows


def audit_events() -> dict[str, list[str]]:
    """Return ``event -> modules`` for every literal audit event name."""
    found: dict[str, set[str]] = {}
    for path in _py_files():
        text = path.read_text(encoding="utf-8")
        for match in _EVENT_NAME.finditer(text):
            found.setdefault(match.group(1), set()).add(path.name)
    return {event: sorted(files) for event, files in sorted(found.items())}


def _type_label(annotation: object) -> str:
    """Render a dataclass annotation as a short reference type."""
    if annotation is type(None):
        return "null"
    origin = typing.get_origin(annotation)
    if origin is list:
        args = typing.get_args(annotation)
        return f"list[{_type_label(args[0])}]" if args else "list"
    if origin is typing.Union:
        parts = [_type_label(arg) for arg in typing.get_args(annotation)]
        return " | ".join(parts)
    if isinstance(annotation, type) and not isinstance(annotation, types.GenericAlias):
        return annotation.__name__
    return str(annotation).replace("typing.", "")


def _json_default(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        return f'"{value}"'
    return json.dumps(value)


def _default_label(field_info: object) -> str:
    if field_info.default is not MISSING:
        value = field_info.default
    elif field_info.default_factory is not MISSING:
        value = field_info.default_factory()
    else:
        return "—"
    return f"`{_json_default(value)}`"


def _parameter_type(spec: dict[str, object]) -> str:
    if "type" in spec:
        return str(spec["type"])
    variants = spec.get("anyOf")
    if isinstance(variants, list):
        return "|".join(_parameter_type(variant) for variant in variants)
    return "any"


def _render_parameter(name: str, spec: dict[str, object], required: bool) -> str:
    rendered = f"`{name}`: {_parameter_type(spec)}"
    if required:
        return f"{rendered} (required)"
    if "default" in spec:
        return f"{rendered} = {_json_default(spec['default'])}"
    return rendered


def _table(header: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def render_error_codes() -> str:
    return _table(
        ["Code", "Raised in"],
        [
            [f"`{code}`", ", ".join(f"`{name}`" for name in names)]
            for code, names in error_code_sources().items()
        ],
    )


def render_env_vars() -> str:
    rows: list[list[str]] = []
    # Production variables first, then the installer's inputs (both alphabetical).
    ordered = sorted(
        env_var_sources().items(),
        key=lambda item: (0 if "production" in item[1]["scope"] else 1, item[0]),
    )
    for name, entry in ordered:
        scope = " + ".join(sorted(entry["scope"]))
        files = ", ".join(f"`{path}`" for path in sorted(entry["files"]))
        rows.append([f"`{name}`", scope, files])
    return _table(["Variable", "Scope", "Read in"], rows)


def render_config_keys() -> str:
    return _table(
        ["Key", "Type", "Default"],
        [
            [f"`{row['name']}`", f"`{row['type']}`", row["default"]]
            for row in config_fields()
        ],
    )


def render_job_fields() -> str:
    return _table(
        ["Field", "Type"],
        [[f"`{row['name']}`", f"`{row['type']}`"] for row in job_fields()],
    )


def render_mcp_tools() -> str:
    return _table(
        ["Tool", "Parameters"],
        [[f"`{row['name']}`", row["parameters"]] for row in tool_rows()],
    )


def render_audit_events() -> str:
    return _table(
        ["Event", "Emitted in"],
        [
            [f"`{event}`", ", ".join(f"`{name}`" for name in names)]
            for event, names in audit_events().items()
        ],
    )


# key -> (page relative to doc/reference, renderer)
BLOCKS: dict[str, tuple[str, typing.Callable[[], str]]] = {
    "error-codes": ("errors.md", render_error_codes),
    "env-vars": ("environment.md", render_env_vars),
    "config-keys": ("config-keys.md", render_config_keys),
    "job-fields": ("state-layout.md", render_job_fields),
    "mcp-tools": ("mcp-tools.md", render_mcp_tools),
    "audit-events": ("log-formats.md", render_audit_events),
}


def render_block(key: str) -> str:
    """Render the generated table for *key* (no markers)."""
    return BLOCKS[key][1]()


def replace_block(text: str, key: str) -> str:
    """Replace the content between the markers for *key* in *text*."""
    begin = BEGIN.format(key=key)
    end = END.format(key=key)
    if begin not in text or end not in text:
        raise ValueError(f"missing generated-block markers for '{key}'")
    start = text.index(begin) + len(begin)
    stop = text.index(end, start)
    return f"{text[:start]}\n{render_block(key)}\n{text[stop:]}"


def check_block(text: str, key: str) -> bool:
    """Return True when the generated block for *key* is fresh."""
    try:
        return text == replace_block(text, key)
    except ValueError:
        return False


def write_all() -> int:
    """Refresh every generated block; return the number of changed pages."""
    changed = 0
    for key, (relative, _renderer) in BLOCKS.items():
        page = DOC / relative
        if not page.is_file():
            print(f"error: missing {page.relative_to(ROOT)}", file=sys.stderr)
            return -1
        text = page.read_text(encoding="utf-8")
        try:
            updated = replace_block(text, key)
        except ValueError as exc:
            print(f"error: {page.relative_to(ROOT)}: {exc}", file=sys.stderr)
            return -1
        if updated != text:
            page.write_text(updated, encoding="utf-8")
            changed += 1
            print(f"updated {page.relative_to(ROOT)} ({key})")
    return changed


def check_all() -> list[str]:
    """Return one message per missing marker or stale generated block."""
    findings: list[str] = []
    for key, (relative, _renderer) in BLOCKS.items():
        page = DOC / relative
        if not page.is_file():
            findings.append(f"missing page: doc/reference/{relative}")
            continue
        text = page.read_text(encoding="utf-8")
        begin = BEGIN.format(key=key)
        end = END.format(key=key)
        if begin not in text or end not in text:
            findings.append(f"missing generated markers '{key}' in doc/reference/{relative}")
        elif not check_block(text, key):
            findings.append(
                f"stale generated block '{key}' in doc/reference/{relative}; "
                "run .venv/bin/python scripts/gen_reference.py --write"
            )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="rewrite the generated blocks")
    parser.add_argument("--check", action="store_true", help="exit 1 when a block is stale")
    parser.add_argument("--stdout", metavar="KEY", help="print one generated table")
    args = parser.parse_args(argv)

    if args.stdout:
        if args.stdout not in BLOCKS:
            print(f"error: unknown block '{args.stdout}'", file=sys.stderr)
            return 2
        print(render_block(args.stdout))
        return 0

    if args.write:
        return 0 if write_all() >= 0 else 2

    findings = check_all()
    if findings:
        for finding in findings:
            print(f"error: {finding}", file=sys.stderr)
        return 1
    print("doc/reference generated blocks are fresh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
