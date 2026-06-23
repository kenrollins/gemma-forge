"""Sigma-rule evaluator — runs one Sigma rule against a corpus subset.

Friday-night slice: supports the constructs the cred-dumping rule
(``proc_access_win_lsass_memdump.yml``) uses, which is the common
80% of SigmaHQ rules:

  Modifiers: |contains, |endswith, |startswith, |all (combines with
             contains), |re (regex), |cidr (skipped — returns
             RULE_PARSE_FAILURE)
  Values:    string equality, list (OR by default, AND with |all),
             null/empty, integer / hex-integer
  Condition: bare name, `not`, `and`, `or`, parens, `1 of <pattern>`,
             `all of <pattern>`, `*` wildcard in patterns

Exotic constructs raise ``RuleParseError`` — the harness routes that
to the RULE_PARSE_FAILURE failure mode so the Reflector knows
the rule isn't broken, the evaluator just doesn't speak its dialect.

Why we parse the YAML directly instead of using pysigma's AST:
    pysigma's object model is structured for backend codegen (SPL,
    KQL, ESQL) — its primitives don't map cleanly to "evaluate
    against a pandas Series." Walking the YAML dict directly gives
    us the exact semantics we need with ~80 lines instead of fighting
    pysigma's type lattice.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml


class RuleParseError(Exception):
    """The rule uses a Sigma construct this evaluator doesn't support."""


@dataclass(frozen=True)
class EvalScores:
    tp: int
    fp: int
    fn: int
    tn: int
    precision: float
    recall: float
    f1: float
    matched_count: int


# -- Field-level matchers ----------------------------------------------------


def _hex_int_eq(a: str, b: str) -> bool:
    """0x1fffff == 0x001FFFFF when both look like hex."""
    if not (isinstance(a, str) and isinstance(b, str)):
        return False
    al, bl = a.lower(), b.lower()
    if not (al.startswith("0x") and bl.startswith("0x")):
        return False
    try:
        return int(al, 16) == int(bl, 16)
    except ValueError:
        return False


def _string_eq(field_value: str, rule_value) -> bool:
    """Default equality: case-insensitive string compare, with hex-int promotion."""
    if rule_value is None:
        return field_value == "" or field_value is None
    if isinstance(rule_value, (int, float)):
        rule_value = str(rule_value)
    fv = (field_value or "").lower()
    rv = (rule_value or "").lower()
    if fv == rv:
        return True
    return _hex_int_eq(field_value, rule_value)


def _make_matcher(modifier: str | None) -> Callable[[str, str], bool]:
    """Build a comparator for one Sigma modifier (or default)."""
    if modifier is None:
        return _string_eq
    if modifier == "contains":
        return lambda fv, rv: (rv or "").lower() in (fv or "").lower()
    if modifier == "endswith":
        return lambda fv, rv: (fv or "").lower().endswith((rv or "").lower())
    if modifier == "startswith":
        return lambda fv, rv: (fv or "").lower().startswith((rv or "").lower())
    if modifier == "re":
        return lambda fv, rv: bool(re.search(rv, fv or "", flags=re.IGNORECASE))
    raise RuleParseError(f"unsupported field modifier: {modifier}")


def _eval_field(
    field_value: str,
    modifiers: list[str],
    rule_value,
) -> bool:
    """Apply a single field's matcher(s) against a single event value."""
    if "cidr" in modifiers:
        raise RuleParseError("|cidr modifier not supported in Friday-night slice")

    primary = next((m for m in modifiers if m != "all"), None)
    matcher = _make_matcher(primary)
    require_all = "all" in modifiers

    if isinstance(rule_value, list):
        results = [matcher(field_value, v) for v in rule_value]
        return all(results) if require_all else any(results)
    return matcher(field_value, rule_value)


# -- Block-level evaluation --------------------------------------------------


def _build_block_predicate(block: dict) -> Callable[[pd.Series], bool]:
    """Compile one Sigma selection/filter block into a row predicate.

    A block is a dict of {field_spec: rule_value} where field_spec is
    either ``Field`` or ``Field|mod1|mod2``. All entries in the block
    are ANDed together. Empty block (rare, malformed) evaluates True.
    """
    if not isinstance(block, dict) or not block:
        # An empty dict in a selection means "match everything" in Sigma's
        # semantics. Rare but legal — treat as constant-True.
        return lambda row: True

    field_clauses: list[tuple[str, list[str], object]] = []
    for field_spec, rule_value in block.items():
        if "|" in field_spec:
            parts = field_spec.split("|")
            field = parts[0]
            modifiers = parts[1:]
        else:
            field = field_spec
            modifiers = []
        field_clauses.append((field, modifiers, rule_value))

    def predicate(row: pd.Series) -> bool:
        for field, modifiers, rule_value in field_clauses:
            event_value = row.get(field, "")
            if not _eval_field(event_value, modifiers, rule_value):
                return False
        return True

    return predicate


# -- Condition expression evaluation -----------------------------------------


_QUANTIFIER_RE = re.compile(r"^(1|all)\s+of\s+(\S+)$")


def _resolve_pattern(pattern: str, block_names: list[str]) -> list[str]:
    """Resolve `selection_*`, `filter_*`, or `them` to actual block names."""
    if pattern == "them":
        return list(block_names)
    if "*" in pattern:
        # Glob → regex
        rx = re.compile("^" + re.escape(pattern).replace(r"\*", ".*") + "$")
        return [n for n in block_names if rx.match(n)]
    return [pattern] if pattern in block_names else []


def _eval_condition(
    condition: str,
    block_results: dict[str, bool],
) -> bool:
    """Evaluate a Sigma condition expression against pre-computed block results."""
    block_names = list(block_results.keys())

    # Replace `1 of pattern` / `all of pattern` with parenthesized
    # OR / AND expressions over resolved block names.
    def expand_quantifier(match: re.Match) -> str:
        quant, pattern = match.group(1), match.group(2)
        resolved = _resolve_pattern(pattern, block_names)
        if not resolved:
            return "False"
        joiner = " or " if quant == "1" else " and "
        return "(" + joiner.join(resolved) + ")"

    expanded = re.sub(
        r"(1|all)\s+of\s+([A-Za-z_][A-Za-z0-9_*]*)",
        expand_quantifier,
        condition,
    )

    # Now we have a plain Python-ish boolean expression over block names.
    # Replace bare block-name identifiers with their boolean values.
    # Use word-boundary so substrings of names don't get hit.
    def sub_name(match: re.Match) -> str:
        name = match.group(0)
        if name in ("and", "or", "not", "True", "False"):
            return name
        if name in block_results:
            return "True" if block_results[name] else "False"
        # Unknown identifier in a condition is a Sigma rule bug; treat as False.
        return "False"

    py_expr = re.sub(r"\b[A-Za-z_][A-Za-z0-9_]*\b", sub_name, expanded)

    try:
        return bool(eval(py_expr, {"__builtins__": {}}, {}))
    except Exception as exc:
        raise RuleParseError(
            f"Could not evaluate condition: {condition!r} → {py_expr!r} ({exc})"
        ) from exc


# -- Public API --------------------------------------------------------------


def load_rule(rule_path: str | Path) -> dict:
    """Parse a Sigma rule YAML file. Returns the raw dict."""
    with open(rule_path) as f:
        return yaml.safe_load(f)


def rule_logsource(rule: dict) -> tuple[str, str]:
    """Return (category, product) for the rule's logsource declaration."""
    ls = rule.get("logsource") or {}
    category = ls.get("category", "")
    product = ls.get("product", "")
    if not category:
        raise RuleParseError("rule has no logsource.category — not supported")
    return category, product


def compile_rule(rule: dict) -> Callable[[pd.Series], bool]:
    """Compile a parsed Sigma rule to a row predicate."""
    detection = rule.get("detection")
    if not isinstance(detection, dict):
        raise RuleParseError("rule has no detection block")
    condition = detection.get("condition")
    if not isinstance(condition, str):
        raise RuleParseError("rule has no condition or condition is non-string")

    # Build a predicate per named block (everything that isn't "condition")
    block_preds: dict[str, Callable[[pd.Series], bool]] = {}
    for name, block in detection.items():
        if name == "condition":
            continue
        if isinstance(block, list):
            # Some rules use a list-of-dicts for OR-ing alternatives within a
            # named block. Friday-night slice doesn't handle this; surface
            # cleanly so the harness can classify.
            raise RuleParseError(f"block {name!r} is a list — not supported in Friday-night slice")
        block_preds[name] = _build_block_predicate(block)

    def predicate(row: pd.Series) -> bool:
        results = {name: pred(row) for name, pred in block_preds.items()}
        return _eval_condition(condition, results)

    return predicate


def evaluate_rule(
    rule_path: str | Path,
    events: pd.DataFrame,
    positive_mask: pd.Series,
) -> EvalScores:
    """Compute P/R/F1 for a Sigma rule against a labeled event subset.

    ``events`` must already be scoped to the rule's logsource (use
    ``CorpusLoader.scope_for_logsource``). ``positive_mask`` is a
    bool Series aligned with ``events.index``.
    """
    rule = load_rule(rule_path)
    predicate = compile_rule(rule)
    matched = (
        events.apply(predicate, axis=1)
        if len(events)
        else pd.Series(
            [],
            dtype=bool,
            index=events.index,
        )
    )
    return _score(matched, positive_mask)


def _score(matched: pd.Series, positive: pd.Series) -> EvalScores:
    tp = int((matched & positive).sum())
    fp = int((matched & ~positive).sum())
    fn = int((~matched & positive).sum())
    tn = int((~matched & ~positive).sum())
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return EvalScores(
        tp=tp,
        fp=fp,
        fn=fn,
        tn=tn,
        precision=precision,
        recall=recall,
        f1=f1,
        matched_count=int(matched.sum()),
    )
