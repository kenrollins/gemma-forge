# ADR-0002: Google ADK LoopAgent as the agent harness

- **Status:** Accepted
- **Date:** 2026-04-08
- **Deciders:** Ken Rollins

## Context

GemmaForge's central narrative is the **Ralph loop**: an agent that
fails, reasons through the failure, reverts, and retries until the
mission succeeds. This is the demo's headline — not first-try success.
We need an agent framework that:

1. Has a **first-class loop primitive** with explicit termination
   conditions, not just a generic "reAct" autoregressive loop.
2. Is **Apache-2.0** and self-hostable with no SaaS dependency.
3. Cleanly supports a **multi-agent role split** (Architect, Worker,
   Auditor, Sentry) where each role can be backed by a different model
   on a different GPU.
4. Can talk to **OpenAI-compatible vLLM endpoints directly** without a
   router layer (LiteLLM is explicitly ruled out — see ADR-0001).
5. Has **observability hooks** that we can wire to OpenTelemetry
   (ADR-0007).

## Decision

We use **Google's Agent Development Kit (ADK) for Python**
(`google/adk-python`), specifically the `LoopAgent` workflow primitive,
as the orchestration layer for the Ralph loop. Sub-agents (Architect,
Worker, Auditor, Sentry) are ADK `Agent` instances configured against
distinct vLLM endpoints via the OpenAI-compatible adapter.

## Alternatives considered

- **LangGraph (LangChain)** — Currently the most production-deployed
  Python agent framework in Federal pilots. Has explicit graph-based
  control flow, including loops. We did not pick it because (a) ADK's
  `LoopAgent` is purpose-built for exactly the workflow we need with
  less ceremony, (b) LangGraph pulls in the broader LangChain dependency
  tree which we'd rather avoid for an air-gap-clean reference build,
  and (c) ADK's role/sub-agent model maps more naturally onto our
  four-GPU role split. LangGraph remains a credible second choice if
  ADK's pre-1.0 API churns destabilize the build.

- **CrewAI** — Strong "multi-agent crew" abstractions. Less mature loop
  primitives; the natural pattern is autoregressive role-play rather
  than explicit termination on a measurable predicate (STIG = 100% AND
  mission app uptime intact). We need the latter, not the former.

- **AutoGen (Microsoft)** — Solid multi-agent conversation framework.
  Same objection as CrewAI: the loop primitive isn't shaped like ours.

- **Plain Python (no framework)** — Always tempting for a demo. We
  resisted because (a) we want the orchestration code to look idiomatic
  for the framework Federal customers will see in vendor reference
  architectures, not a one-off, and (b) ADK gives us trace
  instrumentation hooks for free that we'd otherwise build ourselves.

## Consequences

### Positive

- Native `LoopAgent` primitive lines up exactly with the
  Fail → Revert → Retry pattern; loop termination is a clean predicate
  rather than a heuristic.
- Multi-agent role split maps onto ADK sub-agents with no impedance
  mismatch — each sub-agent gets its own model endpoint, system prompt,
  and tool set.
- Open source, Apache-2, no SaaS coupling.
- ADK's instrumentation hooks integrate cleanly with OpenTelemetry
  (ADR-0007), so a Ralph-loop run produces a single coherent distributed
  trace across all four agents.

### Negative / accepted trade-offs

- **`google/adk-python` is pre-1.0** as of this writing (~v0.3 series,
  bi-weekly releases). The `LoopAgent` API surface is documented and
  stable enough to build on, but the broader package may rev breaking
  changes between phases. We mitigate by **pinning the ADK version
  tightly in `pyproject.toml`** (`google-adk>=0.3.0,<0.5.0`) and
  re-checking the [release notes](https://github.com/google/adk-python/releases)
  before each phase boundary. If ADK churns badly enough to threaten
  the demo timeline, the contingency is to fall back to LangGraph; the
  cost of that swap is bounded because the harness logic and the tool
  surface are framework-agnostic by design (`gemma_forge/harness/tools/`
  imports nothing from ADK).

- ADK's documentation and community are smaller than LangChain's. We
  accept the lower bus factor in exchange for the cleaner primitive.

## References

- [google/adk-python on GitHub](https://github.com/google/adk-python)
- [ADK documentation: Loop agents](https://google.github.io/adk-docs/agents/workflow-agents/loop-agents/)
- [adk-python releases](https://github.com/google/adk-python/releases)
- ADR-0001: vLLM as the inference server
- ADR-0007: OpenTelemetry primary, Langfuse secondary
