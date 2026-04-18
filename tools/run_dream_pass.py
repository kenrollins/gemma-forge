#!/usr/bin/env python
"""tools/run_dream_pass.py — CLI entrypoint for the dream pass.

Usage:
    # Run against a specific run ID:
    ./tools/run_dream_pass.py --run-id 20260414-012052

    # Run against the most recent run (auto-detected):
    ./tools/run_dream_pass.py

    # Specify environment tag:
    ./tools/run_dream_pass.py --env-tag baseline-20260414
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


async def main_async(run_id: str | None, env_tag: str | None, skill: str, force: bool = False) -> int:
    from gemma_forge.dream.pass_ import run_dream_pass

    if run_id is None:
        # Auto-detect the most recent run
        import os
        from gemma_forge.dream.pass_ import _load_env, _pg_conninfo
        import psycopg

        _load_env(REPO_ROOT)
        with psycopg.connect(_pg_conninfo("forge_admin")) as conn:
            conn.execute("SET search_path TO stig")
            row = conn.execute(
                """
                SELECT id FROM runs
                WHERE ended_at IS NOT NULL
                ORDER BY started_at DESC LIMIT 1
                """
            ).fetchone()
        if row is None:
            print("dream pass: no completed runs found in stig.runs", file=sys.stderr)
            return 1
        run_id = row[0]
        print(f"dream pass: auto-detected most recent completed run: {run_id}")

    result = await run_dream_pass(
        run_id=run_id,
        repo_root=REPO_ROOT,
        skill=skill,
        environment_tag=env_tag,
        force=force,
    )

    if result is None:
        print(f"dream pass: run {run_id} was already dreamed — skipped (pass --force to re-run)")
        return 0

    print()
    print(f"Dream pass complete for run {result.run_id}")
    print(f"  Categories analyzed: {result.categories_analyzed}")
    print(f"  Lessons updated: {result.lessons_updated}")
    print(f"  Positive credit: {result.lessons_with_positive_credit} categories")
    print(f"  Negative credit: {result.lessons_with_negative_credit} categories")
    print(f"  Neutral: {result.lessons_with_neutral_credit} categories")
    print(f"  Environment tag: {result.environment_tag}")
    print(f"  Report: runs/dreams/dream-{result.run_id}.md")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", default=None, help="Run ID to analyze (default: most recent)")
    ap.add_argument("--env-tag", default=None, help="Environment baseline tag (default: auto)")
    ap.add_argument("--skill", default="stig", help="Skill / group_id (default: stig)")
    ap.add_argument("--force", action="store_true",
                    help="Re-run even if run has already been dreamed (overrides idempotency guard).")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    return asyncio.run(main_async(args.run_id, args.env_tag, args.skill, args.force))


if __name__ == "__main__":
    sys.exit(main())
