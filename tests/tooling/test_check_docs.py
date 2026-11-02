"""Tests for the documentation checker and the reference-table generator."""

from pathlib import Path

from scripts import check_docs, gen_reference


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_relative_links_resolve_from_the_page(tmp_path: Path) -> None:
    """A relative link is resolved from the page directory, not the repo root."""
    root_page = _write(tmp_path / "root.md", "# Root\n")
    target = _write(tmp_path / "sub" / "target.md", "# Target\n")
    page = _write(
        tmp_path / "sub" / "page.md",
        "\n".join(
            [
                "# Page",
                "[ok](target.md)",
                "[up](../root.md)",
                "[web](https://example.com/x)",
                "[anchor](#section)",
                "[bad](missing.md)",
            ]
        ),
    )

    findings = check_docs.broken_links([page])

    assert len(findings) == 1
    assert "missing.md" in findings[0]
    assert root_page.is_file() and target.is_file()


def test_pages_missing_sources_and_bad_paths(tmp_path: Path) -> None:
    """The Sources section is required, and each path in it must exist."""
    no_sources = _write(tmp_path / "no-sources.md", "# Page\n\nBody.\n")
    good = _write(
        tmp_path / "good.md",
        "# Page\n\nBody.\n\n## Sources\n\n- `README.md` — real file.\n",
    )
    bad = _write(
        tmp_path / "bad.md",
        "# Page\n\nBody.\n\n## Sources\n\n- `src/agy_mcp/does-not-exist.py`\n",
    )

    assert check_docs.pages_missing_sources([no_sources, good, bad]) == [
        f"missing Sources: {no_sources}"
    ]
    assert check_docs.bad_source_paths([no_sources, good]) == []
    findings = check_docs.bad_source_paths([bad])
    assert len(findings) == 1
    assert "does-not-exist.py" in findings[0]


def test_sources_heading_inside_a_fence_is_not_a_section(tmp_path: Path) -> None:
    """The page template shows a fenced `## Sources`; it is an example, not a section."""
    page = _write(
        tmp_path / "template.md",
        "\n".join(
            [
                "# Page",
                "",
                "```markdown",
                "## Sources",
                "- `src/agy_mcp/example.py`",
                "```",
                "",
                "Body.",
                "",
                "## Sources",
                "",
                "- `README.md` — the real source.",
                "",
            ]
        ),
    )

    assert check_docs.pages_missing_sources([page]) == []
    assert check_docs.bad_source_paths([page]) == []


def test_sources_without_a_repo_path_is_a_finding(tmp_path: Path) -> None:
    page = _write(
        tmp_path / "empty-sources.md",
        "# Page\n\nBody.\n\n## Sources\n\nNothing here.\n",
    )

    findings = check_docs.bad_source_paths([page])

    assert findings == [f"Sources has no repository path: {page}"]


def test_every_error_code_is_documented() -> None:
    assert check_docs.missing_error_codes() == []


def test_every_environment_variable_is_documented() -> None:
    assert check_docs.missing_env_vars() == []


def test_every_config_key_is_documented() -> None:
    assert check_docs.missing_config_keys() == []


def test_every_tool_job_field_and_event_is_documented() -> None:
    assert check_docs.missing_tools() == []
    assert check_docs.missing_job_fields() == []
    assert check_docs.missing_events() == []


def test_generated_blocks_are_fresh() -> None:
    assert gen_reference.check_all() == []


def test_stale_generated_block_is_detected() -> None:
    begin = gen_reference.BEGIN.format(key="error-codes")
    end = gen_reference.END.format(key="error-codes")
    stale = f"{begin}\n| stale |\n{end}\n"

    assert gen_reference.check_block(stale, "error-codes") is False
    fresh = gen_reference.replace_block(stale, "error-codes")
    assert gen_reference.check_block(fresh, "error-codes") is True


def test_full_check_passes_on_the_repository(capsys) -> None:
    exit_code = check_docs.main([])
    output = capsys.readouterr().out

    assert exit_code == 0, output
    assert "docs check: OK" in output


def test_report_mode_returns_zero_and_fail_mode_returns_one(monkeypatch, capsys) -> None:
    monkeypatch.setattr(check_docs, "collect_findings", lambda: ["synthetic finding"])

    assert check_docs.main(["--report"]) == 0
    assert check_docs.main([]) == 1
    output = capsys.readouterr().out
    assert "synthetic finding" in output
