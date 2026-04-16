#!/usr/bin/env python
"""tools/migrate_sqlite_to_postgres.py — Phase C migration.

One-shot import of the retired SQLite ``memory/stig-rhel9.db`` plus the
``runs/*.jsonl`` event log corpus into the new ``stig`` schema in the
shared Supabase Postgres (ADR-0016).

Maps:
    SQLite runs                  -> stig.runs
    SQLite work_items            -> stig.work_items
    SQLite attempts              -> stig.attempts
    SQLite lessons               -> stig.lessons_current  (frequency tier)
    runs/*.jsonl  (each run)     -> stig.runs (UPSERT) + stig.run_events

The lessons land in ``lessons_current`` with ``confidence`` left NULL.
The Phase D dream pass populates ``confidence`` from outcome data; until
then, the harness reads the legacy ``weight`` column unchanged.

Modes:
    --dry-run                Plan only. No writes.
    --reset                  TRUNCATE the destination tables first.
                             Required if any data is already present.
    --skip-smoke-tests       Default: ignore JSONL files with <100 events.
    --include-smoke-tests    Override the above and ingest everything.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from pathlib import Path

import psycopg
from psycopg import sql

REPO_ROOT = Path(__file__).resolve().parents[1]
SQLITE_PATH = REPO_ROOT / "memory" / "stig-rhel9.db"
RUNS_DIR = REPO_ROOT / "runs"
SMOKE_TEST_THRESHOLD = 100  # JSONL files with fewer events are skipped by default


def load_env() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        sys.exit(f"migrate: {env_path} not found")
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def epoch_to_ts(value: float | None) -> dt.datetime | None:
    if value is None:
        return None
    return dt.datetime.fromtimestamp(float(value), tz=dt.timezone.utc)


def parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def jsonl_files(include_smoke: bool) -> list[Path]:
    files = sorted(RUNS_DIR.glob("*.jsonl"))
    if include_smoke:
        return files
    return [f for f in files if sum(1 for _ in f.open()) >= SMOKE_TEST_THRESHOLD]


def truncate_destination(cur: psycopg.Cursor) -> None:
    """Wipe stig.* of historical data. CASCADE so FK chains drop together.

    We keep ``migrations_applied`` so the schema isn't reapplied. Sequences
    are restarted so IDs read clean post-migration.
    """
    print("migrate: --reset → truncating destination tables...")
    cur.execute(
        "TRUNCATE TABLE "
        "stig.run_events, stig.attempts, stig.work_items, "
        "stig.turns, stig.lessons_current, stig.runs "
        "RESTART IDENTITY CASCADE"
    )


def migrate_sqlite(conn: psycopg.Connection, sqlite_path: Path, dry_run: bool) -> dict[str, int]:
    counts: dict[str, int] = {"runs": 0, "work_items": 0, "attempts": 0, "lessons": 0}
    if not sqlite_path.is_file():
        print(f"migrate: SQLite source {sqlite_path} not found, skipping legacy migration")
        return counts

    sq = sqlite3.connect(sqlite_path)
    sq.row_factory = sqlite3.Row
    cur = conn.cursor() if not dry_run else None

    # runs
    rows = sq.execute("SELECT id, skill, started_at, ended_at, config, summary FROM runs").fetchall()
    counts["runs"] = len(rows)
    if not dry_run:
        for r in rows:
            cur.execute(
                """
                INSERT INTO stig.runs (id, skill, started_at, ended_at, config, summary)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    r["id"],
                    r["skill"] or "stig",
                    epoch_to_ts(r["started_at"]),
                    epoch_to_ts(r["ended_at"]),
                    r["config"] or "{}",
                    r["summary"] or "{}",
                ),
            )

    # work_items
    rows = sq.execute(
        "SELECT run_id, item_id, title, category, outcome, attempts, wall_time_s FROM work_items"
    ).fetchall()
    counts["work_items"] = len(rows)
    if not dry_run:
        for r in rows:
            cur.execute(
                """
                INSERT INTO stig.work_items
                    (run_id, item_id, title, category, outcome, attempts, wall_time_s)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id, item_id) DO NOTHING
                """,
                (
                    r["run_id"],
                    r["item_id"],
                    r["title"],
                    r["category"],
                    r["outcome"],
                    r["attempts"] or 0,
                    r["wall_time_s"] or 0.0,
                ),
            )

    # attempts (skip rows referencing run_ids/items we didn't migrate — the
    # legacy `_global_ban` pseudo-rows have item_id='_global_ban' with no
    # matching work_item; insert them with item_id NULL is messy so just drop
    # them — banned patterns also live in stig.attempts.banned_pattern of
    # actual attempts).
    rows = sq.execute(
        """
        SELECT a.run_id, a.item_id, a.attempt_num, a.approach, a.eval_passed,
               a.failure_mode, a.reflection, a.lesson, a.banned_pattern,
               a.wall_time_s, a.created_at
        FROM attempts a
        WHERE a.item_id != '_global_ban'
          AND EXISTS (SELECT 1 FROM work_items w WHERE w.run_id=a.run_id AND w.item_id=a.item_id)
        """
    ).fetchall()
    counts["attempts"] = len(rows)
    if not dry_run:
        for r in rows:
            cur.execute(
                """
                INSERT INTO stig.attempts
                    (run_id, item_id, attempt_num, approach, eval_passed,
                     failure_mode, reflection, lesson, banned_pattern,
                     wall_time_s, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    r["run_id"],
                    r["item_id"],
                    r["attempt_num"],
                    r["approach"],
                    bool(r["eval_passed"]),
                    r["failure_mode"],
                    r["reflection"],
                    r["lesson"],
                    r["banned_pattern"],
                    r["wall_time_s"] or 0.0,
                    epoch_to_ts(r["created_at"]),
                ),
            )

    # lessons → lessons_current. confidence is left NULL until the dream pass runs.
    rows = sq.execute(
        """
        SELECT category, lesson, source_run_id, source_item_id,
               success_count, failure_count, weight, created_at
        FROM lessons
        """
    ).fetchall()
    counts["lessons"] = len(rows)
    if not dry_run:
        for r in rows:
            cur.execute(
                """
                INSERT INTO stig.lessons_current
                    (category, lesson, source_run_id, source_item_id,
                     success_count, failure_count, weight, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    r["category"],
                    r["lesson"],
                    r["source_run_id"],
                    r["source_item_id"],
                    r["success_count"] or 0,
                    r["failure_count"] or 0,
                    r["weight"] or 0.5,
                    epoch_to_ts(r["created_at"]),
                ),
            )

    if not dry_run:
        cur.close()
    sq.close()
    return counts


def ingest_jsonl_run(
    conn: psycopg.Connection,
    path: Path,
    dry_run: bool,
) -> tuple[str | None, int]:
    """Ingest a single JSONL run file → stig.runs (upsert) + stig.run_events.

    Returns (run_id, event_count).
    """
    cur = conn.cursor() if not dry_run else None

    run_id: str | None = None
    started_at: dt.datetime | None = None
    ended_at: dt.datetime | None = None
    config: dict | None = None
    summary: dict | None = None
    rows: list[tuple] = []

    with path.open() as fh:
        for line in fh:
            ev = json.loads(line)
            ts = parse_iso(ev.get("timestamp"))
            elapsed = float(ev.get("elapsed_s", 0.0))
            etype = ev.get("event_type", "unknown")
            agent = ev.get("agent")
            iteration = ev.get("iteration")
            data = ev.get("data") or {}
            rule_id = data.get("rule_id") if isinstance(data, dict) else None

            if etype == "run_start" and run_id is None:
                run_id = data.get("run_id") or path.stem.replace("run-", "")
                started_at = parse_iso(data.get("start_time")) or ts
                config = data.get("config")
            if etype == "run_complete":
                ended_at = ts
                summary = data

            if run_id is None:
                # Some smoke tests have no run_start. Synthesize from the
                # filename so the FK to stig.runs is still satisfiable.
                run_id = path.stem.replace("run-", "") + "-orphan"
                started_at = ts

            rows.append(
                (run_id, ts, elapsed, etype, agent, iteration, rule_id, json.dumps(data))
            )

    if not rows:
        if cur is not None:
            cur.close()
        return None, 0

    if dry_run:
        return run_id, len(rows)

    # Idempotency: if any events already exist for this run_id, skip the
    # whole run. Truncating the table is the supported re-run path.
    cur.execute("SELECT 1 FROM stig.run_events WHERE run_id = %s LIMIT 1", (run_id,))
    if cur.fetchone() is not None:
        cur.close()
        return run_id, 0

    # Ensure the runs row exists. Don't overwrite a legacy SQLite-imported row.
    cur.execute(
        """
        INSERT INTO stig.runs (id, skill, started_at, ended_at, config, summary)
        VALUES (%s, 'stig', %s, %s, %s::jsonb, %s::jsonb)
        ON CONFLICT (id) DO NOTHING
        """,
        (
            run_id,
            started_at or rows[0][1],
            ended_at,
            json.dumps(config or {}),
            json.dumps(summary or {}),
        ),
    )

    # Bulk insert events via COPY for speed (52K rows total across the corpus).
    with cur.copy(
        "COPY stig.run_events "
        "(run_id, ts, elapsed_s, event_type, agent, iteration, rule_id, data) "
        "FROM STDIN"
    ) as copy:
        for row in rows:
            copy.write_row(row)

    cur.close()
    return run_id, len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="Plan only, no writes.")
    ap.add_argument("--reset", action="store_true", help="TRUNCATE destination tables first.")
    ap.add_argument(
        "--include-smoke-tests",
        action="store_true",
        help=f"Include JSONL files with fewer than {SMOKE_TEST_THRESHOLD} events.",
    )
    args = ap.parse_args()

    load_env()

    pg_host = os.environ.get("PG_HOST", "127.0.0.1")
    pg_port = os.environ.get("PG_PORT", "5432")
    pg_db = os.environ.get("PG_DATABASE", "gemma_forge")
    # The migration runs as forge_admin so ALL stig.* tables are writable.
    admin_pw = os.environ.get("PG_FORGE_ADMIN_PASSWORD")
    if not admin_pw:
        sys.exit("migrate: PG_FORGE_ADMIN_PASSWORD missing from .env")

    conninfo = f"host={pg_host} port={pg_port} dbname={pg_db} user=forge_admin password={admin_pw} options=-c\\ search_path=stig"

    print(f"migrate: connecting to {pg_host}:{pg_port}/{pg_db} as forge_admin (dry_run={args.dry_run})")

    with psycopg.connect(conninfo) as conn:
        cur = conn.cursor()

        # Sanity: check existing data.
        cur.execute("SELECT COUNT(*) FROM stig.runs")
        existing_runs = cur.fetchone()[0]
        if existing_runs > 0 and not args.reset and not args.dry_run:
            sys.exit(
                f"migrate: stig.runs already has {existing_runs} rows. "
                "Use --reset to wipe and re-import, or --dry-run to plan."
            )

        if args.reset and not args.dry_run:
            truncate_destination(cur)

        # 1. Legacy SQLite import.
        sqlite_counts = migrate_sqlite(conn, SQLITE_PATH, args.dry_run)
        print(
            f"migrate: SQLite plan/done — runs={sqlite_counts['runs']} "
            f"work_items={sqlite_counts['work_items']} attempts={sqlite_counts['attempts']} "
            f"lessons={sqlite_counts['lessons']}"
        )

        # 2. JSONL event log import.
        files = jsonl_files(args.include_smoke_tests)
        print(f"migrate: {len(files)} JSONL file(s) to process "
              f"({'including' if args.include_smoke_tests else 'excluding'} smoke tests)")
        total_events = 0
        for f in files:
            run_id, n = ingest_jsonl_run(conn, f, args.dry_run)
            total_events += n
            tag = "PLAN" if args.dry_run else ("SKIP-already-loaded" if n == 0 and run_id else "OK")
            print(f"  {tag:10s} {f.name:40s} run_id={run_id} events={n}")
        print(f"migrate: total events {'planned' if args.dry_run else 'inserted'}: {total_events}")

        if args.dry_run:
            conn.rollback()
            print("migrate: dry-run — no commit.")
        else:
            conn.commit()
            print("migrate: commit.")

        # Final counts (read-only, also runs in dry-run for sanity).
        for tbl in ("runs", "work_items", "attempts", "lessons_current", "run_events", "turns"):
            cur.execute(f"SELECT COUNT(*) FROM stig.{tbl}")
            print(f"  stig.{tbl}: {cur.fetchone()[0]}")


if __name__ == "__main__":
    main()
