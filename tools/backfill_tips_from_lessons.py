#!/usr/bin/env python
"""tools/backfill_tips_from_lessons.py — migrate V1 lessons → V2 tips.

Phase F3 of the V2 memory architecture. Reads every row in
``stig.lessons_current`` and inserts a matching row into ``stig.tips``
so the transition from V1 retrieval (category+confidence ranking) to
V2 retrieval (similarity + per-(tip, rule) hits) has a populated tips
table to work against.

Mapping:

    lessons_current.lesson           → tips.text
    lessons_current.category         → tips.application_context = [category]
    lessons_current.source_run_id    → tips.source_run_id
    lessons_current.source_item_id   → tips.source_rule_id
    (implicit: no attempt FK)        → tips.source_attempt_id = NULL
    tip_type                         → 'recovery' (honest default — V1
                                        only saved lessons on Reflector
                                        failure traces, so the whole
                                        corpus is failure-derived).
    tips.trigger_conditions          → NULL (Phase G extracts via LLM)
    tips.embedding                   → NULL (Phase G generates)
    tips.outcome_at_source_*         → NULL (not captured per-lesson in V1)

The lesson's ``weight`` / ``confidence`` / ``success_count`` /
``failure_count`` fields are deliberately not migrated — V2 replaces
those with per-(tip, rule) hit tracking from ``tip_retrievals``, and
surfacing the old signals would re-create the V1 coarseness problem
Diagnostic 2 exposed.

Why everything defaults to 'recovery': the harness natively produces
``tip_type`` inline — the F-next Reflector will emit it as a JSON
field alongside the tip text in one pass, seeing the full attempt
trace. Backfill is a one-shot migration; running a separate
LLM-based classifier over historical lesson text would label tips
with a prompt different from the Reflector's natural output, giving
an inconsistent UI color signal that flips back when Run 5's real
V2 tips arrive. See
``docs/journal/journey/29-tip-classifier-decision.md`` for the full
rationale.

Idempotent: re-running without ``--reset`` is a no-op if any
backfilled tips already exist. With ``--reset``, all prior-backfill
tips are retired (``retired_at = now(), retired_reason =
'backfill_reset'``) before the new rows are inserted — history is
preserved via the bi-temporal column.

Usage:
  ./tools/backfill_tips_from_lessons.py --skill stig [--reset] [--dry-run]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from gemma_forge.harness.db import get_pool  # noqa: E402
from gemma_forge.memory.tip_writer import Tip, TipWriter  # noqa: E402

logger = logging.getLogger("backfill_tips")


def load_lessons(role: str) -> list[tuple]:
    """Read every active lesson from ``lessons_current``.

    ``superseded_by_uuid IS NULL`` filters out dream-pass-superseded
    rows; those are history, not current memory, and re-migrating them
    would pollute the tips table with stale entries.
    """
    pool = get_pool(role)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, category, lesson, source_run_id, source_item_id,
                   weight, confidence, environment_tag, created_at
            FROM lessons_current
            WHERE superseded_by_uuid IS NULL
              AND lesson IS NOT NULL
              AND length(trim(lesson)) > 0
            ORDER BY id
            """
        )
        return cur.fetchall()


def count_existing_backfilled_tips(role: str) -> int:
    """Count tips that look like they came from a prior backfill run."""
    pool = get_pool(role)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM tips
            WHERE source_run_id IS NOT NULL
              AND retired_at IS NULL
              AND tip_type = 'recovery'
            """
        )
        return cur.fetchone()[0]


def retire_prior_backfill(role: str) -> int:
    """Mark prior backfill tips retired. Returns rows affected."""
    pool = get_pool(role)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE tips
               SET retired_at = now(),
                   retired_reason = 'backfill_reset'
            WHERE retired_at IS NULL
              AND source_run_id IS NOT NULL
            """
        )
        affected = cur.rowcount
        conn.commit()
    return affected


def lesson_to_tip(row: tuple) -> Tip:
    """Map one ``lessons_current`` row to a ``Tip`` dataclass."""
    (_id, category, lesson, source_run_id, source_item_id,
     _weight, _confidence, environment_tag, _created_at) = row
    return Tip(
        text=lesson,
        tip_type="recovery",
        trigger_conditions=None,
        application_context=[category] if category else [],
        source_attempt_id=None,
        source_run_id=source_run_id,
        source_rule_id=source_item_id,
        outcome_at_source_value=None,
        outcome_at_source_confidence=None,
        environment_tag=environment_tag,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--skill", default="stig", help="Skill name (default: stig)")
    parser.add_argument("--reset", action="store_true",
                        help="Retire prior-backfill tips before re-inserting. "
                             "Safe — retired tips are preserved in the table with retired_at set.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Count and sample without inserting.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    role = f"forge_{args.skill}"

    logger.info("Loading lessons from %s.lessons_current...", args.skill)
    rows = load_lessons(role)
    logger.info("  %d active lessons found", len(rows))
    if not rows:
        logger.warning("Nothing to migrate; exiting.")
        return 0

    existing = count_existing_backfilled_tips(role)
    logger.info("  %d already-backfilled tips present in tips table", existing)

    if existing > 0 and not args.reset:
        logger.warning(
            "Backfilled tips already exist; re-run with --reset to replace them, "
            "or delete this check manually if you want to append. Exiting.")
        return 1

    if args.reset and existing > 0:
        if args.dry_run:
            logger.info("[dry-run] would retire %d prior-backfill tips", existing)
        else:
            n = retire_prior_backfill(role)
            logger.info("Retired %d prior-backfill tips (retired_reason='backfill_reset')", n)

    if args.dry_run:
        logger.info("[dry-run] sample of what would be inserted:")
        for row in rows[:3]:
            tip = lesson_to_tip(row)
            logger.info("  type=%-12s category=%s src_rule=%s text=%r",
                        tip.tip_type, tip.application_context,
                        tip.source_rule_id, tip.text[:100])
        logger.info("[dry-run] total to insert: %d", len(rows))
        return 0

    # Batch the writes in chunks so a single bad row doesn't abort the run.
    writer = TipWriter(skill=args.skill)
    chunk_size = 200
    total_inserted = 0
    for chunk_start in range(0, len(rows), chunk_size):
        chunk = rows[chunk_start:chunk_start + chunk_size]
        tips = [lesson_to_tip(r) for r in chunk]
        try:
            ids = writer.write_many(tips)
            total_inserted += len(ids)
            logger.info("  inserted %d/%d", total_inserted, len(rows))
        except Exception as exc:  # noqa: BLE001
            logger.error("chunk %d-%d failed: %s",
                         chunk_start, chunk_start + len(chunk), exc)
            raise

    logger.info("Backfill complete: %d tips inserted (all tip_type=recovery).",
                total_inserted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
