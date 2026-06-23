# ADR-0024: Evaluate Gemma 4 12B vs 31B (bf16) — does the system close the model-size gap?

- **Status:** Accepted — tiered/hybrid (measured 2026-06-13; see Results)
- **Date:** 2026-06-05 (reframed 2026-06-11; resolved 2026-06-13)
- **Deciders:** Ken Rollins
- **Related:** [ADR-0013](0013-one-triton-per-l4-no-nvlink.md), [ADR-0015](0015-gemma-4-model-lineup.md), [ADR-0016](0016-graphiti-neo4j-postgres-memory-stack.md), [ADR-0018](0018-vllm-mtp-cutover.md)

## Context

On 2026-06-03 Google released **Gemma 4 12B** — an 11.95B dense,
decoder-only, multimodal model (48 layers, 256K context). Published
benchmarks put it neck-and-neck with the 26B MoE and a notch under the
31B Dense (~77% MMLU-Pro, ~79% GPQA-Diamond). The live inference plane
([`config/models.yaml`](../../config/models.yaml),
[`journey/12-bf16-tp4-full-precision`](../journal/journey/12-bf16-tp4-full-precision.md))
is a single **31B bf16 TP=4** engine shared sequentially by all LLM roles.

The first instinct was a throughput bake-off — the 12B fits on one L4 and
could run as a 4-instance data-parallel fleet, sidestepping the non-NVLink
all-reduce penalty that caps the 31B ([ADR-0013](0013-one-triton-per-l4-no-nvlink.md)).
That framing was **set aside on 2026-06-11** for two reasons:

1. **This is not just about the XR7620.** Other hardware architectures
   will be in play. An L4-specific throughput result (all-reduce penalties,
   FP8 tensor cores, 4×24 GB topology) is largely throwaway when the next
   box arrives. We want a portable claim.
2. **No official FP8/NVFP4 12B checkpoint exists** (Google or NVIDIA — only
   community quants; NVIDIA published NVFP4 for the 31B only). For a Federal
   audience, "official weights" is load-bearing
   ([ADR-0015](0015-gemma-4-model-lineup.md)'s reasoning), and the only
   official sub-bf16 option is Google QAT-int4, which quantizes attention
   (the Architect's reasoning substrate) and serves through the rough GGUF
   path. So a quantized fleet is off the table for now.

**bf16-only, and a better question.** Both models run at full precision.
The real thing worth proving is this project's foundational thesis — that
**the system around the model (the loop, the context graph, the accumulated
tips/learnings — [ADR-0016](0016-graphiti-neo4j-postgres-memory-stack.md))
carries the capability**, not raw parameter count. If that is true, then:

> A **smaller** model inside a **matured** system should perform comparably
> to a **larger** bare model. And the sharp form: **learnings built up on
> the 31B, handed to the 12B, should lift the 12B to 31B-class results.**

If it holds, "12B instead of 31B" is not a downgrade — it is the proof
point: deploy the cheaper, parallelizable, hardware-portable model and let
the system carry the difference. That generalizes off this box to any
architecture. If it fails — if raw reasoning is irreplaceable for the
Architect and the system cannot close the gap — that is an equally
valuable result: "[non-determinism has a cost](../../README.md)" with a
number attached, and a measured floor on how small the model can go.

The standing hazard from
[`journey/09-the-nemotron-experiment`](../journal/journey/09-the-nemotron-experiment.md)
still applies inverted: don't let a *model swap* be justified by anything
other than measured task outcomes.

## Decision

**Run a pre-registered capability experiment comparing 12B and 31B (both
bf16) across system-maturity conditions, and gate any model-strategy change
on task capability — not tok/s.** Success criteria are fixed in advance so
the goalposts cannot move after the numbers come in.

**Conditions:**

| # | Model | System state | Question it answers |
|---|---|---|---|
| **1** | 31B bf16 (TP=4) | cold (no tips) | reference ceiling — bare big model |
| **2** | 12B bf16 (TP=2) | cold (no tips) | the raw size gap — *"straight-up 12B vs 31B"* |
| **3** | 12B bf16 (TP=2) | warm — tips built by the 31B | **the headline: does the system close the gap?** |
| 4 (opt) | 31B bf16 (TP=4) | warm — its own tips | true ceiling |
| 5 (opt) | 12B bf16 (TP=2) | warm — self-built tips | control: transfer vs. any warm system |

**Pre-vacation slice:** Conditions 1 + 2 only — the straight-up cold
comparison. Fits the run window, gives talking points, anchors the gap.
**The arc:** Condition 3 (cross-model tip transfer) is the real proof and
runs with more time afterward.

**Primary metric: capability**, on a frozen STIG rule set — rules
successfully remediated (deterministic Evaluator), iterations/reverts per
rule, and Architect plan quality
([ADR-0015](0015-gemma-4-model-lineup.md)'s "Architect-grade" bar).
**Secondary: cost/throughput** — tok/s and wall-clock, framed as the
kicker ("comparable capability at a fraction of the compute, and
parallelizable"), never as the headline.

**Pre-registered gate.** A 12B-based strategy is worth adopting **only if**
Condition 3 (12B + 31B-built system) reaches the Condition 1 reference on
capability — comparable rules-fixed and Architect quality on the held-out
set. Permitted outcomes, all clean:
- **System closes the gap** (3 ≈ 1) → the thesis is demonstrated; pursue
  the smaller-model-in-a-matured-system architecture.
- **Gap partially closes** → quantify how far the system carries a smaller
  model, and where raw reasoning remains irreplaceable.
- **System does not close the gap** → "non-determinism has a cost" with a
  measured floor; stay on the 31B for reasoning-critical roles.

## Alternatives considered

- **The original throughput bake-off (FP8 fleet, 4 arms).** Rejected per
  the Context: L4-specific and non-portable, and blocked by the absence of
  an official FP8 checkpoint for a Federal audience. The serve-script
  matrix built for it is retained for the bf16 arms (A=31B, C=12B bf16
  TP=2); the FP8 arms are shelved, not deleted.

- **Quantized 12B to fit one L4 (QAT-int4 or community FP8).** Would
  enable the single-GPU fleet, but QAT-int4 quantizes attention (the
  Architect's reasoning) and rides the experimental GGUF path; community
  FP8 is not vendor-published. Both undercut the Federal "official weights"
  requirement and confound the capability question with a quantization
  variable. Revisit if/when an official FP8/NVFP4 12B ships.

- **Capability comparison without the warm/transfer conditions** (just
  bare 12B vs bare 31B). This is the pre-vacation slice, not the whole
  experiment. On its own it only measures the size gap — it cannot test
  the thesis (that the *system* closes it). Necessary but not sufficient.

- **26B MoE instead of 12B.** ~52 GB bf16, needs multi-GPU parallelism,
  no single-GPU story, and weaker single-stream reasoning than the Dense.
  Already set aside in [ADR-0015](0015-gemma-4-model-lineup.md). The 12B
  Dense is the right small-model probe.

## Consequences

### Positive

- **Tests the actual thesis, portably.** Capability-vs-system is
  hardware-agnostic — the result survives the next box, unlike an L4
  throughput number. Directly exercises the
  [memory-stack](0016-graphiti-neo4j-postgres-memory-stack.md) /
  context-graph claim.
- **Pre-registered and falsifiable.** "The system does not close the gap"
  is an explicitly acceptable, publishable outcome.
- **Fully official weights.** Both models are Google bf16 releases
  (Apache-2.0, ungated) — no quantization caveats, clean for Federal.
- **Reuses existing machinery.** Cold/warm runs, the dream pass, and a
  model-agnostic tip pool already exist (Run 1 cold → Run 2 warm is this
  shape); the cross-model condition is a tip-pool hand-off, not new infra.

### Negative / accepted trade-offs

- **No single-GPU fleet result this round.** bf16 12B needs TP=2 (2
  instances max on 4× L4), so the "4× data-parallel" throughput headline
  is deferred until an official quantized checkpoint exists.
- **Cross-model tip transfer is an assumption to verify.** It requires the
  tip pool / context graph to be genuinely model-agnostic in storage and
  consumable by the 12B. If tips encode 31B-specific phrasing, Condition 3
  needs care (or a translation pass).
- **Capability scoring is heavier than tok/s.** Condition 3 needs full
  Ralph runs and Architect-quality judging, not just a serving benchmark —
  more wall-clock and a judging protocol (see the preflight).
- **GPU exclusivity.** 31B and 12B both need the L4s; conditions run in
  sequence in a teardown window against the shared, boot-enabled 31B
  service (see the preflight's stop/restore).

## Measured results & decision (2026-06-13)

Ran the **full 263-rule corpus on both models** from the identical pristine
52-run memory and the same baseline VM (12B first; its deposits rewound so the
31B started from the same clean state). Full narrative: [`journey/43`](../journal/journey/43-memory-isnt-reasoning.md).

| | 12B (bf16 TP=2) | 31B (bf16 TP=4) |
|---|---|---|
| Remediated | 198/260 (76%) | 235/257 (91%) |
| Escalated | 58 | 18 |
| Total attempts | 663 | 360 |
| Completion tokens | 644k | 335k |
| Rule wall-clock | 7.0h | 4.6h |
| **`sysctl_net` family** | **1/21** | **21/21** |

**Outcome vs the pre-registered gate:** the system did **not** close the gap on
the hard tail. The two models were near-identical on the declarative majority
(auth/audit/ssh/packages 90–100% both), but the entire 15-point gap concentrated
in one cliff — kernel/`sysctl_net` precise-persistent-state rules — where the 12B
went 1/21 *despite hydrating the relevant lessons from memory*. Same VM, same
memory, 1/21 vs 21/21 ⇒ a **model-capability gap, not environmental**. Corroborated
by published benchmarks (LiveCodeBench-v6 +8, MMLU-Pro +8). Memory ≠ reasoning.

**Decision — tiered/hybrid, not a single-model swap:**

- The 12B (cheap, single-GPU) runs the corpus and autonomously clears the ~76%
  declarative bulk.
- Its **preemptive escalations** (55/58 of failures — it reliably knows when it's
  stuck) are the routing signal: the hard class escalates to the 31B (or a human).
  This buys ~91%-class outcomes at mostly-12B cost.
- Cost intuition **inverts at the wall**: the 12B burned *more* tokens and
  wall-clock grinding rules it could not solve (35–37 attempts × 20 min on single
  sysctl rules) than the 31B spent succeeding. Edge systems must **cap-and-escalate**,
  not grind.
- A single-GPU quantized fleet remains shelved pending an official FP8/NVFP4 12B;
  this experiment was bf16/official to keep it Federal-defensible.

## References

- [Gemma 4 12B developer guide](https://developers.googleblog.com/gemma-4-12b-the-developer-guide/)
- [The New Stack — 12B nearly matches 26B](https://thenewstack.io/google-gemma-local-ai/)
- [vLLM Gemma 4 12B recipe](https://recipes.vllm.ai/Google/gemma-4-12B-it)
- [ADR-0015](0015-gemma-4-model-lineup.md) — measured-not-assumed model lineup; the official-weights / Architect-quality reasoning
- [ADR-0016](0016-graphiti-neo4j-postgres-memory-stack.md) — the memory stack whose value this experiment puts a number on
- [ADR-0018](0018-vllm-mtp-cutover.md) — MTP speculative decoding in production
- [`journey/09-the-nemotron-experiment`](../journal/journey/09-the-nemotron-experiment.md) — measured-outcomes-only discipline for model/role changes
- [`futures/gemma4-12b-fleet-preflight.md`](../journal/futures/gemma4-12b-fleet-preflight.md) — the executable protocol
