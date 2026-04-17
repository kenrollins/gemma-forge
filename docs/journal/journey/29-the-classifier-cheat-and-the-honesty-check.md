---
id: journey-29-the-classifier-cheat-and-the-honesty-check
type: journey
title: "The Classifier Cheat and the Honesty Check"
date: 2026-04-17
tags: [V2-memory, phase-F, tip-classification, methodology, failure-mode]
related:
  - journey/28-run-4-and-the-coarseness-problem
  - adr/0016-graphiti-neo4j-postgres-memory-stack
one_line: "We almost shipped a prompt-tuned classifier into the V2 backfill so the Memory tab wouldn't lean amber. The question that stopped the commit — 'are we doing what the system will do naturally from this point on?' — had an answer that was uncomfortable but simple: no. This entry documents the near-miss, because the way we avoid cheating on our own experiments matters more than the specific choice."
---

# The Classifier Cheat and the Honesty Check

## The setup

Phase F is the V2 memory architecture's tip-generation layer. Before the F-next Reflector change that would start producing tips naturally, F3 backfills the existing 2,353 V1 lessons into the new `stig.tips` table so V2 retrieval has a starting corpus. The schema has a `tip_type` column — `strategy | recovery | optimization | warning` — and a new Memory tab in the dashboard color-codes tiles by that field.

The original V2 plan defaulted every migrated lesson to `'recovery'`. That got flagged before coding started:

> Rather than defaulting every migrated lesson to 'recovery'... ask the classifier to pick one of the four on the same call. Defaulting everything to recovery makes the post-migration Memory tab lean entirely amber even though the corpus includes real strategies — the UI's color signal becomes useless until fresh V2 runs accumulate.

Reasonable. We wired a classifier.

## What got built

`gemma_forge/memory/classifier.py` — a separate LLM pass over lesson text that asked Gemma 4 to pick one of the four labels. First prompt was strict: classify by the opening framing of the text. Result: **2,349 recovery / 4 warning** out of 2,353 — because the V1 Reflector only fires on failure, so every lesson opens with *"Attempt N failed because..."*. That would have put the Memory tab 99% amber.

So the prompt got rewritten. Now: *"Focus on the PRESCRIPTIVE CONTENT — what the lesson tells the next attempt to DO — not on whether the lesson's opening sentence mentions a failure."* The new prompt even included an example: *"Attempt 2 failed... must run `augenrules --load` and verify with auditctl -l — Prescription: run augenrules + verify. STRATEGY, not recovery."*

Result: **1,806 strategy / 506 recovery / 41 warning / 0 optimization**. 77% of historical lessons re-labeled as strategy.

The backfill ran. A commit was staged.

## The honesty check

The question that stopped the commit was one sentence:

> are we doing what the system will do naturally from this point on? I don't want to "cheat" by optimizing something that wouldn't have been done by the harness itself.

That question forced a hard look at what the classifier had actually produced. The answer was uncomfortable:

**The V1 corpus is framed as recovery because the Reflector only ran on failures. That's not a bug in the data, that's the ground truth about what V1 produced.** The second prompt wasn't classifying the corpus — it was telling a model to *look past the framing* to find a more flattering label. Same data, same text, different prompt, 77-point swing in the strategy count. That's not classification, that's wish-casting.

And the kicker: in F-next the Reflector will emit `tip_type` inline as it writes each tip, seeing the full attempt trace — the failure, the approach, the evaluation. That's a fundamentally different decision from post-hoc labeling of a compressed lesson string. If we'd backfilled with the generous prompt and then Run 5's Reflector produced tips with naturally honest labels, the Memory tab would be bright green from the backfill and then suddenly amber for new tips. The UI wouldn't show "V2 is working" — it would show "the backfill lied and then the runtime told the truth."

The generous classifier wasn't making the color signal better. It was destroying the color signal and replacing it with something that looked like a color signal.

## What we did instead

Deleted `classifier.py`. The backfill now runs one path only: every historical lesson maps to `tip_type = 'recovery'`, period. The memory of a failure-derived corpus accurately reflects V1's operating reality — the Reflector only learned from failures.

When F-next ships and the V2 Reflector starts producing tips inline, its tip_type emissions are the natural ground truth. If Run 5 produces a strategy tip, the Memory tab lights up green. That green tile means *"the Reflector saw a fresh attempt and decided the prescription was strong enough to call it strategy"*. Not *"we massaged a prompt until the label we wanted fell out"*.

The Memory tab sits amber for Run 5's early iterations. That's fine. The whole project is about honest observability of how learning accumulates. A flat-amber tab that gradually acquires green and blue tiles as V2 runs accumulate is more informative than a pre-colored tab.

## The meta-lesson

There's an asymmetry with Gemma 4 that quietly tempts you to cheat. The model will confidently produce *whatever label you prime it for*. You can make the corpus look like 99% recovery or 77% strategy with the same text and twenty-five lines of different prompt instructions. That tempting malleability is the warning: **the more a classifier's output moves when you tweak the prompt, the less the classifier's output means.**

The correct test isn't *"does this produce the distribution I hoped for?"* — it's *"would I make the same call if I had never seen the desired outcome?"* The original strict prompt (99% recovery) would have been the answer if the labeling work had been done honestly, without the knowledge that the dashboard was about to be amber-heavy. The generous prompt existed solely because the desired answer was already known.

Once you have a prompt that contains the shape of the answer you want, you're not measuring anything anymore. You're decorating.

## What changed in the code

- `gemma_forge/memory/classifier.py` — **deleted**. No separate classifier exists anywhere in the system.
- `tools/backfill_tips_from_lessons.py` — simplified: all 2,353 migrated tips map to `tip_type = 'recovery'` without any LLM call.
- `gemma_forge/memory/tip_writer.py` — unchanged. Still accepts any of the four `tip_type` values; the Reflector in F-next will use the other three naturally.
- Docs updated to reflect that tip_type classification happens inline in the Reflector, never as a post-hoc pass.

## Related

- [`journey/28`](28-run-4-and-the-coarseness-problem.md) — the Run 4 verdict that triggered V2 in the first place.
- [`adr/0016`](../../adr/0016-graphiti-neo4j-postgres-memory-stack.md) — the memory architecture decision.
