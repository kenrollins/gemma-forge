---
title: GemmaForge
hide:
  - navigation
  - toc
---

# GemmaForge

!!! quote ""
    **An exploration of Ralph loop architecture and Gemma 4 at the edge — building your own agentic harness, from scratch.**

    By **Ken Rollins**, Chief AI Technology Strategist in Dell Federal.

---

## What this is

GemmaForge is a personal exploration into two things in combination:

- **Ralph loop architecture** as a pattern for autonomous-but-accountable
  agent systems that grind through problems with persistence, learning
  from each failure.
- **Running Gemma 4 models at the edge** on commodity Dell hardware,
  without commercial harness software in the way.

**DISA STIG remediation** was chosen as the anchor use case because it
exercises the interesting parts of the architecture — persistence,
revert-on-failure, verifiable outcomes, real target-system side
effects — but the patterns documented here apply to a wide range of
problem spaces.

## Why this exists

Most agentic-AI demos are beautiful when they work and have nothing to
say when they don't. They show the happy path and skip the recovery.
For technical evaluators who have to defend an architecture to a CISO,
the happy-path demo is a non-starter.

GemmaForge is the opposite. Every failure mode is documented. Every
recovery path is written down. Every architectural decision has a
journal entry explaining what was tried, what failed, and what we
landed on instead. The exploration is the product; the STIG
remediation is the witness.

**Goal**: share what we learned so other presales engineers, SI
partners, and technical evaluators can build similar systems faster
on their own hardware of choice.

---

## Start here

<div class="grid cards" markdown>

-   :material-map-marker-radius:{ .lg .middle } **System Architecture**

    ---

    The 5-layer enterprise AI stack map, the components GemmaForge uses
    at each layer, and the patterns that drill into each one.

    [:octicons-arrow-right-24: View the architecture](journal/architecture/00-system-architecture.md)

-   :material-book-open-variant:{ .lg .middle } **Journey**

    ---

    First-person field notes of how this was built. Chronological,
    honest, specific. Start at
    [journey/00 — Origin](journal/journey/00-origin.md) or jump to any
    entry that catches your eye.

    [:octicons-arrow-right-24: Read the journey](journal/journey/00-origin.md)

-   :material-lightbulb-alert:{ .lg .middle } **Failure Modes**

    ---

    A project-agnostic taxonomy of six failure modes in reflexive agent
    harnesses, with prescribed harness mechanisms for each. The
    contribution artifact that came out of the project.

    [:octicons-arrow-right-24: Read the failure modes](journal/architecture/01-reflexive-agent-harness-failure-modes.md)

-   :material-lightning-bolt:{ .lg .middle } **Gotchas**

    ---

    Small atomic lessons that cost hours to discover. If you are
    building something similar, this is where to look to save yourself
    the pain.

    [:octicons-arrow-right-24: Browse the gotchas](journal/gotchas/index.md)

</div>

---

## The 5-Layer Enterprise AI Partner Map

GemmaForge is organized around a standard five-layer view of the
enterprise AI stack. Every entry in this journal is tagged with which
layer(s) it touches, so you can filter the whole thing by "just the
orchestration content" or "just the infrastructure content" and
quickly find the parts relevant to what you're building.

| Layer | What it is | GemmaForge components |
|---|---|---|
| **5 — Application** | Vertical SaaS AI, end-user applications | STIG Remediation skill, Dashboard UI, this site |
| **4 — Orchestration** | RAG pipelines, agents, LLM frameworks | Ralph Loop Harness, Google ADK, skills system, memory tiers |
| **3 — Model** | Foundation models, inference engines | Gemma 4 31B bf16, vLLM, TP=4 configuration |
| **2 — Platform / MLOps** | Observability, lifecycle, monitoring | OTel + Jaeger + Prometheus + Grafana, structured run logger |
| **1 — Data / Infrastructure** | Storage, compute, hypervisor, hardware | Dell PowerEdge XR7620, 4× NVIDIA L4, libvirt, Rocky Linux 9 |

See the [full architecture page](journal/architecture/00-system-architecture.md)
for components, industry alternatives at each layer (open-source and
enterprise), and the patterns that drill into each layer.

---

## Who this is for

- **Dell presales engineers and SEs** who need to understand what's
  possible on Dell edge hardware well enough to have credible
  technical conversations with their own customers.
- **Federal technical evaluators** looking at edge AI hardware and
  wanting to see what real-world agentic deployment looks like,
  including the parts that don't work.
- **SI partners and reseller technical teams** who want reference
  material they can extend to build their own demos and customer
  solutions.
- **Engineers building reflexive agent harnesses anywhere** — the
  failure modes piece, in particular, is deliberately project-agnostic
  and applies to any reflexion-loop system.

See also: the [About](about.md) page for the full project disclaimer.
