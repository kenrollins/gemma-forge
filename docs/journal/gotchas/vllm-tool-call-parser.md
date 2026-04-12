---
id: gotcha-vllm-tool-call-parser
type: gotcha
title: "Gotcha: vLLM requires explicit flags for function calling"
date: 2026-04-09
tags: [L3-model, tool-calling, discovery]
related:
  - journey/06-tool-calling
  - gotchas/nemotron-tool-parser
one_line: "vLLM requires --enable-auto-tool-choice and --tool-call-parser gemma4 to accept OpenAI-format tool calls for Gemma 4. The default configuration rejects tool-call requests with a 400."
---

# Gotcha: vLLM requires explicit flags for function calling

## Symptom
```
Error code: 400 - "auto" tool choice requires --enable-auto-tool-choice
and --tool-call-parser to be set
```

When sending an OpenAI-format request with `tools` parameter to vLLM,
the request is rejected with a 400 error.

## Root cause
vLLM does not enable function calling by default. Each model family
has a specific output format for tool calls (Gemma uses a different
format than Llama, which uses a different format than Mistral, etc.).
vLLM must be told which parser to use at startup.

## Fix
Add two flags to the `vllm serve` command:

```bash
vllm serve <model> \
    --enable-auto-tool-choice \
    --tool-call-parser gemma4
```

For other model families, the available parsers in vLLM 0.19.0 include:
gemma4, functiongemma, llama4_pythonic, llama, mistral, hermes,
qwen3coder, qwen3xml, granite, deepseekv3, deepseekv31, deepseekv32,
pythonic, openai, and many more. Check:
```bash
ls /path/to/vllm/tool_parsers/*_tool_parser.py
```

## Important
The `gemma4` parser is DIFFERENT from the `functiongemma` parser. The
`functiongemma` parser is for the older FunctionGemma model
(google/functiongemma-270m-it). The `gemma4` parser is for Gemma 4
models (31B, 26B, E4B, E2B).

## Verified behavior
With the flags enabled, both Gemma 4 31B-IT NVFP4 (tp=2) and E4B-IT
correctly produce structured `tool_calls` in their responses (not text
that looks like tool calls — actual structured tool_calls with
`finish_reason: "tool_calls"`). ADK's FunctionTool framework can
execute these directly.

## Environment
- vLLM 0.19.0
- Gemma 4 31B-IT NVFP4 and E4B-IT
- vllm/vllm-openai container with gemma-forge/vllm:latest Dockerfile
