---
title: "gemma-forge: Architecture Brief"
---

# gemma-forge: Architecture Brief

> **An exploration of Ralph loop architecture and Gemma 4 at the edge —
> building your own agentic harness, from scratch.**
>
> By **Ken Rollins**, Chief AI Technology Strategist in Dell Federal.
>
> Repository: [github.com/kenrollins/gemma-forge](https://github.com/kenrollins/gemma-forge)
> · Site: [kenrollins.github.io/gemma-forge](https://kenrollins.github.io/gemma-forge/)

!!! note "Personal Exploration"
    This is a personal project. It is not a Dell product, reference
    architecture, or supported offering. Views and findings are the
    author's own.

---

## What this is

gemma-forge demonstrates that a smaller open-weights model on commodity
edge hardware, paired with the right harness architecture, can
autonomously solve complex multi-step problems — learning from every
failure, improving across runs, and producing a decision audit trail
that meets emerging Federal AI standards.

The architecture combines two patterns:

- **Ralph loop persistence** — the agent doesn't stop when it fails.
  It diagnoses, reverts, reflects, and retries until the problem is
  solved or the time budget expires.
- **Reflexion-style self-improvement** — each failure produces a
  distilled lesson that prevents the same mistake on the next attempt.
  Lessons persist across runs via a structured-tip memory store
  backed by Postgres and Neo4j (Graphiti), so the system gets smarter
  over time without any code changes.

**Two skills ship today** on the same harness:

- **DISA STIG remediation** on Rocky Linux 9 — the anchor use case.
  Exercises every interesting property of the architecture:
  persistence across many retries, revert-on-failure, verifiable
  outcomes, and real target-system side effects. 270 rules, 7 runs
  including a 19-hour overnight completion.
- **CVE Response** on Rocky Linux 9 — the second-skill validation.
  Autonomous advisory remediation via Vuls (scan) and `dnf advisory`
  (apply), with per-package-family reboot batching and snapshot
  rollback per family. Two complete runs (baseline + STIG-hardened
  starting state), 44/44 advisories remediated in 35 minutes,
  every one first-try.

STIG is the hard case. CVE is the easy case. Both run on the same
harness without modification. The harness is skill-agnostic: adding
a new use case is a folder-per-skill exercise with no harness
modifications.

---

## The model: Gemma 4 31B Dense

Google's [Gemma 4](https://blog.google/technology/developers/gemma-4/)
(released April 2, 2026) is the first open-weights model family with
native function calling and Day-0 vLLM support. gemma-forge uses the
**31B Dense Instruct** variant in **bf16 full precision** — no
quantization, no compromises on reasoning quality.

**Why bf16 over quantized variants?** We tested four configurations
on the same hardware. The bf16 full-precision configuration at TP=4
delivered the best balance of throughput and reasoning quality. The
NVFP4 quantized variant promised a smaller memory footprint (the naive
estimate was 15.5 GB) but the real footprint was 22 GB because
attention layers stay in bf16 — and the reasoning quality degradation
wasn't worth the modest VRAM savings.

**Key model characteristics:**

| Parameter | Value |
|-----------|-------|
| Architecture | 31B Dense, bf16 full precision |
| Context window | 128K tokens (native) |
| Function calling | Native (Gemma 4 tool-call format) |
| Parallelism | Tensor Parallel = 4 across all 4 GPUs |
| KV cache | ~6.5 GB across 4 GPUs at bf16 (TP=4 shards the KV heads) |
| Throughput | ~14 tok/s sustained (TP=4 on 4× L4, no NVLink) |

The model serves all three agent roles (Architect, Worker, Reflector)
through a single vLLM instance. This simplifies operations and keeps
the supply chain to one model weight file.

---

## The inference engine: vLLM

[vLLM 0.19.0](https://docs.vllm.ai/) provides the OpenAI-compatible
REST interface. Key architectural decisions:

- **Direct REST, no proxy.** No LiteLLM, no commercial API gateway.
  The harness talks directly to vLLM's `/v1/chat/completions` endpoint.
  This decision was driven by a [March 2026 supply chain incident](https://kenrollins.github.io/gemma-forge/journal/journey/03.5-litellm-observability-decision/)
  in the LiteLLM ecosystem.
- **Tensor Parallelism = 4** across all four L4 GPUs. This is
  determined by model architecture, not operator preference — the 31B
  Dense model's attention head count divides evenly across 4 GPUs.
- **`--tool-call-parser gemma4`** required. Without this flag, vLLM
  rejects Gemma 4's native tool-call format with a 400 error.
- **Continuous batching** handles concurrent agent requests natively,
  which enables future parallel worker execution.

---

## The hardware: Dell PowerEdge XR7620

| Component | Specification |
|-----------|---------------|
| Platform | Dell PowerEdge XR7620 (short-depth rugged edge server) |
| CPUs | 2× Intel Xeon Gold 6442Y (96 cores total) |
| Memory | 256 GB DDR5 |
| GPUs | 4× NVIDIA L4 24 GB (no NVLink between cards) |
| GPU Driver | NVIDIA 580 |
| OS | Ubuntu 24.04 LTS |
| Interconnect | PCIe Gen4 ×16 per GPU (no NVLink — each GPU is independent) |

The XR7620 is a 2U short-depth server designed for tactical edge
deployment — data centers, forward operating bases, retail locations,
or anywhere that needs GPU compute in a rugged, portable form factor.
The same architecture applies to any Dell edge platform with NVIDIA
GPUs: PowerEdge R760xa, XE9680, or the XR8620.

The **no-NVLink constraint** is deliberate: the L4 is a single-slot
inference GPU without NVLink bridges. Tensor parallelism works over
PCIe, but the bandwidth math is different — PCIe Gen4 ×16 provides
~32 GB/s per direction vs. NVLink's 600+ GB/s. This means TP=4 on
L4s has higher inter-GPU latency than on A100/H100, but at the edge
this is the trade-off: four affordable inference GPUs in a server you
can carry under one arm.

---

## The harness: Ralph loop with reflexion

The harness is ~4,000 lines of Python built on [Google ADK](https://github.com/google/adk-python)
for per-agent-turn machinery, with a Python-driven outer reflexion
loop. The harness makes all structural decisions (retry policy,
evaluation, revert, termination); the model makes all reasoning
decisions (which item to work on, what approach to try, why it
failed).

### The loop

```
OUTER: Architect selects a work item from the task graph
INNER (time-budgeted per item):
  1. Worker generates a fix/change
  2. HARNESS evaluates deterministically (no LLM — real scanner)
  3. If PASS → checkpoint progress, advance to next item
  4. If FAIL → classify failure mode, revert, Reflector analyzes
  5. Reflector distills a one-sentence lesson → episodic memory
  6. Architect re-engages periodically: CONTINUE / PIVOT / ESCALATE
```

### Three agent roles

| Role | Responsibility | Tools |
|------|---------------|-------|
| **Architect** | Selects work items, plans approaches, decides when to pivot or escalate | Scan tool (skill-provided) |
| **Worker** | Generates and applies fixes/changes to the target | Apply tool (skill-provided) |
| **Reflector** | Analyzes failures, distills lessons, recommends bans | None (pure reasoning) |

### Memory tiers

- **Working memory** — per-attempt conversation. Cleared each turn
  via fresh ADK sessions to prevent context pollution.
- **Episodic memory** — per-item attempt history. Distilled lessons
  (not raw text) keep the context compact.
- **Semantic memory** — cross-item banned patterns, preferred
  approaches, and strategic lessons. Persists for the entire run.
- **Persistent memory (V2)** — cross-run structured tips stored in
  Postgres with a Graphiti-on-Neo4j knowledge graph for causal
  relationships. Tips carry explicit mechanism fields (*why*
  something works, not just that it did), per-(tip, rule) utility
  tracking, and automatic eviction of low-utility tips. The harness
  starts smarter on Run 2+ with no code changes. See
  [ADR-0016](adr/0016-graphiti-neo4j-postgres-memory-stack.md) for
  why SQLite (V1) was retired and what replaced it.

### Evaluation triage

The evaluator classifies every failure into a structured mode, and
the harness responds differently to each. The first four are
harness-core modes; the last three were added by the CVE skill and
are extension points available to any skill:

| Mode | Meaning | Response |
|------|---------|----------|
| **Health failure** | The fix broke the target | Immediate revert |
| **Evaluator gap** | Target healthy but evaluator says fail | Count toward scanner-gap early escalation |
| **False negative** | Evaluator passed but noise triggered revert | Accept the fix (don't revert good work) |
| **Clean failure** | Normal failure | Revert + reflect + retry |
| **Needs reboot** *(CVE)* | Package upgraded, live verification pending reboot | Defer to post-loop per-family batch |
| **RPM conflict** *(CVE)* | dnf dependency conflict | Clean failure with diagnostic hint |
| **Policy violation** *(CVE)* | Ban-worthy approach (e.g., `dnf remove` as a "fix") | Immediate revert + ban the approach |

The last three were contributed by the CVE skill via the harness's
`FailureMode` extension point — the harness itself stays
skill-agnostic; skills declare the modes their evaluator can return.

### Deferred-verification architecture

Not every skill can evaluate immediately after an apply. CVE's
kernel and glibc advisories need a reboot before verification is
meaningful. The harness supports this via three skill-provided
extension points:

- **`deferrable_failure_modes`** on `EvaluatorMetadata` — tells the
  harness which failure modes should be deferred rather than
  escalated ("the fix was applied, but I can't verify it yet").
- **`SkillRuntime.resolve_deferred(reason, items, emit)`** — a
  post-loop phase the harness invokes after the main queue drains.
  The skill owns the resolution mechanics (reboot, snapshot revert,
  healthcheck) and returns one `DeferredItemOutcome` per item with
  a skill-chosen `reason` string (`family_verified`,
  `family_reboot_failed`, etc.) that becomes the authoritative
  verdict — no re-evaluation.
- **`EmitEvent` callback** — lets the skill stream structured
  progress events during long-running resolution phases so the UI
  has a narrative to paint instead of silence.

CVE uses all three for per-package-family reboot batching. STIG
declares no deferrable modes and doesn't touch any of this code.
See [entry 37](journal/journey/37-per-family-reboot-batching-landed.md)
for the architectural landing and
[ADR on deferred verification](journal/journey/36-per-family-reboot-batching.md)
for the design rationale.

---

## Skill-agnostic architecture

The harness operates on five abstract interfaces. Skills implement
them for their domain:

| Interface | Purpose | STIG implementation | CVE implementation |
|-----------|---------|---------------------|--------------------|
| **WorkQueue** | Produce work items | OpenSCAP scan | Vuls scan |
| **Executor** | Apply changes | SSH + bash fix | SSH + `dnf upgrade --advisory=<ID>` |
| **Evaluator** | Check results | OpenSCAP + health checks | `dnf updateinfo` + mission health |
| **Checkpoint** | Save/restore state | libvirt VM snapshots | libvirt VM snapshots (including per-family) |
| **SkillRuntime** | Bundle the above | STIG-specific wiring | CVE-specific wiring + `resolve_deferred` for reboot batches |

Adding a new skill: create a `skills/<name>/` folder with a manifest,
prompts, and a `runtime.py` implementing the five interfaces. The
same task graph, evaluation triage, cross-run memory, and
deferred-verification machinery work for any skill. CVE added three
new harness extension points (`FailureMode` additions,
`deferrable_failure_modes`, `DeferredItemOutcome`) without any
changes to the harness loop itself — STIG never touches them and
they stay inert for skills that don't need them.

**Candidate future skills:**

- **Red-team/nuclei active verification** — work items are nuclei
  findings; evaluator is the absence of the finding on re-probe;
  checkpoint is a VM snapshot. Pairs with CVE as a closed-loop
  verify-after-patch pipeline.
- **Certificate rotation** — work items are certs; evaluator checks
  TLS handshake; deferred-verification for propagation waits.
- **Windows STIG** — same shape as Rocky STIG but against a
  Windows Server target. Different scanner, same harness.

---

## Observability

| Component | Role |
|-----------|------|
| **OpenTelemetry** | Instrumentation standard — spans emitted once, consumed by multiple backends |
| **Jaeger** | Distributed tracing — per-request trace visualization |
| **Prometheus** | Metrics collection — throughput, latency, GPU utilization |
| **Grafana** | Dashboards — operational monitoring |
| **gemma-forge Dashboard** | Live task graph heatmap, agent activity, event stream |

The dashboard renders a **waffle-chart heatmap** of all work items,
color-coded by state (green = completed, cyan = active, amber =
escalated, gray = queued). Categories are visually grouped so the
audience can see progress patterns at a glance. An interactive React
Flow DAG view provides zoom/pan/click-to-inspect for dependency
exploration.

---

## Results

### STIG — the hard case

Seven complete runs of the 270-rule DISA STIG profile. Each run
builds on the previous via cross-run memory. Numbers below are from
**Run 6**, which landed the ordering constraint (auto-dependency
respect on immutable cascades) and retired 356 low-utility tips via
auto-consolidation:

| Metric | Run 6 |
|--------|-------|
| Rules attempted | 270 |
| Fix rate | **61.9%** (+5.6pp vs Run 5) |
| Wall time | 19.1 hours |
| `audit_rules_immutable` cascade | Position **84/84** (fully absorbed; was 11/83 in Run 5) |
| Mechanism field compliance on tips | 100% (781 tips) |
| Low-utility tips retired at run-end | 356 (auto-consolidation) |

STIG is where the reflexion loop earns its keep — multi-attempt
fixes, Architect re-engagements, Reflector plateaus, and genuine
strategy pivots are routine.

### CVE — the easy case

Two complete runs (stock Rocky 9 baseline + STIG-hardened starting
state). Same harness, same loop, same Gemma 4 deployment:

| Metric | CVE Run |
|--------|---------|
| Advisories attempted | 44 |
| Remediated | **44/44 (100%)** |
| Wall time | 35 minutes |
| Remediated on attempt 1 | **29/29** (every single one) |
| Architect re-engagements | 0 |
| Reflector plateaus | 0 |
| Reboot-required advisories | 15, batched into 2 families (1 glibc + 14 kernels) |
| Reboots issued | 2 (one per family) |

CVE is the opposite shape: dnf is deterministic, Vuls is
deterministic, the reflexion machinery stayed quiet because no rule
needed it. Same code path, load-adaptive. That the harness handles
both without modification is the whole point.

### Architecture evolution

| Version | Capability |
|---------|-----------|
| v1 | Basic retry loop |
| v2 | Reflexion (reflect on failure) |
| v3 | Episodic memory + architect re-engagement + per-turn action budget |
| v4 | Skill-agnostic interfaces + task graph + evaluation triage |
| v5 | Cross-run memory (V1: SQLite dream pass), adaptive concurrency clutch (built, deferred behind UI work) |
| v5+V2 memory | Structured tips, rule-prefix similarity, per-(tip, rule) utility tracking, history-based eviction; SQLite retired for Postgres + Neo4j (Graphiti) |
| v5+ordering | Dependency-aware ordering constraint (entry 34); closed the STIG immutable cascade |
| v5+CVE | Second skill + per-family reboot batching + `DeferredItemOutcome` contract + `EmitEvent` observability (entries 33-37) |

Each version is documented in the [developer journal](https://kenrollins.github.io/gemma-forge/journal/journey/)
with honest failures, pivots, and discoveries.

---

## Decision provenance and Federal AI compliance

Every autonomous action the harness takes is captured in a structured
JSONL event stream with full provenance:

- What was attempted and why
- What the evaluator found
- Why the reflector said it failed
- What the architect decided (CONTINUE / PIVOT / ESCALATE)
- What lessons were distilled for future attempts

This aligns with the [NIST AI Agent Standards Initiative](https://www.nist.gov/caisi/ai-agent-standards-initiative)
(February 2026) requirements for chain-of-custody logging, prompt
provenance, and audit trails for autonomous agent actions. The
decision trace is not a bolt-on compliance feature — it is how the
architecture works.

---

## Technology stack summary

| Layer | Component | Why |
|-------|-----------|-----|
| **Model** | Gemma 4 31B Dense bf16 | Open weights, native tool calling, Day-0 vLLM support |
| **Inference** | vLLM 0.19.0, TP=4 | Direct OpenAI-compatible REST, continuous batching |
| **Harness** | Python + Google ADK | Ralph loop + reflexion, skill-agnostic interfaces |
| **Memory** | Postgres + Neo4j (Graphiti) | Structured-tip utility tracking, causal-graph relationships, history-based eviction |
| **Target** | libvirt VM + virsh snapshots | Two-tier revert safety (script + full-state snapshot) |
| **Observability** | OTel + Jaeger + Prometheus + Grafana | Federal-credible, no vendor lock-in |
| **Frontend** | Next.js + React Flow | Live heatmap + interactive DAG + activity ticker |
| **Hardware** | Dell PowerEdge XR7620, 4× L4 | Rugged edge, no NVLink, air-gappable |

---

## How to learn more

**Start here:**

- [**Architecture Overview**](https://kenrollins.github.io/gemma-forge/journal/architecture/00-system-architecture/) —
  the 5-layer map with components and industry alternatives
- [**Failure Modes in Reflexive Agent Harnesses**](https://kenrollins.github.io/gemma-forge/journal/architecture/01-reflexive-agent-harness-failure-modes/) —
  the project-agnostic contribution piece

**If you have 15 minutes:**

- [**The Second Skill: CVE Response**](https://kenrollins.github.io/gemma-forge/journal/journey/33-second-skill-cve-pivot/) —
  the pivot that tested the skill-agnostic thesis.
- [**Run 6 — Ordering Works, Runtime Doesn't**](https://kenrollins.github.io/gemma-forge/journal/journey/34-run-6-ordering-works-runtime-doesnt/) —
  the STIG run that closed the immutable cascade.
- [**Per-Family Reboot Batching Lands**](https://kenrollins.github.io/gemma-forge/journal/journey/37-per-family-reboot-batching-landed/) —
  44/44 CVE remediations, two families batched, zero retries.

**If you want the full story:**

- [**Developer Journal**](https://kenrollins.github.io/gemma-forge/journal/journey/) —
  37 chronological field notes from origin through the CVE skill.
  Start at [Entry 00: Origin](https://kenrollins.github.io/gemma-forge/journal/journey/00-origin/)
  or jump to whatever catches your eye. Two favorites for early
  readers: [the 10-hour overnight run](https://kenrollins.github.io/gemma-forge/journal/journey/14-overnight-run-findings/)
  that found four architectural flaws, and
  [the second overnight run](https://kenrollins.github.io/gemma-forge/journal/journey/18-second-overnight-run/)
  that validated the fixes.

**If you're building something similar:**

- [**Gotchas**](https://kenrollins.github.io/gemma-forge/journal/gotchas/) —
  13 atomic "X breaks Y because Z" lessons that cost hours to discover
- [**Adding a Skill**](https://kenrollins.github.io/gemma-forge/adding-a-skill/) —
  how to author a new skill for the harness
