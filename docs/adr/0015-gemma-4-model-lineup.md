# ADR-0015: Gemma 4 model lineup — NVFP4 31B + edge models on 4× L4

- **Status:** Accepted (revised 2026-04-09 — NVFP4 pivot based on measured data)
- **Date:** 2026-04-09
- **Deciders:** Ken Rollins
- **Related:** [ADR-0013](0013-one-triton-per-l4-no-nvlink.md), [ADR-0014](0014-triton-vllm-director-shared-host-service.md)

## Context

The Ralph loop has four agent roles (Architect, Worker, Auditor,
Sentry) running on a Dell XR7620 with 4× NVIDIA L4 24GB GPUs
(**no NVLink**). The
[Gemma 4 release](https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/)
ships four variants:

- **Gemma 4 31B Dense (31B-IT)** — flagship dense model
- **Gemma 4 26B MoE** — mixture-of-experts variant
- **Gemma 4 E4B** — "effective 4B" edge variant (8B total params)
- **Gemma 4 E2B** — "effective 2B" edge variant (5.1B total params)

We have to assign models to roles and to GPUs in a way that:

1. Fits the 4× L4 (24 GB each) GPU budget — the binding constraint.
2. Preserves reasoning quality for the Architect role, which plans
   STIG remediations and drives the Ralph loop's decision-making.
3. Preserves the demo's narrative coherence: distinct roles, distinct
   responsibilities, recognizable on the dashboard.

### What we measured (Phase 1 validation, 2026-04-09)

The original plan was to follow the official vLLM Gemma 4 recipe
verbatim, which specifies `tensor_parallel_size=2` for the bf16
31B-IT. **Testing on the actual L4 hardware revealed this doesn't
work:**

| Configuration | VRAM per GPU | Fits on L4 (24 GB)? | Result |
|---|---|---|---|
| 31B bf16, tp=2 | ~31 GB | **No** (62 GB / 2 > 24 GB) | OOM |
| 31B bf16, pp=2 | ~31 GB | **No** | OOM |
| 31B NVFP4, tp=1 | ~22 GB | **No** (no room for KV cache) | OOM |
| **31B NVFP4, tp=2** | **~11 GB** | **Yes** (13 GB headroom) | **Working — 15.1 tok/s** |
| 31B NVFP4, pp=2 | — | — | vLLM bug (`IntermediateTensors`) |
| E4B bf16, tp=1 | ~16 GB | Yes | Working |
| E2B bf16, tp=1 | ~10 GB | Yes | Working — verified |

The official vLLM recipe's `tp=2` for bf16 was written for A100/H100
(80 GB per GPU). On L4 (24 GB per GPU), even bf16 with tp=2 doesn't
fit because 62 GB / 2 = 31 GB > 24 GB.

**NVIDIA's NVFP4 quantization** (`nvidia/Gemma-4-31B-IT-NVFP4`,
produced by modelopt v0.37.0) reduces the in-VRAM footprint from
62 GB to ~22 GB by quantizing MLP/FFN layers to FP4 while **keeping
all 60 self-attention layers at full bf16 precision**. This quality-
preserving recipe was confirmed to produce Architect-grade STIG
remediation plans in testing (structured, accurate, included proper
backup/rollback steps, knew FIPS-validated algorithms by name).

With tp=2, the 22 GB model splits to ~11 GB per GPU, leaving ~13 GB
headroom per L4 for KV cache. **Quantization is the enabling
technology** that makes multi-agent inference possible on 4× L4
without consuming all four GPUs for a single model.

## Decision

| GPU(s) | Role | Model | TP | Measured VRAM | Measured throughput |
|---|---|---|---|---|---|
| **0+1** | **Architect + Worker** (shared engine) | `nvidia/Gemma-4-31B-IT-NVFP4` | 2 | 21.9 GB/GPU | 15.1 tok/s |
| **2** | **Auditor** | `google/gemma-4-E4B-it` (bf16) | 1 | ~16 GB | TBD |
| **3** | **Sentry** | `google/gemma-4-E2B-it` (bf16) | 1 | 20.6 GB | Fast (sub-second for 106 tok) |

Architect and Worker share a single vLLM engine because they execute
**sequentially** within a Ralph loop iteration (Architect drafts →
Worker applies → Auditor validates → repeat). Sharing one engine for
two roles is correct, not a compromise.

### Container image

All models are served via `gemma-forge/vllm:latest`, a derived image
built from `vllm/vllm-openai:latest` (vLLM 0.19.0) with
`transformers>=4.58` baked in (required for Gemma 4 `gemma4` model
type recognition). See `infra/vllm/Dockerfile`.

### Why NVFP4 and not bf16

On this hardware, quantization is not optional:

- **bf16 31B doesn't fit on 2 L4s** (31 GB/GPU > 24 GB).
- **bf16 31B requires tp=4** (all 4 GPUs) — leaving nothing for
  E4B/E2B.
- **NVFP4 31B fits on 2 L4s** with tp=2 (11 GB/GPU + 13 GB headroom).
- **Quality is preserved** because NVIDIA's NVFP4 recipe keeps
  self-attention at full precision. Tested: produced structured,
  accurate STIG remediation plans with proper reasoning chains.

### Why tp=2 and not pp=2

- **Pipeline parallelism (pp=2)** was tested. vLLM 0.19.0 has an
  `IntermediateTensors` compatibility bug with the Gemma 4
  architecture — pp=2 crashes during model initialization.
- **Tensor parallelism (tp=2)** works correctly at 15.1 tok/s. On
  non-NVLink L4s, the PCIe all-reduce overhead is present (~60
  all-reduce operations per forward pass over PCIe Gen4 ~32 GB/s)
  but bounded. 15.1 tok/s is 3× faster than human reading speed —
  suitable for interactive agent workflows and live demos.
- Future vLLM releases may fix the PP path, which would reduce
  inter-GPU communication to a single activation transfer per
  forward pass. When that happens, we should re-benchmark.

## Alternatives considered

- **bf16 31B-IT with tp=2 (the official vLLM recipe verbatim)** —
  The original plan. Rejected after testing: 62 GB at bf16 split
  across 2× 24 GB L4s = 31 GB per GPU, OOM. The recipe was written
  for A100/H100 (80 GB per GPU), not L4.

- **bf16 31B-IT with tp=4 (all 4 GPUs)** — Would work (~15.5 GB
  per GPU) but consumes the entire GPU budget for one model. E4B
  and E2B would have no GPU to run on. Defeats the multi-agent
  story.

- **NVFP4 31B-IT on a single L4 (tp=1)** — Tested. OOM. The
  NVFP4 model is 22 GB in VRAM (attention layers stay bf16),
  which fills the L4 completely with no room for KV cache.

- **Gemma 4 26B MoE for Architect/Worker** — 26B total parameters
  = ~52 GB at bf16, also requires multi-GPU parallelism. No
  advantage over the 31B Dense on this hardware, and the Dense
  model has stronger single-stream reasoning performance.

- **E4B for all roles (skip 31B entirely)** — Would simplify
  Phase 1 enormously. Rejected because the 31B is the model that
  actually demonstrates the XR7620's heavyweight inference
  capability. The demo loses its main hardware story without it.

- **GPTQ/AWQ 4-bit community quant (attention also quantized)** —
  Would likely fit the 31B on a single L4 (~15 GB). Rejected
  because (a) no NVIDIA-official variant exists, (b) quantizing
  attention degrades reasoning quality — the one thing the
  Architect role depends on, and (c) NVIDIA's NVFP4 is a
  vendor-published, modelopt-produced variant that's defensible
  to Federal evaluators.

## Consequences

### Positive

- **Works on the actual hardware.** Measured, not assumed.
- **Quality preserved where it matters.** NVFP4 keeps attention at
  full precision; tested STIG remediation output is Architect-grade.
- **Honest hardware story.** The dashboard shows a wide NVFP4 31B
  engine spanning two L4s plus two compact edge models — an accurate
  picture of what real edge inference looks like on this form factor.
- **Two-generation hardware story for the whitepaper.** On L4: NVFP4
  enables multi-agent inference within the GPU budget. On future
  GB10/Blackwell: the same NVFP4 model gets native FP4 tensor-core
  acceleration and fits on a single GPU with 100+ GB headroom. Zero
  code changes required.
- **NVIDIA-vendor-backed quantization.** `nvidia/Gemma-4-31B-IT-NVFP4`
  produced by modelopt v0.37.0 — defensible to Federal evaluators
  as a vendor-supplied optimization, not a community hack.
- **Sequentially-shared engine matches loop semantics.** Architect
  and Worker never contend for the GPU simultaneously.

### Negative / accepted trade-offs

- **15.1 tok/s is slower than bf16 on larger GPUs.** This is the
  PCIe all-reduce overhead from tp=2 on non-NVLink hardware, not
  a quantization penalty. Acceptable for the demo: tokens appear 3×
  faster than human reading speed.
- **Architect and Worker visually appear as the same model on the
  dashboard.** Mitigated by labeling them by *role* (distinct system
  prompts). The dashboard's thought-stream component shows the
  active role for each request.
- **NVFP4 is marked "experimental" by vLLM.** The log line
  `WARNING: Detected ModelOpt NVFP4 checkpoint. Please note that
  the format is experimental and could change in future.` We
  accept this because (a) the output quality is verified, (b) the
  model is NVIDIA-published, and (c) the warning refers to the
  serialization format, not the inference quality.
- **21.9 GB / 23.0 GB per GPU is tight.** Little headroom for
  increasing `max_model_len` beyond 4096 on the 31B engine. For
  longer context: either reduce `gpu_memory_utilization` (less KV
  cache), or quantize KV cache more aggressively (already FP8),
  or wait for more VRAM on future hardware.

## References

- [Gemma 4 release blog](https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/)
- [vLLM Gemma 4 recipe](https://docs.vllm.ai/projects/recipes/en/latest/Google/Gemma4.html)
- [nvidia/Gemma-4-31B-IT-NVFP4 on HuggingFace](https://huggingface.co/nvidia/Gemma-4-31B-IT-NVFP4)
- [vLLM blog: Announcing Gemma 4](https://vllm.ai/blog/gemma4)
- ADR-0013: One Triton process per L4
- ADR-0014: Triton-managed vLLM director (shared host service)
- `docs/whitepaper/notes.md` — full measured results from Phase 1 validation
