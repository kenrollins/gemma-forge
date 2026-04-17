#!/usr/bin/env python
"""tools/graphiti_init.py — initialize the Graphiti-on-Neo4j schema.

Phase B of the memory refactor (ADR-0016).

Neo4j Community Edition is single-database (the multi-database feature
is Enterprise-only). For per-skill isolation we use Graphiti's native
``group_id`` field on episodes — one Neo4j instance, one underlying
database (``neo4j``), logical per-skill partitioning. Future skills
get their own ``group_id`` and their nodes / edges never collide.

This script:

1. Confirms it can talk to Neo4j with the credentials in ``.env``.
2. Calls ``build_indices_and_constraints()`` so Graphiti's standard
   schema (entity name / fact embedding / temporal indexes) exists.
3. Inserts a skill marker node so we can verify per-skill partitioning
   from a Cypher shell.
4. Disables Graphiti's PostHog telemetry — the sovereign-edge thesis
   requires no phone-home, period.

Run: ``./tools/graphiti_init.py --skill stig``
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_env() -> None:
    """Populate ``os.environ`` from the repo-root ``.env`` file.

    Tiny inline parser so this script has no extra dependency on
    python-dotenv just for reading three values.
    """
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        sys.exit(f"graphiti_init: {env_path} not found; run bootstrap_database.sh first")
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


async def main(skill: str) -> None:
    load_env()

    # No phone-home. Set BEFORE importing graphiti so its module-level
    # telemetry init sees the disabled state.
    os.environ["GRAPHITI_TELEMETRY_ENABLED"] = "false"
    # Graphiti's default LLM client reads from these. We're not adding
    # episodes in this script (init only), but the constructor still
    # builds clients eagerly. Stub values keep it quiet.
    os.environ.setdefault("OPENAI_API_KEY", "init-only-no-llm-calls")
    os.environ.setdefault("OPENAI_BASE_URL", "http://localhost:9999/init-only")

    from graphiti_core import Graphiti  # noqa: E402

    bolt_port = os.environ.get("NEO4J_BOLT_HOST_PORT", "7687")
    uri = f"bolt://127.0.0.1:{bolt_port}"
    user = "neo4j"
    password = os.environ.get("NEO4J_PASSWORD")
    if not password:
        sys.exit("graphiti_init: NEO4J_PASSWORD missing from .env")

    print(f"graphiti_init: connecting to {uri} as {user}")
    graphiti = Graphiti(uri=uri, user=user, password=password)

    try:
        # Ensure Graphiti's standard indices / constraints exist. Idempotent.
        print("graphiti_init: building indices and constraints (idempotent)...")
        await graphiti.build_indices_and_constraints()

        # Drop a skill marker node via the raw driver so we can confirm
        # the per-skill partition is visible without rerunning the LLM
        # entity-extraction pipeline.
        print(f"graphiti_init: writing skill marker node for group_id={skill!r}...")
        async with graphiti.driver.session() as session:
            await session.run(
                """
                MERGE (s:Skill {group_id: $skill})
                SET s.initialized_at = coalesce(s.initialized_at, datetime()),
                    s.touched_at = datetime(),
                    s.note = $note
                """,
                skill=skill,
                note="Created by tools/graphiti_init.py — Phase B",
            )

            result = await session.run(
                "MATCH (s:Skill) RETURN s.group_id AS group_id, s.initialized_at AS initialized_at"
            )
            rows = [record async for record in result]

        print("graphiti_init: skill markers in graph:")
        for row in rows:
            print(f"  - group_id={row['group_id']!r}, initialized_at={row['initialized_at']}")

        # ----- Phase E3: V2 memory architecture schema --------------------
        # Tip / Rule labels and indexes for the similarity-retrieval query:
        #   (r:Rule)<-[:HELPED]-(t:Tip {retired_at: null, group_id: $skill})
        # Postgres stig.tips.id ↔ Neo4j Tip.tip_id (BIGINT int). Rule.rule_id
        # mirrors the xccdf rule identifier. Graphiti's standard indices don't
        # cover this pattern because Tip / Rule are overlay labels we add on
        # top of the episode / entity graph.
        print("graphiti_init: adding V2 Tip/Rule indexes (Phase E3)...")
        _tip_rule_ddl = [
            "CREATE INDEX tip_group_id   IF NOT EXISTS FOR (t:Tip)  ON (t.group_id)",
            "CREATE INDEX tip_tip_id     IF NOT EXISTS FOR (t:Tip)  ON (t.tip_id)",
            "CREATE INDEX tip_retired_at IF NOT EXISTS FOR (t:Tip)  ON (t.retired_at)",
            "CREATE INDEX tip_source_rule IF NOT EXISTS FOR (t:Tip) ON (t.source_rule_id)",
            "CREATE INDEX rule_rule_id   IF NOT EXISTS FOR (r:Rule) ON (r.rule_id)",
            "CREATE INDEX rule_group_id  IF NOT EXISTS FOR (r:Rule) ON (r.group_id)",
            # Uniqueness of tip_id within a skill. Matches the Postgres
            # tips.id primary key after the Postgres tip is written and its
            # UUID echoed back into Neo4j.
            "CREATE CONSTRAINT tip_group_id_tip_id_unique IF NOT EXISTS "
            "FOR (t:Tip) REQUIRE (t.group_id, t.tip_id) IS UNIQUE",
            # Rule.rule_id is the natural key; uniqueness within a skill.
            "CREATE CONSTRAINT rule_group_id_rule_id_unique IF NOT EXISTS "
            "FOR (r:Rule) REQUIRE (r.group_id, r.rule_id) IS UNIQUE",
        ]
        async with graphiti.driver.session() as session:
            for stmt in _tip_rule_ddl:
                await session.run(stmt)
                print(f"  ok  {stmt.split(' IF NOT EXISTS')[0][:70]}")

    finally:
        await graphiti.close()

    print("graphiti_init: done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Initialize Graphiti schema for a skill.")
    parser.add_argument("--skill", required=True, help="Skill identifier (e.g. 'stig')")
    args = parser.parse_args()
    if not args.skill.replace("_", "").isalnum() or not args.skill[0].isalpha():
        sys.exit("graphiti_init: --skill must match [a-zA-Z][a-zA-Z0-9_]*")
    asyncio.run(main(args.skill))
