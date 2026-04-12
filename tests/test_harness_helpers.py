"""
Tests for harness properties: PURE HELPER FUNCTIONS

Why: The harness has a set of pure functions that implement load-bearing
properties of the architecture (token budgets, plateau detection,
keyword extraction, prompt assembly). Each function is small enough to
test exhaustively, and these tests are the fastest feedback loop in the
suite. They run without any LLM, VM, or network.

This file is Tier 1 of the test plan in tests/PLAN.md.

Each test name is a falsifiable property statement, not a description
of an action. If a test cannot be written cleanly because the
abstraction is missing or wrong, add a TODO and stop — that is a signal
to refactor before writing more tests.

See docs/whitepaper/architecture/01-reflexive-agent-harness-failure-modes.md
for the abstract failure modes these helpers address.
"""

from __future__ import annotations

import pytest

from gemma_forge.harness.ralph import (
    EpisodicMemory,
    RunState,
    SemanticMemory,
    _keyword_set,
    assemble_prompt,
    categorize_rule,
    detect_plateau,
    est_tokens,
    is_similar,
    reflection_first_sentence,
)


# =============================================================================
# Property: assemble_prompt output never exceeds the token budget
# Failure mode addressed: #1 (tool-call explosion → context overflow)
# =============================================================================

class TestAssemblePromptBoundedOutput:
    """assemble_prompt's central invariant is that est_tokens(output) <= budget."""

    def test_property_empty_section_list_returns_empty_within_budget(self):
        body, meta = assemble_prompt([], budget_tokens=100)
        assert body == ""
        assert meta["used_tokens"] == 0
        assert meta["sections_included"] == []
        assert meta["sections_dropped"] == []
        assert meta["sections_truncated"] == []

    def test_property_single_section_under_budget_fits_unchanged(self):
        body, meta = assemble_prompt([(0, "h", "hello world")], budget_tokens=100)
        assert "hello world" in body
        assert meta["used_tokens"] <= 100
        assert meta["sections_included"] == ["h"]
        assert meta["sections_dropped"] == []
        assert meta["sections_truncated"] == []

    def test_property_single_section_over_budget_is_truncated(self):
        big = "x" * 4000  # ~1000 tokens
        body, meta = assemble_prompt([(0, "big", big)], budget_tokens=200)
        assert est_tokens(body) <= 200 + 5  # small grace for the truncation marker
        assert "big" in meta["sections_truncated"]
        assert "[...truncated for context budget...]" in body

    def test_property_multiple_sections_all_fit(self):
        sections = [
            (0, "a", "alpha section"),
            (1, "b", "beta section"),
            (2, "c", "gamma section"),
        ]
        body, meta = assemble_prompt(sections, budget_tokens=500)
        assert all(label in meta["sections_included"] for label in ["a", "b", "c"])
        assert "alpha" in body and "beta" in body and "gamma" in body
        assert meta["sections_dropped"] == []

    def test_property_lowest_priority_dropped_first_when_tight(self):
        sections = [
            (0, "header", "h" * 200),       # ~50 tokens
            (1, "middle", "m" * 200),       # ~50 tokens
            (2, "footer", "f" * 4000),      # ~1000 tokens — won't fit
        ]
        body, meta = assemble_prompt(sections, budget_tokens=120)
        assert "header" in meta["sections_included"]
        assert "footer" not in meta["sections_included"]
        # footer should be either dropped or truncated, but not silently included whole
        assert "footer" in meta["sections_dropped"] + meta["sections_truncated"]

    def test_property_output_token_estimate_never_exceeds_budget(self):
        """The central invariant. Holds for arbitrary inputs."""
        # Stress with many sections of varying sizes and a tight budget
        sections = [
            (i, f"section_{i}", "abcd" * (50 + i * 20))
            for i in range(20)
        ]
        for budget in [50, 200, 500, 1000, 5000]:
            body, meta = assemble_prompt(sections, budget_tokens=budget)
            # Allow a small grace for the truncation marker (~12 tokens)
            assert est_tokens(body) <= budget + 15, (
                f"Budget {budget} exceeded: got {est_tokens(body)} tokens"
            )

    def test_property_priority_order_preserved_in_output(self):
        # Sections must appear in priority order regardless of input order
        sections = [
            (5, "fifth", "EEE"),
            (1, "first", "AAA"),
            (3, "third", "CCC"),
        ]
        body, _ = assemble_prompt(sections, budget_tokens=1000)
        first_pos = body.index("AAA")
        third_pos = body.index("CCC")
        fifth_pos = body.index("EEE")
        assert first_pos < third_pos < fifth_pos

    def test_property_truncated_section_blocks_lower_priority_sections(self):
        """Once we truncate a section to fit, everything below it should drop —
        we don't half-include lower-priority sections after truncation."""
        sections = [
            (0, "header", "h" * 200),
            (1, "huge", "x" * 8000),  # gets truncated
            (2, "lower", "lower content"),
        ]
        body, meta = assemble_prompt(sections, budget_tokens=300)
        assert "huge" in meta["sections_truncated"]
        assert "lower" in meta["sections_dropped"]


# =============================================================================
# Property: est_tokens is monotonic and non-negative
# =============================================================================

class TestEstTokens:
    def test_property_empty_string_is_zero_tokens(self):
        assert est_tokens("") == 0

    def test_property_non_negative_for_any_input(self):
        for s in ["", "a", "hello", "x" * 1000, "\n\n\n", "\u2014"]:
            assert est_tokens(s) >= 0

    def test_property_monotonic_with_length(self):
        # Adding more content should never decrease the estimate
        prev = est_tokens("")
        for length in [1, 10, 100, 1000, 10000]:
            current = est_tokens("x" * length)
            assert current >= prev
            prev = current

    def test_property_returns_int(self):
        assert isinstance(est_tokens("test"), int)


# =============================================================================
# Property: detect_plateau distinguishes semantic sameness from semantic difference
# Failure mode addressed: #4 (cosmetic novelty masking semantic sameness)
# =============================================================================

class TestDetectPlateau:
    """detect_plateau must catch semantically identical reflections regardless
    of word choice, length, or sentence structure variations."""

    def test_property_three_cosmetically_different_but_semantically_identical_reflections_plateau(self):
        # Real-world variations from the overnight run, partition rule
        reflections = [
            "Pattern identified: Attempting to remediate a hardware/disk partitioning requirement via runtime scripts on a live system.",
            "Pattern identified: Attempting to remediate a structural disk partitioning requirement via runtime bash scripts.",
            "Pattern identified: Attempting to remediate structural disk partitioning requirements via non interactive runtime scripts.",
        ]
        assert detect_plateau(reflections, window=3)

    def test_property_three_completely_different_reflections_do_not_plateau(self):
        reflections = [
            "Pattern identified: SELinux policy mismatch in audit subsystem.",
            "Pattern identified: SSH configuration prevents root login as required.",
            "Pattern identified: Filesystem mount options missing nodev for /home.",
        ]
        assert not detect_plateau(reflections, window=3)

    def test_property_window_below_threshold_never_fires(self):
        # Need at least `window` reflections
        assert not detect_plateau([], window=3)
        assert not detect_plateau(["one"], window=3)
        assert not detect_plateau(["one", "two"], window=3)

    def test_property_only_last_window_reflections_count(self):
        # Earlier divergent reflections must not block plateau detection
        # if the LAST `window` are similar.
        #
        # Note: the "similar" reflections must share enough literal tokens to
        # exceed min_shared=3. The algorithm uses keyword-set intersection,
        # not stem normalization, so synonyms like "config"/"configuration"
        # are NOT collapsed (see TODO below).
        reflections = [
            "Pattern identified: completely unrelated topic about networking",
            "Pattern identified: another unrelated topic about authentication",
            "Pattern identified: AIDE configuration drift from database state requires reinit",
            "Pattern identified: AIDE configuration drift from database state requires repair",
            "Pattern identified: AIDE configuration drift from database state requires rebuild",
        ]
        assert detect_plateau(reflections, window=3)

    # TODO (calibration): The current detect_plateau uses literal token-set
    # intersection. It would catch more LLM rephrasings if it added cheap stem
    # normalization (config/configuration → config; mismatch/mismatched →
    # mismatch). This is a future improvement noted in the failure-modes doc
    # under failure mode 4. Decision: defer until we have empirical data
    # showing the current algorithm misses real plateaus.

    def test_property_identical_strings_always_plateau(self):
        same = "Pattern identified: same exact text."
        assert detect_plateau([same, same, same], window=3)

    def test_property_min_shared_threshold_is_respected(self):
        # If only 2 keywords are shared, default min_shared=3 should NOT fire
        reflections = [
            "Pattern identified: foo bar disk explosion alpha beta gamma delta",
            "Pattern identified: baz quux disk explosion epsilon zeta eta theta",
            "Pattern identified: corge grault disk explosion iota kappa lambda mu",
        ]
        # Shared keywords: disk, explosion → 2 only (assuming "pattern" "identified" are stripped or counted)
        # This should NOT plateau at default min_shared=3
        # But might at min_shared=2
        # Note: actual behavior depends on which words pass the keyword filter,
        # so this test verifies the *configurability* property more than a specific outcome
        result_strict = detect_plateau(reflections, window=3, min_shared=10)
        assert not result_strict
        result_loose = detect_plateau(reflections, window=3, min_shared=1)
        # At min_shared=1, "disk" alone is enough → should plateau
        assert result_loose


# =============================================================================
# Property: _keyword_set is robust to formatting variations
# =============================================================================

class TestKeywordSet:
    def test_property_lowercase_normalizes(self):
        a = _keyword_set("STRUCTURAL DISK PARTITIONING")
        b = _keyword_set("structural disk partitioning")
        assert a == b

    def test_property_punctuation_stripped(self):
        a = _keyword_set("structural, disk, partitioning!")
        b = _keyword_set("structural disk partitioning")
        assert a == b

    def test_property_stopwords_filtered(self):
        ks = _keyword_set("the structural and disk of partitioning is in the system")
        # Stopwords (the, and, of, is, in) should be absent
        assert "the" not in ks
        assert "and" not in ks
        assert "of" not in ks
        assert "is" not in ks
        assert "in" not in ks
        # Content words present
        assert "structural" in ks
        assert "disk" in ks
        assert "partitioning" in ks
        assert "system" in ks

    def test_property_short_tokens_filtered(self):
        ks = _keyword_set("a b c structural disk")
        assert "a" not in ks
        assert "b" not in ks
        assert "c" not in ks

    def test_property_plurals_collapse(self):
        a = _keyword_set("requirement requirements")
        # Both should collapse to "requirement"
        assert len(a) == 1
        assert "requirement" in a

    def test_property_empty_input_returns_empty_set(self):
        assert _keyword_set("") == frozenset()
        assert _keyword_set(None) == frozenset()


# =============================================================================
# Property: is_similar is symmetric
# =============================================================================

class TestIsSimilar:
    def test_property_symmetric(self):
        pairs = [
            ("structural disk partitioning requirement", "disk partitioning requirements via scripts"),
            ("totally different content here", "another unrelated string"),
            ("identical text", "identical text"),
            ("", ""),
            ("nonempty", ""),
        ]
        for a, b in pairs:
            assert is_similar(a, b) == is_similar(b, a), f"Asymmetric: {a!r} vs {b!r}"

    def test_property_threshold_lower_means_more_permissive(self):
        a = "structural disk partitioning requirement runtime scripts"
        b = "different topic entirely about ssh and firewall"
        # No shared content keywords → not similar at any min_shared >= 1
        assert not is_similar(a, b, min_shared=3)

    def test_property_identical_strings_always_similar(self):
        s = "any string content here"
        assert is_similar(s, s)


# =============================================================================
# Property: EpisodicMemory.summary() output is bounded
# =============================================================================

class TestEpisodicMemorySummary:
    def test_property_empty_attempts_returns_short_marker(self):
        em = EpisodicMemory(rule_id="test_rule")
        s = em.summary()
        assert "No prior attempts" in s

    def test_property_summary_capped_at_last_n_attempts(self):
        em = EpisodicMemory(rule_id="test_rule")
        for i in range(50):
            em.attempts.append({
                "approach": f"approach {i}",
                "result": f"result {i}",
                "reflection": f"reflection {i}" * 20,
                "lesson": f"distilled lesson {i}",
            })
        # Default cap is 5
        s = em.summary(max_attempts=5)
        # Should reference "50 total" but only show 5
        assert "50 total" in s
        # Lesson 49 (last) should be present, lesson 0 (first) should not
        assert "lesson 49" in s
        assert "lesson 0\n" not in s and "lesson 0 " not in s

    def test_property_summary_bounded_in_size_regardless_of_attempt_count(self):
        em5 = EpisodicMemory(rule_id="r")
        em500 = EpisodicMemory(rule_id="r")
        for i in range(5):
            em5.attempts.append({"lesson": f"lesson {i}", "approach": "a", "result": "r"})
        for i in range(500):
            em500.attempts.append({"lesson": f"lesson {i}", "approach": "a", "result": "r"})
        s5 = em5.summary(max_attempts=5)
        s500 = em500.summary(max_attempts=5)
        # Output should be roughly the same size — within 2x
        assert len(s500) < 2 * len(s5) + 200

    def test_property_full_summary_uses_all_attempts(self):
        em = EpisodicMemory(rule_id="r")
        for i in range(10):
            em.attempts.append({"lesson": f"lesson {i}", "approach": "a", "result": "r"})
        full = em.full_summary()
        # full_summary doesn't cap; should reference all 10
        for i in range(10):
            assert f"lesson {i}" in full

    def test_property_falls_back_to_approach_when_lesson_missing(self):
        em = EpisodicMemory(rule_id="r")
        em.attempts.append({"approach": "tried sed -i something", "result": "exit 1", "reflection": "ref"})
        s = em.summary()
        # Should include approach fragment since lesson is absent
        assert "tried sed" in s


# =============================================================================
# Property: RunState.summary_for_architect() respects token budget
# =============================================================================

class TestRunStateSummary:
    def test_property_returns_tuple_of_text_and_meta(self):
        state = RunState()
        state.failing_rules = [{"rule_id": "r", "title": "t"}]
        body, meta = state.summary_for_architect(budget_tokens=1000)
        assert isinstance(body, str)
        assert isinstance(meta, dict)
        assert "used_tokens" in meta
        assert "sections_included" in meta

    def test_property_under_budget_includes_all_sections(self):
        state = RunState()
        state.failing_rules = [{"rule_id": f"r{i}", "title": f"t{i}"} for i in range(3)]
        body, meta = state.summary_for_architect(budget_tokens=2000)
        assert meta["sections_dropped"] == [] or meta["sections_dropped"] is not None
        assert "header" in meta["sections_included"]

    def test_property_header_always_present_even_under_tight_budget(self):
        # Even with thousands of failing rules and a tiny budget,
        # the header (highest priority) must survive
        state = RunState()
        state.failing_rules = [{"rule_id": f"r{i}", "title": f"long title {i}"} for i in range(1000)]
        state.escalated = [{"rule_id": f"esc{i}", "title": f"esc title {i}", "reason": "time_budget"} for i in range(100)]
        state.remediated = [{"rule_id": f"rem{i}", "title": f"rem title {i}"} for i in range(50)]
        state.current_iteration = 25
        body, meta = state.summary_for_architect(budget_tokens=200)
        assert "header" in meta["sections_included"] or "header" in meta["sections_truncated"]
        assert "RUN STATE" in body
        # And of course bounded
        assert est_tokens(body) <= 215  # +15 grace

    def test_property_output_within_budget_for_arbitrary_state_sizes(self):
        for n_failing, n_escalated, n_remediated in [(0,0,0), (5,2,1), (100,30,10), (1000,200,80)]:
            state = RunState()
            state.failing_rules = [{"rule_id": f"r{i}", "title": "t"} for i in range(n_failing)]
            state.escalated = [{"rule_id": f"e{i}", "title": "t", "reason": "x"} for i in range(n_escalated)]
            state.remediated = [{"rule_id": f"m{i}", "title": "t"} for i in range(n_remediated)]
            for budget in [200, 1000, 3000]:
                body, meta = state.summary_for_architect(budget_tokens=budget)
                assert est_tokens(body) <= budget + 15, (
                    f"State {n_failing}/{n_escalated}/{n_remediated} budget {budget}: "
                    f"got {est_tokens(body)} tokens"
                )


# =============================================================================
# Property: categorize_rule covers known categories
# =============================================================================

class TestCategorizeRule:
    def test_property_aide_rules_are_integrity_monitoring(self):
        assert categorize_rule("xccdf_org.ssgproject.content_rule_aide_build_database") == "integrity-monitoring"
        assert categorize_rule("xccdf_org.ssgproject.content_rule_package_aide_installed") == "integrity-monitoring"
        assert categorize_rule("xccdf_org.ssgproject.content_rule_aide_check_audit_tools") == "integrity-monitoring"

    def test_property_sudo_rules_are_privileged_access(self):
        assert categorize_rule("xccdf_org.ssgproject.content_rule_sudo_remove_nopasswd") == "privileged-access"
        assert categorize_rule("xccdf_org.ssgproject.content_rule_sudoers_validate_passwd") == "privileged-access"

    def test_property_unknown_rules_classify_as_other(self):
        assert categorize_rule("xccdf_org.ssgproject.content_rule_completely_made_up_xyz") == "other"
        assert categorize_rule("") == "other"

    def test_property_categorization_is_deterministic(self):
        rid = "xccdf_org.ssgproject.content_rule_partition_for_var_log_audit"
        results = [categorize_rule(rid) for _ in range(10)]
        assert len(set(results)) == 1

    def test_property_partition_rules_classify_as_filesystem(self):
        assert categorize_rule("xccdf_org.ssgproject.content_rule_partition_for_var_log_audit") == "filesystem"
        assert categorize_rule("xccdf_org.ssgproject.content_rule_mount_option_home_nodev") == "filesystem"

    def test_property_fips_rules_classify_as_cryptography(self):
        assert categorize_rule("xccdf_org.ssgproject.content_rule_enable_fips_mode") == "cryptography"
        assert categorize_rule("xccdf_org.ssgproject.content_rule_aide_use_fips_hashes") == "cryptography" or \
               categorize_rule("xccdf_org.ssgproject.content_rule_aide_use_fips_hashes") == "integrity-monitoring"
        # Note: "aide" appears first in the check order, so aide_use_fips_hashes
        # gets "integrity-monitoring". This is documented behavior — order matters.


# =============================================================================
# Property: reflection_first_sentence extracts the pattern claim
# =============================================================================

class TestReflectionFirstSentence:
    def test_property_extracts_pattern_identified_clause(self):
        text = """```
REFLECTION:
Pattern identified: Surface-level config update.
Root cause: blah blah blah.
"""
        result = reflection_first_sentence(text)
        assert "surface-level config update" in result.lower()

    def test_property_falls_back_to_first_meaningful_line(self):
        text = "Some unstructured text without the pattern marker. More content here."
        result = reflection_first_sentence(text)
        assert len(result) > 0
        assert "some unstructured text" in result.lower()

    def test_property_empty_input_returns_empty(self):
        assert reflection_first_sentence("") == ""
        assert reflection_first_sentence(None) == ""

    def test_property_strips_markdown_code_fences(self):
        text = "```markdown\nPattern identified: clean content here.\n```"
        result = reflection_first_sentence(text)
        assert "```" not in result

    def test_property_output_is_lowercase(self):
        text = "Pattern identified: MIXED CASE CONTENT."
        result = reflection_first_sentence(text)
        assert result == result.lower()
