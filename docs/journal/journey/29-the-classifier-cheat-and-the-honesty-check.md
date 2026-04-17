---
id: journey-29-the-classifier-cheat-and-the-honesty-check
type: journey
title: "The Classifier Cheat and the Honesty Check"
date: 2026-04-17
tags: [memory, methodology, failure-mode]
related:
  - journey/28-run-4-and-the-coarseness-problem
  - adr/0016-graphiti-neo4j-postgres-memory-stack
one_line: "We almost shipped an LLM that retroactively re-labeled every historical lesson with a prompt tuned to produce the dashboard colors we wanted. The question that stopped the commit — 'are we doing what the system will do naturally from this point on?' — had an answer that was uncomfortable but simple: no. This entry documents the near-miss, because the way we avoid cheating on our own experiments matters more than the specific choice."
---

# The Classifier Cheat and the Honesty Check

## The setup

The cross-run memory system was getting a rewrite. One change: every new piece of remembered knowledge would carry a type label — `strategy`, `recovery`, `optimization`, or `warning` — and the Memory tab in the dashboard would color-code each tile by that type. Going forward, the Reflector would pick the type as it wrote each new lesson, seeing the full attempt trace (what was tried, whether it worked, what the evaluator said).

The old cross-run memory had no such label. It just had 2,353 lessons from prior runs, all free-text, all written by the Reflector analyzing failures. Migrating those 2,353 into the new format meant answering: what type should each of them get?

The direction was: don't default everything to `recovery`. That would make the Memory tab look flat and amber on day one. Some of those old lessons *are* strategies — lessons that actually tell the Worker what to do, not just what went wrong. Ask an LLM classifier to read each one and pick the right type.

Reasonable. We wired it up.

## What got built

A small LLM pass read each historical lesson and picked one of the four labels. First prompt asked the model to classify by the *framing* of the text. Result: **2,349 recovery / 4 warning** out of 2,353.

Why so lopsided? Because every historical lesson was written by the Reflector analyzing a failure. The text literally starts with "Attempt N failed because..." So by framing, every lesson is a recovery. That would have put the Memory tab 99% amber on day one — which is what the original direction was specifically trying to avoid.

So the prompt got rewritten. The new version said: *"Focus on the PRESCRIPTIVE CONTENT — what the lesson tells the next attempt to DO — not on whether the lesson's opening sentence mentions a failure."* It even included a worked example: *"Attempt 2 failed... must run `augenrules --load` and verify with auditctl -l — Prescription: run augenrules + verify. STRATEGY, not recovery."*

Result: **1,806 strategy / 506 recovery / 41 warning / 0 optimization**. 77% of historical lessons re-labeled as strategy.

The backfill ran. A commit was staged.

## The honesty check

The question that stopped the commit was one sentence:

> are we doing what the system will do naturally from this point on? I don't want to "cheat" by optimizing something that wouldn't have been done by the harness itself.

That question forced a hard look at what the classifier had actually produced. The answer was uncomfortable:

**The V1 corpus is framed as recovery because the Reflector only ever ran on failures. That's not a bug in the data, that's the ground truth about what the old system produced.** The second prompt wasn't classifying the corpus — it was telling a model to *look past the framing* to find a more flattering label. Same data, same text, twenty-five lines of different prompt instructions, 77-point swing in the strategy count.

That is not classification. That is wish-casting.

And the kicker: going forward, the Reflector will pick each new tip's type *inline*, as it writes the tip, seeing the full attempt trace — the failure, the approach, the evaluator's verdict. That is a fundamentally different decision from post-hoc labeling of a compressed one-sentence lesson. If we had backfilled with the generous prompt and then the next run's Reflector produced tips with naturally honest labels, the Memory tab would start bright green from the backfill and then suddenly go amber as fresh tips arrived. The UI wouldn't show "the new memory system is working." It would show "the backfill lied and then the runtime told the truth."

The generous classifier wasn't making the color signal better. It was destroying the color signal and replacing it with something that looked like a color signal.

## What we did instead

Deleted the classifier. The migration now runs one path only: every historical lesson maps to `recovery`. The memory of a failure-derived corpus accurately reflects the old system's operating reality — the Reflector only learned from failures.

When the new Reflector starts producing tips inline, its type emissions are the natural ground truth. If a fresh run produces a strategy tip, the Memory tab lights up green. That green tile means *"the Reflector saw a fresh attempt and decided the prescription was strong enough to call it strategy"* — not *"we massaged a prompt until the label we wanted fell out."*

The Memory tab sits amber through the first runs on the new system. That is fine. The whole project is about honest observability of how learning accumulates. A flat-amber tab that gradually acquires green and blue tiles as new runs accumulate is more informative than a pre-colored tab.

## The meta-lesson

There is an asymmetry with Gemma 4 that quietly tempts you to cheat. The model will confidently produce *whatever label you prime it for*. You can make the same corpus look like 99% recovery or 77% strategy with the same text and different prompt instructions. That tempting malleability is the warning: **the more a classifier's output moves when you tweak the prompt, the less the classifier's output means.**

The correct test isn't *"does this produce the distribution I hoped for?"* — it's *"would I make the same call if I had never seen the desired outcome?"* The original strict prompt (99% recovery) would have been the answer if the labeling work had been done honestly, without the knowledge that the dashboard was about to be amber-heavy. The generous prompt existed solely because the desired answer was already known.

Once you have a prompt that contains the shape of the answer you want, you're not measuring anything anymore. You are decorating.

## What changed in the code

- The classifier module was deleted. No separate classifier lives anywhere in the system.
- The migration tool was simplified — all 2,353 historical lessons map to `recovery`, with no LLM call.
- The tip writer was unchanged. It still accepts any of the four type values; the Reflector will pick from the other three naturally as it writes fresh tips.
- Docs updated to say: type classification happens inline in the Reflector, never as a post-hoc pass.

## Related

- [`journey/28`](28-run-4-and-the-coarseness-problem.md) — the Run 4 verdict that triggered the memory rewrite in the first place.
- [`adr/0016`](../../adr/0016-graphiti-neo4j-postgres-memory-stack.md) — the memory architecture decision.
