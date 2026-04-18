---
id: journey-31-run-5-grading-the-bets
type: journey
title: "Run 5: Grading the Bets"
date: 2026-04-18
tags: [L4-orchestration, reflexion-loop, postmortem, predictions]
related:
  - journey/30-building-v2
  - journey/28-run-4-and-the-coarseness-problem
  - journey/25-run-3-learning-plateaus
one_line: "Four bets won, three lost. The one I most wanted to be wrong about — that V2 would lift aggregate fix rate into the 58–62% band — I lost by two points. The one I was most confident in — that same-rule sim=1.30 tips would end the dac_modification regressions — I also lost, because no amount of arithmetic unlocks a kernel. The bets I won were the narrower ones about loop shape. V2 is neutral on aggregate, better on shape, and exposed a failure mode I couldn't have predicted: the Architect scheduled audit_rules_immutable at position 11 instead of position 50, and the cascade closed the audit category from there."
---

# Run 5: Grading the Bets

Four right, three wrong. 143 rules remediated out of 254 — 56.3% fix rate against a predicted 58–62%. The one I was most confident in, that V2 would end the `dac_modification` regressions, also lost: 12 of 14 still escalated. The bets I won were the uninteresting ones about loop shape. V2 is neutral on aggregate, better on shape, and exposed a new failure mode I couldn't have seen ahead of time.

## The scorecard

Entry 30 put seven bets on the record before Run 5 started. Here they are with the data.

| # | Prediction | Actual | Grade |
|---|---|---|---|
| 1 | dac_modification regressions gone | 12 of 14 still escalated | **Lost** |
| 2 | Aggregate fix rate 58–62% | 56.3% | **Lost** |
| 3 | First-try rate 48–50% | 49% | **Won** |
| 4 | Per-escalated attempts down from Run 4's 4.73 | 3.06 (−35%) | **Won** |
| 5 | Wall time up 1–2 hours vs Run 4's 16.2h | 14.3h (down 1.9h) | **Lost** |
| 6 | `tip_added` >60% strategy-labeled | 82% strategy | **Won** |
| 7 | Run 5 will surface a failure mode I haven't thought of | Immutable cascade at position 11/83 | **Won** |

The bet I was most confident in (#1) and the bet I most wanted to be wrong about (#2) both lost. The bets I won were the ones about shape, not value: the loop moved faster and quit earlier, but the aggregate number didn't move.

## The one I wanted to be wrong about

Bet #2 was a range prediction of 58–62% with a median call of 60%. If Run 5 had landed at 65%+, that would have meant V2's hit-rate signal accrued fast enough to matter even cold-started, and Run 6 would become uninteresting. I hoped to be told my arithmetic on cold-start was overcautious.

It wasn't overcautious. It was wrong in the other direction — V2 didn't recover the Run 3→4 regression at all. 56.3% against Run 4's 56.18% is arithmetic noise. Against Run 3's 59.5% it's still 3.2 points below.

The reason isn't cold-start. It's the immutable cascade (Bet #7 — the one I couldn't see coming). The cascade costs ~18 rules of forced escalation. If I remove those from the denominator as "structural loss, not knowledge loss," Run 5 would have landed closer to 62%. That's the right aggregate to hit eventually, but a fix rate you only get by excusing the cascade is not a fix rate you get to claim.

## The one I was most confident in

Bet #1 was grounded in specific arithmetic: same-rule recovery tips carry a composite score of 1.30 (1.00 base + 0.30 category). Sibling-rule tips top out at 0.65 even with perfect hit history. That 2× dominance means the Architect sees its own past failures on the exact rule before it sees anything generic, and the `dac_modification` family's prior-run tips should have been sufficient to close the regression.

The arithmetic was right. The tips surfaced exactly where they should have — all 14 `dac_modification` rules retrieved their own prior-run recovery tips at sim=1.30 on attempt 1. It didn't matter. Twelve of them still escalated.

The reason isn't retrieval. The `audit_rules_immutable` rule was picked as the 11th audit rule of the run, at elapsed time 8.62 hours. That call sets the kernel's audit subsystem to immutable mode, which means every subsequent `audit_rules_*` change fails at `auditctl -e 1` until a reboot. After position 11, 72 audit rules still needed to run. No tip retrieval unlocks a kernel that has been deliberately locked.

!!! quote ""
    The arithmetic was right. The kernel was locked. No amount of arithmetic unlocks a kernel.

In Run 4, the Architect happened to pick `audit_rules_immutable` at position 50 of 86 — late enough that most of the `dac_modification` family had already run. In Run 5, the Architect picked it at position 11. Same prompt. Same Architect model. The `"IMPORTANT: Process audit_rules_immutable LAST within audit rules."` line in the prompt was present in both runs. The Architect ignored it in Run 4 and ignored it worse in Run 5.

## The one I couldn't see coming

Bet #7 was a hedge: "Runs 1–4 each taught me something I had no way to predict. Run 5 will too." The bet was meta — I was betting on my own blind spot being real without being able to describe it. That makes grading it a little cheap, but the shape of the surprise matters: the cascade itself isn't new (Run 4 had it), but the *position* is. An Architect that picks rules from a list can produce wildly different run-level outcomes based on an ordering decision that isn't in any way load-bearing in the design.

The design fix is skill-declared ordering constraints — entry 32 will cover the build. But the *recognition* is: prompt-level guidance is not enforcement. Entry 25 flagged this in a different instance; it's a generalizable pattern. Any future skill with rule-ordering requirements hits the same wall, so the mechanism needs to be harness-level and skill-declared from the start.

## The bets I won

Three out of seven landed — all about loop shape.

**First-try rate at 49%** (Bet #3) was a coin-flip prediction with a narrow band. V1+V2 carried side-by-side in prompts could have distracted the Worker into worse first-try performance, or same-rule tips could have lifted it. The data says neither: the aggregate first-try rate is flat to Run 4's 51%, with a lot of churn underneath (10 rules gained first-try status, 12 lost it — see entry 32 for the traces).

**Per-escalated attempts dropped 35%**, from Run 4's 4.73 to Run 5's 3.06 (Bet #4). The `scanner_gap_detected` trigger and the Architect re-engagement cycle cut off dead-end grinding earlier. This is the best narrow signal in Run 5. A rule that's going to escalate, escalates faster — the loop doesn't waste 90 seconds × 4 attempts on something the Reflector knows is structurally broken by attempt 2.

**Strategy-labeled tips at 82%** (Bet #6). Success-mode Reflector fires on first-try wins, and first-try wins outnumber failures, so strategy tips dominate new emissions. Predicted >60%. Comfortably above.

## What the grading tells us

V2 is better at *shape* and neutral on *value*. The loop quits zombie attempts faster, generates tips at the predicted mix, and keeps first-try performance steady. It did not, however, produce an aggregate fix-rate lift, and the cases where it should have helped most — the regressed `dac_modification` family — aren't blocked by knowledge.

That's an uncomfortable result to hold. The V2 plumbing works: 612 `tip_added` events, 1270 retrievals with outcomes populated, hit-rate signal accruing for Run 6 to use. The mechanism is sound. But "mechanism works" ≠ "aggregate metric moves," and a memory architecture that doesn't move the aggregate metric is either measuring the wrong metric or fighting the wrong problem.

Three possibilities, in order of how much evidence I have for each:
1. **Wrong problem**: The cascade costs 18 rules outright. No memory system closes those. They need a harness-level ordering fix. (Strongest evidence; Run 6 tests this.)
2. **Right problem, wrong priors**: Recovery tips are noisy (entry 32 traces three cases). If a tip is "attempt X failed" without "because Y," the Worker dutifully avoids X — including the cases where X would have worked. Eviction should filter these out; it hasn't because eviction has never run. (Medium evidence; Run 6's pre-run consolidation tests this.)
3. **Wrong architecture**: V2's prefix-similarity + hit-rate composite is too crude. A-MEM-style semantic linking and AgeMem-style learned policy would do better. (Weak evidence — deferred to V3 in [`deferred.md`](../../deferred.md) DEF-06 and DEF-07.)

Run 6 tests (1) directly via the ordering constraint and (2) indirectly via eviction-before-launch. Entry 32 covers the design.

## The honest read

I took seven bets on a run that was supposed to validate V2's aggregate claim. I won the ones about the loop's mechanics and lost the ones about its outcome. The plumbing is ready and the data is in. What V2 actually *buys* is still an open question. Entry 32 describes the three changes shipping before Run 6 attempts to answer it.

## Related

- [`journey/30`](30-building-v2.md) — the bets, before the run.
- [`journey/28`](28-run-4-and-the-coarseness-problem.md) — the Run 4
  post-mortem that V2 was supposed to close.
- [`journey/25`](25-run-3-learning-plateaus.md) — where "prompt guidance
  is not enforcement" first got flagged.
- [`deferred.md`](../../deferred.md) — the registry where DEF-02 now
  tracks the general prompt-vs-enforcement pattern.
