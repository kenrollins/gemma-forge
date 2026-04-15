---
id: journey-19-research-and-v4-architecture
type: journey
title: "Standing on Whose Shoulders? Research, Validation, and the v4 Architecture Decision"
date: 2026-04-12
tags: [L4-orchestration, reflexion-loop, context-management, decision]
related:
  - journey/18-second-overnight-run
  - journey/06.5-stateful-loop-refactor
  - architecture/01-reflexive-agent-harness-failure-modes
one_line: "Before building v4, I stopped to ask whether I was reinventing wheels — researched the state of Ralph loops, Reflexion agents, and their combination, validated architectural choices against the literature, and designed the v4 interface boundary that separates harness concerns from skill concerns."
---

# Standing on Whose Shoulders?

## The story in one sentence

After two overnight runs and five architectural fixes, I paused
before committing to v4 to ask: is this doing something novel, is it
catching up with what others did months ago, and were the right
choices made — because ripping and replacing now is cheaper than
discovering I was wrong after three more improvements.

## Why this is its own entry

This is a decision moment, not a coding moment. The research changed
what got built next and how the project is framed. Without it, v4
would have been three more STIG-specific improvements. With it, v4
became an interface extraction that makes the harness skill-agnostic.

---

## What I found

### Ralph loops: established pattern, narrow application

The "Ralph Wiggum technique" was coined by Geoffrey Huntley in May
2025. Named after the lovably persistent Simpsons character, it
describes a bash loop that feeds an AI coding agent a prompt
repeatedly until the task is done. Progress persists in files and git
history, not in the LLM's context window.

Key implementations: [snarktank/ralph](https://github.com/snarktank/ralph)
(10K+ stars), [vercel-labs/ralph-loop-agent](https://github.com/vercel-labs/ralph-loop-agent),
and an official Claude Code plugin. All focus on autonomous coding
tasks.

**What I learned**: Ralph loops are widely adopted for code
generation but there's no evidence of production use for
infrastructure remediation or compliance enforcement. The pattern
as commonly understood is simpler than what this project is
building — persistence through external state, not multi-agent
reflexion with deterministic evaluation.

### Reflexion agents: solid academic foundation

Reflexion (Shinn et al., NeurIPS 2023, 443+ citations) is the key
paper. After each task attempt, the agent generates a verbal self-
critique, stores it in episodic memory, and uses it to guide retries.
LangGraph and Google ADK both have official implementations.

Key follow-on: **MAR (Multi-Agent Reflexion, Dec 2025)** extends the
pattern to multi-agent settings and notes a critical limitation:
*Reflexion can reinforce earlier mistakes on complex tasks*. This
matches exactly what we observed in the scanner-semantic-gap cases —
the model kept trying variations of a fundamentally wrong approach.

**VIGIL (Dec 2025)** is architecturally closest to GemmaForge: a
reflective runtime that supervises agents with mostly-deterministic
code, using the LLM only for high-level reasoning. Its
Roses/Buds/Thorns diagnostic structure is similar to the post-mortem
capture here.

### The combination: emerging, thin, and nobody's doing infrastructure

Thomas Chong published a Medium post explicitly combining Ralph loops
with ADK LoopAgent. The R^5 framework (Relax, Reflect, Reference,
Retry, Report) operationalizes the pattern in ADK. But these are
blog-post-level integrations aimed at coding tasks.

**Nobody is doing fail-diagnose-revert-reflect-retry for
infrastructure compliance.** The closest work is a January 2026 paper
called "Sentinel" that uses local LLMs for Linux self-healing, but it
has no revert mechanism, no multi-agent architecture, and no
deterministic evaluation.

### Were the right choices made?

**Deterministic evaluator: yes.** Anthropic's own "Demystifying Evals
for AI Agents" (Jan 2026) explicitly recommends: "Choose deterministic
graders where possible, LLM graders where necessary." For compliance,
correctness is binary. OpenSCAP is exactly the right tool.

**Snapshot-based revert: yes, and more novel than I thought.** A
March 2026 paper (ACRFence) identifies security risks in agent
checkpoint-restore systems — semantic rollback attacks where agents
re-synthesize different requests after restore. The VM-level snapshot
here avoids this entire class because it's a full-state revert, not
an agent-level checkpoint.

**Fresh sessions per turn: yes.** The MAR paper confirms that
Reflexion performance degrades when context gets polluted with stale
history. The fresh-session approach with episodic memory distillation
is the validated pattern.

**Single open-weights model at the edge: a deployment story, not a
limitation.** Nobody else has published this specific scenario —
running a 31B open-weights model on commodity edge hardware for
sovereign compliance automation.

### What's genuinely novel

The individual components (Ralph loops, Reflexion, multi-agent,
deterministic eval) are all established. The *combination* is new:
a multi-agent reflexion loop with deterministic evaluation, two-tier
revert safety (script + VM snapshot), episodic memory distillation,
and conversation-history management, running on open-weights models
at the edge. The application to infrastructure compliance is
unexplored territory.

The right framing: this isn't inventing new AI techniques. It's
*combining established patterns* and applying them to a domain nobody
else has tried, on hardware that makes it sovereign and air-gappable.
That's the story for a technical audience.

---

## The v4 architecture decision

The research validated the patterns, but the second overnight run
analysis revealed something more important: **the harness is too
tightly coupled to the STIG skill.**

The evaluator calls OpenSCAP directly. The revert calls `virsh
snapshot-revert` directly. If you wanted to write a configuration
drift skill or a data pipeline validation skill, you'd be fighting
the harness, not using it.

### The interface insight

Every skill has the same five concerns, with different mechanisms:

| Concern | STIG | Whitepaper | Code Refactor |
|---------|------|------------|---------------|
| Checkpoint | virsh snapshot | git commit | git commit |
| Evaluator | OpenSCAP + health | spell/format + LLM | pytest + mypy |
| Executor | SSH to VM | file writes | file edits |
| Revert | snapshot restore | git checkout | git revert |
| WorkQueue | scan results | section outline | module list |

The harness should operate on *interfaces* for these five concerns.
Skills implement them. The task graph, parallelism, evaluation
triage, and conversation management all live in the harness and work
for every skill.

### What this enables

1. **Task graph and DAG visualization** — skill-agnostic. The harness
   manages nodes (work items), edges (dependencies), and state
   transitions. The dashboard renders the graph without knowing what
   the items represent.

2. **Optimistic parallelism** — the harness runs independent items
   concurrently and serializes conflicting items. Conflict detection
   comes from the Executor interface, which reports what resources
   each item touches.

3. **Evaluation triage** — the harness classifies failure modes
   (health failure, evaluator gap, false negative) using signals from
   the Evaluator interface. Different failure modes drive different
   responses.

4. **New skills as a folder-copy exercise** — implement five small
   classes, write prompts, drop into `skills/`. No harness changes.

### What the audience sees

A live DAG in the dashboard with state-colored nodes — green for done,
blue for in-progress, yellow for blocked on a dependency, red for
escalated, gray for queued. Edges show dependencies. Animated
transitions as the run progresses. The visualization works for STIG
rules, whitepaper sections, code modules, certificate chains —
anything the harness processes.

That's a more compelling demo moment than a scrolling log. The
audience sees the architecture *working* at a glance.

---

## Sources consulted

- Shinn et al., "Reflexion: Language Agents with Verbal
  Reinforcement Learning," NeurIPS 2023
- MAR: Multi-Agent Reflexion (arxiv, Dec 2025)
- VIGIL: A Reflective Runtime for Self-Healing Agents (arxiv, Dec 2025)
- ACRFence: Preventing Semantic Rollback Attacks (arxiv, March 2026)
- Anthropic, "Demystifying Evals for AI Agents" (Jan 2026)
- DynTaskMAS: Dynamic task graph generation (ICAPS 2025)
- AWS Strands Agents SDK conversation management
- Microsoft Semantic Kernel compaction
- Geoffrey Huntley, "The Ralph Wiggum Technique" (May 2025)
- Thomas Chong, "Ralph Loop with Google ADK" (Medium)
- Self-Healing Infrastructure for IaC (Zenodo, March 2026)
- Sentinel: Host-Centric Self-Healing Framework (Jan 2026)

## Related

- [`journey/18`](18-second-overnight-run.md) — the run analysis that
  prompted this research.
- [`architecture/01`](../architecture/01-reflexive-agent-harness-failure-modes.md) —
  the failure-mode taxonomy, now validated against the literature.
- [`journey/06.5`](06.5-stateful-loop-refactor.md) — the original
  refactor from ADK LoopAgent to Python-driven loop, now vindicated by
  the MAR paper's findings on context pollution.
