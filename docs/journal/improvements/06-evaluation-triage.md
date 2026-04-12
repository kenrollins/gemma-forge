---
id: improvement-06-evaluation-triage
type: improvement
title: "Improvement #6: Evaluation Triage"
date: 2026-04-12
tags: [L4-orchestration, reflexion-loop, snapshot-revert]
related:
  - journey/18-second-overnight-run
  - improvements/04-snapshot-based-revert
  - architecture/01-reflexive-agent-harness-failure-modes
---

# Improvement #6: Evaluation Triage

## Problem

The evaluator treats all failures identically: revert and retry. But
the second overnight run reveals three distinct failure modes with
different optimal responses, and the current binary logic wastes time
on one and throws away working fixes on another.

**Mode A — Health failure (2.3% of reverts):** The fix broke something.
Revert immediately. **Current behavior is correct.**

**Mode B — Scanner gap (88% of reverts):** Health checks pass, but the
OpenSCAP scanner says the rule still fails. The model writes
technically correct configuration that the scanner doesn't recognize.
After 3+ clean attempts with different approaches, this is a
*knowledge gap*, not a *logic gap*. Current behavior: grind to 15+
attempts before escalating.

**Mode C — False-negative revert (2.1% of reverts):** The rule
*actually passed* the scanner, but journal noise (non-fatal warnings,
service restarts) caused the evaluator to classify the fix as failed.
9 good fixes were reverted. The harness then re-discovered the same
fix on a later attempt.

## Impact

- Mode B: early scanner-gap detection would save ~4 hours of the 6.1h
  spent on escalated rules. 88% of reverts are this pattern.
- Mode C: eliminating false-negative reverts would prevent 9 wasted
  re-discovery cycles (est. 15–30 minutes).

## Proposed mechanism

### Scanner-gap detector

Track consecutive reverts where `health=True` and `rule=False`. If
a rule accumulates 3+ such reverts with *distinct approaches* (not
just retries of the same command), flag it as a **scanner semantic
gap** and recommend early escalation to the architect.

The architect's re-engagement prompt should include a flag:
`scanner_gap_detected: true`. This lets the architect make a more
informed PIVOT/ESCALATE decision — "the model has tried 3 different
file-writing strategies and all produce correct config that the
scanner rejects."

### Journal-noise tolerance

When the scanner reports `rule=True` (the rule passes compliance),
treat the fix as successful *regardless of journal warnings*. Journal
noise on a passing rule is diagnostic information, not a revert
condition.

Specifically: change the evaluation priority order from:
```
if not health_ok: REVERT
if not journal_clean: REVERT
if rule_passed: ACCEPT
```
to:
```
if not health_ok: REVERT
if rule_passed: ACCEPT  (journal noise is logged but not blocking)
if not journal_clean: REVERT  (only blocks if rule also failed)
```

## What this is not

This is not about making the evaluator smarter about *which* fixes
to try — that's the architect's job. This is about giving the
evaluator enough vocabulary to describe what happened so the architect
can make better decisions, and about not throwing away working fixes.

## Verification

- **Scanner-gap detector**: Create a test rule that always passes
  health but always fails the scanner. After 3 attempts, confirm the
  architect receives the `scanner_gap_detected` flag.
- **Journal tolerance**: Create a test where the rule passes but
  journal has warnings. Confirm the fix is accepted, not reverted.
- **No regression**: Confirm that health failures still trigger
  immediate revert.
