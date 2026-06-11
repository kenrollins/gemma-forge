---
id: futures-gemma4-12b-fleet-preflight
type: futures
title: "Gemma 4 12B Data-Parallel Fleet — Bake-Off Protocol"
date_first_surfaced: 2026-06-05
related:
  - adr/0024-gemma4-12b-data-parallel-fleet-evaluation
  - adr/0015-gemma-4-model-lineup
  - adr/0018-vllm-mtp-cutover
  - journey/09-the-nemotron-experiment
  - journey/12-bf16-tp4-full-precision
status: SPECULATIVE — protocol defined, run awaits commitment
one_line: "Operational companion to ADR-0024. The 12B Dense fits on one L4, which removes the all-reduce penalty that caps the 31B and opens a data-parallel fleet. This doc is the pre-registered 4-arm bake-off — exact serve configs, three metric tiers, and a falsifiable go/no-go gate — built so the hardware can surprise us cheaply."
---

# Gemma 4 12B Data-Parallel Fleet — Bake-Off Protocol

Operational companion to
[`adr/0024`](../../adr/0024-gemma4-12b-data-parallel-fleet-evaluation.md).
That doc holds the decision and reasoning. This one holds the executable
method: what we serve, what we measure, and the gate that decides go /
no-go / hybrid.

The whole point is to let the hardware embarrass our predictions
**cheaply** — Tier 1 is hours, Tier 3 is the expensive part, and we don't
pay for Tier 3 on an arm that already failed Tier 1 or Tier 2.

## Operating constraints

The bake-off runs against the live box without disturbing what's
running:

- **The live 31B engine stays up.** Arm A *is* the current production
  config (`config/models.yaml`, served on :8050). Do not restart it to
  test the others — the 12B arms get their own ports and their own GPUs,
  scheduled around the live engine's GPU usage (see "GPU scheduling"
  below).
- **No host tooling.** Everything serves via the existing
  `vllm/vllm-openai` image family in Docker, same pattern as
  `infra/vllm/scripts/serve-gemma-bf16-tp4.sh`.
- **Shared services untouched.** Triton director, observability stack,
  web tier, Postgres/Neo4j memory stack — all left alone.
- **Scratch lives outside the repo.** Raw run logs, tok/s captures, and
  serve stdout go to `/tmp/12b-bakeoff/`; only the results table + the
  journey entry land in git.

Safe playground:

- `infra/vllm/scripts/serve-12b-*.sh` (new serve scripts, one per arm)
- `/tmp/12b-bakeoff/` (external scratch — captures, raw metrics)
- `tools/bakeoff/` (new — the results harness, if we build it)
- Weights: `/data/triton/weights/` (pull the 12B + its drafter here)

### GPU scheduling

Arm A occupies all 4 L4s. The 12B arms need 1–2 GPUs. Two options,
decide at run time:

- **Tear-down-and-test** (simplest): bring Arm A down, run B/C/D, bring
  Arm A back. Clean, but the box is "off" for the live demo during the
  run. Fine for a focused bake-off window.
- **Coexist**: only viable if Arm A is temporarily reconfigured to
  TP=2 on 2 GPUs (NVFP4, per ADR-0015's old lineup) to free 2 L4s for
  the 12B arms. More moving parts; only worth it if the box must stay
  demo-able during the run. **Default: tear-down-and-test in a window.**

## Pre-flight checks (do these before any arm runs)

These are the "is this even buildable" gates, mirroring the
detection-tuning / pentest pre-flight pattern. All must be green.

- [ ] **12B weights pull.** `google/gemma-4-12B-it` → `/data/triton/weights/`.
- [ ] **12B MTP drafter pull.** The 12B's own assistant/drafter checkpoint
      (Gemma 4 ships one per variant). Confirm path + size.
- [ ] **FP8 checkpoint sourced.** Either pull an `gemma-4-12B-it-FP8`
      checkpoint or produce one with modelopt. (Recipe data says FP8-it
      beats NVFP4-it for this model — FP8 is the target, not NVFP4.)
- [ ] **Nightly image present.** 12B MTP needs the
      `vllm/vllm-openai:gemma4-unified` nightly (or a nightly wheel).
      Pin the exact digest in the serve script.
- [ ] **Eval set frozen.** A fixed list of N failing STIG rules (propose
      N=20, spanning easy/medium/hard) + their known-good remediation
      signatures, snapshotted so every arm sees the identical workload.

If any pre-flight is red, stop and record why — that's a finding, not a
detour.

## The four arms — exact serve configs

Mirror `serve-gemma-bf16-tp4.sh`'s flag style. Sketch configs (tune
`gpu-memory-utilization` against *measured* VRAM, per ADR-0015's lesson —
never trust the math):

**Arm A — control (already live, do not rebuild):**
```
--model /weights/gemma-4-31B-it --tensor-parallel-size 4
--dtype bfloat16 --max-model-len 16384 --gpu-memory-utilization 0.92
--speculative-config '{"method":"mtp","model":"/weights/gemma-4-31B-it-assistant","num_speculative_tokens":2}'
--enable-auto-tool-choice --tool-call-parser gemma4 --enforce-eager
```

**Arm B — 12B FP8 TP=1 (data-parallel candidate), one per GPU:**
```
--model /weights/gemma-4-12B-it-FP8 --tensor-parallel-size 1
--quantization fp8 --kv-cache-dtype fp8 --max-model-len 16384
--gpu-memory-utilization 0.90
--speculative-config '{"method":"mtp","model":"/weights/gemma-4-12B-it-assistant","num_speculative_tokens":4}'
--enable-auto-tool-choice --tool-call-parser gemma4
# replicate across CUDA_VISIBLE_DEVICES=0 / 1 / 2 / 3 on ports 8051-8054 for the fleet test
```

**Arm C — 12B bf16 TP=2 (quantization control), two instances:**
```
--model /weights/gemma-4-12B-it --tensor-parallel-size 2 --dtype bfloat16
--max-model-len 16384 --gpu-memory-utilization 0.90
--speculative-config '{"method":"mtp","model":"/weights/gemma-4-12B-it-assistant","num_speculative_tokens":4}'
--enable-auto-tool-choice --tool-call-parser gemma4
# instance 1 on GPUs 0,1 (:8055); instance 2 on GPUs 2,3 (:8056)
```

**Arm D — 12B FP8 TP=2 (KV-headroom / concurrency candidate), two instances:**
```
--model /weights/gemma-4-12B-it-FP8 --tensor-parallel-size 2
--quantization fp8 --kv-cache-dtype fp8 --max-model-len 32768
--gpu-memory-utilization 0.90
--enable-auto-tool-choice --tool-call-parser gemma4
# note the larger max-model-len — D's reason to exist is KV headroom; push context + batch
```

> `num_speculative_tokens` is 4 for the 12B arms per the recipe's 4–8
> recommendation, vs 2 for the 31B. The acceptance rate it actually
> achieves on STIG is a Tier-1 measurement, not an assumption (see
> surprises).

## Metrics — three tiers, cheap-to-expensive

Run in order. An arm that fails a tier doesn't advance.

### Tier 1 — serving (cheap, run first, all arms)

- **Measured VRAM/GPU** and **max usable KV cache / context before OOM.**
  Record actuals, not predictions.
- **Single-stream tok/s + TTFT**, MTP on and off.
- **MTP acceptance rate on the STIG workload specifically.** ADR-0018
  already caught this: 99% on prose, 73% on STIG XML/bash for the 31B.
  The 12B drafter may differ — measure it, don't inherit it.
- **Aggregate tok/s under a concurrency sweep** (1, 4, 8, 16 parallel
  requests). This is where Arm B's 4-instance fleet should pull ahead —
  *if* PCIe / host-memory bandwidth doesn't choke it.

### Tier 2 — Architect quality gate (the kill switch)

- Run the frozen N-rule set through each arm **as the Architect**.
- Score each plan on [ADR-0015](../../adr/0015-gemma-4-model-lineup.md)'s
  own "Architect-grade" criteria: structured, correct, includes
  backup/rollback, names FIPS-validated algorithms correctly.
- Verdict per rule: pass/fail, judged by the 31B (Arm A) as reference
  judge **or** a deterministic checker where one exists.
- **This tier can kill an arm for the Architect role independent of any
  speed win.** An arm that fails here is still eligible for the *Worker*
  role in a hybrid — note it, don't discard it.

### Tier 3 — end-to-end, the decider (expensive, only for arms that pass 1+2)

- Run the **actual Ralph loop** on the frozen N-rule set with each
  surviving arm.
- Capture per arm: **rules successfully remediated** (deterministic
  Evaluator passes), **wall-clock**, **iterations/reverts per rule**,
  **tokens consumed**.
- Headline metric: **STIG rules fixed per hour at acceptable quality.**
  This is the number ADR-0024's gate is written against. It collapses
  speed and quality into one figure and is the only one that decides
  adoption.

## Surprises to instrument for (bets placed in advance)

Pre-registering the failure modes so we notice them instead of
explaining them away:

1. **MTP acceptance craters on STIG bash for the 12B drafter** — same
   prose-vs-code gap ADR-0018 found, possibly worse on a smaller drafter.
2. **FP8 12B passes as a Worker but fails the Architect bar** → pushes
   toward the hybrid (31B Architect + 12B Worker fleet). This is the most
   likely "interesting" outcome.
3. **4 concurrent instances scale sub-linearly** — contention on PCIe
   Gen4 / host memory bandwidth, not GPU. Aggregate tok/s flattens before
   4×.
4. **TP=2 on the 12B costs almost nothing** (smaller hidden dim → smaller
   all-reduce payload than the 31B) → Arm C (bf16, full quality, 2
   instances, negligible penalty) becomes the surprise winner.

Any of these is a publishable result. "We expected X, the L4s did Y" is
the journal voice this project runs on.

## Decision gate (pre-registered — do not move after data is in)

Adopt a 12B configuration **only if** a surviving arm beats **Arm A** on
Tier 3 (STIG rules fixed per hour) **and** clears the Tier 2 Architect
bar. Permitted outcomes, all clean:

- **12B fleet wins outright** → ADR-0024 moves to Accepted; build the
  router / parallel-loop harness (separate scope).
- **Hybrid wins** (31B Architect + 12B Worker fleet) → ADR-0024 Accepted
  with the hybrid topology; new ADR for role-to-engine assignment.
- **31B holds** → ADR-0024 Superseded-by-itself-as-Rejected; record the
  measured reason. Arm A stays. **This is a real, acceptable result.**

The trap to avoid, explicitly: do not adopt the fleet because "four GPUs
are lit up." The Tier-3 gate exists so that
[the Nemotron hardware-first failure mode](../journey/09-the-nemotron-experiment.md)
cannot pass by looking impressive on the dashboard.

## Files to read first (in this order, if running)

1. [`adr/0024`](../../adr/0024-gemma4-12b-data-parallel-fleet-evaluation.md)
   — the decision + the gate
2. This file — the executable protocol
3. [`adr/0015`](../../adr/0015-gemma-4-model-lineup.md) — the
   measured-not-assumed precedent; the Architect quality bar
4. [`adr/0018`](../../adr/0018-vllm-mtp-cutover.md) — MTP flags + the
   prose-vs-STIG acceptance gap
5. [`infra/vllm/scripts/serve-gemma-bf16-tp4.sh`](../../../infra/vllm/scripts/serve-gemma-bf16-tp4.sh)
   — the serve-script pattern to mirror
6. [`gemma_forge/harness/loop.py`](../../../gemma_forge/harness/loop.py) —
   the sequential loop a fleet would have to parallelize
7. [`journey/09-the-nemotron-experiment`](../journey/09-the-nemotron-experiment.md)
   — why "more models / more GPUs" must earn its keep

## What still needs deciding before Arm B runs

- **Eval set size N and difficulty spread.** Default: 20 rules, frozen,
  spanning easy/medium/hard. Bigger N = better signal, longer Tier-3.
- **GPU scheduling: tear-down vs coexist** (see above). Default:
  tear-down-and-test in a dedicated window.
- **FP8 checkpoint provenance.** Pull a published `gemma-4-12B-it-FP8`
  vs produce one with modelopt. For Federal defensibility, a
  vendor-published checkpoint is preferable (same reasoning as
  [ADR-0015](../../adr/0015-gemma-4-model-lineup.md)'s NVFP4 choice) —
  confirm one exists before defaulting to a self-produced quant.
- **Reference judge for Tier 2.** 31B-as-judge vs deterministic checker
  vs both. Default: deterministic where possible, 31B-as-judge for the
  rest, and spot-check the judge's calls by hand.

These settle at run time with no impact on the protocol's shape.

---

## Decision gate

This protocol is **complete and pre-registered**. The technical question
("can we run this bake-off?") is answered: **yes — four serve scripts, a
frozen eval set, and the existing Ralph loop as the Tier-3 harness.**

The remaining gate is **commitment to a run window**: bring the box into
a bake-off state (GPU scheduling), pull the 12B weights + FP8 checkpoint +
nightly image, and spend the Tier-3 wall-clock. Captured here for an
explicit yes/no before Arm B's first token.
