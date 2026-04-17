"""Live-DB tests for V2 tip eviction.

Phase H1. Uses the per-test schema fixture from test_memory_and_clutch
so each test runs in an isolated schema that DROPs on teardown.
"""
from __future__ import annotations

import uuid

import pytest

from gemma_forge.memory.eviction import evict_low_utility_tips

# Reuse the fixtures from the existing memory test module so we don't
# duplicate the per-test schema scaffolding.
from tests.test_memory_and_clutch import admin_pool, mem_store  # noqa: F401


def _insert_tip(conn, *, text: str, tip_type: str = "recovery",
                source_rule_id: str | None = None) -> int:
    """Insert a tip and return its id. Bypasses TipWriter validation
    to keep the test setup minimal."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tips (text, tip_type, source_rule_id, application_context)
            VALUES (%s, %s, %s, ARRAY['audit'])
            RETURNING id
            """,
            (text, tip_type, source_rule_id),
        )
        return cur.fetchone()[0]


def _insert_retrieval(conn, *, tip_id: int, run_id: str, rule_id: str,
                      outcome_value: float | None,
                      outcome_confidence: float | None = 1.0) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tip_retrievals
              (run_id, tip_id, rule_id, rank, similarity_score,
               outcome_value, outcome_confidence)
            VALUES (%s, %s, %s, 1, 1.0, %s, %s)
            RETURNING id
            """,
            (run_id, tip_id, rule_id, outcome_value, outcome_confidence),
        )
        return cur.fetchone()[0]


def _is_retired(conn, tip_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT retired_at FROM tips WHERE id = %s", (tip_id,))
        row = cur.fetchone()
        return row is not None and row[0] is not None


def _setup_basic_fixture(mem_store):
    """Create:
      - tip_good: 3 retrievals averaging 0.8 (above threshold, should keep)
      - tip_bad:  3 retrievals averaging 0.1 (below threshold, should evict)
      - tip_thin: 1 retrieval at 0.0 (below min_retrievals, should keep)
      - tip_empty: 0 retrievals (should keep)
    Returns (pool, tip_ids_dict).
    """
    run_id = f"test-run-{uuid.uuid4().hex[:6]}"
    with mem_store._pool.connection() as conn:
        # Create a run row so FK passes
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO runs (id, skill, started_at) VALUES (%s, 'stig', now())",
                (run_id,),
            )

        tip_good = _insert_tip(conn, text="Known-good tip", source_rule_id="rule_good")
        tip_bad = _insert_tip(conn, text="Known-bad tip", source_rule_id="rule_bad")
        tip_thin = _insert_tip(conn, text="Thin-evidence tip", source_rule_id="rule_thin")
        tip_empty = _insert_tip(conn, text="Zero-evidence tip", source_rule_id="rule_empty")

        # 3 good retrievals
        for v in (0.8, 0.8, 0.8):
            _insert_retrieval(conn, tip_id=tip_good, run_id=run_id,
                              rule_id="rule_good", outcome_value=v)
        # 3 bad retrievals
        for v in (0.0, 0.1, 0.2):
            _insert_retrieval(conn, tip_id=tip_bad, run_id=run_id,
                              rule_id="rule_bad", outcome_value=v)
        # 1 thin retrieval
        _insert_retrieval(conn, tip_id=tip_thin, run_id=run_id,
                          rule_id="rule_thin", outcome_value=0.0)
        # tip_empty: no retrievals

        conn.commit()

    return mem_store._pool, {
        "good": tip_good, "bad": tip_bad, "thin": tip_thin, "empty": tip_empty,
    }


# -- The tests ---------------------------------------------------------


def test_evicts_below_threshold_with_sufficient_evidence(mem_store):
    pool, tids = _setup_basic_fixture(mem_store)

    report = evict_low_utility_tips(
        min_retrievals=3, threshold=0.3, pool=pool,
    )

    assert report.tips_retired_this_sweep == 1
    assert len(report.retired) == 1
    assert report.retired[0].tip_id == tids["bad"]

    with pool.connection() as conn:
        assert _is_retired(conn, tids["bad"]) is True
        assert _is_retired(conn, tids["good"]) is False
        assert _is_retired(conn, tids["thin"]) is False
        assert _is_retired(conn, tids["empty"]) is False


def test_keeps_good_tips_above_threshold(mem_store):
    pool, tids = _setup_basic_fixture(mem_store)

    report = evict_low_utility_tips(
        min_retrievals=3, threshold=0.3, pool=pool,
    )

    # tip_good (avg 0.8) should never appear in retired
    assert all(c.tip_id != tids["good"] for c in report.retired)


def test_respects_min_retrievals_gate(mem_store):
    pool, tids = _setup_basic_fixture(mem_store)

    # min_retrievals=5: tip_bad's 3 outcomes are now insufficient
    report = evict_low_utility_tips(
        min_retrievals=5, threshold=0.3, pool=pool,
    )
    assert report.tips_retired_this_sweep == 0
    with pool.connection() as conn:
        assert _is_retired(conn, tids["bad"]) is False


def test_tips_with_zero_retrievals_never_evicted(mem_store):
    pool, tids = _setup_basic_fixture(mem_store)

    # Even with a very lenient evidence gate (1 outcome), tip_empty has
    # zero outcomes and should be invisible to the eviction sweep.
    report = evict_low_utility_tips(
        min_retrievals=1, threshold=0.99, pool=pool,
    )
    assert all(c.tip_id != tids["empty"] for c in report.retired)
    with pool.connection() as conn:
        assert _is_retired(conn, tids["empty"]) is False


def test_threshold_tuning(mem_store):
    pool, tids = _setup_basic_fixture(mem_store)

    # tip_good avg 0.8, tip_bad avg 0.1
    # threshold 0.05 → evict nothing (both above)
    report = evict_low_utility_tips(
        min_retrievals=3, threshold=0.05, pool=pool,
    )
    assert report.tips_retired_this_sweep == 0

    # threshold 0.9 → evict both tip_bad AND tip_good
    report = evict_low_utility_tips(
        min_retrievals=3, threshold=0.9, pool=pool,
    )
    assert report.tips_retired_this_sweep == 2


def test_dry_run_does_not_retire(mem_store):
    pool, tids = _setup_basic_fixture(mem_store)

    report = evict_low_utility_tips(
        min_retrievals=3, threshold=0.3, pool=pool, dry_run=True,
    )

    # Report shows what *would* be retired
    assert len(report.retired) == 1
    assert report.retired[0].tip_id == tids["bad"]
    # But no rows actually changed
    assert report.tips_retired_this_sweep == 0
    with pool.connection() as conn:
        assert _is_retired(conn, tids["bad"]) is False


def test_confidence_scales_utility(mem_store):
    """A tip with outcome_value=1.0 but outcome_confidence=0.2 has
    utility 0.2, below threshold — should evict."""
    run_id = f"test-run-{uuid.uuid4().hex[:6]}"
    with mem_store._pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO runs (id, skill, started_at) VALUES (%s, 'stig', now())", (run_id,))

        tip_low_conf = _insert_tip(conn, text="Low-confidence tip")
        for _ in range(3):
            _insert_retrieval(conn, tip_id=tip_low_conf, run_id=run_id,
                              rule_id="rule_x",
                              outcome_value=1.0, outcome_confidence=0.2)
        conn.commit()

    report = evict_low_utility_tips(
        min_retrievals=3, threshold=0.3, pool=mem_store._pool,
    )
    retired_ids = [c.tip_id for c in report.retired]
    assert tip_low_conf in retired_ids


def test_already_retired_tip_not_re_retired(mem_store):
    pool, tids = _setup_basic_fixture(mem_store)

    # First sweep retires tip_bad
    report1 = evict_low_utility_tips(min_retrievals=3, threshold=0.3, pool=pool)
    assert report1.tips_retired_this_sweep == 1

    # Second sweep finds nothing new
    report2 = evict_low_utility_tips(min_retrievals=3, threshold=0.3, pool=pool)
    assert report2.tips_retired_this_sweep == 0
