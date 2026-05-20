---
id: gotcha-mtp-streaming-usage
type: gotcha
title: "Gotcha: MTP speculative decoding silently breaks naive SSE token counters"
date: 2026-05-20
tags: [L3-model, observability, speculative-decoding]
related:
  - journey/12-bf16-tp4-full-precision
one_line: "With Gemma 4 MTP enabled, each SSE chunk in /v1/completions streaming carries multiple tokens (~3 at 99% acceptance). Counting chunks as tokens silently undercounts throughput by ~3×; the only reliable count is usage.completion_tokens, which requires stream_options.include_usage."
---

# Gotcha: MTP speculative decoding silently breaks naive SSE token counters

## Symptom

After enabling Gemma 4 MTP speculative decoding on vLLM 0.21, a
benchmark that streamed `/v1/completions` and counted SSE `data:`
chunks as tokens reported the same throughput as the pre-MTP baseline
(~15 tok/s) — but the `samples` counter dropped from 128 to 44 for a
128-token request. The throughput hadn't stalled; the counter was lying.

## Root cause

With speculative decoding (MTP, EAGLE, n-gram), vLLM verifies *k*
draft tokens per forward pass on the target model. When `k = num_speculative_tokens`
tokens are accepted, all of them appear in a single SSE chunk. At 99%
acceptance with `num_speculative_tokens=2`, the average chunk contains
~2.99 tokens (1 base + 2 speculated). Counting chunks therefore
undercounts real tokens by roughly the effective speedup factor — i.e.,
exactly the metric you were trying to measure.

The OpenAI-compatible streaming contract emits the `usage` block on
the *final* SSE event **only if** `stream_options.include_usage` is
set on the request. Without it, the only ground-truth token count is
unavailable to the client.

## Fix

- **Benchmarks / harness streaming code:** include `stream_options={"include_usage": True}` in every streaming request, and read `usage.completion_tokens` from the final event for tok/s.
- **Non-streaming code:** unaffected. `response.usage.completion_tokens` is always populated on a non-streaming `chat.completions.create()` call.
- **Reference implementation:** `tools/bench_vllm.py` shows the correct pattern.

## Current harness status (2026-05-20)

Both LLM call sites in the harness — `gemma_forge/models/vllm_llm.py:296`
and `gemma_forge/harness/loop.py:105` — issue non-streaming requests
and read `response.usage` directly, so the per-run `tok/s` value
emitted to OTel + the dashboard is correct under MTP. The hazard is
latent: any future refactor that flips a call to streaming without
also adding `stream_options.include_usage` would silently zero out
the `usage` block and corrupt token telemetry. Each call site has a
short comment pointing back to this entry.

## How to prevent

- Default streaming requests against any spec-decoded endpoint to
  `stream_options={"include_usage": True}`.
- Never derive tok/s from SSE chunk counts. Use either the usage
  block (streaming) or `response.usage.completion_tokens`
  (non-streaming).
- vLLM exposes the spec-decode counters directly on `/metrics`:
  `vllm:spec_decode_num_drafts_total`,
  `vllm:spec_decode_num_draft_tokens_total`,
  `vllm:spec_decode_num_accepted_tokens_total`. Acceptance rate is
  `accepted / drafted`; effective tokens per decode step is
  `1 + accepted / drafts`. Both panels live on the GemmaForge
  overview dashboard.

## Environment

- vLLM 0.21.0 (image `gemma4-vllm:0.21.0-mtp`)
- transformers 5.8.1
- Gemma 4 31B-it + `gemma-4-31B-it-assistant` drafter,
  `num_speculative_tokens=2`
- 4× L4 TP=4 bf16, no NVLink
