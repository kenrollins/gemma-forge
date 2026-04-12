---
id: journey-02-model-strategy
type: journey
title: "Journey: The Model Strategy — bf16, NVFP4, and the VRAM Reality"
date: 2026-04-09
tags: [L3-model, quantization, decision]
related:
  - journey/01-inference-layer
  - journey/08-model-architecture-revision
  - gotchas/nvfp4-vram-math
one_line: "We tried four different configurations of the Gemma 4 31B model on L4 GPUs before finding the one that actually works, and the answer turned out to be both the most practical AND the most compelling."
---

# Journey: The Model Strategy — bf16, NVFP4, and the VRAM Reality

## The story in one sentence
We tried four different configurations of the Gemma 4 31B model on
L4 GPUs before finding the one that actually works, and the answer
(NVFP4 + tp=2) turned out to be both the most practical AND the
most compelling demo story.

## What we planned (ADR-0015 original)

The original model lineup followed the official vLLM Gemma 4 recipe
verbatim:
- Architect + Worker: Gemma 4 31B-IT, bf16, tensor_parallel_size=2
- Auditor: Gemma 4 E4B, bf16
- Sentry: Gemma 4 E2B, bf16

The recipe says tp=2 for the 31B. We assumed this meant 2 L4s.

## The VRAM reality

**The recipe was written for A100/H100 (80 GB per GPU).** On L4 (24 GB
per GPU):

| Config | VRAM per GPU | Fits on L4? | Result |
|---|---|---|---|
| 31B bf16, tp=2 | ~31 GB | **No** (31 > 24) | OOM |
| 31B bf16, pp=2 | ~31 GB | **No** | OOM |
| 31B bf16, tp=4 | ~15.5 GB | Yes but uses ALL GPUs | No room for E4B/E2B |
| 31B NVFP4, tp=1 | ~22 GB | **No** (fills L4, no KV cache) | OOM |
| **31B NVFP4, tp=2** | **~11 GB** | **Yes, 13 GB headroom** | **15.1 tok/s** |

The bf16 31B is 62 GB. Even split across 2 L4s (48 GB combined),
31 GB per GPU > 24 GB capacity. The official recipe simply doesn't
work on L4 hardware.

## The NVFP4 discovery

Ken asked about FP4 quantization: *"I didn't realize NVFP4 could
actually run on my L4s."*

NVIDIA published `nvidia/Gemma-4-31B-IT-NVFP4` on HuggingFace,
produced by modelopt v0.37.0. Key findings:

1. **NVIDIA's NVFP4 recipe keeps ALL 60 self-attention layers at
   full bf16 precision.** Only MLP/FFN layers are quantized to FP4.
   This is a quality-preserving recipe — attention is where reasoning
   chains live.

2. **On-disk: 31 GB. In-VRAM: 22 GB.** Not the naive 15.5 GB estimate
   (31B × 4 bits / 8). The full-precision attention is the difference.

3. **On a single L4: OOM.** 22 GB model fills the 24 GB GPU completely,
   leaving nothing for KV cache.

4. **With tp=2: 11 GB per GPU, 13 GB headroom.** This is the sweet spot.

## The quality test

We tested the NVFP4 31B with a STIG remediation prompt (V-257844: SSH
FIPS key exchange). The model produced:
- A structured remediation plan with backup, fix, rollback, validation
- Knew FIPS-validated key exchange algorithms by name
- Mentioned `fips-mode-setup --check` as a precondition
- Included `sshd -t` for config syntax validation
- Provided the exact `ssh -vv` validation command

**Architect-grade reasoning from a quantized model.** The NVFP4 recipe's
attention-preservation strategy works.

## TP vs PP comparison

Ken asked: *"are we doing the simple stretching of an LLM between two
L4s, or the thing where so many layers get loaded on the first GPU and
the other layers get loaded on the second?"*

- **Tensor Parallelism (TP)**: every layer split across both GPUs,
  60 all-reduce operations per forward pass over PCIe
- **Pipeline Parallelism (PP)**: layers stacked, 1 transfer per forward pass

On non-NVLink L4s, PP should be better (less PCIe traffic). But:

**PP=2 crashed** with `IntermediateTensors` compatibility bug in vLLM
0.19.0's Gemma 4 implementation. TP=2 works correctly at 15.1 tok/s.

The 15.1 tok/s throughput is 3× faster than human reading speed —
suitable for interactive agent workflows and live demos.

## The whitepaper story

This journey produces three valuable narratives:

1. **"Quantization is the enabling technology on edge hardware."**
   Without NVFP4, the 31B needs all 4 GPUs (tp=4) and there's no room
   for the edge models. With NVFP4, it fits on 2 GPUs with generous
   headroom. The quantization makes multi-agent inference POSSIBLE.

2. **"On future hardware (GB10/Blackwell), the same model gets native
   FP4 tensor-core acceleration with zero code changes."** Two-generation
   hardware story.

3. **"We measured, we tested, we chose based on data."** The
   Federal-credibility argument. Not "we picked what's popular" but
   "we tested four configurations on the actual hardware and selected
   the one that works."

## Measured results

- **31B NVFP4, tp=2**: 15.1 tok/s, 21.9 GB/GPU, 180s cold load
- **E4B bf16**: 19-26 tok/s, 20.5 GB, 150s cold load
- **E2B bf16**: fast (sub-second for 106 tokens), 20.6 GB, 90s cold load
- **31B bf16, tp=2**: OOM (31 GB/GPU > 24 GB L4 capacity)
- **31B NVFP4, tp=1**: OOM (22 GB model, no room for KV cache)
- **31B NVFP4, pp=2**: vLLM bug (IntermediateTensors)

## Key artifacts

- ADR-0015 (revised) — the full model lineup with measured data
- `docs/whitepaper/notes.md` — raw measured results section
- `nvidia/Gemma-4-31B-IT-NVFP4` — the NVIDIA-published model we use
- Memory: `project_triton_version_gap.md` — context for future sessions
