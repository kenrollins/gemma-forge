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
one_line: "How GemmaForge went from 'what could we credibly show a Federal customer on an XR7620' to 'an exploration of Ralph loops and Gemma 4 at the edge with a roll-your-own harness.'"
---

# The Origin of GemmaForge

The project began at the intersection of two interests. The first was Ralph loops and agentic harness architectures: the pattern where a persistent agent grinds through a problem, failing and retrying and learning from each failure, until it either solves it or provably can't. Reading Ghuntley's Ralph writeup and the Reflexion paper was enough to see the shape of it, but not enough to understand it. The best way to actually understand a pattern like this is to build one yourself.

The second was Gemma 4, released April 2, 2026. A new open-weights model family with native multimodality, native function calling, and a lineup spanning efficient 2B and 4B variants up through a dense 31B flagship — small enough to run on commodity edge GPUs, smart enough for real agentic work, and open-weights so the whole stack can be inspected and modified. Open-weights tool calling had been possible for a while with varying reliability, and Gemma 4 was a chance to see how far the capability had come when it was designed into the model rather than coaxed out of it.

Those two ideas formed the basis for a test project: build an agent harness from scratch around Gemma 4, run it on ruggedized edge hardware like the Dell PowerEdge XR7620, and use it to explore what a Federal customer could credibly deploy in an air-gapped environment. Early brainstorming leaned on the multimodality — disconnected maintenance assistants, audio-driven field recon, ISR aggregation, local SIGINT triage — but none of those offered the volume of independent test cases needed to really stress the harness, and they all pulled focus toward the model when the interesting questions were really about the orchestration around it.

## The question that changed everything

Once the harness moved to the center of the project, the question changed from "what should this system demonstrate" to "what kind of work would actually exercise an agent harness end-to-end." The deeper I looked, the more excited I got about the possibilities.

The project really came down to two goals. The first was to build an agentic harness from scratch and use it to explore what current agent architectures actually entail — the tool-calling patterns, the memory design, the failure modes, the operational reality of running an agent for hours at a time rather than just prompting a chatbot. The second was to put Gemma 4 through some interesting paces: see how one of the larger variants (31B dense or 27B MoE, still undecided at this point) handles sustained agentic work, understand where the smaller E4B and E2B variants fit, and find out whether native function calling is as reliable in practice as the benchmarks suggest.

Sitting underneath those two goals was the beginning of a harder question, still forming at this stage: is the architecture itself viable? Can a harness that persistently throws tokens at a model until it succeeds — with retries, reflection, and memory — actually work as an approach, or does it fall apart under real load? That question matters most at the edge, where running the latest frontier model is often not an option: air-gapped networks, constrained power, ruggedized hardware, sovereignty requirements. If the architecture holds up, it changes what a smaller, deployable model can be trusted to do in those environments.

Implementation-wise, the project stayed inside the Google ecosystem: Gemma 4 as the model and Google's Agent Development Kit as the agent framework. ADK had shipped an update alongside the Gemma 4 release, and it had been a while since I had spent serious time with it. Keeping the moving parts minimal made it easier to stay focused on the harness itself.

The combination needed a name for the repo. "Forge" won out because that is roughly what a Ralph loop does: refine something inside a self-contained environment over repeated cycles until it is ready to come out.

## Why STIG remediation earned the anchor slot

With the architecture in focus, the use case needed to stress it properly. Several candidates came up — legacy code migration, automated log scrubbing, self-healing tactical network configs — but STIG remediation won because it was ruthlessly honest.

A STIG rule either passes or fails an OpenSCAP audit. No ambiguity, no judgment call. The reflexion loop gets a deterministic success signal, which makes it possible to measure whether persistence actually helps or whether the agent is just thrashing. A STIG fix modifies a live system, so real damage is possible, which makes revert-on-failure a first-class concern instead of a footnote. And every Federal agency has a STIG compliance problem and an administrator who is terrified of breaking a mission app while hardening a box, so the narrative lands without explanation.

Best of all, STIG rules fail in *interesting* ways. Sudo breaks. A service won't restart. A config file has an unexpected comment that trips a regex. The audit system gets locked into immutable mode. Exactly the failure modes that stress a harness design, and stress-testing was where I wanted to spend the time.

STIG is the anchor, not the only use case. The skills system was in the design from the start: `skills/stig-rhel9/` alongside planned stubs for other compliance and operations scenarios. The patterns apply to any problem where a reflexion loop adds value. STIG is the first witness.

## What this is not

GemmaForge is not a product, not an official Dell reference architecture, and not a competitor to commercial agentic-AI platforms. It is personal exploration published as reference material, built to teach the patterns, not to sell a solution.
