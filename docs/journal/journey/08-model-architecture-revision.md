---
id: journey-08-model-architecture-revision
type: journey
title: "Journey: Model Architecture Revision — From "One GPU Per Role" to "Right Model Per Role""
date: 2026-04-10
tags: [L3-model, L4-orchestration, refactor]
related:
  - journey/02-model-strategy
  - journey/11-the-missing-reflector
one_line: "We started with four models assigned to four GPUs because we had four GPUs, then recognized it as hardware-first thinking, and redesigned around what each agent role actually needs."
---

# Journey: Model Architecture Revision — From "One GPU Per Role" to "Right Model Per Role"

## The story in one sentence
We started with four models assigned to four GPUs because we had four
GPUs, then Ken called it out as hardware-first thinking, and we
redesigned around what each agent role actually NEEDS.

## What was wrong

The original lineup (ADR-0015) assigned models by size:
- GPU 0+1: 31B Dense (biggest) → Architect + Worker
- GPU 2: E4B (medium) → Auditor
- GPU 3: E2B (smallest) → Sentry (never wired in)

Three problems Ken identified:

1. **"We are utilizing each GPU just cause we can."** The assignment
   was hardware-first, not architecture-first. The Sentry GPU was
   loaded but idle — pure waste.

2. **"Wouldn't the auditor benefit from increased intelligence?"**
   The Auditor makes the HARDEST decision in the loop (keep or revert)
   but ran on the second-weakest model. That's like giving your code
   reviewer a junior developer's brain.

3. **"We were doing mostly pass/fail tests — are we really auditing?"**
   The Auditor's entire job was: call healthcheck, read "HEALTHY" or
   "UNHEALTHY", decide. A bash script could do that. The LLM was a
   wrapper around a three-word string.

## The architectural insight

The roles in the Ralph loop have fundamentally different cognitive needs:

| Role | Cognitive task | What it needs |
|---|---|---|
| Architect | Planning, strategy, selection | Strong reasoning, broad knowledge |
| Worker | Code generation, tool use | Strong code gen, structured output |
| Auditor | Evaluation, judgment, verification | **Different perspective** from the creator |

The Auditor doesn't need to be SMARTER than the Architect. It needs
to THINK DIFFERENTLY. Same model evaluating its own work has the same
blind spots. A different model family catches systematic biases.

This is the red team / blue team principle applied to agentic AI.

## The revised architecture

| GPU(s) | Role | Model | Why |
|---|---|---|---|
| 0+1 | Architect + Worker | Gemma 4 31B NVFP4 (Google) | Flagship for planning + code gen |
| 2 | Auditor | Nemotron-3-Nano-30B NVFP4 (NVIDIA) | Different model family for cross-evaluation |
| 3 | Available | — | Future skills / mission-flexible |

## The expanded Auditor

The Auditor was also redesigned from a liveness checker to a real auditor:

Old: check_health → HEALTHY/UNHEALTHY → pass/fail
New: check_health + stig_check_rule + read_recent_journal + revert

The expanded Auditor:
1. Checks mission app health (liveness)
2. Re-scans the specific STIG rule to verify the fix WORKED
3. Reads recent journal entries for side effects
4. Makes a judgment call with reasoning
5. Reverts if any of the above fail

This justifies the stronger model — a bash script can check liveness,
but reasoning about whether a journal warning is a real problem or
benign noise requires actual intelligence.

## Why Nemotron specifically

- **NVIDIA-official NVFP4** — not a community quant, defensible for Federal
- **Different model family** — Llama 3.3 derivative, different training data from Gemma
- **30B total / 3B active** — MoE architecture, fast per-token inference
- **Trained with 33% synthetic data for tool calling** — directly relevant
- **Fits on single L4** — ~16-17 GB NVFP4, leaves headroom for KV cache
- **~7-12% behind Gemma 4 on benchmarks** — acceptable for evaluation role

## Why GPU 3 stays free

Ken liked "available" better than forcing a role. The demo story:
"3 GPUs running 2 model families for 3 agent roles, with a 4th GPU
available for additional skills. This XR7620 isn't maxed out — it
has room for the next mission."

Having headroom to grow is more important than seeing a config 100% utilized.
