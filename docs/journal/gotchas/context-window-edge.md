---
id: gotcha-context-window-edge
type: gotcha
title: "Gotcha: NVFP4 31B on L4 has minimal KV cache headroom"
date: 2026-04-10
tags: [L4-orchestration, context-management, discovery]
related:
  - journey/14-overnight-run-findings
  - improvements/03-context-budget-assembly
one_line: "Per-turn ADK session history grows when the model makes multiple tool calls inside a single turn; context overflows come from that in-turn growth, not from between-turn state."
---

# Gotcha: NVFP4 31B on L4 has minimal KV cache headroom

## Symptom
```
Error code: 400 - This model's maximum context length is 4096 tokens.
However, you requested 1024 output tokens and your prompt contains at
least 3073 input tokens, for a total of at least 4097 tokens.
```

Hits on the second or third turn of a multi-turn conversation, even
with max_model_len=4096. The first turn works fine.

## Root cause
The NVFP4 31B model consumes ~21.9 GB per GPU (of 23.0 GB L4 capacity).
After the model weights load, only ~642 MiB per GPU remains for KV cache.
vLLM allocates 10,432 tokens of KV cache total (with FP8 compression).

At max_model_len=4096, vLLM can handle 3.58x concurrent requests.
But in a multi-turn conversation, the prompt grows with each turn
(previous messages + tool calls + tool results). A STIG scan result
(even truncated) + previous conversation can exceed 3000 tokens by
the second turn.

## Fix (multi-pronged)

1. **Keep tool outputs compact.** The full STIG scan is 71K chars
   (~20K tokens). Truncate to the 20 most relevant failing rules
   (~2K chars). The model doesn't need 270 rules to make a decision.

2. **Increase max_model_len to 8192.** With 10,432 tokens of KV cache,
   8192 is achievable for a single concurrent request. This gives
   headroom for multi-turn conversations.

3. **Design agent instructions for concise responses.** "Be concise"
   in the system prompt helps. Long-winded analysis wastes tokens.

4. **Consider conversation summarization** between LoopAgent iterations
   if conversations grow beyond 3-4 iterations. ADK doesn't do this
   natively; it would be a custom callback.

## Key insight
On edge hardware (L4, 24 GB VRAM), context management is a first-class
engineering concern. You can't just set max_model_len=128K and forget
about it — VRAM is a shared budget between model weights, KV cache,
and CUDA graphs.

The numbers for NVFP4 31B on 2× L4 (tp=2):
- Model weights: ~16.3 GiB per GPU (after NVFP4 + TP splitting)
- CUDA graphs: ~1.47 GiB per GPU
- KV cache: ~2.39 GiB per GPU (FP8 compressed)
- Total: ~21.9 GiB of 22.0 GiB available (at 0.90 utilization)

## Environment
- nvidia/Gemma-4-31B-IT-NVFP4
- tensor_parallel_size=2
- gpu_memory_utilization=0.90
- NVIDIA L4 (23,034 MiB per GPU)
- KV cache: FP8 e4m3
