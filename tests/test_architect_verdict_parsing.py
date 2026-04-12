"""
Tests for harness property: ARCHITECT VERDICT PARSER IS ROBUST TO REAL MODEL OUTPUT

Why: The architect re-engagement mechanism (failure mode 5 in
docs/whitepaper/architecture/01-reflexive-agent-harness-failure-modes.md)
relies on parsing structured verdicts from real LLM responses. LLMs
produce many variations of "the same" output: clean, markdown-wrapped,
prefixed with explanation, in different cases, with extra whitespace,
etc. The parser must extract VERDICT and NEW_PLAN reliably across all of
these.

This file is Tier 2 of the test plan in tests/PLAN.md.

Note: this test file requires `parse_architect_verdict` exist as a
module-level function in `gemma_forge.harness.ralph`. The first action of
this tier was to extract it from inline code in the inner loop. The
extraction is itself an example of the test discipline: the function
exists because the property "verdict parsing is testable in isolation"
demands it.
"""

from __future__ import annotations

from gemma_forge.harness.ralph import parse_architect_verdict


# =============================================================================
# Property: parser handles every plausible LLM output format
# =============================================================================

class TestVerdictParserFormatRobustness:
    def test_property_clean_format_parses(self):
        text = "VERDICT: ESCALATE\nREASONING: cannot be solved at runtime\nNEW_PLAN:"
        result = parse_architect_verdict(text)
        assert result["verdict"] == "ESCALATE"
        assert result["parsed_cleanly"] is True

    def test_property_continue_verdict_parses(self):
        text = "VERDICT: CONTINUE\nREASONING: making progress\nNEW_PLAN: keep editing aide.conf"
        result = parse_architect_verdict(text)
        assert result["verdict"] == "CONTINUE"
        assert "aide.conf" in result["new_plan"]

    def test_property_pivot_verdict_parses(self):
        text = "VERDICT: PIVOT\nREASONING: wrong tool\nNEW_PLAN: use authselect instead of sed"
        result = parse_architect_verdict(text)
        assert result["verdict"] == "PIVOT"
        assert "authselect" in result["new_plan"]

    def test_property_lowercase_verdict_parses(self):
        text = "verdict: escalate\nreasoning: blah"
        result = parse_architect_verdict(text)
        assert result["verdict"] == "ESCALATE"

    def test_property_extra_whitespace_parses(self):
        text = "  VERDICT:    ESCALATE   \n  REASONING:  ...  "
        result = parse_architect_verdict(text)
        assert result["verdict"] == "ESCALATE"

    def test_property_markdown_code_fence_parses(self):
        text = "```\nVERDICT: ESCALATE\nREASONING: ...\n```"
        result = parse_architect_verdict(text)
        assert result["verdict"] == "ESCALATE"

    def test_property_markdown_with_language_parses(self):
        text = "```yaml\nVERDICT: PIVOT\nNEW_PLAN: try systemd drop-in\n```"
        result = parse_architect_verdict(text)
        assert result["verdict"] == "PIVOT"
        assert "systemd" in result["new_plan"]

    def test_property_prefixed_with_prose_parses(self):
        text = """
After analyzing all 5 attempts, I have decided.

VERDICT: ESCALATE
REASONING: physical partitioning cannot be done at runtime
"""
        result = parse_architect_verdict(text)
        assert result["verdict"] == "ESCALATE"

    def test_property_reordered_fields_parse(self):
        text = """
NEW_PLAN: switch to using authselect
REASONING: sed is too fragile
VERDICT: PIVOT
"""
        result = parse_architect_verdict(text)
        assert result["verdict"] == "PIVOT"
        assert "authselect" in result["new_plan"]

    def test_property_first_verdict_wins_when_multiple(self):
        text = "VERDICT: CONTINUE\n... later ...\nVERDICT: ESCALATE"
        result = parse_architect_verdict(text)
        # First wins — protects against the model debating itself
        assert result["verdict"] == "CONTINUE"

    def test_property_bullet_prefix_parses(self):
        text = "- VERDICT: PIVOT\n- NEW_PLAN: try a different config approach"
        result = parse_architect_verdict(text)
        assert result["verdict"] == "PIVOT"
        assert "config approach" in result["new_plan"]

    def test_property_heading_prefix_parses(self):
        text = "## VERDICT: ESCALATE\n## REASONING: impossible"
        result = parse_architect_verdict(text)
        assert result["verdict"] == "ESCALATE"


# =============================================================================
# Property: parser falls back to CONTINUE on unparseable input
# =============================================================================

class TestVerdictParserFallback:
    def test_property_empty_string_falls_back_to_continue(self):
        result = parse_architect_verdict("")
        assert result["verdict"] == "CONTINUE"
        assert result["parsed_cleanly"] is False

    def test_property_none_input_falls_back(self):
        result = parse_architect_verdict(None)
        assert result["verdict"] == "CONTINUE"
        assert result["parsed_cleanly"] is False

    def test_property_pure_prose_with_no_marker_falls_back(self):
        text = "This is a very thoughtful analysis but I forgot to use the format."
        result = parse_architect_verdict(text)
        assert result["verdict"] == "CONTINUE"
        assert result["parsed_cleanly"] is False

    def test_property_garbage_input_falls_back(self):
        result = parse_architect_verdict("!!!@@@###")
        assert result["verdict"] == "CONTINUE"
        assert result["parsed_cleanly"] is False

    def test_property_verdict_word_with_no_value_falls_back(self):
        text = "VERDICT: BANANA\nREASONING: yummy"
        result = parse_architect_verdict(text)
        # BANANA is not a recognized verdict — fall back
        assert result["verdict"] == "CONTINUE"
        assert result["parsed_cleanly"] is False


# =============================================================================
# Property: NEW_PLAN extraction is bounded
# =============================================================================

class TestNewPlanExtraction:
    def test_property_new_plan_extracted_when_present(self):
        text = "VERDICT: PIVOT\nNEW_PLAN: use the authselect tool"
        result = parse_architect_verdict(text)
        assert result["new_plan"] == "use the authselect tool"

    def test_property_missing_new_plan_returns_empty(self):
        text = "VERDICT: ESCALATE\nREASONING: gave up"
        result = parse_architect_verdict(text)
        assert result["new_plan"] == ""

    def test_property_long_new_plan_is_bounded(self):
        long_plan = "x" * 5000
        text = f"VERDICT: PIVOT\nNEW_PLAN: {long_plan}"
        result = parse_architect_verdict(text)
        assert len(result["new_plan"]) <= 1000

    def test_property_new_plan_strips_markdown_decoration(self):
        text = "VERDICT: PIVOT\nNEW_PLAN: **use authselect** to manage PAM"
        result = parse_architect_verdict(text)
        # Leading/trailing markdown should be stripped
        assert "authselect" in result["new_plan"]
        # We don't strip ALL markdown — just edges. Bold inside is fine.

    def test_property_new_plan_with_empty_value(self):
        text = "VERDICT: ESCALATE\nNEW_PLAN: \nREASONING: nothing to plan"
        result = parse_architect_verdict(text)
        assert result["verdict"] == "ESCALATE"
        # Empty NEW_PLAN with verdict ESCALATE is fine
        assert result["new_plan"] == ""


# =============================================================================
# Property: parser output structure is consistent
# =============================================================================

class TestVerdictParserOutputShape:
    def test_property_always_returns_dict_with_all_keys(self):
        for input_text in [
            "",
            "VERDICT: ESCALATE",
            "garbage",
            "VERDICT: CONTINUE\nNEW_PLAN: x",
            None,
        ]:
            result = parse_architect_verdict(input_text)
            assert isinstance(result, dict)
            assert "verdict" in result
            assert "new_plan" in result
            assert "parsed_cleanly" in result
            assert result["verdict"] in ("CONTINUE", "PIVOT", "ESCALATE")
            assert isinstance(result["new_plan"], str)
            assert isinstance(result["parsed_cleanly"], bool)

    def test_property_parsed_cleanly_flag_is_accurate(self):
        # Clean input → True
        assert parse_architect_verdict("VERDICT: ESCALATE")["parsed_cleanly"] is True
        # Empty → False
        assert parse_architect_verdict("")["parsed_cleanly"] is False
        # Unrecognized verdict word → False
        assert parse_architect_verdict("VERDICT: MAYBE")["parsed_cleanly"] is False
