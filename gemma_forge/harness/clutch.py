"""Adaptive concurrency controller — the "clutch."

The clutch decides how many work items to process in parallel based on:
1. Learned difficulty from prior runs (via the MemoryStore protocol)
2. Resource conflict constraints (via TaskGraph)
3. A configurable max concurrency ceiling

The metaphor: a clutch transfers power from the engine (GPU) to the
wheels (workers). When the engine has spare capacity and the road is
smooth (easy items), engage more gears. When the road is rough (hard
items with high failure rates), disengage and go serial.

First run: no prior data → serial (max_workers=1).
Subsequent runs: difficulty model informs concurrency per category.

The clutch is skill-agnostic — it operates on CategoryStats from the
MemoryStore and WorkItem metadata from the TaskGraph.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from gemma_forge.harness.memory_store import CategoryStats, MemoryStoreProtocol
from gemma_forge.harness.task_graph import TaskGraph

logger = logging.getLogger(__name__)


@dataclass
class ClutchConfig:
    """Configuration for the adaptive concurrency controller."""
    max_workers: int = 3           # hard ceiling
    min_workers: int = 1           # always at least one
    # Category thresholds for concurrency decisions
    easy_threshold: float = 0.85   # success_rate above this → full parallel
    hard_threshold: float = 0.50   # success_rate below this → serial only
    # First run (no data) defaults to serial
    default_workers_no_data: int = 1


@dataclass
class ClutchState:
    """Current clutch state — readable by the dashboard."""
    recommended_workers: int = 1
    reason: str = "no prior data"
    category_decisions: dict = field(default_factory=dict)
    has_prior_data: bool = False


class Clutch:
    """Adaptive concurrency controller.

    Usage:
        clutch = Clutch(config, mem_store)
        clutch.initialize()  # loads difficulty model from prior runs

        # During the run, ask how many workers for a given category
        n = clutch.recommend_workers("authentication")  # → 3 (easy)
        n = clutch.recommend_workers("integrity-monitoring")  # → 1 (hard)

        # Get items to process in parallel
        items = clutch.select_batch(task_graph)
    """

    def __init__(self, config: ClutchConfig = None,
                 mem_store: MemoryStoreProtocol = None):
        self.config = config or ClutchConfig()
        self.mem_store = mem_store
        self.state = ClutchState()
        self._category_stats: dict[str, CategoryStats] = {}

    def initialize(self) -> None:
        """Load difficulty model from prior runs."""
        if self.mem_store is None:
            self.state.reason = "no memory store"
            return

        stats = self.mem_store.get_category_stats()
        if not stats:
            self.state.reason = "first run — no prior data, starting serial"
            return

        self.state.has_prior_data = True
        for cs in stats:
            self._category_stats[cs.category] = cs

        # Compute overall recommendation
        avg_success = sum(cs.success_rate for cs in stats) / len(stats)
        if avg_success >= self.config.easy_threshold:
            self.state.recommended_workers = self.config.max_workers
            self.state.reason = f"prior data: {avg_success:.0%} avg success → max parallel"
        elif avg_success >= self.config.hard_threshold:
            self.state.recommended_workers = 2
            self.state.reason = f"prior data: {avg_success:.0%} avg success → moderate parallel"
        else:
            self.state.recommended_workers = 1
            self.state.reason = f"prior data: {avg_success:.0%} avg success → serial"

        # Per-category decisions
        for cs in stats:
            if cs.success_rate >= self.config.easy_threshold:
                decision = self.config.max_workers
                label = "easy"
            elif cs.success_rate >= self.config.hard_threshold:
                decision = 2
                label = "moderate"
            else:
                decision = 1
                label = "hard"
            self.state.category_decisions[cs.category] = {
                "workers": decision,
                "label": label,
                "success_rate": round(cs.success_rate, 2),
                "avg_attempts": round(cs.avg_attempts, 1),
                "total_items": cs.total_items,
            }

        logger.info("Clutch initialized: %d categories from prior runs",
                     len(self._category_stats))
        for cat, dec in self.state.category_decisions.items():
            logger.info("  %s: %s (%.0f%% success, %.1f avg attempts) → %d workers",
                        cat, dec["label"], dec["success_rate"] * 100,
                        dec["avg_attempts"], dec["workers"])

    def recommend_workers(self, category: str) -> int:
        """How many parallel workers for items in this category?"""
        if not self.state.has_prior_data:
            return self.config.default_workers_no_data

        cs = self._category_stats.get(category)
        if cs is None:
            return self.config.default_workers_no_data

        if cs.success_rate >= self.config.easy_threshold:
            return self.config.max_workers
        elif cs.success_rate >= self.config.hard_threshold:
            return 2
        else:
            return 1

    def select_batch(self, graph: TaskGraph,
                     active_resources: set[str] = None) -> list:
        """Select the next batch of items to process in parallel.

        Returns up to N ready items where N is determined by the
        difficulty of the available items' categories.
        """
        if active_resources is None:
            active_resources = graph.get_active_resources()

        ready = graph.get_ready_items(active_resources=active_resources)
        if not ready:
            return []

        # Determine batch size from the easiest available category
        # (process easy items in parallel while hard items queue)
        max_batch = 1
        for item in ready:
            rec = self.recommend_workers(item.category)
            max_batch = max(max_batch, rec)

        # Cap at the config ceiling
        max_batch = min(max_batch, self.config.max_workers)

        # Select items, preferring easy categories first
        if self.state.has_prior_data:
            ready.sort(key=lambda item: (
                self._category_stats.get(item.category, CategoryStats()).success_rate
            ), reverse=True)  # highest success rate first

        return ready[:max_batch]

    def snapshot(self) -> dict:
        """State snapshot for event logging / dashboard."""
        return {
            "recommended_workers": self.state.recommended_workers,
            "reason": self.state.reason,
            "has_prior_data": self.state.has_prior_data,
            "category_decisions": self.state.category_decisions,
        }
