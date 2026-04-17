---
title: About
---

# About gemma-forge

## What this project is

gemma-forge is a personal exploration by **Ken Rollins**, Chief AI
Technology Strategist in Dell Federal, into two things in combination:

1. **Ralph loop architecture** — a pattern for autonomous-but-accountable
   agent systems that grind through problems with persistence, learning
   from each failure instead of giving up.
2. **Running Gemma 4 models at the edge** on commodity Dell hardware,
   without commercial agentic frameworks sitting between the harness
   and the infrastructure.

DISA STIG remediation was chosen as the anchor use case because it
exercises the interesting parts of the architecture — persistence,
revert-on-failure, verifiable outcomes, and real target-system side
effects. But the patterns documented throughout this project apply to
a wide range of problem spaces. STIG is the witness, not the point.

## What this project is not

- **Not an official Dell product.** There is nothing to buy. Nothing
  here is for sale.
- **Not a Dell reference architecture.** This work has not been
  produced, endorsed, or reviewed through any official Dell channel.
  Views, technical findings, and opinions represented here are the
  author's own and do not represent an official Dell position.
- **Not a commercial agentic-AI framework.** gemma-forge deliberately
  rolls its own harness on open components so the whole thing can be
  read, reasoned about, and reused. It is reference material, not a
  platform.
- **Not a benchmark.** Numbers in the journal entries describe
  specific measured outcomes on specific hardware in specific
  configurations. They are honest, but they are not normalized
  comparisons across platforms.

## Why this project exists

Most agentic-AI demos are beautiful when they work and have nothing
useful to say when they don't. They show the happy path, skip the
recovery, and hide the source so no one can learn from the build.

gemma-forge is the opposite. The code is open. Every failure mode is
documented. Every architectural decision has a journal entry
explaining what was tried, what failed, and what we landed on
instead. Every known limitation is called out honestly rather than
hidden. The exploration is the product; the STIG remediation is the
witness; the goal is to enable other engineers to build similar
systems faster and with less surprise.

## How the hardware fits in

Dell hardware is referenced throughout because it is what the author
works with day-to-day at Dell Federal. The techniques and patterns
described apply to any platform with comparable capabilities. The
specific lab environment this exploration ran on is a **Dell
PowerEdge XR7620 with 4× NVIDIA L4 GPUs**, a ruggedized 2U short-depth
chassis with no NVLink between the GPUs. The lessons transfer to
other Dell edge platforms (XE-series AI Factory nodes, other XR
chassis, Precision workstations with GPUs) and to non-Dell hardware
with similar constraints.

Working with the XR7620's specific constraints — particularly the
lack of NVLink and the four-way PCIe interconnect topology — is part
of the story. Several journey entries document how those constraints
shaped architectural decisions, and how some of them turned into
surprising strengths (see [journey/12](journal/journey/12-bf16-tp4-full-precision.md)).

## The collaboration

gemma-forge was built in an agentic coding workflow — a human operator
paired with an AI coding assistant, with the human making every
architectural and strategic decision and the AI contributing
implementation velocity, test coverage, and documentation drafting.
One of the unexpected insights from this process was that the
journal — written in real time as discoveries happened — became
more valuable than we expected. That realization has its own entry
at [journey/16 — Capturing Lightning](journal/journey/16-agentic-coding-as-a-method.md).

## License

gemma-forge is released under the **Apache License 2.0**, matching the
license of the Gemma 4 model family. See `LICENSE` in the repository
root for the full text.

## Who to contact

This is a personal project maintained by the author. Questions,
suggestions, and technical discussion are welcome through the
[GitHub issues](https://github.com/kenrollins/gemma-forge/issues) on
the project repo. For conversations about Dell hardware, Dell
Federal, or how this exploration relates to official Dell offerings,
please work through your existing Dell account team — this project
does not represent a commercial channel.
