---
id: journey-14-overnight-run-findings
type: journey
title: "Journey: The Overnight Run That Taught Us More Than a Successful One Would Have"
date: 2026-04-11
tags: [L4-orchestration, reflexion-loop, context-management, snapshot-revert, postmortem]
related:
  - journey/13-ralph-persistence-retry-budget
  - journey/15-the-test-as-architecture-discovery
  - architecture/01-reflexive-agent-harness-failure-modes
  - improvements/01-architect-reengagement
  - improvements/02-worker-single-action-enforcement
  - improvements/03-context-budget-assembly
  - improvements/04-snapshot-based-revert
one_line: "We ran the time-budgeted reflexion loop for 10 hours against 270 STIG rules, fixed 2, escalated 26, and discovered four architectural flaws — the most important of which was that the Worker was silently running a 15-deep retry loop inside what the harness thought was a single attempt."
---

# Journey: The Overnight Run That Taught Us More Than a Successful One Would Have

## The story in one sentence
We ran the time-budgeted Ralph loop for 10 hours against 270 STIG rules, fixed
2, escalated 26, and discovered four architectural flaws we couldn't have seen
any other way — the most important of which was that the Worker was silently
running a 15-deep retry loop *inside* what we thought was a single attempt.

## What we ran

- **Start**: 2026-04-11 01:33 UTC
- **Duration**: 10 hours 4 minutes (stopped by operator)
- **Skill**: `stig-rhel9`
- **Model**: Gemma 4 31B bf16, TP=4, all 4 L4 GPUs
- **Config**: `max_iterations: 1000`, `max_retries_per_rule: 100` (safety cap only),
  `max_wall_time_per_rule_s: 1200` (20 min per rule — the real escalation trigger)
- **Instrumentation**: the full v2 telemetry from `journey/13`: heartbeats, phase
  timing, rule categories, plateau detection, `rule_complete` events, `ban_added`
  events, `tool_error` capture.

## The headline numbers

| Metric | Value |
|---|---|
| Total events | 6,158 |
| `rule_complete` events | 28 |
| Remediated | 2 |
| Escalated | 26 |
| Reflections emitted | 229 |
| Banned patterns accumulated | 128 |
| Context overflow errors | 9 |
| LLM calls | 520 |
| Tool calls | 2,174 |
| **Throughput** | **2.78 rules/hour** |

Both successful remediations happened at **attempt 1** (both trivial package
installs: 10s and 43s). Zero rules were remediated at attempts 2 or later.
Every single escalation hit the 20-minute wall-clock budget, not the
100-retry ceiling.

## Finding 1: The Reflector was right from attempt 1 and was ignored for 20 more

The deepest escalation was `xccdf_org.ssgproject.content_rule_partition_for_var_log_audit`
— 24 attempts, 20.3 minutes wall time, 20 reflections.

The **first reflection** on this rule said:

> "Pattern: Attempting to remediate a hardware/disk partitioning requirement via
> runtime scripts on a live system. Root cause: The Architect is treating a
> structural infrastructure requirement (separate partition) as a configuration
> file change. Strategic recommendation: Stop attempting to fix the partition
> layout on the fly."

The Reflector said some variation of "stop attempting to physically partition
the disk via runtime scripts" **20 times in a row**. The Worker kept trying
bash variations on `fdisk`/`mount`/`losetup`/LVM commands because nothing in
the loop had the authority to act on the Reflector's escalate recommendation.

This is direct empirical confirmation of the hypothesis in
`improvements/01-architect-reengagement.md`: **the Reflector can diagnose but
cannot act.** The Architect never re-engages during the inner loop. The Worker
is compelled to keep trying. The loop grinds until the clock runs out.

## Finding 2: Plateau detection was catastrophically naive

We added a plateau-detection metric that compared the first sentences of
consecutive reflections via string match. Results:

- 226 reflections total
- 163 unique first sentences (72% uniqueness)
- **0% flagged as plateaued**

But look at the actual first sentences from the partition rule:

- "Attempting to remediate a hardware/disk partitioning requirement…"
- "Attempting to remediate a structural disk partitioning requirement…"
- "Attempting to remediate structural disk partitioning requirements…"
- "Attempting to remediate structural disk partitioning requirements (LVM, fdisk, loopback mounts)…"

Cosmetically different, semantically identical. The detector counted them as
novel because the exact string didn't match. We needed **semantic similarity**
(embedding cosine or fuzzy match), not string equality.

The top 10 near-duplicate patterns across the run:

| Count | Pattern (first 80 chars, lowercased) |
|---:|---|
| 12 | attempting to remediate structural disk partitioning requirements (lvm, fdisk, l |
| 11 | privilege escalation deadlock |
| 9 | systematic privilege escalation failure |
| 6 | systematic execution failure due to `sudo` authentication requirements and tty r |
| 5 | infinite loop of privilege escalation failures |
| 4 | persistent privilege escalation failure |
| 4 | total execution blockade due to `sudo` authentication requirements and tty restr |
| 3 | transport-layer obsession |
| 3 | total execution blockade |
| 3 | persistent failure of privileged package installation due to interactive `sudo`  |

Real plateau rate is probably 60–80%, not 0%.

## Finding 3: The sudo deadlock — an environment-level failure the loop couldn't detect

Of 226 reflections, **42 (18%)** cited sudo/privilege escalation as the root
cause. Several were in "infinite loop of privilege escalation failures" or
"persistent privilege escalation failure" framings. This suggests a real
environment-level breakage that started partway through the run.

Two likely causes:

1. **Non-tty sudo**: our SSH tool uses `asyncssh` with a non-tty session. Rules
   that touch `/etc/sudoers` with `requiretty` settings may have broken sudo
   for subsequent non-tty calls, and the revert may not have fully undone it.
2. **Revert incompleteness**: the revert script is generated by the Worker and
   stored before the fix runs. If the fix partially executed (like the
   `APPLIED` call at 3500s discussed in Finding 4), the revert script may not
   cover the state it needed to undo.

Regardless of root cause, **the loop had no environment sanity check**. The
architecture assumes each rule starts from a known-good VM state but never
verifies it. The right behavior when sudo is provably broken is either: (a)
preemptively escalate every remaining rule as "environment-broken", or (b)
halt the run entirely and alert the operator. We did neither. We just kept
grinding.

## Finding 4: The Worker had an unbounded internal retry loop (the real context bomb)

This was the most surprising finding, and it reframes the entire run.

When the first context overflow fired at 3753s, the log showed:

```
[3403s] tool_call(apply_fix)    description: "Configure AIDE to verify ACLs..."
[3404s] tool_result             APPLY_FAILED
[3425s] tool_call(apply_fix)    description: "Configure AIDE to verify ACLs..."  (re-tried)
[3426s] tool_result             APPLY_FAILED
[3452s] tool_call(apply_fix)    (re-tried)
[3453s] tool_result             APPLY_FAILED
[3475s] tool_call(apply_fix)    (re-tried)
[3476s] tool_result             APPLY_FAILED
[3500s] tool_call(apply_fix)    (re-tried)
[3501s] tool_result             APPLIED   ← THIS ONE SUCCEEDED!
[3523s] tool_call(apply_fix)    (Worker kept going anyway)
[3524s] tool_result             APPLY_FAILED
... 9 more tool_call/result pairs ...
[3753s] tool_call(apply_fix)    → HTTP 400 context overflow (16,385 > 16,384)
```

**Between 3403s and 3753s — 5 minutes and 50 seconds of wall time — there are
15 `apply_fix` calls, 15 tool results, and ZERO `agent_response` events.**

This is all happening **inside a single `_run_agent_turn()` call**. The Worker
LLM called `apply_fix`, saw `APPLY_FAILED`, and instead of returning a text
response (which would end the turn), it made another `apply_fix` call. Each
tool_call + tool_result pair stays in ADK's in-turn session. By the 16th LLM
invocation, the in-turn conversation had accumulated ~6K tokens of tool
history, pushing the total prompt over 16K.

### Token breakdown of the failing prompt

Reconstructing the ~14,337 token total:

| Component | Est. tokens |
|---:|---|
| System prompt (Worker) | ~1,500 |
| Tool schema (`apply_fix` definition) | ~500 |
| Initial user message (rule + architect plan + episodic + semantic) | ~5,000 |
| 15 tool_call + tool_result pairs in turn history | ~6,000 |
| LLM reasoning text between tool calls | ~1,300 |
| **Total** | **~14,300** ✓ |

**The episodic memory was not the culprit.** The capped semantic memory was
not the culprit. The Architect's run state summary was not the culprit.
**The Worker LLM, trapped in its own tool-calling loop, was the culprit.**

### Why this matters beyond the context overflow

Our entire reflexion architecture depends on the assumption that each
`_run_agent_turn` is one discrete attempt: one fix applied, one evaluation,
one reflection (if failed), then a new attempt with fresh state informed by
episodic memory.

But if the Worker can do 15 hidden internal retries inside a single "attempt",
then:

- **Our "229 total attempts" count is a massive undercount.** The real number
  of fix invocations was probably 500-1000+.
- **The Reflector only runs after the outer harness-level attempt, not after
  each internal LLM-level retry.** The Worker was retrying WITHOUT reflection
  14 times out of every 15. The reflexion compounding story never really got
  a chance to play out.
- **The "fresh context per turn" principle is violated inside a turn.** Each
  new internal retry sees the full history of prior failed retries in the
  same turn — exactly the context pollution Ralph is supposed to avoid.
- **A single rule's "attempts" log entries don't match the LLM's experience.**
  Our event stream says "attempt 6 on rule X", but from the Worker's
  perspective, it's seen this exact rule fail ~60-90 times across all the
  hidden internal retries.

### Root cause of the internal retry loop

The Worker prompt (`skills/stig-rhel9/prompts/worker.md`) says:

> YOUR JOB:
> 1. Read the Architect's plan from the conversation history.
> 2. Call apply_fix with the fix_script, revert_script, and description.
>
> Call apply_fix now. Do not output scripts as text — use the tool.

It does **not** say "call `apply_fix` exactly once and return." The LLM's
default tool-calling behavior on a failure is to retry with tweaked arguments,
which is normally what you want — just not when there's an entire outer
harness loop designed to do that retry with a reflection step in between.

This is our bug, not a model quirk. We told the Worker to "call apply_fix"
and didn't constrain it to a single call, so the model filled in the gap
with its default retry instinct.

## Fixes (in priority order)

### 1. Stop the Worker internal retry loop (CRITICAL, both layers)

**Layer 1 — prompt:**
```
Call apply_fix EXACTLY ONCE and return a brief text summary of the outcome.
If apply_fix returns APPLY_FAILED, do NOT call it again — the harness will
handle the retry with reflection. Do NOT call apply_fix more than once per turn.
```

**Layer 2 — harness enforcement:**
In `_run_agent_turn`, count tool_call events. After the first tool_call
completes its result, force the turn to end by not yielding further events.
This is defense in depth: even if the LLM tries to retry, the harness won't
let it.

### 2. Architect re-engagement (HIGH)

Already proposed in `improvements/01-architect-reengagement.md`. This run
provides direct evidence: the 20-reflection partition escalation would have
resolved correctly at reflection 2 or 3 if the Architect had been re-engaged
to hear "this rule needs an infrastructure change, preemptively escalate."

### 3. Context budget with explicit assembly (HIGH)

Even after fix #1, we should have a deterministic token budget for each
prompt. Before each `_run_agent_turn` call, assemble the prompt pieces in
priority order (system prompt → rule context → last 3 distilled episodic
lessons → capped semantic summary → architect's plan → truncated tool results)
and drop lowest-priority pieces if the estimated token count exceeds the
budget. Rough estimate is fine — anything better than "YOLO and hope the model
doesn't overflow."

### 4. Semantic plateau detection (MEDIUM)

Replace string-match first-sentence comparison with either:
- **Fuzzy match** via RapidFuzz ratio at threshold ~85
- **Embedding cosine** via a small sentence-transformer (`all-MiniLM-L6-v2` is
  6 MB and local)

Use this both as a metric on `rule_complete` and as a signal to the Architect
re-engagement logic.

### 5. Environment sanity check (MEDIUM)

Before each attempt, run a cheap `sudo true` probe. If it fails, mark the VM
as environment-broken, trigger an automatic snapshot restore, and either
retry once or halt. The 42 sudo deadlock reflections are all symptoms of the
same root cause — a broken environment that the loop couldn't recognize.

### 6. Investigate sudo breakage origin (LOW but interesting)

Is it the non-tty SSH session (our SSH tool's fault)? Or is it an early fix
that modified `/etc/sudoers` and the revert didn't undo it cleanly? Worth a
focused investigation but lower priority than the architectural fixes.

## What this run taught us that a successful run would not have

1. **The Worker's internal retry loop is invisible in the logs.** We'd have
   shipped v2 of the reflexion loop to a demo without realizing that 80-90%
   of the Worker's actual behavior was happening outside our instrumented
   boundary.

2. **The context bomb isn't where we thought.** My pre-run hypothesis was
   "accumulating episodic/semantic memory blows up prompts." Empirical reality:
   the episodic/semantic memory is small and well-capped. The bomb is in
   the in-turn tool_call accumulation.

3. **The Reflector is smart enough to diagnose correctly.** It said "STOP,
   this is a partitioning problem" on attempt 1. It said the same thing 20
   times. We don't need a smarter Reflector. We need an Architect that
   listens to it.

4. **Plateau detection needs semantic similarity.** Our naive string match
   detector saw 0% plateau when the real rate was probably 60-80%. This was
   a false confidence signal that told us the loop was "working" when it
   was stuck.

5. **Environment breakage is invisible to the loop.** 18% of reflections cited
   sudo failures but the loop never halted or diagnosed the environment
   itself. In production this is a fatal operational blind spot.

6. **Failure runs are more valuable than success runs for architecture work.**
   Two successful AIDE install fixes would have told us nothing. 26 failures
   taught us everything we needed for v3 of the loop.

## Related

- `journey/13-ralph-persistence-retry-budget.md` — the time-budget change that
  enabled this run to expose these flaws
- `improvements/01-architect-reengagement.md` — the architect re-engagement
  proposal, now empirically validated
- `improvements/02-worker-single-action-enforcement.md` — to be written, covers
  fix #1 above
- `improvements/03-context-budget-assembly.md` — to be written, covers fix #3
- Raw run log: `runs/run-20260411-013326.jsonl`
- Run duration: 10h 4m
- Process PID: 3235122 (stopped cleanly by `bin/forge stop-run`)
