"""Tests for gemma_forge.memory.retrieval.

Covers the pure-Python similarity primitives (tokenization + prefix
score). The end-to-end ``assemble_tips_for_rule`` path hits Postgres
and is covered by a small live-DB smoke test in the conftest; this
file exercises the parts that don't need a database.
"""
from gemma_forge.memory.retrieval import (
    rule_prefix_similarity,
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
