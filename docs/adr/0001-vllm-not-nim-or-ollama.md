# ADR-0001: vLLM as the inference server (not NVIDIA NIM, not Ollama)

- **Status:** Superseded by [ADR-0014](0014-triton-vllm-director-shared-host-service.md) on 2026-04-09
- **Date:** 2026-04-08
- **Deciders:** Ken Rollins

> **Superseded.** This ADR captured the original "four dedicated vLLM
> containers, one per L4" decision. Verification during Phase 0
> revealed that (a) Gemma 4 31B-IT does not fit on a single L4 at bf16
> per the official vLLM recipe (it requires `tensor_parallel_size=2`),
> and (b) the XR7620 is a multi-demo host where the operator wants to
> swap model sets between demos without redeploying containers. ADR-0014
> revisits the inference layer accordingly: vLLM is still the engine,
> but it is now wrapped by NVIDIA Triton Inference Server as a shared
> host service at `/data/triton/`, with EXPLICIT model control mode
> for dynamic load/unload. The reasoning below remains valid for **why
> vLLM is still the engine** — it is the wrapper layer that changed.

## Context

GemmaForge runs four Gemma 4 models concurrently on a Dell PowerEdge
XR7620 with 4× NVIDIA L4 GPUs, intended as a Federal reference build.
The inference server must be:

1. **Air-gappable** — no license activation, no telemetry phone-home, no
   external dependency at runtime. Federal customers will deploy this in
   environments with zero egress.
2. **Apache-2.0 or equivalent permissively licensed** — Federal legal
   teams must be able to redistribute it inside customer environments
   without negotiating new license terms.
3. **Day-0 Gemma 4 capable** — the demo's value depends on running Gemma
   4 31B-IT, E4B, and E2B variants natively, not on a quantized
   community port.
4. **OpenAI-compatible REST** — the harness deliberately does not use
   LiteLLM (per the original PRD security constraint), so the server
   must speak the OpenAI Chat Completions API directly.
5. **Production-grade throughput** at the tactical edge — paged-attention,
   continuous batching, and tensor-parallel serving on a single L4 are
   table stakes for the demo's "rugged resilience" story.

## Decision

We use **vLLM** as the inference server, deployed as one container per
L4 GPU, exposing the OpenAI-compatible REST API.

We pin the vLLM container image to a tag that ships **Day-0 Gemma 4
support** (released 2026-04-02 alongside Gemma 4 itself per the
[vLLM blog announcement](https://vllm.ai/blog/gemma4) and the
[official Gemma 4 vLLM recipe](https://docs.vllm.ai/projects/recipes/en/latest/Google/Gemma4.html)).

## Alternatives considered

- **NVIDIA NIM** — NVIDIA's production inference microservice. Excellent
  performance and packaging, but requires NVIDIA AI Enterprise licensing
  and (in current releases) calls home for license verification. Both
  conditions are non-starters for a Federal air-gap demo. Ruled out.

- **NVIDIA Triton Inference Server** — Open-source, FIPS-able, widely
  deployed in DoD. Strong Federal pedigree. Triton's "model repository"
  pattern (one process serving many models, hot-reloadable, with
  filesystem-based versioning and pluggable backends) is the right
  shape for fleet inference platforms serving dozens of models. It is
  *not* the right shape for GemmaForge's fixed 4-role architecture
  where per-GPU container isolation is the *point* (see Consequences
  and ADR-0013). Importantly, Triton supports vLLM as a backend, so
  picking vLLM today is a low-cost reversible decision: a future
  customer who standardizes on Triton can wrap our vLLM engines
  without re-architecting.

- **Ollama** — Excellent developer ergonomics and great for laptops.
  Not designed for concurrent multi-GPU production serving on
  server-class hardware, lacks the throughput characteristics we need
  for a live demo with four agents in flight, and its single-process
  model fights the fault-isolation story (one vLLM container per L4)
  that ADR-0013 depends on. Ruled out for production-grade serving;
  would be a fine choice for a developer-laptop variant of this same
  demo.

- **Hugging Face Text Generation Inference (TGI)** — Credible Apache-2
  alternative to vLLM. We picked vLLM because (a) Gemma 4 Day-0 support
  landed there first with an officially-blessed recipe, (b) Red Hat
  ships vLLM as Red Hat AI Inference Server (RHAIIS), giving Federal
  customers a commercially-supportable upgrade path on the same engine,
  and (c) the OpenAI-compat API surface is more mature on vLLM as of
  this writing.

- **LiteLLM as a router in front of any backend** — Explicitly ruled out
  in the original PRD on security-surface grounds. We talk to vLLM
  directly via `httpx` to keep the dependency tree and the auditable
  call-path minimal.

## Consequences

### Positive

- Day-0 Gemma 4 support, including the 31B Dense, E4B, and E2B variants
  the harness assigns to Architect/Worker, Auditor, and Sentry roles.
- Apache-2.0 licensed, redistributable, no phone-home — clean
  air-gappability story for Federal customers.
- Per-GPU container isolation is natural with vLLM (one process per L4
  via `CUDA_VISIBLE_DEVICES`), enabling the fault-tolerance story
  documented separately in ADR-0013.
- Commercial support upgrade path exists via Red Hat AI Inference Server
  (RHAIIS) for customers who require a vendor SLA.

### Negative / accepted trade-offs

- **vLLM container images change rapidly** during the post-Gemma-4
  release window. We must pin tags explicitly in `docker-compose.yml`
  and re-validate on each bump rather than tracking `latest`.

- **vLLM does not provide Triton-style multi-model multiplexing,
  hot model reload, or `config.pbtxt`-based fleet management.** None of
  these are needed for GemmaForge's fixed 4-role architecture: we run
  one model per L4 deliberately (ADR-0013), every demo run starts from
  a version-pinned model set for reproducibility, and revving a model
  is "edit one tag in `docker-compose.yml` and `docker compose up -d
  vllm-architect`" — under 30 seconds. The decision is also reversible
  at low cost because vLLM is supported as a Triton backend; a Federal
  customer who standardizes on Triton can wrap our vLLM engines without
  re-architecting GemmaForge.

## References

- [vLLM blog: Announcing Gemma 4 on vLLM](https://vllm.ai/blog/gemma4)
- [vLLM recipe: Gemma 4 Usage Guide](https://docs.vllm.ai/projects/recipes/en/latest/Google/Gemma4.html)
- [Red Hat: Run Gemma 4 with Red Hat AI on Day 0](https://developers.redhat.com/articles/2026/04/02/run-gemma-4-red-hat-ai-day-0-step-step-guide)
- ADR-0013: One vLLM container per L4 (no NVLink dependency)
