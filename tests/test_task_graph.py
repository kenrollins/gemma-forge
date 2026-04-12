"""Property tests for the task graph — DAG scheduling with dependency awareness.

These tests verify the graph's properties without any skill-specific logic:
  - Independent items can all be scheduled
  - Blocked items are not schedulable until dependencies resolve
  - Completing a dependency unblocks its dependents
  - Escalating a dependency cascades to dependents
  - Cycle detection prevents circular dependencies
  - Resource conflict detection prevents parallel conflicts
  - Graph snapshot is always structurally valid
"""

import pytest

from gemma_forge.harness.interfaces import WorkItem
from gemma_forge.harness.task_graph import TaskGraph, NodeState


def _item(id: str, title: str = "", category: str = "test",
          depends_on: list = None, resources: list = None) -> WorkItem:
    return WorkItem(
        id=id, title=title or id, category=category,
        depends_on=depends_on or [], resources=resources or [],
    )


class TestGraphScheduling:
    """Property: independent items are all schedulable."""

    def test_all_independent_items_are_ready(self):
        g = TaskGraph()
        items = [_item("a"), _item("b"), _item("c")]
        g.add_items(items)
        ready = g.get_ready_items()
        assert len(ready) == 3
        assert {i.id for i in ready} == {"a", "b", "c"}

    def test_empty_graph_returns_no_ready_items(self):
        g = TaskGraph()
        assert g.get_ready_items() == []

    def test_active_items_are_not_ready(self):
        g = TaskGraph()
        g.add_items([_item("a"), _item("b")])
        g.mark_active("a")
        ready = g.get_ready_items()
        assert len(ready) == 1
        assert ready[0].id == "b"


class TestDependencyResolution:
    """Property: blocked items unblock when dependencies complete."""

    def test_dependent_item_is_blocked(self):
        g = TaskGraph()
        g.add_items([_item("base"), _item("child", depends_on=["base"])])
        ready = g.get_ready_items()
        assert len(ready) == 1
        assert ready[0].id == "base"
        assert g.nodes["child"].state == NodeState.BLOCKED

    def test_completing_dependency_unblocks_child(self):
        g = TaskGraph()
        g.add_items([_item("base"), _item("child", depends_on=["base"])])
        g.mark_active("base")
        g.mark_completed("base")
        ready = g.get_ready_items()
        assert any(i.id == "child" for i in ready)

    def test_chain_dependency_a_b_c(self):
        g = TaskGraph()
        g.add_items([
            _item("a"),
            _item("b", depends_on=["a"]),
            _item("c", depends_on=["b"]),
        ])
        # Only a is ready
        assert [i.id for i in g.get_ready_items()] == ["a"]
        g.mark_active("a")
        g.mark_completed("a")
        # Now b is ready, c still blocked
        ready_ids = {i.id for i in g.get_ready_items()}
        assert "b" in ready_ids
        assert "c" not in ready_ids
        g.mark_active("b")
        g.mark_completed("b")
        # Now c is ready
        assert any(i.id == "c" for i in g.get_ready_items())

    def test_skip_counts_as_resolved(self):
        g = TaskGraph()
        g.add_items([_item("base"), _item("child", depends_on=["base"])])
        g.mark_skipped("base")
        ready = g.get_ready_items()
        assert any(i.id == "child" for i in ready)


class TestEscalationCascade:
    """Property: escalating a dependency cascades to dependents."""

    def test_escalation_cascades_to_dependent(self):
        g = TaskGraph()
        g.add_items([_item("base"), _item("child", depends_on=["base"])])
        g.mark_active("base")
        g.mark_escalated("base", reason="time_budget")
        assert g.nodes["child"].state == NodeState.ESCALATED
        assert "dependency_failed" in g.nodes["child"].escalation_reason

    def test_escalation_cascades_through_chain(self):
        g = TaskGraph()
        g.add_items([
            _item("a"),
            _item("b", depends_on=["a"]),
            _item("c", depends_on=["b"]),
        ])
        g.mark_active("a")
        g.mark_escalated("a", reason="time_budget")
        assert g.nodes["b"].state == NodeState.ESCALATED
        assert g.nodes["c"].state == NodeState.ESCALATED

    def test_escalation_does_not_cascade_if_alternate_path(self):
        """If child depends on both a and b, escalating a doesn't cascade
        if b hasn't been attempted yet."""
        g = TaskGraph()
        g.add_items([
            _item("a"), _item("b"),
            _item("child", depends_on=["a", "b"]),
        ])
        g.mark_active("a")
        g.mark_escalated("a")
        # child is still blocked (b hasn't resolved), not cascaded
        assert g.nodes["child"].state == NodeState.BLOCKED


class TestCycleDetection:
    """Property: cycles are rejected."""

    def test_direct_cycle_rejected(self):
        g = TaskGraph()
        g.add_items([_item("a"), _item("b")])
        g.add_dependency("a", "b")
        result = g.add_dependency("b", "a")
        assert result is False

    def test_indirect_cycle_rejected(self):
        g = TaskGraph()
        g.add_items([_item("a"), _item("b"), _item("c")])
        g.add_dependency("b", "a")
        g.add_dependency("c", "b")
        result = g.add_dependency("a", "c")
        assert result is False


class TestResourceConflicts:
    """Property: items touching the same resource are not scheduled together."""

    def test_conflicting_resources_serialized(self):
        g = TaskGraph()
        g.add_items([
            _item("a", resources=["/etc/ssh/sshd_config"]),
            _item("b", resources=["/etc/ssh/sshd_config"]),
            _item("c", resources=["/etc/pam.d/system-auth"]),
        ])
        g.mark_active("a")
        active_res = g.get_active_resources()
        ready = g.get_ready_items(active_resources=active_res)
        ready_ids = {i.id for i in ready}
        assert "b" not in ready_ids  # conflicts with a
        assert "c" in ready_ids      # no conflict

    def test_no_conflict_when_nothing_active(self):
        g = TaskGraph()
        g.add_items([
            _item("a", resources=["/etc/ssh/sshd_config"]),
            _item("b", resources=["/etc/ssh/sshd_config"]),
        ])
        ready = g.get_ready_items(active_resources=set())
        assert len(ready) == 2


class TestDiscoveredDependencies:
    """Property: runtime-discovered dependencies are added correctly."""

    def test_discovered_dependency_blocks_item(self):
        g = TaskGraph()
        g.add_items([_item("base"), _item("child")])
        # Both are initially ready
        assert len(g.get_ready_items()) == 2
        # Discover dependency at runtime
        g.add_dependency("child", "base")
        ready = g.get_ready_items()
        assert len(ready) == 1
        assert ready[0].id == "base"

    def test_failure_clustering(self):
        g = TaskGraph()
        g.add_items([_item("a"), _item("b"), _item("c")])
        g.record_failure_cause("a", "aide_database_missing")
        g.record_failure_cause("b", "aide_database_missing")
        g.record_failure_cause("c", "aide_database_missing")
        clusters = g.get_discovered_dependencies()
        assert len(clusters) == 1
        assert clusters[0]["cause"] == "aide_database_missing"
        assert len(clusters[0]["items"]) == 3


class TestGraphSnapshot:
    """Property: snapshot is always structurally valid."""

    def test_snapshot_has_all_nodes(self):
        g = TaskGraph()
        g.add_items([_item("a"), _item("b", depends_on=["a"])])
        snap = g.snapshot()
        assert len(snap["nodes"]) == 2
        assert len(snap["edges"]) == 1
        assert snap["edges"][0] == {"from": "a", "to": "b"}

    def test_snapshot_counts_are_consistent(self):
        g = TaskGraph()
        g.add_items([_item("a"), _item("b"), _item("c")])
        g.mark_active("a")
        g.mark_completed("a")
        g.mark_active("b")
        g.mark_escalated("b", reason="test")
        snap = g.snapshot()
        counts = snap["counts"]
        assert counts["completed"] == 1
        assert counts["escalated"] == 1
        assert counts["queued"] == 1
        total = sum(counts.values())
        assert total == 3
