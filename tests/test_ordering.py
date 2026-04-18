"""Ordering-constraint unit tests.

Smoke-level coverage of the skill-declared, harness-enforced ordering
mechanism. The STIG manifest declares audit_rules_immutable should
defer until audit is nearly complete; these tests verify both states
(constraint active → deferred; constraint released → visible).
"""
from __future__ import annotations

from pathlib import Path

from gemma_forge.harness.ordering import (
    OrderingConstraint,
    filter_deferred,
    is_deferred,
    load_constraints_from_manifest,
)


def _immutable_constraint() -> OrderingConstraint:
    return OrderingConstraint(
        rule_id="xccdf_org.ssgproject.content_rule_audit_rules_immutable",
        predicate="category_nearly_complete",
        params={"category": "audit", "remaining_lte": 1},
        reason="test",
    )


def test_category_nearly_complete_active_when_many_siblings():
    """Many audit rules left → constraint is active → immutable deferred."""
    c = _immutable_constraint()
    rules = [
        {"rule_id": c.rule_id, "category": "audit"},
        {"rule_id": "audit_rules_dac_modification_chmod", "category": "audit"},
        {"rule_id": "audit_rules_dac_modification_chown", "category": "audit"},
        {"rule_id": "kernel_module_cramfs_disabled", "category": "kernel"},
    ]
    assert is_deferred(c, rules) is True


def test_category_nearly_complete_releases_at_threshold():
    """Immutable alone in category → count ≤ remaining_lte → constraint releases."""
    c = _immutable_constraint()
    rules = [
        {"rule_id": c.rule_id, "category": "audit"},
        {"rule_id": "kernel_module_cramfs_disabled", "category": "kernel"},
    ]
    assert is_deferred(c, rules) is False


def test_filter_deferred_splits_correctly():
    """filter_deferred returns (visible, deferred_with_reasons) as documented."""
    c = _immutable_constraint()
    rules = [
        {"rule_id": c.rule_id, "category": "audit"},
        {"rule_id": "audit_rules_dac_modification_chmod", "category": "audit"},
        {"rule_id": "kernel_module_cramfs_disabled", "category": "kernel"},
    ]
    visible, deferred = filter_deferred(rules, [c])
    assert len(visible) == 2
    assert len(deferred) == 1
    assert deferred[0][0]["rule_id"] == c.rule_id
    assert deferred[0][1].predicate == "category_nearly_complete"


def test_filter_deferred_no_constraints_is_identity():
    """Empty constraint list returns failing_rules unchanged and no deferred."""
    rules = [{"rule_id": "x", "category": "audit"}]
    visible, deferred = filter_deferred(rules, [])
    assert visible == rules
    assert deferred == []


def test_load_from_stig_manifest_roundtrip():
    """The actual STIG skill.yaml loads into the expected shape."""
    skill_dir = Path(__file__).parent.parent / "skills" / "stig-rhel9"
    constraints = load_constraints_from_manifest(skill_dir)
    assert len(constraints) >= 1
    immutable = next(
        (c for c in constraints if "audit_rules_immutable" in c.rule_id), None,
    )
    assert immutable is not None
    assert immutable.predicate == "category_nearly_complete"
    assert immutable.params["category"] == "audit"
    assert immutable.params["remaining_lte"] == 1


def test_load_from_missing_manifest_returns_empty():
    """A directory without skill.yaml returns [], does not raise."""
    result = load_constraints_from_manifest(Path("/tmp/does-not-exist"))
    assert result == []


def test_unknown_predicate_is_skipped_not_crashing(tmp_path: Path):
    """Malformed manifest with unknown predicate logs warning, returns empty."""
    (tmp_path / "skill.yaml").write_text("""
ordering_constraints:
  - rule_id: "x"
    defer_until:
      predicate: nonexistent_predicate
      some_param: value
    reason: "test"
""")
    result = load_constraints_from_manifest(tmp_path)
    assert result == []
