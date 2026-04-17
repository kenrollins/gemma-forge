#!/usr/bin/env python
"""tools/smoke_v2_events.py — verify V2 event plumbing in a run JSONL.

Post-run check that exercises the Phase F-H plumbing landed:

  1. prompt_assembled events carry both category_lessons_loaded (V1)
     and v2_tips_loaded (V2) for Worker prompts
  2. tip_added events fire with the enriched key set the UI expects
  3. ban_added events include outcome_at_source_confidence
  4. evaluation events carry an outcome_signal block
  5. tip_retired events appear at startup (if offline eviction ran)
  6. tip_writer actually wrote rows to stig.tips (spot-check count)
  7. tip_retrievals rows got their outcome_value filled in post-eval

Usage:
  ./tools/smoke_v2_events.py runs/run-YYYY-mm-dd.jsonl
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path


REQUIRED_TIP_ADDED_KEYS = {
    "tip_id", "tip_type", "text", "rule_id", "category",
    "trigger_conditions", "application_context", "phase", "tips_total",
    "outcome_at_source_value", "outcome_at_source_confidence",
    "environment_tag", "source_attempt_id", "source_run_id",
}

REQUIRED_BAN_ADDED_KEYS = {
    "rule_id", "pattern", "attempt", "banned_patterns_total",
    "outcome_at_source_confidence",
}

REQUIRED_EVAL_OUTCOME_KEYS = {"value", "confidence", "utility_contribution", "signal_type"}


def color(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m"


def ok(msg: str) -> None:
    print(f"  {color('32', '✓')} {msg}")


def fail(msg: str) -> None:
    print(f"  {color('31', '✗')} {msg}")


def warn(msg: str) -> None:
    print(f"  {color('33', '!')} {msg}")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"smoke_v2_events: {path} not found")
        return 2

    events: list[dict] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    counts = collections.Counter(e["event_type"] for e in events if "event_type" in e)
    print(f"\nEvents seen ({len(events)} total):")
    for t, n in counts.most_common():
        print(f"  {n:>6}  {t}")
    print()

    failures = 0

    # ---- Check 1: prompt_assembled carries both lesson snapshots -----
    print(color("1", "== prompt_assembled (Worker) has V1 + V2 snapshots =="))
    worker_prompts = [e for e in events
                      if e.get("event_type") == "prompt_assembled"
                      and e.get("data", {}).get("phase") == "apply_fix"]
    if not worker_prompts:
        fail("no apply_fix prompt_assembled events")
        failures += 1
    else:
        with_v1 = [e for e in worker_prompts if "category_lessons_loaded" in e["data"]]
        with_v2 = [e for e in worker_prompts if "v2_tips_loaded" in e["data"]]
        ok(f"{len(worker_prompts)} Worker prompts; V1-snapshot on {len(with_v1)}, V2-snapshot on {len(with_v2)}")
        nonempty_v2 = sum(1 for e in with_v2 if e["data"]["v2_tips_loaded"])
        if nonempty_v2:
            ok(f"{nonempty_v2} prompts had ≥1 V2 tip retrieved")
        else:
            warn("no prompt had any V2 tips retrieved — tips table may be empty or no similarity match")

    # ---- Check 2: tip_added events well-formed -----------------------
    print(color("1", "\n== tip_added events carry the enriched key set =="))
    tip_adds = [e for e in events if e.get("event_type") == "tip_added"]
    if not tip_adds:
        warn("no tip_added events — Reflector may not have emitted TIPS_JSON, or parser dropped all blocks")
    else:
        ok(f"{len(tip_adds)} tip_added events")
        phases = collections.Counter(e["data"].get("phase") for e in tip_adds)
        for phase, n in phases.items():
            ok(f"  phase={phase}: {n}")
        sample = tip_adds[0]["data"]
        missing = REQUIRED_TIP_ADDED_KEYS - set(sample.keys())
        if missing:
            fail(f"tip_added missing required keys: {sorted(missing)}")
            failures += 1
        else:
            ok("all required keys present on sample event")
        # Check tip_type is in the vocabulary
        bad_types = [e["data"]["tip_type"] for e in tip_adds
                     if e["data"].get("tip_type") not in {"strategy", "recovery", "optimization", "warning"}]
        if bad_types:
            fail(f"tip_added with unknown tip_type: {set(bad_types)}")
            failures += 1
        else:
            ok(f"all tip_type values in vocabulary; distribution: {dict(collections.Counter(e['data']['tip_type'] for e in tip_adds))}")

    # ---- Check 3: ban_added carries outcome_at_source_confidence -----
    print(color("1", "\n== ban_added carries outcome_at_source_confidence =="))
    ban_adds = [e for e in events if e.get("event_type") == "ban_added"]
    if not ban_adds:
        warn("no ban_added events (no BANNED: output from Reflector)")
    else:
        ok(f"{len(ban_adds)} ban_added events")
        missing = [e for e in ban_adds if "outcome_at_source_confidence" not in e["data"]]
        if missing:
            fail(f"{len(missing)} ban_added events missing outcome_at_source_confidence")
            failures += 1
        else:
            ok("all ban_added events have outcome_at_source_confidence")

    # ---- Check 4: evaluation carries outcome_signal ------------------
    print(color("1", "\n== evaluation events carry outcome_signal =="))
    evals = [e for e in events if e.get("event_type") == "evaluation"]
    if not evals:
        fail("no evaluation events — did the harness actually evaluate anything?")
        failures += 1
    else:
        with_sig = [e for e in evals if "outcome_signal" in e["data"]]
        if len(with_sig) != len(evals):
            fail(f"{len(evals) - len(with_sig)}/{len(evals)} evaluation events missing outcome_signal")
            failures += 1
        else:
            ok(f"all {len(evals)} evaluation events have outcome_signal")
            sample = evals[0]["data"]["outcome_signal"]
            missing = REQUIRED_EVAL_OUTCOME_KEYS - set(sample.keys())
            if missing:
                fail(f"outcome_signal missing keys: {sorted(missing)}")
                failures += 1
            else:
                ok(f"outcome_signal shape: {sample}")

    # ---- Check 5: tip_retired events (optional) ----------------------
    print(color("1", "\n== tip_retired events (only if offline eviction ran) =="))
    retired = [e for e in events if e.get("event_type") == "tip_retired"]
    if retired:
        ok(f"{len(retired)} tip_retired events at startup")
    else:
        warn("no tip_retired events — expected if no eviction sweep has run yet")

    # ---- Check 6: tips table actually grew ---------------------------
    print(color("1", "\n== stig.tips table received the inserts =="))
    try:
        import sys as _sys
        REPO_ROOT = Path(__file__).resolve().parents[1]
        _sys.path.insert(0, str(REPO_ROOT))
        from gemma_forge.harness.db import get_pool
        pool = get_pool("forge_admin")
        with pool.connection() as c, c.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM stig.tips WHERE retired_at IS NULL")
            n_active = cur.fetchone()[0]
            cur.execute("""
                SELECT COUNT(*) FROM stig.tips
                WHERE source_run_id IS NOT NULL
                  AND created_at > (now() - interval '2 hours')
            """)
            n_fresh = cur.fetchone()[0]
        ok(f"{n_active} active tips in stig.tips; {n_fresh} written in the last 2 hours")
        if tip_adds and n_fresh == 0:
            fail(f"tip_added emitted {len(tip_adds)} events but no tips inserted in last 2h — writer path broken?")
            failures += 1
    except Exception as exc:
        warn(f"could not query stig.tips: {exc}")

    # ---- Check 7: tip_retrievals outcomes got filled -----------------
    print(color("1", "\n== tip_retrievals outcomes got populated post-eval =="))
    try:
        with pool.connection() as c, c.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*),
                       COUNT(*) FILTER (WHERE outcome_value IS NOT NULL)
                FROM stig.tip_retrievals
                WHERE retrieved_at > (now() - interval '2 hours')
            """)
            total, with_outcome = cur.fetchone()
        if total == 0:
            warn("no tip_retrievals rows in last 2h — V2 retrieval may not have found anything to load")
        else:
            ok(f"{total} tip_retrievals rows; {with_outcome} have outcome_value filled ({with_outcome/total*100:.0f}%)")
            if with_outcome == 0 and total > 0:
                fail("tip_retrievals written but no outcomes updated — update_retrieval_outcomes not firing?")
                failures += 1
    except Exception as exc:
        warn(f"could not query stig.tip_retrievals: {exc}")

    print()
    if failures:
        print(color("31", f"✗ {failures} smoke check(s) failed"))
        return 1
    print(color("32", "✓ All smoke checks passed"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
