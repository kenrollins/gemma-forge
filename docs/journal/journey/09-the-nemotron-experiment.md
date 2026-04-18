---
id: journey-09-nemotron-experiment
type: journey
title: "The Nemotron Experiment: Cross-Model Architecture, and Why I Walked It Back"
date: 2026-04-10
tags: [L3-model, L4-orchestration, parallelism, refactor]
related:
  - journey/08-model-architecture-revision
  - journey/10-the-parallelism-maze
  - gotchas/nemotron-tool-parser
  - gotchas/nemotron-tp-tiling-error
one_line: "I spent most of a day wiring up a cross-model architecture where Nemotron 30B MoE served as the Auditor role alongside Gemma 4, discovered it worked technically but fought the design, and walked it back to all-Gemma."
---

# The Nemotron Experiment: Cross-Model Architecture, and Why I Walked It Back

I spent most of a day building a cross-model architecture where
Nemotron 30B MoE ran as the Auditor role — a second foundation model
providing independent judgment — and discovered that while the
technical integration worked, the architectural complexity wasn't
earning its keep, so I walked it back to all-Gemma.

## What I was trying to prove

The agentic architecture has multiple roles (Architect, Worker,
Reflector, Auditor) and there's an open question about whether those
roles should all run on the same model or on different models. The
intuition for different-model is compelling:

- An **independent Auditor** that uses a different model family
  brings a different prior. If Gemma 4 31B has a blind spot, a
  second model might catch it.
- **Cross-model "peer review"** is a pattern some agentic systems
  use for safety — one model proposes, another reviews, consensus
  matters.
- For a Federal demo, showing **multi-model orchestration** on a
  single piece of edge hardware is a visually compelling story.
  Two different logos on the same dashboard says something the
  single-model story doesn't.

Nemotron 30B (NVIDIA's MoE model) was the natural pick for the
second model. It's open-weights, served by vLLM, fits on 2× L4 via
pipeline parallelism, and has a different architectural lineage
from Gemma. If cross-model architecture was going to work, this
was the test case.

## What worked

The integration itself was fine. Specifically:

- **Nemotron 30B MoE at PP=2 on 2× L4.** Pipeline parallelism
  stacks layers across GPUs instead of sharding weights within
  each layer. Because Nemotron is MoE (mixture-of-experts), PP is
  the right parallelism choice — each GPU only holds half the
  experts, which freed up ~120× more KV cache headroom than TP=2
  would have. See [`journey/10-the-parallelism-maze`](10-the-parallelism-maze.md) for the
  parallelism reasoning.
- **Tool calling through vLLM.** Nemotron's native tool-call
  format is different from Gemma's. vLLM requires the right
  parser flags:
  `--tool-call-parser qwen3_coder --reasoning-parser nano_v3 --reasoning-parser-plugin nano_v3_reasoning_parser.py`.
  Hermes and llama3_json do not work. Documented in
  [`gotchas/nemotron-tool-parser`](../gotchas/nemotron-tool-parser.md).
- **ADK integration.** Once the vLLM endpoint was returning
  structured tool calls, the ADK FunctionTool framework consumed
  them just like Gemma's output.
- **Dashboard visualization.** The dashboard happily showed two
  different models running on two different GPU groups with
  different colored borders and metrics.

Technically, the cross-model architecture worked.

## What didn't work

The complexity wasn't earning its keep. Several specific problems:

### The Auditor wasn't actually doing audit work

The original intent was for the Auditor to independently judge the
Worker's output — a second opinion on whether a fix was good. But
once I actually looked at what the Auditor had to judge, the
judgment reduced to deterministic checks:

- Did the STIG rule pass or fail an OpenSCAP rescan? (Deterministic.)
- Is the mission app still healthy? (Deterministic, via a
  shell-script healthcheck.)
- Are there new errors in the journal since the fix ran?
  (Deterministic, via `journalctl -p err`.)

None of these required an LLM. They required *Python*. An LLM
evaluator was adding latency, token spend, and a model of the
world that could be wrong, when a deterministic check would give
the same answer in a fraction of the time with zero ambiguity.

### Two models on one box is a distraction, not an advantage

The Auditor role on a second model was visually compelling for a
demo but didn't actually help solve the problem the loop was trying
to solve. The architecture was doing work to *show something
interesting* rather than work to *make the loop better*. That's a
bad trade, and it was clearly "hardware-first thinking" — assigning
roles to models to make the GPU pie chart look balanced, not because
the roles needed different models.

### The Nemotron TP=2 tiling bug

A separate problem: I briefly tried Nemotron at TP=2 instead of
PP=2 to see if it was faster. It crashed with a Marlin kernel
tiling error — 5152 not divisible by 64 — because one of the
intermediate dimensions didn't align for the TP=2 kernel. Switching
to PP=2 worked around it. Documented in
[`gotchas/nemotron-tp-tiling-error`](../gotchas/nemotron-tp-tiling-error.md).
Not a dealbreaker, but another small piece of fragility that
wouldn't exist with a single-model architecture.

## The reframe: roles are about judgment, not models

The moment the Auditor role clarified as really a deterministic-
evaluation role, the whole architecture clarified.
Roles in this project are about *kinds of judgment*, not about
*which model runs where*:

| Role | What it does | LLM or not? |
|---|---|---|
| **Architect** | Strategic planning: pick a rule, plan an approach | LLM (needs reasoning over state) |
| **Worker** | Concrete action: generate a fix, call a tool | LLM (needs code/script generation) |
| **Reflector** | Failure analysis: diagnose what went wrong | LLM (needs pattern recognition over history) |
| **Eval** | Verdict: did this attempt succeed or fail? | **Deterministic Python, no LLM** |

The Eval role replaced what the Auditor was supposed to do. And
because Eval is deterministic, it's the one piece of the loop that
can never be wrong about its own output. The Reflector now gets
*certain* facts as input ("the STIG rule check returned false, the
journal shows three permission errors"), not *probabilistic*
facts ("an LLM thinks the fix was bad"). That's a much stronger
foundation for reflexion.

## Why the Nemotron code stayed around (but didn't get used)

I didn't delete the Nemotron integration code after walking back
the cross-model architecture. It still works. The vLLM config for
Nemotron PP=2 is still in the repo. The parser flags are
documented as gotchas. If a future skill or a future iteration
wants to bring Nemotron back — for example, as a second Reflector
that brings a different prior to failure analysis — the path is
still open.

What I *did* remove:

- The four-GPU role assignment that put Gemma on two GPUs and
  Nemotron on two GPUs (that assignment was driven by hardware
  rather than by role needs)
- The Auditor role as an LLM-driven role (now Eval, deterministic)
- The cross-model orchestration in the default skill
  configuration (the stig-rhel9 skill now uses all Gemma)

## What I took away from this experiment

1. **Multi-model is not automatically better.** Adding a second
   model to a system should earn its keep. If the second model
   isn't doing work that the first model can't, it's complexity
   without benefit. The test is: what specifically is this role's
   output that the primary model couldn't produce? If you can't
   answer that cleanly, collapse the roles.

2. **"Independent judgment" in an agentic system is often
   deterministic, not LLM-driven.** The kinds of checks you want
   to be most-trustworthy — "did it pass the test," "is the
   service responding," "is the file syntactically valid" — are
   almost always doable in Python with high confidence and zero
   ambiguity. An LLM doing the same check is strictly worse:
   slower, more expensive, less deterministic, and can be wrong.
   Let the LLM handle the reasoning work; let Python handle the
   verdicts.

3. **Hardware-first architecture is a trap.** Assigning roles to
   models to balance GPU utilization is tempting (especially on a
   box with 4 GPUs where it feels wasteful to leave 2 idle), but
   it produces architectures that are more complex than the
   problem demands. The fix is to ask "what does this role
   actually need" first, and let the hardware assignment fall out
   of that. On the XR7620 specifically, this led me to bf16 TP=4
   across all 4 GPUs for the single Gemma model, which turned out
   to match NVFP4 TP=2 throughput and simplify the whole inference
   plane. See
   [`journey/12-bf16-tp4-full-precision`](12-bf16-tp4-full-precision.md).

4. **It's okay to walk back complex ideas.** I spent ~8 hours on
   cross-model integration. That's not wasted time — the walk-back
   was as valuable as any feature work because it clarified what
   roles actually are in this architecture. The clearest
   architectural insights come from the things you built and then
   decided not to keep.

## Related entries

- [`journey/08-model-architecture-revision`](08-model-architecture-revision.md)
  — the broader revision that included this walk-back
- [`journey/10-the-parallelism-maze`](10-the-parallelism-maze.md) — the parallelism
  reasoning that was relevant for Nemotron's PP choice
- [`gotchas/nemotron-tool-parser`](../gotchas/nemotron-tool-parser.md)
  — the atomic lesson about the required vLLM flags
- [`gotchas/nemotron-tp-tiling-error`](../gotchas/nemotron-tp-tiling-error.md)
  — the Marlin kernel bug
- [`journey/12-bf16-tp4-full-precision`](12-bf16-tp4-full-precision.md)
  — the ultimate single-model, all-4-GPU configuration that came
  after this experiment was walked back
