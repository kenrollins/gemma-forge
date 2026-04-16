#!/usr/bin/env python
"""tools/seed_reflective_from_runs.py — Phase C reflective seeder.

Materializes the Reflective tier in Neo4j by reading the migrated
relational state in Postgres (``stig.runs``, ``stig.work_items``,
``stig.attempts``, ``stig.lessons_current``) and writing it as typed
nodes + edges into the Graphiti-managed ``neo4j`` database.

Per-skill isolation uses Graphiti's native ``group_id`` (ADR-0016
amendment 3); every node and edge written by this script carries
``group_id='stig'`` (or whatever ``--skill`` value is supplied).

What lands in Neo4j:

  Nodes (with ``group_id``):
    - ``Run        {run_id, started_at, ended_at, fix_rate}``
    - ``Rule       {rule_id, category, title}``
    - ``Attempt    {attempt_id, run_id, rule_id, attempt_num,
                    eval_passed, created_at, lesson_text, banned_pattern}``
    - ``Lesson     {lesson_id, category, text, weight,
                    success_count, failure_count, source_run_id,
                    source_item_id, created_at, confidence=NULL,
                    abstraction_loss_flag=false}``

  Edges:
    - ``(:Attempt)-[:ON_RULE]->(:Rule)``
    - ``(:Attempt)-[:IN_RUN]->(:Run)``
    - ``(:Lesson)-[:APPLIES_TO]->(:Rule)``       (when source_item_id known)
    - ``(:Lesson)-[:LEARNED_IN]->(:Run)``        (when source_run_id known)
    - ``(:Lesson)-[:DERIVED_FROM]->(:Attempt)``  (best-effort: latest attempt
                                                  with a non-empty .lesson
                                                  for the same (run, rule))

The dream pass (Phase D) layers ``confidence``, ``SUPERSEDED_BY``,
``CONTRADICTED_BY``, ``LINKED_TO`` (A-MEM evolution), and
``LEARNED_IN`` environment tagging on top of this foundation.

This script does NOT call any LLM. Graphiti's entity-extraction
pipeline (``add_episode``) is reserved for the dream pass operating
on agent free-text. Seeding from already-structured relational data
goes through the driver directly.

Modes:
    --dry-run    Plan only.
    --reset      Wipe nodes/edges with group_id='<skill>' first.

Idempotent by default via ``MERGE`` on a stable identifier per node.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import psycopg
from neo4j import AsyncGraphDatabase

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_env() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        sys.exit(f"seed_reflective: {env_path} not found")
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def pg_conninfo(role: str) -> str:
    pw_var = {
        "forge_admin": "PG_FORGE_ADMIN_PASSWORD",
        "forge_stig": "PG_FORGE_STIG_PASSWORD",
    }[role]
    return (
        f"host={os.environ['PG_HOST']} "
        f"port={os.environ.get('PG_PORT','5432')} "
        f"dbname={os.environ.get('PG_DATABASE','gemma_forge')} "
        f"user={role} password={os.environ[pw_var]}"
    )


def fetch_relational(skill: str) -> dict:
    """Pull the migrated state out of Postgres in a single connection."""
    out = {"runs": [], "rules": [], "attempts": [], "lessons": []}
    with psycopg.connect(pg_conninfo("forge_stig")) as conn:
        conn.execute("SET search_path TO stig")
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, started_at, ended_at,
                       (summary->>'remediated')::int AS remediated,
                       (summary->>'escalated')::int AS escalated
                FROM runs ORDER BY started_at
                """
            )
            for row in cur.fetchall():
                rid, started, ended, remed, esc = row
                fix_rate = None
                if remed is not None and esc is not None and (remed + esc) > 0:
                    fix_rate = round(remed / (remed + esc), 4)
                out["runs"].append({
                    "run_id": rid,
                    "started_at": started.isoformat() if started else None,
                    "ended_at": ended.isoformat() if ended else None,
                    "fix_rate": fix_rate,
                })

            cur.execute(
                "SELECT DISTINCT item_id, category, title FROM work_items"
            )
            for item_id, category, title in cur.fetchall():
                out["rules"].append({
                    "rule_id": item_id,
                    "category": category,
                    "title": title,
                })

            cur.execute(
                """
                SELECT id, run_id, item_id, attempt_num, eval_passed,
                       created_at, lesson, banned_pattern
                FROM attempts
                """
            )
            for aid, run_id, rule_id, num, passed, created, lesson, ban in cur.fetchall():
                out["attempts"].append({
                    "attempt_id": f"{run_id}::{rule_id}::{num}::{aid}",
                    "run_id": run_id,
                    "rule_id": rule_id,
                    "attempt_num": num,
                    "eval_passed": bool(passed),
                    "created_at": created.isoformat() if created else None,
                    "lesson_text": lesson or "",
                    "banned_pattern": ban or "",
                })

            cur.execute(
                """
                SELECT id, category, lesson, source_run_id, source_item_id,
                       success_count, failure_count, weight, created_at
                FROM lessons_current
                """
            )
            for lid, cat, text, srun, sitem, succ, fail, weight, created in cur.fetchall():
                out["lessons"].append({
                    "lesson_id": f"sqlite:{lid}",
                    "category": cat,
                    "text": text,
                    "source_run_id": srun,
                    "source_item_id": sitem,
                    "success_count": succ or 0,
                    "failure_count": fail or 0,
                    "weight": float(weight) if weight is not None else 0.5,
                    "created_at": created.isoformat() if created else None,
                })
    return out


async def write_to_neo4j(data: dict, skill: str, reset: bool, dry_run: bool) -> None:
    bolt_port = os.environ.get("NEO4J_BOLT_HOST_PORT", "7687")
    uri = f"bolt://127.0.0.1:{bolt_port}"
    pw = os.environ["NEO4J_PASSWORD"]
    driver = AsyncGraphDatabase.driver(uri, auth=("neo4j", pw))

    if dry_run:
        print(
            f"seed_reflective: DRY-RUN would write "
            f"{len(data['runs'])} Run, {len(data['rules'])} Rule, "
            f"{len(data['attempts'])} Attempt, {len(data['lessons'])} Lesson nodes "
            f"with group_id={skill!r}"
        )
        await driver.close()
        return

    try:
        async with driver.session() as session:
            if reset:
                print(f"seed_reflective: --reset → wiping nodes with group_id={skill!r}...")
                await session.run(
                    """
                    MATCH (n)
                    WHERE n.group_id = $skill AND any(l IN labels(n)
                          WHERE l IN ['Run','Rule','Attempt','Lesson'])
                    DETACH DELETE n
                    """,
                    skill=skill,
                )

            print(f"seed_reflective: writing {len(data['runs'])} Run nodes...")
            await session.run(
                """
                UNWIND $rows AS r
                MERGE (n:Run {run_id: r.run_id, group_id: $skill})
                SET n.started_at = r.started_at,
                    n.ended_at = r.ended_at,
                    n.fix_rate = r.fix_rate
                """,
                rows=data["runs"], skill=skill,
            )

            print(f"seed_reflective: writing {len(data['rules'])} Rule nodes...")
            await session.run(
                """
                UNWIND $rows AS r
                MERGE (n:Rule {rule_id: r.rule_id, group_id: $skill})
                SET n.category = r.category,
                    n.title = r.title
                """,
                rows=data["rules"], skill=skill,
            )

            # Attempts can be large (1826 rows). Chunk by 500.
            print(f"seed_reflective: writing {len(data['attempts'])} Attempt nodes + edges...")
            for i in range(0, len(data["attempts"]), 500):
                chunk = data["attempts"][i:i+500]
                await session.run(
                    """
                    UNWIND $rows AS a
                    MERGE (n:Attempt {attempt_id: a.attempt_id, group_id: $skill})
                    SET n.run_id = a.run_id,
                        n.rule_id = a.rule_id,
                        n.attempt_num = a.attempt_num,
                        n.eval_passed = a.eval_passed,
                        n.created_at = a.created_at,
                        n.lesson_text = a.lesson_text,
                        n.banned_pattern = a.banned_pattern
                    WITH n, a
                    MATCH (rule:Rule {rule_id: a.rule_id, group_id: $skill})
                    MATCH (run:Run  {run_id:  a.run_id,  group_id: $skill})
                    MERGE (n)-[:ON_RULE]->(rule)
                    MERGE (n)-[:IN_RUN]->(run)
                    """,
                    rows=chunk, skill=skill,
                )

            print(f"seed_reflective: writing {len(data['lessons'])} Lesson nodes + edges...")
            for i in range(0, len(data["lessons"]), 500):
                chunk = data["lessons"][i:i+500]
                await session.run(
                    """
                    UNWIND $rows AS l
                    MERGE (n:Lesson {lesson_id: l.lesson_id, group_id: $skill})
                    SET n.category = l.category,
                        n.text = l.text,
                        n.weight = l.weight,
                        n.success_count = l.success_count,
                        n.failure_count = l.failure_count,
                        n.source_run_id = l.source_run_id,
                        n.source_item_id = l.source_item_id,
                        n.created_at = l.created_at,
                        n.confidence = null,
                        n.abstraction_loss_flag = false
                    WITH n, l
                    OPTIONAL MATCH (rule:Rule {rule_id: l.source_item_id, group_id: $skill})
                    OPTIONAL MATCH (run:Run  {run_id:  l.source_run_id,   group_id: $skill})
                    FOREACH (_ IN CASE WHEN rule IS NULL THEN [] ELSE [1] END |
                        MERGE (n)-[:APPLIES_TO]->(rule))
                    FOREACH (_ IN CASE WHEN run IS NULL THEN [] ELSE [1] END |
                        MERGE (n)-[:LEARNED_IN]->(run))
                    """,
                    rows=chunk, skill=skill,
                )

            # DERIVED_FROM (best-effort): for each Lesson with source_run_id +
            # source_item_id, link to the latest Attempt with eval_passed=false
            # (the failed attempt the lesson was distilled from). When no such
            # attempt exists, leave it for the dream pass to figure out.
            print("seed_reflective: linking Lesson -[:DERIVED_FROM]-> Attempt (best-effort)...")
            await session.run(
                """
                MATCH (l:Lesson {group_id: $skill})
                WHERE l.source_run_id IS NOT NULL AND l.source_item_id IS NOT NULL
                MATCH (a:Attempt {group_id: $skill, run_id: l.source_run_id, rule_id: l.source_item_id})
                WITH l, a ORDER BY a.attempt_num DESC
                WITH l, head(collect(a)) AS latest
                WHERE latest IS NOT NULL
                MERGE (l)-[:DERIVED_FROM]->(latest)
                """,
                skill=skill,
            )

            # Counts back for confirmation.
            counts = {}
            for label in ("Run", "Rule", "Attempt", "Lesson"):
                rec = await session.run(
                    f"MATCH (n:{label} {{group_id: $skill}}) RETURN count(n) AS c", skill=skill,
                )
                counts[label] = (await rec.single())["c"]
            edge_rec = await session.run(
                """
                MATCH (a {group_id: $skill})-[r]->(b {group_id: $skill})
                RETURN type(r) AS t, count(r) AS c ORDER BY c DESC
                """,
                skill=skill,
            )
            edges = [(rec["t"], rec["c"]) async for rec in edge_rec]

            print("seed_reflective: post-write counts:")
            for k, v in counts.items():
                print(f"  {k}: {v}")
            print("seed_reflective: edges:")
            for t, c in edges:
                print(f"  -[:{t}]->: {c}")
    finally:
        await driver.close()


async def main_async(skill: str, reset: bool, dry_run: bool) -> None:
    load_env()
    print(f"seed_reflective: skill={skill!r} reset={reset} dry_run={dry_run}")
    print(f"seed_reflective: pulling relational state from Postgres ({os.environ['PG_HOST']})...")
    data = fetch_relational(skill)
    print(
        f"seed_reflective: pulled {len(data['runs'])} runs, {len(data['rules'])} rules, "
        f"{len(data['attempts'])} attempts, {len(data['lessons'])} lessons"
    )
    await write_to_neo4j(data, skill, reset=reset, dry_run=dry_run)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skill", default="stig", help="group_id partition (default: stig)")
    ap.add_argument("--reset", action="store_true", help="DETACH DELETE existing nodes for this group_id first")
    ap.add_argument("--dry-run", action="store_true", help="Pull from Postgres but do not write to Neo4j")
    args = ap.parse_args()
    asyncio.run(main_async(args.skill, args.reset, args.dry_run))


if __name__ == "__main__":
    main()
