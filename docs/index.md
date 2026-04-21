---
title: gemma-forge
hide:
  - navigation
  - toc
---

# gemma-forge

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

The bet: a smaller model can punch above its weight if the harness
around it is doing the right work. Specifically, by combining two
architectures I hadn't really seen used together
before: [**Ralph loop persistence**](https://ghuntley.com/ralph/) —
where an agent doesn't quit when it fails but keeps grinding, using
external state to persist across context boundaries — with
[**Reflexion-style self-improvement**](https://arxiv.org/abs/2303.11366),
where each failure produces a self-critique that makes the next
attempt smarter. I wanted to build that combined harness from scratch,
understand every design decision firsthand, and run it on a Dell
PowerEdge XR7620 with four NVIDIA L4 GPUs. No cloud dependency. No
internet required. Everything local.

**Why "gemma-forge" as a project name?** Gemma, obviously, because
this is built around Google's Gemma 4 model. And Forge because of
what the system represents — a controlled environment where raw
material gets heated, shaped, and refined through repeated cycles
until it becomes something useful. Raw model output goes in; the
reflexion loop hammers it against a deterministic evaluator; failures
get reflected on and fed back; and what comes out is a refined
solution, or an honest explanation of why the problem can't be solved
yet. Each run leaves the forge smarter than the last.

**Why build my own harness?** Agent harnesses have become a central
topic in AI architecture recently — there's a growing recognition
that the orchestration layer around a model matters as much as the
model itself. How the harness manages memory, handles failures,
controls tool use, decides when to persist versus when to escalate:
these are the engineering decisions that separate a demo from a
deployable system. I wanted to understand those decisions by making
them myself, not by inheriting them from a framework.

Lastly, I designed the harness as an **extensible skill system** —
a skill-agnostic core with abstract interfaces that any use case can
implement. **DISA STIG remediation** on Rocky Linux 9 is the anchor
use case because it pushes every part of the architecture —
persistence across many retries, real side effects on a live system,
a deterministic evaluator with no ambiguity, and the need for safe
revert when things go wrong. Any individual fix across its 270 rules
can break SSH, sudo, or the mission application.

To validate the skill-agnostic thesis, I added **CVE Response** as a
second skill — autonomous advisory remediation driven by Vuls
(scan) and `dnf advisory` (apply), with per-package-family reboot
batching and snapshot rollback per family. The two skills run on the
same harness, the same Gemma 4 deployment, and the same four-agent
reflexion loop. The harness itself doesn't know which workflow it's
doing: it processes work items through interfaces, and adding a new
skill is a folder and five small Python classes.

## Why all this documentation?

I built this project using an agentic coding workflow — a human
and an AI coding partner building together at speed. Beyond sharing
the source code, I wanted to capture the full process:
the insights, the gotchas, the dead ends, and the moments where
something finally clicked. Originally the notes were just for my own
learning, but looking back at them, I think there's real value in
making them public.

For this project, I decided to have my agentic coding partner capture
into a journal the critical insights, decisions, successes, and
failures as they were happening. For me, the focus for this effort
was as much about the journey as the destination. So if you have
time, explore the journal entries. Every failure mode is documented.
Every pivot is explained. Every architectural decision has an entry
showing what was tried, what broke, and what I landed on instead.
If you haven't yet tried building your own project with an agentic
coding system, I hope this gives you some insight into the process
and encourages you to try. It's one of the most engaging and
rewarding ways to learn — the velocity is real, the collaboration
is genuine, and the results will surprise you.

I hope what I learned helps other presales engineers, SI partners,
and technical evaluators build similar systems faster on their own
hardware.

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

    The 5-layer enterprise AI stack map with gemma-forge's components
    at each layer, industry alternatives (open-source and enterprise),
    and the six failure modes in reflexive agent harnesses.

    [:octicons-arrow-right-24: View the architecture](journal/architecture/00-system-architecture.md)

-   :material-book-open-variant:{ .lg .middle } **Journey**

    ---

    Chronological field notes of how this was built. Honest,
    specific, and written as I went — failures included. Start at
    [the origin](journal/journey/00-origin.md), jump to the
    [overnight run](journal/journey/14-overnight-run-findings.md)
    that changed everything, or skip to
    [the CVE pivot](journal/journey/33-second-skill-cve-pivot.md) and
    [per-family reboot batching](journal/journey/37-per-family-reboot-batching-landed.md)
    for the latest work.

    [:octicons-arrow-right-24: Read the journey](journal/journey/index.md)

-   :material-arrow-up-bold-circle:{ .lg .middle } **Improvements**

    ---

    Engineering specs for each architectural fix — the v3 and v5
    harness improvements, each with problem statement, mechanism,
    and verification criteria.

    [:octicons-arrow-right-24: View improvements](journal/improvements/01-architect-reengagement.md)

-   :material-lightning-bolt:{ .lg .middle } **Gotchas**

    ---

    Atomic "X breaks Y because Z" lessons that cost hours to
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

<div class="layer-stack" style="max-width: 900px; margin: 0 auto;">

<div style="background: linear-gradient(135deg, rgba(239, 68, 68, 0.15), rgba(239, 68, 68, 0.05)); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 8px 8px 0 0; padding: 1.2rem 1.5rem;">
<h3 style="color: #EF4444;">&#9316; Layer 5 — Application</h3>
<p style="margin: 0.3rem 0;"><strong>STIG Remediation Skill</strong> · <strong>CVE Response Skill</strong> · <strong>gemma-forge Dashboard</strong> · <strong>This Documentation Site</strong></p>
<p style="margin: 0.3rem 0; opacity: 0.7; font-style: italic; font-size: 0.85rem;">Where the user sees results. Two skills ship today — STIG hardening and CVE remediation — running on the same harness. Adding a third is a folder and five Python classes.</p>
</div>

<div style="background: linear-gradient(135deg, rgba(168, 85, 247, 0.15), rgba(168, 85, 247, 0.05)); border: 1px solid rgba(168, 85, 247, 0.3); border-top: none; padding: 1.2rem 1.5rem;">
<h3 style="color: #A855F7;">&#9315; Layer 4 — Orchestration</h3>
<p style="margin: 0.3rem 0;"><strong>Ralph Loop Harness</strong> · <strong>Google ADK</strong> · <strong>Skills System</strong> · <strong>Cross-run Memory (Postgres + Neo4j/Graphiti)</strong> · <strong>V2 Structured Tips</strong></p>
<p style="margin: 0.3rem 0; opacity: 0.7; font-style: italic; font-size: 0.85rem;">Where agents reason, reflect, and persist. The harness makes structural decisions; the model makes reasoning decisions.</p>
</div>

<div style="background: linear-gradient(135deg, rgba(0, 118, 206, 0.15), rgba(0, 118, 206, 0.05)); border: 1px solid rgba(0, 118, 206, 0.3); border-top: none; padding: 1.2rem 1.5rem;">
<h3 style="color: #0076CE;">&#9314; Layer 3 — Model</h3>
<p style="margin: 0.3rem 0;"><strong>Gemma 4 31B Dense bf16</strong> · <strong>vLLM 0.19.0</strong> · <strong>Tensor Parallel = 4</strong></p>
<p style="margin: 0.3rem 0; opacity: 0.7; font-style: italic; font-size: 0.85rem;">Where inference happens. Full precision across all four GPUs, ~14 tok/s sustained, no NVLink required.</p>
</div>

<div style="background: linear-gradient(135deg, rgba(16, 185, 129, 0.15), rgba(16, 185, 129, 0.05)); border: 1px solid rgba(16, 185, 129, 0.3); border-top: none; padding: 1.2rem 1.5rem;">
<h3 style="color: #10B981;">&#9313; Layer 2 — Platform / MLOps</h3>
<p style="margin: 0.3rem 0;"><strong>OpenTelemetry</strong> · <strong>Jaeger</strong> · <strong>Prometheus</strong> · <strong>Grafana</strong> · <strong>Structured JSONL Run Logger</strong></p>
<p style="margin: 0.3rem 0; opacity: 0.7; font-style: italic; font-size: 0.85rem;">Where you observe and measure. Federal-credible standards, no vendor lock-in.</p>
</div>

<div style="background: linear-gradient(135deg, rgba(245, 158, 11, 0.15), rgba(245, 158, 11, 0.05)); border: 1px solid rgba(245, 158, 11, 0.3); border-top: none; border-radius: 0 0 8px 8px; padding: 1.2rem 1.5rem;">
<h3 style="color: #F59E0B;">&#9312; Layer 1 — Infrastructure</h3>
<p style="margin: 0.3rem 0;"><strong>Dell PowerEdge XR7620</strong> · <strong>4x NVIDIA L4 24 GB</strong> · <strong>libvirt + virsh snapshots</strong> · <strong>Rocky Linux 9</strong></p>
<p style="margin: 0.3rem 0; opacity: 0.7; font-style: italic; font-size: 0.85rem;">The foundation. A rugged 2U short-depth edge server — no cloud, air-gappable, built for the tactical edge.</p>
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
