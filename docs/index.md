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
in April 2026 with native function calling and Day-0 vLLM support, I
saw an opportunity to explore a question that had been nagging me:
**can a smaller open-weights model at the tactical edge solve real
problems autonomously if you give it the right harness?**

Not by throwing a bigger model at it. Not by calling a cloud API. By
combining two ideas that hadn't been put together before: **Ralph
loop persistence** — where an agent doesn't quit when it fails but
keeps grinding, using external state to persist across context
boundaries — with **Reflexion-style self-improvement**, where each
failure produces a self-critique that makes the next attempt smarter.
I wanted to build that combined harness from scratch, understand every
design decision firsthand, and run it on a Dell PowerEdge XR7620 with
four NVIDIA L4 GPUs. No cloud dependency. No internet required.
Everything local.

**Why "GemmaForge"?** Gemma, obviously, because this is built around
Google's Gemma 4 model. And Forge because of what the system
represents — a controlled environment where raw material gets heated,
shaped, and refined through repeated cycles until it becomes something
useful. Raw model output goes in; the reflexion loop hammers it
against a deterministic evaluator; failures get reflected on and fed
back; and what comes out is a refined solution, or an honest
explanation of why the problem can't be solved yet. Each run leaves
the forge smarter than the last.

**Agent harnesses** have become a central topic in AI architecture
recently — the growing recognition that the orchestration layer around
a model matters as much as the model itself. How the harness manages
memory, handles failures, controls tool use, decides when to persist
versus when to escalate: these are the engineering decisions that
separate a demo from a deployable system. This project is my
exploration of those decisions, built from scratch and documented
along the way.

I designed the harness as an **extensible skill system** with a
skill-agnostic core and abstract interfaces that any use case can
implement. To stress-test what the architecture could handle, I
needed a use case that would push every part of it: persistence
across many retries, real side effects on a live system, a
deterministic evaluator with no ambiguity, and the need for safe
revert when things go wrong. **DISA STIG remediation** on Rocky
Linux 9 turned out to be a perfect fit — hardening a live VM against
270 security rules, where any individual fix can break SSH, sudo, or
the mission application, exercises the harness in ways that a
text-generation task never would. But the harness itself doesn't know
it's doing STIG. It processes work items through interfaces, and
adding a new skill is a folder and five small Python classes.

## Why all this documentation?

I built this project using an agentic coding workflow — a process
that's worth its own discussion (see
[journey/16](journal/journey/16-agentic-coding-as-a-method.md)).
Beyond sharing the source code, I wanted to capture the full process:
the insights, the gotchas, the dead ends, and the moments where
something finally clicked. Originally the notes were just for my own
learning, but looking back at them, I think there's real value in
making them public.

If you haven't yet explored the latest agentic coding tools, or
haven't taken on a project with one, I'd encourage you to read
through the journal entries. The learnings in there may be as
valuable as the finished product — maybe more so, because they
transfer to whatever you're building, not just STIG remediation.

For this effort, I wanted to focus as much on the journey as the
destination. Every failure mode is documented. Every pivot is
explained. Every architectural decision has a journal entry showing
what was tried, what broke, and what I landed on instead. I hope what
I learned helps other presales engineers, SI partners, and technical
evaluators build similar systems faster on their own hardware.

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
    specific, and written as I went — failures included. Start at
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

<div style="max-width: 900px; margin: 0 auto;">

<div markdown style="background: linear-gradient(135deg, rgba(239, 68, 68, 0.12), rgba(239, 68, 68, 0.04)); border: 1px solid rgba(239, 68, 68, 0.25); border-radius: 8px 8px 0 0; padding: 1.2rem 1.5rem;">

:material-numeric-5-circle:{ style="color: #EF4444; font-size: 1.4em; vertical-align: middle;" }
**Application**{ style="color: #EF4444; font-size: 1.1em;" }

STIG Remediation skill · GemmaForge Dashboard · This documentation site

*Where the user sees results. Skills are pluggable — STIG is the first, not the only.*

</div>

<div markdown style="background: linear-gradient(135deg, rgba(168, 85, 247, 0.12), rgba(168, 85, 247, 0.04)); border: 1px solid rgba(168, 85, 247, 0.25); border-left: 1px solid rgba(168, 85, 247, 0.25); border-right: 1px solid rgba(168, 85, 247, 0.25); padding: 1.2rem 1.5rem;">

:material-numeric-4-circle:{ style="color: #A855F7; font-size: 1.4em; vertical-align: middle;" }
**Orchestration**{ style="color: #A855F7; font-size: 1.1em;" }

Ralph Loop Harness · Google ADK · Skills System · Cross-run SQLite Memory · Adaptive Concurrency Clutch

*Where agents reason, reflect, and persist. The harness makes all structural decisions; the model makes all reasoning decisions.*

</div>

<div markdown style="background: linear-gradient(135deg, rgba(0, 118, 206, 0.12), rgba(0, 118, 206, 0.04)); border: 1px solid rgba(0, 118, 206, 0.25); padding: 1.2rem 1.5rem;">

:material-numeric-3-circle:{ style="color: #0076CE; font-size: 1.4em; vertical-align: middle;" }
**Model**{ style="color: #0076CE; font-size: 1.1em;" }

Gemma 4 31B Dense bf16 · vLLM 0.19.0 · Tensor Parallel = 4

*Where inference happens. Full precision, all four GPUs, ~14 tok/s sustained with no NVLink.*

</div>

<div markdown style="background: linear-gradient(135deg, rgba(16, 185, 129, 0.12), rgba(16, 185, 129, 0.04)); border: 1px solid rgba(16, 185, 129, 0.25); padding: 1.2rem 1.5rem;">

:material-numeric-2-circle:{ style="color: #10B981; font-size: 1.4em; vertical-align: middle;" }
**Platform / MLOps**{ style="color: #10B981; font-size: 1.1em;" }

OpenTelemetry · Jaeger · Prometheus · Grafana · Structured JSONL Run Logger

*Where you observe and measure. Federal-credible standards, no vendor lock-in.*

</div>

<div markdown style="background: linear-gradient(135deg, rgba(245, 158, 11, 0.12), rgba(245, 158, 11, 0.04)); border: 1px solid rgba(245, 158, 11, 0.25); border-radius: 0 0 8px 8px; padding: 1.2rem 1.5rem;">

:material-numeric-1-circle:{ style="color: #F59E0B; font-size: 1.4em; vertical-align: middle;" }
**Infrastructure**{ style="color: #F59E0B; font-size: 1.1em;" }

Dell PowerEdge XR7620 · 4x NVIDIA L4 24 GB · libvirt + virsh snapshots · Rocky Linux 9

*The foundation. A rugged 2U edge server — no cloud, no NVLink, air-gappable.*

</div>

</div>

<p style="text-align: center; margin-top: 1rem;">
<a href="journal/architecture/00-system-architecture/">View the full architecture with industry alternatives at each layer →</a>
</p>

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
