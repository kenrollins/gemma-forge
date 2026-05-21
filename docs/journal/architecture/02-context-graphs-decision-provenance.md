---
id: architecture-02-context-graphs-decision-provenance
type: architecture
title: "Context Graphs: Per-Retrieval Causal Attribution as a Memory Pattern"
date: 2026-05-21
tags: [L4-orchestration, memory, context-graphs, decision-provenance, architecture]
related:
  - architecture/00-system-architecture
  - architecture/01-reflexive-agent-harness-failure-modes
  - journey/22-context-graphs-and-the-memory-question
  - journey/38.5-the-cryptography-regression-postmortem
  - journey/38.6-context-graphs-and-the-tip-causal-signal
  - adr/0016-graphiti-neo4j-postgres-memory-stack
one_line: "An architectural pattern for retrieval-augmented agentic systems: outcome-only signals at the retrieval level accumulate plausible-but-wrong context that survives by being correlated with success without being responsible for it. The fix is to record per-retrieval causal attribution as a first-class column, separately from outcome. This document is the project-agnostic statement of the pattern."
---

# Context Graphs: Per-Retrieval Causal Attribution as a Memory Pattern

**Status:** One empirical witness so far (Run 7 cryptography regression). Pattern is hypothesized to generalize. This document predates the empirical proof in other domains; it will get updated as other instances surface.
**Audience:** People building retrieval-augmented agentic systems where past actions inform future ones.
**Note:** Project-agnostic. The empirical witness uses STIG remediation, but the abstract pattern applies to any system that retrieves past examples into a prompt and then attributes credit based on outcomes.

## Premise

A *retrieval-augmented agentic system* is one where:

- An agent takes actions in some environment.
- Past actions, their context, and their outcomes are stored as memory.
- For each new action, the system retrieves relevant past memory and includes it in the agent's prompt.
- The retrieved memory's helpfulness is scored based on the outcome that follows.
- Memory items with higher historical helpfulness are preferred in future retrievals.

This is the shape of [Mem0](https://mem0.ai), [Graphiti](https://github.com/getzep/graphiti), the [A-MEM](https://arxiv.org/abs/2502.12110) paper, and the [Reflexion](https://arxiv.org/abs/2303.11366) reference architecture. It's also the shape of most production agentic systems shipping in 2026 — code-review assistants, customer-support agents, autonomous remediation loops, research-paper summarizers — wherever the memory pool grows with use and learns from outcomes.

The pattern works for a while. Then it stops working in a specific way.

## The failure mode

After enough runs, retrieval starts surfacing memory items that have *high recorded helpfulness* but whose advice the agent didn't actually follow on the runs that produced that helpfulness rating. These items survive in the corpus because they were *present* during successful outcomes, not because they were *responsible* for those outcomes. Future runs retrieve them, the agent (especially a model conditioned more strongly on its context, e.g., post-MTP or post-fine-tuning) is more likely to follow them, and the agent's success rate degrades on exactly the cases where these items dominate the retrieval.

The cryptography regression in journey/38.5 is the empirical witness:

- Five tips retrieved for an SSH crypto rule, all advising approach X.
- Run 6's agent ignored the tips, used approach Y, succeeded.
- All five tips marked `outcome_value = 1` because the rule passed.
- Run 7's agent (more in-context-obedient after an inference-stack upgrade) followed the tips, used approach X, failed.

The same memory items that were retrieved successfully in Run 6 caused failures in Run 7. The corpus had no signal to distinguish.

## Why this happens — the structural diagnosis

The standard tip-retrieval schema records *two* things at the retrieval site:

```
(tip_id, attempt_id, rank, similarity_score, outcome_value)
```

`outcome_value` is whether the attempt succeeded. It is an outcome-level signal projected back onto the retrieval row. It contains no information about whether the agent's action in that attempt was *caused* by the tip's content.

The credit-assignment rule "tip retrieved + attempt succeeded → tip helpful" assumes:

> Every retrieved item that survives to the prompt either contributed to the action or was ignored irrelevantly.

This assumption holds when the agent operates against weak retrieval signal (the prompt has many tips, the agent picks the one that matches its plan) but breaks when the corpus contains *plausible-but-wrong* tips that the agent might follow on a future run even though it ignored them this run.

The deeper structural issue: **the retrieval pipeline is a knowledge graph (facts and relationships) being asked to do context-graph work (causation and provenance)**. The schema captures *what was retrieved* and *what outcome followed*. It doesn't capture *what caused what*.

## The pattern: separate result quality from causal attribution

A retrieval row in a context-graph-shaped memory captures three things, not two:

```
result quality       — did the attempt succeed, and how well (graded, not binary)
causal attribution   — did the agent's action reflect this specific retrieval's content
outcome confidence   — how trustworthy is the result-quality reading itself
```

The three are independent. A retrieval can have:

- High result quality + high attribution: tip was followed and helped (strong positive signal)
- High result quality + low attribution: tip was present but ignored, success came from elsewhere (no positive signal — neutral)
- Low result quality + high attribution: tip was followed and the attempt failed (strong negative signal — misleading tip)
- Low result quality + low attribution: tip was present and ignored, attempt failed (neutral)

The four quadrants are the entire credit-assignment surface. Without the attribution column, quadrants 1 and 2 collapse to "helpful" and quadrants 3 and 4 collapse to "unhelpful" — and the corpus accumulates type-2 misleading items as if they were type-1.

## What "causal attribution" means in practice

The attribution signal can come from any of several places, depending on the domain:

1. **Semantic similarity between retrieval content and agent output**. Embedding cosine, MinHash, BLEU-style overlap, etc. Cheap, deterministic, language-agnostic. Catches "the tip advised X and the agent did something X-shaped."
2. **LLM-as-judge ruling**. A separate LLM call (low-temperature, fixed prompt) reads the retrieval and the agent's output and answers "did the agent follow this retrieval's advice?" Slower and non-deterministic, but catches semantic matches the embedding misses.
3. **Structured action-trace overlap**. If the agent emits structured actions (tool calls with named arguments), compare the action set in the retrieval's source attempt to the action set in this attempt. Most rigorous when the domain supports it.
4. **Self-attestation from the agent**. The agent emits "I'm following tip Y" as part of its reasoning. Cheap to record, expensive to validate (agents lie or get confused).

In practice systems use #1 + #2 together: embedding for the deterministic-where-correctness-matters leg, LLM judge for the semantic-coverage leg. They serve as cross-validation; when they disagree, the row is flagged for review.

## Where the signal lives in the schema

The attribution column lives on the **retrieval row**, not on the tip and not on the attempt:

```sql
ALTER TABLE retrievals
  ADD COLUMN followed_llm BOOLEAN,           -- LLM judge ruling
  ADD COLUMN followed_emb DOUBLE PRECISION,  -- embedding cosine score
  ADD COLUMN followed_computed_at TIMESTAMPTZ;
```

The retrieval is the right granularity because a single memory item can be followed in one retrieval and ignored in another. The tip itself isn't followed-or-not; the *instance of its retrieval into a specific prompt* is.

This is what makes the schema a context graph instead of a knowledge graph:

- Knowledge graph: nodes (tips, attempts, rules), edges (retrieval-of, outcome-of).
- Context graph: knowledge-graph + per-edge attributes capturing causation. The edge "retrieval R linked tip T to attempt A" carries the *contribution* of T to A's action.

## Where the signal is consumed

Three places, all post-retrieval:

1. **The dream pass / credit-assignment step at end-of-run**. Computes per-tip aggregated "helpful when followed" rates, separate from "helpful when present." Tips with low follow rates accumulating high outcome scores get flagged as plausible-but-irrelevant; tips with high follow rates AND low outcomes get demoted as misleading.
2. **The retrieval ranker on subsequent runs**. The composite score includes `followed_*` as a multiplier on outcome history. A tip's "helpfulness" is now `helpful_when_followed` not raw `passed_at_retrieval`.
3. **The eviction policy**. Items that are retrieved often, present during successes often, but rarely actually followed are candidates for retirement — they're cluttering the prompt without contributing.

In all three places, the signal is consumed *historically*, not at retrieval time. Computing attribution at retrieval time is wasteful (it requires running the LLM judge and embedding model on every retrieval, in the hot path). Computing it at end-of-run in the dream pass is cheap (batched, parallelizable, off the critical path).

## When to add this pattern

A retrieval-augmented agentic system needs per-retrieval causal attribution when:

- The memory corpus has grown past a few hundred items and is still growing.
- The agent has accumulated enough successful outcomes that "every retrieval present during success" no longer differentiates good retrievals from neutral ones.
- The success rate on long-history workloads (categories of work the system has done many times) is meaningfully *worse* than on fresh-history workloads, with no upstream explanation.

The third bullet is the most diagnostic. In the cryptography case, categories the agent had worked through many times (cryptography, audit, logging, filesystem) regressed disproportionately compared to fresh categories (kernel, authentication, package-management) where the memory corpus is small. That's the smoking gun for type-2 misleading items dominating.

Systems that *don't* need this pattern yet:

- New skills/domains with a small (<200) memory pool.
- Domains where retrieved memory is read-only reference (documentation, not advice).
- Systems where the agent's action format is so structured that retrieval contribution is trivially observable.

## The relationship to broader literature

[Mem0](https://mem0.ai) emphasizes *what* to remember and how to evict. The retrieval row's schema there is shallower than what this pattern proposes. Mem0 + per-retrieval attribution would be strictly richer.

[Graphiti](https://github.com/getzep/graphiti) is bi-temporal — it tracks when facts became valid and when they became invalid. It's closer to the context-graph shape, but the validity is at the node level, not the per-retrieval-edge level. Adding attribution to the retrieval edges is the natural extension.

[A-MEM](https://arxiv.org/abs/2502.12110) cross-links semantically similar memory items so retrieval can expand outward. The semantic-link graph is orthogonal to attribution — A-MEM widens retrieval, attribution narrows the credit assignment.

[NIST AI agent standards](https://www.nist.gov/itl/ai-risk-management-framework) require audit logs of decision provenance. Per-retrieval attribution is the technical mechanism that makes provenance audit-able at the right grain. The audit log shape is essentially this pattern's retrieval table with `followed_*` columns.

The Foundation Capital "Context Graphs: AI's Trillion-Dollar Opportunity" framing — *the knowledge about how a decision was made is becoming as or more important than the decision itself* — is what this pattern is, in technical form. The cryptography case made the abstract thesis concrete: the missing column was, literally, the entire thesis.

## What's still open

This pattern raises questions the project hasn't answered:

- **Does it generalize to episodic memory** (the chain of reflections), or only to tips (declarative advice)? Reflections are stored with similar outcome scoring; the same lucky-neighbor problem probably applies. The fix would look the same — attribution at the per-retrieval row.
- **What's the right way to combine LLM-judge attribution and embedding attribution into a single score**? Average them? Use one as a gate for the other? Weight by their historical agreement rate? Empirically TBD.
- **How does attribution interact with cross-skill memory transfer?** If a STIG tip is retrieved for a CVE rule (cross-skill semantic match), the attribution model trained on STIG examples may not apply to CVE. Per-skill attribution prompts are the obvious first move; whether attribution generalizes across skills is open.

These are placed in `deferred.md` as the work surfaces them.

## Summary in one paragraph

A retrieval-augmented agentic system that scores retrievals only on the outcome they were present for will, over time, accumulate retrievals whose recorded helpfulness reflects lucky correlation rather than actual contribution. The fix is to add per-retrieval causal attribution as a first-class column — separately from outcome — and consume it in credit assignment, retrieval ranking, and eviction. This converts the memory schema from a knowledge graph (facts) into a context graph (causation), which is what the broader literature has been describing as the next-generation memory architecture for agentic AI. The cryptography case in journey/38.5 is the first empirical witness in this project. The pattern is hypothesized to generalize wherever the memory corpus is large enough to contain plausible-but-wrong items that have survived by correlation.
