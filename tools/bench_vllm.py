#!/usr/bin/env python3
"""
Single-stream throughput benchmark for the Gemma 4 31B vLLM endpoint.

Hits an OpenAI-compatible /v1/completions endpoint at a fixed sequence
of max_tokens values and reports median TTFT + decode tok/s per length.

Mirrors the columns in docs/journal/journey/12-bf16-tp4-full-precision.md
so the numbers are directly comparable to the baseline TP=4 measurement
captured there.

Usage:
    python tools/bench_vllm.py --base-url http://localhost:8050 --model /weights/gemma-4-31B-it
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request

PROMPT = (
    "Write a detailed technical explanation of how tensor parallelism "
    "works for large language model inference on GPUs without NVLink. "
    "Cover the all-reduce step, why PCIe bandwidth matters, and how "
    "this differs from pipeline parallelism."
)


def stream_complete(base_url: str, model: str, max_tokens: int, temperature: float = 0.0) -> tuple[float, float, int, int]:
    """Returns (ttft_s, tok_per_s, completion_tokens, sse_chunks).

    Uses `stream_options.include_usage` so the final SSE event carries
    accurate `usage.completion_tokens` — important for speculative
    decoding paths where one SSE chunk may carry multiple verified
    tokens, making chunk-count an unreliable token estimate.
    """
    body = json.dumps({
        "model": model,
        "prompt": PROMPT,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }).encode()
    req = urllib.request.Request(
        f"{base_url}/v1/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t_start = time.perf_counter()
    t_first: float | None = None
    sse_chunks = 0
    completion_tokens = 0
    with urllib.request.urlopen(req, timeout=600) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                break
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            usage = obj.get("usage")
            if usage and usage.get("completion_tokens") is not None:
                completion_tokens = usage["completion_tokens"]
            choices = obj.get("choices") or []
            if not choices:
                continue
            text = choices[0].get("text", "")
            if not text:
                continue
            if t_first is None:
                t_first = time.perf_counter()
            sse_chunks += 1
    t_end = time.perf_counter()
    ttft = (t_first - t_start) if t_first else float("nan")
    decode_time = (t_end - t_first) if t_first else float("nan")
    tok_per_s = (completion_tokens - 1) / decode_time if decode_time > 0 and completion_tokens > 1 else float("nan")
    return ttft, tok_per_s, completion_tokens, sse_chunks


def run(base_url: str, model: str, lengths: list[int], repeats: int) -> None:
    print(f"endpoint={base_url} model={model} repeats={repeats}\n")
    print(f"{'max_tokens':>10} | {'median_ttft_s':>13} | {'median_tok/s':>13} | {'tok/chunk':>9} | {'completed':>9}")
    print("-" * 74)
    for length in lengths:
        ttfts: list[float] = []
        tps: list[float] = []
        per_chunk: list[float] = []
        completed = 0
        for i in range(repeats):
            try:
                ttft, tok_per_s, n_tokens, n_chunks = stream_complete(base_url, model, length)
            except Exception as exc:  # noqa: BLE001
                print(f"  iter {i}: ERROR {exc}")
                continue
            ttfts.append(ttft)
            tps.append(tok_per_s)
            if n_chunks > 0:
                per_chunk.append(n_tokens / n_chunks)
            completed = max(completed, n_tokens)
        if not tps:
            print(f"{length:>10} | {'-':>13} | {'-':>13} | {'-':>9} | {0:>9}")
            continue
        tpc = statistics.median(per_chunk) if per_chunk else float("nan")
        print(f"{length:>10} | {statistics.median(ttfts):>13.2f} | {statistics.median(tps):>13.2f} | {tpc:>9.2f} | {completed:>9}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:8050")
    p.add_argument("--model", default="/weights/gemma-4-31B-it")
    p.add_argument("--lengths", type=int, nargs="+", default=[128, 256, 512])
    p.add_argument("--repeats", type=int, default=3)
    args = p.parse_args()
    run(args.base_url, args.model, args.lengths, args.repeats)


if __name__ == "__main__":
    main()
