"""Persistent cross-run memory store — Postgres backend.

The MemoryStore persists decision traces, strategic lessons, and
difficulty estimates across runs. Phase C3 of the memory refactor
(ADR-0016) replaced the prior SQLite implementation with a Postgres-
backed one running against the shared Supabase Postgres on the host
(database ``gemma_forge``, schema named after the skill).

Schema (per skill, in ``stig.*``):
  runs             — one row per harness execution
  work_items       — outcomes per work item per run
  attempts         — decision traces: approach, evaluation, reflection
  lessons_current  — cross-run lessons (legacy ``weight``; the dream
                     pass writes ``confidence`` separately)

Design: the harness interacts through ``MemoryStoreProtocol``. This
module provides the Postgres implementation. The Reflective tier
(Neo4j + Graphiti) is the source of truth for lessons; the Postgres
``lessons_current`` table is a fast read-side projection rebuilt by
the dream pass between runs.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from psycopg_pool import ConnectionPool

from gemma_forge.harness.db import get_pool

logger = logging.getLogger(__name__)


# -- Data types --------------------------------------------------------------


@dataclass
class StoredLesson:
    """A strategic lesson learned from one or more runs."""
    id: int = 0
    category: str = ""
    lesson: str = ""
    source_run_id: str = ""
    source_item_id: str = ""
    success_count: int = 0
    failure_count: int = 0
    weight: float = 0.5  # frequency-driven; the dream pass adds confidence separately


@dataclass
class StoredAttempt:
    """A single attempt trace from a prior run."""
    run_id: str = ""
    item_id: str = ""
    attempt_num: int = 0
    approach: str = ""
    eval_passed: bool = False
    failure_mode: str = ""
    reflection: str = ""
    lesson: str = ""
    banned_pattern: str = ""
    wall_time_s: float = 0.0


@dataclass
class CategoryStats:
    """Aggregated performance stats for a category — feeds the clutch."""
    category: str = ""
    total_items: int = 0
    completed: int = 0
    escalated: int = 0
    success_rate: float = 0.0
    avg_attempts: float = 0.0
    avg_wall_time_s: float = 0.0
    total_runs_seen: int = 0


# -- Protocol ----------------------------------------------------------------


@runtime_checkable
class MemoryStoreProtocol(Protocol):
    """Abstract memory persistence — Postgres-backed in production,
    pluggable for tests and future backends."""

    def initialize(self) -> None: ...
    def start_run(self, skill_name: str, config: dict) -> str: ...
    def end_run(self, run_id: str, summary: dict) -> None: ...

    def save_item_outcome(self, run_id: str, item_id: str, title: str,
                          category: str, outcome: str, attempts: int,
                          wall_time_s: float) -> None: ...

    def save_attempt(self, run_id: str, item_id: str, attempt_num: int,
                     approach: str, eval_passed: bool, failure_mode: str,
                     reflection: str, lesson: str, banned_pattern: str,
                     wall_time_s: float) -> None: ...

    def save_lesson(self, category: str, lesson: str, run_id: str,
                    item_id: str) -> None: ...
    def update_lesson_weight(self, lesson_id: int, success: bool) -> None: ...

    def load_lessons(self, category: str, min_weight: float = 0.0,
                     limit: int = 10) -> list[StoredLesson]: ...
    def load_global_bans(self) -> list[str]: ...
    def query_prior_attempts(self, item_id: str,
                             limit: int = 10) -> list[StoredAttempt]: ...
    def get_category_stats(self) -> list[CategoryStats]: ...
    def get_run_count(self) -> int: ...


# -- Postgres implementation -------------------------------------------------


class PostgresMemoryStore:
    """Postgres-backed memory store, scoped to one skill schema.

    Reuses the process-wide connection pool from ``gemma_forge.harness.db``.
    The pool's role (``forge_<skill>``) has its ``search_path`` pinned at
    bootstrap time so unqualified table names resolve into the skill schema.
    """

    def __init__(self, skill: str = "stig", *, pool: Optional[ConnectionPool] = None):
        self.skill = skill
        self._role = f"forge_{skill}"
        self._pool: Optional[ConnectionPool] = pool

    # -- Lifecycle -----------------------------------------------------------

    def initialize(self) -> None:
        """Open / verify the connection pool. Schema is applied separately
        by ``tools/apply_migrations.sh``; this method only proves we can talk."""
        if self._pool is None:
            self._pool = get_pool(self._role)
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_user, current_database(), current_setting('search_path')")
                user, db, path = cur.fetchone()
        logger.info(
            "Memory store ready: user=%s db=%s search_path=%s skill=%s",
            user, db, path, self.skill,
        )

    def close(self) -> None:
        """Pool lifetime is process-wide; nothing to close per-store."""
        self._pool = None

    def _conn(self):
        if self._pool is None:
            self.initialize()
        return self._pool.connection()

    # -- Run lifecycle -------------------------------------------------------

    def start_run(self, skill_name: str, config: dict) -> str:
        run_id = str(uuid.uuid4())[:12]
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO runs (id, skill, started_at, config) "
                    "VALUES (%s, %s, to_timestamp(%s), %s::jsonb)",
                    (run_id, skill_name, time.time(), json.dumps(config)),
                )
            conn.commit()
        logger.info("Memory store: run %s started (skill=%s)", run_id, skill_name)
        return run_id

    def end_run(self, run_id: str, summary: dict) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE runs SET ended_at = to_timestamp(%s), summary = %s::jsonb WHERE id = %s",
                    (time.time(), json.dumps(summary), run_id),
                )
            conn.commit()

    # -- Item outcomes -------------------------------------------------------

    def save_item_outcome(self, run_id: str, item_id: str, title: str,
                          category: str, outcome: str, attempts: int,
                          wall_time_s: float) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO work_items
                        (run_id, item_id, title, category, outcome, attempts, wall_time_s)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, item_id) DO UPDATE SET
                        title = EXCLUDED.title,
                        category = EXCLUDED.category,
                        outcome = EXCLUDED.outcome,
                        attempts = EXCLUDED.attempts,
                        wall_time_s = EXCLUDED.wall_time_s
                    """,
                    (run_id, item_id, title, category, outcome, attempts, wall_time_s),
                )
            conn.commit()

    # -- Attempt traces ------------------------------------------------------

    def save_attempt(self, run_id: str, item_id: str, attempt_num: int,
                     approach: str, eval_passed: bool, failure_mode: str,
                     reflection: str, lesson: str, banned_pattern: str,
                     wall_time_s: float) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO attempts
                        (run_id, item_id, attempt_num, approach, eval_passed,
                         failure_mode, reflection, lesson, banned_pattern,
                         wall_time_s, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                    """,
                    (
                        run_id, item_id, attempt_num, approach[:500],
                        bool(eval_passed), failure_mode,
                        reflection[:500], lesson[:200], banned_pattern[:200],
                        wall_time_s,
                    ),
                )
            conn.commit()

    # -- Lessons (cross-run meta-cognitions) ---------------------------------

    def save_lesson(self, category: str, lesson: str, run_id: str,
                    item_id: str) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                # Deduplicate on (category, exact lesson text). Repeated lessons
                # boost the legacy frequency weight; the dream pass owns confidence.
                cur.execute(
                    "SELECT id FROM lessons_current WHERE category = %s AND lesson = %s",
                    (category, lesson),
                )
                row = cur.fetchone()
                if row is not None:
                    cur.execute(
                        "UPDATE lessons_current SET "
                        "    weight = LEAST(weight + 0.1, 1.0), "
                        "    success_count = success_count + 1, "
                        "    updated_at = now() "
                        "WHERE id = %s",
                        (row[0],),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO lessons_current
                            (category, lesson, source_run_id, source_item_id, weight)
                        VALUES (%s, %s, %s, %s, 0.5)
                        """,
                        (category, lesson, run_id, item_id),
                    )
            conn.commit()

    def update_lesson_weight(self, lesson_id: int, success: bool) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                if success:
                    cur.execute(
                        "UPDATE lessons_current SET "
                        "    weight = LEAST(weight + 0.1, 1.0), "
                        "    success_count = success_count + 1, "
                        "    updated_at = now() "
                        "WHERE id = %s",
                        (lesson_id,),
                    )
                else:
                    cur.execute(
                        "UPDATE lessons_current SET "
                        "    weight = GREATEST(weight - 0.05, 0.0), "
                        "    failure_count = failure_count + 1, "
                        "    updated_at = now() "
                        "WHERE id = %s",
                        (lesson_id,),
                    )
            conn.commit()

    # -- Read path -----------------------------------------------------------

    def load_lessons(self, category: str, min_weight: float = 0.0,
                     limit: int = 10) -> list[StoredLesson]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, category, lesson, source_run_id, source_item_id,
                           success_count, failure_count, weight
                    FROM lessons_current
                    WHERE category = %s AND weight >= %s
                    ORDER BY weight DESC, id
                    LIMIT %s
                    """,
                    (category, min_weight, limit),
                )
                rows = cur.fetchall()
        return [
            StoredLesson(
                id=r[0], category=r[1], lesson=r[2],
                source_run_id=r[3] or "", source_item_id=r[4] or "",
                success_count=r[5] or 0, failure_count=r[6] or 0,
                weight=float(r[7]) if r[7] is not None else 0.0,
            )
            for r in rows
        ]

    def load_all_lessons(self, min_weight: float = 0.2,
                         limit: int = 30) -> list[StoredLesson]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, category, lesson, source_run_id, source_item_id,
                           success_count, failure_count, weight
                    FROM lessons_current
                    WHERE weight >= %s
                    ORDER BY weight DESC, id
                    LIMIT %s
                    """,
                    (min_weight, limit),
                )
                rows = cur.fetchall()
        return [
            StoredLesson(
                id=r[0], category=r[1], lesson=r[2],
                source_run_id=r[3] or "", source_item_id=r[4] or "",
                success_count=r[5] or 0, failure_count=r[6] or 0,
                weight=float(r[7]) if r[7] is not None else 0.0,
            )
            for r in rows
        ]

    def load_global_bans(self) -> list[str]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT banned_pattern
                    FROM attempts
                    WHERE banned_pattern IS NOT NULL AND banned_pattern <> ''
                    ORDER BY banned_pattern
                    LIMIT 50
                    """
                )
                rows = cur.fetchall()
        return [r[0] for r in rows]

    def query_prior_attempts(self, item_id: str,
                             limit: int = 10) -> list[StoredAttempt]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT run_id, item_id, attempt_num, approach, eval_passed,
                           failure_mode, reflection, lesson, banned_pattern, wall_time_s
                    FROM attempts
                    WHERE item_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (item_id, limit),
                )
                rows = cur.fetchall()
        return [
            StoredAttempt(
                run_id=r[0], item_id=r[1], attempt_num=r[2],
                approach=r[3] or "", eval_passed=bool(r[4]),
                failure_mode=r[5] or "", reflection=r[6] or "",
                lesson=r[7] or "", banned_pattern=r[8] or "",
                wall_time_s=float(r[9]) if r[9] is not None else 0.0,
            )
            for r in rows
        ]

    # -- Difficulty model (for the clutch) -----------------------------------

    def get_category_stats(self) -> list[CategoryStats]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        category,
                        COUNT(*)::int                                                AS total,
                        SUM(CASE WHEN outcome = 'completed' THEN 1 ELSE 0 END)::int  AS completed,
                        SUM(CASE WHEN outcome = 'escalated' THEN 1 ELSE 0 END)::int  AS escalated,
                        AVG(CASE WHEN outcome = 'completed' THEN 1.0 ELSE 0.0 END)   AS success_rate,
                        AVG(attempts)                                                 AS avg_attempts,
                        AVG(wall_time_s)                                              AS avg_time,
                        COUNT(DISTINCT run_id)::int                                   AS runs_seen
                    FROM work_items
                    WHERE outcome IN ('completed', 'escalated')
                    GROUP BY category
                    ORDER BY success_rate DESC NULLS LAST
                    """
                )
                rows = cur.fetchall()
        return [
            CategoryStats(
                category=r[0], total_items=r[1], completed=r[2],
                escalated=r[3],
                success_rate=float(r[4]) if r[4] is not None else 0.0,
                avg_attempts=float(r[5]) if r[5] is not None else 0.0,
                avg_wall_time_s=float(r[6]) if r[6] is not None else 0.0,
                total_runs_seen=r[7],
            )
            for r in rows
        ]

    def get_run_count(self) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM runs")
                row = cur.fetchone()
        return int(row[0]) if row else 0

    # -- Summary -------------------------------------------------------------

    def summary(self) -> str:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM runs")
                runs = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM work_items")
                items = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM attempts")
                attempts = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM lessons_current")
                lessons = cur.fetchone()[0]
        return (f"Memory store (skill={self.skill}): "
                f"{runs} runs, {items} items, {attempts} attempts, {lessons} lessons")
