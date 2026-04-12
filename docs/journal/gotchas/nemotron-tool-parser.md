---
id: gotcha-nemotron-tool-parser
type: gotcha
title: "Gotcha: Nemotron-3-Nano requires specific vLLM tool parser + reasoning plugin"
date: 2026-04-10
tags: [L3-model, tool-calling, discovery]
related:
  - journey/08-model-architecture-revision
one_line: "Nemotron tool calling needs --tool-call-parser qwen3_coder --reasoning-parser nano_v3 --reasoning-parser-plugin nano_v3_reasoning_parser.py. Hermes and llama3_json do not work."
---

# Gotcha: Nemotron-3-Nano requires specific vLLM tool parser + reasoning plugin

## Symptom
Model responds with text like "We need to verify system health. Let
me call check_health..." but never produces a structured `tool_calls`
response. `finish_reason: stop` or `finish_reason: length`, never
`finish_reason: tool_calls`.

## Root cause
Nemotron-3-Nano-30B uses a custom tool-calling format (not Llama-style
JSON, not Hermes-style, not OpenAI-style). It requires:

1. `--tool-call-parser qwen3_coder` — the tool call output format
2. `--reasoning-parser nano_v3` — the reasoning/thinking parser
3. `--reasoning-parser-plugin nano_v3_reasoning_parser.py` — a custom
   plugin shipped with the model weights on HuggingFace

Without all three, the model generates reasoning text that DESCRIBES
calling a tool but never produces the structured output the parser
expects.

## Fix
```bash
vllm serve /weights/Nemotron-3-Nano-30B-A3B-NVFP4 \
    --trust-remote-code \
    --enable-auto-tool-choice \
    --tool-call-parser qwen3_coder \
    --reasoning-parser-plugin nano_v3_reasoning_parser.py \
    --reasoning-parser nano_v3
```

The working directory must be set to the model weights directory
(where `nano_v3_reasoning_parser.py` lives) or provide the full path.
In Docker: `-w /weights/Nemotron-3-Nano-30B-A3B-NVFP4`

## Also: max_tokens must be sufficient
Nemotron generates a `<think>` reasoning block before the tool call.
With max_tokens=200, the reasoning block consumes the budget before
the tool call is emitted. Set max_tokens >= 512 for tool-calling
prompts, >= 4096 for complex audit tasks.

## How we found this
Tried: hermes, llama3_json, pythonic, llama — all failed.
Searched the vLLM recipes and HuggingFace discussions.
The official vLLM recipe at github.com/vllm-project/recipes specifies
qwen3_coder with the nano_v3 reasoning parser.

## Environment
- nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4
- vLLM 0.19.0
- Pipeline parallel size 2
