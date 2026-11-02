"""Wave-2a move proof: the cleaner split must not change any code.

Runs ``scripts/check_moved_code.py`` against the old source text saved in
``tests/fixtures/old_cleanup_*.py.txt`` (captured from ``main`` before the
split), so the gate needs neither git history nor a stored ``ast.dump``. Every
old top-level function, class and constant must exist with an identical
``ast.dump`` (docstrings included, line numbers excluded) in exactly one new
``agy_mcp/cleanup`` module, or verbatim in a shim. Both dumps are computed at
run time with the running interpreter, so the test passes on any supported
Python version.
"""

from scripts.check_moved_code import check


def test_cleaner_split_is_a_pure_move(capsys) -> None:
    """No old cleaner symbol may be missing or changed by the split."""
    report = check()
    output = capsys.readouterr().out

    assert report.total > 0
    assert report.missing == []
    assert report.changed == []
    assert report.ambiguous == []
    assert report.moved + report.kept == report.total
    assert "missing:     0" in output
    assert "changed:     0" in output
