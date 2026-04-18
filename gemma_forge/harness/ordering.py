"""Skill-declared ordering constraints — skill-agnostic, harness-enforced.

The problem this module closes: prompt-level guidance is not enforcement.
A skill's Architect prompt can say "process rule X last" and the
Architect can ignore it. The harness needs a mechanism for skills to
declare ordering constraints that hold regardless of what the Architect
chooses, and for those constraints to filter the Architect's candidate
pool before the prompt is built — not nag the Architect through prose.

Pattern: the skill's ``skill.yaml`` declares *what* (a rule_id and a
predicate expression). The harness evaluates *how* (compute visible vs
deferred subset against current run state before each rule selection).
Neither knows about the other's domain; both agree on the manifest
schema.

Canonical first use: STIG's ``audit_rules_immutable`` locks the kernel
audit subsystem when applied, causing every subsequent ``audit_rules_*``
change to fail until reboot. The skill declares
``defer_until: category_nearly_complete(category=audit, remaining_lte=1)``
— the harness hides the rule from the Architect's pool until audit has
at most one rule still unfinished (that rule being audit_rules_immutable
itself, the last one to run).

Adding a new predicate: implement a function that takes the predicate's
params dict plus the current list of failing rules, returns True if the
constraint is currently *active* (rule should be deferred). Register it
in ``_PREDICATES``. The schema is open for extension; current predicates
cover the known cases (one, ``category_nearly_complete``, as of Run 6).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrderingConstraint:
    """One skill-declared ordering constraint, loaded from skill.yaml.

    The rule identified by ``rule_id`` is deferred from the Architect's
    candidate pool whenever ``predicate`` evaluated against current run
    state returns True. ``reason`` is the human-readable explanation
    logged when the constraint fires.
    """
    rule_id: str
    predicate: str
    params: dict
    reason: str


def _category_nearly_complete(params: dict, failing_rules: list[dict]) -> bool:
    """Predicate: defer while more than ``remaining_lte`` rules of
    ``category`` are still unfinished.

    "Unfinished" == present in failing_rules (which is the harness's
    to-process queue). Once the count of same-category rules drops to
    the threshold or below, the constraint releases and the protected
    rule becomes visible to the Architect.
    """
    category = params["category"]
    remaining_lte = params["remaining_lte"]
    same_category_count = sum(
        1 for r in failing_rules if r.get("category") == category
    )
    # Constraint is ACTIVE (defer) whenever the count is ABOVE threshold.
    # At count == threshold the constraint releases — exactly the moment
    # where the protected rule is one of the last ``remaining_lte + 1``
    # in its category and can safely run.
    return same_category_count > remaining_lte


_PREDICATES: dict[str, Callable[[dict, list[dict]], bool]] = {
    "category_nearly_complete": _category_nearly_complete,
}


def load_constraints_from_manifest(skill_dir: Path) -> list[OrderingConstraint]:
    """Read the skill.yaml's optional ``ordering_constraints`` block.

    Missing block or malformed entries yield an empty list (soft failure,
    the harness should log a warning but not crash — a skill without
    ordering constraints is a valid configuration).
    """
    manifest_path = skill_dir / "skill.yaml"
    if not manifest_path.exists():
        return []

    try:
        with open(manifest_path) as f:
            raw = yaml.safe_load(f) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("ordering: skill.yaml parse error at %s: %s", manifest_path, exc)
        return []

    block = raw.get("ordering_constraints") or []
    constraints: list[OrderingConstraint] = []
    for i, entry in enumerate(block):
        try:
            rule_id = entry["rule_id"]
            defer_until = entry["defer_until"]
            predicate = defer_until.pop("predicate")
            params = defer_until
            reason = entry.get("reason", "")
            if predicate not in _PREDICATES:
                logger.warning(
                    "ordering: unknown predicate '%s' in constraint %d; skipping",
                    predicate, i,
                )
                continue
            constraints.append(OrderingConstraint(
                rule_id=rule_id,
                predicate=predicate,
                params=params,
                reason=reason,
            ))
        except (KeyError, TypeError) as exc:
            logger.warning("ordering: malformed constraint %d: %s", i, exc)
            continue
    return constraints


def is_deferred(
    constraint: OrderingConstraint,
    failing_rules: list[dict],
) -> bool:
    """Evaluate one constraint against the current failing-rules list."""
    predicate_fn = _PREDICATES.get(constraint.predicate)
    if predicate_fn is None:
        # Unknown predicate — should have been caught at load time, but
        # if something survives to here, treat as not-deferred (permissive
        # fallback, with a log warning).
        logger.warning(
            "ordering: predicate '%s' missing at eval time; allowing rule",
            constraint.predicate,
        )
        return False
    try:
        return predicate_fn(constraint.params, failing_rules)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ordering: predicate %s raised on %s: %s; allowing rule",
            constraint.predicate, constraint.rule_id, exc,
        )
        return False


def filter_deferred(
    failing_rules: list[dict],
    constraints: list[OrderingConstraint],
) -> tuple[list[dict], list[tuple[dict, OrderingConstraint]]]:
    """Split ``failing_rules`` into (visible, deferred_with_reasons).

    Deferred entries carry the constraint that deferred them so the
    harness can log a ``rule_deferred`` event with reason intact.
    Rules not covered by any constraint are always visible.
    """
    if not constraints:
        return failing_rules, []

    # Build rule_id -> matching constraint map for O(1) lookup
    by_rule: dict[str, OrderingConstraint] = {c.rule_id: c for c in constraints}

    visible: list[dict] = []
    deferred: list[tuple[dict, OrderingConstraint]] = []
    for rule in failing_rules:
        rid = rule.get("rule_id", "")
        constraint = by_rule.get(rid)
        if constraint is None:
            visible.append(rule)
            continue
        if is_deferred(constraint, failing_rules):
            deferred.append((rule, constraint))
        else:
            visible.append(rule)
    return visible, deferred
