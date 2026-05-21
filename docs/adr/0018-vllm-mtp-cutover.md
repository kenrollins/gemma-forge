# ADR-0018: vLLM 0.21.0 + Gemma 4 MTP speculative decoding for production inference

- **Status:** Accepted
- **Date:** 2026-05-20
- **Deciders:** Ken Rollins
- **Related:** [ADR-0001](0001-vllm-not-nim-or-ollama.md), [ADR-0013](0013-one-triton-per-l4-no-nvlink.md), [ADR-0014](0014-triton-vllm-director-shared-host-service.md), [ADR-0015](0015-gemma-4-model-lineup.md)

## Context

The inference plane shipped at vLLM 0.19.0 with Gemma 4 31B-it
running bf16 tensor-parallel across all four L4s (ADR-0013,
ADR-0014). Six STIG runs against that configuration produced
sustained ~15 tok/s single-stream throughput. The XR7620's 4×L4
PCIe-without-NVLink topology was the binding constraint:
60-layer all-reduce per forward pass over PCIe Gen4 dominates
latency.

Two things happened in the six weeks after the inference plane
froze.

1. **vLLM 0.21.0 shipped on 2026-05-15**, including PR #41745
   (merged 2026-05-06): native support for Gemma 4 MTP (Multi-Token
   Prediction) speculative decoding using Google's officially
   released assistant drafters (`google/gemma-4-31B-it-assistant`
   etc., 927 MB bf16). The drafter shares the target model's KV
   cache via the `Gemma4MTPModel` architecture; per-call cost is
   dominated by one extra forward pass on the small drafter, not
   on the 31B target.
2. **Real-workload throughput on Ralph's prompts was the binding
   constraint on iteration cadence** — Run 6's 19.1 hours meant
   one overnight run per day was the maximum cadence, and the
   experimental arc was paying for that.

A clean micro-benchmark on the prose-style prompt the vLLM recipes
use measured 41 tok/s vs the 15 tok/s baseline — 2.74× — at 99%
MTP acceptance. The implementation has known caveats (L4's SM 8.9
falls inside a documented `fp8e4nv not supported` window for FP8
KV-cache; we don't use FP8 KV so this didn't bind), and the L4
case isn't validated in the upstream PR. The risk was real but
contained, with a clean rollback path (the prior vLLM 0.19.0 image
is preserved as `gemma4-vllm:pre-mtp`).

## Decision

We adopt **vLLM 0.21.0 with Gemma 4 MTP speculative decoding** as
the production inference configuration for `gemma4-31b-vllm.service`,
serving the OpenAI-compatible API on `:8050`.

The cutover is captured in three commits:

- `a90695a` — Dockerfile bumped to `vllm/vllm-openai:latest`
  (2026-05-15-dated base, vLLM 0.21.0 + transformers 5.8.1); production
  serve script gains `--speculative-config '{"method":"mtp","model":
  "/weights/gemma-4-31B-it-assistant","num_speculative_tokens":2}'` and
  `--max-num-batched-tokens 4096` (required for MTP draft slots — the
  0.21 default of 2048 is below Gemma 4's `max_tokens_per_mm_item`).
- `fccc34d` — per-call MTP delta captured on `VllmLlm` OTel spans
  via `_snapshot_mtp_counters` snapshots around each
  `chat.completions.create()`. Failure-safe (wraps in try/except,
  returns `None` on any error). Spans gain
  `gen_ai.vllm.mtp.{drafts,drafted,accepted,acceptance,tokens_per_step}`.
- `bfd5eda` — Grafana panels for rolling MTP acceptance and
  effective tokens/step; gotcha entry
  ([`gotchas/mtp-streaming-usage.md`](../journal/gotchas/mtp-streaming-usage.md))
  documenting that MTP collapses multiple verified tokens into one
  SSE chunk, breaking naïve chunk-counting benchmarks.

Production tagging:
```
gemma4-vllm:0.21.0-mtp   ← canonical version tag
gemma4-vllm:mtp          ← convenience alias
gemma4-vllm:latest       ← serves the systemd unit
gemma4-vllm:pre-mtp      ← rollback (vLLM 0.19.0 + transformers 5.5.1)
```

Rollback procedure documented in
[`/data/triton/scripts/serve-gemma4-31b-bf16-tp4.sh`](https://github.com/kenrollins/gemma-forge/blob/main/infra/vllm/scripts/serve-gemma-bf16-tp4.sh):
`VLLM_IMAGE=gemma4-vllm:pre-mtp systemctl restart gemma4-31b-vllm`.

## What this delivers — measured

Run 7 (2026-05-20 → 2026-05-21, the first overnight against the new
stack) provides the production datapoint. Numbers are from
[`journey/38-mtp-in-practice.md`](../journal/journey/38-mtp-in-practice.md):

| Metric | Run 6 (vLLM 0.19) | Run 7 (vLLM 0.21 + MTP) | Delta |
|---|---:|---:|---:|
| Wall clock | 19.08 h | 12.20 h | **−6.88 h, 1.57× faster** |
| Architect median tok/s | ~15 | 28.0 | 1.87× |
| Worker median tok/s | ~15 | 25.9 | 1.73× |
| Reflector median tok/s | ~15 | 29.0 | 1.93× |
| MTP acceptance (aggregate) | n/a | 73.0% | — |
| Effective tokens/decode step | 1.00 | 2.46 | 2.46× per-forward |
| KV cache headroom (single-stream) | 17,968 tokens | 45,336 tokens | 2.52× |

The KV-cache headroom jump is a side benefit of vLLM 0.21's V1
engine memory layout, not of MTP itself. It bought us margin we
weren't asking for.

The per-call MTP acceptance on Ralph's workload (73%) is lower
than the prose-benchmark figure (99%) because the drafter
speculates less successfully on STIG XML, bash scripts, and tool
arguments than on free prose. Worker prompts (script-heavy) drop
to ~60% acceptance in the p10 of their distribution; Architect
and Reflector prompts (more discursive) hold at >95% acceptance
in their median. The architectural read: **MTP's win is shape-
dependent within a skill**, and the headline number is the
weighted mix.

## Alternatives considered

- **SGLang**. Merged Gemma 4 support 2026-04-07 (PR #21952). The
  best-published numbers (153 tok/s aggregate on 4× GB10) require
  Blackwell SM 121 + custom CUTLASS patches + RoCE 200 GbE
  interconnect. The L4/PCIe path is untested in public material.
  We'd be the first to validate it on this hardware, with no
  upstream support story if something went sideways. The
  risk/reward against vLLM's documented L4 support (even with
  caveats) didn't pencil out.
- **TensorRT-LLM**. Supports Gemma 4 (transformers ≥ 5.5.3) but
  has [open issues on the NVFP4 export](https://github.com/NVIDIA/TensorRT-LLM/issues/12764).
  The bf16 path we'd actually use isn't a documented
  fast-path on TRT-LLM, and the engine compile step adds a
  multi-minute startup penalty per model load that doesn't align
  with the Triton-director model-swap demo narrative
  ([ADR-0014](0014-triton-vllm-director-shared-host-service.md)).
- **EAGLE3 community drafter (RedHatAI / Thoughtworks, vLLM PR
  #39450, merged 2026-04-10)**. Measured 1.72× on Gemma 4 31B at
  ~65% draft acceptance. Compared favorably with MTP at first
  read, but the official Google MTP drafter ships with the
  guarantee of architectural compatibility (same vocabulary,
  same tokenizer, KV-cache sharing) that a community drafter
  doesn't. We picked the path with vendor-blessed integration
  semantics.
- **Pipeline parallelism for Gemma 4**. The structural blocker
  from [`journey/10`](../journal/journey/10-the-parallelism-maze.md)
  (missing `get_intermediate_tensors()` on
  `Gemma4ForConditionalGeneration`) is still open as of 2026-05-21.
  vLLM 0.21 fixed PP for Gemma 3 multimodal (PR #40786) but
  Gemma 4 hasn't followed yet. When it does, the same 4× L4 stack
  could see another 2-3× on top of MTP for free.

## Caveats and open questions

The cutover delivered the wall-clock win and introduced a
fix-rate regression: 145 remediated vs Run 6's 153 (−5%) on
effectively the same rule set, concentrated catastrophically in
the cryptography category (83% → 29%). Two hypotheses are open:

1. **Drafter randomness** — speculative decoding accepts/rejects
   drafter proposals based on the verifier's probability over
   that exact token, which means the effective sampling path
   differs from greedy sampling under the verifier alone. On
   hard prompts near a decision boundary (e.g., cryptography
   rules with subtle config-file edits), this is plausibly the
   cause.
2. **Baseline VM snapshot non-determinism** — the OpenSCAP scan
   on the same baseline snapshot returned a different failing-rule
   set between Run 6 and Run 7. Libvirt snapshots are filesystem
   determinism, not behavioral determinism; `dnf`, `systemd`, and
   the audit subsystem all carry state that diverges between
   restores.

Run 8 (planned 2026-05-21 evening) will land observability and
graded `outcome_value` work but won't change MTP, the drafter, or
the snapshot. If Run 8 reproduces the same cryptography regression
on the same rules, the VM-state hypothesis is the suspect. If it
regresses on different rules, the drafter-randomness hypothesis
becomes the working theory.

This ADR ships with the regression *named*, not resolved. The
wall-clock economics are decisive enough on their own — six STIG
runs of 19h each meant one overnight per day; with 12h runs we
get two runs per day and the experimental arc compresses by half.
The fix-rate question becomes a tractable experimental program
rather than a blocker on adoption.

## Consequences

### Positive

- **Wall clock cut by a third** — 19.1h → 12.2h. Cadence becomes
  two runs per day rather than one overnight.
- **Same demo story, same hardware** — no quantization caveat
  added, no parallelism story changed, all four L4s still visibly
  engaged. The "Gemma 4 31B running fast on the edge" narrative
  gains the speculative-decoding hook without losing anything.
- **Per-role observability landed alongside the cutover** — OTel
  spans now carry per-call MTP acceptance, Grafana renders the
  rolling rate, and the gotcha entry is in place for anyone
  building a downstream benchmark.
- **Rollback is one command and one image tag** — the entire
  cutover is reversible without code changes.

### Negative / accepted trade-offs

- **Fix-rate regression on cryptography rules is real and
  unresolved as of this writing.** Run 8 is the next datapoint
  that narrows the cause.
- **MTP draft buffer requires `--max-num-batched-tokens 4096`**
  in the systemd unit; the vLLM 0.21 default of 2048 was below
  Gemma 4's `max_tokens_per_mm_item` of 2496 and caused first-launch
  failures. Documented in the script comments.
- **Dashboard tile for MTP doesn't exist yet** — DEF-25 is the
  next-run package that surfaces it. Until then, MTP is only
  visible on Grafana and in OTel spans, not on the
  ArchitecturePanel hardware card the audience watches.

### Reversibility

Complete. The prior image (`gemma4-vllm:pre-mtp`) is tagged and
preserved; the systemd unit reads its image tag from an env var
(`VLLM_IMAGE`). Reverting is a single `systemctl restart` with
`VLLM_IMAGE` overridden. No state lives in the running container
that wouldn't be rebuilt at startup from `/data/triton/weights/`.

## References

- [vLLM PR #41745 — Gemma 4 MTP support (merged 2026-05-06)](https://github.com/vllm-project/vllm/pull/41745)
- [vLLM PR #39450 — Gemma 4 EAGLE3 support (merged 2026-04-10)](https://github.com/vllm-project/vllm/pull/39450)
- [Google blog — Multi-Token Prediction drafters for Gemma 4](https://blog.google/innovation-and-ai/technology/developers-tools/multi-token-prediction-gemma-4/)
- [vLLM MTP feature docs](https://docs.vllm.ai/en/latest/features/speculative_decoding/mtp/)
- [`journey/38`](../journal/journey/38-mtp-in-practice.md) — the production datapoint with the fix-rate regression named
- [`gotchas/mtp-streaming-usage.md`](../journal/gotchas/mtp-streaming-usage.md) — the streaming + usage.completion_tokens caveat
- [`deferred.md`](../deferred.md) DEF-23, DEF-24, DEF-25 — the observability work that pairs with this ADR but ships in Run 8's pre-flight
