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

When Google released [Gemma 4](https://blog.google/technology/developers/gemma-4/)
in April 2026 with native function calling and Day-0 vLLM support, we
wanted to answer a simple question: **can a smaller open-weights model
at the tactical edge solve real problems autonomously if you give it
the right harness?**

Not by throwing a bigger model at it. Not by calling a cloud API. By
building a **reflexion harness** — a Ralph loop that fails, diagnoses,
reverts, reflects, and retries — and running it on a Dell PowerEdge
XR7620 with four NVIDIA L4 GPUs. No cloud dependency. No internet
required. Everything runs local.

The name comes from the concept of a forge: a controlled environment
where raw material is heated, shaped, and refined through repeated
cycles until it becomes something useful. That's what the harness
does with each run. Raw model output goes in; the reflexion loop
hammers it against a deterministic evaluator; failures get reflected
on and fed back; and what comes out is a refined solution — or an
honest explanation of why the problem can't be solved yet. Each run
leaves the forge smarter than the last.

**Agent harnesses** have become a central topic in AI architecture
over the past few months — the recognition that the orchestration
layer around a model matters as much as the model itself. How the
harness manages memory, handles failures, controls tool use, and
decides when to persist versus when to escalate: these are the
engineering decisions that separate a demo from a system. GemmaForge
is our exploration of those decisions, built from scratch, documented
as we went.

**DISA STIG remediation** on Rocky Linux 9 is the anchor use case
because it exercises every interesting property — persistence,
revert-on-failure, verifiable outcomes, real target-system side
effects. But the harness is skill-agnostic: adding a new use case
is a folder-per-skill exercise with no harness code changes. STIG is
the witness, not the point.

## Why this exists

We believe the journey matters more than the destination. This project
documents every step of building an agentic system at the edge — the
failures that taught us the most, the pivots we didn't expect, the
architectural patterns we discovered by breaking things. The
exploration *is* the product.

**Goal**: share what we learned so other presales engineers, SI
partners, and technical evaluators can build similar systems faster
on their own hardware of choice.

---

## What you'll find here

This site has six sections. Here's what each one is for:

<div class="grid cards" markdown>

-   :material-file-document-outline:{ .lg .middle } **Architecture Brief**

    ---

    The one-document overview. Covers the model, the harness, the
    hardware, the results, and the reading guide. **Start here** if
    you have 10 minutes.

    [:octicons-arrow-right-24: Read the brief](brief.md)

-   :material-map-marker-radius:{ .lg .middle } **Architecture**

    ---

    The 5-layer enterprise AI stack map with GemmaForge's components
    at each layer, industry alternatives (open-source and enterprise),
    and the six failure modes in reflexive agent harnesses.

    [:octicons-arrow-right-24: View the architecture](journal/architecture/00-system-architecture.md)

-   :material-book-open-variant:{ .lg .middle } **Journey**

    ---

    22 chronological field notes of how this was built. Honest,
    specific, and written as we went — failures included. Start at
    [the origin](journal/journey/00-origin.md) or jump to the
    [overnight run](journal/journey/14-overnight-run-findings.md)
    that changed everything.

    [:octicons-arrow-right-24: Read the journey](journal/journey/index.md)

-   :material-arrow-up-bold-circle:{ .lg .middle } **Improvements**

    ---

    Engineering specs for each architectural fix — the v3 and v5
    harness improvements, each with problem statement, mechanism,
    and verification criteria.

    [:octicons-arrow-right-24: View improvements](journal/improvements/01-architect-reengagement.md)

-   :material-lightning-bolt:{ .lg .middle } **Gotchas**

    ---

    13 atomic "X breaks Y because Z" lessons that cost hours to
    discover. If you're building something similar, start here to
    save yourself the pain.

    [:octicons-arrow-right-24: Browse the gotchas](journal/gotchas/index.md)

-   :material-bookshelf:{ .lg .middle } **Reference**

    ---

    ADRs for every non-obvious technical choice, plus the skill
    authoring guide for adding your own use case to the harness.

    [:octicons-arrow-right-24: View reference](adding-a-skill.md)

</div>

---

## The stack at a glance

| Layer | What it is | GemmaForge components |
|---|---|---|
| **5 — Application** | End-user solutions | STIG Remediation skill, Dashboard, this site |
| **4 — Orchestration** | Agents, harness, memory | Ralph Loop, Google ADK, skills system, cross-run SQLite memory |
| **3 — Model** | Inference | Gemma 4 31B bf16, vLLM 0.19.0, TP=4 |
| **2 — Platform** | Observability | OTel + Jaeger + Prometheus + Grafana |
| **1 — Infrastructure** | Hardware | Dell PowerEdge XR7620, 4x NVIDIA L4, libvirt, Rocky 9 |

See the [architecture overview](journal/architecture/00-system-architecture.md)
for the full picture with alternatives at each layer.

---

## Who this is for

- **Dell presales engineers and SEs** who need to understand edge AI
  well enough to have credible technical conversations with customers.
- **Federal technical evaluators** looking at what real-world agentic
  deployment looks like — including the parts that don't work.
- **SI partners and reseller teams** who want reference material to
  build their own demos and solutions.
- **Engineers building agent harnesses** — the
  [failure modes](journal/architecture/01-reflexive-agent-harness-failure-modes.md)
  piece is deliberately project-agnostic and applies to any
  reflexion-loop system.

---

!!! note "Personal Exploration"
    This is a personal project by Ken Rollins. It is not a Dell product,
    reference architecture, or supported offering.
    [Read the full disclaimer.](about.md)
