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

import asyncio
import datetime as dt
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
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
    # DEF-03: follow-aware signal derived from per-tip causal attribution.
    # When the dream pass has DEF-27 data (Run 8+), prefer this signal
    # over the legacy success_rate — it captures whether wins were
    # earned through followed advice (lessons working as intended) or
    # were lucky-neighbor outcomes (lessons present but ignored).
    follow_aware_signal: Optional[float] = None  # in [-1, +1] when computed
    follow_sample_size: int = 0                  # n retrievals contributing

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
        """Maps the category's outcome quality to a confidence delta [-1, +1].

        DEF-03: when follow-aware signal is available (DEF-27 data
        populated for this run), use it — it reflects "wins earned via
        followed advice" rather than raw pass/fail. Falls back to the
        legacy success_rate-derived signal when follow-aware data is
        absent (pre-DEF-27 runs, or new categories with no retrievals).
        """
        if self.follow_aware_signal is not None and self.follow_sample_size >= 5:
            return self.follow_aware_signal
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
    """Pull per-category outcome counts for a specific run from Postgres.

    DEF-03: when DEF-27 tip-follow data is available for this run's
    retrievals, also compute the follow-aware category signal — the
    average of (outcome × follow_modifier) across retrievals grouped
    by the rule's category. This signal is preferred by
    `CategoryCredit.confidence_signal` when populated, because it
    reflects "wins earned via followed advice" rather than raw
    pass/fail counts.
    """
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
        credits = [
            CategoryCredit(category=r[0], remediated=r[1], escalated=r[2], skipped=r[3])
            for r in rows
        ]

        # DEF-03 — follow-aware signal per category. Only computed
        # over rows that have non-NULL DEF-27 columns; pre-DEF-27
        # runs (R7 and earlier) skip this entirely and the legacy
        # success_rate signal handles them via the property fallback.
        follow_rows = conn.execute(
            """
            SELECT wi.category,
                   COUNT(*) AS n_judged,
                   AVG(
                     tr.outcome_value * tr.outcome_confidence
                     * CASE
                         WHEN tr.tip_followed_llm IS TRUE THEN 1.0
                         WHEN tr.tip_followed_llm IS FALSE THEN 0.3
                         WHEN tr.tip_followed_emb IS NOT NULL
                              AND tr.tip_followed_emb >= 0.6 THEN 1.0
                         WHEN tr.tip_followed_emb IS NOT NULL
                              AND tr.tip_followed_emb <  0.6 THEN 0.3
                         ELSE 0.5
                       END
                   ) AS follow_aware_mean
            FROM tip_retrievals tr
            JOIN work_items wi
              ON wi.run_id = tr.run_id AND wi.item_id = tr.rule_id
            WHERE tr.run_id = %s
              AND tr.outcome_value IS NOT NULL
              AND tr.outcome_confidence IS NOT NULL
              AND (tr.tip_followed_llm IS NOT NULL
                   OR tr.tip_followed_emb IS NOT NULL)
            GROUP BY wi.category
            """,
            (run_id,),
        ).fetchall()
        follow_by_cat = {r[0]: (int(r[1]), float(r[2])) for r in follow_rows}

    # Map mean ∈ [0,1] → signal ∈ [-1, +1] same shape as legacy.
    for cc in credits:
        if cc.category in follow_by_cat:
            n, mean = follow_by_cat[cc.category]
            cc.follow_sample_size = n
            cc.follow_aware_signal = 2.0 * mean - 1.0
    return credits


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


# ---------------------------------------------------------------------
# DEF-27 — per-retrieval causal attribution
#
# For each tip_retrievals row in the run, compute two complementary
# "did the Worker follow this tip's advice" signals:
#
#   tip_followed_llm:  LLM judge ruling (bool). Reads the tip text and
#                      the Worker's approach narrative, returns YES/NO
#                      with brief reasoning. Captures directional
#                      alignment ("tip said use X, worker used Y").
#
#   tip_followed_emb:  Sentence-transformers cosine similarity (float).
#                      Deterministic, fast, captures topical overlap
#                      but is known to miss direction — see
#                      architecture/02 for why both signals exist.
#
# Both run in the dream pass at end-of-run (cold path) so the
# retrieval pipeline stays fast during the run itself. See
# journey/38.5 for the cryptography case that motivated this.
# ---------------------------------------------------------------------


_VLLM_BASE_URL = os.environ.get("VLLM_BASE_URL", "http://localhost:8050/v1")
_JUDGE_CONCURRENCY = int(os.environ.get("TIP_FOLLOW_JUDGE_CONCURRENCY", "8"))
_JUDGE_MAX_TOKENS = 100
_JUDGE_TEMPERATURE = 0.0
_EMBED_MODEL_NAME = "all-MiniLM-L6-v2"


def _load_judge_prompt(repo_root: Path, skill: str) -> Optional[tuple[str, str]]:
    """Load (system_prompt, user_template) from skills/<skill>/prompts/tip_follow_judge.md.

    The prompt file has a `## System` section and a `## User template`
    section in markdown — we extract both. Returns None if the file
    doesn't exist (then this skill skips LLM judging gracefully).

    Resolves ``skill`` (a Postgres schema short-name like "stig" / "cve"
    / "detection") to the skill directory via the manifest-scanning
    helper, so adding a new skill doesn't require editing a local map
    here. Falls back to a direct ``skill``-as-dirname lookup for skills
    whose schema equals their directory name.
    """
    from gemma_forge.skills.loader import find_skill_dir_by_schema

    resolved = find_skill_dir_by_schema(skill, skills_dir=str(repo_root / "skills"))
    skill_dir = resolved.name if resolved is not None else skill
    path = repo_root / "skills" / skill_dir / "prompts" / "tip_follow_judge.md"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")

    def _section(header: str) -> Optional[str]:
        # Match `## {header}` and capture until the next `## ` or end of file.
        m = re.search(rf"^## {re.escape(header)}\s*$\n(.*?)(?=\n## |\Z)", text, re.MULTILINE | re.DOTALL)
        return m.group(1).strip() if m else None

    system = _section("System")
    user_template = _section("User template")
    if not system or not user_template:
        return None
    # The "User template" section in the markdown is an explanation of the
    # format with a code block showing the literal template. Extract the
    # ```...``` block that contains {tip_text} and {worker_approach}.
    m = re.search(r"```(?:[^\n]*)\n(.*?\{tip_text\}.*?\{worker_approach\}.*?)\n```", user_template, re.DOTALL)
    if m:
        user_template = m.group(1).strip()
    return system, user_template


async def _judge_one(
    client: httpx.AsyncClient,
    system_prompt: str,
    user_template: str,
    tip_text: str,
    worker_approach: str,
    model: str,
) -> Optional[bool]:
    """One LLM-judge call. Returns True/False or None on failure."""
    user = user_template.format(tip_text=tip_text, worker_approach=worker_approach)
    try:
        resp = await client.post(
            f"{_VLLM_BASE_URL}/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user},
                ],
                "temperature": _JUDGE_TEMPERATURE,
                "max_tokens": _JUDGE_MAX_TOKENS,
            },
            timeout=60.0,
        )
        if resp.status_code != 200:
            return None
        content = resp.json()["choices"][0]["message"]["content"]
    except Exception:
        return None
    # Parse the JSON line. The judge prompt instructs single-line JSON;
    # be tolerant of fenced markdown.
    m = re.search(r"\{[^{}]*\"followed\"\s*:\s*(true|false)[^{}]*\}", content, re.IGNORECASE)
    if not m:
        return None
    return m.group(1).lower() == "true"


def _resolve_jsonl_path(run_id: str, repo_root: Path) -> Optional[Path]:
    """Locate the JSONL run log for ``run_id``.

    Postgres ``runs`` has the started_at; ``runs/`` on disk holds files
    named ``run-YYYYMMDD-HHMMSS.jsonl``. We match by timestamp because
    the on-disk filename uses the harness start time (sub-second), not
    the run_id (a separate UUID-like).
    """
    runs_dir = repo_root / "runs"
    if not runs_dir.is_dir():
        return None
    with psycopg.connect(_pg_conninfo("forge_admin")) as conn:
        conn.execute("SET search_path TO stig")
        row = conn.execute(
            "SELECT started_at FROM runs WHERE id = %s", (run_id,),
        ).fetchone()
    if not row or not row[0]:
        return None
    started_at: dt.datetime = row[0]
    # Filename format: run-YYYYMMDD-HHMMSS.jsonl, harness rounds down to whole seconds.
    candidates = []
    for p in runs_dir.glob("run-*.jsonl"):
        try:
            stem = p.stem  # run-20260520-212629
            parts = stem.split("-")
            ts = dt.datetime.strptime(parts[1] + parts[2], "%Y%m%d%H%M%S")
            ts = ts.replace(tzinfo=dt.timezone.utc)
        except Exception:
            continue
        delta_s = abs((ts - started_at).total_seconds())
        candidates.append((delta_s, p))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    best_delta, best_path = candidates[0]
    if best_delta > 60:  # off by more than a minute → not the right file
        return None
    return best_path


def _parse_run_for_tip_followscoring(jsonl_path: Path) -> dict[tuple[str, int, int], str]:
    """Read the run's JSONL and build a map from (rule_id, attempt_num, tip_id)
    to the Worker's fix_script text.

    Each apply_fix prompt_assembled event carries ``v2_tips_loaded`` (a list
    of dicts with ``tip_id``). The Worker's tool_call event in the same
    attempt has ``args.fix_script``. We stitch them so each retrieval can
    be scored against the script that ran in the same attempt.
    """
    by_key: dict[tuple[str, int, int], str] = {}
    current_rule: Optional[str] = None
    current_attempt: int = 0
    current_tip_ids: list[int] = []
    pending_script: Optional[str] = None

    with open(jsonl_path) as f:
        for line in f:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            et = obj.get("event_type", "")
            d = obj.get("data") or {}
            if et == "rule_selected":
                current_rule = d.get("rule_id")
                current_attempt = 0
                current_tip_ids = []
            elif et == "attempt_start":
                current_attempt = d.get("attempt") or (current_attempt + 1)
                current_tip_ids = []
                pending_script = None
            elif et == "prompt_assembled" and d.get("phase") == "apply_fix":
                v2 = d.get("v2_tips_loaded") or []
                if isinstance(v2, list):
                    current_tip_ids = [t.get("tip_id") for t in v2 if isinstance(t, dict) and t.get("tip_id") is not None]
            elif et == "tool_call":
                args = d.get("args") or {}
                fix = args.get("fix_script")
                if fix and current_rule and current_attempt:
                    pending_script = fix
                    for tid in current_tip_ids:
                        by_key[(current_rule, current_attempt, tid)] = fix
    return by_key


def _fetch_unscored_retrievals(skill: str, run_id: str) -> list[dict]:
    """Fetch retrievals from this run that still need tip_followed scoring.

    Returns rows with (id, tip_id, rule_id, tip_text, retrieved_at). The
    Worker's fix_script comes from JSONL parsing — see
    ``_parse_run_for_tip_followscoring`` — because attempts.approach is
    a 500-char-truncated narrative, not the actual script the Worker ran.
    """
    with psycopg.connect(_pg_conninfo("forge_admin")) as conn:
        conn.execute(f"SET search_path TO {skill}")
        rows = conn.execute(
            """
            SELECT tr.id, tr.tip_id, tr.rule_id, t.text, tr.retrieved_at
            FROM tip_retrievals tr
            JOIN tips t ON t.id = tr.tip_id
            WHERE tr.run_id = %s
              AND tr.tip_followed_computed_at IS NULL
              AND tr.outcome_value IS NOT NULL
              AND length(t.text) > 0
            ORDER BY tr.retrieved_at, tr.rank
            """,
            (run_id,),
        ).fetchall()
    return [
        {"id": r[0], "tip_id": r[1], "rule_id": r[2], "tip_text": r[3], "retrieved_at": r[4]}
        for r in rows
    ]


def _attach_fix_scripts(
    retrievals: list[dict],
    script_map: dict[tuple[str, int, int], str],
) -> list[dict]:
    """For each retrieval, pick the fix_script for (rule_id, attempt_num, tip_id).

    Without ``tip_retrievals.attempt_id`` (NULL on pre-DEF-27 runs), we
    use retrieved_at ordering within a rule to infer attempt_num: the
    first 5 retrievals for a rule belong to attempt 1, the next 5 to
    attempt 2, etc. New runs (with attempt_id populated) can swap this
    out for a clean DB lookup — see TODO marker in the code.
    """
    # Group retrievals by rule, ordered by retrieved_at. Assume k=5 per
    # attempt (RetrievedTip.rank goes 1..5 per assemble_tips_for_rule).
    by_rule: dict[str, list[dict]] = {}
    for r in retrievals:
        by_rule.setdefault(r["rule_id"], []).append(r)
    K = 5  # default top-k retrievals per Worker prompt
    out: list[dict] = []
    for rid, rows in by_rule.items():
        rows.sort(key=lambda x: x["retrieved_at"])
        for idx, row in enumerate(rows):
            inferred_attempt = (idx // K) + 1
            key = (rid, inferred_attempt, row["tip_id"])
            row["fix_script"] = script_map.get(key)
            out.append(row)
    return out


def _write_followed_back(
    skill: str,
    updates: list[tuple[int, Optional[bool], Optional[float]]],
) -> int:
    """Write tip_followed_llm/emb/computed_at back to tip_retrievals.

    Updates is a list of (retrieval_id, followed_llm, followed_emb).
    Returns number of rows updated.
    """
    if not updates:
        return 0
    with psycopg.connect(_pg_conninfo("forge_admin")) as conn:
        conn.execute(f"SET search_path TO {skill}")
        with conn.cursor() as cur:
            cur.executemany(
                """
                UPDATE tip_retrievals
                   SET tip_followed_llm = %s,
                       tip_followed_emb = %s,
                       tip_followed_computed_at = now()
                 WHERE id = %s
                """,
                [(followed_llm, followed_emb, rid) for rid, followed_llm, followed_emb in updates],
            )
            affected = cur.rowcount
        conn.commit()
    return affected


async def score_tip_follow_for_run(
    run_id: str,
    skill: str,
    repo_root: Path,
    jsonl_path: Optional[Path] = None,
    model: Optional[str] = None,
) -> dict:
    """Compute tip_followed_llm + tip_followed_emb for every unscored
    retrieval in this run. Returns summary stats.

    Reads fix_scripts from the run's JSONL (canonical source — the
    DB's attempts.approach column is a 500-char narrative). Uses
    sentence-transformers on CPU for the embedding leg (the GPUs are
    pinned by vLLM) and batched httpx calls to vLLM for the LLM
    judge. Failure-safe per-row: if either signal can't be computed
    the column stays NULL and the other signal stands alone.
    """
    # Locate JSONL — caller may pass it explicitly (ralph.py knows
    # its log path), or we resolve via run.started_at.
    if jsonl_path is None:
        jsonl_path = _resolve_jsonl_path(run_id, repo_root)
    if jsonl_path is None or not jsonl_path.exists():
        logger.warning(
            "tip-follow scoring: no JSONL found for run_id=%s; skipping (no fix_scripts to compare)",
            run_id,
        )
        return {"rows": 0, "embedded": 0, "judged": 0, "skipped": "no_jsonl"}

    script_map = _parse_run_for_tip_followscoring(jsonl_path)
    if not script_map:
        logger.warning("tip-follow scoring: JSONL parsed but no (rule, attempt, tip) -> script entries built")
        return {"rows": 0, "embedded": 0, "judged": 0, "skipped": "empty_jsonl"}

    raw_rows = _fetch_unscored_retrievals(skill, run_id)
    if not raw_rows:
        return {"rows": 0, "embedded": 0, "judged": 0}
    rows = [r for r in _attach_fix_scripts(raw_rows, script_map) if r.get("fix_script")]
    logger.info(
        "tip-follow scoring: %d retrievals total, %d matched to a fix_script (others skip)",
        len(raw_rows), len(rows),
    )
    if not rows:
        return {"rows": 0, "embedded": 0, "judged": 0, "skipped": "no_matches"}

    # --- Embedding leg (CPU; the GPUs are pinned by vLLM) ---
    import os as _os
    _prev_cvd = _os.environ.get("CUDA_VISIBLE_DEVICES")
    _os.environ["CUDA_VISIBLE_DEVICES"] = ""
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        import numpy as np  # type: ignore
        encoder = SentenceTransformer(_EMBED_MODEL_NAME, device="cpu")
        tip_embs = encoder.encode([r["tip_text"] for r in rows], show_progress_bar=False)
        scr_embs = encoder.encode([r["fix_script"] for r in rows], show_progress_bar=False)
        emb_scores: list[Optional[float]] = []
        for t, a in zip(tip_embs, scr_embs):
            denom = float(np.linalg.norm(t)) * float(np.linalg.norm(a))
            emb_scores.append(float(np.dot(t, a) / denom) if denom > 0 else None)
        embedded_count = sum(1 for s in emb_scores if s is not None)
    except Exception as exc:
        logger.warning("tip-follow scoring: embedding step failed: %s", exc)
        emb_scores = [None] * len(rows)
        embedded_count = 0
    finally:
        if _prev_cvd is None:
            _os.environ.pop("CUDA_VISIBLE_DEVICES", None)
        else:
            _os.environ["CUDA_VISIBLE_DEVICES"] = _prev_cvd

    # --- LLM judge leg (batched concurrent to vLLM) ---
    judge = _load_judge_prompt(repo_root, skill)
    if not judge:
        logger.info("tip-follow scoring: no judge prompt for skill=%s; skipping LLM leg", skill)
        llm_scores: list[Optional[bool]] = [None] * len(rows)
        judged_count = 0
    else:
        system_prompt, user_template = judge
        judge_model = model or os.environ.get("FORGE_MODEL", "/weights/gemma-4-31B-it")
        sem = asyncio.Semaphore(_JUDGE_CONCURRENCY)

        async def _gated(client: httpx.AsyncClient, row: dict) -> Optional[bool]:
            async with sem:
                return await _judge_one(
                    client, system_prompt, user_template,
                    row["tip_text"], row["fix_script"], judge_model,
                )

        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            llm_scores = await asyncio.gather(*[_gated(client, r) for r in rows])
        judged_count = sum(1 for s in llm_scores if s is not None)

    updates = [
        (row["id"], llm, emb)
        for row, llm, emb in zip(rows, llm_scores, emb_scores)
    ]
    written = _write_followed_back(skill, updates)
    logger.info(
        "tip-follow scoring: %d rows scored (%d via embedding, %d via LLM judge), %d updated",
        len(rows), embedded_count, judged_count, written,
    )
    return {
        "rows": len(rows),
        "embedded": embedded_count,
        "judged": judged_count,
        "written": written,
        "jsonl_path": str(jsonl_path),
    }


async def run_dream_pass(
    run_id: str,
    repo_root: Optional[Path] = None,
    skill: str = "stig",
    environment_tag: Optional[str] = None,
    force: bool = False,
    jsonl_path: Optional[Path] = None,
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

    # Step 3.5 (DEF-27): per-retrieval causal attribution. For each
    # tip_retrievals row in this run, compute tip_followed_llm and
    # tip_followed_emb. Failure-safe: never break the dream pass on
    # a scoring error — the columns just stay NULL and the retrieval
    # ranker falls back to outcome-only.
    try:
        follow_stats = await score_tip_follow_for_run(
            run_id, skill, repo_root, jsonl_path=jsonl_path,
        )
        logger.info("dream pass: tip-follow scoring complete — %s", follow_stats)
    except Exception as exc:  # noqa: BLE001 — must not break credit assignment
        logger.warning("dream pass: tip-follow scoring failed: %s", exc)
        follow_stats = {"rows": 0, "embedded": 0, "judged": 0}

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
