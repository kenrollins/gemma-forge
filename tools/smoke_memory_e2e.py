#!/usr/bin/env python
"""tools/smoke_memory_e2e.py — Phase C3 end-to-end smoke test.

Exercises every PostgresMemoryStore call site ralph.py touches, against
the live shared Postgres, plus the cross-run hydration the harness
performs at startup. Two simulated micro-runs verify:

  Run S1 (the 'first run after cutover')
  ─ start_run / end_run lifecycle round-trip
  ─ save_item_outcome upsert (one new + one updated row)
  ─ save_attempt with a banned_pattern (later visible to load_global_bans)
  ─ save_lesson for both a new and a duplicate text (weight boost path)
  ─ update_lesson_weight for both success and failure branches

  Hydration before Run S2 (the 'cross-run learning' path)
  ─ get_run_count includes both the migrated history and Run S1
  ─ load_global_bans picks up the banned_pattern from S1
  ─ load_all_lessons returns at least one lesson, sorted by weight
  ─ load_lessons (per-category) returns the new lesson
  ─ query_prior_attempts returns S1's attempt for the test rule
  ─ get_category_stats includes the test category from S1

  Run S2 lifecycle proves the second-run write path is independent of
  the first.

  The clutch is initialized against the same store and asked to make
  a recommendation — proves the integration with category stats works
  against real Postgres data.

All test rows go into a uniquely-named test schema created by the
script and DROPped on exit, so this run does NOT pollute stig.* with
synthetic data. Success criterion: every assertion passes and the
schema is cleaned up.

Usage: ./tools/smoke_memory_e2e.py
"""
from __future__ import annotations

import logging
import os
import re
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

from psycopg_pool import ConnectionPool  # noqa: E402

from gemma_forge.harness.db import _conninfo, _load_dotenv_once  # noqa: E402
from gemma_forge.harness.memory_store import PostgresMemoryStore  # noqa: E402
from gemma_forge.harness.clutch import Clutch, ClutchConfig  # noqa: E402

MIGRATIONS_DIR = REPO_ROOT / "migrations" / "stig"
MIGRATION_SQL = "\n".join(p.read_text() for p in sorted(MIGRATIONS_DIR.glob("*.sql")))


def adapt_for_test_schema(sql: str) -> str:
    sql = re.sub(r"^SET search_path TO stig\s*;\s*$", "", sql, flags=re.MULTILINE)
    sql = re.sub(r"^GRANT [^;]+;\s*$", "", sql, flags=re.MULTILINE)
    return sql


def make_schema(admin_pool: ConnectionPool, schema: str) -> None:
    with admin_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')
            cur.execute(f'SET search_path TO "{schema}"')
            cur.execute(adapt_for_test_schema(MIGRATION_SQL))


def drop_schema(admin_pool: ConnectionPool, schema: str) -> None:
    with admin_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def step(label: str) -> None:
    print(f"  → {label}")


def main() -> int:
    print("smoke_memory_e2e: starting end-to-end memory smoke test")
    _load_dotenv_once()
    if not os.environ.get("PG_FORGE_ADMIN_PASSWORD"):
        print("smoke_memory_e2e: PG_FORGE_ADMIN_PASSWORD missing; aborting", file=sys.stderr)
        return 2

    schema = "smk_" + uuid.uuid4().hex[:10]
    print(f"smoke_memory_e2e: scratch schema = {schema}")

    admin_pool = ConnectionPool(
        conninfo=_conninfo("forge_admin"),
        min_size=1,
        max_size=2,
        kwargs={"autocommit": True},
        open=True,
    )

    try:
        make_schema(admin_pool, schema)

        # Per-store pool with search_path pinned on every checkout.
        def _configure(c):
            with c.cursor() as cur:
                cur.execute(f'SET search_path TO "{schema}"')
            c.commit()

        store_pool = ConnectionPool(
            conninfo=_conninfo("forge_admin"),
            min_size=1,
            max_size=4,
            kwargs={"autocommit": False},
            configure=_configure,
            open=True,
        )

        try:
            store = PostgresMemoryStore(skill=schema, pool=store_pool)
            store.initialize()

            print()
            print("Run S1 — write path")
            step("start_run")
            run1 = store.start_run("smoke-skill", {"max_rules_per_run": 2})
            assert isinstance(run1, str) and len(run1) >= 8

            step("save_item_outcome (new) and (update)")
            store.save_item_outcome(run1, "smoke_rule_a", "Smoke A", "smoke-cat",
                                    "completed", 1, 12.3)
            # update path: same key, new outcome
            store.save_item_outcome(run1, "smoke_rule_a", "Smoke A", "smoke-cat",
                                    "completed", 2, 14.1)

            step("save_attempt with banned_pattern")
            store.save_attempt(run1, "smoke_rule_a", 1, "approach-1", True, "",
                               "reflection text", "lesson text",
                               "rm -rf /  # banned", 9.9)

            step("save_lesson (new)")
            store.save_lesson("smoke-cat", "Use authselect, not pam-auth-update.",
                              run1, "smoke_rule_a")
            step("save_lesson (duplicate → weight boost)")
            store.save_lesson("smoke-cat", "Use authselect, not pam-auth-update.",
                              run1, "smoke_rule_a")

            step("update_lesson_weight (success path)")
            lessons = store.load_lessons("smoke-cat", min_weight=0.0, limit=5)
            assert len(lessons) == 1, f"expected 1 lesson, got {len(lessons)}"
            store.update_lesson_weight(lessons[0].id, success=True)

            step("update_lesson_weight (failure path)")
            store.update_lesson_weight(lessons[0].id, success=False)

            step("end_run")
            store.end_run(run1, {"remediated": 1, "escalated": 0, "iterations": 1})

            print()
            print("Hydration — cross-run reads")
            step("get_run_count")
            assert store.get_run_count() == 1

            step("load_global_bans")
            bans = store.load_global_bans()
            assert "rm -rf /  # banned" in bans, f"banned pattern missing; got {bans}"

            step("load_all_lessons (top by weight)")
            top = store.load_all_lessons(min_weight=0.0, limit=10)
            assert any("authselect" in l.lesson for l in top), "smoke lesson missing"

            step("load_lessons (category-scoped)")
            cat_lessons = store.load_lessons("smoke-cat", min_weight=0.0, limit=10)
            assert len(cat_lessons) == 1
            # weight: 0.5 (insert) +0.1 dup +0.1 update success -0.05 update fail = 0.65
            assert abs(cat_lessons[0].weight - 0.65) < 1e-6, (
                f"unexpected lesson weight {cat_lessons[0].weight!r}"
            )

            step("query_prior_attempts")
            atts = store.query_prior_attempts("smoke_rule_a", limit=5)
            assert len(atts) == 1
            assert atts[0].approach == "approach-1"
            assert atts[0].eval_passed is True

            step("get_category_stats")
            stats = store.get_category_stats()
            cats = {s.category: s for s in stats}
            assert "smoke-cat" in cats
            assert cats["smoke-cat"].completed == 1
            assert cats["smoke-cat"].success_rate == 1.0

            print()
            print("Run S2 — second run lifecycle")
            step("start_run + end_run independently of Run S1")
            run2 = store.start_run("smoke-skill", {})
            store.save_item_outcome(run2, "smoke_rule_b", "Smoke B", "smoke-cat",
                                    "escalated", 5, 200.0)
            store.end_run(run2, {"remediated": 0, "escalated": 1})
            assert store.get_run_count() == 2

            print()
            print("Clutch — wired against real category stats")
            clutch = Clutch(config=ClutchConfig(max_workers=3), mem_store=store)
            clutch.initialize()
            assert clutch.state.has_prior_data, "clutch should see prior data after S1"
            snap = clutch.snapshot()
            assert "smoke-cat" in snap["category_decisions"], (
                f"smoke-cat missing from clutch decisions: {snap['category_decisions']}"
            )

            print()
            print("Summary line:")
            print("  " + store.summary())
        finally:
            store_pool.close()
    finally:
        drop_schema(admin_pool, schema)
        admin_pool.close()

    print()
    print("smoke_memory_e2e: ALL ASSERTIONS PASSED")
    print(f"smoke_memory_e2e: scratch schema {schema} dropped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
