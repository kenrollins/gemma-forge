---
id: gotcha-nvfp4-vram-math
type: gotcha
title: "Gotcha: NVFP4 31B is 22 GB in VRAM, not the naive 15.5 GB estimate"
date: 2026-04-09
tags: [L3-model, quantization, discovery]
related:
  - journey/02-model-strategy
  - journey/12-bf16-tp4-full-precision
one_line: "NVFP4 31B is 22 GB in VRAM, not the naive 15.5 GB estimate. Attention layers stay in bf16 while MLP is quantized to FP4, and the actual memory footprint reflects that hybrid."
---

# Gotcha: NVFP4 31B is 22 GB in VRAM, not the naive 15.5 GB estimate

## Symptom
Attempting to load `nvidia/Gemma-4-31B-IT-NVFP4` on a single L4
(24 GB VRAM):
```
ERROR: Failed to load model - not enough GPU memory.
GPU 0 has a total capacity of 22.03 GiB of which 17.06 MiB is free.
Process has 22.01 GiB memory in use.
```

## Root cause
The naive estimate (31B × 4 bits / 8 bits per byte = ~15.5 GB) is
wrong because **NVIDIA's NVFP4 recipe does NOT quantize everything.**

Per the model's `config.json` → `quantization_config`:
- **Quantized (FP4):** MLP/FFN layers only (weights AND activations,
  group_size=16)
- **NOT quantized (stays bf16):** ALL 60 self-attention layers, the
  LM head, and the vision encoder

The self-attention layers account for roughly 30-40% of the model's
parameters. At bf16 (2 bytes per param), they contribute significantly
to the VRAM footprint. The actual in-VRAM usage:

| Component | Precision | Approx VRAM |
|---|---|---|
| MLP/FFN weights | FP4 | ~8-9 GB |
| Self-attention weights | bf16 | ~10-12 GB |
| LM head + embeddings | bf16 | ~1-2 GB |
| Runtime overhead | — | ~1 GB |
| **Total** | — | **~22 GB** |

This leaves zero room for KV cache on a 24 GB L4.

## Why NVIDIA did this
Keeping attention at full precision is a **quality-preserving** design
choice. Self-attention is where reasoning chains, long-range
dependencies, and contextual coherence live. Quantizing attention
degrades these capabilities more than quantizing the MLP layers.

We confirmed this empirically: the NVFP4 model produced Architect-grade
STIG remediation plans with proper reasoning chains, structured output,
and accurate domain knowledge.

## The fix
Use `tensor_parallel_size=2` with NVFP4. This splits the 22 GB across
2 L4s → ~11 GB per GPU → 13 GB headroom for KV cache. This is
actually the best configuration for L4 hardware: the quantization
makes it POSSIBLE to fit on 2 GPUs (bf16 at 62 GB doesn't fit even
on 2 L4s), while the tp=2 provides enough headroom for useful context.

## The lesson
Never estimate VRAM from model parameter count alone. Always check:
1. The `quantization_config` in `config.json` — what's actually quantized?
2. The `ignore` list — what stays at full precision?
3. Test empirically — VRAM usage after loading is the ground truth

## Environment
- Model: nvidia/Gemma-4-31B-IT-NVFP4 (modelopt v0.37.0)
- On-disk: 31 GB (4 safetensors shards)
- In-VRAM: 22 GB (measured via nvidia-smi)
- GPU: NVIDIA L4, 23,034 MiB (22.5 GiB)
