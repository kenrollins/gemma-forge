#!/usr/bin/env python3
"""Memory subsystem audit — read-only cross-run report.

Packages the queries used during the 2026-05-21 Run 7 mid-flight
triage (see journey/38.5) into a single command. Designed to be run
post-run against any STIG or CVE run's Postgres state to assess
memory-pipeline health and surface anomalies.

The script covers six diagnostic angles:

  1. Tip pool state — counts, retired vs active, age cohorts.
  2. Retrieval activity in the target run — count, helpful rate.
  3. Per-cohort retrieval performance (DEF-27-aware: helpful_when_followed).
  4. Misleading-tip detector — tips with high outcome_value but low
     follow rate. The cryptography case from journey/38.5 is the
     canonical example.
  5. DEF-26 pain-signal check — distribution of outcome_value across
     {strong-helpful, weak-helpful, neutral, harmful}. If the "harmful"
     bucket is empty across all retrievals, the graded scoring isn't
     differentiating attempt quality and likely needs review.
  6. DEF-03 pain-signal check — tips with outcome_at_source_value > 0.5
     whose avg retrieval outcome is near 0 (or whose follow rate is
     near 0). These are the "stale lessons mislead" failure mode.

Usage:
    python tools/audit_memory.py --skill stig
    python tools/audit_memory.py --skill stig --run-id a0cba9d8-620
    python tools/audit_memory.py --skill cve --no-color

The script reads the same Postgres credentials that the harness uses
(from the project .env file). It is strictly read-only — no writes.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import psycopg  # noqa: E402

# ---------------------------------------------------------------------
# Config + connection
# ---------------------------------------------------------------------


def _load_env() -> None:
    """Load .env into os.environ for PG_* vars. Minimal parser."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


def _conninfo() -> str:
    host = os.environ.get("PG_HOST", "127.0.0.1")
    port = os.environ.get("PG_PORT", "5432")
    db = os.environ.get("PG_DATABASE", "gemma_forge")
    user = os.environ.get("PG_FORGE_ADMIN_ROLE", "forge_admin")
    pw = os.environ.get("PG_FORGE_ADMIN_PASSWORD", "")
    if not pw:
        raise SystemExit("audit_memory: PG_FORGE_ADMIN_PASSWORD missing from .env")
    return f"host={host} port={port} dbname={db} user={user} password={pw}"


# ---------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------


class _C:
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    END = "\033[0m"


def _section(title: str, color: bool) -> None:
    bar = "=" * 60
    if color:
        print(f"\n{_C.BOLD}{_C.BLUE}{bar}\n{title}\n{bar}{_C.END}")
    else:
        print(f"\n{bar}\n{title}\n{bar}")


def _flag(text: str, color: bool, level: str = "warn") -> str:
    if not color:
        return text
    palette = {"warn": _C.YELLOW, "bad": _C.RED, "ok": _C.GREEN, "dim": _C.DIM}
    return f"{palette.get(level, _C.YELLOW)}{text}{_C.END}"


# ---------------------------------------------------------------------
# Queries — each section is a function returning a list of rows
# ---------------------------------------------------------------------


def _resolve_run_id(conn: psycopg.Connection, schema: str, run_id: str | None) -> str:
    """If run_id is None, return the most recent run's id."""
    if run_id:
        return run_id
    conn.execute(f"SET search_path TO {schema}")
    row = conn.execute("SELECT id FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
    if not row:
        raise SystemExit(f"audit_memory: no runs found in schema {schema!r}")
    return row[0]


def section_tip_pool(conn: psycopg.Connection, schema: str, color: bool) -> None:
    _section("1. Tip pool state", color)
    conn.execute(f"SET search_path TO {schema}")
    row = conn.execute("""
        SELECT
            count(*) FILTER (WHERE retired_at IS NULL)              AS active,
            count(*) FILTER (WHERE retired_at IS NOT NULL)          AS retired,
            count(*)                                                AS total,
            count(*) FILTER (WHERE created_at > now() - interval '7 days')  AS new_7d,
            count(*) FILTER (WHERE created_at > now() - interval '30 days') AS new_30d
        FROM tips
    """).fetchone()
    print(f"  active:        {row[0]:>6d}")
    print(f"  retired:       {row[1]:>6d}  ({100 * row[1] / max(row[2], 1):.1f}% of all-time)")
    print(f"  total:         {row[2]:>6d}")
    print(f"  new last  7d:  {row[3]:>6d}")
    print(f"  new last 30d:  {row[4]:>6d}")


def section_run_retrievals(conn: psycopg.Connection, schema: str, run_id: str, color: bool) -> None:
    _section(f"2. Retrieval activity in run {run_id}", color)
    conn.execute(f"SET search_path TO {schema}")
    row = conn.execute(
        """
        SELECT
            count(*)                                                       AS total,
            count(*) FILTER (WHERE outcome_value IS NOT NULL)              AS scored,
            count(*) FILTER (WHERE outcome_value > 0)                      AS helpful,
            count(*) FILTER (WHERE outcome_value < 0)                      AS harmful,
            count(*) FILTER (WHERE outcome_value = 0)                      AS neutral,
            avg(outcome_value) FILTER (WHERE outcome_value IS NOT NULL)    AS avg_outcome,
            count(*) FILTER (WHERE tip_followed_llm IS NOT NULL)           AS judged,
            count(*) FILTER (WHERE tip_followed_llm IS TRUE)               AS followed_llm,
            count(*) FILTER (WHERE tip_followed_emb IS NOT NULL)           AS embedded
        FROM tip_retrievals WHERE run_id = %s
    """,
        (run_id,),
    ).fetchone()
    total, scored, helpful, harmful, neutral, avg_outcome, judged, followed_llm, embedded = row
    print(f"  retrievals:           {total:>6d}")
    print(f"  with outcome scored:  {scored:>6d}")
    if scored:
        helpful_rate = 100 * helpful / scored
        flag = "ok" if helpful_rate > 40 else "warn" if helpful_rate > 25 else "bad"
        print(
            f"  helpful (>0):         {helpful:>6d}  ({_flag(f'{helpful_rate:5.1f}%', color, flag)})"
        )
        print(f"  harmful (<0):         {harmful:>6d}")
        print(f"  neutral (=0):         {neutral:>6d}")
        print(f"  avg outcome:          {avg_outcome:>6.3f}")
    else:
        print(f"  {_flag('(no scored retrievals in this run)', color, 'dim')}")
    print(
        f"  judged (LLM):         {judged:>6d}  {_flag('(DEF-27 active)' if judged else '(DEF-27 NOT yet scored — dream pass run yet?)', color, 'ok' if judged else 'warn')}"
    )
    print(f"  embedded:             {embedded:>6d}")
    if judged:
        print(f"  follow rate (LLM):    {100 * followed_llm / judged:>5.1f}%")


def section_per_cohort_outcomes(
    conn: psycopg.Connection, schema: str, run_id: str, color: bool
) -> None:
    _section(f"3. Per-cohort retrieval helpfulness in run {run_id}", color)
    conn.execute(f"SET search_path TO {schema}")
    rows = conn.execute(
        """
        SELECT
            date_trunc('day', t.created_at)::date AS cohort,
            count(t.id)                  AS tips_in_cohort,
            count(r.tip_id)              AS retrievals,
            round(avg(r.outcome_value)::numeric, 3) AS avg_outcome,
            count(*) FILTER (WHERE r.tip_followed_llm IS TRUE)  AS llm_followed,
            count(*) FILTER (WHERE r.tip_followed_llm IS FALSE) AS llm_ignored,
            count(*) FILTER (WHERE r.tip_followed_llm IS NULL)  AS llm_null
        FROM tips t
        LEFT JOIN tip_retrievals r
          ON r.tip_id = t.id AND r.run_id = %s
        GROUP BY 1
        HAVING count(r.tip_id) > 0
        ORDER BY 1 DESC
        LIMIT 30
    """,
        (run_id,),
    ).fetchall()
    if not rows:
        print(f"  {_flag('(no cohort data — no retrievals matched in this run)', color, 'dim')}")
        return
    print(f"  {'cohort':<12s} {'tips':>6s} {'retrievals':>11s} {'helpful%':>9s} {'foll%':>7s}")
    for cohort, tips_count, retrievals, avg_o, llm_f, llm_i, _llm_n in rows:
        helpful_pct = (100 * avg_o) if avg_o is not None else 0.0
        flag = "ok" if helpful_pct > 40 else "warn" if helpful_pct > 25 else "bad"
        judged_ct = (llm_f or 0) + (llm_i or 0)
        foll_pct = (100 * llm_f / judged_ct) if judged_ct else None
        foll_str = f"{foll_pct:5.1f}%" if foll_pct is not None else "  —  "
        print(
            f"  {cohort!s:<12s} {tips_count:>6d} {retrievals:>11d} {_flag(f'{helpful_pct:7.1f}%', color, flag):>9s} {foll_str:>7s}"
        )


def section_misleading_tips(conn: psycopg.Connection, schema: str, color: bool) -> None:
    _section("4. Misleading-tip detector (high outcome, low follow rate)", color)
    conn.execute(f"SET search_path TO {schema}")
    # Tips with at least 5 retrievals where LLM judge has scored;
    # outcome is positive on average but the tip's advice isn't followed.
    # These are the "lucky neighbors" — the cryptography pattern.
    rows = conn.execute("""
        SELECT
            t.id,
            substr(t.text, 1, 100)                            AS preview,
            count(r.id)                                       AS n_retrievals,
            round(avg(r.outcome_value)::numeric, 3)           AS avg_outcome,
            count(*) FILTER (WHERE r.tip_followed_llm IS TRUE)  AS followed,
            count(*) FILTER (WHERE r.tip_followed_llm IS FALSE) AS ignored
        FROM tips t
        JOIN tip_retrievals r ON r.tip_id = t.id
        WHERE r.tip_followed_llm IS NOT NULL
          AND t.retired_at IS NULL
        GROUP BY t.id, t.text
        HAVING count(*) FILTER (WHERE r.tip_followed_llm IS NOT NULL) >= 5
           AND avg(r.outcome_value) > 0.4
           AND count(*) FILTER (WHERE r.tip_followed_llm IS TRUE)
               < count(*) FILTER (WHERE r.tip_followed_llm IS FALSE)
        ORDER BY (count(*) FILTER (WHERE r.tip_followed_llm IS FALSE))::float
                 / NULLIF(count(*) FILTER (WHERE r.tip_followed_llm IS NOT NULL), 0) DESC,
                 avg_outcome DESC
        LIMIT 15
    """).fetchall()
    if not rows:
        print(
            f"  {_flag('(no DEF-27-scored retrievals yet — run the dream pass on at least one run with the new scoring)', color, 'dim')}"
        )
        return
    print(f"  {'tip_id':>7s} {'n_ret':>5s} {'avg_out':>7s} {'fol':>4s} {'ign':>4s}  preview")
    for tip_id, preview, n, avg_o, followed, ignored in rows:
        print(
            f"  {tip_id:>7d} {n:>5d} {avg_o:>7.3f} {followed:>4d} {ignored:>4d}  {_flag(preview, color, 'warn')}"
        )
    print()
    print(
        f"  {_flag('These tips look helpful by outcome but their advice is rarely followed.', color, 'warn')}"
    )
    print(
        f"  {_flag('Candidates for retirement once retrieval ranker has more DEF-27 data.', color, 'warn')}"
    )


def section_outcome_distribution(conn: psycopg.Connection, schema: str, color: bool) -> None:
    _section("5. DEF-26 pain check (outcome_value distribution)", color)
    conn.execute(f"SET search_path TO {schema}")
    row = conn.execute("""
        SELECT
            count(*) FILTER (WHERE outcome_value > 0.7)                    AS strong_helpful,
            count(*) FILTER (WHERE outcome_value > 0 AND outcome_value <= 0.7) AS weak_helpful,
            count(*) FILTER (WHERE outcome_value = 0)                      AS neutral,
            count(*) FILTER (WHERE outcome_value < 0)                      AS harmful,
            count(*) FILTER (WHERE outcome_value IS NULL)                  AS unscored,
            min(outcome_value)                                             AS min_val,
            max(outcome_value)                                             AS max_val
        FROM tip_retrievals
    """).fetchone()
    strong, weak, neutral, harmful, unscored, mn, mx = row
    print(f"  strong helpful (>0.7):   {strong}")
    print(f"  weak helpful (0..0.7]:   {weak}")
    print(f"  neutral (=0):            {neutral}")
    print(f"  harmful (<0):            {harmful}")
    print(f"  unscored (NULL):         {unscored}")
    print(f"  min/max:                 {mn} / {mx}")
    if harmful == 0:
        print()
        print(
            f"  {_flag('FLAG: harmful column is zero across the entire history.', color, 'warn')}"
        )
        print(
            f"  {_flag('Binary outcome_value collapses 6 retrieval shapes onto {0,1}.', color, 'warn')}"
        )
        print(
            f"  {_flag('DEF-26 graded scoring should populate negative values once active.', color, 'warn')}"
        )
    if weak == 0 and strong > 0:
        print()
        print(
            f"  {_flag('FLAG: outcomes are bimodal {0, 1.0}. DEF-26 graded scoring missing.', color, 'warn')}"
        )


def section_def03_pain(conn: psycopg.Connection, schema: str, color: bool) -> None:
    _section("6. DEF-03 pain check (stale-confident tips)", color)
    conn.execute(f"SET search_path TO {schema}")
    rows = conn.execute("""
        SELECT
            t.id,
            t.outcome_at_source_value,
            count(r.id)                              AS n_retrievals,
            round(avg(r.outcome_value)::numeric, 3)  AS avg_retrieval_outcome,
            substr(t.text, 1, 80)                    AS preview
        FROM tips t
        JOIN tip_retrievals r ON r.tip_id = t.id
        WHERE t.retired_at IS NULL
          AND t.outcome_at_source_value > 0.5
        GROUP BY t.id, t.outcome_at_source_value, t.text
        HAVING count(r.id) >= 5
           AND avg(r.outcome_value) < 0.1
        ORDER BY avg(r.outcome_value) ASC
        LIMIT 15
    """).fetchall()
    if not rows:
        print(
            f"  {_flag('(no tips matching the DEF-03 pain signal — corpus looks clean here)', color, 'ok')}"
        )
        return
    print(f"  {'tip_id':>7s} {'src_v':>6s} {'n_ret':>5s} {'avg_out':>7s}  preview")
    for tip_id, src_v, n_ret, avg_o, preview in rows:
        print(
            f"  {tip_id:>7d} {src_v:>6.2f} {n_ret:>5d} {avg_o:>7.3f}  {_flag(preview, color, 'warn')}"
        )
    print()
    print(
        f"  {_flag('These tips looked good at birth (outcome_at_source > 0.5) but', color, 'warn')}"
    )
    print(
        f"  {_flag('rarely help on retrieval. The DEF-03 dream-pass per-tip credit', color, 'warn')}"
    )
    print(
        f"  {_flag('rewrite would let the dream pass downweight them automatically.', color, 'warn')}"
    )


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skill", default="stig", help="Skill schema to audit (default: stig)")
    parser.add_argument("--run-id", default=None, help="Run id to focus on (default: most recent)")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    args = parser.parse_args()

    _load_env()
    color = not args.no_color and sys.stdout.isatty()

    with psycopg.connect(_conninfo()) as conn:
        run_id = _resolve_run_id(conn, args.skill, args.run_id)
        if color:
            print(f"{_C.BOLD}audit_memory: skill={args.skill} run_id={run_id}{_C.END}")
        else:
            print(f"audit_memory: skill={args.skill} run_id={run_id}")
        section_tip_pool(conn, args.skill, color)
        section_run_retrievals(conn, args.skill, run_id, color)
        section_per_cohort_outcomes(conn, args.skill, run_id, color)
        section_misleading_tips(conn, args.skill, color)
        section_outcome_distribution(conn, args.skill, color)
        section_def03_pain(conn, args.skill, color)
    return 0


if __name__ == "__main__":
    sys.exit(main())
