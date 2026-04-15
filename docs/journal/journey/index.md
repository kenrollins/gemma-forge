---
title: Journey Overview
---

# The Journey

First-person field notes of how GemmaForge was built. Chronological,
honest, specific. Each entry is scoped to a single moment in the
project — a decision, a discovery, a refactor, or a postmortem — and
is meant to be readable on its own.

## How to read this

- **Chronological**: entries are numbered in the order they happened.
  Decimal numbers (00.5, 06.5, etc.) mark mid-project entries added
  retroactively to cover moments the original numbering missed.
- **Self-contained**: each entry starts with a one-sentence hook and
  a "why this is its own entry" section that explains what this
  moment is about.
- **Cross-linked**: every entry lists the related entries at the top
  (via frontmatter) and links to them in the body where relevant.
- **Tagged**: every entry has layer, pattern, moment, and optional
  domain tags so you can find entries by topic in the site search.

## Entries, in order

### Phase 0 — Starting from scratch
- [**00. The Origin of GemmaForge**](00-origin.md) — why Ralph loops, why Gemma 4, why STIG as the anchor, and what the project explicitly *is not*.
- [**00.5. Can Gemma 4 Even Run Here?**](00.5-can-gemma-4-even-run-here.md) — the validation gate: does the 31B model fit on 4× L4, and what does NVFP4 actually look like in VRAM.

### Phase 1 — The inference layer
- [**01. The Inference Layer Evolution**](01-inference-layer.md) — Triton was the first choice, we pivoted to vLLM, and we kept the Triton scaffolding for when it catches up.
- [**02. Model Strategy**](02-model-strategy.md) — four configurations of the 31B tested on real hardware, and the one that worked.

### Phase 2 — The target VM
- [**04. VM Provisioning**](04-vm-provisioning.md) — OpenTofu + libvirt v0.9.7 + Rocky 9, and an hour of debugging a GRUB hang caused by missing ACPI features.

### Phase 3 — The harness
- [**06. Tool Calling**](06-tool-calling.md) — getting Gemma 4 to actually call tools through vLLM and ADK, and realizing our first "loop" was a script pretending to be an agent.
- [**06.5. The Stateful Loop Refactor**](06.5-stateful-loop-refactor.md) — replacing ADK's `LoopAgent` with a Python-driven outer loop and fresh per-turn sessions.
- [**07. The Skills System**](07-skills-system.md) — pulling STIG-specific logic into a skill manifest so other use cases are a folder-copy away.
- [**07.5. Virsh Console Fallback**](07.5-virsh-console-fallback.md) — the out-of-band recovery path for when SSH+sudo is broken, and the honest documentation of its current bug.

### Phase 4 — Iterating on the architecture
- [**08. Model Architecture Revision**](08-model-architecture-revision.md) — moving away from hardware-first role assignment to judgment-based roles.
- [**09. The Nemotron Experiment**](09-the-nemotron-experiment.md) — cross-model Auditor role, why it worked technically, and why we walked it back.
- [**10. The Parallelism Maze**](10-the-parallelism-maze.md) — every path we tried was blocked by a different constraint until only one option remained.

### Phase 5 — Observability
- [**03. Observability**](03-observability.md) — the OpenTelemetry stack, the dual-purpose decision.
- [**03.5. The LiteLLM Decision**](03.5-litellm-observability-decision.md) — the March 2026 supply chain incident, and the OTel-pure architecture that came out of it.
- [**05. Infrastructure Gap**](05-infrastructure-gap.md) — what "Day-0 model support" actually means when the surrounding stack hasn't caught up.
- [**12.5. Structured Run Logger**](12.5-structured-run-logger.md) — the boring-sounding JSONL decision that became the backbone of everything downstream.

### Phase 6 — The reflexion architecture
- [**11. The Missing Reflector**](11-the-missing-reflector.md) — realizing three agents wasn't actually reflexion and adding the fourth.
- [**12. bf16 TP=4 Full Precision**](12-bf16-tp4-full-precision.md) — the unexpected benchmark result that reshaped our production configuration.
- [**13. The Retry Budget That Wasn't Ralph**](13-ralph-persistence-retry-budget.md) — replacing the attempt counter with a wall-clock budget.

### Phase 7 — The overnight run and its aftermath
- [**14. The Overnight Run**](14-overnight-run-findings.md) — 10 hours, 2 rules remediated, 26 escalated, four architectural flaws discovered.
- [**15. The Test as Architecture Discovery**](15-the-test-as-architecture-discovery.md) — the discipline reframe that turned verification tests into property tests.
- [**15.5. The Test Pass in Practice**](15.5-test-pass-in-practice.md) — 99 tests across 7 tiers, the real bugs caught, the honest gaps.
- [**16. Capturing Lightning**](16-agentic-coding-as-a-method.md) — why the journal became the memory, and what happens when you don't stop to write it down.
- [**17. The v3 Fix Pass**](17-v3-fix-pass.md) — the narrative of the five architectural changes, in the order we made them.

### Phase 8 — The second overnight run and v4
- [**18. The Second Overnight Run**](18-second-overnight-run.md) — 93 rules remediated (78%), the time-waste ratio in the other 26, and three architectural findings for v4.
- [**19. Standing on Whose Shoulders?**](19-research-and-v4-architecture.md) — research validation of our choices, the literature landscape, and the v4 interface extraction decision.
- [**20. The Interface Extraction**](20-the-interface-extraction.md) — ripping the engine apart mid-flight: five interfaces, a STIG runtime, and 75 tests that still passed.
- [**21. The Task Graph**](21-task-graph-and-react-flow.md) — from flat queue to live DAG: dependency awareness, conflict detection, and a React Flow visualization.
- [**22. Context Graphs and the Memory Question**](22-context-graphs-and-the-memory-question.md) — the research spiral from decision provenance to NIST requirements to "do we even need a database?" — and how the clutch mechanism answered the question.

### Phase 9 — The first complete run and cross-run learning
- [**23. The First Complete Run**](23-first-complete-run.md) — 270 rules, 13.5 hours, 85 remediated, 157 escalated — and the discovery that the cross-run memory system was storing everything but teaching nothing.
- [**24. Run 2 — Cross-Run Learning**](24-run-2-cross-run-learning.md) — the fix landed: 59 rules flipped from escalated to remediated, the fix rate jumped 35% → 58%, then Run 2 exposed a new cascade and the uncomfortable question of whether memory that was right yesterday can be wrong tomorrow.
- [**25. Run 3 — When the Learning Curve Bends**](25-run-3-learning-plateaus.md) — 60% fix rate, diminishing returns, the environment fidelity problem showing up in real data.

## Related

- [Architecture overview](../architecture/00-system-architecture.md) — the same content organized by layer instead of by time.
- [Failure modes in reflexive agent harnesses](../architecture/01-reflexive-agent-harness-failure-modes.md) — the project-agnostic contribution piece.
- [Improvement proposals](../improvements/01-architect-reengagement.md) — the per-fix engineering docs that accompanied the v3 pass.
- [Gotchas](../gotchas/index.md) — the atomic "X breaks Y because Z" lessons.
