---
id: journey-20-the-interface-extraction
type: journey
title: "The Interface Extraction: Ripping the Engine Apart Mid-Flight"
date: 2026-04-12
tags: [L4-orchestration, reflexion-loop, refactor]
related:
  - journey/19-research-and-v4-architecture
  - journey/06.5-stateful-loop-refactor
  - journey/17-v3-fix-pass
one_line: "I realized the harness and the STIG skill were the same code — and that meant any new skill would require forking the loop. So I extracted five interfaces, moved all STIG-specific logic into a runtime module, and rewired 1,400 lines of harness code without breaking a single test."
---

# The Interface Extraction: Ripping the Engine Apart Mid-Flight

The harness had just remediated 93 STIG rules overnight. The v3 fixes held. The demo was ready. The sensible move was to stop and ship.

Instead I opened `ralph.py` and stared at one line:

```python
eval_result = await evaluate_fix(
    _ssh_config, selected["rule_id"], _stig_profile, _stig_datastream
)
```

That's the heart of the harness. It calls `mission_healthcheck` (SSH to the VM), then `stig_check_rule` (OpenSCAP on the VM), then `read_recent_journal` (journald on the VM). Every one of those is STIG-on-a-VM-specific. A second skill — code repair, configuration drift, anything else — would require forking the loop, gutting the middle, copy-pasting the outer structure. The harness was not a harness. It was a STIG remediation script that happened to have good architecture around it.

So I ripped the engine apart mid-flight, with working code and a demo deadline, because the thing that made it work for STIG was the same thing that would prevent it from working for anything else.

## The five interfaces

What does the harness *actually* need from a skill? Not "what does STIG provide" — what does the *loop* need?

Five things:

1. **WorkQueue** — "give me work items to process." For STIG, that's
   an OpenSCAP scan. For a whitepaper, it's a section outline. For
   code refactoring, it's a module list.

2. **Executor** — "apply a change to the target." SSH for STIG. File
   writes for a whitepaper. `git apply` for code.

3. **Evaluator** — "did the change work?" OpenSCAP for STIG. A
   rubric checker (or LLM judge) for a whitepaper. pytest for code.

4. **Checkpoint** — "save state so we can revert." VM snapshot for
   STIG. Git commit for everything else.

5. **SkillRuntime** — bundles the other four so the harness gets
   one object to talk to.

That's it. Those five abstractions are everything the Ralph loop
needs. Everything else — the memory tiers, the plateau detection,
the architect re-engagement, the evaluation triage, the conversation
management — lives in the harness and works for *any skill* that
implements these five interfaces.

## The evaluation triage insight

While extracting the evaluator interface, a second realization landed.
The old evaluator returned a boolean: pass or fail. But the overnight
run data showed three *kinds* of failure, and the harness needed to
respond differently to each.

So `EvalResult` doesn't just say "pass" or "fail" — it says *how*
it failed:

```python
class FailureMode(Enum):
    HEALTH_FAILURE = "health_failure"   # target is broken
    EVALUATOR_GAP  = "evaluator_gap"    # target healthy, evaluator says fail
    FALSE_NEGATIVE = "false_negative"   # evaluator passed but noise triggered
    CLEAN_FAILURE  = "clean_failure"    # normal failure
```

This is the key to the scanner-gap detector. When the harness sees
three consecutive `EVALUATOR_GAP` failures with distinct approaches,
it tells the architect: "the model has tried three different
strategies and they all produced correct configuration that the
evaluator rejected. This is a knowledge gap, not a logic gap.
Consider ESCALATE."

The STIG evaluator maps its signals to these modes:
- Health check fails → `HEALTH_FAILURE`
- Health OK but OpenSCAP says fail → `EVALUATOR_GAP`
- Everything passes → success

A whitepaper evaluator would map differently:
- Spell check fails → `CLEAN_FAILURE` (fixable)
- LLM judge says "incoherent argument" → `EVALUATOR_GAP` (might need
  a fundamentally different approach)
- Word count check fails → `CLEAN_FAILURE`

Same harness logic, different signals. That's the abstraction working.

## The terrifying moment

I had 75 property tests and a proven overnight run. The refactor
touched every function in the main loop — evaluation, checkpointing,
scanning, tool wiring. One thing wrong and the next run would fail
in ways that were hard to predict.

The strategy: change the plumbing, not the behavior. Every concrete
call (`snapshot_exists`, `evaluate_fix`, `stig_scan`) got replaced
with an interface call (`runtime.checkpoint.exists`,
`runtime.evaluator.evaluate`, `runtime.work_queue.scan`). The STIG
skill's runtime module reimplemented the exact same logic, calling
the exact same underlying functions, through the new interface.

The test: `pytest tests/ -v`. 75 passed. Zero failed.

The refactor added no new features. The run would produce
*identical* results. But now the harness doesn't know it's running
STIG. It knows it has work items, an evaluator, a checkpoint
mechanism, and an executor. What those *are* is the skill's problem.

## What this enables

A new skill is now:

1. A `skill.yaml` manifest (name, description, prompts, UI labels)
2. A `runtime.py` implementing five small classes
3. Prompts for architect/worker/reflector

No harness changes. No ralph.py modifications. No forking.

The task graph and parallelism coming next operate on `WorkItem`
objects — they don't care if those objects are STIG rules, whitepaper
sections, or Kubernetes manifests. The DAG visualization in the
dashboard will show nodes and edges — it doesn't care what the nodes
represent.

That's the payoff of doing the extraction before the fun stuff: the
fun stuff is automatically skill-agnostic because it builds on
interfaces, not STIG code.

## The meta-lesson

I could have skipped this refactor and built task graphs directly
into the STIG-specific code. It would have been faster for the demo.
But it would have meant every future skill reimplements the task
graph, the triage logic, the conversation management — or worse,
nobody writes a second skill because the cost is too high.

The overnight run proved the architecture works. The interface
extraction made it *transferable*. For a project whose explicit goal
is "share what was learned so others can build similar systems
faster," that transferability isn't a nice-to-have. It's the whole
point.

---

## Related

- [`journey/19`](19-research-and-v4-architecture.md) — the research
  and decision that led to this refactor.
- [`journey/06.5`](06.5-stateful-loop-refactor.md) — the previous
  major refactor (ADK LoopAgent → Python-driven loop). Same courage,
  different scale.
- [`journey/17`](17-v3-fix-pass.md) — the v3 fixes that had to be
  preserved through the refactor.
