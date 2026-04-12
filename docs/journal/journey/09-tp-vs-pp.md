---
id: journey-09-tp-vs-pp
type: journey
title: "Journey: Tensor Parallelism vs Pipeline Parallelism on L4s Without NVLink"
date: 2026-04-10
tags: [L3-model, parallelism, discovery]
related:
  - journey/10-parallelism-deep-dive
  - gotchas/nemotron-tp-tiling-error
one_line: "Gemma 4 uses TP because it is a dense model that needs all-reduce after every layer; Nemotron uses PP because it is an MoE that can stack expert layers — and PP gives 120× more KV cache headroom because each GPU only holds half the weights."
---

# Journey: Tensor Parallelism vs Pipeline Parallelism on L4s Without NVLink

## The story in one sentence
Gemma 4 uses TP because it's a dense model that needs all-reduce after
every layer; Nemotron uses PP because it's an MoE that can stack expert
layers — and PP gives 120x more KV cache headroom because each GPU
only holds half the weights.

## Why this matters at the edge

On data center GPUs with NVLink (600+ GB/s), TP's all-reduce overhead
is negligible. On L4s with PCIe Gen4 (~32 GB/s), it's the dominant
bottleneck. But TP is the only option for dense models because every
layer's matrix multiply needs the full weight tensor — you CAN'T split
layers, you MUST split matrices.

MoE models unlock PP because each expert is a self-contained layer
block. You can assign experts 0-63 to GPU 2 and experts 64-127 to
GPU 3. The only inter-GPU transfer is one activation tensor at the
pipeline boundary, not 60 all-reduce operations.

## The VRAM consequence nobody talks about

This is the most important insight for edge deployment:

| Parallelism | Weight distribution | Free VRAM per GPU | KV cache |
|---|---|---|---|
| TP=2 (Gemma 31B NVFP4) | All layers on both GPUs (split matrices) | ~640 MiB | 10,432 tokens |
| PP=2 (Nemotron 30B NVFP4) | Half the layers per GPU | ~9.7 GiB | 1,257,472 tokens |

**PP gives 120x more KV cache** because each GPU only holds half the
model weights. With TP, both GPUs hold the full model (split per
dimension), so VRAM savings come only from the matrix split — not
from the total weight count.

For the Auditor role, this means:
- TP=2 with Gemma: max_model_len=8192, tight, context management critical
- PP=2 with Nemotron: max_model_len=32768, massive headroom, Auditor can
  reason at length about complex side effects

## Why Gemma 4 can't use PP

We tested PP=2 for Gemma 4 and it failed with an `IntermediateTensors`
bug in vLLM 0.19.0. But even if it worked, PP for a dense model on
non-NVLink hardware has a different problem: pipeline bubbles. With
PP, GPU 1 is idle while GPU 0 processes its layers, and vice versa.
For a single-request workload (our Ralph loop), that means 50% GPU
utilization. TP keeps both GPUs busy simultaneously.

For MoE, pipeline bubbles are less of an issue because each GPU's
"half" is a full set of experts that process independently. The
activation transfer at the pipeline boundary is small relative to
the compute per expert.

## Why Nemotron can't use TP

We tested TP=2 for Nemotron NVFP4 and it failed with a Marlin kernel
tiling error: `size_n = 5152 is not divisible by tile_n_size = 64`.
When the MoE expert weights are split across GPUs via TP, the resulting
matrix dimensions don't align with the Marlin NVFP4 kernel's tile
size. This is a quantization + MoE + TP interaction — PP avoids it
because it doesn't split weight matrices.

## The demo narrative

"Two models, two parallelism strategies, matched to their architectures.
Gemma's dense 31B uses tensor parallelism because every layer needs
both GPUs. Nemotron's 30B MoE uses pipeline parallelism because its
expert layers can be stacked. The result: Gemma gets maximum compute
throughput, Nemotron gets maximum context depth. Both optimized for
L4 hardware without NVLink."

## Measured data

| Metric | Gemma 4 31B (TP=2) | Nemotron 30B (PP=2) |
|---|---|---|
| VRAM per GPU | ~22 GB (95%) | ~21.5 + 22 GB |
| KV cache | 10,432 tokens | 1,257,472 tokens |
| max_model_len | 8,192 | 32,768 |
| max_tokens (harness) | 1,024 | 4,096 |
| Parallelism | Tensor (all-reduce/layer) | Pipeline (1 transfer/fwd) |
| Load time | ~170s | ~80s |
| Tool call parser | gemma4 | qwen3_coder + nano_v3 reasoning |
| Model family | Google (Gemma) | NVIDIA (Llama 3.3 derivative) |

## Performance comparison (TODO)

Need to measure and compare:
- Time to first token (TTFT) — PP should be faster (no all-reduce latency)
- Token throughput (tok/s) — TP might be faster (both GPUs compute simultaneously)
- Total iteration time — Auditor turn vs Architect turn
- Token cost per iteration — which model uses more tokens for the same decision

These measurements will be captured during the next full run and
displayed on the frontend dashboard.
