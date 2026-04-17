---
id: futures-harness-as-training-data-factory
type: futures
title: "The Harness Is a Training Data Factory"
date_first_surfaced: 2026-04-16
related:
  - journey/27-building-the-dream-pass
  - journey/28-run-4-and-the-coarseness-problem
  - drafts/v2-architecture-plan
status: SPECULATIVE — not built, not scheduled, captured for posterity
one_line: "Every run produces structured (context, action, outcome) data with deterministic outcome labels. That is, by construction, a fine-tuning corpus. Open weights make the full loop — train locally, deploy locally, improve the next run — possible in a way that closed weights structurally cannot."
---

# The Harness Is a Training Data Factory

## What this entry is

A speculative engineering note. The idea surfaced during the V2
architecture discussion. It is interesting enough to capture, large
enough to deserve its own write-up, and uncertain enough that nobody
should commit to it on the basis of this document alone.

If someone — including future-Ken — ever builds this, the journey
entry that results should link back here as the record of when the
idea first showed up.

## The observation

Every Ralph-loop run on the gemma-forge harness produces a specific
shape of data, by construction:

- **Per agent turn** — Architect, Worker, Auditor, Reflector. Each
  turn has a structured prompt (rule context, prior attempts, lessons
  in scope), an LLM response (free text + tool calls), and a record
  of what happened immediately after.
- **Per attempt** — a (rule, approach, evaluator outcome) triple.
  After Phase D, the outcome is an explicit `OutcomeSignal` with a
  `value ∈ [0, 1]` and a `confidence ∈ [0, 1]`. For STIG, the
  confidence is always 1.0 because the OScap scanner is deterministic.
- **Per (tip, rule) retrieval** — V2's `tip_retrievals` table records
  every tip that landed in a Worker prompt and the outcome of the
  attempt that consumed it. That's a preference-pair dataset for
  RLHF-style training, by accident.

After Run 4, on STIG alone, we have:
- ~1,800 attempts × 4 agent roles ≈ 7,000+ structured (prompt, response, outcome) triples
- 1,738 lessons (soon to be tips) with provenance edges back to the
  attempts that produced them
- 51,906 events in the run logs, every one of which has a timestamp,
  agent, and structured payload

The data was generated as a side effect of running the harness. We
did not set out to collect it. The harness's enforced structure made
it inevitable.

## Why this matters

Most fine-tuning datasets in the wild are one of:

1. **Human-labeled.** Expensive, slow, biased toward what's easy to
   label. SuperGLUE, MMLU, HumanEval — all of these took significant
   human effort to build.
2. **LLM-judged.** Cheap, fast, noisy. Constitutional AI datasets,
   self-instruct datasets, most "synthetic data" pipelines. The
   noise compounds when you train on judgments produced by the same
   class of model you're improving.
3. **Behavioral logs without ground truth.** Customer support
   transcripts, code review history, search-and-click logs. Lots of
   data, no clean signal.

gemma-forge produces something different: **agent traces with
deterministic outcome labels.** Every Worker action is paired with
a scanner result that says, in binary, whether the action achieved
the intended state. Every Architect verdict is paired with what
actually happened next. Every Reflector lesson is paired with whether
the lesson, when retrieved on a subsequent attempt, helped or hurt.

That is not a common shape. The cited literature on agent memory
(Xu et al. 2505.16067, Trajectory-Informed 2603.10600, AgeMem) all
explicitly note that they lack ground truth and have to use proxy
signals. We have ground truth as a primitive. The architecture
generates the dataset.

## Two distinct fine-tuning targets

### Target A: Skill-specific fine-tune

Take Gemma 4 31B, fine-tune on STIG attempt traces, deploy. Output:
a model that is measurably better at STIG remediation than the base
model.

- **Data shape:** SFT pairs of (Worker prompt with rule context, Worker response with fix script). Reward filter: only train on attempts where the OScap evaluator returned pass.
- **Data volume:** ~150 successful attempts per run × 4 runs = ~600 high-quality SFT examples for STIG. LoRA (low-rank adaptation) works comfortably on a few hundred examples.
- **Training time:** ~2-4 hours on the XR7620's 4× L4 (96GB total VRAM) with QLoRA + offloading.
- **Expected effect:** marginal first-try success improvement (~2-5 percentage points). Probably worth the effort once. Probably not worth maintaining as the corpus changes.

This is the obvious use case. It would work today. It is not the
interesting one.

### Target B: Architecture-pattern fine-tune

Take Gemma 4 31B, fine-tune on cross-skill traces from the harness.
Output: a model that is better at *being a Ralph-loop agent* in
general — better at being an Architect, better at being a Worker,
better at being a Reflector — regardless of which skill it's working on.

- **Data shape:** SFT pairs grouped by agent role. The Architect SFT dataset is every Architect prompt + verdict pair across every skill, every run. The Worker SFT dataset is every Worker prompt + tool call pair. The Reflector SFT dataset is every Reflector prompt + structured output pair.
- **Data volume:** harness produces ~7,000 turn-level examples per run. With 5 skills × 5 runs each, that's ~175,000 examples. By that scale, full fine-tune (not just LoRA) becomes practical.
- **Training time:** depends on full-vs-LoRA. LoRA is hours; full fine-tune is days but the resulting model is strictly more capable for the harness pattern.
- **Expected effect:** the model develops a stronger prior for the agent-role decomposition. Worker becomes faster at producing valid tool calls. Architect becomes better at category-difficulty intuition. Reflector becomes better at extracting actionable structured tips. Cross-skill, not just on the skills it was trained on.

This is the structurally interesting case. It rewards every additional
skill added to the harness — more skills means more data means a
better base model means better runs in the next iteration.

## The loop that closed-weights cannot offer

```
runs → structured trace data → fine-tuned model → better runs → more data → ...
```

This loop is the actual contribution of the open-weights story for
agentic systems. Closed-weights vendors offer none of these:

- **Anthropic / OpenAI / Google hosted Gemini:** you cannot fine-tune
  on your own data and then deploy locally. Best case, you can
  fine-tune via the vendor's API and get a vendor-hosted private
  model. Your data leaves the boundary.
- **NVIDIA NIM (closed paths):** same constraint. NIM provides the
  inference container; the underlying model and its training pipeline
  remain vendor-controlled.
- **AWS Bedrock / Azure OpenAI:** you can fine-tune, but the data
  used for fine-tuning leaves your environment to the cloud provider.
  Federal sovereignty story collapses.

gemma-forge with open weights:
- Data stays on the host (Postgres + Neo4j on the XR7620).
- Fine-tuning runs on the host (PyTorch + PEFT or Unsloth, using the
  same 4× L4 GPUs that run inference).
- The new model adapter (LoRA) is a few hundred MB on disk.
- vLLM loads the adapter; the next run uses the fine-tuned model.
- No data, no model weights, no telemetry leaves the host at any
  point in the loop.

This is the loop. Open weights are not just "cheaper" or "less
locked in." They are *structurally enabling* of a capability that
closed weights cannot match. For federal customers operating on
classified or air-gapped data, this is the actual differentiator.

## Why this is in `futures/` and not `journey/`

Because nobody has done it yet. Specifically:

1. **The data isn't quite ready yet.** V2's `tip_retrievals` table
   and structured Reflector output are required to make the dataset
   export trivial. Pre-V2 export requires more cleanup work because
   the data is partially in JSONL events and partially in SQLite
   (now Postgres) tables with inconsistent schemas.
2. **The corpus is too small for Target B today.** With one skill
   (STIG) at four runs, we have enough for Target A but not enough
   for Target B to show meaningful cross-skill generalization. Two
   or three more skills, ~5 runs each, gets us into the 50,000-example
   range where SFT becomes effective.
3. **It is not on the critical path** for the project's stated thesis
   (harness architecture for agentic infrastructure operations).
   It is a downstream affordance, not a core capability.

If any of those three conditions changes — V2 ships, more skills get
added, or the project's thesis evolves to include "the harness as a
data-generation system" — then this becomes implementable in earnest
and gets promoted to a journey entry.

## What a real implementation would look like

For someone picking this up cold, here is the rough shape:

### Phase F1 — Dataset export tooling (~1 day)

Add `tools/export_training_data.py`:

```python
# Usage examples
./tools/export_training_data.py --role worker --skill stig --filter eval_passed=true --format jsonl --out worker_sft.jsonl
./tools/export_training_data.py --role reflector --skills all --format dpo --out reflector_dpo.jsonl
./tools/export_training_data.py --tier preference-pairs --format dpo --out tip_preferences.jsonl
```

The exporter is a SQL query against the V2 schema:

```sql
-- Worker SFT example
SELECT
    a.approach_prompt AS prompt,    -- the Worker's input context
    a.tool_call AS response,         -- the Worker's tool invocation
    a.eval_passed AS reward          -- ground-truth outcome
FROM stig.attempts a
WHERE a.eval_passed = true
  AND a.approach_prompt IS NOT NULL
  AND a.tool_call IS NOT NULL;
```

Output is HuggingFace-Datasets-compatible JSONL. ~2 hours of work
once V2's schema is in place.

### Phase F2 — Fine-tuning pipeline (~1 day)

Add `tools/finetune.py` wrapping Unsloth or PEFT:

- Loads exported JSONL.
- Configures QLoRA fine-tune for Gemma 4 31B (4-bit base + LoRA
  adapters).
- Runs on the XR7620's 4× L4 with proper sharding.
- Outputs an adapter under `/data/triton/models/gemma-4-31b-it-stig-vN/`.

### Phase F3 — Deployment integration (~half day)

vLLM supports LoRA adapters natively (`--enable-lora --lora-modules`).
Add a config flag to the harness so `--model gemma-4-31b-it-stig-v1`
loads the adapter alongside the base.

### Phase F4 — Validation (one overnight run)

Run the next overnight (call it Run N+1) with the fine-tuned adapter.
Compare against Run N (base model, same architecture). Measure:

- First-try success rate (expected delta: +2-5 pp on skills the model
  was trained on; ideally no regression on others).
- Token efficiency (expected: fewer tokens per attempt as the model
  internalizes the tool-calling pattern).
- Cross-skill behavior (only meaningful if multiple skills are
  trained on; with one skill, expect specialization toward that skill).

### Honest caveats

- **VRAM tight:** Gemma 4 31B QLoRA fine-tune needs careful memory
  management. 4-bit base + sharded across 4 L4s is doable but not
  trivial.
- **Catastrophic forgetting:** the fine-tuned model may regress on
  general capabilities. Worth running an MMLU subset or HumanEval
  before/after to measure.
- **Skill-specific overfit:** Target A produces a STIG specialist
  that may underperform on a future skill. Don't deploy a Target A
  fine-tune as the harness's default model; keep base Gemma 4
  alongside.
- **Data quality matters:** the V2 history-based deletion mechanism
  helps here — it culls the bad lessons before they enter the
  training set. But the underlying attempt traces still include
  failed approaches that weren't always *cleanly* failed. Worth
  filtering on more than just `eval_passed`.

## What this would mean for the whitepaper

If this ever ships, the whitepaper gets a new section in the
"Future Directions" part — but more importantly, the open-weights
argument shifts from "less vendor lock-in" to "the only architecture
that supports the runs-improve-the-model loop." That is a stronger
claim and one that closed-weights vendors structurally cannot
counter.

The narrative arc:

1. We built a sovereign-edge agentic harness with cross-run learning.
2. The harness produces structured (context, action, outcome) data
   with deterministic outcomes by construction.
3. That data is a fine-tuning corpus, by construction.
4. With open weights, fine-tuning happens locally; the new model
   deploys locally; the next run uses the improved model. Loop closed.
5. With closed weights, this loop cannot exist — fine-tuning requires
   sending data out, deployment requires accepting vendor-controlled
   inference. The federal sovereignty story breaks.
6. *The architecture composes with open weights in a way it does not
   compose with hosted models.*

That last line is the reframe. It moves "open weights" from a license
preference to a capability prerequisite for the harness's improvement
loop. For federal customers operating on classified or air-gapped
data, that is the actual differentiator.

## Why this entry exists at all

Capturing speculative ideas at the moment they surface is cheap.
Re-deriving them later is expensive. The specific framing in this
entry — "the harness is a training data factory," the distinction
between Target A and Target B, the open-weights-as-capability-not-
preference argument — came together in one conversation and would
not be easy to reconstruct from scratch.

If anyone, future-Ken or otherwise, picks this up, the conversation
that produced it is on the record. Start here. Don't reinvent the
framing.
