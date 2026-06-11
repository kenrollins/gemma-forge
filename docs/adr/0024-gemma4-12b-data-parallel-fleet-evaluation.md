# ADR-0024: Evaluate Gemma 4 12B FP8 as a single-GPU data-parallel fleet vs the single 31B TP=4 engine

- **Status:** Proposed
- **Date:** 2026-06-05
- **Deciders:** Ken Rollins
- **Related:** [ADR-0013](0013-one-triton-per-l4-no-nvlink.md), [ADR-0015](0015-gemma-4-model-lineup.md), [ADR-0018](0018-vllm-mtp-cutover.md)

## Context

The live inference plane (see [`config/models.yaml`](../../config/models.yaml)
and [`journey/12-bf16-tp4-full-precision`](../journal/journey/12-bf16-tp4-full-precision.md))
is a **single Gemma 4 31B bf16 engine at TP=4 across all four L4s**,
with MTP speculative decoding ([ADR-0018](0018-vllm-mtp-cutover.md)):

- ~15 tok/s baseline → ~41 tok/s with MTP (single stream).
- All three LLM roles (Architect, Worker, Reflector) share this one
  engine **sequentially**; the Evaluator is deterministic Python.
- The binding cost is **PCIe all-reduce on non-NVLink hardware**.
  [ADR-0015](0015-gemma-4-model-lineup.md) and
  [ADR-0013](0013-one-triton-per-l4-no-nvlink.md) are explicit that the
  ~15 tok/s floor is the tensor-parallel all-reduce penalty (~60
  all-reduce ops per forward pass over PCIe Gen4 ~32 GB/s), **not** a
  compute or quantization limit. There is no NVLink to make it cheaper.

On 2026-06-03, Google released **Gemma 4 12B** — a 11.95B dense,
decoder-only, multimodal model (48 layers, 256K context, native audio,
encoder-free). Published benchmarks put it neck-and-neck with the 26B
MoE and a notch under the 31B Dense (~77% MMLU-Pro, ~79% GPQA-Diamond).
All Gemma 4 variants, including the 12B, ship their own MTP drafter, so
speculative decoding parity is preserved. The vLLM recipe data reports
**FP8-it beats NVFP4-it** for this model on both baseline and MTP.

This changes the hardware math in a way the 31B never could:

| 12B config | Weights/GPU | Fits one L4 (24 GB)? | Instances on 4× L4 |
|---|---|---|---|
| bf16 | ~24 GB | No (no KV room — same trap as 31B-NVFP4-tp1 OOM) | — |
| **FP8, TP=1** | **~12 GB** | **Yes** (~11 GB free for KV) | **4 (data-parallel)** |
| FP8, TP=2 | ~6 GB | Yes (huge KV headroom) | 2 |
| bf16, TP=2 | ~12 GB | Yes | 2 |

A model that fits on **one** GPU eliminates all-reduce entirely (TP=1),
and L4 (Ada) has native FP8 tensor cores. That opens two doors the 31B
foreclosed:

1. **Per-stream speedup** — removing the all-reduce penalty should raise
   single-stream tok/s even though the model is smaller.
2. **Data-parallel scale-out** — up to four independent 12B instances,
   one per L4, zero inter-GPU comms. This is the structural change:
   roles that run *sequentially today because they contend for one
   shared engine* could run **concurrently** on separate instances (or N
   parallel Ralph loops over partitioned scans).

Two forces pull opposite ways: **speed up, quality down.** The 12B is a
smaller model; the Architect role is the one that depends on reasoning
quality ([ADR-0015](0015-gemma-4-model-lineup.md)'s "Architect-grade"
bar). Whether the trade nets positive is an empirical question, and this
project's history is emphatic that the hardware humiliates prediction —
[ADR-0015](0015-gemma-4-model-lineup.md) itself records the official
vLLM TP=2 bf16 recipe OOM-ing on real L4s.

There is also a standing hazard from
[`journey/09-the-nemotron-experiment`](../journal/journey/09-the-nemotron-experiment.md):
**hardware-first architecture is a trap.** "Four instances because we
have four GPUs" is exactly the failure mode that entry warns against. A
12B fleet only earns its keep if concurrency makes the *loop* measurably
better (more STIG rules fixed per hour at acceptable quality) — not
because it makes the GPU pie chart look balanced.

## Decision

**Do not adopt the 12B fleet yet. Run a pre-registered 4-arm bake-off
and gate the decision on an end-to-end metric.** This ADR records the
decision to *evaluate*, with success criteria fixed in advance so the
goalposts cannot move after the numbers come in.

**Arms under test:**

| Arm | Config | GPUs | Instances | What it isolates |
|---|---|---|---|---|
| **A (control)** | 31B bf16 TP=4 + MTP | 4 | 1 | The live incumbent |
| **B** | 12B FP8 TP=1 + MTP | 1 | up to 4 | Data-parallel candidate; zero all-reduce |
| **C** | 12B bf16 TP=2 + MTP | 2 | 2 | Quantization control (no FP8 quality risk) |
| **D** | 12B FP8 TP=2 | 2 | 2 | KV-headroom / concurrency candidate |

The arms are chosen to **de-confound three variables** the 31B kept
welded together: model size (31B vs 12B), quantization (bf16 vs FP8),
and parallelism (all-reduce penalty vs none).

**Pre-registered gate.** The 12B fleet is adopted **only if** an arm
beats Arm A on the end-to-end decider — **STIG rules successfully
remediated per hour** (deterministic Evaluator passes) — **while clearing
the Architect quality bar** on the held-out rule set. A speed win that
needs more iterations or fails the quality gate is a **no-go**, and the
honest outcome may be a **hybrid** (31B Architect + 12B Worker fleet) or
**stay on Arm A**. All three are acceptable results; the bake-off exists
to find out which is true, not to justify the fleet.

The full protocol (serve configs, metric tiers, procedure, surprises to
watch) lives in
[`futures/gemma4-12b-fleet-preflight.md`](../journal/futures/gemma4-12b-fleet-preflight.md).

## Alternatives considered

- **Stay on the 31B TP=4 incumbent; don't test.** The live config works
  and is documented. Rejected as the *default* because the all-reduce
  bottleneck is a known, named ceiling and the 12B is the first model
  that can structurally remove it. Not testing leaves a plausible
  step-change unmeasured. (Note: "stay on Arm A" remains a *legitimate
  outcome* of the bake-off — it is only rejected as a reason to skip it.)

- **Adopt the 12B fleet directly without a bake-off.** Tempting given
  the clean VRAM math. Rejected: this is precisely the
  prediction-over-measurement error [ADR-0015](0015-gemma-4-model-lineup.md)
  was written to prevent, and it ignores the quality risk to the
  Architect role. Adopting before measuring would also repeat the
  Nemotron "hardware-first" mistake.

- **Pre-commit to the hybrid (31B Architect + 12B Worker fleet).** A
  likely-attractive endpoint, since it keeps reasoning quality where it
  matters and parallelizes the Worker. Rejected as a *pre-commitment*:
  it is a hypothesis the bake-off should confirm or refute (does 12B
  actually fail the Architect bar? does the Worker actually benefit from
  concurrency on this workload?), not an assumption. It is one of the
  three permitted outcomes of the gate.

- **26B MoE instead of 12B.** The MoE runs near 4B-speed but is ~52 GB
  at bf16 and still needs multi-GPU parallelism — it does not fit one L4
  and so does not unlock the data-parallel fleet. Already weighed and
  set aside in [ADR-0015](0015-gemma-4-model-lineup.md); the 12B Dense is
  the variant that changes the topology.

## Consequences

### Positive

- **The decision is pre-registered and falsifiable.** Success criteria
  are fixed before data collection; the bake-off can return "no, stay on
  31B" and that is a clean result, not a failure.
- **Three confounds get separated.** Arms B/C/D isolate quantization and
  parallelism from model size — answers transfer to future hardware and
  future models, consistent with the general-purpose-harness goal.
- **If it wins, the win is structural, not incremental.** Removing
  all-reduce and unlocking concurrency is a topology change, not a
  tuning tweak — and it sidesteps the no-NVLink limitation instead of
  fighting it.
- **Guards against the Nemotron trap by construction.** The gate is an
  end-to-end loop metric, so "four GPUs lit up" cannot by itself pass.

### Negative / accepted trade-offs

- **Concurrency requires harness work the bake-off does not deliver.**
  [`loop.py`](../../gemma_forge/harness/loop.py) is deliberately
  sequential. Exploiting a fleet needs a worker pool / router or N
  parallel loops — out of scope here, and a real cost if we proceed.
- **New serving dependencies.** 12B MTP needs the nightly
  `vllm/vllm-openai:gemma4-unified` image; an FP8-it checkpoint must be
  pulled or produced. Pins drift; the preflight records exact versions.
- **A second production model path.** If the hybrid wins, we maintain
  two model configs (31B + 12B) instead of today's single engine —
  operational surface the single-model architecture deliberately shed.
- **Quality regression risk is real.** The 12B is a notch under the 31B;
  the Architect bar is the live hazard and the reason the gate is
  quality-AND-throughput, not throughput alone.

## References

- [Gemma 4 12B developer guide](https://developers.googleblog.com/gemma-4-12b-the-developer-guide/)
- [The New Stack — 12B nearly matches 26B](https://thenewstack.io/google-gemma-local-ai/)
- [vLLM Gemma 4 12B recipe](https://recipes.vllm.ai/Google/gemma-4-12B-it)
- [Gemma 4 MTP drafters](https://blog.google/innovation-and-ai/technology/developers-tools/multi-token-prediction-gemma-4/)
- [DGX Spark Gemma 4 MTP throughput (108 tok/s single / 670 aggregate)](https://ai-muninn.com/en/blog/dgx-spark-gemma4-mtp-108-toks)
- [ADR-0013](0013-one-triton-per-l4-no-nvlink.md) — no-NVLink all-reduce constraint
- [ADR-0015](0015-gemma-4-model-lineup.md) — measured-not-assumed model lineup
- [ADR-0018](0018-vllm-mtp-cutover.md) — MTP speculative decoding in production
- [`journey/09-the-nemotron-experiment`](../journal/journey/09-the-nemotron-experiment.md) — the hardware-first-architecture trap
- [`futures/gemma4-12b-fleet-preflight.md`](../journal/futures/gemma4-12b-fleet-preflight.md) — the executable bake-off protocol
