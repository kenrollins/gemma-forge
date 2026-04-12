"""Task graph — DAG-based work item scheduling with dependency awareness.

The task graph manages the lifecycle of work items through the Ralph loop.
It tracks dependencies (declared and discovered), detects conflicts between
items that touch the same resources, and provides parallel-safe scheduling.

The graph is skill-agnostic — it operates on WorkItem objects from the
interfaces module. Skills populate it via their WorkQueue; the harness
manages state transitions and dependency resolution.

Node states:
  QUEUED     → waiting for dependencies or a free worker slot
  BLOCKED    → has unresolved dependencies
  ACTIVE     → currently being processed by a worker
  COMPLETED  → successfully resolved
  ESCALATED  → failed after exhausting retries/budget
  SKIPPED    → architect decided to skip

Events are emitted for every state transition so the dashboard can
render the live DAG visualization.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from gemma_forge.harness.interfaces import WorkItem

logger = logging.getLogger(__name__)


class NodeState(Enum):
    QUEUED = "queued"
    BLOCKED = "blocked"
    ACTIVE = "active"
    COMPLETED = "completed"
    ESCALATED = "escalated"
    SKIPPED = "skipped"


@dataclass
class GraphNode:
    """A node in the task graph — wraps a WorkItem with lifecycle state."""
    item: WorkItem
    state: NodeState = NodeState.QUEUED
    # Dependencies: node IDs that must complete before this one starts
    depends_on: set = field(default_factory=set)
    # Reverse deps: nodes that are waiting on this one
    blocks: set = field(default_factory=set)
    # Resources this item touches — for conflict detection
    resources: set = field(default_factory=set)
    # Metadata
    attempts: int = 0
    wall_time_s: float = 0.0
    escalation_reason: Optional[str] = None


class TaskGraph:
    """DAG-based work item scheduler.

    Manages the lifecycle of work items, tracks dependencies, and
    provides parallel-safe next-item selection.
    """

    def __init__(self):
        self.nodes: dict[str, GraphNode] = {}
        # Resource → set of node IDs that touch it
        self._resource_map: dict[str, set[str]] = defaultdict(set)
        # Post-mortem clustering for discovered dependencies
        self._failure_clusters: dict[str, list[str]] = defaultdict(list)

    def add_items(self, items: list[WorkItem]) -> None:
        """Add work items to the graph. Sets up declared dependencies."""
        for item in items:
            node = GraphNode(
                item=item,
                depends_on=set(item.depends_on),
                resources=set(item.resources),
            )
            self.nodes[item.id] = node
            for res in item.resources:
                self._resource_map[res].add(item.id)

        # Resolve initial blocked state
        self._update_blocked_states()

    def _update_blocked_states(self) -> None:
        """Recompute BLOCKED state for all QUEUED nodes."""
        for node_id, node in self.nodes.items():
            if node.state not in (NodeState.QUEUED, NodeState.BLOCKED):
                continue
            # Check if all dependencies are resolved
            unresolved = set()
            for dep_id in node.depends_on:
                if dep_id in self.nodes:
                    dep_state = self.nodes[dep_id].state
                    if dep_state not in (NodeState.COMPLETED, NodeState.SKIPPED):
                        unresolved.add(dep_id)
            if unresolved:
                node.state = NodeState.BLOCKED
            else:
                node.state = NodeState.QUEUED

    def add_dependency(self, from_id: str, to_id: str) -> bool:
        """Add a discovered dependency: from_id depends on to_id.

        Returns True if the dependency was new, False if it already existed
        or would create a cycle.
        """
        if from_id not in self.nodes or to_id not in self.nodes:
            return False
        if to_id in self.nodes[from_id].depends_on:
            return False  # already known
        # Simple cycle check: to_id cannot depend (transitively) on from_id
        if self._has_path(to_id, from_id):
            logger.warning("Skipping dependency %s→%s: would create cycle", from_id, to_id)
            return False
        self.nodes[from_id].depends_on.add(to_id)
        self.nodes[to_id].blocks.add(from_id)
        self._update_blocked_states()
        return True

    def _has_path(self, start: str, end: str, visited: set = None) -> bool:
        """Check if there's a dependency path from start to end (DFS)."""
        if visited is None:
            visited = set()
        if start == end:
            return True
        if start in visited:
            return False
        visited.add(start)
        if start not in self.nodes:
            return False
        for dep in self.nodes[start].depends_on:
            if self._has_path(dep, end, visited):
                return True
        return False

    def get_ready_items(self, active_resources: set[str] = None) -> list[WorkItem]:
        """Get work items that are ready to process (QUEUED, no unresolved deps).

        If active_resources is provided, also excludes items that would
        conflict with currently-active work (optimistic parallelism with
        conflict detection).
        """
        if active_resources is None:
            active_resources = set()

        ready = []
        for node in self.nodes.values():
            if node.state != NodeState.QUEUED:
                continue
            # Check resource conflicts with active work
            if node.resources & active_resources:
                continue
            ready.append(node.item)
        return ready

    def mark_active(self, item_id: str) -> None:
        """Mark an item as actively being processed."""
        if item_id in self.nodes:
            self.nodes[item_id].state = NodeState.ACTIVE

    def mark_completed(self, item_id: str, attempts: int = 0,
                       wall_time_s: float = 0.0) -> None:
        """Mark an item as successfully completed. Unblocks dependents."""
        if item_id in self.nodes:
            node = self.nodes[item_id]
            node.state = NodeState.COMPLETED
            node.attempts = attempts
            node.wall_time_s = wall_time_s
            self._update_blocked_states()

    def mark_escalated(self, item_id: str, reason: str = "",
                       attempts: int = 0, wall_time_s: float = 0.0) -> None:
        """Mark an item as escalated (failed). Does NOT unblock dependents."""
        if item_id in self.nodes:
            node = self.nodes[item_id]
            node.state = NodeState.ESCALATED
            node.escalation_reason = reason
            node.attempts = attempts
            node.wall_time_s = wall_time_s
            # Check if any blocked nodes should be escalated as a chain
            self._cascade_escalation(item_id)

    def mark_skipped(self, item_id: str) -> None:
        """Mark an item as skipped. Unblocks dependents (skip counts as resolved)."""
        if item_id in self.nodes:
            self.nodes[item_id].state = NodeState.SKIPPED
            self._update_blocked_states()

    def _cascade_escalation(self, failed_id: str) -> None:
        """If an escalated item has dependents, mark them as blocked-by-escalation."""
        for node_id, node in self.nodes.items():
            if node.state == NodeState.BLOCKED and failed_id in node.depends_on:
                # Check if ALL dependencies of this node are either completed,
                # skipped, or escalated. If so, it's stuck forever.
                all_resolved_or_failed = all(
                    self.nodes.get(d, GraphNode(item=WorkItem(id="?", title="?"))).state
                    in (NodeState.COMPLETED, NodeState.SKIPPED, NodeState.ESCALATED)
                    for d in node.depends_on
                )
                if all_resolved_or_failed:
                    has_failed_dep = any(
                        self.nodes.get(d, GraphNode(item=WorkItem(id="?", title="?"))).state
                        == NodeState.ESCALATED
                        for d in node.depends_on
                    )
                    if has_failed_dep:
                        node.state = NodeState.ESCALATED
                        node.escalation_reason = f"dependency_failed:{failed_id}"
                        logger.info("Cascade-escalated %s (dependency %s failed)",
                                    node_id, failed_id)
                        self._cascade_escalation(node_id)

    def record_failure_cause(self, item_id: str, cause_key: str) -> None:
        """Record a failure cause for post-mortem clustering.

        The cause_key should be a normalized string (service name, file path,
        error signature) extracted from the post-mortem. When 2+ items share
        the same cause_key, the graph can suggest dependency ordering.
        """
        self._failure_clusters[cause_key].append(item_id)

    def get_discovered_dependencies(self, min_cluster_size: int = 2) -> list[dict]:
        """Get dependency suggestions discovered from failure clustering.

        Returns a list of {prerequisite: str, dependents: [str], cause: str}
        for clusters where multiple items failed for the same reason.
        """
        suggestions = []
        for cause, item_ids in self._failure_clusters.items():
            if len(item_ids) >= min_cluster_size:
                # The item with the most fundamental-looking ID is the likely prerequisite
                # (e.g., "aide_build_database" before "aide_verify_acls")
                suggestions.append({
                    "cause": cause,
                    "items": list(set(item_ids)),
                    "cluster_size": len(set(item_ids)),
                })
        return suggestions

    def get_active_resources(self) -> set[str]:
        """Get the set of resources touched by currently-active items."""
        resources = set()
        for node in self.nodes.values():
            if node.state == NodeState.ACTIVE:
                resources |= node.resources
        return resources

    # -- Snapshot for events/visualization -----------------------------------

    def snapshot(self) -> dict:
        """Return the full graph state for event logging / dashboard."""
        nodes = []
        edges = []
        for node_id, node in self.nodes.items():
            nodes.append({
                "id": node_id,
                "title": node.item.title,
                "category": node.item.category,
                "state": node.state.value,
                "attempts": node.attempts,
                "wall_time_s": round(node.wall_time_s, 1),
                "escalation_reason": node.escalation_reason,
            })
            for dep_id in node.depends_on:
                edges.append({"from": dep_id, "to": node_id})
        return {
            "nodes": nodes,
            "edges": edges,
            "counts": self.counts(),
        }

    def counts(self) -> dict:
        """Aggregate counts by state."""
        c = {s.value: 0 for s in NodeState}
        for node in self.nodes.values():
            c[node.state.value] += 1
        return c
