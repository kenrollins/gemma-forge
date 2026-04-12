"""Property tests for v4 interfaces and evaluation triage.

These tests verify:
  - EvalResult and FailureMode classification properties
  - TriageState scanner-gap detection
  - WorkItem contract
  - Interface protocol satisfaction (STIG runtime)
"""

import pytest

from gemma_forge.harness.interfaces import (
    EvalResult,
    FailureMode,
    WorkItem,
    Checkpoint,
    Evaluator,
    Executor,
    WorkQueue,
    SkillRuntime,
)
from gemma_forge.harness.ralph import TriageState


# =============================================================================
# Property: EvalResult always has required fields
# =============================================================================

class TestEvalResult:
    def test_property_default_is_clean_failure(self):
        r = EvalResult(passed=False)
        assert r.failure_mode == FailureMode.CLEAN_FAILURE
        assert r.summary == ""
        assert r.signals == {}

    def test_property_passed_result_has_no_failure_mode_implications(self):
        r = EvalResult(passed=True, summary="all good")
        assert r.passed is True
        # failure_mode is present but irrelevant when passed=True
        assert r.failure_mode == FailureMode.CLEAN_FAILURE

    def test_property_signals_are_pass_through(self):
        r = EvalResult(
            passed=False,
            failure_mode=FailureMode.EVALUATOR_GAP,
            signals={"health_ok": True, "rule_ok": False, "custom_field": 42},
        )
        assert r.signals["health_ok"] is True
        assert r.signals["custom_field"] == 42

    def test_property_all_failure_modes_are_distinct(self):
        modes = list(FailureMode)
        assert len(modes) == len(set(m.value for m in modes))
        assert len(modes) == 4  # HEALTH_FAILURE, EVALUATOR_GAP, FALSE_NEGATIVE, CLEAN_FAILURE


# =============================================================================
# Property: TriageState detects scanner gaps correctly
# =============================================================================

class TestTriageState:
    def test_property_no_gap_initially(self):
        t = TriageState()
        assert not t.is_scanner_gap(threshold=3)
        assert t.evaluator_gap_count == 0

    def test_property_gap_after_threshold_distinct_approaches(self):
        t = TriageState()
        t.record_gap("approach A: sed -i config file")
        t.record_gap("approach B: python rewrite config")
        t.record_gap("approach C: echo overwrite config")
        assert t.is_scanner_gap(threshold=3)

    def test_property_repeated_same_approach_does_not_trigger(self):
        """Same approach text (first 80 chars) repeated 3x should not count
        as 3 distinct approaches."""
        t = TriageState()
        t.record_gap("sed -i 's/old/new/g' /etc/security/limits.conf")
        t.record_gap("sed -i 's/old/new/g' /etc/security/limits.conf")
        t.record_gap("sed -i 's/old/new/g' /etc/security/limits.conf")
        assert t.evaluator_gap_count == 3  # 3 gaps recorded
        assert len(t.distinct_approaches_in_gap) == 1  # but only 1 distinct
        assert not t.is_scanner_gap(threshold=3)  # so no scanner gap

    def test_property_threshold_is_configurable(self):
        t = TriageState()
        t.record_gap("approach A")
        t.record_gap("approach B")
        assert t.is_scanner_gap(threshold=2)
        assert not t.is_scanner_gap(threshold=3)

    def test_property_gap_requires_both_count_and_distinct(self):
        """Even with 5 gaps, if only 2 are distinct, threshold=3 is not met."""
        t = TriageState()
        t.record_gap("approach A")
        t.record_gap("approach A")
        t.record_gap("approach B")
        t.record_gap("approach A")
        t.record_gap("approach B")
        assert t.evaluator_gap_count == 5
        assert len(t.distinct_approaches_in_gap) == 2
        assert not t.is_scanner_gap(threshold=3)


# =============================================================================
# Property: WorkItem is a proper data container
# =============================================================================

class TestWorkItem:
    def test_property_has_required_fields(self):
        item = WorkItem(id="test-1", title="Test Item")
        assert item.id == "test-1"
        assert item.title == "Test Item"
        assert item.category == "uncategorized"
        assert item.resources == []
        assert item.depends_on == []

    def test_property_resources_and_deps_are_mutable_lists(self):
        item = WorkItem(
            id="test-1", title="Test",
            resources=["/etc/ssh/sshd_config"],
            depends_on=["prereq-1"],
        )
        assert len(item.resources) == 1
        assert len(item.depends_on) == 1
        # Mutable — can be updated at runtime
        item.resources.append("/etc/pam.d/system-auth")
        assert len(item.resources) == 2

    def test_property_two_items_are_independent_by_default(self):
        a = WorkItem(id="a", title="A")
        b = WorkItem(id="b", title="B")
        # No shared resources, no dependencies
        assert not set(a.resources) & set(b.resources)
        assert a.id not in b.depends_on
        assert b.id not in a.depends_on


# =============================================================================
# Property: STIG runtime satisfies protocol contracts
# =============================================================================

class TestStigRuntimeProtocol:
    """Verify the STIG runtime implements all five interfaces.

    These tests import the runtime module and check protocol satisfaction
    WITHOUT actually connecting to a VM or running OpenSCAP.
    """

    def test_property_stig_runtime_is_importable(self):
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "stig_runtime", Path("skills/stig-rhel9/runtime.py"))
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert hasattr(mod, "StigSkillRuntime")

    def test_property_runtime_has_all_interface_properties(self):
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "stig_runtime", Path("skills/stig-rhel9/runtime.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        from gemma_forge.harness.tools.ssh import SSHConfig
        ssh = SSHConfig(host="test", user="test", key_path="/dev/null")
        runtime = mod.StigSkillRuntime(ssh, "profile", "datastream")

        # Check all five interface properties exist
        assert hasattr(runtime, "work_queue")
        assert hasattr(runtime, "executor")
        assert hasattr(runtime, "evaluator")
        assert hasattr(runtime, "checkpoint")
        assert hasattr(runtime, "get_scan_tool")

        # Check they return objects with the right methods
        assert hasattr(runtime.work_queue, "scan")
        assert hasattr(runtime.executor, "apply")
        assert hasattr(runtime.executor, "get_agent_tools")
        assert hasattr(runtime.evaluator, "evaluate")
        assert hasattr(runtime.checkpoint, "exists")
        assert hasattr(runtime.checkpoint, "save")
        assert hasattr(runtime.checkpoint, "restore")
        assert hasattr(runtime.checkpoint, "delete")
        assert callable(runtime.get_scan_tool())
