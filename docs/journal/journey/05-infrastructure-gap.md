---
id: journey-05-infrastructure-gap
type: journey
title: "The Infrastructure Gap — Model Release ≠ Infrastructure Readiness"
date: 2026-04-09
tags: [L3-model, discovery, supply-chain]
related:
  - journey/01-inference-layer
  - gotchas/triton-vllm-version
  - gotchas/transformers-gemma4
one_line: "A model release does not mean the surrounding inference infrastructure is ready on the same day — a structural property of open-source AI infrastructure that matters at the edge more than in the cloud."
---

# The Infrastructure Gap — Model Release ≠ Infrastructure Readiness

## The insight

One of the most practical findings from Phase 1 is that a model release
does not mean the surrounding inference infrastructure is ready on the
same day. This is a structural property of open-source AI infrastructure,
not a criticism of any vendor.

## The specific gap I hit

Gemma 4 released **2026-04-02**. I started building **2026-04-08**.
One week after release:

| Component | Status | Gap |
|---|---|---|
| **vLLM engine** | Day-0 support in v0.19.0 ✓ | None |
| **HuggingFace transformers** | `gemma4` model type NOT in 4.57.6 (the current PyPI release) | Requires `>=4.58` or install from git |
| **NVIDIA Triton Inference Server** | vLLM backend in 26.03 built against vLLM 0.17.1; upgrading breaks the backend | Blocked until Triton 26.04 (late April) |
| **NVIDIA Gemma 4 NVFP4 quantization** | Published on HuggingFace ✓ | None — NVIDIA was ready Day-0 |
| **HuggingFace model gating** | No gate — Apache 2.0, free download ✓ | None — policy change from Gemma 1-3 |

The model itself worked. The engine that runs it worked. But the
ecosystem around it — the model-config library, the serving
orchestrator, the container images — lagged by days to weeks.

## Why this matters for edge deployments

Air-gapped and sovereign edge deployments must plan for this gap because:

1. **They can't just `pip install` from PyPI on a classified network.**
   Every dependency must be pre-staged and validated. If the validated
   transformers version is 4.57.6 and the model needs 4.58, the
   model is unusable until the next validation cycle.

2. **They pin infrastructure versions for stability.** A Federal site
   running Triton 26.03 won't upgrade to 26.04 on Day-0 — they'll
   wait for their own validation, which might be 26.06 or later.

3. **The gap is structural, not accidental.** The model, the inference
   engine, the model-config library, and the serving orchestrator are
   four independent release trains maintained by four different teams
   (Google DeepMind, vLLM project, HuggingFace, NVIDIA). Synchronizing
   them is nobody's job.

## How gemma-forge handles it

The approach: **maintain the ability to compose components at different
release cadences** rather than pinning to a single vendor's stack.

- `gemma-forge/vllm:latest` is a derived Dockerfile that decouples the
  transformers version from the vLLM container version
- The harness talks the OpenAI-compatible API, which is stable across
  vLLM versions and will be the same when Triton catches up
- The systemd units are swappable between vLLM-direct and Triton without
  touching the harness code
- Model weights are stored in a host-level catalog (`/data/triton/weights/`)
  that outlives any single container version

## Key artifacts

- `docs/whitepaper/notes.md` → "The infrastructure gap" section
- ADR-0014 → documents the Triton gap and the workaround
- Memory: `project_triton_version_gap.md`
- `infra/vllm/Dockerfile` → the derived image that bridges the gap
