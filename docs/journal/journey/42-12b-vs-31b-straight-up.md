---
id: journey-42-12b-vs-31b-straight-up
type: journey
title: "Straight-Up: the 12B Sees the Same Memory and Still Can't Crack the Hard Rule"
date: 2026-06-12
tags: [L3-model, capability, memory, measurement, gemma4-12b]
related:
  - adr/0024-gemma4-12b-data-parallel-fleet-evaluation
  - journey/09-the-nemotron-experiment
  - journey/12-bf16-tp4-full-precision
  - futures/gemma4-12b-fleet-preflight
one_line: "Ran the new Gemma 4 12B and the production 31B head-to-head on the identical 52-run production memory and the same baseline VM. Both scored 4/5 — but on the one genuinely hard rule both attempted, the 31B reasoned out the fix and the 12B, with the exact same accumulated tips, escalated. Raw model size still mattered where it counted."
---

# Straight-Up: the 12B Sees the Same Memory and Still Can't Crack the Hard Rule

Google dropped a **Gemma 4 12B Dense** on June 3. It fits on a single L4,
benchmarks neck-and-neck with the 26B, and immediately raised the question
this whole project exists to answer: if the *system around the model* —
the loop, the context graph, fifty-two runs of accumulated tips — is where
the capability lives, then can a *smaller* model riding that system match a
*bigger* bare one? Or, sharper: hand the 12B everything the 31B has
learned, and does it close the gap?

This is the pre-vacation slice of that experiment (the full protocol is in
[ADR-0024](../../adr/0024-gemma4-12b-data-parallel-fleet-evaluation.md)):
**straight-up 12B vs 31B, both bf16, both with MTP, both warm on the same
production memory, same baseline VM, same five-rule budget.** Not the full
corpus — that's grinding overnight as I write this — but a clean first cut.

## The setup, and the boring parts that mattered

A fair fight needed more plumbing than I expected:

- **Both official bf16.** No FP8 — there's no official FP8 12B checkpoint,
  and for a Federal audience "official weights" is the whole game. So the
  flashy four-instance single-GPU fleet is shelved; this is one 12B at TP=2
  on two L4s vs the 31B at TP=4 on all four.
- **Both with MTP.** The 12B ships its own official drafter
  (`google/gemma-4-12B-it-assistant`), and MTP is *lossless* — same tokens,
  just faster. So it doesn't touch the capability question, only throughput.
  Giving the 12B its MTP kept the speed comparison honest.
- **Identical memory — the part I almost got wrong.** The 31B ran first and
  *deposited* into the shared tip pool: 210 bans and a clutch of AIDE
  lessons about the exact rules the 12B was about to attempt, plus an
  auto-dream-pass that reshuffled tip weights. Left alone, the 12B would
  have inherited the 31B's fresh homework. So between conditions I rewound
  the `stig` schema surgically — scoped deletes by run-id and timestamp —
  back to the exact 52-run state, verified row-for-row against a backup.
  Both models started from the same memory, neither contaminated by the
  other.

The "Model: Gemma 4 31B" line in the harness banner is hard-coded; I
confirmed the 12B condition was genuinely hitting the 12B by checking the
`model` field on every response (`/weights/gemma-4-12B-it`). Trust, verify.

## The scoreboard says tie

| | 31B | 12B |
|---|---|---|
| Rules remediated | **4/5** | **4/5** |
| Total attempts | 15 | **23** |
| Agent calls | 44 | 64 |
| Rule wall-clock | 11.9 min | 13.6 min |
| Completion tokens | 14,081 | 15,678 |
| Decode speed | 25.9 tok/s | **28.7 tok/s** |
| MTP tokens/step | 2.43 | **3.01** |
| MTP acceptance | **0.72** | 0.50 |

Four out of five each. If I'd stopped at the headline count I'd have
written "the 12B keeps up — the system carries it." That would have been
wrong.

## The scoreboard is lying

They didn't fix the same five rules. Both opened on the same pick —
`package_aide_installed`, which is nastier than it sounds: the baseline VM
has a clock skew that breaks `dnf`'s SSL certificate validation, so a naive
`dnf install aide` just fails with a cert error. You have to *reason* your
way out of it.

- **31B: remediated in 5 attempts.** It worked out the
  `--setopt=sslverify=false` bypass.
- **12B: escalated after 11 attempts and 414 seconds.** Same problem, same
  52 runs of tips and bans available, and it could not get there.

And here's the tell: after the 12B gave up on that rule, its Architect
*pivoted to easy wins* — `nss-tools`, `rsyslog-gnutls`, one attempt and
~25 seconds each. It filled its 4/5 quota by routing around difficulty. The
31B, meanwhile, stayed in the harder AIDE-config family and still went 4/5.
Same count, completely different mountain.

The 12B also simply *worked harder* for its tie: 53% more attempts, 45%
more agent calls, more tokens, longer wall-clock — and that's **despite**
faster raw decoding. MTP genuinely helped it (3.0 tokens/step vs the 31B's
2.4), but at lower draft acceptance on this gnarly STIG/bash workload
(0.50 vs 0.72), and all that speed bought was the privilege of grinding the
same rule eleven times before quitting.

## What it means (and what it doesn't)

The thesis got a real, if early, answer: **the system did not substitute
for model size on the rule that actually required reasoning.** Both models
stood on the identical mature memory; the smaller one still couldn't crack
the environmental gotcha the larger one reasoned through. This is
"non-determinism has a cost," with a number attached — exactly the kind of
result this project would rather measure than assume.

The honest caveats, loudly:

- **N=5, one run each.** This is anecdote-strength. No variance estimate.
- **Rule selection diverged**, so it's only a true head-to-head on the one
  shared hard rule — which happens to be the most informative data point,
  but still, one point.
- **This isn't the full thesis test.** The headline was always the
  *controlled cross-model transfer*; this slice just suggests that even
  *with* the 31B-built memory in hand, the 12B's reasoning ceiling showed.

Which is why the full corpus is running tonight. Five rules can't tell you
whether the 31B is the exception (maybe you usually need something bigger
at the edge) or whether the 12B closes the gap on the long tail of easy
rules and only stumbles on a rare hard core. 263 rules and a complete JSONL
will. That's the next entry.

## The unglamorous win

Nothing leaked. The 31B came back to demo state, `models.yaml` reverted,
the production memory restored row-for-row to its pre-experiment 52-run
state, the run logs preserved as immutable per-run JSONL. The most
reassuring line of the night wasn't a benchmark — it was
`runs=52  lessons=6126  tips=8136`, matching the backup exactly, after two
model swaps and a shared-service teardown. You can run a real experiment
against production infrastructure without scarring it, as long as you back
up first and rewind on purpose.
