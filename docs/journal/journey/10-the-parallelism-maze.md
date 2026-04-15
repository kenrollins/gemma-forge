---
id: journey-10-the-parallelism-maze
type: journey
title: "The Parallelism Maze: When the Ecosystem Says No"
date: 2026-04-10
tags: [L3-model, parallelism, discovery, edge-deployment]
related:
  - journey/09-the-nemotron-experiment
  - journey/12-bf16-tp4-full-precision
  - gotchas/nemotron-tp-tiling-error
one_line: "I thought picking a parallelism strategy would be a configuration choice. Instead, every path I tried was blocked by a different constraint — model architecture, quantization format, kernel compatibility, or vLLM's implementation — until only one option remained."
---

# The Parallelism Maze: When the Ecosystem Says No

## The story in one sentence

I spent real time exploring multi-model parallelism strategies,
convinced I could get 4-5x throughput with the right combination
of tensor parallelism and pipeline parallelism — and the ecosystem
blocked me at every turn until only one working configuration
remained.

## Why this looked easy

Coming out of the model architecture revision
([entry 08](08-model-architecture-revision.md)) and the Nemotron
experiment ([entry 09](09-the-nemotron-experiment.md)), the plan
looked clean: Gemma 4 31B for the Architect and Worker (strong
reasoning), Nemotron 30B for the Auditor (fast evaluation). Two
models, two parallelism strategies, four GPUs. The textbook answer.

The theory was compelling. On L4s with PCIe Gen4 (~32 GB/s, no
NVLink), the parallelism strategy should matter enormously:

- **Tensor Parallelism (TP)** splits weight matrices across GPUs.
  Every layer requires an all-reduce synchronization. For a
  60-layer model: 60 all-reduces per forward pass, each going
  over PCIe. That's the dominant bottleneck — measured ~14
  tok/s for Gemma 4 with TP=2.

- **Pipeline Parallelism (PP)** assigns different layers to
  different GPUs. One activation transfer per forward pass instead
  of 60 all-reduces. On paper: 4-5x faster.

The math said PP was the answer. The ecosystem had other ideas.

## Path 1: Gemma 4 with PP — blocked

I tested PP=2 for Gemma 4. Crashed immediately with an
`IntermediateTensors` attribute error in vLLM 0.19.0. Gemma 4
is multimodal (vision + audio + text) — the intermediate tensors
between pipeline stages include cross-attention outputs from
the encoders. vLLM's Gemma 4 implementation doesn't handle the
handoff.

Not a configuration issue. Not a version issue. A fundamental
gap in the implementation — someone would need to add
`get_intermediate_tensors()` and `set_intermediate_tensors()` to
`Gemma4ForConditionalGeneration`. No open PRs as of April 2026.

**Gemma 4 is TP-only. No choice.**

## Path 2: Nemotron — a longer road than expected

Nemotron is an MoE (Mixture of Experts), and PP is the right
theoretical fit for MoE architectures. I started with PP=2 and it
worked — Nemotron loaded, served tool calls, the Auditor role
functioned. Good.

Then I got curious: would TP=2 be faster? MoE models should
support either strategy. So I tried it. Crashed immediately
with a Marlin kernel tiling error:
`size_n = 5152 is not divisible by tile_n_size = 64`. When the
MoE expert weights are split across GPUs via TP, the resulting
matrix dimensions don't align with the Marlin NVFP4 kernel's
tile requirements. This is a quantization + MoE + TP interaction
that nobody documents — three things that individually work but
fail in combination.

So PP=2 it was, which was working fine. By now one thing was
clear: PP wasn't a preference, it was the *only* option for
Nemotron NVFP4. And the numbers were impressive:

| Metric | Gemma 4 (TP=2) | Nemotron (PP=2) | Factor |
|---|---|---|---|
| Tokens/sec | ~14 | ~52-70 | **4-5x** |
| Time to first token | 10-20s | 2-8s | **3-5x** |
| KV cache capacity | 10,432 tokens | 1,257,472 tokens | **120x** |
| Max context | 8,192 tokens | 32,768 tokens | **4x** |
| Inter-GPU transfers/fwd | 60 all-reduces | 1 activation | **60x fewer** |

The 120x KV cache difference was the most surprising finding, and
it comes down to how the weight memory is distributed. Same total
model size, same total VRAM, same GPUs — but TP puts all layers on
all GPUs (split per matrix dimension), leaving ~600 MiB per GPU
for KV cache. PP puts half the layers on each GPU, leaving ~10 GB
per GPU for KV cache. The parallelism strategy doesn't just affect
throughput — it fundamentally changes how much context the model
can hold.

But as documented in [entry 09](09-the-nemotron-experiment.md),
the multi-model approach introduced complexity that didn't justify
the performance gain. More importantly, the Auditor wasn't really
an LLM role at all — it was a deterministic evaluation that should
be done by the harness, not a model ([entry 09](09-the-nemotron-experiment.md)
has the full story of that reframe). The Nemotron instance was
shut down.

## What I actually learned

The parallelism deep dive consumed real time — testing
configurations, debugging kernel errors, measuring throughput,
understanding why each failure happened. In the moment, it felt
like wasted effort once the stack converged on single-model
TP=4 ([entry 12](12-bf16-tp4-full-precision.md)).

Looking back, the learning was worth it:

**1. Today, the parallelism strategy is not a configuration knob.**
It's an architectural constraint determined by the model family,
the quantization format, and the inference engine's implementation.
On this stack (vLLM 0.19.0, NVFP4, L4s without NVLink), dense
models get TP and MoE models get PP. This will likely change —
TensorRT-LLM and future vLLM releases may open up PP for dense
models like Gemma 4, which could mean a 4-5x throughput jump on
the same hardware with no code changes. But as of April 2026,
the ecosystem constrains the choice.

**2. "Day-0 model support" has layers.** vLLM 0.19.0 has Day-0
Gemma 4 support for inference. It does NOT have PP support for
Gemma 4. The model loads and runs, but only in the parallelism
mode the implementation handles. This distinction matters for
anyone planning edge deployments.

**3. Edge hardware amplifies these constraints.** On a DGX with
NVLink, TP's 60 all-reduces per forward pass are negligible. On
L4s with PCIe, they're the dominant bottleneck. The same model,
the same code, dramatically different performance characteristics
based on the interconnect you don't have.

**4. The ecosystem will catch up — but not on your timeline.** The
specific paths visible as of April 2026:

- **Triton 26.04** (expected late April) — fixes the vLLM backend
  version gap so Gemma 4 loads in Triton natively, and restores
  EXPLICIT model control mode for dynamic load/unload. Does NOT
  change the TP-only constraint for Gemma 4 via vLLM.
- **vLLM PP for Gemma 4** — someone needs to implement
  `get_intermediate_tensors()` and `set_intermediate_tensors()` on
  `Gemma4ForConditionalGeneration`. Non-trivial because Gemma 4 is
  multimodal — the intermediate tensors between pipeline stages
  include cross-attention outputs from vision and audio encoders
  that don't exist in text-only models. No open PRs as of this
  writing. When it lands: same container, same weights, add
  `--pipeline-parallel-size 2`, potentially 4-5x throughput.
- **TensorRT-LLM** — NVIDIA's inference engine with native PP
  support and kernel optimizations. If Gemma 4 is added, the same
  XR7620 could jump from 14 tok/s to 50+ tok/s. Known caveat:
  TensorRT-LLM has had issues with PP + NVFP4 for some model
  families (Llama 3.x, Llama 4). Would need verification.

The "buy the hardware now, the software keeps getting faster"
story is real here. The same four L4s will get meaningfully faster
as the inference stack matures. But today, you ship with what
works.

The lesson for anyone deploying open models at the edge: budget
time for this maze. The model download is the easy part. Making it
actually run at acceptable throughput on your specific hardware
with your specific inference stack is where the real work lives.

---

## Related

- [`journey/09`](09-the-nemotron-experiment.md) — the Nemotron
  experiment that motivated the parallelism deep dive.
- [`journey/12`](12-bf16-tp4-full-precision.md) — where we landed:
  single model, bf16 full precision, TP=4, the configuration nobody
  predicted.
- [`gotchas/nemotron-tp-tiling-error`](../gotchas/nemotron-tp-tiling-error.md)
  — the Marlin kernel tiling failure.
