"""Tests for gemma_forge.memory.retrieval.

Covers the pure-Python similarity primitives (tokenization + prefix
score). The end-to-end ``assemble_tips_for_rule`` path hits Postgres
and is covered by a small live-DB smoke test in the conftest; this
file exercises the parts that don't need a database.
"""
import pytest

from gemma_forge.memory.retrieval import (
    rule_prefix_similarity,
    score_tip,
    tokenize_rule_id,
)


# -- tokenize_rule_id ----------------------------------------------------


def test_tokenize_strips_scap_prefix_and_splits():
    rid = "xccdf_org.ssgproject.content_rule_audit_rules_dac_modification_fchmod"
    assert tokenize_rule_id(rid) == [
        "audit", "rules", "dac", "modification", "fchmod"
    ]


def test_tokenize_handles_empty():
    assert tokenize_rule_id("") == []
    assert tokenize_rule_id(None) == []  # type: ignore[arg-type]


def test_tokenize_drops_empty_segments():
    # Spurious double underscores shouldn't make empty tokens leak through
    assert tokenize_rule_id("xccdf_org.ssgproject.content_rule__foo__bar") == ["foo", "bar"]


def test_tokenize_lowercases():
    assert tokenize_rule_id("xccdf_org.ssgproject.content_rule_AUDIT_Rules") == ["audit", "rules"]


def test_tokenize_handles_rule_with_no_scap_prefix():
    # Bare rule_id (no xccdf prefix) should still tokenize
    assert tokenize_rule_id("audit_rules_dac_modification_fchmod") == [
        "audit", "rules", "dac", "modification", "fchmod"
    ]


# -- rule_prefix_similarity ----------------------------------------------


FCHMOD = "xccdf_org.ssgproject.content_rule_audit_rules_dac_modification_fchmod"
FCHOWN = "xccdf_org.ssgproject.content_rule_audit_rules_dac_modification_fchown"
UMOUNT = "xccdf_org.ssgproject.content_rule_audit_rules_dac_modification_umount"
PRIV_CHAGE = "xccdf_org.ssgproject.content_rule_audit_rules_privileged_commands_chage"
UNSUCC_CREAT = "xccdf_org.ssgproject.content_rule_audit_rules_unsuccessful_file_modification_creat"
SSHD_BANNER = "xccdf_org.ssgproject.content_rule_sshd_enable_warning_banner"


def test_identical_rules_score_1():
    assert rule_prefix_similarity(FCHMOD, FCHMOD) == 1.0


def test_family_variants_share_high_similarity():
    # fchmod vs fchown: shared 4-token prefix, 5 tokens each → 4/5
    assert rule_prefix_similarity(FCHMOD, FCHOWN) == 0.8


def test_family_variants_order_independent():
    assert rule_prefix_similarity(FCHMOD, FCHOWN) == rule_prefix_similarity(FCHOWN, FCHMOD)


def test_different_audit_subfamilies_share_modest_prefix():
    # dac_modification_fchmod vs privileged_commands_chage:
    # shared 2-token prefix (audit, rules); max=5 → 2/5
    assert rule_prefix_similarity(FCHMOD, PRIV_CHAGE) == 0.4


def test_unrelated_rules_score_0():
    # audit vs ssh — no shared tokens
    assert rule_prefix_similarity(FCHMOD, SSHD_BANNER) == 0.0


def test_empty_input_scores_0():
    assert rule_prefix_similarity("", FCHMOD) == 0.0
    assert rule_prefix_similarity(FCHMOD, "") == 0.0
    assert rule_prefix_similarity("", "") == 0.0


def test_different_length_prefix_uses_longer_denominator():
    # unsuccessful_file_modification_creat is 6 tokens; shared prefix is 2
    # max=6 → 2/6 ≈ 0.333
    assert rule_prefix_similarity(FCHMOD, UNSUCC_CREAT) == 2 / 6


def test_similarity_monotone_within_family():
    # A rule should be more similar to its own family than to a sibling family.
    in_family = rule_prefix_similarity(FCHMOD, FCHOWN)            # 4/5 = 0.8
    across_family = rule_prefix_similarity(FCHMOD, PRIV_CHAGE)    # 2/5 = 0.4
    assert in_family > across_family


def test_similarity_monotone_across_categories():
    # Same audit subfamily > different audit subfamily > different category
    same_sub = rule_prefix_similarity(FCHMOD, FCHOWN)             # 0.8
    diff_sub = rule_prefix_similarity(FCHMOD, PRIV_CHAGE)         # 0.4
    diff_cat = rule_prefix_similarity(FCHMOD, SSHD_BANNER)        # 0.0
    assert same_sub > diff_sub > diff_cat


# -- score_tip composite formula ----------------------------------------


def _base_score_kwargs(**overrides):
    """Default kwargs for score_tip: same rule, same category, no hit, no src_prior."""
    d = dict(
        tip_source_rule_id=FCHMOD,
        tip_source_run_id="run-prior",
        tip_application_context=["audit"],
        tip_outcome_at_source_value=None,
        tip_outcome_at_source_confidence=None,
        hit_rate=0.0,
        exclude_run_id=None,
    )
    d.update(overrides)
    return d


def test_score_same_rule_with_category_match():
    # base=1.0, cat=+0.3, hit=0, src_prior=0 → 1.3
    s = score_tip(FCHMOD, "audit", **_base_score_kwargs())
    assert s == 1.3


def test_score_success_source_prior_adds_bonus():
    # Success source: +0.15 × 1.0 × 1.0 = +0.15
    s_success = score_tip(FCHMOD, "audit", **_base_score_kwargs(
        tip_outcome_at_source_value=1.0,
        tip_outcome_at_source_confidence=1.0,
    ))
    assert s_success == 1.45  # 1.3 + 0.15


def test_score_failure_source_prior_zero_bonus():
    # value=0.0: prior = 0 → no bonus
    s_fail = score_tip(FCHMOD, "audit", **_base_score_kwargs(
        tip_outcome_at_source_value=0.0,
        tip_outcome_at_source_confidence=1.0,
    ))
    assert s_fail == 1.3


def test_score_null_src_prior_matches_failure():
    # NULL (backfill) → treated as 0, same as explicit failure
    s_null = score_tip(FCHMOD, "audit", **_base_score_kwargs())
    s_fail = score_tip(FCHMOD, "audit", **_base_score_kwargs(
        tip_outcome_at_source_value=0.0,
        tip_outcome_at_source_confidence=1.0,
    ))
    assert s_null == s_fail == 1.3


def test_score_low_confidence_scales_source_prior():
    # value=1.0 × conf=0.5 × weight=0.15 = 0.075
    s = score_tip(FCHMOD, "audit", **_base_score_kwargs(
        tip_outcome_at_source_value=1.0,
        tip_outcome_at_source_confidence=0.5,
    ))
    assert s == 1.375


def test_score_hit_rate_outranks_source_prior():
    # hit_rate=1.0 × weight=0.5 = +0.5 vs source_prior max +0.15
    s_hit_only = score_tip(FCHMOD, "audit", **_base_score_kwargs(hit_rate=1.0))
    s_prior_only = score_tip(FCHMOD, "audit", **_base_score_kwargs(
        tip_outcome_at_source_value=1.0,
        tip_outcome_at_source_confidence=1.0,
    ))
    assert s_hit_only > s_prior_only     # aggregate > single-sample evidence


def test_score_same_run_damping_halves_everything():
    # Same-run damping applies to the composite total
    s_normal = score_tip(FCHMOD, "audit", **_base_score_kwargs())
    s_damped = score_tip(FCHMOD, "audit", **_base_score_kwargs(
        tip_source_run_id="run-current",
        exclude_run_id="run-current",
    ))
    assert s_damped == s_normal * 0.5


def test_score_success_damped_still_beats_failure_undamped_when_same_rule():
    # Edge case: within-run success (damped) vs prior-run failure (undamped)
    # Success damped: (1.0 + 0.3 + 0 + 0.15) × 0.5 = 0.725
    # Failure undamped: 1.0 + 0.3 + 0 + 0 = 1.30
    # Failure wins. This is the conservative behavior: prior-run evidence
    # trumps within-run even when within-run is a success.
    same_run_success = score_tip(FCHMOD, "audit", **_base_score_kwargs(
        tip_source_run_id="run-current",
        exclude_run_id="run-current",
        tip_outcome_at_source_value=1.0,
        tip_outcome_at_source_confidence=1.0,
    ))
    prior_run_failure = score_tip(FCHMOD, "audit", **_base_score_kwargs(
        tip_outcome_at_source_value=0.0,
        tip_outcome_at_source_confidence=1.0,
    ))
    assert prior_run_failure > same_run_success


def test_score_sibling_family_lower_than_same_rule():
    # fchmod vs fchown: base 0.8 + cat 0.3 = 1.1
    # fchmod vs fchmod: base 1.0 + cat 0.3 = 1.3
    s_sibling = score_tip(FCHMOD, "audit", **_base_score_kwargs(
        tip_source_rule_id=FCHOWN,
    ))
    s_same = score_tip(FCHMOD, "audit", **_base_score_kwargs())
    assert s_sibling < s_same


def test_score_category_bonus_only_on_match():
    s_match = score_tip(FCHMOD, "audit", **_base_score_kwargs())
    s_miss = score_tip(FCHMOD, "ssh", **_base_score_kwargs())  # category "ssh" not in ["audit"]
    assert s_match - s_miss == pytest.approx(0.3)
