"""Regression tests for cross-interpreter stability of reference table generation."""

from scripts import gen_reference


def test_job_fields_table_preserves_parameterized_generic_types() -> None:
    """Ensure dict[str, Any] generic parameters are not stripped to bare 'dict'.

    On Python 3.10, types.GenericAlias is an instance of type, which caused
    _type_label() to return annotation.__name__ ('dict') instead of preserving
    the full parameterized type ('dict[str, Any]').
    """
    rendered = gen_reference.render_block("job-fields")
    assert "dict[str, Any] | null" in rendered
    assert "list[dict[str, Any]]" in rendered
