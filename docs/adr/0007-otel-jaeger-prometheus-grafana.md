# ADR-0007: OpenTelemetry + Jaeger + Prometheus + Grafana for observability (no Langfuse)

- **Status:** Accepted
- **Date:** 2026-04-09
- **Deciders:** Ken Rollins
- **Related:** [ADR-0014](0014-triton-vllm-director-shared-host-service.md)

## Context

The Ralph loop's value depends on its **audit trail**. Every prompt,
every completion, every tool invocation, and every revert event has to
be captured, queryable, and presentable to a Federal evaluator who
wants to answer questions like:

- *"Show me everything Architect said in step 4 of run abc123."*
- *"How many tokens did the Worker model burn this month, broken down
  by skill?"*
- *"How long did the Auditor take to validate the mission app on each
  iteration of the last run?"*
- *"Did the harness ever exceed our token budget for a single run?"*

We also have two non-negotiable constraints:

1. **Federal-credible.** The observability stack has to be the same
   stack a Federal evaluator already runs. Anything that requires them
   to learn a new product is a credibility tax.
2. **Air-gappable, locally-hostable, no SaaS.** Same constraint as
   ADR-0001 / ADR-0014: zero phone-home, zero vendor activation, runs
   100% on the XR7620 with the network cable pulled.

The original PRD specified **Langfuse** for LLM observability, and an
earlier draft of this decision (in `docs/adr/0007-*` planning) had
"OpenTelemetry primary, Langfuse secondary" with Langfuse there for
its LLM-native UI. Two facts changed the calculus:

1. **The OpenTelemetry GenAI semantic conventions
   (`gen_ai.*` attributes and events) are ratified and widely adopted
   as of 2025–2026.** Token usage, prompt/completion content, model
   identity, and agent/workflow context are all first-class span
   attributes / span events under the official OpenTelemetry spec.
   Anything Langfuse exposes about an LLM call has a vendor-neutral
   equivalent in OTel.

2. **Langfuse has had documented security issues** that make it a
   problematic dependency for a Federal-leaning reference build, and
   the host operator (Ken) is independently considering migrating
   off Langfuse for unrelated workloads. Building gemma-forge to
   depend on a product the host is moving away from is a strategic
   mistake regardless of whether the security issues are
   user-impacting today.

## Decision

gemma-forge adopts an **OpenTelemetry-pure observability stack** with no
Langfuse dependency. The full stack runs locally inside the gemma-forge
`docker-compose.yml`:

| Layer | Component | Role |
|---|---|---|
| **Instrumentation** | OpenTelemetry Python SDK in `gemma_forge.observability` | Emits OTLP spans and metrics from the harness using **OpenTelemetry GenAI semantic conventions** (`gen_ai.*` attributes and events) |
| **Collector** | `otel/opentelemetry-collector-contrib` | Receives OTLP from the harness; fans out to Jaeger (traces) and Prometheus (metrics); extracts `gen_ai.usage.*` attributes into Prometheus counters |
| **Trace storage + UI** | **Jaeger v2** (OTLP-native) | Trace storage and the human-readable trace browser. Each LLM call's prompt and completion are visible inline as span events. |
| **Metrics storage** | **Prometheus v3** | Stores time-series metrics including token counters per `{model, role, skill, run_id}` |
| **Dashboards** | **Grafana v11** | Token-accounting dashboards, latency views, mission-app uptime, GPU memory, all via PromQL against Prometheus and trace queries against Jaeger |

### Token accounting via OTel GenAI semantic conventions

Every LLM call from the harness emits an OTel span tagged using the
**ratified OpenTelemetry GenAI semantic conventions**:

```
span: gemma_forge.architect.generate
  attributes:
    gen_ai.system              = "vllm"
    gen_ai.request.model       = "gemma4-31b-it"
    gen_ai.request.max_tokens  = 4096
    gen_ai.response.id         = "..."
    gen_ai.response.model      = "gemma4-31b-it"
    gen_ai.response.finish_reasons = ["stop"]
    gen_ai.usage.input_tokens  = 1234
    gen_ai.usage.output_tokens = 567
    forge.skill                = "stig-rhel9"
    forge.role                 = "architect"
    forge.run_id               = "run-abc123"
    forge.iteration            = 4
  events:
    gen_ai.prompt:     <full system prompt + user message>
    gen_ai.completion: <full model response, including any <thought> tokens>
```

The OTel collector forwards the spans whole to Jaeger (so a human can
read the prompt/completion content for any selected call) and extracts
the `gen_ai.usage.*` attributes into Prometheus counters with labels:

- `gen_ai_input_tokens_total{model, role, skill, run_id}`
- `gen_ai_output_tokens_total{model, role, skill, run_id}`

Grafana queries those counters via PromQL and renders dashboards like:
*Total tokens per skill per day*, *Token spend per Ralph-loop run*,
*Tokens by role*, *Token blowup detection*. The same metrics power
alerts and quotas without writing gemma-forge-specific code.

## Alternatives considered

- **Langfuse** (the original PRD choice) — Excellent LLM-native UI for
  trace browsing and token accounting. Rejected for three reasons:
  (a) the OTel GenAI semantic conventions reached parity with
  Langfuse's data model in 2025, eliminating the LLM-specific
  capability gap that was Langfuse's original justification;
  (b) Langfuse has had documented security issues that make it a
  problematic dependency for a Federal-leaning reference build;
  (c) the host operator is migrating off Langfuse for unrelated
  workloads, so building gemma-forge to depend on it now is a
  strategic mistake. The OTel-pure path is more Federal-credible
  and avoids the maintenance/security lifecycle of a third-party
  product entirely.

- **Grafana LGTM stack (Loki / Grafana / Tempo / Mimir)** — The
  modern Grafana observability stack. Excellent fit for production
  fleet observability, fully open source, all Apache-2. Considered
  seriously. Rejected because it's heavier than we need for a
  single-host demo: Tempo + Mimir + Loki is three separate storage
  backends versus Jaeger + Prometheus's two, and the operational
  complexity isn't justified at our scale. We may revisit if a
  Federal customer wants to deploy gemma-forge into an existing LGTM
  environment — at which point the OTel collector simply gets a
  different exporter target, which is exactly the portability win
  the OTel-pure architecture buys us.

- **Honeycomb / DataDog / New Relic / Lightstep** — Excellent SaaS
  observability backends. Rejected on the same air-gap / no-SaaS
  constraint that drove ADR-0001 and ADR-0014. None of them are
  Federal-deployable in classified or air-gapped environments.

- **Just write trace events to a SQLite file** — Considered as a
  zero-dependency fallback. Rejected because it doesn't tell the
  Federal "we use the same observability stack you do" story, and
  because Jaeger + Prometheus are essentially zero-dependency
  themselves (one container each, no external storage required).

- **OTel collector → SQLite or DuckDB** — Would let us drop Jaeger
  and Prometheus in favor of a single embedded analytics database.
  Tempting for simplicity, but Jaeger's UI is the actual value add
  for the demo (operators want to click on a span and see the
  prompt/completion). Reinventing that UI is more work than running
  Jaeger.

## Consequences

### Positive

- **Federal-credible by construction.** Every Federal observability
  team already runs OTel + Prometheus + Grafana, frequently with
  Jaeger as the trace backend. gemma-forge's traces are immediately
  legible in their existing tools without translation.
- **Token accounting is queryable.** PromQL on
  `gen_ai_*_tokens_total{...}` answers any question about token
  spend by any dimension we care to label. No proprietary query
  language to learn.
- **Vendor-neutral by standard.** The harness emits standard OTel
  GenAI spans. Swapping any backend (e.g., to Grafana Tempo, to
  Honeycomb, to a customer's existing collector) is one config
  change, not a code change.
- **No SaaS dependency, no phone-home, no license activation.**
  Extends the air-gap-clean property of the inference layer to the
  observability layer.
- **One fewer security maintenance lifecycle to track.** Dropping
  Langfuse means we don't carry its CVEs, patch cadence, or
  upstream-product risk into a Federal reference build.
- **Forward-compatible with the eventual whitepaper.** The "we use
  open standards end-to-end" story is much cleaner than "we use
  open standards plus this one third-party product."

### Negative / accepted trade-offs

- **Jaeger's trace browser is more generic than Langfuse's LLM-native
  UI.** A user who wants to see a prompt and its completion side by
  side has to expand the span and read the events. Mitigated by
  Phase 6's gemma-forge dashboard, which can deep-link to a Jaeger
  trace by ID and (optionally) render a prettier prompt/completion
  view inline using the OTel events.
- **Five observability containers** (`otel-collector`, `jaeger`,
  `prometheus`, `grafana`, plus one Grafana provisioning sidecar
  if needed) vs Langfuse's six. Net headcount goes down, but the
  components are more numerous in concept. Mitigated by clear
  scoping under the `observability` Compose profile and a single
  `make obs-up` Makefile target.
- **No built-in "user feedback" or "annotation" UI** (which Langfuse
  provides for human-in-the-loop labeling of LLM responses).
  Acceptable: gemma-forge is a fully autonomous Ralph loop, not a
  human-in-the-loop chatbot. If we ever need human labeling, we add
  it as a separate skill rather than coupling it into the
  observability stack.
- **Custom Grafana dashboards have to be authored.** Mitigated by
  shipping a `infra/observability/grafana/dashboards/` directory
  with provisioned dashboards for the standard views (token spend,
  Ralph loop iterations, agent latency).

## References

- [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
- [Jaeger v2 (OTel-native)](https://www.jaegertracing.io/docs/2.0/)
- [Prometheus](https://prometheus.io/)
- [Grafana](https://grafana.com/oss/grafana/)
- [OpenTelemetry Collector Contrib](https://github.com/open-telemetry/opentelemetry-collector-contrib)
- ADR-0014: Triton-managed vLLM director (shared host service)
- ADR-0012: `/data/<service>/` host layout convention
