---
id: journey-01-inference-layer
type: journey
title: "The Inference Layer Evolution"
date: 2026-04-09
tags: [L3-model, decision, supply-chain]
related:
  - journey/02-model-strategy
  - gotchas/triton-vllm-version
  - gotchas/transformers-gemma4
  - gotchas/vllm-tool-call-parser
one_line: "We started with Triton Inference Server for dynamic model management, hit a Gemma 4 version gap, pivoted to plain vLLM containers, and ended with a cleaner operational model that's ready for Triton when it catches up."
---

# Journey: The Inference Layer Evolution

## The story in one sentence
We started with Triton Inference Server for dynamic model management,
discovered it couldn't run Gemma 4 yet, pivoted to plain vLLM containers,
and ended up with a cleaner operational model that's ready for Triton
when it catches up.

## What we planned (ADR-0001, ADR-0014)

The original PRD specified Ollama. During the interview phase with Ken,
we rejected Ollama (not production-grade for multi-GPU serving) and
picked **vLLM** as the inference engine. This was validated: vLLM has
Day-0 Gemma 4 support, is Apache-2, air-gappable, and is what Red Hat
ships as RHAIIS.

Then Ken raised a critical insight: *"I don't like vLLM because it
doesn't support dynamic loading and unloading of models."* He wanted
the XR7620 to be a multi-demo host where different demos load different
model sets without redeploying containers.

This led to the **Triton Inference Server with EXPLICIT model control
mode** architecture (ADR-0014). Triton wraps vLLM as a backend and adds:
- Runtime model load/unload via REST API
- A shared model catalog (`/data/triton/models/`)
- systemd-managed processes, one per GPU (ADR-0013)
- The "watch the L4s warm up" demo theater

We researched this thoroughly. A subagent verified:
- Triton vLLM backend exists, maintained by NVIDIA
- EXPLICIT mode mechanics (POST /v2/repository/models/<name>/load)
- Multi-GPU best practice: one Triton per GPU (forced by GitHub #7786)
- The vLLM backend supports Gemma 4 by `pip install -U vllm` inside
  the container

## What went wrong

**Triton 26.03 (released 2026-03-27) ships vLLM 0.17.1.** Gemma 4
released April 2 — five days AFTER the Triton cut. The model type
`gemma4` is not in transformers 4.57.6 or vLLM 0.17.1.

We tried upgrading vLLM inside the Triton container:
- `pip install -U vllm` → installed vLLM 0.19.0
- But this broke the Triton vLLM backend code:
  `ModuleNotFoundError: No module named 'vllm.inputs.data'`
- The backend at `/opt/tritonserver/backends/vllm/utils/request.py`
  imports internal vLLM modules that were reorganized between 0.17.1
  and 0.19.0
- Upgrading vLLM without upgrading the Triton backend code = broken

We also discovered that `transformers>=4.58` is needed for the `gemma4`
model type. The stock transformers 4.57.6 in both Triton and
vllm-openai containers doesn't have it.

## What we decided

**Pivot to plain `vllm/vllm-openai` containers** for serving, with a
custom `gemma-forge/vllm:latest` Dockerfile that bakes in
`transformers>=4.58`.

The Triton infrastructure (systemd units, model repository layout,
install scripts) stays in the repo as scaffolding for when Triton 26.04
ships. The harness code talks the OpenAI Chat Completions API, which
is the same whether Triton or vLLM is behind the endpoint — so the
swap is transparent.

Three role-named systemd units were created:
- `gemma-forge-architect.service` — 31B NVFP4 tp=2, GPUs 0+1, port 8050
- `gemma-forge-auditor.service` — E4B bf16, GPU 2, port 8060
- `gemma-forge-sentry.service` — E2B bf16, GPU 3, port 8070

Makefile targets: `make demo-up` / `make demo-down` / `make demo-status`

Verified end-to-end: all 3 endpoints responding, all 4 GPUs loaded,
`make demo-down` frees all GPUs to 0 MiB, 29 existing Docker containers
untouched.

## What we learned

1. **"Day-0 model support" from one vendor does not guarantee Day-0
   support from the rest of the stack.** The model, the inference
   engine, the model-config library (transformers), and the serving
   orchestrator (Triton) are four independent release trains. Sovereign
   edge operators must treat them as such.

2. **The "shared host service" pattern survived the pivot.** Even
   though we switched from Triton to plain vLLM, the concept of
   `/data/triton/` as a host-level model catalog, systemd-managed
   inference services, and `make demo-up/down` as the operational
   interface — all of that transferred cleanly.

3. **Triton's EXPLICIT mode is the right long-term architecture.**
   When 26.04 ships, we swap the systemd units back and gain runtime
   model swap via API with zero harness code changes. The pivot was
   tactical, not strategic.

## Key artifacts

- ADR-0001 (superseded by ADR-0014) — why vLLM as the engine
- ADR-0014 — Triton as shared host service (the target architecture)
- `infra/vllm/Dockerfile` — the production image with transformers fix
- `infra/vllm/systemd/gemma-forge-*.service` — role-named units
- `infra/vllm/scripts/serve.sh` — generic vLLM serve wrapper
- Memory: `project_triton_version_gap.md` — tracks the blocker
