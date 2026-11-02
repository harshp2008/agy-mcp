"""Freshness checks for the hand-maintained codebase map.

These tests fail when `doc/project/code-layout.md` drifts from the source tree: a module
added under `src/agy_mcp/` must be mentioned, and the generated module index
must match what `scripts/gen_codemap_index.py` would produce right now.
"""

from pathlib import Path

from scripts import gen_codemap_index
from scripts.gen_codemap_index import (
    BEGIN_MARKER,
    CODEMAP_PATH,
    END_MARKER,
    collect_modules,
    replace_generated,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_codemap_exists_and_has_markers() -> None:
    """The map must exist and keep the generator's marker pair intact."""
    assert CODEMAP_PATH.is_file(), f"missing {CODEMAP_PATH}"
    text = CODEMAP_PATH.read_text(encoding="utf-8")
    assert BEGIN_MARKER in text
    assert END_MARKER in text


def test_every_module_is_mentioned_in_codemap() -> None:
    """Every importable module under src/agy_mcp/ must appear in CODEMAP.md."""
    text = CODEMAP_PATH.read_text(encoding="utf-8")
    # Use the generator's own recursive walk so a subpackage module is named the
    # same way here as in the table (audit A11/R6).
    modules = [info.name for info in collect_modules()]
    assert modules, "no modules found under src/agy_mcp/"
    missing = [name for name in modules if f"`{name}`" not in text]
    assert not missing, f"code-layout.md does not mention modules: {missing}"


def test_generated_section_is_fresh() -> None:
    """The generated index must be byte-identical to a fresh render."""
    text = CODEMAP_PATH.read_text(encoding="utf-8")
    expected = replace_generated(text)
    assert text == expected, (
        "doc/project/code-layout.md generated section is stale; run "
        ".venv/bin/python scripts/gen_codemap_index.py --write"
    )


def test_codemap_is_inside_the_repo() -> None:
    """Sanity check: the map lives in doc/project/ next to the other project docs."""
    assert CODEMAP_PATH == REPO_ROOT / "doc" / "project" / "code-layout.md"


def test_generator_recurses_into_a_subpackage(tmp_path, monkeypatch) -> None:
    """A temporary fixture package proves subpackage modules are indexed.

    No subpackage exists in ``src/`` yet (that is Wave 2), so the recursive walk
    is proven against a throwaway package under ``tmp_path``: the top level and
    the nested module must both appear, the nested one under its dotted name, and
    the render must keep rows in dotted order.
    """
    package = tmp_path / "agy_mcp"
    (package / "cleanup").mkdir(parents=True)
    top_source = '"""Top."""\n\n\ndef top_level() -> None:\n    """No-op."""\n'
    init_source = '"""Subpackage."""\n'
    nested_source = (
        '"""Store IO."""\n\n\n'
        'def load() -> None:\n    """No-op."""\n\n\n'
        'class Store:\n    """No-op."""\n'
    )
    (package / "__init__.py").write_text('"""Package."""\n', encoding="utf-8")
    (package / "top.py").write_text(top_source, encoding="utf-8")
    (package / "cleanup" / "__init__.py").write_text(init_source, encoding="utf-8")
    (package / "cleanup" / "store_io.py").write_text(nested_source, encoding="utf-8")
    monkeypatch.setattr(gen_codemap_index, "MODULES_DIR", package)

    modules = {info.name: info for info in gen_codemap_index.collect_modules()}

    assert list(modules) == [
        "__init__.py",
        "cleanup.__init__.py",
        "cleanup.store_io.py",
        "top.py",
    ]
    nested = modules["cleanup.store_io.py"]
    assert nested.lines == len(nested_source.splitlines())
    assert nested.api == ("`load()`", "`Store`")
    assert modules["cleanup.__init__.py"].lines == len(init_source.splitlines())
    rendered = gen_codemap_index.render_block()
    assert f"| `cleanup.store_io.py` | {nested.lines} | `load()`; `Store` |" in rendered

