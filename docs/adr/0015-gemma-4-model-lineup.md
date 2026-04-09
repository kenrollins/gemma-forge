# ADR-0015: Gemma 4 model lineup — official vLLM recipe (Option A)

- **Status:** Accepted
- **Date:** 2026-04-09
- **Deciders:** Ken Rollins
- **Related:** [ADR-0013](0013-one-triton-per-l4-no-nvlink.md), [ADR-0014](0014-triton-vllm-director-shared-host-service.md)

## Context

The Ralph loop has four agent roles (Architect, Worker, Auditor,
Sentry) running on a Dell XR7620 with 4× NVIDIA L4 24GB GPUs. The
[Gemma 4 release](https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/)
ships four variants:

- **Gemma 4 31B Dense (31B-IT)** — flagship dense model
- **Gemma 4 26B MoE** — mixture-of-experts variant
- **Gemma 4 E4B** — "effective 4B" edge variant
- **Gemma 4 E2B** — "effective 2B" edge variant

We have to assign models to roles and to GPUs in a way that:

1. Matches the official [vLLM Gemma 4 recipe](https://docs.vllm.ai/projects/recipes/en/latest/Google/Gemma4.html)
   wherever possible (defensibility with Federal evaluators).
2. Fits the per-Triton-per-L4 process topology from ADR-0013.
3. Honors the reality that **Gemma 4 31B-IT does not fit on a single
   L4** at bf16 (~62 GB weights vs. 24 GB VRAM) — the official recipe
   specifies `tensor_parallel_size=2` for 31B-IT.
4. Preserves the demo's narrative coherence: distinct roles, distinct
   responsibilities, recognizable on the dashboard.

## Decision

We adopt the **official vLLM Gemma 4 recipe verbatim**, assigning
models to roles as follows:

| Role       | Model              | GPU(s)        | Triton instance     | TP | Notes |
|------------|-------------------|---------------|---------------------|----|-------|
| Architect  | Gemma 4 31B-IT    | GPUs 0+1      | `triton@wide-01`    | 2  | shares engine with Worker; sequential in the loop |
| Worker     | Gemma 4 31B-IT    | GPUs 0+1      | `triton@wide-01`    | 2  | shares engine with Architect |
| Auditor    | Gemma 4 E4B       | GPU 2         | `triton@2`          | 1  | fast validation, mission-app health checks |
| Sentry     | Gemma 4 E2B       | GPU 3         | `triton@3`          | 1  | lightweight watchdog, telemetry classification |

Architect and Worker share a single vLLM engine because they execute
**sequentially** within a Ralph loop iteration (Architect drafts → Worker
applies → Auditor validates → repeat). Sharing one engine for two
roles is correct, not a compromise: it matches how the harness
actually issues requests, and it frees GPUs 2+3 for the edge models
that need them.

## Alternatives considered

- **Option B: Gemma 4 26B MoE for Architect/Worker** (one model per
  L4, no wide Triton). Considered seriously. Rejected because:
  (a) the official vLLM recipe targets 31B-IT and 26B MoE explicitly
  with different patterns, and following the recipe verbatim is
  worth more in Federal credibility than a slightly cleaner
  per-L4 layout; (b) MoE memory math at the edge is non-obvious
  (expert routing can blow past static weight estimates); and
  (c) Architect and Worker sharing the 31B engine is operationally
  fine, so the "one model per GPU" symmetry isn't worth compromising
  the recipe to achieve.

- **Option C: FP8 / NVFP4 quantized 31B Dense to fit on one L4.**
  Considered. L4 has native FP8 tensor cores. Rejected for the
  day-one critical path because (a) the official Gemma 4 release
  does not ship a quantized 31B variant, (b) we'd be picking a
  community quant or running quantization ourselves, and (c) it
  adds quantization to the demo's day-one risk surface. May be
  added as a future skill once the baseline architecture is proven.

- **Different models for Architect and Worker (e.g., 31B-IT for
  Architect, 26B MoE for Worker)** — Visually distinct on the
  dashboard but operationally muddled and not what the recipe
  expects. Rejected: complexity for cosmetics.

- **Run only the edge models (E4B, E2B) and skip 31B entirely** —
  Would simplify Phase 1 enormously and avoid the wide-Triton
  complication. Rejected because the 31B is the model that
  *actually demonstrates* the XR7620's heavyweight inference
  capability — the demo loses its main hardware story without it.

## Consequences

### Positive

- **Defensibility.** "We run Gemma 4 exactly the way Google and vLLM
  ship it" is the strongest possible answer to Federal evaluators
  who ask about model deployment.
- **Sets up the model-swap demo perfectly.** The lineup leaves the
  GPU 2 and GPU 3 single-Triton instances available to demonstrate
  dynamic model loading without disturbing the wide-Triton 31B
  engine. Future demo: *"watch us swap E4B for a vision model on
  GPU 2 mid-mission, then swap it back."*
- **Sequentially-shared engine matches loop semantics.** Architect
  and Worker contend for the same GPU resource only at the same
  step in a loop iteration, which is exactly when one of them is
  *not* running. No throughput penalty.
- **Honest hardware story.** The dashboard shows a "wide" 31B engine
  spanning two L4s plus two compact edge models — that's a more
  accurate picture of what real edge inference looks like and
  prepares the audience for the operational reality their own
  hardware will face.

### Negative / accepted trade-offs

- **Architect and Worker visually appear as the same model on the
  dashboard.** Mitigated by labeling them by *role* (with their
  distinct system prompts) rather than by *model*. The dashboard's
  thought-stream component shows the system prompt active for each
  request, so the audience can see Architect and Worker reasoning
  differently even though the underlying weights are shared.
- **The wide Triton process is the most fragile component in the
  stack** (TP+EXPLICIT mode requires the `ray` distributed executor
  workaround per ADR-0013/0014). The fallback if Phase 1 validation
  gates fail is documented: drop the 31B, return to the Option B
  layout with 26B MoE on single L4s. This is a real contingency,
  not a theoretical one.
- **GPU memory headroom on the wide Triton is tight.** 31B-IT at
  bf16 with `tp=2` consumes most of GPUs 0+1's combined 48 GB.
  We won't be able to coexist a second model on those GPUs, which
  removes one direction for future model-swap experiments.

## References

- [Gemma 4 release blog](https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/)
- [vLLM Gemma 4 recipe](https://docs.vllm.ai/projects/recipes/en/latest/Google/Gemma4.html)
- [vLLM blog: Announcing Gemma 4](https://vllm.ai/blog/gemma4)
- ADR-0013: One Triton process per L4
- ADR-0014: Triton-managed vLLM director (shared host service)
