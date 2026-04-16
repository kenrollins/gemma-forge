"""Property tests for the v5 memory tier (Postgres backend) and the clutch.

Phase C3 of the memory refactor (ADR-0016). The retired SQLite test
fixture (one tmp_path SQLite file per test) is replaced with a
per-test temp schema inside the shared ``gemma_forge`` Postgres,
created and torn down by ``forge_admin``. The DDL is reused
verbatim from ``migrations/stig/0001_base_schema.sql`` so the test
schema and the production schema can never drift.

Tests verify:
  - MemoryStore CRUD operations and schema integrity
  - Cross-run lesson persistence and retrieval
  - Lesson weight reinforcement (repeated lessons gain weight)
  - Category difficulty model accuracy
  - Clutch concurrency decisions based on difficulty
  - Clutch batch selection respects resource conflicts
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from gemma_forge.harness.db import _conninfo, _load_dotenv_once
from gemma_forge.harness.memory_store import (
    PostgresMemoryStore,
    StoredLesson,
    StoredAttempt,
    CategoryStats,
)
from gemma_forge.harness.clutch import Clutch, ClutchConfig
from gemma_forge.harness.task_graph import TaskGraph
from gemma_forge.harness.interfaces import WorkItem

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = REPO_ROOT / "migrations" / "stig"
MIGRATION_SQL = "\n".join(
    p.read_text() for p in sorted(MIGRATIONS_DIR.glob("*.sql"))
)


def _adapt_for_test_schema(sql: str) -> str:
    """Adapt the production migration to a per-test schema:

    1. Strip the ``SET search_path TO stig`` line — the test fixture
       sets the path on the cursor before applying, and we want CREATEs
       to land in the test schema instead.
    2. Strip the ``GRANT ... TO forge_stig`` boilerplate at the end —
       neither the production schema nor the production role exists
       inside a per-test schema.
    """
    sql = re.sub(r"^SET search_path TO stig\s*;\s*$", "", sql, flags=re.MULTILINE)
    sql = re.sub(r"^GRANT [^;]+;\s*$", "", sql, flags=re.MULTILINE)
    return sql


@pytest.fixture(scope="session")
def admin_pool() -> ConnectionPool:
    """One forge_admin pool shared by every test in the session."""
    _load_dotenv_once()
    if not os.environ.get("PG_FORGE_ADMIN_PASSWORD"):
        pytest.skip("PG_FORGE_ADMIN_PASSWORD missing — Phase A bootstrap not run")
    pool = ConnectionPool(
        conninfo=_conninfo("forge_admin"),
        min_size=1,
        max_size=4,
        kwargs={"autocommit": True},
        open=True,
    )
    yield pool
    pool.close()


@pytest.fixture
def mem_store(admin_pool):
    """Per-test isolated Postgres-backed MemoryStore.

    Creates a fresh schema ``mst_<uuid>`` inside ``gemma_forge``,
    applies the production DDL into it, hands back a PostgresMemoryStore
    bound to a per-test pool whose connections SET search_path on
    checkout. Teardown DROPs the schema CASCADE.
    """
    schema = "mst_" + uuid.uuid4().hex[:10]

    with admin_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')
            cur.execute(f'SET search_path TO "{schema}"')
            cur.execute(_adapt_for_test_schema(MIGRATION_SQL))

    def _configure(c):
        # SET search_path is a SQL statement that opens an implicit
        # transaction with autocommit=False; psycopg-pool refuses to
        # hand back a connection still in INTRANS, so commit explicitly.
        with c.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}"')
        c.commit()

    test_pool = ConnectionPool(
        conninfo=_conninfo("forge_admin"),
        min_size=1,
        max_size=2,
        kwargs={"autocommit": False},
        configure=_configure,
        open=True,
    )

    store = PostgresMemoryStore(skill=schema, pool=test_pool)
    store.initialize()
    try:
        yield store
    finally:
        test_pool.close()
        with admin_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def _populate_two_runs(store: PostgresMemoryStore):
    """Helper: populate two runs with mixed outcomes for testing."""
    r1 = store.start_run("test-skill", {})
    store.save_item_outcome(r1, "auth_1", "Auth Rule 1", "authentication", "completed", 1, 30.0)
    store.save_item_outcome(r1, "auth_2", "Auth Rule 2", "authentication", "completed", 1, 25.0)
    store.save_item_outcome(r1, "auth_3", "Auth Rule 3", "authentication", "completed", 2, 60.0)
    store.save_item_outcome(r1, "aide_1", "AIDE Rule 1", "integrity-monitoring", "escalated", 15, 1200.0)
    store.save_item_outcome(r1, "aide_2", "AIDE Rule 2", "integrity-monitoring", "escalated", 12, 1000.0)
    store.save_lesson("authentication", "PAM rules respond well to authselect", r1, "auth_1")
    store.save_lesson("integrity-monitoring", "AIDE database init fails on Rocky 9", r1, "aide_1")
    store.end_run(r1, {"remediated": 3, "escalated": 2})

    r2 = store.start_run("test-skill", {})
    store.save_item_outcome(r2, "auth_4", "Auth Rule 4", "authentication", "completed", 1, 20.0)
    store.save_item_outcome(r2, "aide_3", "AIDE Rule 3", "integrity-monitoring", "escalated", 10, 900.0)
    store.save_lesson("authentication", "PAM rules respond well to authselect", r2, "auth_4")
    store.end_run(r2, {"remediated": 1, "escalated": 1})

    return r1, r2


# =============================================================================
# Memory Store: CRUD operations
# =============================================================================


class TestMemoryStoreCRUD:
    def test_property_run_lifecycle(self, mem_store):
        run_id = mem_store.start_run("stig-rhel9", {"max_retries": 3})
        assert len(run_id) > 0
        mem_store.end_run(run_id, {"remediated": 5})
        assert mem_store.get_run_count() == 1

    def test_property_item_outcome_persists(self, mem_store):
        run_id = mem_store.start_run("test", {})
        mem_store.save_item_outcome(run_id, "rule_1", "Test", "kernel", "completed", 2, 45.0)
        stats = mem_store.get_category_stats()
        assert len(stats) == 1
        assert stats[0].category == "kernel"
        assert stats[0].completed == 1

    def test_property_attempt_trace_persists(self, mem_store):
        run_id = mem_store.start_run("test", {})
        mem_store.save_item_outcome(run_id, "rule_1", "Test", "kernel", "completed", 1, 30.0)
        mem_store.save_attempt(run_id, "rule_1", 1, "sed -i config", True, "", "", "worked", "", 30.0)
        attempts = mem_store.query_prior_attempts("rule_1")
        assert len(attempts) == 1
        assert attempts[0].approach == "sed -i config"
        assert attempts[0].lesson == "worked"

    def test_property_summary_is_readable(self, mem_store):
        run_id = mem_store.start_run("test", {})
        mem_store.save_item_outcome(run_id, "r1", "T", "k", "completed", 1, 10.0)
        s = mem_store.summary()
        assert "1 runs" in s
        assert "1 items" in s


# =============================================================================
# Cross-run learning
# =============================================================================


class TestCrossRunLearning:
    def test_property_lessons_persist_across_runs(self, mem_store):
        _populate_two_runs(mem_store)
        lessons = mem_store.load_lessons("authentication")
        assert len(lessons) >= 1
        assert any("PAM" in l.lesson for l in lessons)

    def test_property_duplicate_lessons_gain_weight(self, mem_store):
        _populate_two_runs(mem_store)
        lessons = mem_store.load_lessons("authentication")
        pam_lesson = [l for l in lessons if "PAM" in l.lesson][0]
        assert pam_lesson.weight > 0.5
        assert pam_lesson.success_count >= 1

    def test_property_global_bans_persist(self, mem_store):
        run_id = mem_store.start_run("test", {})
        mem_store.save_item_outcome(run_id, "r1", "T", "k", "completed", 1, 10.0)
        mem_store.save_attempt(run_id, "r1", 1, "bad approach", False, "eval_gap",
                               "failed", "", "systemctl stop sshd", 10.0)
        bans = mem_store.load_global_bans()
        assert "systemctl stop sshd" in bans

    def test_property_prior_attempts_queryable_by_item(self, mem_store):
        r1 = mem_store.start_run("test", {})
        mem_store.save_item_outcome(r1, "rule_x", "X", "kernel", "escalated", 3, 100.0)
        for i in range(3):
            mem_store.save_attempt(r1, "rule_x", i + 1, f"approach_{i}", False,
                                   "evaluator_gap", f"reflection_{i}", f"lesson_{i}", "", 30.0)
        attempts = mem_store.query_prior_attempts("rule_x")
        assert len(attempts) == 3

    def test_property_lessons_filterable_by_weight(self, mem_store):
        run_id = mem_store.start_run("test", {})
        mem_store.save_lesson("cat_a", "strong lesson", run_id, "r1")
        mem_store.save_lesson("cat_a", "strong lesson", run_id, "r2")
        mem_store.save_lesson("cat_a", "strong lesson", run_id, "r3")
        mem_store.save_lesson("cat_a", "weak lesson", run_id, "r4")
        strong = mem_store.load_lessons("cat_a", min_weight=0.6)
        assert len(strong) == 1
        assert "strong" in strong[0].lesson


# =============================================================================
# Category difficulty model
# =============================================================================


class TestDifficultyModel:
    def test_property_stats_aggregate_across_runs(self, mem_store):
        _populate_two_runs(mem_store)
        stats = mem_store.get_category_stats()
        cats = {s.category: s for s in stats}

        assert "authentication" in cats
        assert "integrity-monitoring" in cats

        auth = cats["authentication"]
        assert auth.completed == 4
        assert auth.success_rate > 0.9

        aide = cats["integrity-monitoring"]
        assert aide.escalated == 3
        assert aide.success_rate == 0.0

    def test_property_run_count_increments(self, mem_store):
        _populate_two_runs(mem_store)
        assert mem_store.get_run_count() == 2


# =============================================================================
# Clutch: concurrency decisions
# =============================================================================


class TestClutchDecisions:
    def test_property_first_run_is_serial(self, mem_store):
        clutch = Clutch(mem_store=mem_store)
        clutch.initialize()
        assert clutch.state.recommended_workers == 1
        assert not clutch.state.has_prior_data

    def test_property_easy_categories_get_max_workers(self, mem_store):
        _populate_two_runs(mem_store)
        clutch = Clutch(config=ClutchConfig(max_workers=3), mem_store=mem_store)
        clutch.initialize()
        assert clutch.recommend_workers("authentication") == 3

    def test_property_hard_categories_get_serial(self, mem_store):
        _populate_two_runs(mem_store)
        clutch = Clutch(config=ClutchConfig(max_workers=3), mem_store=mem_store)
        clutch.initialize()
        assert clutch.recommend_workers("integrity-monitoring") == 1

    def test_property_unknown_category_gets_default(self, mem_store):
        _populate_two_runs(mem_store)
        clutch = Clutch(config=ClutchConfig(max_workers=3), mem_store=mem_store)
        clutch.initialize()
        assert clutch.recommend_workers("never-seen-before") == 1

    def test_property_max_workers_respects_ceiling(self, mem_store):
        _populate_two_runs(mem_store)
        clutch = Clutch(config=ClutchConfig(max_workers=2), mem_store=mem_store)
        clutch.initialize()
        assert clutch.recommend_workers("authentication") <= 2

    def test_property_snapshot_is_complete(self, mem_store):
        _populate_two_runs(mem_store)
        clutch = Clutch(mem_store=mem_store)
        clutch.initialize()
        snap = clutch.snapshot()
        assert "recommended_workers" in snap
        assert "reason" in snap
        assert "has_prior_data" in snap
        assert snap["has_prior_data"] is True
        assert "authentication" in snap["category_decisions"]


# =============================================================================
# Clutch: batch selection
# =============================================================================


class TestClutchBatchSelection:
    def test_property_batch_from_easy_category(self, mem_store):
        _populate_two_runs(mem_store)
        clutch = Clutch(config=ClutchConfig(max_workers=3), mem_store=mem_store)
        clutch.initialize()

        graph = TaskGraph()
        graph.add_items([
            WorkItem(id="a1", title="A1", category="authentication"),
            WorkItem(id="a2", title="A2", category="authentication"),
            WorkItem(id="a3", title="A3", category="authentication"),
        ])
        batch = clutch.select_batch(graph)
        assert len(batch) == 3

    def test_property_batch_from_hard_category(self, mem_store):
        _populate_two_runs(mem_store)
        clutch = Clutch(config=ClutchConfig(max_workers=3), mem_store=mem_store)
        clutch.initialize()

        graph = TaskGraph()
        graph.add_items([
            WorkItem(id="i1", title="I1", category="integrity-monitoring"),
            WorkItem(id="i2", title="I2", category="integrity-monitoring"),
        ])
        batch = clutch.select_batch(graph)
        assert len(batch) >= 1

    def test_property_batch_respects_resource_conflicts(self, mem_store):
        _populate_two_runs(mem_store)
        clutch = Clutch(config=ClutchConfig(max_workers=3), mem_store=mem_store)
        clutch.initialize()

        graph = TaskGraph()
        graph.add_items([
            WorkItem(id="a1", title="A1", category="authentication",
                     resources=["/etc/pam.d/system-auth"]),
            WorkItem(id="a2", title="A2", category="authentication",
                     resources=["/etc/pam.d/system-auth"]),
            WorkItem(id="a3", title="A3", category="authentication",
                     resources=["/etc/ssh/sshd_config"]),
        ])
        graph.mark_active("a1")
        active_res = graph.get_active_resources()
        batch = clutch.select_batch(graph, active_resources=active_res)
        batch_ids = {item.id for item in batch}
        assert "a2" not in batch_ids
        assert "a3" in batch_ids

    def test_property_empty_graph_returns_empty_batch(self, mem_store):
        clutch = Clutch(mem_store=mem_store)
        clutch.initialize()
        graph = TaskGraph()
        batch = clutch.select_batch(graph)
        assert batch == []
