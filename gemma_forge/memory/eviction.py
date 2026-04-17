"""History-based deletion for V2 tips.

Phase H1 of the V2 memory architecture (see
docs/drafts/v2-architecture-plan.md §2.4). Lifted from Xu et al.
arxiv 2505.16067 and adapted to our deterministic-outcome setting.

The mechanism:

  1. For each active tip (``retired_at IS NULL``), aggregate the
     outcome evidence from its ``stig.tip_retrievals`` rows where
     ``outcome_value`` and ``outcome_confidence`` are recorded.
  2. If the tip has enough evidence (``n_outcomes >= min_retrievals``)
     AND its average utility (``AVG(value × confidence)``) falls below
     the skill's threshold, retire the tip by setting
     ``retired_at = now()`` and a descriptive ``retired_reason``.

Nothing is ever deleted. Retirement is bi-temporal — the row stays
in the table with ``retired_at`` marking when it stopped being
eligible for retrieval. This preserves provenance for audit queries
and lets a future policy un-retire if we change our mind.

Skill-agnostic by construction: the eviction parameters
(``min_retrievals_before_eviction``, ``eviction_threshold``) come
from the skill's ``EvaluatorMetadata``, not from a hardcoded constant
here. A graded-signal skill with noisy outcomes can set
``min_retrievals=10, threshold=0.5`` and this function works the same.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from psycopg_pool import ConnectionPool

from gemma_forge.harness.db import get_pool

logger = logging.getLogger(__name__)


@dataclass
class TipUtilitySnapshot:
    """One row in the pre-eviction utility report."""
    tip_id: int
    source_rule_id: Optional[str]
    tip_type: str
    n_outcomes: int
    avg_utility: float
    text_preview: str                   # first 120 chars for human-readable dream report


@dataclass
class EvictionReport:
    """Summary of one eviction sweep. Suitable for logging, the dream
    report, and JSONL emission."""
    skill: str
    min_retrievals: int
    threshold: float
    # Counts
    total_active_tips: int = 0
    tips_with_sufficient_evidence: int = 0
    tips_retired_this_sweep: int = 0
    # Detail
    retired: list[TipUtilitySnapshot] = field(default_factory=list)
    # For parity / UI
    remaining_active: int = 0


def _count_active_tips(pool: ConnectionPool) -> int:
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM tips WHERE retired_at IS NULL")
        return cur.fetchone()[0]


def _find_eviction_candidates(
    pool: ConnectionPool,
    *,
    min_retrievals: int,
    threshold: float,
) -> tuple[list[TipUtilitySnapshot], int]:
    """Return (candidates_to_retire, count_of_tips_with_sufficient_evidence).

    A tip is a candidate when it has ≥ ``min_retrievals`` non-null
    outcome rows AND its mean utility is < ``threshold``.
    """
    with pool.connection() as conn, conn.cursor() as cur:
        # Two subqueries collapsed: the HAVING clause gates on evidence
        # volume; the WHERE on avg_utility triggers retirement. LEFT JOIN
        # on tip_retrievals so tips with zero outcomes drop out naturally.
        cur.execute(
            """
            WITH utility AS (
                SELECT t.id AS tip_id,
                       t.source_rule_id,
                       t.tip_type,
                       substr(t.text, 1, 120) AS text_preview,
                       COUNT(tr.id) AS n_outcomes,
                       AVG(tr.outcome_value * tr.outcome_confidence) AS avg_utility
                FROM tips t
                JOIN tip_retrievals tr ON tr.tip_id = t.id
                WHERE t.retired_at IS NULL
                  AND tr.outcome_value IS NOT NULL
                  AND tr.outcome_confidence IS NOT NULL
                GROUP BY t.id, t.source_rule_id, t.tip_type, t.text
                HAVING COUNT(tr.id) >= %s
            )
            SELECT tip_id, source_rule_id, tip_type, text_preview,
                   n_outcomes, avg_utility,
                   (avg_utility < %s) AS should_retire
            FROM utility
            ORDER BY avg_utility ASC, tip_id ASC
            """,
            (min_retrievals, threshold),
        )
        rows = cur.fetchall()

    candidates: list[TipUtilitySnapshot] = []
    n_sufficient = len(rows)
    for (tip_id, src_rule, tip_type, preview, n_out, avg_u, should_retire) in rows:
        if should_retire:
            candidates.append(
                TipUtilitySnapshot(
                    tip_id=tip_id,
                    source_rule_id=src_rule,
                    tip_type=tip_type,
                    n_outcomes=int(n_out),
                    avg_utility=float(avg_u),
                    text_preview=preview or "",
                )
            )
    return candidates, n_sufficient


def _retire_tips(
    pool: ConnectionPool,
    candidates: list[TipUtilitySnapshot],
    *,
    threshold: float,
) -> int:
    """Mark each candidate retired. Returns rows updated."""
    if not candidates:
        return 0
    reason_template = (
        "history_based_deletion: avg_utility={util:.3f} < threshold={thr} "
        "after {n} retrieval(s)"
    )
    updated = 0
    with pool.connection() as conn, conn.cursor() as cur:
        for c in candidates:
            reason = reason_template.format(
                util=c.avg_utility, thr=threshold, n=c.n_outcomes,
            )
            cur.execute(
                """
                UPDATE tips
                   SET retired_at = now(),
                       retired_reason = %s
                 WHERE id = %s
                   AND retired_at IS NULL
                """,
                (reason, c.tip_id),
            )
            updated += cur.rowcount
        conn.commit()
    return updated


def evict_low_utility_tips(
    *,
    skill: str = "stig",
    min_retrievals: int,
    threshold: float,
    pool: Optional[ConnectionPool] = None,
    dry_run: bool = False,
) -> EvictionReport:
    """Run one eviction sweep. Returns a structured report.

    ``min_retrievals`` and ``threshold`` come from the skill's
    ``EvaluatorMetadata`` — a binary-deterministic skill like STIG
    can use n=3, threshold=0.3; a graded or judgment-based skill
    needs higher n and threshold to avoid evicting on noise.

    If ``dry_run=True``, evaluates which tips *would* be retired and
    reports them, but performs no UPDATE. Useful for the dream report
    preview before committing.
    """
    pool = pool or get_pool(f"forge_{skill}")

    total_active = _count_active_tips(pool)
    candidates, n_sufficient = _find_eviction_candidates(
        pool, min_retrievals=min_retrievals, threshold=threshold,
    )

    logger.info(
        "eviction sweep: %d tips active, %d have ≥%d outcomes; "
        "%d would be retired (threshold=%.2f, dry_run=%s)",
        total_active, n_sufficient, min_retrievals,
        len(candidates), threshold, dry_run,
    )

    retired_count = 0
    if not dry_run:
        retired_count = _retire_tips(pool, candidates, threshold=threshold)

    remaining = total_active - retired_count

    return EvictionReport(
        skill=skill,
        min_retrievals=min_retrievals,
        threshold=threshold,
        total_active_tips=total_active,
        tips_with_sufficient_evidence=n_sufficient,
        tips_retired_this_sweep=retired_count,
        retired=candidates,
        remaining_active=remaining,
    )
