"""Unit tests for the shared edge-protection fixpoint (audit C4).

Both cleaners must keep a job record that a kept record still references, and
must follow the reference chain transitively. The loop used to exist twice
(cleanup_jobs.py and cleanup_fixtures.py); these tests pin the rule itself, so
the two callers cannot drift apart again.
"""

from agy_mcp.cleanup_fixtures import _apply_edge_protection


def test_direct_reference_moves_candidate_to_kept() -> None:
    """A candidate id contained in the kept text must become a kept row."""
    candidates = {"aaa": {"job_id": "aaa"}}
    kept: dict[str, list[str]] = {}
    kept_text = "a kept record mentions aaa in passing"

    result = _apply_edge_protection(
        candidates, kept, kept_text, strings_for=lambda cid: []
    )

    assert candidates == {}
    assert kept == {"aaa": ["referenced by kept job"]}
    assert result == kept_text


def test_reference_cascades_transitively() -> None:
    """Keeping a candidate can expose further ids; the loop must follow the chain."""
    candidates = {"aaa": {}, "bbb": {}, "ccc": {}}
    kept: dict[str, list[str]] = {}
    records = {"aaa": ["bbb"], "bbb": ["ccc"], "ccc": []}
    calls: list[str] = []

    def strings_for(cid: str) -> list[str]:
        calls.append(cid)
        return records[cid]

    result = _apply_edge_protection(
        candidates, kept, "root kept record mentions aaa", strings_for=strings_for
    )

    assert candidates == {}
    assert set(kept) == {"aaa", "bbb", "ccc"}
    assert calls == ["aaa", "bbb", "ccc"]
    assert "bbb" in result and "ccc" in result


def test_unreferenced_candidates_stay_candidates() -> None:
    """Edge protection must not keep rows nothing points at."""
    candidates = {"aaa": {}, "bbb": {}}
    kept: dict[str, list[str]] = {}

    result = _apply_edge_protection(
        candidates, kept, "a kept record mentions aaa", strings_for=lambda cid: []
    )

    assert set(candidates) == {"bbb"}
    assert set(kept) == {"aaa"}
    assert result == "a kept record mentions aaa"


def test_no_candidates_leaves_the_text_untouched() -> None:
    """An empty candidate set is a no-op, which is what the callers rely on."""
    candidates: dict[str, dict] = {}
    kept: dict[str, list[str]] = {}

    result = _apply_edge_protection(
        candidates, kept, "kept text", strings_for=lambda cid: ["unexpected"]
    )

    assert candidates == {}
    assert kept == {}
    assert result == "kept text"


def test_substring_containment_is_deliberately_fail_closed() -> None:
    """The rule is `id in kept_text`, not token equality: over-protecting is safe."""
    candidates = {"abc": {}, "abc-def": {}}
    kept: dict[str, list[str]] = {}

    _apply_edge_protection(
        candidates, kept, "mentions abc-def", strings_for=lambda cid: []
    )

    assert set(kept) == {"abc", "abc-def"}
