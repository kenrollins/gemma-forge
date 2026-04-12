---
id: architecture-01-reflexive-agent-harness-failure-modes
type: architecture
title: "Failure Modes in Reflexive Agent Harnesses"
date: 2026-04-11
tags: [L4-orchestration, reflexion-loop, tool-calling, context-management, snapshot-revert, postmortem]
related:
  - journey/14-overnight-run-findings
  - journey/15-the-test-as-architecture-discovery
  - improvements/01-architect-reengagement
  - improvements/02-worker-single-action-enforcement
  - improvements/03-context-budget-assembly
  - improvements/04-snapshot-based-revert
one_line: "A project-agnostic taxonomy of six failure modes in reflexive agent harnesses, with prescribed harness mechanisms for each. Discovered empirically from a 10-hour adversarial run."
---

# Failure Modes in Reflexive Agent Harnesses

**Status:** Working draft, evidence still being collected
**Audience:** People building agentic systems with a reflection / retry loop
**Note:** This document is intentionally project-agnostic. It emerged from
empirical work on a specific system (a STIG remediation harness on edge
hardware), but the failure modes are not specific to that domain. The
running example in each section uses STIG remediation, but the abstract
properties apply to any reflexive agent loop.

## Premise

A *reflexive agent harness* is a control loop that wraps one or more LLM-driven
agents and structures their work via:

- A discrete unit of work (a *work item* — a task, a rule, a CVE, a ticket)
- Multiple agents with distinct roles (typically a planner / strategist, an
  executor / worker, an evaluator, and a reflector)
- A retry mechanism that learns from prior failures
- A target environment that is modified by the executor and inspected by the
  evaluator

The Reflexion paper (Shinn et al., NeurIPS 2023) is the canonical academic
treatment. Implementations of the same idea now appear in many production
systems under names like "self-healing pipelines," "agentic remediation,"
"closed-loop ops AI," etc.

The pattern is intuitive enough that most teams build a first version of it
in a few days. That version usually works on a small toy example. Then it
gets pointed at a real workload and breaks in surprising ways. This document
catalogs the surprises and prescribes the harness mechanisms that handle each.

The six failure modes below were discovered empirically. Each entry includes:
the abstract failure, a concrete witness from the empirical run, the root
cause, and the harness mechanism that addresses it. Citations to the
underlying run data are in the running-example footnotes.

---

## Failure Mode 1: Tool-call explosion

### The abstract failure
An agent turn that should produce one tool call instead produces many. The
agent's LLM, when its tool call returns an error or unsuccessful result,
defaults to retrying the call with tweaked arguments. The retries happen
*inside* a single conversational turn, accumulating in the LLM's in-turn
context, bypassing whatever outer retry / reflection mechanism the harness
uses, and eventually exceeding the model's context window.

### The witness
In a 10-hour run, a single agent turn made **15 consecutive `apply_fix`
tool calls** over 350 seconds before crashing with a context-overflow error.
Zero text responses were produced in that window. The harness's outer
reflexion loop counted this as "one attempt." The Reflector never saw the
14 hidden retries.

### Root cause
The LLM's tool-calling default behavior is "if the tool failed, try again."
This is reasonable for some tasks (e.g., a search assistant trying different
queries) but disastrous for any agent embedded in a harness that already
has a structured retry mechanism. The agent's local retry instinct competes
with — and bypasses — the harness's global retry mechanism.

### Harness mechanism: per-turn action budget
Every agent turn has a configurable maximum number of tool invocations,
defaulting to 1 for executor agents. The harness intercepts attempted tool
calls beyond the cap, ends the turn, and yields a synthetic text response
explaining what happened. The outer loop then handles retry-with-reflection
as designed.

This mechanism is **independent of which tool, which agent, or which
skill** — it's a property of the harness itself. Tests of this mechanism
should use synthetic agents and dummy tools so they verify the property,
not a specific instance.

### What it costs
A single fast probe inside the harness's event loop. Tool counts are easy
to maintain. The hardest part is understanding that you need it.

### Generalization
The deeper principle: **agent-local control instincts compete with
harness-global control mechanisms, and the harness must always win.**
Tool retry is one example. Other examples include: an agent that decides
to "wait and try again" by sleeping (add a wall-clock cap); an agent that
tries to spawn sub-agents (add an agent-spawn cap); an agent that produces
unbounded text (add a token cap on its output). Every time the LLM has a
default behavior for handling adversity, the harness needs an explicit
opinion about whether to allow it.

---

## Failure Mode 2: Target-state corruption

### The abstract failure
The executor agent applies a change to the target environment that the
revert mechanism cannot fully undo. From that point on, every subsequent
attempt — on this work item or on any other — operates against a corrupted
environment. The corruption is silent: the harness doesn't know the target
is broken, the agents don't know either, and progress stops.

### The witness
A fix for the STIG rule "remove NOPASSWD from sudoers" successfully removed
NOPASSWD (the rule check passed!), but broke passwordless sudo as a
side effect. The mission-app health check needed sudo. The revert script
needed sudo to restore /etc/sudoers. Sudo was broken. The revert silently
failed. From that point on, **every attempt for ~7 hours hit "sudo: a
password is required" and produced 42 reflections about "privilege
escalation deadlock."** None of them were recoverable because the target
was permanently corrupted.

### Root cause
Script-based revert assumes the revert script is correct AND that the
preconditions for running it (sudo, network, filesystem, key binaries) all
still hold. The fix is allowed to violate any of those preconditions. There
is no point at which the harness has both (a) authoritative recovery power
and (b) something that cannot be defeated by the guest itself.

### Harness mechanism: snapshot-based revert
The revert mechanism must be **above** the target environment, not inside it.
For VMs, this means hypervisor-level snapshots (libvirt, VMware,
container snapshots, ZFS clones). For other targets, the equivalent: git
reset for code, transaction rollback for databases, AWS CloudFormation
stack rollback for cloud infrastructure, etc. The principle is: the
revert authority must live in a control plane the workload cannot touch.

A two-snapshot scheme works well in practice:
- **`baseline`**: pristine initial state, never modified
- **`progress`**: rolling, advanced after each successful work-item
  remediation, preserving accumulated wins

Failed attempts restore to `progress` (or `baseline` if no progress yet).

### Generalization
The deeper principle: **assume the executor will at some point break
something it didn't anticipate, and design recovery to be authoritative
regardless of what was broken.** The Worker-written revert script becomes
informational metadata ("this is what I thought I was undoing"), useful for
post-run analysis but no longer load-bearing for correctness.

This shift is uncomfortable for teams who like the narrative "the agent
reverts its own work." That narrative is sweet but it cannot survive
contact with a real target that the agent is allowed to modify in
unconstrained ways.

---

## Failure Mode 3: Diagnostic blindness

### The abstract failure
When an attempt fails, the harness records the fact of failure but not its
cause. The Reflector receives only "APPLY_FAILED" or equivalent, has no
real facts about *why* the failure occurred, and produces speculation
instead of analysis. Subsequent attempts learn nothing from the prior
failure because there was nothing concrete to learn from.

### The witness
Of 226 reflections in the overnight run, **42 (18%) cited sudo as the
root cause** in some form. The Reflector was correct — sudo was broken.
But the Reflector was *guessing* from indirect signals (the worker's
output mentioned permission errors), because the harness never gave it
direct access to environment state. The result: 42 different rephrasings
of the same diagnosis, none actionable, because the underlying fact
(sudoers had been edited and not restored) was never named.

### Root cause
The reflexion architecture assumes the executor's output is sufficient
input for the reflector. It is not. The executor only knows what it
*tried*; it doesn't know the environmental state that caused the failure.
The reflector therefore reasons about reported behavior, not actual state.

### Harness mechanism: structured diagnostic capture
Before any revert, the harness runs a **diagnostic capture** against the
target environment that gathers structured forensic data:

- Health probes for each known critical service / invariant
- The state of each configuration domain the executor might have touched
  (sudoers, PAM, SELinux, network, filesystem, etc.)
- Recent error logs from the target's own logging
- A boolean flag for each invariant ("is sudo working", "is the mission
  app healthy", "is the filesystem writable")

The diagnostic snapshot is emitted as a structured event in the run log
AND fed into the reflector's prompt as concrete facts. The reflector now
reasons about real state, not about reported behavior.

The diagnostic capture ALSO needs an out-of-band channel — when the
target's primary access path is broken, the diagnostic gather is exactly
when we need it most. For VMs, this means hypervisor-level access (virsh
console, VM serial). For other targets: bypass paths that don't depend on
what the agent might have broken.

### Generalization
The deeper principle: **the reflector cannot reason about what it cannot
see, and the only thing it sees by default is the executor's report of
its own behavior, which is insufficient.** Skills should declare what
their target's "vital signs" are, and the harness should capture those
vital signs every time something goes wrong, with a transport channel
that doesn't depend on the target being healthy.

---

## Failure Mode 4: Cosmetic novelty masking semantic sameness

### The abstract failure
The reflector is asked "is this the same as last time?" and answers no
because the reflection text is different — different word choices, different
sentence structure, different elaboration — even though the underlying
diagnosis is identical. Plateau detection fails. The harness believes the
reflector is making progress when it is in fact stuck.

### The witness
A naive plateau detector compared the first sentences of consecutive
reflections via string equality. Across 226 reflections in the overnight
run, it flagged **0%** as plateaued. Manual inspection of the reflection
data shows the actual rate was **76%** — for example, the rule
`partition_for_var_log_audit` produced 20 reflections, all of which said
some variation of "this is a partitioning requirement that cannot be done
at runtime via bash scripts," and none of which the naive detector
identified as repeats.

The variations were things like "structural disk partitioning requirement"
vs "structural disk partitioning requirements" vs "hardware/disk
partitioning requirement via runtime scripts on a live system." Cosmetically
different, semantically identical.

### Root cause
LLMs exhibit *high cosmetic novelty* — they readily rephrase the same idea
in different words, especially when the prompt asks them to "reflect" or
"analyze." String-equality plateau detection therefore underdetects by an
order of magnitude.

### Harness mechanism: content-set comparison
Replace string comparison with **semantic-set comparison**: extract content
keywords (drop stopwords, normalize plurals, lowercase, strip punctuation)
from each reflection, then test whether the keyword sets across a window of
recent reflections share enough core content to be considered the same.

A simple implementation: window of 3 reflections, plateau fires when the
intersection of all three keyword sets contains ≥ 3 content words. This is
robust to length variation, word order, and most cosmetic rewrites.

For higher accuracy, embeddings (`sentence-transformers/all-MiniLM-L6-v2`,
6 MB local model) and cosine similarity work better. Choice depends on
your dependency tolerance.

### Generalization
The deeper principle: **LLMs have many degrees of freedom in how they
phrase the same idea, and any "is this novel?" check that operates on the
phrasing rather than the content will be defeated.** Apply this lesson
broadly: deduplication of LLM outputs, novelty-bonus reward signals,
"have I seen this before?" queries — all need content-level comparison,
not lexical comparison.

---

## Failure Mode 5: Authority hierarchy gap

### The abstract failure
The reflector identifies a fundamental problem with the current strategy
("this rule cannot be solved at runtime, give up") and the executor cannot
act on it because the executor's instructions are scoped to a single
attempt, not to overall strategy. The strategy-setting agent (the planner
/ architect) is not consulted during the inner retry loop, so its initial
strategy persists unchanged even when the reflector knows it's wrong. The
loop grinds.

### The witness
The Reflector for `partition_for_var_log_audit` produced 20 reflections in
sequence, every one of which said "stop attempting to partition the disk."
The Worker kept trying because the Worker's prompt was scoped to "fix this
rule with a bash script," and the Worker had no authority to escalate or
change strategy. The Architect was never consulted between rule selection
and rule completion. **20 minutes of wall clock burned on a rule the
Reflector had correctly diagnosed in the first 30 seconds.**

### Root cause
The reflexion architecture as commonly implemented has the strategy layer
(plan / select) outside the retry loop, and the execution + reflection
layers inside. There is no path for reflection-derived insights to reach
the strategy layer until *after* the work item completes. By then it is
too late to act on them.

### Harness mechanism: strategic re-engagement
Periodically — every N failed attempts on the same work item, OR whenever
the plateau detector fires — the strategy agent is re-invoked in
"re-engagement mode" with the full failure history for the current work
item. It returns one of:

- **CONTINUE**: the current strategy is sound; provide refined guidance
- **PIVOT**: the current strategy is wrong; provide a fundamentally
  different approach
- **ESCALATE**: this work item cannot be solved with the current toolset
  / environment / permissions; preemptively escalate

The verdict is parsed and acted on. PIVOT and CONTINUE update the
"current plan" that subsequent executor attempts see. ESCALATE breaks the
inner loop with a structured escalation reason.

### Generalization
The deeper principle: **observation and authority must be coupled, or
observation is wasted.** Whatever agent in your system knows that something
is wrong needs a path to make a decision about it. The path can be direct
(the observer also has decision authority) or hierarchical (the observer
notifies a higher-level agent on a schedule) — but it must exist.

The hierarchical version is usually preferred because it preserves
specialization: the executor stays focused on execution, the reflector
stays focused on analysis, and a separate strategist makes decisions
informed by both. But the hierarchical version requires explicit
re-engagement points, which most reflexion implementations lack.

---

## Failure Mode 6: Budget-type mismatch

### The abstract failure
The harness limits work using a metric that doesn't reflect what physically
constrains progress. The most common version: limiting the number of
retries per work item. This metric makes sense for benchmark settings
where each task is bounded in cost, but it makes no sense for production
settings where the cost is wall-clock time and the value is rules
remediated. The harness either gives up too early (when more grinding
would have worked) or burns resources unboundedly (when grinding will not
work).

### The witness
The Reflexion paper uses a fixed retry cap of 3-5 because it's evaluating
hundreds of tasks and needs bounded per-task cost. The naive implementation
of GemmaForge inherited this as `max_retries_per_rule: 3`. We then noticed
that the most interesting reflexion behavior happens at retries 4-10
(where the reflector has accumulated enough failure history to suggest
fundamentally different approaches). The 3-retry cap was cutting off the
loop exactly when it would have started paying off. Switching to a
**wall-clock budget** (20 minutes per rule) revealed both successes and
failures the attempt cap had hidden.

### Root cause
"Number of attempts" is a *proxy* for the resource that actually constrains
the system. In an academic benchmark, the constrained resource is "compute
spent per task," and attempt count is a tolerable proxy. In a production
system, the constrained resources are wall-clock time and operator
patience, and attempt count is a *bad* proxy because attempts can be fast
or slow depending on what's being tried.

### Harness mechanism: time-budgeted work items
Each work item gets a wall-clock budget. The inner retry loop runs until
the budget is exhausted, with a high attempt-count safety cap that exists
only to prevent runaway loops with degenerate timing. Escalation reasons
distinguish "time budget" from "retry ceiling" so post-run analysis can
tell which limit hit.

This couples directly with the strategic re-engagement mechanism: the
re-engagement check fires when there is still meaningful budget remaining
("don't re-engage if there's only 30 seconds left"), and the architect's
verdict can preemptively escalate to free up budget for other work items.

### Generalization
The deeper principle: **constrain the resource that physically limits
your system, not a proxy for it.** Wall-clock for production. Tokens for
cost-sensitive APIs. GPU-seconds for compute pools. Operator approval
events for human-in-the-loop systems. Whatever is actually scarce is
what your loop should count.

---

## What this list is and isn't

**It is**: a starter taxonomy of failure modes that any team building a
reflexive agent harness will encounter, with prescribed harness mechanisms
that address each.

**It isn't**: complete. We expect to add to it as we run further experiments
on this and other skills. Two failure modes we suspect but have not yet
empirically demonstrated:

- **Memory tier collapse**: episodic and semantic memory grow unboundedly
  and collide. We have a rough fix for this (distillation + token budget)
  but not a deep treatment.
- **Inter-agent context drift**: agents in a multi-agent system gradually
  lose alignment because they each see different slices of state. The
  fix is probably "shared structured state with explicit views," but we
  haven't built or tested it yet.

**It also isn't**: a paper. There's enough here for a paper, but a paper
needs comparison against alternative architectures, statistical claims
about effect sizes, and a more rigorous evaluation. This document is the
field notes that a paper would build on.

## Known limitations of the current GemmaForge implementation

These are gaps where the abstract failure modes are correctly named but
the harness's *current implementation* of the prescribed mechanism falls
short. They are documented honestly here so the contribution doesn't
overclaim.

### Out-of-band diagnostic channel reliability (gap in failure mode 3)

The diagnostic gather is supposed to fall back to an out-of-band channel
(virsh console) when the in-band channel (SSH+sudo) is broken. The
current `gather_environment_diagnostics` correctly invokes the fallback
when SSH-via-sudo fails, but the underlying console channel
implementation (`gemma_forge/harness/tools/console.py`) is fragile —
the virsh console subprocess protocol fails with "Connection lost" in
practice. This was discovered by Tier 3 of the test pass when we
deliberately broke sudo and watched the fallback path try to take over.

The consequence is that when sudo is broken, the harness still detects
that condition (sudo_ok=False is reported with high confidence directly
from the SSH probe's stderr), but cannot gather rich forensics for
*why* — service status, mission healthcheck, journal errors are all
unreachable until sudo is restored via snapshot.

The architectural prescription stands: out-of-band channels should
exist and be used. The implementation choice (virsh console) needs
revisiting. Two cleaner alternatives:

1. **QEMU guest agent** (`virsh qemu-agent-command`) — a virtio-serial
   control plane that bypasses the guest's auth stack entirely. Requires
   `qemu-guest-agent` running in the VM but is otherwise the cleanest
   answer.
2. **A dedicated hypervisor-side agent** that stores diagnostics on
   shared storage the host can read directly.

For now: the harness recovers correctly from broken sudo because the
snapshot restore is at the libvirt level (also out-of-band), and the
loop's post-restore probe is a direct SSH call that doesn't depend on
the diagnostic gather. The Reflector receives a partial post_mortem
("sudo_ok=False") which is sufficient signal for it to reason about,
just less rich than we'd like.

This is a real architectural gap. It's documented because the
discipline is to be honest about implementation completeness, not
just architectural intent.

## Companion artifacts

If you want the empirical evidence backing each failure mode:

- The first run that produced the data: `runs/run-20260411-013326.jsonl`
- The journey narrative of how each mode was discovered:
  `journey/14-overnight-run-findings.md`
- The discipline conversation that produced this taxonomy:
  `journey/15-the-test-as-architecture-discovery.md` (TODO)
- The harness implementation that addresses each mode:
  `gemma_forge/harness/ralph.py`, `gemma_forge/harness/tools/ssh.py`
- The test suite that asserts the harness mechanisms hold as properties:
  `tests/test_harness_properties.py` (TODO)

## Versioning note

This is v0.1 of the failure-modes document. It will be revised as the
test suite produces evidence and as the harness refactor (lifting
STIG-specific concerns out of the harness core) proceeds. The version
that ships with the GemmaForge whitepaper will likely add: a seventh
failure mode (TBD), a comparison table of "what most reflexion
implementations get wrong" vs the prescribed mechanisms, and a worked
example of porting the harness to a non-STIG skill.
