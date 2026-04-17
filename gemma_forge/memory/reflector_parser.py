"""Parse structured tip JSON out of Reflector free-text output.

Phase F-next of the V2 memory architecture. The Reflector's prompt
keeps its existing BANNED/PREFERRED/LESSON/DISTILLED free-text
output and appends a ``TIPS_JSON:`` block with a ``tips_to_save``
array. The free text preserves analytical depth (Addendum 1 behavioral
risk); the JSON gives the harness structured fields for the V2 tips
table.

Parse is best-effort:

  - If the Reflector produces malformed JSON, ``parse_tips_json``
    returns an empty list. The existing free-text path still runs,
    so no regression.
  - If the Reflector produces tips without required fields, those
    individual tips are skipped (not the whole block).
  - If ``tip_type`` is outside the four-value vocabulary, the tip is
    skipped rather than silently rewritten — loud failure beats a
    quietly mislabeled row.

This module has no Postgres dependency; it's pure parsing so tests
can exercise every branch quickly.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


_VALID_TIP_TYPES = frozenset({"strategy", "recovery", "optimization", "warning"})

# The Reflector is prompted to emit the block after a ``TIPS_JSON:``
# marker. Be lenient about whitespace, case, and surrounding markdown
# code fences — models sometimes wrap JSON in ```json ... ``` even when
# told not to.
_TIPS_JSON_MARKER = re.compile(r"TIPS_JSON\s*:?", re.IGNORECASE)


def extract_json_object(text: str, start_offset: int = 0) -> Optional[str]:
    """Return the first balanced ``{...}`` substring starting at or after
    ``start_offset``, or None if none found. Handles escaped quotes
    inside strings so a ``"}"`` literal inside a value doesn't close
    the object prematurely.
    """
    n = len(text)
    # Find the first '{' at/after start_offset
    i = start_offset
    while i < n and text[i] != "{":
        i += 1
    if i >= n:
        return None

    depth = 0
    in_string = False
    string_char = ""
    escape_next = False
    start = i
    while i < n:
        ch = text[i]
        if escape_next:
            escape_next = False
        elif in_string:
            if ch == "\\":
                escape_next = True
            elif ch == string_char:
                in_string = False
        else:
            if ch == '"' or ch == "'":
                in_string = True
                string_char = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        i += 1
    return None


def _normalize_str_list(value: Any) -> Optional[list[str]]:
    """Accept list[str] or str; return a clean list or None."""
    if value is None:
        return None
    if isinstance(value, list):
        out = [str(x).strip() for x in value if x and str(x).strip()]
        return out or None
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return None


def _validate_tip(obj: dict) -> Optional[dict]:
    """Return a clean tip dict, or None if invalid.

    Required: ``text`` (non-empty), ``tip_type`` (one of the four).
    Optional: ``trigger_conditions`` (list[str]), ``application_context``
    (list[str]). Unknown fields are dropped silently.
    """
    text = obj.get("text")
    if not isinstance(text, str) or not text.strip():
        logger.debug("reflector_parser: dropping tip with missing/empty text")
        return None
    tip_type = obj.get("tip_type")
    if not isinstance(tip_type, str):
        logger.debug("reflector_parser: dropping tip with non-string tip_type %r", tip_type)
        return None
    tip_type = tip_type.strip().lower()
    if tip_type not in _VALID_TIP_TYPES:
        logger.debug("reflector_parser: dropping tip with bad tip_type %r", tip_type)
        return None
    return {
        "text": text.strip(),
        "tip_type": tip_type,
        "trigger_conditions": _normalize_str_list(obj.get("trigger_conditions")),
        "application_context": _normalize_str_list(obj.get("application_context")) or [],
    }


def parse_tips_json(ref_output: str) -> list[dict]:
    """Extract a clean list of tip dicts from a Reflector response.

    Returns [] if no block, no JSON object, parse error, or zero
    valid tips. Never raises — callers can proceed on [] without
    special-casing.
    """
    if not ref_output:
        return []

    marker = _TIPS_JSON_MARKER.search(ref_output)
    if not marker:
        return []

    obj_text = extract_json_object(ref_output, start_offset=marker.end())
    if obj_text is None:
        logger.debug("reflector_parser: no JSON object after TIPS_JSON marker")
        return []

    try:
        obj = json.loads(obj_text)
    except json.JSONDecodeError as exc:
        logger.info("reflector_parser: JSON parse failed, dropping block: %s", exc)
        return []

    if not isinstance(obj, dict):
        return []
    raw_tips = obj.get("tips_to_save")
    if not isinstance(raw_tips, list):
        return []

    clean: list[dict] = []
    for i, t in enumerate(raw_tips):
        if not isinstance(t, dict):
            continue
        v = _validate_tip(t)
        if v is not None:
            clean.append(v)
    if not clean:
        logger.info("reflector_parser: TIPS_JSON had %d tips, none valid", len(raw_tips))
    return clean


# ---------------------------------------------------------------------
# Prompt fragments — shared between the failure-mode and success-mode
# Reflector calls so the JSON schema description is authored in one
# place.
# ---------------------------------------------------------------------


TIPS_JSON_INSTRUCTIONS = """After your free-text analysis, emit exactly ONE line starting with TIPS_JSON: followed by a JSON object of this shape:

TIPS_JSON: {"tips_to_save": [{"text": "...", "tip_type": "strategy|recovery|optimization|warning", "trigger_conditions": ["...", "..."], "application_context": ["..."]}]}

Field guidance:
- text: one actionable sentence. Self-contained — do not reference "this attempt" or "the Worker"; future runs will read this alone.
- tip_type: pick exactly one.
    strategy — a positive approach known to work ("use X")
    recovery — failure-derived advice where the prescription is vague or diagnostic
    optimization — a refinement of a working approach (edge cases, precision)
    warning — a specific pattern or command to avoid
- trigger_conditions: short phrases describing when the tip applies ("audit rule modification", "Rocky 9", "augenrules available"). Optional; emit null or [] when you cannot name any.
- application_context: skill-scoped identifiers for what this tip applies to (category name, rule family prefix). Optional; defaults to the current rule's category.

Emit zero, one, or a few tips — only those you are confident would help a future attempt on a similar rule. Empty list ({"tips_to_save": []}) is an acceptable answer when the attempt produced nothing reusable."""
