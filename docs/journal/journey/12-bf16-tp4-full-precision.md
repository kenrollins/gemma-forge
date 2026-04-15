---
id: journey-12-bf16-tp4-full-precision
type: journey
title: "Journey: Full Precision bf16 on All 4 L4s — No Quantization, No Compromises"
date: 2026-04-10
tags: [L3-model, parallelism, quantization, discovery]
related:
  - journey/10-the-parallelism-maze
  - journey/10-the-parallelism-maze
  - journey/02-model-strategy
one_line: "bf16 TP=4 on 4× L4 matched NVFP4 TP=2 throughput at full precision — an unexpected finding that reshaped the demo configuration and surfaced when PCIe aggregate bandwidth beats NVLink intuition."
---

# Journey: Full Precision bf16 on All 4 L4s — No Quantization, No Compromises

## The story in one sentence
I expected bf16 tp=4 to be painfully slow on the XR7620's 4 L4s without
NVLink, tested it anyway, and discovered it matches the NVFP4 tp=2
throughput while running at full precision — an unexpected finding that
reshaped the demo configuration.

## Why I tested it

With the architecture revision (deterministic evaluator, all LLM roles
on Gemma), 4 GPUs were available but only 2 were needed for NVFP4 tp=2.
The question: with 4 GPUs available, does that change how to configure
the 31B?

The math said it should work: 62 GB bf16 / 4 GPUs = 15.5 GB per GPU,
leaving ~5 GB for KV cache. The question was throughput — tp=4 on
PCIe means 4-way all-reduce on every layer.

## The measurement

| Config | 128 tok | 256 tok | 512 tok | KV cache |
|---|---|---|---|---|
| bf16 tp=4 (4× L4) | 9.8 tok/s | 14.9 tok/s | 14.8 tok/s | 17,968 tok |
| NVFP4 tp=2 (2× L4) | 14.9 tok/s | 15.1 tok/s | 15.1 tok/s | 10,432 tok |
| GB10 DGX Spark *(published benchmark, reference only)* | 6.9 tok/s | 6.9 tok/s | 6.9 tok/s | large |

The surprise: at sustained generation (256+ tokens), bf16 tp=4
**matches** NVFP4 tp=2 at ~15 tok/s. The initial tokens are slower
(9.8 tok/s at 128 tokens — the 4-way all-reduce startup overhead)
but it converges.

The GB10 row is included as a reference data point because its
published bandwidth-bound throughput for this model is commonly cited
in discussion. It is not a like-for-like comparison — the two platforms
are built for different workload shapes — but it's useful context for
anyone calibrating expectations.

The operationally meaningful finding for this project: **full precision,
no quantization loss, 72% more KV cache headroom than the NVFP4
configuration, all 4 GPUs engaged.** That is what changed the demo config.

## Why bf16 matches NVFP4 throughput

Counter-intuitive: shouldn't full precision be slower? Two factors:

1. **NVFP4 uses software dequantization on L4.** The L4 (Ada Lovelace,
   compute 8.9) does NOT have native FP4 tensor cores. NVFP4 weights
   are dequantized to bf16 at runtime before the matmul. So the actual
   COMPUTE is bf16 in both cases — the NVFP4 just saves memory.

2. **tp=4 has more aggregate memory bandwidth.** 4 GPUs reading from
   4 separate GDDR pools can sustain higher total bandwidth than 2
   GPUs reading from 2 pools, even with the all-reduce overhead.
   The bandwidth advantage approximately cancels the communication
   overhead at sustained generation.

## Why this became the demo configuration

- **No quantization asterisk.** The demo runs the flagship model
  at full fidelity. No NVFP4 to explain, no quality caveats.
- **Maximum reasoning quality.** For the Reflector role (the hardest
  cognitive task in the loop), full precision eliminates any
  possibility that quantization-induced degradation is causing
  analysis failures.
- **More KV cache.** 17,968 tokens vs 10,432 — reduces context
  overflow risk in the reflexion loop, which matters because the
  loop's entire learning mechanism depends on accumulated episodic
  memory per rule.
- **All 4 GPUs engaged.** The dashboard shows all 4 L4s loaded and
  working — useful for demos because it visually answers the "is
  the hardware actually doing work?" question that often comes up.

## Context: why this configuration exists on this hardware

The XR7620 has 4 L4s with no NVLink. The "no NVLink" part is often
framed as a constraint, and for many inference configurations it is.
But for LLM generation workloads, which are primarily memory-bandwidth-
bound during decoding, the 4× L4 configuration has 4 independent
GDDR channels at ~300 GB/s each. That aggregate bandwidth cancels
most of the all-reduce overhead that tp=4 over PCIe introduces.

This isn't a universal win — tp=4 over PCIe is genuinely worse for
workloads that need NVLink-class interconnect (long training runs,
very small batch sizes, MoE routing). But for the single-user,
long-generation reflexion-loop workload this project cares about,
the 4× L4 aggregate-bandwidth advantage materializes in the
benchmark table above.

Different platforms serve different workload shapes. The XR7620's
strength is multi-channel aggregate bandwidth in a ruggedized 2U
chassis; other platforms with unified memory are stronger for
single-process workloads that need NVLink-speed intra-GPU
communication. For this project's use case — sequential reflexion
loop iterations with a long KV cache — the XR7620 configuration
fit unexpectedly well.

## Technical notes

- Required `--enforce-eager` (no CUDA graphs) — the sampler warm-up
  with 256 dummy requests OOM'd during CUDA graph capture. Eager
  mode has ~10% overhead but avoids the memory spike.
- Required `--max-num-seqs 8` (default 256) — reduces warm-up memory.
- Required `--gpu-memory-utilization 0.92` (default 0.90) — squeezes
  an extra 460 MiB per GPU for KV cache.
- VRAM per GPU: 21,149 MiB (91.8% of 23,034 MiB L4 capacity).
- Available KV cache: 4.12 GiB per GPU.
- Load time: ~120s (vs ~170s for NVFP4 tp=2).

## Key artifacts

- Measured data in this document
- `infra/vllm/systemd/` — will be updated for bf16 tp=4 config
- `config/models.yaml` — will point all roles at the bf16 endpoint
