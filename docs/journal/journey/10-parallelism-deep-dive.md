---
id: journey-10-parallelism-deep-dive
type: journey
title: "Journey: The Parallelism Deep Dive — Why TP vs PP Matters More at the Edge Than Anywhere Else"
date: 2026-04-10
tags: [L3-model, parallelism, discovery, edge-deployment]
related:
  - journey/09-tp-vs-pp
  - journey/12-bf16-tp4-full-precision
one_line: "On data center GPUs with NVLink, the parallelism strategy is a minor optimization; on L4s with PCIe it is the difference between 14 tok/s and 70 tok/s — and it is determined by the model architecture, not operator choice."
---

# Journey: The Parallelism Deep Dive — Why TP vs PP Matters More at the Edge Than Anywhere Else

## The story in one sentence
On data center GPUs with NVLink, the parallelism strategy is a minor
optimization; on L4s with PCIe, it's the difference between 14 tok/s
and 70 tok/s — and it's determined by the model architecture, not
the operator's choice.

## The constraint: PCIe Gen4 without NVLink

The Dell XR7620's 4× NVIDIA L4 GPUs are connected only via PCIe Gen4
(~32 GB/s per direction). There is no NVLink (which provides 600+
GB/s on data center platforms like DGX). This isn't a deficiency —
NVLink doesn't exist in the ruggedized 2U form factor. It's a
physical constraint of the tactical edge.

Every multi-GPU inference strategy requires inter-GPU communication.
The question is: how MUCH communication, and how OFTEN?

## Tensor Parallelism: the all-reduce tax

**What it does:** Splits each layer's weight matrices across GPUs.
Both GPUs participate in EVERY layer simultaneously. After each
layer's matrix multiply, an **all-reduce** operation synchronizes
the results across GPUs before the next layer can start.

**For a 60-layer model: 60 all-reduce operations per forward pass.**

On NVLink (600 GB/s): each all-reduce takes ~microseconds. Total
overhead: negligible. This is why every data center recipe defaults
to TP.

On PCIe Gen4 (32 GB/s): each all-reduce takes ~milliseconds. 60 of
them per forward pass adds up. For Gemma 4 31B NVFP4, we measured
**14 tok/s** — the PCIe all-reduce is the dominant bottleneck.

**Why Gemma 4 is stuck on TP:**
1. It's a dense model — every layer needs the full weight tensor.
   You can't split layers, you MUST split matrices.
2. It's multimodal (vision + audio + text) — the `IntermediateTensors`
   that PP needs to pass between stages include cross-attention
   outputs from the vision/audio encoders. vLLM's Gemma 4
   implementation doesn't handle this.
3. We tested PP=2 — crashed with `IntermediateTensors` attribute
   error. Not a simple fix.

## Pipeline Parallelism: the layer-stacking win

**What it does:** Assigns different layers to different GPUs. Data
flows sequentially: GPU 0 processes layers 0-29, sends the result
to GPU 1, which processes layers 30-59.

**For any model: 1 activation transfer per forward pass.**

On PCIe Gen4: one transfer of ~tens of MB takes ~1ms. Compared to
60 all-reduces, this is almost free.

**Why Nemotron works with PP:**
1. It's an MoE — expert layers are self-contained blocks with
   natural split points. No cross-attention between stages.
2. It's text-only — no vision/audio encoders to route around
   pipeline stage boundaries.
3. The Marlin NVFP4 kernel works per-layer (PP) but fails when
   splitting expert weight matrices across GPUs (TP) — we got
   `size_n = 5152 is not divisible by tile_n_size = 64` with TP.

## The measured impact

| Metric | Gemma 4 31B (TP=2) | Nemotron 30B (PP=2) | Factor |
|---|---|---|---|
| Tokens/sec | ~14 | ~52-70 | **4-5×** |
| Time to first token | 10-20s | 2-8s | **3-5×** |
| KV cache capacity | 10,432 tokens | 1,257,472 tokens | **120×** |
| Max context | 8,192 tokens | 32,768 tokens | **4×** |
| Inter-GPU transfers/fwd | 60 all-reduces | 1 activation | **60×fewer** |
| Auditor max_tokens | 1,024 | 4,096 | **4×** |

The 120× KV cache difference is the most surprising finding. It's a
direct consequence of the weight distribution:
- **TP**: both GPUs hold ALL layers (split per dimension) → ~22 GB
  per GPU, leaving ~600 MiB for KV cache
- **PP**: each GPU holds HALF the layers → ~11 GB per GPU, leaving
  ~10 GB for KV cache

Same total model, same total VRAM, same GPUs — but PP distributes
the weight memory burden while TP duplicates it.

## Why this matters for the demo

The cross-model architecture isn't just about different perspectives
for evaluation. It's about **matching the parallelism strategy to the
model architecture on edge hardware**:

- Gemma 4 (dense, multimodal) → TP is the only option → pays the
  PCIe tax → 14 tok/s → strong reasoning at slower throughput
- Nemotron (MoE, text-only) → PP works → minimal PCIe overhead →
  70 tok/s → fast evaluation with deep reasoning budget

The Architect needs quality (long-form planning, broad knowledge).
The Auditor needs speed (multiple tool calls per turn, deep evaluation
with large context). The parallelism strategies deliver exactly what
each role needs.

## The future: what changes

### Triton 26.04 (expected late April 2026)
- Fixes the vLLM backend version gap (Gemma 4 loads in Triton)
- Restores EXPLICIT model control mode (dynamic load/unload)
- Does NOT change the TP-only constraint for Gemma 4 via vLLM

### TensorRT-LLM + Gemma 4 (unknown timeline)
- NVIDIA's own inference engine with native PP support
- If Gemma 4 is added with PP, the same XR7620 could see Gemma 4
  jump from 14 tok/s (vLLM TP) to potentially 50+ tok/s
  (TensorRT-LLM PP) — with NVIDIA's kernel optimizations on top
- Known caveat: TensorRT-LLM has issues with PP + NVFP4 for some
  model families (Llama 3.x, Llama 4). Would need verification.
- The "buy the hardware now, the software keeps getting faster"
  story for Federal procurement

### vLLM PP support for Gemma 4 (requires implementation)
- Someone needs to implement `get_intermediate_tensors()` and
  `set_intermediate_tensors()` on `Gemma4ForConditionalGeneration`
- Non-trivial because Gemma 4 is multimodal — the intermediate
  tensors include cross-attention outputs from vision/audio encoders
- No open issues or PRs as of April 10, 2026
- If/when this lands, same container image, same weights, same
  command — just add `--pipeline-parallel-size 2` and throughput
  potentially 4-5× faster

## The architectural lesson

On edge hardware without NVLink:

1. **Dense models pay a PCIe tax proportional to their depth.**
   60 layers × all-reduce per layer = significant overhead at 32 GB/s.

2. **MoE models dodge the tax because their expert layers have
   natural pipeline split points.** One activation transfer per
   forward pass regardless of depth.

3. **The parallelism strategy is not a configuration knob — it's
   an architectural constraint** determined by the model family,
   the quantization format, and the inference engine's implementation.

4. **VRAM distribution matters as much as VRAM total.** PP puts
   half the weights per GPU, leaving room for KV cache. TP puts
   all weights on all GPUs (split per dimension), leaving almost
   nothing for KV cache.

5. **The right answer is model-specific.** Running the same
   parallelism for every model is like using the same gear for
   every road — you CAN, but you pay a performance price that
   matters at the edge where every watt and every token counts.

## Key artifacts

- `docs/whitepaper/journey/09-tp-vs-pp.md` — the earlier TP vs PP comparison
- `infra/vllm/systemd/gemma-forge-architect.service` — TP=2 config
- `infra/vllm/systemd/gemma-forge-auditor.service` — PP=2 config
- `infra/vllm/scripts/serve-auditor.sh` — PP=2 runner script
- Measured data in `runs/run-20260410-180020.jsonl` — the enriched
  run with per-turn token throughput and TTFT
- Memory: `project_frontend_design.md` — dashboard should show
  TP vs PP performance comparison
