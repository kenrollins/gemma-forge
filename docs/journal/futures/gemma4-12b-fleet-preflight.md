---
id: futures-gemma4-12b-fleet-preflight
type: futures
title: "Gemma 4 12B vs 31B — Capability-vs-System Experiment Protocol"
date_first_surfaced: 2026-06-05
related:
  - adr/0024-gemma4-12b-data-parallel-fleet-evaluation
  - adr/0015-gemma-4-model-lineup
  - adr/0016-graphiti-neo4j-postgres-memory-stack
  - journey/09-the-nemotron-experiment
  - journey/12-bf16-tp4-full-precision
status: SPECULATIVE — protocol defined, run awaits commitment
one_line: "Operational companion to ADR-0024. Both models bf16, both official weights. The question is portable: can the SYSTEM (the loop + accumulated tips/learnings) lift a smaller 12B to the capability of a bigger bare 31B — especially when the 12B inherits learnings the 31B built? Capability is the metric; throughput is the kicker."
---

# Gemma 4 12B vs 31B — Capability-vs-System Experiment

Operational companion to
[`adr/0024`](../../adr/0024-gemma4-12b-data-parallel-fleet-evaluation.md).
That doc holds the decision and the gate. This one holds the executable
method: what we serve, what we run, what we measure.

**The reframe (2026-06-11):** this stopped being an L4 throughput bake-off.
The portable claim — and the one this project actually rests on — is that
the *system around the model* carries the capability
([ADR-0016](../../adr/0016-graphiti-neo4j-postgres-memory-stack.md)). So we
hold the task fixed, run both models at full precision, and vary **system
maturity**. The headline is whether a 12B inheriting the 31B's accumulated
learnings reaches 31B-class task results. If it does, model size is
substitutable by system maturity — a result that survives the next box.

## Operating constraints

- **Both models are official Google bf16** (`gemma-4-31B-it`,
  `gemma-4-12B-it`) — Apache-2.0, ungated, no quantization. No FP8/QAT,
  no community checkpoints (see ADR-0024 Context: no official FP8 exists,
  and Federal needs official weights).
- **Teardown window, not coexist.** Unlike the shelved FP8 plan, bf16 12B
  needs TP=2 (2 L4s) and the 31B needs all 4 — they cannot share the box.
  Conditions run **sequentially**: stop the shared 31B, run a 12B
  condition, restore. Use `infra/vllm/scripts/bakeoff/stop-31b.sh` /
  `restore-31b.sh` (systemctl-correct; the 31B is a shared, boot-enabled
  service — see that dir's README).
- **The 31B is shared** with quantum-ai-orchestrator. Confirm nothing else
  is mid-run on :8050 before tearing down. Don't reboot mid-window.
- **Memory-stack stays up.** The Graphiti/Neo4j/Postgres tip store is the
  independent variable — leave it running; snapshot/restore tip state
  between conditions rather than wiping it.
- **Scratch outside the repo:** raw logs/captures to `/tmp/12b-vs-31b/`;
  only the results table + journey entry land in git.

## Conditions

| # | Model | Serve | System (tips) | Answers |
|---|---|---|---|---|
| **1** | 31B bf16 | live :8050 (TP=4) | **cold** | reference ceiling — bare big model |
| **2** | 12B bf16 | TP=2 :8055 | **cold** | the raw size gap — *"straight-up 12B vs 31B"* |
| **3** | 12B bf16 | TP=2 :8055 | **warm — tips built by the 31B** | **headline: does the system close the gap?** |
| 4 (opt) | 31B bf16 | live :8050 (TP=4) | warm — own tips | true ceiling |
| 5 (opt) | 12B bf16 | TP=2 :8055 | warm — self-built tips | control: transfer vs. any warm system |

**Pre-vacation slice = Conditions 1 + 2** (both cold). Answers the literal
"what does straight-up 12B vs 31B look like," fits the window, anchors the
gap. **The arc = Condition 3**, the cross-model tip transfer — the actual
thesis test, run with more time.

## Pre-flight checks (all green before any run)

- [ ] **12B weights pull.** `google/gemma-4-12B-it` → `/data/triton/weights/`
      (~24 GB bf16). Official, ungated.
- [ ] **31B already present.** `/data/triton/weights/gemma-4-31B-it` (live).
- [ ] **12B serves on TP=2.** Stand it up once
      (`infra/vllm/scripts/bakeoff/serve-12b-bf16-tp2.sh`), confirm
      `:8055/v1/models` answers and a trivial completion returns. NOTE: that
      script currently includes `--speculative-config` (MTP); MTP is a
      throughput-only optimization irrelevant to the capability metric and
      there is **no confirmed official 12B drafter** — drop
      `--speculative-config` for the capability runs (override or a no-MTP
      variant). Add it back only for the throughput kicker if an official
      drafter is confirmed.
- [ ] **Eval set frozen.** A fixed list of N failing STIG rules (propose
      N=20, spanning easy/medium/hard) snapshotted so every condition sees
      the identical workload. Same set the harness already scans.
- [ ] **Tip-pool snapshot/restore works.** Verify you can snapshot the
      memory-stack tip state to a labeled checkpoint and restore it — this
      is what makes "cold" and "31B-built warm" reproducible. (Cold = empty
      tip pool; warm = a specific snapshot.)

If any check is red, stop and record why — that's a finding, not a detour.

## Serve configs

Only two engines now (the FP8 serve scripts in
`infra/vllm/scripts/bakeoff/` are **shelved, not deleted** — they return if
an official quantized 12B ships):

- **31B (Conditions 1, 4):** the live `:8050` service — do not rebuild.
- **12B bf16 TP=2 (Conditions 2, 3, 5):**
  `bakeoff/serve-12b-bf16-tp2.sh`, GPUs 0,1 → `:8055` (one instance is
  enough for a capability run; the second instance only matters for the
  throughput kicker).

## Metrics

### Primary — capability (the decider)

Run the **actual Ralph loop** on the frozen N-rule set for each condition.
Capture:

- **Rules successfully remediated** — deterministic Evaluator passes. The
  headline number.
- **Iterations / reverts per rule** — how hard the model worked to get there.
- **Architect plan quality** — scored on
  [ADR-0015](../../adr/0015-gemma-4-model-lineup.md)'s "Architect-grade"
  bar (structured, correct, backup/rollback present, FIPS algorithms named
  correctly). Judge: deterministic checks where possible, 31B-as-judge for
  the rest, hand-spot-checked.

The comparison that matters: **Condition 3 vs Condition 1.** Does 12B +
31B-built tips reach the bare-31B reference? Condition 2 anchors the gap
the system had to close.

### Secondary — cost / throughput (the kicker, not the headline)

- tok/s + wall-clock per condition; tokens consumed per rule.
- The framing if capability is comparable: *"same task outcomes at a
  fraction of the compute, and the 12B parallelizes where the 31B can't."*
  Never lead with this — it's the kicker after the capability result.

## The cross-model transfer (Condition 3) — the headline procedure

1. **Build the tips on the 31B.** Run the 31B (cold start) over a *training*
   slice of STIG rules; let the loop + dream pass accumulate the tip pool /
   context graph as it normally does. Snapshot that tip state → `tips@31B`.
2. **Freeze, snapshot, tear down.** Snapshot the memory-stack at `tips@31B`.
   `stop-31b.sh`.
3. **Run the 12B with the 31B's learnings.** Stand up 12B bf16 TP=2 with the
   memory-stack restored to `tips@31B`. Run the loop over the *held-out*
   eval slice (disjoint from the training slice — this measures transfer,
   not memorization).
4. **Compare** Condition 3 (12B@tips31B, held-out) against Condition 1
   (31B cold, held-out) and Condition 2 (12B cold, held-out).

**Assumption to verify first:** the tip pool must be genuinely
model-agnostic in storage and consumable by the 12B. If tips encode
31B-specific phrasing or token patterns, Condition 3 needs care (or a
light translation pass). Check a few tips by hand before trusting the run.

## Surprises to instrument for (bets placed in advance)

1. **The system closes the gap (3 ≈ 1).** The thesis lands: a smaller model
   + inherited learnings ≈ bigger bare model. The result we're hunting.
2. **The gap only partially closes.** The system carries the 12B *most* of
   the way; a residual reasoning gap remains on the hardest rules — quantify
   it. Maps to a hybrid (31B for the hardest Architect calls, 12B elsewhere).
3. **The system doesn't transfer.** Tips built on the 31B don't help the
   12B (model-specific learnings, or the 12B can't execute on good plans).
   "Non-determinism has a cost," measured.
4. **The 12B cold is closer to the 31B than expected.** The raw size gap is
   small on this task → the system's marginal lift is what to watch, and the
   throughput/cost kicker carries the story on its own.

Any of these is publishable. Honest failures are the journal's whole voice.

## Decision gate (pre-registered — do not move after data is in)

Per ADR-0024: a 12B-based strategy is worth adopting **only if** Condition 3
reaches the Condition 1 reference on capability. Permitted outcomes, all
clean:

- **3 ≈ 1** → thesis demonstrated; pursue smaller-model-in-a-matured-system.
- **3 partially closes** → quantify how far the system carries a smaller
  model; hybrid where reasoning is irreplaceable.
- **3 does not close** → stay on the 31B for reasoning-critical roles; the
  measured floor is the result.

## Files to read first (in this order, if running)

1. [`adr/0024`](../../adr/0024-gemma4-12b-data-parallel-fleet-evaluation.md)
   — the decision + the gate
2. This file — the executable protocol
3. [`adr/0016`](../../adr/0016-graphiti-neo4j-postgres-memory-stack.md) —
   the memory stack this experiment puts a number on
4. [`adr/0015`](../../adr/0015-gemma-4-model-lineup.md) — the
   measured-not-assumed precedent; the Architect quality bar
5. [`infra/vllm/scripts/bakeoff/README.md`](https://github.com/kenrollins/gemma-forge/blob/main/infra/vllm/scripts/bakeoff/README.md)
   — serve + stop/restore mechanics (shared, boot-enabled 31B)
6. [`gemma_forge/harness/loop.py`](https://github.com/kenrollins/gemma-forge/blob/main/gemma_forge/harness/loop.py) —
   the loop that runs each condition
7. [`journey/09-the-nemotron-experiment`](../journey/09-the-nemotron-experiment.md)
   — measured-outcomes-only discipline for model/role changes

## What still needs deciding before Condition 1 runs

- **Eval set size N + difficulty spread.** Default: 20 rules, frozen.
- **Training vs held-out split for the transfer run.** Condition 3 needs a
  disjoint training slice (tips built here) and held-out slice (measured
  here) so we test transfer, not memorization. Default: 50/50 by rule,
  difficulty-balanced.
- **Tip-pool snapshot mechanism.** How exactly we checkpoint/restore the
  Graphiti/Neo4j/Postgres tip state to `cold` / `tips@31B`. Confirm the
  existing cross-run machinery (Run 1 cold → Run 2 warm) exposes this
  cleanly, or add a thin snapshot helper.
- **Architect-quality judge.** Deterministic where possible; 31B-as-judge
  for the rest; hand-spot-check.

These settle at run time with no impact on the protocol's shape.

---

## Decision gate

This protocol is **complete and pre-registered**. Technical feasibility is
green: official bf16 weights, the existing Ralph loop as the run harness,
the existing memory-stack as the tip store.

The remaining gate is **commitment to a run window**. The pre-vacation
slice (Conditions 1 + 2, straight-up cold 12B vs 31B) is the cheap,
high-value start; the cross-model transfer (Condition 3) is the headline
and wants more time. Captured here for an explicit yes/no.
