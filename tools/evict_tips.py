#!/usr/bin/env python
"""tools/evict_tips.py — run a V2 tip eviction sweep.

Phase H1 of the V2 memory architecture. Computes per-tip utility
from stig.tip_retrievals, retires tips whose average utility falls
below the skill's threshold AFTER accumulating the skill's minimum
number of outcomes. Retired tips are bi-temporally marked (retired_at
+ retired_reason), never deleted.

Usage:
  ./tools/evict_tips.py --skill stig [--dry-run]
  ./tools/evict_tips.py --skill stig --min-retrievals 5 --threshold 0.4

Thresholds default to the skill's EvaluatorMetadata. Overrides are
provided for experimentation — production should use the skill's
declared values.

Intended to be called:
  - Manually, between runs (offline curation)
  - From the dream pass, as an additive step (integration TBD)
  - From the harness, automatically at end-of-run (integration TBD)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from gemma_forge.memory.eviction import evict_low_utility_tips  # noqa: E402

logger = logging.getLogger("evict_tips")


def _skill_evaluator_metadata(skill: str):
    """Import the skill's Evaluator and return its metadata.

    Keeps eviction skill-agnostic — the thresholds come from the
    skill, not from this CLI. Loads via importlib because skill
    directories have hyphens (e.g. stig-rhel9) that aren't valid
    Python module identifiers; ralph.py uses the same pattern.
    """
    import importlib.util

    # Map schema name (e.g. "stig") to skill directory. If more skills
    # land, read this from skill manifest metadata instead.
    skill_dir_name = {"stig": "stig-rhel9"}.get(skill, skill)
    runtime_path = REPO_ROOT / "skills" / skill_dir_name / "runtime.py"
    if not runtime_path.is_file():
        raise SystemExit(
            f"evict_tips: no skill runtime at {runtime_path}; "
            f"pass explicit --min-retrievals + --threshold."
        )
    spec = importlib.util.spec_from_file_location(f"{skill}_runtime", runtime_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Convention: every skill runtime exposes an Evaluator class whose
    # name ends with "Evaluator" and carries a .metadata class attribute.
    for name in dir(mod):
        cls = getattr(mod, name)
        if (isinstance(cls, type) and name.endswith("Evaluator")
                and hasattr(cls, "metadata")):
            return cls.metadata
    raise SystemExit(
        f"evict_tips: {runtime_path} has no *Evaluator class with .metadata"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--skill", default="stig")
    parser.add_argument("--min-retrievals", type=int, default=None,
                        help="Override skill's min_retrievals_before_eviction.")
    parser.add_argument("--threshold", type=float, default=None,
                        help="Override skill's eviction_threshold.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute + report but do not retire any tips.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Resolve thresholds from skill metadata unless overridden.
    # We only load the skill if the user didn't give explicit overrides.
    if args.min_retrievals is None or args.threshold is None:
        try:
            meta = _skill_evaluator_metadata(args.skill)
        except Exception as exc:
            logger.error("Could not load %s skill metadata: %s", args.skill, exc)
            logger.error("Either fix the skill import, or pass explicit "
                         "--min-retrievals and --threshold.")
            return 2
        min_retrievals = args.min_retrievals or meta.min_retrievals_before_eviction
        threshold = args.threshold if args.threshold is not None else meta.eviction_threshold
    else:
        min_retrievals = args.min_retrievals
        threshold = args.threshold

    logger.info("Eviction sweep: skill=%s min_retrievals=%d threshold=%.2f dry_run=%s",
                args.skill, min_retrievals, threshold, args.dry_run)

    report = evict_low_utility_tips(
        skill=args.skill,
        min_retrievals=min_retrievals,
        threshold=threshold,
        dry_run=args.dry_run,
    )

    print()
    print(f"  Active tips before sweep:          {report.total_active_tips}")
    print(f"  Tips with ≥{min_retrievals} outcome(s):            "
          f"{report.tips_with_sufficient_evidence}")
    print(f"  Tips below threshold {threshold}:            "
          f"{len(report.retired)}")
    if args.dry_run:
        print(f"  [dry-run] no rows updated")
    else:
        print(f"  Tips retired this sweep:           {report.tips_retired_this_sweep}")
    print(f"  Active tips after sweep:           {report.remaining_active}")
    print()

    if report.retired:
        print("  Retired tips (sorted by avg_utility ascending):")
        for c in report.retired[:25]:
            src_short = (c.source_rule_id or "").split("content_rule_")[-1][:40]
            print(f"    tip_id={c.tip_id:<6} avg_u={c.avg_utility:.3f}  "
                  f"n={c.n_outcomes}  [{c.tip_type}]  src={src_short}")
            print(f"      {c.text_preview}")
        if len(report.retired) > 25:
            print(f"    ... +{len(report.retired) - 25} more")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
