---
id: journey-00-origin
type: journey
title: "The Origin of GemmaForge"
date: 2026-04-09
tags: [L5-application, L4-orchestration, decision]
related:
  - journey/01-inference-layer
  - journey/02-model-strategy
  - architecture/00-system-architecture
  - architecture/01-reflexive-agent-harness-failure-modes
one_line: "How GemmaForge went from 'what could I show a Federal customer on an XR7620' to 'an exploration of Ralph loops and Gemma 4 at the edge with a roll-your-own harness.'"
---

# The Origin of GemmaForge

## The story in one sentence
GemmaForge began as an open-ended question about what to demo on the
Dell edge hardware in our lab, narrowed through several concept rounds
into "a reflexion-loop harness running Gemma 4 against a real target
system," and landed on DISA STIG remediation as the anchor use case
because it exercises all the parts of the architecture that matter.

## The question we started with

The Gemma 4 family landed on April 2, 2026. The release was significant
for edge deployments specifically: the first Gemma generation with
native multimodality, native function calling trained into the weights,
and a lineup that covers both flagship dense models and small efficient
variants. For anyone building demos on small-form-factor Dell hardware,
this was the moment the question changed from *"what can we run at the
edge"* to *"what can we **build** at the edge."*

The question we started with was simple:

> What could we credibly demonstrate to a Federal customer on commodity
> Dell edge hardware that would be interesting, truthful, and
> reproducible?

The lab environment was a Dell PowerEdge XR7620 with 4× NVIDIA L4 GPUs,
no NVLink, running Ubuntu 24.04. Nothing exotic. The same class of box
a tactical edge deployment would use in the field.

## What we rejected

Several ideas came up in the early concept rounds and got filtered out:

- **A big RAG demo over Federal documents.** Interesting but played out.
  Any vendor in the ecosystem can show a RAG demo in 2026, and they
  mostly look alike. Doesn't differentiate.
- **Multimodal SIGINT / ISR analysis.** Compelling but politically
  sensitive and hard to demo without real data. The kind of thing you
  talk about in a cleared briefing room, not something to build in a
  lab and publish.
- **Autonomous developer assistant.** Too close to what every vendor in
  the coding-assistant space already does. Hard to add value.
- **Predictive maintenance / digital twin.** Good use case but requires
  domain data we don't have. Would read as toy.

What pulled through all these rejections was a shared frustration with
most agentic demos: they are beautiful when they work but have nothing
to say when they don't. They don't show the *reasoning*. They don't
show the *recovery*. They don't show what happens when the agent makes
a mistake. For Federal customers, who have to defend every technology
choice to a CISO and an auditor, the "happy path with no failure mode"
demo is a non-starter.

## The shift: from "what to demo" to "what is interesting to explore"

The real unlock came when we stopped asking "what's the coolest demo"
and started asking "what's an interesting thing to **learn**." Three
ideas fit together once we framed it that way:

1. **Ralph loops** — the pattern of a persistence-first agent that
   grinds through a problem, failing and retrying and learning from
   each failure, until it either solves it or provably can't. This was
   the architectural heart of the idea from the start. It's the thing
   that distinguishes a reasoning system from a chatbot.

2. **Gemma 4 on edge hardware** — small enough to run on commodity
   edge GPUs, smart enough to do real agentic work, open-weights
   enough to deploy air-gapped. The first open model family where all
   three of those things were simultaneously true.

3. **Building our own agentic harness** — not using a commercial
   autonomous-agent product, not pulling in a closed framework, but
   implementing the loop end-to-end with open components so we
   understand every piece and can publish every line of it. Federal
   customers care about transparency and auditability; a roll-your-own
   harness is how you deliver both.

The combination of those three things became the actual project. The
question changed from "what should the demo be" to "what does it take
to build this kind of harness from scratch on this hardware, and what
can you learn about the edges of what's possible while doing it."

## Why DISA STIG remediation as the anchor

Once the architecture was in focus, we needed a use case that would
exercise it in a way that generated real data. Many options were on
the table. STIG remediation won for specific reasons, not because it
was the sexiest choice:

- **It has a verifiable outcome.** A STIG rule either passes or fails
  an OpenSCAP audit. No ambiguity, no judgment call. That matters for
  the reflexion loop because the loop needs a deterministic success
  signal to decide whether to keep going.
- **It has real side effects.** A STIG fix modifies a live system.
  There is actual, measurable damage possible. That makes revert-on-
  failure a first-class concern, which is exactly the harness feature
  Federal customers care about and the one most demos skip.
- **It's a universally relatable pain point.** Every Federal agency
  has a STIG compliance problem. Every one of them has an
  administrator who is terrified of breaking a mission app while
  hardening it. The demo's narrative lands immediately without
  explanation.
- **It rewards persistence.** Some STIG rules are trivial. Others are
  subtle, multi-step, and can easily break other rules. A reflexion
  loop that keeps trying and learning from failures has a real
  advantage over a one-shot script, which is exactly the story we
  wanted to tell.
- **It has a rich failure mode catalogue.** When things go wrong with
  STIG remediation, they go wrong in interesting ways: sudo breaks, a
  service won't restart, a config file has an unexpected comment that
  trips a regex. These are the kinds of failure modes that stress a
  harness design, and stress-testing is where we wanted to spend our
  time.

Critically, STIG is the **anchor** use case, not the **only** use
case. The skills system was in the design from the beginning
(`skills/stig-rhel9/` alongside planned `skills/cve-response/`,
`skills/service-recovery/`, etc.). The patterns documented here apply
to any problem space where a reflexion loop adds value. STIG is the
first witness.

## What the project is **not**

A few things are worth saying explicitly:

- GemmaForge is **not a product**. Nothing here is for sale. There is
  no "GemmaForge Enterprise" tier. The whole thing is personal
  exploration published as reference material.
- GemmaForge is **not an official Dell reference architecture**. The
  author works at Dell Federal and uses Dell hardware because that is
  the lab environment available, but this work has not been through
  official review and does not represent a Dell position.
- GemmaForge is **not a competitor to commercial agentic-AI
  products**. It is deliberately roll-your-own precisely because the
  goal is to teach the patterns, not to sell a solution. Readers are
  free — and encouraged — to take the ideas and use them with whatever
  tools fit their environment.

## What comes next in the journey

The rest of the journey entries document the actual build, in roughly
chronological order:

- [`journey/01-inference-layer`](01-inference-layer.md) — getting
  Gemma 4 running on the hardware (and why Triton was the first choice
  and vLLM the second)
- [`journey/02-model-strategy`](02-model-strategy.md) — four
  configurations of the 31B model tested, the one that worked, and why
- [`journey/03-observability`](03-observability.md) — the
  OpenTelemetry stack and the LiteLLM supply-chain decision
- [`journey/04-vm-provisioning`](04-vm-provisioning.md) — setting up
  the target VM with OpenTofu and libvirt
- [`journey/06-tool-calling`](06-tool-calling.md) — getting Gemma 4 to
  actually call tools through vLLM, which turned out to be the thing
  that separated a script pretending to be an agent from a real
  agentic harness
- ...and so on through the current state

Each entry is meant to be self-contained and useful to read on its own.
Taken together, they trace the shape of the exploration.
