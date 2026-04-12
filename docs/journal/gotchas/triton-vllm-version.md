---
id: gotcha-triton-vllm-version
type: gotcha
title: "Gotcha: Triton 26.03 vLLM backend incompatible with Gemma 4"
date: 2026-04-09
tags: [L3-model, discovery, supply-chain]
related:
  - journey/01-inference-layer
  - gotchas/transformers-gemma4
one_line: "Triton 26.03 ships vLLM 0.17.1; Gemma 4 needs 0.19.0. Upgrading vLLM inside the Triton container breaks the Triton vLLM backend because the backend code imports vllm internals that were reorganized between versions."
---

# Gotcha: Triton 26.03 vLLM backend incompatible with Gemma 4

## Symptom
Loading a Gemma 4 model in Triton 26.03 with EXPLICIT mode:

First error (before vLLM upgrade):
```
The checkpoint you are trying to load has model type `gemma4` but
Transformers does not recognize this architecture.
```

Second error (after `pip install -U vllm` inside the container):
```
ModuleNotFoundError: No module named 'vllm.inputs.data'
```

## Root cause
Triton 26.03 was released **2026-03-27**, five days before Gemma 4
(2026-04-02). It ships vLLM 0.17.1 which doesn't know `gemma4`.

Upgrading vLLM to 0.19.0 inside the container gives Gemma 4 support
but **breaks the Triton vLLM backend code**. The backend at
`/opt/tritonserver/backends/vllm/utils/request.py` imports
`vllm.inputs.data` which was reorganized in the vLLM 0.17→0.19 jump.

This is a non-trivial version gap: you can't just `pip install -U vllm`
because the Triton backend code is pinned to the old API.

## Fix
**Use the `vllm/vllm-openai` container directly** instead of Triton.
Build a derived image that bakes in `transformers>=4.58`:

```dockerfile
FROM vllm/vllm-openai:latest
RUN pip install --no-cache-dir 'transformers>=4.58'
```

The Triton infrastructure stays in the repo as scaffolding. When Triton
26.04 ships (expected late April 2026), swap the systemd units back.
The harness code talks the OpenAI-compatible API regardless.

## How to prevent
- Never `pip install -U vllm` inside a Triton container without
  checking that the backend code is compatible
- Check `gh api repos/triton-inference-server/server/releases --jq '.[0].name'`
  before starting Triton-related work in a new session
- Pin container image tags explicitly; don't use `latest` for Triton

## Environment
- Triton 26.03 (v2.67.0, NGC container nvcr.io/nvidia/tritonserver:26.03-vllm-python-py3)
- vLLM 0.17.1 (bundled) → 0.19.0 (upgraded, breaks backend)
- transformers 4.57.5 → 4.57.6 (still too old for gemma4)
