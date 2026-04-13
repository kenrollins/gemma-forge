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
combining two patterns that hadn't been put together before: **Ralph
loop persistence** — where the agent doesn't stop when it fails but
grinds through with retries and external state — with **Reflexion-style
self-improvement** — where each failure produces a verbal self-critique
that makes the next attempt smarter. We built that combined harness
from scratch and ran it on a Dell PowerEdge XR7620 with four NVIDIA L4
GPUs. No cloud dependency. No internet required. Everything runs local.

**Why GemmaForge?** Gemma, obviously, from building this around
Google's new Gemma 4 model. And Forge because of what it represents:
a controlled environment where raw material is heated, shaped, and
refined through repeated cycles until it becomes something useful.
That's what the harness does with each run. Raw model output goes in;
the reflexion loop hammers it against a deterministic evaluator;
failures get reflected on and fed back; and what comes out is a
refined solution — or an honest explanation of why the problem can't
be solved yet. Each run leaves the forge smarter than the last.

**Agent harnesses** have become a central topic in AI architecture
over the past few months — the recognition that the orchestration
layer around a model matters as much as the model itself. How the
harness manages memory, handles failures, controls tool use, and
decides when to persist versus when to escalate: these are the
engineering decisions that separate a demo from a system. GemmaForge
is our exploration of those decisions, built from scratch, documented
as we went.

The harness is designed as an **extensible skill system** — a
skill-agnostic core with abstract interfaces that any use case can
implement. To deeply explore what this architecture can do, we wanted
a use case that would stress every part of it: persistence across
many retries, real side effects on a live system, deterministic
evaluation with no ambiguity, and the need for safe revert when
things go wrong. **DISA STIG remediation** on Rocky Linux 9 fit
perfectly. Hardening a live VM against 270 security rules — where
each fix can break SSH, sudo, or the mission application — exercises
the harness in ways that a text-generation task never would. But the
harness doesn't know it's doing STIG. It processes work items through
interfaces. Adding a new skill is a folder and five small Python
classes. STIG is the witness, not the point.

## Why this exists

In addition to sharing the source code, we wanted to record and
journal the entire process of creating this — the insights, the
gotchas, the failures, and the eureka moments as we went. Originally
this was just for our own learning, but there's real value in sharing
it publicly. If you haven't yet taken a deep look at the latest
agentic coding tools, or haven't built your own project with one,
take some time and read through the journal notes. The learnings and
insights in there may be just as valuable as the final product.

Life before death. Strength before weakness. *Journey before
destination.* This project is documented the way it was built — one
step at a time, with honest accounting of what worked and what didn't.
Every failure mode is written down. Every pivot is explained. Every
architectural decision has a journal entry showing what was tried,
what broke, and what we landed on instead.

We hope what we learned helps other presales engineers, SI partners,
and technical evaluators build similar systems faster on their own
hardware of choice.

---

## Explore the site

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

## The 5-Layer Enterprise AI Stack

<div class="grid" markdown>

<div markdown style="background: rgba(239, 68, 68, 0.08); border-left: 4px solid #EF4444; padding: 1rem 1.2rem; border-radius: 0 8px 8px 0; margin-bottom: 0.5rem;">

**Layer 5 — Application**{ style="color: #EF4444" }

STIG Remediation skill, GemmaForge Dashboard, this documentation site.
*Where the user sees value.*

</div>

<div markdown style="background: rgba(168, 85, 247, 0.08); border-left: 4px solid #A855F7; padding: 1rem 1.2rem; border-radius: 0 8px 8px 0; margin-bottom: 0.5rem;">

**Layer 4 — Orchestration**{ style="color: #A855F7" }

Ralph Loop Harness, Google ADK, skills system, cross-run SQLite memory,
adaptive concurrency clutch. *Where agents reason, reflect, and persist.*

</div>

<div markdown style="background: rgba(34, 211, 238, 0.08); border-left: 4px solid #22D3EE; padding: 1rem 1.2rem; border-radius: 0 8px 8px 0; margin-bottom: 0.5rem;">

**Layer 3 — Model**{ style="color: #22D3EE" }

Gemma 4 31B bf16 full precision, vLLM 0.19.0, Tensor Parallel = 4.
*Where inference happens — 14 tok/s on 4x L4 with no NVLink.*

</div>

<div markdown style="background: rgba(16, 185, 129, 0.08); border-left: 4px solid #10B981; padding: 1rem 1.2rem; border-radius: 0 8px 8px 0; margin-bottom: 0.5rem;">

**Layer 2 — Platform / MLOps**{ style="color: #10B981" }

OpenTelemetry + Jaeger + Prometheus + Grafana, structured JSONL run
logger. *Where you observe and measure.*

</div>

<div markdown style="background: rgba(245, 158, 11, 0.08); border-left: 4px solid #F59E0B; padding: 1rem 1.2rem; border-radius: 0 8px 8px 0; margin-bottom: 0.5rem;">

**Layer 1 — Infrastructure**{ style="color: #F59E0B" }

Dell PowerEdge XR7620, 4x NVIDIA L4 24 GB, libvirt + virsh snapshots,
Rocky Linux 9 target VM. *The edge hardware that makes it sovereign.*

</div>

</div>

See the [architecture overview](journal/architecture/00-system-architecture.md)
for the full picture — including industry alternatives at each layer
so you can map this to your own environment.

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
