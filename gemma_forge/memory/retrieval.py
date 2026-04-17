"""Similarity-based tip retrieval.

Phase G of the V2 memory architecture. Replaces the V1 call chain
``mem_store.load_lessons(category, ...)`` — which retrieves by
category+confidence and produced the dac_modification regressions
Run 4 surfaced — with a structured-similarity query that surfaces
tips whose source rule looks like the current rule.

Why structured similarity (lexical prefix + category + per-(tip, rule)
historical co-occurrence) rather than embedding similarity as the
primary predicate: Diagnostic 1 from Run 4 showed that two of eight
wins came from pure category-retrieval noise (chronyd lessons for
networkmanager, RPM-DB-corruption lessons for sshd_enable_warning_banner).
Embedding similarity over rule_id alone would reproduce that class of
false-positive because the rule_id embeddings collapse on superficial
shared tokens. STIG rule IDs are highly structured
(``xccdf_org.ssgproject.content_rule_audit_rules_dac_modification_fchmod``);
their lexical prefix carries the rule-family relationship the V2 plan
actually wants. Embeddings can be layered in later as a secondary
signal, but the structured predicate is the correct primary.

See docs/drafts/v2-architecture-plan.md §2.3 and §11 refinement 1
for the full rationale.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from psycopg_pool import ConnectionPool

from gemma_forge.harness.db import get_pool

logger = logging.getLogger(__name__)


@dataclass
class RetrievedTip:
    """One tip selected for inclusion in the Worker prompt."""
    tip_id: int
    text: str
    tip_type: str
    trigger_conditions: Optional[list[str]]
    application_context: list[str]
    source_rule_id: Optional[str]
    source_run_id: Optional[str]
    similarity_score: float       # [0, 1] — structured similarity to current rule
    rank: int                     # 1-based within the assembled prompt


# ---------------------------------------------------------------------
# Rule tokenization
# ---------------------------------------------------------------------


_RULE_PREFIX = "content_rule_"


def tokenize_rule_id(rule_id: str) -> list[str]:
    """Strip the SCAP prefix and split on underscores.

    ``xccdf_org.ssgproject.content_rule_audit_rules_dac_modification_fchmod``
    → ``['audit', 'rules', 'dac', 'modification', 'fchmod']``
    """
    if not rule_id:
        return []
    tail = rule_id.split(_RULE_PREFIX, 1)[-1].lower()
    return [t for t in tail.split("_") if t]


def rule_prefix_similarity(a: str, b: str) -> float:
    """Shared-prefix similarity of two tokenized rule_ids, in [0, 1].

    Length of common token prefix divided by the longer rule's token
    count. Two identical rule_ids score 1.0; no shared tokens scores 0.0.
    A rule family's internal variants
    (``audit_rules_dac_modification_fchmod`` vs
    ``audit_rules_dac_modification_fchown``) share a 4-token prefix
    out of 5 → 0.8.
    """
    ta = tokenize_rule_id(a)
    tb = tokenize_rule_id(b)
    if not ta or not tb:
        return 0.0
    shared = 0
    for x, y in zip(ta, tb):
        if x == y:
            shared += 1
        else:
            break
    return shared / max(len(ta), len(tb))


# ---------------------------------------------------------------------
# Historical co-occurrence — per-(tip, rule) hit-rate
# ---------------------------------------------------------------------


def _fetch_hit_rates(pool: ConnectionPool, rule_id: str,
                     min_retrievals: int = 1) -> dict[int, float]:
    """Return per-tip_id hit rate on ``rule_id``.

    Hit rate = mean(outcome_value × outcome_confidence) across prior
    ``tip_retrievals`` rows where this (tip, rule) pair was loaded and
    the outcome landed. Defaults to {} on first-run cold-start.
    """
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT tip_id, AVG(outcome_value * outcome_confidence) AS hit_rate
            FROM tip_retrievals
            WHERE rule_id = %s
              AND outcome_value IS NOT NULL
              AND outcome_confidence IS NOT NULL
            GROUP BY tip_id
            HAVING COUNT(*) >= %s
            """,
            (rule_id, min_retrievals),
        )
        return {row[0]: float(row[1]) for row in cur.fetchall()}


# ---------------------------------------------------------------------
# Main retrieval entry point
# ---------------------------------------------------------------------


_DEFAULT_CANDIDATE_POOL = 200   # pull top-N candidates from SQL; rank in Python
_CATEGORY_BONUS = 0.30          # added to similarity when category matches
_HIT_RATE_WEIGHT = 0.50         # weight of per-(tip, rule) hit rate in the composite


def assemble_tips_for_rule(
    rule_id: str,
    category: str,
    *,
    skill: str = "stig",
    k: int = 5,
    pool: Optional[ConnectionPool] = None,
    exclude_run_id: Optional[str] = None,
    same_run_damping: float = 0.5,
) -> list[RetrievedTip]:
    """Return up to ``k`` tips most likely to help on ``rule_id``.

    Algorithm (structured similarity, per V2 §2.3 + §11 refinement 1):

    1. Candidate pool: active tips in ``stig.tips`` whose
       ``application_context`` overlaps ``[category]`` or whose
       ``source_rule_id`` shares any tokens with ``rule_id``.
    2. For each candidate, compute a composite score:
         base      = rule_prefix_similarity(rule_id, source_rule_id)
         category  = +_CATEGORY_BONUS if category ∈ application_context
         hit_rate  = + _HIT_RATE_WEIGHT × per-(tip, rule) hit rate (if any data)
         same-run  = × same_run_damping if tip came from the current run
                      (§11 refinement 5: damp, don't exclude — some wins
                      come from within-run lesson accumulation)
    3. Sort by composite desc, return top ``k`` as RetrievedTip.

    ``exclude_run_id`` is not an exclusion — it is the current run_id
    so same-run tips get damped rather than dominating. Pass the
    current run_id in; tips born in *this* run are kept but down-weighted.

    Returns [] when the tips table has no candidate match — Phase G
    wires this alongside the V1 ``load_lessons`` path, so an empty
    result just means Worker prompts get their existing V1 section
    instead.
    """
    pool = pool or get_pool(f"forge_{skill}")

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, text, tip_type, trigger_conditions, application_context,
                   source_rule_id, source_run_id
            FROM tips
            WHERE retired_at IS NULL
              AND (
                    %s = ANY(application_context)
                 OR source_rule_id IS NOT NULL
              )
            LIMIT %s
            """,
            (category or "", _DEFAULT_CANDIDATE_POOL * 10),
            # big cap — we filter candidate_pool count after computing scores
        )
        rows = cur.fetchall()

    if not rows:
        return []

    hit_rates = _fetch_hit_rates(pool, rule_id)

    scored: list[tuple[float, float, tuple]] = []  # (composite, base_sim, row)
    for row in rows:
        tip_id, _text, _tip_type, _triggers, app_ctx, src_rule, src_run = row
        base = rule_prefix_similarity(rule_id, src_rule or "")
        cat_bonus = _CATEGORY_BONUS if (category and category in (app_ctx or [])) else 0.0
        hit = hit_rates.get(tip_id, 0.0) * _HIT_RATE_WEIGHT
        composite = base + cat_bonus + hit
        if exclude_run_id and src_run == exclude_run_id:
            composite *= same_run_damping
        # Drop tips with 0 similarity AND 0 category-match AND no hit history —
        # those are just noise (random tips from other rule families with no
        # prior evidence they help here).
        if composite <= 0.0:
            continue
        scored.append((composite, base, row))

    scored.sort(key=lambda x: (-x[0], -x[1], x[2][0]))
    top = scored[:k]

    return [
        RetrievedTip(
            tip_id=row[0],
            text=row[1],
            tip_type=row[2],
            trigger_conditions=row[3],
            application_context=row[4] or [],
            source_rule_id=row[5],
            source_run_id=row[6],
            similarity_score=round(composite, 4),
            rank=i + 1,
        )
        for i, (composite, _base, row) in enumerate(top)
    ]


# ---------------------------------------------------------------------
# tip_retrievals — logging + outcome update
# ---------------------------------------------------------------------


def log_retrievals(
    retrievals: list[RetrievedTip],
    *,
    run_id: str,
    rule_id: str,
    skill: str = "stig",
    pool: Optional[ConnectionPool] = None,
) -> list[int]:
    """Insert tip_retrievals rows for ``retrievals``. Returns the new ids
    in the same order, so the caller can update ``outcome_value`` /
    ``outcome_confidence`` once the attempt evaluates.
    """
    if not retrievals:
        return []
    pool = pool or get_pool(f"forge_{skill}")
    new_ids: list[int] = []
    with pool.connection() as conn, conn.cursor() as cur:
        for r in retrievals:
            cur.execute(
                """
                INSERT INTO tip_retrievals (
                    run_id, attempt_id, tip_id, rule_id, rank, similarity_score
                )
                VALUES (%s, NULL, %s, %s, %s, %s)
                RETURNING id
                """,
                (run_id, r.tip_id, rule_id, r.rank, r.similarity_score),
            )
            new_ids.append(cur.fetchone()[0])
        conn.commit()
    return new_ids


def update_retrieval_outcomes(
    retrieval_ids: list[int],
    outcome_value: float,
    outcome_confidence: float,
    *,
    skill: str = "stig",
    pool: Optional[ConnectionPool] = None,
) -> int:
    """Set outcome_value / outcome_confidence on a batch of tip_retrievals
    rows once the attempt they belong to has been evaluated. Returns
    rows updated.
    """
    if not retrieval_ids:
        return 0
    pool = pool or get_pool(f"forge_{skill}")
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE tip_retrievals
               SET outcome_value = %s,
                   outcome_confidence = %s
             WHERE id = ANY(%s)
            """,
            (outcome_value, outcome_confidence, retrieval_ids),
        )
        affected = cur.rowcount
        conn.commit()
    return affected
