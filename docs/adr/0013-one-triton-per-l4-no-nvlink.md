# ADR-0013: One Triton process per L4 (plus one wide Triton for tp=2 31B), no NVLink dependency

- **Status:** Accepted
- **Date:** 2026-04-09
- **Deciders:** Ken Rollins
- **Related:** [ADR-0014](0014-triton-vllm-director-shared-host-service.md), [ADR-0015](0015-gemma-4-model-lineup.md)

## Context

The XR7620 has 4× NVIDIA L4 24GB GPUs and no NVLink. The inference
layer has to serve four agent roles (Architect, Worker, Auditor,
Sentry) with Gemma 4 model variants while preserving a "rugged
resilience" story for the Federal-edge audience: if one GPU wedges, the
others must keep serving.

Two architectural questions follow:

1. **Process topology.** Do we run one Triton Inference Server process
   that owns all 4 GPUs, or four Triton processes pinned one-per-GPU?
2. **Tensor parallelism.** Gemma 4 31B-IT does not fit on a single L4
   at bf16 (~62 GB weights vs. 24 GB VRAM). The official
   [vLLM Gemma 4 recipe](https://docs.vllm.ai/projects/recipes/en/latest/Google/Gemma4.html)
   requires `tensor_parallel_size=2` for 31B-IT. How does that fit
   into a per-GPU isolation pattern?

## Decision

We run **N+1 Triton processes** on the XR7620:

- **Four single-GPU Triton processes** (`triton@0` … `triton@3`), each
  pinned to one L4 via `CUDA_VISIBLE_DEVICES=N`, each in
  `--model-control-mode=explicit`. These serve the single-L4 models:
  Gemma 4 E4B (Auditor) and Gemma 4 E2B (Sentry), plus any future
  edge-sized models.

- **One "wide" Triton process** (`triton@wide-01`) pinned to GPUs 0+1
  via `CUDA_VISIBLE_DEVICES=0,1`, with `tensor_parallel_size=2` and
  `distributed_executor_backend=ray` in `model.json` (the workaround
  for the documented Triton-EXPLICIT-mode + tensor-parallelism
  interaction; see Consequences). This serves Gemma 4 31B-IT, which
  Architect and Worker share (per ADR-0015).

All five processes are systemd units under `/data/triton/`, all sharing
the same model repository at `/data/triton/models/`. They are clients
of one common model catalog, not five independent islands.

## Alternatives considered

- **One Triton process owning all 4 GPUs with `instance_group.gpus`** —
  Looks cleaner on paper. Rejected because of
  [triton-inference-server/server#7786](https://github.com/triton-inference-server/server/issues/7786):
  the Triton vLLM backend's `validate_device_config` calls
  `torch.cuda.set_device()` but does **not** set
  `CUDA_VISIBLE_DEVICES`, which vLLM actually reads. Result:
  `instance_group { gpus: [N] }` is silently ignored, models pile onto
  GPU 0, OOM. Status as of early 2026: unresolved in released
  containers; the community workaround is to set
  `CUDA_VISIBLE_DEVICES` per-process. NVIDIA's own
  [Triton FAQ](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/faq.html)
  endorses the one-Triton-per-GPU pattern explicitly.

- **One wide Triton process spanning all 4 GPUs for the 31B model** —
  Would let the 31B model use `tensor_parallel_size=4`. Rejected
  because (a) `tp=4` is wasteful for a 31B Dense model that fits
  comfortably in `tp=2`, and (b) it would consume the entire host's
  GPU budget for a single model, leaving nothing for the edge-sized
  variants. The N+1 layout we picked spans only the GPUs the wide
  model actually needs (0+1) and leaves GPUs 2+3 free for E4B/E2B and
  for future models.

- **Skip the 31B Dense model entirely; use Gemma 4 26B MoE for the
  Architect/Worker roles to fit one model per L4** — Considered
  seriously and rejected per ADR-0015. Following the official vLLM
  Gemma 4 recipe is a credibility win with Federal evaluators worth
  the additional Triton process. ADR-0015 captures the model-lineup
  decision in detail.

- **FP8 / NVFP4 quantization of 31B Dense to fit on one L4** —
  Considered. L4 has native FP8 tensor cores and a quantized 31B might
  fit on a single L4. Rejected for the day-one critical path because
  (a) the official Gemma 4 release does not ship a quantized 31B
  variant, (b) we'd be picking a community quant or running it
  ourselves, and (c) it adds quantization to the demo's day-one risk
  surface. We may add a quantized variant as a future skill once the
  baseline architecture is proven.

## Consequences

### Positive

- **Fault isolation by construction.** A wedged vLLM engine on one L4
  takes down only its own Triton process; the other GPUs keep serving.
  This is the "rugged resilience" story for Federal-edge customers,
  enforced at the OS level rather than asserted in marketing.
- **Per-GPU CUDA isolation.** Each Triton process sees only its
  assigned GPU(s) via `CUDA_VISIBLE_DEVICES`, sidestepping the GPU 0
  pile-on bug entirely.
- **Sized for the actual hardware.** The wide Triton consumes only the
  2 GPUs it needs for `tp=2`; the other GPUs remain available for
  independent edge models, future skills, and dynamic loading
  experiments.
- **Systemd-managed lifecycle.** Each Triton process is a discrete
  systemd unit with its own logs, restart policy, and metrics
  endpoint. Standard operational hygiene.
- **No NVLink required**, matching the XR7620's tactical-edge form
  factor where NVLink is not assumed.

### Negative / accepted trade-offs

- **Five processes instead of one.** More to monitor, more ports to
  manage. Mitigated by systemd template units (`triton@.service`) and
  a small router service that translates demo-name → Triton-instance
  + model-name.
- **Tensor parallelism + EXPLICIT mode requires the `ray` distributed
  executor.** Per Triton release notes, the default
  `distributed_executor_backend` is broken with `tp>1` in EXPLICIT
  mode. We must set `"distributed_executor_backend": "ray"` in the
  31B model's `model.json`. **This is a day-one validation gate in
  Phase 1**: if it doesn't work, we either fall back to Option B
  (26B MoE) or escalate.
- **The wide Triton is a different shape from the four narrow ones.**
  Operators have to remember the `triton@wide-01` unit exists.
  Documented in `docs/host-setup.md`.

## References

- [Triton vLLM backend](https://github.com/triton-inference-server/vllm_backend)
- [Triton FAQ — multi-GPU pattern](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/faq.html)
- [Issue #7786 — vLLM backend GPU selection bug](https://github.com/triton-inference-server/server/issues/7786)
- [Triton release notes 25.08 — TP+EXPLICIT caveat](https://docs.nvidia.com/deeplearning/triton-inference-server/archives/triton-inference-server-2660/release-notes/rel-25-08.html)
- [vLLM Gemma 4 recipe](https://docs.vllm.ai/projects/recipes/en/latest/Google/Gemma4.html)
- ADR-0014: Triton-managed vLLM director (shared host service)
- ADR-0015: Gemma 4 model lineup
