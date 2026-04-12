"""Persistent cross-run memory store — SQLite backend.

The MemoryStore persists decision traces, strategic lessons, and
difficulty estimates across runs. It uses SQLite with WAL mode for
safe concurrent access from parallel workers.

The database file lives at `memory/gemma_forge.db` and is created
automatically on first use. No external dependencies — sqlite3 is
in Python's standard library.

Schema:
  runs          — one row per harness execution
  work_items    — outcomes per work item per run
  attempts      — decision traces: approach, evaluation, reflection
  lessons       — cross-run meta-cognitions with learned weights
  difficulty    — per-category performance model for the clutch

Design: the harness interacts through the MemoryStore protocol
(defined in interfaces.py). This module provides the SQLite
implementation. If someone needs PostgreSQL at enterprise scale,
they implement the same protocol against a different backend.
"""

import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# -- Data types for the memory store -----------------------------------------

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
    weight: float = 0.5  # learned importance — higher = more valuable

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


# -- MemoryStore protocol ----------------------------------------------------

@runtime_checkable
class MemoryStoreProtocol(Protocol):
    """Abstract memory persistence — SQLite now, upgradeable later."""

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


# -- SQLite implementation ---------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    skill TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,
    config TEXT,
    summary TEXT
);

CREATE TABLE IF NOT EXISTS work_items (
    run_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    title TEXT,
    category TEXT,
    outcome TEXT,
    attempts INTEGER DEFAULT 0,
    wall_time_s REAL DEFAULT 0.0,
    PRIMARY KEY (run_id, item_id),
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    attempt_num INTEGER NOT NULL,
    approach TEXT,
    eval_passed INTEGER DEFAULT 0,
    failure_mode TEXT,
    reflection TEXT,
    lesson TEXT,
    banned_pattern TEXT,
    wall_time_s REAL DEFAULT 0.0,
    created_at REAL NOT NULL,
    FOREIGN KEY (run_id, item_id) REFERENCES work_items(run_id, item_id)
);

CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    lesson TEXT NOT NULL,
    source_run_id TEXT,
    source_item_id TEXT,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    weight REAL DEFAULT 0.5,
    created_at REAL NOT NULL,
    FOREIGN KEY (source_run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_work_items_category ON work_items(category);
CREATE INDEX IF NOT EXISTS idx_attempts_item ON attempts(run_id, item_id);
CREATE INDEX IF NOT EXISTS idx_lessons_category ON lessons(category);
CREATE INDEX IF NOT EXISTS idx_lessons_weight ON lessons(weight DESC);
"""


class SQLiteMemoryStore:
    """SQLite-backed persistent memory store.

    Uses WAL mode for concurrent read/write safety — critical for the
    adaptive concurrency clutch where workers read difficulty estimates
    while other workers write completion results simultaneously.
    """

    def __init__(self, db_path: str = "memory/gemma_forge.db"):
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    def initialize(self) -> None:
        """Create the database and schema if they don't exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,  # safe with WAL mode
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()
        logger.info("Memory store initialized: %s", self.db_path)

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.initialize()
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # -- Run lifecycle -------------------------------------------------------

    def start_run(self, skill_name: str, config: dict) -> str:
        run_id = str(uuid.uuid4())[:12]
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO runs (id, skill, started_at, config) VALUES (?, ?, ?, ?)",
            (run_id, skill_name, time.time(), json.dumps(config)),
        )
        conn.commit()
        logger.info("Memory store: run %s started (skill=%s)", run_id, skill_name)
        return run_id

    def end_run(self, run_id: str, summary: dict) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE runs SET ended_at = ?, summary = ? WHERE id = ?",
            (time.time(), json.dumps(summary), run_id),
        )
        conn.commit()

    # -- Item outcomes -------------------------------------------------------

    def save_item_outcome(self, run_id: str, item_id: str, title: str,
                          category: str, outcome: str, attempts: int,
                          wall_time_s: float) -> None:
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO work_items
               (run_id, item_id, title, category, outcome, attempts, wall_time_s)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, item_id, title, category, outcome, attempts, wall_time_s),
        )
        conn.commit()

    # -- Attempt traces ------------------------------------------------------

    def save_attempt(self, run_id: str, item_id: str, attempt_num: int,
                     approach: str, eval_passed: bool, failure_mode: str,
                     reflection: str, lesson: str, banned_pattern: str,
                     wall_time_s: float) -> None:
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO attempts
               (run_id, item_id, attempt_num, approach, eval_passed,
                failure_mode, reflection, lesson, banned_pattern,
                wall_time_s, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, item_id, attempt_num, approach[:500],
             1 if eval_passed else 0, failure_mode,
             reflection[:500], lesson[:200], banned_pattern[:200],
             wall_time_s, time.time()),
        )
        conn.commit()

    # -- Lessons (cross-run meta-cognitions) ---------------------------------

    def save_lesson(self, category: str, lesson: str, run_id: str,
                    item_id: str) -> None:
        conn = self._get_conn()
        # Check for duplicate lessons (same text, same category)
        existing = conn.execute(
            "SELECT id FROM lessons WHERE category = ? AND lesson = ?",
            (category, lesson),
        ).fetchone()
        if existing:
            # Boost weight of repeated lessons — they're more likely valuable
            conn.execute(
                "UPDATE lessons SET weight = MIN(weight + 0.1, 1.0), "
                "success_count = success_count + 1 WHERE id = ?",
                (existing[0],),
            )
        else:
            conn.execute(
                """INSERT INTO lessons
                   (category, lesson, source_run_id, source_item_id,
                    created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (category, lesson, run_id, item_id, time.time()),
            )
        conn.commit()

    def update_lesson_weight(self, lesson_id: int, success: bool) -> None:
        """Adjust a lesson's weight based on whether following it led to success."""
        conn = self._get_conn()
        if success:
            conn.execute(
                "UPDATE lessons SET weight = MIN(weight + 0.1, 1.0), "
                "success_count = success_count + 1 WHERE id = ?",
                (lesson_id,),
            )
        else:
            conn.execute(
                "UPDATE lessons SET weight = MAX(weight - 0.05, 0.0), "
                "failure_count = failure_count + 1 WHERE id = ?",
                (lesson_id,),
            )
        conn.commit()

    # -- Read path (cross-run retrieval) -------------------------------------

    def load_lessons(self, category: str, min_weight: float = 0.0,
                     limit: int = 10) -> list[StoredLesson]:
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT id, category, lesson, source_run_id, source_item_id,
                      success_count, failure_count, weight
               FROM lessons
               WHERE category = ? AND weight >= ?
               ORDER BY weight DESC
               LIMIT ?""",
            (category, min_weight, limit),
        ).fetchall()
        return [
            StoredLesson(id=r[0], category=r[1], lesson=r[2],
                         source_run_id=r[3], source_item_id=r[4],
                         success_count=r[5], failure_count=r[6], weight=r[7])
            for r in rows
        ]

    def load_all_lessons(self, min_weight: float = 0.2,
                         limit: int = 30) -> list[StoredLesson]:
        """Load top lessons across all categories."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT id, category, lesson, source_run_id, source_item_id,
                      success_count, failure_count, weight
               FROM lessons
               WHERE weight >= ?
               ORDER BY weight DESC
               LIMIT ?""",
            (min_weight, limit),
        ).fetchall()
        return [
            StoredLesson(id=r[0], category=r[1], lesson=r[2],
                         source_run_id=r[3], source_item_id=r[4],
                         success_count=r[5], failure_count=r[6], weight=r[7])
            for r in rows
        ]

    def load_global_bans(self) -> list[str]:
        """Load all banned patterns from prior runs."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT DISTINCT banned_pattern FROM attempts
               WHERE banned_pattern IS NOT NULL AND banned_pattern != ''
               ORDER BY created_at DESC
               LIMIT 50""",
        ).fetchall()
        return [r[0] for r in rows]

    def query_prior_attempts(self, item_id: str,
                             limit: int = 10) -> list[StoredAttempt]:
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT run_id, item_id, attempt_num, approach, eval_passed,
                      failure_mode, reflection, lesson, banned_pattern,
                      wall_time_s
               FROM attempts
               WHERE item_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (item_id, limit),
        ).fetchall()
        return [
            StoredAttempt(
                run_id=r[0], item_id=r[1], attempt_num=r[2],
                approach=r[3], eval_passed=bool(r[4]), failure_mode=r[5],
                reflection=r[6], lesson=r[7], banned_pattern=r[8],
                wall_time_s=r[9],
            )
            for r in rows
        ]

    # -- Difficulty model (for the clutch) -----------------------------------

    def get_category_stats(self) -> list[CategoryStats]:
        """Aggregate performance stats by category across all runs."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT
                 category,
                 COUNT(*) as total,
                 SUM(CASE WHEN outcome = 'completed' THEN 1 ELSE 0 END) as completed,
                 SUM(CASE WHEN outcome = 'escalated' THEN 1 ELSE 0 END) as escalated,
                 AVG(CASE WHEN outcome = 'completed' THEN 1.0 ELSE 0.0 END) as success_rate,
                 AVG(attempts) as avg_attempts,
                 AVG(wall_time_s) as avg_time,
                 COUNT(DISTINCT run_id) as runs_seen
               FROM work_items
               WHERE outcome IN ('completed', 'escalated')
               GROUP BY category
               ORDER BY success_rate DESC""",
        ).fetchall()
        return [
            CategoryStats(
                category=r[0], total_items=r[1], completed=r[2],
                escalated=r[3], success_rate=r[4], avg_attempts=r[5],
                avg_wall_time_s=r[6], total_runs_seen=r[7],
            )
            for r in rows
        ]

    def get_run_count(self) -> int:
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) FROM runs").fetchone()
        return row[0] if row else 0

    # -- Summary for logging -------------------------------------------------

    def summary(self) -> str:
        """Human-readable summary of what's in the memory store."""
        conn = self._get_conn()
        runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        items = conn.execute("SELECT COUNT(*) FROM work_items").fetchone()[0]
        attempts = conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        lessons = conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
        return (f"Memory store: {runs} runs, {items} items, "
                f"{attempts} attempts, {lessons} lessons")
