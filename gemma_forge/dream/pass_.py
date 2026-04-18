"""Dream pass V1 — outcome-driven credit assignment.

The dream pass runs BETWEEN runs, reading the last run's outcomes from
Postgres and updating lesson confidence in both Neo4j (the Reflective
tier source of truth) and stig.lessons_current (the fast-read projection
used at prompt-assembly time).

V1 scope (Phase D):
  - Outcome-driven credit assignment at the CATEGORY level. Lessons are
    loaded into Worker prompts by category (load_lessons per category +
    load_all_lessons), so category-level credit is the right granularity.
  - Environment tagging with the VM baseline snapshot identity.
  - Dream report (markdown in runs/dreams/).
  - Rebuild the lessons_current Postgres projection with updated
    confidence scores.

Deferred to V2:
  - Supersession detection (needs Reflector text analysis)
  - Abstraction-loss recovery (needs LLM to assess lesson detail)
  - A-MEM-style semantic linking (needs embeddings)
  - Per-rule lesson attribution (needs prompt-assembled events to log
    lesson IDs, which they don't yet)

The distinctive contribution from ADR-0016: the STIG scanner returns
binary pass/fail per rule per attempt, giving us a deterministic
outcome signal that conversational memory systems do not have. Credit
assignment on lesson confidence is a solved problem when you have
ground-truth outcomes; it is an inference problem when you don't.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import psycopg
from neo4j import AsyncGraphDatabase

logger = logging.getLogger(__name__)


@dataclass
class CategoryCredit:
    """Per-category outcome summary for one run."""
    category: str
    remediated: int = 0
    escalated: int = 0
    skipped: int = 0

    @property
    def total(self) -> int:
        return self.remediated + self.escalated

    @property
    def success_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.remediated / self.total

    @property
    def confidence_signal(self) -> float:
        """Maps success_rate [0,1] to a confidence delta [-1, +1].

        0.0 success → -1.0 (strong negative)
        0.5 success →  0.0 (neutral)
        1.0 success → +1.0 (strong positive)
        """
        return 2.0 * self.success_rate - 1.0


@dataclass
class DreamResult:
    """Summary of one dream pass execution."""
    run_id: str
    timestamp: str
    categories_analyzed: int
    lessons_updated: int
    lessons_with_positive_credit: int
    lessons_with_negative_credit: int
    lessons_with_neutral_credit: int
    environment_tag: str
    category_credits: list[CategoryCredit]


def _load_env(repo_root: Path) -> None:
    env_path = repo_root / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _pg_conninfo(role: str) -> str:
    pw_var = f"PG_{role.upper()}_PASSWORD"
    pw = os.environ.get(pw_var)
    if not pw:
        raise RuntimeError(f"dream pass: {pw_var} missing from environment")
    host = os.environ.get("PG_HOST", "127.0.0.1")
    port = os.environ.get("PG_PORT", "5432")
    db = os.environ.get("PG_DATABASE", "gemma_forge")
    return f"host={host} port={port} dbname={db} user={role} password={pw}"


def compute_category_credits(run_id: str) -> list[CategoryCredit]:
    """Pull per-category outcome counts for a specific run from Postgres."""
    with psycopg.connect(_pg_conninfo("forge_admin")) as conn:
        conn.execute("SET search_path TO stig")
        rows = conn.execute(
            """
            SELECT
                category,
                SUM(CASE WHEN outcome = 'completed' THEN 1 ELSE 0 END)::int AS remediated,
                SUM(CASE WHEN outcome = 'escalated' THEN 1 ELSE 0 END)::int AS escalated,
                SUM(CASE WHEN outcome = 'skip' THEN 1 ELSE 0 END)::int AS skipped
            FROM work_items
            WHERE run_id = %s
            GROUP BY category
            ORDER BY category
            """,
            (run_id,),
        ).fetchall()
    return [
        CategoryCredit(category=r[0], remediated=r[1], escalated=r[2], skipped=r[3])
        for r in rows
    ]


async def update_neo4j_confidence(
    credits: list[CategoryCredit],
    environment_tag: str,
    skill: str = "stig",
) -> int:
    """Update Lesson.confidence and environment_tag in Neo4j.

    Returns the number of Lesson nodes updated.
    """
    bolt_port = os.environ.get("NEO4J_BOLT_HOST_PORT", "7687")
    uri = f"bolt://127.0.0.1:{bolt_port}"
    pw = os.environ["NEO4J_PASSWORD"]
    driver = AsyncGraphDatabase.driver(uri, auth=("neo4j", pw))

    total_updated = 0
    try:
        async with driver.session() as session:
            for cc in credits:
                if cc.total == 0:
                    continue
                delta = cc.confidence_signal
                result = await session.run(
                    """
                    MATCH (l:Lesson {group_id: $skill, category: $category})
                    SET l.confidence = CASE
                            WHEN l.confidence IS NULL THEN $delta
                            ELSE l.confidence + $delta * 0.3
                        END,
                        l.environment_tag = $env_tag,
                        l.last_dream_ts = datetime()
                    RETURN count(l) AS n
                    """,
                    skill=skill,
                    category=cc.category,
                    delta=delta,
                    env_tag=environment_tag,
                )
                record = await result.single()
                total_updated += record["n"]
    finally:
        await driver.close()

    return total_updated


def rebuild_lessons_projection(
    credits: dict[str, CategoryCredit],
    environment_tag: str,
) -> int:
    """Update stig.lessons_current with confidence scores.

    Reads the current lesson rows, applies the category-level credit
    signal to each lesson's confidence column, and sets the
    environment_tag. Returns the number of rows updated.
    """
    with psycopg.connect(_pg_conninfo("forge_admin")) as conn:
        conn.execute("SET search_path TO stig")
        updated = 0
        for cat, cc in credits.items():
            if cc.total == 0:
                continue
            delta = cc.confidence_signal
            cur = conn.execute(
                """
                UPDATE lessons_current
                SET confidence = CASE
                        WHEN confidence IS NULL THEN %s
                        ELSE confidence + %s * 0.3
                    END,
                    environment_tag = %s,
                    updated_at = now()
                WHERE category = %s
                RETURNING id
                """,
                (delta, delta, environment_tag, cat),
            )
            updated += len(cur.fetchall())
        conn.commit()
    return updated


def write_dream_report(result: DreamResult, repo_root: Path) -> Path:
    """Write a markdown dream report to runs/dreams/."""
    dreams_dir = repo_root / "runs" / "dreams"
    dreams_dir.mkdir(parents=True, exist_ok=True)
    path = dreams_dir / f"dream-{result.run_id}.md"

    lines = [
        f"# Dream Pass — Run {result.run_id}",
        "",
        f"**Timestamp:** {result.timestamp}",
        f"**Environment tag:** `{result.environment_tag}`",
        f"**Categories analyzed:** {result.categories_analyzed}",
        f"**Lessons updated:** {result.lessons_updated}",
        "",
        "## Credit assignment summary",
        "",
        f"- Positive credit (category success > 50%): **{result.lessons_with_positive_credit}** lessons",
        f"- Negative credit (category success < 50%): **{result.lessons_with_negative_credit}** lessons",
        f"- Neutral (no rules processed or exactly 50%): **{result.lessons_with_neutral_credit}** lessons",
        "",
        "## Per-category breakdown",
        "",
        "| Category | Remediated | Escalated | Success Rate | Credit Signal |",
        "|---|---|---|---|---|",
    ]
    for cc in sorted(result.category_credits, key=lambda c: c.success_rate, reverse=True):
        signal = f"+{cc.confidence_signal:.2f}" if cc.confidence_signal >= 0 else f"{cc.confidence_signal:.2f}"
        lines.append(
            f"| {cc.category} | {cc.remediated} | {cc.escalated} "
            f"| {cc.success_rate:.0%} | {signal} |"
        )
    lines.extend([
        "",
        "## What this pass does",
        "",
        "V1 of the dream pass performs outcome-driven credit assignment at the",
        "category level: lessons in categories with high remediation rates get",
        "positive confidence boosts; lessons in categories with high escalation",
        "rates get negative adjustments. Confidence is separate from weight",
        "(frequency): weight tracks how often a lesson appears when its category",
        "succeeds; confidence tracks whether the category succeeded in the most",
        "recent run where the lesson was available.",
        "",
        "The `confidence` column in `stig.lessons_current` and on Neo4j",
        "`Lesson` nodes is updated by this pass. The next run's prompt",
        "assembly can factor both weight and confidence into lesson selection.",
        "",
        "## Deferred to V2",
        "",
        "- Supersession detection (Reflector text analysis)",
        "- Abstraction-loss recovery (re-hydration from source attempt traces)",
        "- Semantic linking (A-MEM-style, needs embeddings)",
        "- Per-rule lesson attribution (needs prompt event logging enhancement)",
        "",
    ])

    path.write_text("\n".join(lines))
    return path


async def run_dream_pass(
    run_id: str,
    repo_root: Optional[Path] = None,
    skill: str = "stig",
    environment_tag: Optional[str] = None,
    force: bool = False,
) -> Optional[DreamResult]:
    """Execute the dream pass for a completed run.

    Idempotency: the dream pass updates confidences non-reversibly
    (``new = old + signal × 0.3``) so running it twice on the same
    run drifts values. Before starting, this function checks
    ``stig.runs.dreamed_at`` and returns ``None`` if already set.
    Pass ``force=True`` to override (policy-change backfills).

    Args:
        run_id: The run identifier (e.g., '20260414-012052').
        repo_root: Path to the repo root (auto-detected if None).
        skill: Graphiti group_id / Postgres schema name.
        environment_tag: Baseline identity tag. Auto-generated from
            current timestamp if not provided.
        force: Re-run even if dreamed_at is already set.

    Returns:
        DreamResult with summary of what was updated, or ``None`` if
        the guard fired (already dreamed, force=False).
    """
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[2]
    _load_env(repo_root)

    if environment_tag is None:
        environment_tag = f"baseline-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d')}"

    # Idempotency guard (migration 0005). Skip if already dreamed
    # unless explicitly forced. Returns None so the caller can log
    # "already dreamed, skipping" rather than treating it as an error.
    with psycopg.connect(_pg_conninfo("forge_admin")) as conn:
        conn.execute("SET search_path TO stig")
        row = conn.execute(
            "SELECT dreamed_at FROM runs WHERE id = %s", (run_id,),
        ).fetchone()
    if row and row[0] is not None and not force:
        logger.info(
            "dream pass: run_id=%s already dreamed at %s — skipping (pass force=True to override)",
            run_id, row[0],
        )
        return None

    logger.info("dream pass: starting for run_id=%s skill=%s env=%s", run_id, skill, environment_tag)

    # Step 1: compute per-category credit from run outcomes
    credits = compute_category_credits(run_id)
    credits_dict = {cc.category: cc for cc in credits}
    logger.info("dream pass: %d categories with outcomes", len(credits))

    if not credits:
        logger.warning("dream pass: no work_items found for run_id=%s — was this run migrated?", run_id)
        # Fall back: try the most recent run with outcomes
        with psycopg.connect(_pg_conninfo("forge_admin")) as conn:
            conn.execute("SET search_path TO stig")
            row = conn.execute(
                """
                SELECT DISTINCT run_id FROM work_items
                WHERE outcome IN ('completed', 'escalated')
                ORDER BY run_id DESC LIMIT 1
                """
            ).fetchone()
        if row:
            logger.info("dream pass: falling back to most recent run with outcomes: %s", row[0])
            run_id = row[0]
            credits = compute_category_credits(run_id)
            credits_dict = {cc.category: cc for cc in credits}

    for cc in credits:
        logger.info(
            "  %s: %d remed / %d esc → signal=%.2f",
            cc.category, cc.remediated, cc.escalated, cc.confidence_signal,
        )

    # Step 2: update Neo4j Lesson nodes
    neo4j_updated = await update_neo4j_confidence(credits, environment_tag, skill)
    logger.info("dream pass: %d Neo4j Lesson nodes updated", neo4j_updated)

    # Step 3: update Postgres projection
    pg_updated = rebuild_lessons_projection(credits_dict, environment_tag)
    logger.info("dream pass: %d Postgres lessons_current rows updated", pg_updated)

    # Step 4: tally
    positive = sum(1 for cc in credits if cc.confidence_signal > 0)
    negative = sum(1 for cc in credits if cc.confidence_signal < 0)
    neutral = sum(1 for cc in credits if cc.confidence_signal == 0)
    # Count lessons touched = the larger of neo4j vs postgres (they should match)
    lessons_updated = max(neo4j_updated, pg_updated)

    result = DreamResult(
        run_id=run_id,
        timestamp=dt.datetime.now(dt.timezone.utc).isoformat(),
        categories_analyzed=len(credits),
        lessons_updated=lessons_updated,
        lessons_with_positive_credit=positive,
        lessons_with_negative_credit=negative,
        lessons_with_neutral_credit=neutral,
        environment_tag=environment_tag,
        category_credits=credits,
    )

    # Step 5: write dream report
    report_path = write_dream_report(result, repo_root)
    logger.info("dream pass: report written to %s", report_path)

    # Step 6: mark the run as dreamed so the idempotency guard above
    # fires on any subsequent call. Last — if any prior step failed
    # we want the run eligible for a retry rather than stuck.
    with psycopg.connect(_pg_conninfo("forge_admin")) as conn:
        conn.execute("SET search_path TO stig")
        conn.execute(
            "UPDATE runs SET dreamed_at = now() WHERE id = %s", (run_id,),
        )
        conn.commit()
    logger.info("dream pass: marked run %s as dreamed_at=now()", run_id)

    return result
