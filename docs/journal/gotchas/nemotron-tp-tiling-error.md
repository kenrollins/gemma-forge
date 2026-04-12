---
id: gotcha-nemotron-tp-tiling-error
type: gotcha
title: "Gotcha: Nemotron NVFP4 fails with tensor parallelism (Marlin kernel tiling)"
date: 2026-04-10
tags: [L3-model, parallelism, discovery]
related:
  - journey/09-tp-vs-pp
  - journey/08-model-architecture-revision
one_line: "Nemotron TP=2 fails with Marlin kernel tiling error (5152 not divisible by 64). PP=2 works around it — the dimension that kills TP is fine when the layers are stacked across GPUs."
---

# Gotcha: Nemotron NVFP4 fails with tensor parallelism (Marlin kernel tiling)

## Symptom
```
RuntimeError: size_n = 5152 is not divisible by tile_n_size = 64
```
When loading Nemotron-3-Nano-30B-A3B-NVFP4 with --tensor-parallel-size 2.

## Root cause
Tensor parallelism splits each weight matrix across GPUs by dividing
columns. For MoE models, the expert weight tensors have dimensions
determined by (expert_count × hidden_size / num_experts). When split
by TP=2, the resulting dimension (5152) is not divisible by the
Marlin NVFP4 kernel's tile size (64).

This is a quantization + MoE + TP interaction — three factors that
individually work but fail in combination. The Marlin kernel is
optimized for specific tile sizes and can't handle arbitrary matrix
dimensions after TP splitting.

## Fix
Use pipeline parallelism instead of tensor parallelism:
```
--pipeline-parallel-size 2  # instead of --tensor-parallel-size 2
```

PP doesn't split weight matrices — it assigns entire layers to
different GPUs. Each layer's matrices maintain their original
dimensions, so the Marlin kernel's tiling constraints are satisfied.

## Why this is actually better
PP on non-NVLink L4s gives 4-5× higher throughput than TP because:
- 1 activation transfer per forward pass vs 60+ all-reduces
- Each GPU holds half the weights → more VRAM for KV cache (120×)
- PCIe Gen4 (~32 GB/s) is the bottleneck; PP minimizes its usage

## Environment
- nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4
- vLLM 0.19.0
- NVIDIA L4, compute 8.9
- Marlin NVFP4 GEMM kernel
