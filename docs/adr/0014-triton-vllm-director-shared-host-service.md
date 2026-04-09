# ADR-0014: Triton-managed vLLM director as a shared host service at `/data/triton/`

- **Status:** Accepted (supersedes [ADR-0001](0001-vllm-not-nim-or-ollama.md))
- **Date:** 2026-04-09
- **Deciders:** Ken Rollins
- **Related:** [ADR-0001](0001-vllm-not-nim-or-ollama.md) (superseded), [ADR-0013](0013-one-triton-per-l4-no-nvlink.md), [ADR-0015](0015-gemma-4-model-lineup.md)

## Context

ADR-0001 picked vLLM as the inference server, deployed as four
dedicated vLLM containers inside the GemmaForge `docker-compose.yml`,
each statically loaded with one Gemma 4 variant for one agent role.
That decision was correct *for a single demo* and is still valid for
the underlying engine (vLLM remains the right inference engine —
ADR-0001's reasoning on that point still holds).

Two facts surfaced during Phase 0 verification that force a revision
of how vLLM is **packaged and operated**:

1. **The XR7620 is a multi-demo host.** Ken intends to run multiple
   distinct demos against the same hardware over time, each with its
   own preferred model set (a vision skill, a long-context summarizer
   skill, a code-remediation skill, etc.). Static per-demo vLLM
   containers force every new demo to redeploy its inference layer,
   which is operationally wrong for a *demo host* and unrepresentative
   of how Federal customers will actually use edge AI hardware in the
   field.

2. **Gemma 4 31B-IT does not fit on one L4** (62 GB weights vs. 24 GB
   VRAM); the official vLLM recipe requires `tensor_parallel_size=2`.
   This wasn't catastrophic but it meant the original "one vLLM
   container per L4" topology in ADR-0001 was wrong even for the
   single-demo case.

We need an inference layer that:

- Hosts a **catalog** of models on disk (10+, growable), with the
  ability to load any subset into the L4s on demand.
- Supports **runtime model load/unload** so a demo's startup ritual
  can include "operator selects this mission's model set, system
  warms up the L4s, audience watches it happen."
- Is **owned by the host**, not by any one demo project, so multiple
  demos coexist as clients of the same inference service.
- Preserves the per-GPU **fault isolation** story from the
  superseded ADR-0013.
- Is **air-gappable, Apache-2-licensed, and free of vendor
  phone-home** (the criteria from ADR-0001 still apply).
- Speaks the **OpenAI-compatible REST API** so the harness can talk
  to it via plain `httpx` without LiteLLM.

## Decision

We promote the inference layer to a **shared host service at
`/data/triton/`**, alongside `/data/docker/`, `/data/vm/`, and
`/data/code/`. The service is **NVIDIA Triton Inference Server with
its vLLM backend, in EXPLICIT model control mode**. vLLM remains the
inference engine; Triton is the wrapper that provides the model
catalog, runtime load/unload, and the operational shape we need.

### Layout

```
/data/triton/
  models/                           ← shared model repository
    gemma4-31b-it/
      config.pbtxt                  ← backend: "vllm", instance_group KIND_MODEL
      1/model.json                  ← vLLM init args, tp=2, distributed_executor_backend=ray
    gemma4-e4b/
      config.pbtxt
      1/model.json
    gemma4-e2b/
      config.pbtxt
      1/model.json
    ...future models live here, mounted into every Triton process...
  systemd/                          ← systemd unit files
    triton@.service                 ← templated narrow unit (one per L4)
    triton@wide-01.service          ← wide unit pinned to GPUs 0+1 for tp=2
  config/
    triton-defaults.env             ← shared defaults (model repo path, ports)
  logs/                             ← per-instance logs
```

### Process topology (per ADR-0013)

- `triton@0`, `triton@1`, `triton@2`, `triton@3` — one per L4,
  `CUDA_VISIBLE_DEVICES=N`, ports `8000+N*10` (HTTP), `8001+N*10`
  (gRPC), `8002+N*10` (metrics).
- `triton@wide-01` — `CUDA_VISIBLE_DEVICES=0,1`, dedicated to the
  Gemma 4 31B-IT model with `tensor_parallel_size=2`,
  `distributed_executor_backend=ray`.

All processes use `--model-control-mode=explicit`, sharing the same
`/data/triton/models/` repository.

### Client contract

GemmaForge is a **client** of this service, not its owner. The harness
reads two environment variables:

- `TRITON_DIRECTOR_URL` — base URL for the routing layer (or for the
  appropriate Triton instance if the demo talks to one directly)
- `TRITON_MODEL_REPO` — read-only path for verification only, never
  written to from the client

GemmaForge **never**:

- writes to `/data/triton/models/`
- restarts Triton systemd units
- assumes Triton is up at startup (it requests model loads explicitly)

### Demo startup ritual

Every demo run begins with an **explicit model warm-up phase** that
becomes part of the show:

1. Operator picks a mission profile in the dashboard.
2. The harness POSTs to the appropriate Triton instances'
   `/v2/repository/models/<name>/load` endpoints for the mission's
   model set.
3. The dashboard streams GPU memory growth in real time across all
   four L4s. The audience sees the inference layer warm up live.
4. Once all models report `READY`, the Ralph loop begins.

End of run:

5. The harness POSTs `/v2/repository/models/<name>/unload` for each
   loaded model, freeing the L4s for the next demo.
6. The dashboard shows the L4s drain back to baseline.

This is now part of the demo narrative, not a backstage step.

## Alternatives considered

- **Stay with the original four-dedicated-vLLM-containers plan**
  (ADR-0001 as written) — Rejected because (a) the 31B-IT model
  doesn't fit on one L4, requiring a structural change anyway, and
  (b) it doesn't address the multi-demo-host requirement at all.
- **NVIDIA NIM** — Re-evaluated. NIM does use Triton + vLLM under the
  hood, but NIM containers are pinned single-model and don't exercise
  the explicit-mode multi-model swap path we want. NIM also still has
  the licensing/phone-home objections from ADR-0001.
- **Per-demo Triton containers inside each demo's compose file** —
  Defeats the "shared host service" goal. Every new demo would
  reinstantiate its own Triton, duplicating the model catalog and
  losing the "operator swaps mission sets without redeploying"
  property.
- **A custom Python model-router that calls vLLM directly via Python
  bindings, no Triton** — Would let us implement Ollama-like LRU
  exactly as we want, but reinvents wheels Triton already provides
  (model lifecycle, metrics, the standardized inference API,
  systemd integration). The Triton wrapper is ~ten config files of
  overhead for capabilities we'd otherwise build ourselves.
- **vLLM's own
  [`vllm serve` with multiple model endpoints` pattern](https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html)** —
  vLLM's serving mode does not support runtime load/unload of models,
  which is the entire reason we're doing this revision.

## Consequences

### Positive

- **One model catalog, many demos.** Future demo projects (`/data/code/<demo>`)
  become clients of the same Triton director without redeploying any
  inference layer. This is the right operational shape for a demo
  host.
- **Runtime model swap as part of the demo narrative.** The L4
  warm-up becomes a visible, audible part of the show — "watch the
  GPU memory bars climb as we load this mission's model set." That's
  a stronger story than "four containers were already running before
  you walked in."
- **Federal-credible inference architecture.** Triton is widely
  deployed in DoD and is what vendors like NVIDIA NIM build on. Using
  it explicitly (rather than letting customers wonder) addresses the
  "what production inference server do you use?" question with a
  clean answer.
- **Reversibility preserved.** vLLM is still the engine. A future
  customer who wants to drop Triton in favor of, say, a custom
  Ray Serve deployment can do so without changing the model files
  on disk.
- **No `docker compose down` blast radius.** Triton lives outside the
  GemmaForge compose project, so demo cleanup never accidentally
  disturbs the inference layer or the model catalog.

### Negative / accepted trade-offs

- **Two day-one validation gates in Phase 1**, both real risks:
  1. `distributed_executor_backend=ray` must work for `tp=2` Gemma 4
     31B-IT in EXPLICIT mode. The default backend is documented-broken
     with TP+EXPLICIT; the `ray` workaround is the published mitigation
     but is untested for our exact model.
  2. `pip install -U vllm` inside the Triton container must pick up
     Gemma 4 cleanly. Probably works (Triton vLLM backend just shells
     out to vLLM internally), but must be verified.
  
  If either gate fails, the contingency is documented in ADR-0013's
  Alternatives Considered (fall back to Gemma 4 26B MoE for the heavy
  roles, drop the wide Triton, return to a one-Triton-per-L4 pattern
  without `tp>1`).

- **More moving parts to operate** — five Triton systemd units
  instead of four vLLM containers. Mitigated by `triton@.service`
  template units, shared defaults under `/data/triton/config/`, and
  a host-setup script in Phase 0.5.

- **The shared host service introduces a coupling** between
  GemmaForge and the host's inference layer. We mitigate by making
  the client contract explicit (two env vars, never write to
  `/data/triton/`), documenting it in `docs/host-services.md`, and
  keeping the harness usable against any OpenAI-compatible endpoint
  for unit-test scenarios where Triton isn't available.

- **Triton's vLLM backend has a known streaming-unload bug**
  ([#7626](https://github.com/triton-inference-server/server/issues/7626))
  where models can fail to unload cleanly after streaming inference.
  We track this and add a workaround in Phase 1 if it bites; current
  expectation is that the Ralph loop's request pattern won't trigger
  it.

## References

- [Triton vLLM backend](https://github.com/triton-inference-server/vllm_backend)
- [Triton model management docs](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/model_management.html)
- [Triton vLLM backend README](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/vllm_backend/README.html)
- [vLLM Gemma 4 recipe](https://docs.vllm.ai/projects/recipes/en/latest/Google/Gemma4.html)
- [Issue #7786 — GPU selection bug forcing one-Triton-per-GPU](https://github.com/triton-inference-server/server/issues/7786)
- [Issue #7626 — streaming unload bug](https://github.com/triton-inference-server/server/issues/7626)
- [Triton release notes 25.08 — TP + EXPLICIT mode caveat](https://docs.nvidia.com/deeplearning/triton-inference-server/archives/triton-inference-server-2660/release-notes/rel-25-08.html)
- ADR-0001: vLLM as the inference server (superseded — engine choice still valid)
- ADR-0013: One Triton process per L4
- ADR-0015: Gemma 4 model lineup
