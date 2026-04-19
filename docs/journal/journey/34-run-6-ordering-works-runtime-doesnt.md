---
id: journey-34-run-6-ordering-works-runtime-doesnt
type: journey
title: "Run 6: The Ordering Constraint Works, The Runtime Doesn't"
date: 2026-04-19
tags: [L4-orchestration, reflexion-loop, postmortem, runtime, ordering]
related:
  - journey/33-second-skill-cve-pivot
  - journey/32-three-tips-a-dead-clutch-and-a-registry
  - journey/31-run-5-grading-the-bets
one_line: "Fix rate 61.9% — up 5.6 points from Run 5. The skill-declared ordering constraint completely closed the audit_rules_immutable cascade: the rule that was scheduled at position 11 of 83 in Run 5 was scheduled at position 84 of 84 in Run 6, and zero audit rules ran with a locked kernel. The mechanism-field requirement held at 100% parser acceptance across 781 tip emissions, flipping the tip-type distribution to 93% strategy from Run 5's 82%. Auto-consolidation fired at run-end, retiring 356 low-utility tips. The architectural claims landed. The cost: +4.8 hours of wall time, from 14.3h to 19.1h, with throughput collapsing to near-zero after hour 10. Runtime is now the binding constraint on iteration speed, and the fix isn't in the memory layer — it's in how the loop spends its time on rules that are never going to resolve."
---

# Run 6: The Ordering Constraint Works, The Runtime Doesn't

Fix rate 61.9% against Run 5's 56.3% — a 5.6-point lift, more than enough to call the architectural shipping from entry 32 a success. The audit_rules_immutable cascade that dragged Run 5 down is gone. `audit_rules_immutable` was picked at position 84 of 84 audit rules this time, at t=19.07h, with zero audit rules scheduled after it. In Run 5 it was picked at position 11 of 83 at t=8.62h; 72 audit rules ran with the kernel locked. The skill-declared `category_nearly_complete` constraint did exactly what entry 32 predicted.

What happened after that is where Run 6 started to tell a different story.

## The scorecard

| Metric | Run 5 | Run 6 | Δ |
|---|---|---|---|
| Total rules | 254 | 247 | — |
| Remediated | 143 | 153 | **+10** |
| Escalated | 111 | 94 | **−17** |
| Skipped | 15 | 23 | +8 |
| **Fix rate** | **56.3%** | **61.9%** | **+5.6 pp** |
| First-try of wins | 87% | 86% | flat |
| `rules_deferred` events | — | 269 | (new mechanism) |
| Mechanism-field acceptance | n/a | **100%** (781/781) | — |
| Scanner gaps | 205 | 391 | +186 |
| PIVOT reengagements | 120 | 327 | **+207** |
| ESCALATE reengagements | 110 | 93 | −17 |
| Tips emitted | 612 | 781 | +169 |
| Auto-consolidation | — | dream ✓ + 356 evicted | (new) |
| **Wall time** | **14.3h** | **19.1h** | **+4.8h** |

4 of 4 shipped changes landed clean. The last row is the problem.

## The cascade is closed

Run 5's audit category was a graveyard: `audit_rules_immutable` locked the kernel at 8.6h and 58 of the remaining 83 audit rules failed through no fault of their own. Run 6's ordering constraint deferred immutable until the audit category had ≤1 rule remaining, meaning *immutable itself was that last rule*. It ran at 19.07h against a fully-processed audit pool.

!!! quote ""
    Run 5 had 72 audit rules scheduled after immutable. Run 6 had zero. The cascade isn't reduced — it's gone.

The payoff is visible in the dac_modification family specifically. In Run 5, 12 of 14 rules cascade-failed at 2 attempts each (scanner_gap_detected with a locked kernel). In Run 6, those same 14 rules got their real shot:

- `audit_rules_dac_modification_fchmodat`: R5 cascade-escalated at 2 → R6 first-try win
- `audit_rules_dac_modification_umount2`: R5 cascade-escalated at 2 → R6 first-try win
- `audit_rules_file_deletion_events_rename`: R5 cascade-escalated at 2 → R6 first-try win
- `audit_rules_file_deletion_events_renameat`: R5 cascade-escalated at 2 → R6 first-try win
- `audit_rules_dac_modification_fchmod`: R5 cascade-escalated at 2 → R6 won at attempt 7 (real grinding)

And the ones that stayed hard:

- `audit_rules_dac_modification_fchown`: R5 first-try → **R6 escalated at attempt 5**
- `audit_rules_dac_modification_fchownat`: R5 first-try → **R6 escalated at attempt 5**

That pair is a real regression — two rules Run 5 won first-try, Run 6 couldn't close in five attempts. The ordering constraint isn't blame here (immutable didn't run until t=19h). Something else is making these rules hard. A question for Run 7's prompt work.

Cross-run aggregate: 17 progressions vs 8 regressions → **+9 rules net.** Audit category specifically: 7 progressions, 3 regressions → net +4.

## The mechanism field did what we wanted

781 tips emitted across the run. **100% had the mechanism field populated** — zero parser drops. The Reflector learned the requirement by attempt 1 and held it through 20 hours of inference.

Tip-type distribution flipped exactly as entry 32 predicted:

| Type | Run 5 | Run 6 |
|---|---|---|
| strategy | 504 (82%) | 729 (93%) |
| warning | 106 (17%) | 34 (4%) |
| recovery | 0 | 18 (2%) |
| optimization | 2 | 0 |

The mechanism-field requirement filtered out the "X failed" no-mechanism noise that Run 5's recovery tips suffered from. What remains is mostly positive-outcome strategy tips (first-try wins → success-mode Reflector), with a small share of warnings and mechanism-bearing recovery tips. The tip corpus coming out of Run 6 is higher signal per emission than Run 5's was.

One unforced data point: the live smoke of the CVE skill (entry 35, not yet written) emitted one success-mode tip on first try, with a valid mechanism. The field's generalizes outside STIG's linguistic register on first exposure.

## Auto-consolidation works, and it's closing the loop

The `consolidation_complete` event fired cleanly at run-end:

- **Dream pass**: 14 categories analyzed, 3,472 lessons updated, 8 positive-credit / 5 negative / 1 neutral. Environment-tagged `baseline-20260419`.
- **Eviction**: 3,249 active tips at start, 487 had ≥3 outcomes, **356 retired** (74% of eligible). Active count down to 2,893.

!!! quote ""
    Run 5 ended with 2,973 active tips and zero eviction ever run. Run 6 ended with 2,893 active tips and 356 newly retired. The consolidation loop is closing itself now.

Five `tip_retired` events surfaced within Run 6 itself from a prior manual sweep, which is the dashboard's "here's what was retired since last run" signal. That's the auto-consolidation feeding the telemetry layer.

## The runtime problem confirmed

Wall time went from 14.3h (Run 5) to 19.1h (Run 6). **+4.8 hours for +10 remediated rules.** Per-hour rule throughput:

- Run 5: 17.8 rules/h · 10.0 remediated/h
- Run 6: 12.9 rules/h · 8.0 remediated/h

Throughput dropped 28%. Let the hourly curve tell the rest:

| Hours | Remediations |
|---|---|
| 0–4 | 94 (avg 18.8/h) |
| 5–9 | 40 (avg 8.0/h) |
| 10–14 | 9 (avg 1.8/h) |
| 15–19 | 10 (avg 2.0/h) |

The first five hours produced 61% of all wins. **Hours 10–19 — nearly half the runtime — produced 12% of wins.** Marginal returns collapse after the easy categories are done, and the loop keeps spending 20-minute wall budgets on rules that are never going to remediate.

Contributing factors:

- **Architect reengagement up 173%** (120 PIVOT → 327 PIVOT). More active architect is good for quality, costly for wall time.
- **Scanner gaps up 91%** (205 → 391). More often the loop hits "many approaches failing with healthy target," which triggers reengagement, which runs the Architect again.
- **Hard audit tail** (enabled by ordering constraint — the last half of audit category runs serially now rather than cascade-failing fast).

The honest read: the ordering constraint traded "fast cascade failure" for "slow real grinding." The fix rate gain is real, but it cost hours of wall time on rules that still failed. Not every audit rule is cascade-doomed; some are just hard. The loop doesn't distinguish.

## What Run 6 tells us about what to fix next

Three things surfaced:

1. **Per-rule budget should adapt to historical outcome.** A rule that has escalated N times in prior runs probably doesn't deserve a fresh 20-minute budget. `unsuccessful_file_modification_*` is 0/6 across Run 5 and Run 6 — it should get a 2-minute budget (or an explicit skip) on Run 7. Harness-level policy with skill-declared category hints. This is Track A from entry 33's runtime discussion, but refined: **the budget mechanism is skill-agnostic, the category-specific numbers are skill-declared.**

2. **Tip decay on failing rules.** Run 5's post-mortem already identified this: if a tip has been retrieved N times with zero outcome, drop it. Wasn't implemented for Run 6 because it's skill-agnostic architecture work that benefits more from two-skill evidence. Run 7 is the right time.

3. **Scanner-gap threshold may be tuned too tight.** 391 gap events in Run 6 is almost double Run 5. If the threshold is triggering too eagerly, the Architect is getting re-engaged on rules that might have resolved in another attempt or two. Worth an ablation: what if `scanner_gap_threshold` goes from 3 distinct approaches to 4? How does that shift the PIVOT rate and the wall time?

None of these are CVE-blockers. The CVE MVP landed clean today; its runtime behavior won't be known until a full CVE run produces similar hourly-throughput data. The discipline from entry 33 still holds: don't design Track A against one-skill evidence.

## Entry 33 claims, now scored

The four claims entry 33 put on the record about what a second skill would test:

| # | Claim | Status |
|---|---|---|
| 1 | Harness generalizes — zero edits to `gemma_forge/harness/*.py` to add CVE | **Held.** Added one ordering predicate (wildcard dispatch), one FailureMode enum value, and a skill-dir map entry. All mechanism work, no CVE-specific logic in the loop. |
| 2 | Ordering-constraint mechanism extends cleanly | **Held.** `deferrable_reboot` is a sibling predicate under the existing `defer_until` schema. Wildcard `rule_id: "*"` was added to the filter dispatch, not a schema change. |
| 3 | Tip quality generalizes | **Preliminary held.** 1/1 CVE tips emitted had valid mechanism (smoke run). Full run will tell. |
| 4 | Autonomous reboot-verify loop is novel | **Held.** Deferrable-reboot predicate + CveEvaluator's NEEDS_REBOOT verdict route the pattern. Verified in runtime wiring; will exercise on a full CVE run with kernel RLSAs in the pool. |

The bet I most wanted to win — fix rate lift — landed. Not the 65% unlock some part of me was hoping for; not the neutral result I feared. Six points up, with a provable architectural cause.

## The narrower post-mortem question

Ken asked if Run 6's post-mortem would surface anything that should change the CVE skill direction before we push through. The honest answer is **no**. Every architectural claim Run 6 tested landed. The CVE skill inherits those mechanisms and the same plumbing that worked in Run 6 worked in the CVE MVP smoke three hours ago. The runtime concern is real but orthogonal — it applies to both skills and the fix is the same skill-agnostic Track A work either way.

What Run 6 gave CVE is the confidence that mechanism-field, ordering-constraints, and auto-consolidation are stable primitives. Building the CVE skill on those foundations was the right call; the post-mortem validates it retrospectively.

## Related

- [`journey/33`](33-second-skill-cve-pivot.md) — the pivot that happened
  alongside this run. Claims made there, scored here.
- [`journey/32`](32-three-tips-a-dead-clutch-and-a-registry.md) — where the
  ordering constraint, mechanism field, and auto-consolidation shipped.
- [`journey/31`](31-run-5-grading-the-bets.md) — Run 5's post-mortem;
  the baseline against which Run 6 is measured.
- [`docs/research/cve-agent-landscape-2026-04.md`](../../research/cve-agent-landscape-2026-04.md)
  — landscape research captured today. Nothing in it changes based on Run 6.
- [`deferred.md`](../../deferred.md) — Track A tunings for runtime
  (per-rule budget adaptation, tip decay, scanner-gap threshold)
  are the natural follow-on. Not landed as a DEF yet because
  their design needs the CVE-run data before it's meaningful.
