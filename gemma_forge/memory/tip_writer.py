"""Write structured tips to ``stig.tips``.

Phase F2 of the V2 memory architecture (see docs/drafts/v2-architecture-plan.md
§2.1 and §4). Two call-sites target this module:

  1. Ralph — when the Reflector emits a structured tip block, the harness
     calls ``TipWriter.write`` with the parsed fields plus provenance
     (source attempt id, outcome signal, etc.). Embeddings and trigger
     conditions may be NULL; retrieval (Phase G) can populate them lazily.

  2. The backfill job — reads existing ``lessons_current`` rows and writes
     each as a ``tip_type='recovery'`` tip with ``application_context =
     [category]``. Preserves provenance so the tips table mirrors the
     lessons table 1-for-1 for the transition.

The writer is intentionally minimal in this pass:
  - No embedding generation (Phase G prep).
  - No LLM-driven trigger-condition extraction (Phase G prep).
  - No Neo4j mirror write (Phase G prep when retrieval starts joining
    Rule nodes).

Those are additive later. Getting the Postgres write path right now
means the Reflector prompt change in F-next can flip over without any
schema churn.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from psycopg_pool import ConnectionPool

from gemma_forge.harness.db import get_pool

logger = logging.getLogger(__name__)


_VALID_TIP_TYPES = frozenset({"strategy", "recovery", "optimization", "warning"})


@dataclass
class Tip:
    """A structured memory unit destined for ``stig.tips``.

    Mirrors the schema in ``migrations/stig/0003_tips_schema.sql``
    plus the ``mechanism`` column added in ``0004_tip_mechanism.sql``.
    ``embedding`` and ``trigger_conditions`` are NULL-allowed — Phase G
    populates them as retrieval starts needing them. The Reflector in
    Phase F-next emits trigger_conditions directly when the prompt change
    lands. ``mechanism`` is optional at the dataclass level to tolerate
    backfilled tips; the Reflector parser enforces it for new writes.
    """
    text: str
    tip_type: str = "recovery"                  # strategy | recovery | optimization | warning
    mechanism: Optional[str] = None             # REQUIRED for new tips (Reflector parser enforces)
    trigger_conditions: Optional[list[str]] = None
    application_context: list[str] = field(default_factory=list)
    source_attempt_id: Optional[int] = None
    source_run_id: Optional[str] = None
    source_rule_id: Optional[str] = None
    outcome_at_source_value: Optional[float] = None
    outcome_at_source_confidence: Optional[float] = None
    environment_tag: Optional[str] = None
    # embedding deliberately absent — added by the Phase G backfill/
    # writer once an embedder is wired in.


class TipWriter:
    """Postgres-backed tip writer scoped to one skill.

    Reuses the process-wide connection pool so concurrent writes from
    the harness and the backfill job share connection state.
    """

    def __init__(self, skill: str = "stig", *, pool: Optional[ConnectionPool] = None):
        self.skill = skill
        self._role = f"forge_{skill}"
        self._pool: Optional[ConnectionPool] = pool

    def _conn(self):
        if self._pool is None:
            self._pool = get_pool(self._role)
        return self._pool.connection()

    def write(self, tip: Tip) -> int:
        """Insert ``tip`` and return its new ``tips.id``.

        Validates ``tip_type``; raises ``ValueError`` on unknown value so
        a malformed Reflector output fails loudly rather than quietly
        inserting a bad row.
        """
        if tip.tip_type not in _VALID_TIP_TYPES:
            raise ValueError(
                f"TipWriter.write: invalid tip_type {tip.tip_type!r}; "
                f"expected one of {sorted(_VALID_TIP_TYPES)}"
            )
        if not tip.text or not tip.text.strip():
            raise ValueError("TipWriter.write: tip.text must be non-empty")

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tips (
                        text, tip_type, mechanism, trigger_conditions,
                        application_context, source_attempt_id, source_run_id,
                        source_rule_id, outcome_at_source_value,
                        outcome_at_source_confidence, environment_tag
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        tip.text,
                        tip.tip_type,
                        tip.mechanism,
                        tip.trigger_conditions,        # psycopg maps list→text[]
                        tip.application_context,
                        tip.source_attempt_id,
                        tip.source_run_id,
                        tip.source_rule_id,
                        tip.outcome_at_source_value,
                        tip.outcome_at_source_confidence,
                        tip.environment_tag,
                    ),
                )
                new_id = cur.fetchone()[0]
            conn.commit()
        logger.debug("tip_writer: inserted tip id=%d src_rule=%s type=%s",
                     new_id, tip.source_rule_id, tip.tip_type)
        return new_id

    def write_many(self, tips: list[Tip]) -> list[int]:
        """Insert many tips in one transaction. Returns the new ids in order.

        Backfill uses this to stream ~2.3k lessons into the tips table
        without round-tripping a connection per row.
        """
        if not tips:
            return []
        for tip in tips:
            if tip.tip_type not in _VALID_TIP_TYPES:
                raise ValueError(
                    f"TipWriter.write_many: invalid tip_type {tip.tip_type!r}"
                )
            if not tip.text or not tip.text.strip():
                raise ValueError("TipWriter.write_many: tip.text must be non-empty")

        new_ids: list[int] = []
        with self._conn() as conn:
            with conn.cursor() as cur:
                for tip in tips:
                    cur.execute(
                        """
                        INSERT INTO tips (
                            text, tip_type, mechanism, trigger_conditions,
                            application_context, source_attempt_id, source_run_id,
                            source_rule_id, outcome_at_source_value,
                            outcome_at_source_confidence, environment_tag
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            tip.text,
                            tip.tip_type,
                            tip.mechanism,
                            tip.trigger_conditions,
                            tip.application_context,
                            tip.source_attempt_id,
                            tip.source_run_id,
                            tip.source_rule_id,
                            tip.outcome_at_source_value,
                            tip.outcome_at_source_confidence,
                            tip.environment_tag,
                        ),
                    )
                    new_ids.append(cur.fetchone()[0])
            conn.commit()
        logger.info("tip_writer: inserted %d tips", len(new_ids))
        return new_ids
