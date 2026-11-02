"""ITEM 3: the standing preamble must teach the scratchpad rules.

The rule text is the contract the agent sees on every fresh run and resume.
These assertions pin each rule, not the surrounding wording.
"""

from agy_mcp.util import load_preamble

SCRATCHPAD_RULES = [
    ".agy/NOTES.md",
    "only after the work it describes is actually done",
    "Never tick a box for work you did not do",
    "fill in .agy/REPORT.md",
    '"Not done / skipped"',
    '"Open issues"',
    'even if the answer is "none"',
    "Never claim that tests passed",
]


def test_preamble_contains_each_scratchpad_rule() -> None:
    preamble = load_preamble()
    missing = [rule for rule in SCRATCHPAD_RULES if rule not in preamble]
    assert not missing, f"preamble is missing scratchpad rules: {missing}"


def test_preamble_keeps_existing_safety_rules() -> None:
    """The new rules are additive; the security and verification rules stay."""
    preamble = load_preamble()
    assert "Never create or modify .agents/" in preamble
    assert "Never invoke the ask_question tool" in preamble
    assert "Do not start subagents" in preamble
    assert "NEEDS_DECISION:" in preamble
