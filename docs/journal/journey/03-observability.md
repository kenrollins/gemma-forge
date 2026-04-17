---
id: journey-03-observability
type: journey
title: "Journey: Observability — From Langfuse to OTel-Pure"
date: 2026-04-10
tags: [L2-platform-mlops, decision, supply-chain]
related:
  - gotchas/otel-spanmetrics-connector
one_line: "The original plan was Langfuse; it evolved to OTel primary, Langfuse secondary; then during Phase 0.5 I discovered security concerns with Langfuse and dropped it entirely in favor of an OTel-pure stack that is more Federal-credible anyway."
---

# Journey: Observability — From Langfuse to OTel-Pure

## The story in one sentence
The original PRD specified Langfuse; it evolved to "OTel primary,
Langfuse secondary"; then during Phase 0.5 I discovered Langfuse
was already running on the host AND had security concerns, which
was the push to drop it entirely in favor of an OTel-pure stack
that's more Federal-credible anyway.

## What I planned

The original PRD said Langfuse for tracing. During the interview phase,
I picked "Both" on Langfuse vs OTel — meaning OpenTelemetry as the
instrumentation standard with Langfuse as the LLM-friendly UI on top.

## What changed

### Discovery 1: Langfuse already running on the host

During Phase 0.5 host prep, `docker ps` revealed **29 containers**
already running on the XR7620, including `langfuse-web` and
`langfuse-worker` (6 days uptime). Also: `litellm`, a full Supabase
stack, Qdrant, ClickHouse, MinIO, Redis, Mattermost, Traefik, and
Unstructured.

This triggered the "shared host service" insight: if Langfuse is already
running, gemma-forge should connect to it as a client, not spin up its
own copy.

### Discovery 2: Langfuse has security issues

Langfuse had surfaced security issues that made migrating off it
attractive — specifically, wanting something more Federal-credible
that would run only on this host.

This was the tipping point. Building gemma-forge to depend on a product
the host operator is migrating away from is a strategic mistake.

### The pivot: OTel-pure

The OpenTelemetry GenAI semantic conventions (`gen_ai.*` attributes)
reached parity with Langfuse's data model in 2025. Everything Langfuse
shows about an LLM call has a vendor-neutral OTel equivalent:

| What | Langfuse | OTel GenAI equivalent |
|---|---|---|
| Token usage | Built-in dashboard | `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` → Prometheus counters |
| Prompt/completion content | Trace browser | `gen_ai.prompt` and `gen_ai.completion` span events → Jaeger |
| Cost tracking | Token × price | PromQL on token counters × configured price |
| Model identity | Trace metadata | `gen_ai.request.model`, `gen_ai.response.model` span attributes |
| Session/user grouping | Sessions tab | Custom span attributes (`forge.run_id`, `forge.skill`, `forge.role`) |

The replacement stack:
- **OTel Collector** — receives OTLP from the harness, fans out
- **Jaeger v2** — trace storage + UI (prompt/completion visible as span events)
- **Prometheus v3** — metrics (token counters with labels)
- **Grafana v11** — dashboards (token spend, latency, mission-app uptime)

Five containers vs Langfuse's six. Net headcount actually goes down.

## The Federal-credibility argument

Every Federal observability team already runs OTel + Prometheus + Grafana.
gemma-forge's traces are immediately legible in their existing tools
without translation. "We use the same observability stack you do" is a
stronger answer than "we use this third-party LLM-specific product you
haven't heard of."

## Key artifacts

- ADR-0007 — the full decision record
- `docker-compose.yml` — the OTel-pure stack (otel-collector, jaeger,
  prometheus, grafana)
- Memory: `project_existing_host_services.md` — inventory of what's
  already running
- Memory: `feedback_dont_touch_docker.md` — the "don't disturb existing
  containers" rule that the shared-host-service pattern codifies
