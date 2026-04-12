---
id: gotcha-transformers-gemma4
type: gotcha
title: "Gotcha: transformers < 4.58 doesn't recognize the gemma4 model type"
date: 2026-04-09
tags: [L3-model, discovery, supply-chain]
related:
  - journey/01-inference-layer
  - gotchas/triton-vllm-version
one_line: "transformers 4.57.6 does not recognize the gemma4 model type. 4.58+ is required. Bake it into the inference container image."
---

# Gotcha: transformers < 4.58 doesn't recognize the gemma4 model type

## Symptom
```
pydantic_core._pydantic_core.ValidationError: 1 validation error for ModelConfig
Value error, The checkpoint you are trying to load has model type
`gemma4` but Transformers does not recognize this architecture.
```

## Root cause
Gemma 4 introduced a new model type `gemma4` with architecture class
`Gemma4ForConditionalGeneration`. This architecture is NOT in
`transformers==4.57.6` (the version bundled in both the Triton 26.03
and vllm-openai:latest containers at the time of our testing).

The `auto_map` field in the model's `config.json` is empty — meaning
the model does NOT ship custom code on HuggingFace. It expects
transformers itself to have the architecture class built in.

## Fix
```bash
pip install --no-cache-dir 'transformers>=4.58'
```

For production: bake it into the Dockerfile:
```dockerfile
FROM vllm/vllm-openai:latest
RUN pip install --no-cache-dir 'transformers>=4.58'
```

## Key detail
This is separate from the vLLM version issue. vLLM 0.19.0 has its own
internal model implementation for Gemma 4, but it delegates config
parsing to transformers' `AutoConfig` / `PretrainedConfig` path. If
transformers can't recognize the `model_type`, vLLM fails during
config validation before it ever gets to its own model loading code.

## How to prevent
When a new model releases, check both:
1. Does vLLM support it? (check `vllm/model_executor/models/`)
2. Does the pinned `transformers` version in the container have the
   architecture class? (check `transformers.models`)

If #2 fails, you need a newer transformers. This is the "four
independent release trains" problem from the infrastructure gap
analysis.

## Environment
- Gemma 4 released 2026-04-02
- transformers 4.57.6 — does NOT have gemma4
- transformers 4.58+ (or 5.x dev) — HAS gemma4
- Tested 2026-04-09 (one week after model release)
