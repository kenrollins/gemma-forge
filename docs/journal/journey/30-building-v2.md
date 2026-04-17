---
id: journey-30-building-v2
type: journey
title: "Building V2 — The Memory Rewrite"
date: 2026-04-17
tags: [V2-memory, architecture, reflexion-loop, cross-run-learning, predictions]
related:
  - journey/28-run-4-and-the-coarseness-problem
  - journey/29-the-classifier-cheat-and-the-honesty-check
  - journey/27-building-the-dream-pass
  - journey/22-context-graphs-and-the-memory-question
  - adr/0016-graphiti-neo4j-postgres-memory-stack
one_line: "Run 4 didn't just underperform — it proved the V1 memory ranking was arithmetically unable to separate same-rule evidence from category noise. V2 is a seven-commit, one-day rewrite of the memory substrate: structured tips instead of free-text lessons, rule-prefix similarity instead of category-confidence ranking, per-(tip, rule) hit-tracking as ground truth. It ships running alongside V1 so Run 5 carries both rankings in every Worker prompt and we can diff them per attempt."
---

# Building V2 — The Memory Rewrite

## The story in one sentence

V1's memory architecture hit its ceiling not because the dream pass did anything wrong but because a scalar confidence multiplier on lessons-grouped-by-category can't tell the difference between a lesson that demonstrably helped on a rule and a lesson that sat next to it in a category folder. V2 replaces the folder with a causal graph.

## Why this is its own entry

Entry 28 is the diagnosis. Entry 29 is the methodology slice about almost cheating on the backfill. This entry is the actual V2 — what it *is* architecturally, why each piece landed where it did, and what Run 5 is about to test.

It also belongs in the journal before Run 5 completes because the thesis of this whole record is that predictions land before outcomes. I'll put my bets down here so I can be accountable to them in entry 31.

## The compression

When Run 4 finished at 56.2% — 3.4 percentage points *below* Run 3 — the obvious move was to patch V1's three sub-mechanisms: fix the NULL-vs-penalty ranking interaction, exclude same-run lessons, add a score floor. Three patches, one weekend, probably squeeze another 2-3 percentage points out of V1.

That felt wrong almost immediately. The four diagnostics from the V2 plan §7 (wins decomposition, rule-similarity hypothesis, final aggregate, future-skill walk-through) ran in an afternoon against Run 4's data and each one pointed the same direction:

- **Diagnostic 1** (wins): 3 of 8 Run 4 wins were clearly explained by retrieved lessons; 2 by within-run accumulation; 2 accidental; 1 mixed. V1 had *weak* signal, not strong signal — and the same retrieval mechanism that produced wins by accident in some categories produced structural regressions (dac_modification) in others.
- **Diagnostic 2** (similarity): across the 15-rule dac_modification family, 65 saved lessons converged on 5–7 universally shared insights. Family-level knowledge is *real*. V1 just couldn't see it because it retrieved by category.
- **Diagnostic 3** (aggregate): 56.2% < 59.5%. V1 produced negative aggregate gain.
- **Diagnostic 4** (skill-agnostic): the `OutcomeSignal` abstraction holds for graded test suites and judgment-based signals without harness changes.

Patching V1 would have moved the fix rate a few percentage points. It would not have changed the fact that category-level credit is the wrong granularity. Better to rewrite.

## The reading week

I spent a day reading agent-memory papers before committing. The headline: **our exact failure mode is named and partially solved in the literature**.

- Xu et al. (arxiv [2505.16067](https://arxiv.org/abs/2505.16067), May 2025) named "experience-following" and "error propagation" — which is what Run 4 did. Their fix is history-based deletion gated on per-record utility.
- Trajectory-Informed (arxiv [2603.10600](https://arxiv.org/html/2603.10600), March 2026) proposes structured tips with trigger conditions and application context, ranked by similarity over task descriptions.
- Voyager (arxiv [2305.16291](https://arxiv.org/abs/2305.16291)) showed that skill libraries indexed by natural-language similarity work without ground-truth correctness signals — behavioral signals are sufficient.

What's novel about our situation is narrower than I first thought, but still real: **the STIG scanner gives us a deterministic per-rule pass/fail**. Most cited work has to infer outcomes from LLM judges or conversational patterns. We can close the per-(tip, rule) hit-tracking loop with zero inference.

The right move was: lift the mechanisms, exploit the ground-truth advantage.

## Five refinements that moved, five that didn't

The diagnostic run against the plan produced ten architectural claims. Half of them survived unchanged, half got sharpened.

**Moved:**

1. **Structured similarity (lexical prefix + category + hit-rate) over embeddings as the primary predicate.** Diagnostic 1 showed V1's category retrieval surfaced chronyd lessons for a NetworkManager rule and RPM-corruption lessons for an sshd banner rule. Pure cosine similarity over rule_ids would reproduce that class of false positive. STIG rule IDs are highly structured (`content_rule_audit_rules_dac_modification_fchmod`); their lexical prefix carries the rule-family relationship directly. Embeddings become a secondary fallback signal, not the primary.
2. **NULL-outcome tips: weighted source_prior term, not a hard retrieval gate.** The plan's original refinement proposed gating tips with zero outcome history from retrieval. That creates chicken-and-egg: tips need outcomes to be retrievable, but outcomes only accrue through retrieval. The fix was a +0.15 weighted contribution from `outcome_at_source_value × outcome_at_source_confidence` — success-derived tips rank higher than failure-derived ones without the paradox.
3. **Same-run damping (×0.5), not hard exclusion.** Diagnostic 1 showed 2 of 8 Run 4 wins relied on within-run lesson accumulation. Hard-excluding same-run tips would have killed that signal. Damping them lets within-run evidence contribute without dominating.
4. **Per-prompt lesson ID logging shipped alone first, before the retrieval rewrite.** Diagnostic 1 had to *reconstruct* what would have been retrieved at each win's firing time. Adding lesson IDs to the existing `prompt_assembled` JSONL event was a one-day instrumentation change. It closed the auditability gap immediately, before any behavior shifted.
5. **Reflector fires on first-try successes too.** V1 only ran the Reflector on failure. Three of four remediated dac_modification rules in Run 3 saved zero lessons because their first attempt passed. The "what worked" signal was being thrown away every time the Worker got it right on the first try. V2 adds a cheap success-mode Reflector call that emits only `TIPS_JSON` — no failure-mode fields, no re-analysis.

**Didn't move:**

- The tip schema (text, tip_type, trigger_conditions, application_context, embedding, outcome_at_source, bi-temporal retired_at)
- The OutcomeSignal + EvaluatorMetadata interfaces for skill-agnostic graded outcomes
- Bi-temporal supersession (retire, don't delete)
- Postgres + Neo4j split (Postgres for the data, Neo4j for the graph queries Phase H+ will want)
- History-based deletion (Xu et al.'s mechanism, with threshold parameterized by skill metadata)

## The implementation arc — one day, seven commits

With the plan validated and refined, the code went in a sequence. Each commit is independently revertable; V1 runs in parallel throughout.

| Commit | Phase | What |
|---|---|---|
| `0ebe759` | E | OutcomeSignal + EvaluatorMetadata + tips schema + Neo4j indexes + per-prompt lesson ID logging |
| `324d8b7` | F-now | TipWriter + backfill of 2,353 V1 lessons (all tip_type=recovery, honest default) |
| `3f88613` | F-next | Reflector emits TIPS_JSON alongside free text; success-mode Reflector; tip_added events |
| `da65266` | G | Structured-similarity retrieval; per-(tip, rule) hit tracking; prompts carry V1+V2 side-by-side |
| `a9b4e4d` | G' | source-attempt prior in composite score (refinement 2 refinement) |
| `1df4ee4` | H | History-based eviction + enriched tip events + tip_retired catch-up |

The whole thing is additive. V1's `load_lessons(category)` still runs and the V1 `category_lessons` section still appears in the Worker prompt. V2's `assemble_tips_for_rule(rule_id, category)` runs alongside and its output joins the prompt as a `similar_rule_tips` section. Every `prompt_assembled` event logs both snapshots so post-hoc analysis can diff them per attempt. V1 goes away after Run 5 validates V2's aggregate gain; until then, both rankings inform the Worker and the JSONL is the ground truth for which retrieval surfaced which tips when.

212 tests passing across the seven commits. A mid-afternoon smoke run against three rules cleared the largest behavioral risk — that forcing the Reflector into structured JSON output would shallow out its analytical reasoning. It didn't: both failure-mode and success-mode produced parseable TIPS_JSON every call, and one failure-mode Reflector emitted *two* tips in a single pass.

## What Run 5 tests

Four questions I want Run 5 to answer. Each is falsifiable.

1. **Does the dac_modification regression class disappear?** V2's ranking makes same-rule pre-existing tips score ~1.30 while within-run sibling-subfamily tips score ~0.30-0.65 (after same-run damping). The specific arithmetic that produced Run 4's regressions cannot recur. If dac_modification *still* regresses, I missed something.

2. **Do `v2_tips_loaded` events show same-rule tips dominating the top-5, where V1 showed cross-subfamily noise?** Every Worker `prompt_assembled` event logs both. A per-attempt diff is one SQL join. If V2's top-5 mostly duplicates V1's — or worse, produces worse tips — the structural premise is broken.

3. **Does the aggregate fix rate move?** Run 3: 59.5%. Run 4: 56.2%. Run 5 target: a clear win, clear draw, or clear regression.

4. **Does the Reflector emit the tip_type distribution the smoke hinted at?** 7 strategy / 1 warning / 0 recovery in 8 smoke-run samples. If that holds, it retroactively validates entry 29's decision to decline the backfill classifier — the *live* Reflector's framing is prescriptive when it sees the full attempt trace, not recovery-framed the way compressed lesson text looked.

## Why Run 6 is the one I actually care about

Here's what I haven't told the record yet: Run 5 populates `tip_retrievals` outcomes for the first time, but V2's *learned utility* component (`hit_rate × 0.5` in the composite) is cold-start on Run 5. Most tips have zero recorded outcomes. Run 5 runs mostly on base similarity + category + source_prior — the structural signal. Hit-rate becomes meaningful only after tips have been retrieved multiple times with outcomes recorded.

**Run 5 is the data-accrual run. Run 6 is the thesis test.**

I'm still making predictions for Run 5, but the architecture's full claim — that evidence-driven per-(tip, rule) tracking beats evidence-free ranking — only has enough data behind it to evaluate in Run 6 onward. Entry 31 will grade Run 5; whenever Run 6 happens, it's the one that tells us whether V2 is actually a better memory architecture or just a differently-shaped one.

## The bets — scored later

Predictions before the run, calibrated honestly. Entry 31 grades them.

| # | Prediction | Why | Confidence |
|---|---|---|---|
| 1 | dac_modification regressions are gone | Arithmetic: same-rule prior-run tips (1.30) dominate sibling within-run tips (0.30-0.65) regardless of hit history | High |
| 2 | Aggregate fix rate lands 58–62% | V2 structural fix recovers Run 4's ~3.4 pp drop; hit-rate signal too cold to get a breakout; call median 60% | Medium |
| 3 | First-try success rate: 48–50% (flat to slightly down) | Longer prompts (V1+V2 carried side-by-side) can distract; but same-rule tips may help first-try on backfilled rules. Coin flip. | Low |
| 4 | Per-escalated attempts: down (continuing Run 4's −8% trend) | V2 routes away from dead ends faster; best narrow signal from Run 4 survives into Run 5 | Medium |
| 5 | Run wall time: up 1–2 hours vs Run 4's 15.9h | Success-mode Reflector fires ~130 extra times on easy front; longer Worker prompts | Medium |
| 6 | `tip_added` distribution: heavily strategy-weighted (>60% strategy across all emissions) | Smoke showed 7/1/0 on 8 samples; if it holds at scale, live Reflector's framing differs from compressed lesson-text framing | Medium |
| 7 | Run 5 will surface a failure mode I haven't thought of | Runs 1-4 each did; no reason 5 will be different. I cannot describe this one without betraying how I'd plan against it. | High |

**The one I want to be wrong about:** #2. A median prediction of 60% means "V2 fixed the regression but didn't unlock new signal yet." If Run 5 actually lands at 65%+, it means hit-rate is accruing fast enough to matter even in a cold-start setting, and Run 6 becomes uninteresting because V2 already worked. I would love to be told my arithmetic on cold-start was overcautious.

**The one I'm most worried about:** #7. Every prior run taught me something I had no way to predict. Run 5 will too. What I've guarded against — V1+V2 prompt interference, rare Reflector JSON malformation, success-mode quality drift — are the things I *could* imagine. The bite usually comes from something else.

## Related

- [`journey/28`](28-run-4-and-the-coarseness-problem.md) — the diagnosis that set V2 in motion.
- [`journey/29`](29-the-classifier-cheat-and-the-honesty-check.md) — the methodology near-miss from Phase F.
- [`journey/27`](27-building-the-dream-pass.md) — the V1 dream pass that Run 4 tested.
- [`adr/0016`](../../adr/0016-graphiti-neo4j-postgres-memory-stack.md) — the memory storage substrate that V2 extends.
