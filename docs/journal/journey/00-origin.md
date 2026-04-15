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

The project began at the intersection of two interests. The first was Ralph loops and agentic harness architectures: the pattern where a persistent agent grinds through a problem, failing and retrying and learning from each failure, until it either solves it or provably can't. Reading Ghuntley's Ralph writeup and the Reflexion paper was enough to see the shape of it, but not enough to understand it. The best way to actually understand a pattern like this is to build one yourself — end-to-end, with open components, every piece chosen deliberately.

The second was Gemma 4, released April 2, 2026. A new open-weights model family with native multimodality, native function calling baked into the weights, and a lineup spanning efficient 2B and 4B variants up through a dense 31B flagship. Small enough to run on commodity edge GPUs without reaching back to a cloud, smart enough for real agentic work, and open-weights so the whole stack can be inspected, modified, and redistributed. Open-weights tool calling had been possible for a while, with varying degrees of reliability across the ecosystem, and Gemma 4 was a chance to see how far that capability had come when it was designed into the model rather than coaxed out of it.

Those two ideas formed the basis for a test project: build a reflexive agent harness from scratch around Gemma 4, run it on ruggedized edge hardware like the Dell PowerEdge XR7620, and use it to explore what a Federal customer could credibly deploy in an air-gapped environment. The first brainstorming threads were pattern-matched off the multimodality: disconnected maintenance "expert in a box" assistants using the vision encoder, audio-driven field recon, multi-stream ISR aggregation, local SIGINT triage. Each one was technically plausible. None of them felt like the right story to tell, and none of them offered a clear way to generate the volume of independent test cases needed to really stress the harness.

The multimodal ideas were pulling the project toward the model's capabilities when the interesting questions were really about the harness around it. How does the system behave when a step fails? What does recovery look like? Can learning from one attempt carry into the next? None of those are answered by adding another input modality. They are answered by the orchestration layer — the part worth building in the first place. Gemma 4's capabilities were genuinely interesting on their own, but I kept drifting back to the harness: what it could enable, what structure it should have, and what new kinds of work it could take on if the model's published tool-calling and reasoning benchmarks held up under real use.

## The question that changed everything

Once the harness moved to the center of the project, the question changed from "what should this system demonstrate" to "what kind of work would actually exercise an agent harness end-to-end." The deeper I looked at that question, the more excited I got about the possibilities.

Three threads pulled together almost immediately. First, Ralph loops: the pattern where a persistent agent grinds through a problem, failing and retrying and learning from each failure, until it either solves it or provably can't. That was the architectural heart from the start. It is the thing that separates a reasoning system from a chatbot wearing a tool belt.

Second, Gemma 4 on edge hardware. The native function calling meant the model could drive a real tool-use loop without the brittle JSON-parsing hacks that older open-weights models required. The 31B dense variant gave enough reasoning quality to handle agentic tasks at full precision, and the smaller E4B and E2B variants opened up specialized supporting roles if needed. The whole family fits within the XR7620's 96 GB of aggregate VRAM and can run completely disconnected.

Third, building the harness from scratch with the goal of starting small and open. Google had shipped an update to its Agent Development Kit alongside the Gemma 4 release, and it had been a while since I had spent any real time with their ADK. Staying inside the Google ecosystem for the model and the agent framework kept the moving parts to a minimum and gave me an excuse to dig back into ADK with fresh eyes.

The combination needed a name so the repo could exist on GitHub. "Forge" stuck because that is roughly what a Ralph loop does: refine something inside a self-contained environment over repeated cycles until it is ready to come out. Pair it with the model and the project had a name.

## Why STIG remediation earned the anchor slot

With the architecture in focus, we needed a use case that would stress it properly. Several candidates came up: legacy code migration (fail a lot, eventually succeed), automated log scrubbing, self-healing tactical network configs. Each had merit. STIG remediation won, not because it was glamorous, but because it was ruthlessly honest.

A STIG rule either passes or fails an OpenSCAP audit. No ambiguity, no judgment call. The reflexion loop gets a deterministic success signal, which means we can measure whether persistence actually helps or whether the agent is just thrashing. A STIG fix modifies a live system, so there is real, measurable damage possible, which makes revert-on-failure a first-class concern instead of a footnote. And every Federal agency has a STIG compliance problem and an administrator who is terrified of breaking a mission app while hardening a box, so the narrative lands without explanation.

Best of all, STIG rules fail in *interesting* ways. Sudo breaks. A service won't restart. A config file has an unexpected comment that trips a regex. The audit system gets locked into immutable mode. These are exactly the failure modes that stress a harness design, and stress-testing was where I wanted to spend the time.

STIG is the anchor use case, not the only one. The skills system was in the design from the start. `skills/stig-rhel9/` alongside planned stubs for other compliance and operations scenarios. The patterns here apply to any problem where a reflexion loop adds value. STIG is the first witness.

## What this is not

GemmaForge is not a product, not an official Dell reference architecture, and not a competitor to commercial agentic-AI platforms. It is personal exploration published as reference material, built to teach the patterns, not to sell a solution.
