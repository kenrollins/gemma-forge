---
id: journey-14-overnight-run-findings
type: journey
title: "The Overnight Run That Taught Me More Than a Successful One Would Have"
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
one_line: "Ten hours. Two STIG rules fixed. Twenty-six escalated. And buried in the logs, a silent 15-to-1 discrepancy between what the harness thought was happening and what the Worker was actually doing."
---

# The Overnight Run That Taught Me More Than a Successful One Would Have

On attempt 1 of `xccdf_org.ssgproject.content_rule_partition_for_var_log_audit`, the Reflector wrote this:

> Pattern: Attempting to remediate a hardware/disk partitioning requirement via runtime scripts on a live system. Root cause: The Architect is treating a structural infrastructure requirement (separate partition) as a configuration file change. Strategic recommendation: **Stop attempting to fix the partition layout on the fly.**

That was correct. You cannot fix a partition scheme with a shell script. The rule requires a change to the disk layout — provisioning the VM differently from the start, or accepting that the rule cannot be remediated on a running system. The Reflector understood this inside the first minute.

Attempt 2 re-ran the same class of fix. Attempt 3 re-ran it. Attempt 4. The Reflector produced twenty reflections on this rule, each saying some variation of *this is a partition problem and you cannot fix it with a bash script*. The Worker tried `fdisk`, then `losetup`, then LVM commands, then bind mounts. Twenty-four total attempts. Twenty minutes and eighteen seconds of wall time.

No one could listen. The harness had no mechanism for the Reflector's recommendation to change what the Worker did next. The Reflector wrote its twentieth near-identical reflection and the Worker kept hammering at a structurally unsolvable problem. The budget ran out. The harness marked the rule escalated. The next rule started.

That rule did the same thing.

---

I had run the time-budgeted Ralph loop for 10 hours against 270 STIG rules. Two were fixed. Twenty-six were escalated like the partition rule — the loop grinding on each for its full 20-minute budget before moving on. The other 242 never got their turn.

Both successful remediations happened on attempt 1: trivial package installs, done in ten and forty-three seconds. Zero rules were remediated on attempts 2 or later. **Every single escalation hit the 20-minute wall-clock budget, not the 100-retry ceiling.** The retry ceiling existed. Nothing ever reached it. The time budget was the ceiling.

Throughput: 2.78 rules per hour. At that rate the 270-rule run would finish sometime in 2029.

| | |
|---|---|
| Start | 2026-04-11 01:33 UTC |
| Duration | 10 hours 4 minutes (stopped by operator) |
| Events logged | 6,158 |
| Rules completed | 28 (2 fixed, 26 escalated) |
| Rules untouched | 242 |
| Reflections emitted | 229 |
| Banned approach patterns accumulated | 128 |
| Context overflow errors | 9 |
| LLM calls | 520 |
| Tool calls | 2,174 |

The rest of this entry is the four architectural failures behind those numbers, in the order I found them. The last one reframes everything else.

## Finding 1: The Reflector was screaming into the void

The partition rule wasn't special. Most of the twenty-six escalations followed the same shape. The Reflector identified the problem early, produced roughly correct guidance, and watched the Worker ignore it for the full twenty-minute budget. Twenty reflections into the partition rule: *this is a partition problem*, with minor wording variations. Twenty into the `sudoers` rule: *something just broke sudo and new fixes can't apply*. Twenty into the kernel module rule: *this requires a reboot the loop cannot perform*.

The Reflector is the smartest agent in the architecture. It saw each of these in under three minutes. And nothing in the loop had the authority to act on its recommendation. There is no "ESCALATE" verb the Reflector can emit. There is no Architect re-engagement. The only agent that can change what happens on the next attempt is the Worker, and the Worker only has authority over *how* to try — not *whether* to try. So the loop grinds, and the Reflector keeps producing reflection after reflection that nobody in the system has the power to act on.

!!! quote ""
    **The Reflector can diagnose. It cannot act.**
    It is, structurally, screaming into the void. The architecture assumes the Worker will eventually internalize the Reflector's guidance. The Worker, empirically, does not.

The fix is an Architect re-engagement verb: CONTINUE, PIVOT, or ESCALATE, triggered after N failed attempts or when the Reflector plateaus. [`improvements/01`](../improvements/01-architect-reengagement.md) has the full design. This run confirmed the need; the next one tests the fix.

## Finding 2: Plateau detection was catastrophically naive

To detect when the Reflector was repeating itself, I had added a metric: compare the first sentences of consecutive reflections by exact string match. Simple. Cheap. Fast.

Across 226 reflections, 163 had unique first sentences. **Zero were flagged as plateaued.**

Here are four consecutive first sentences from the partition rule:

> "Attempting to remediate a hardware/disk partitioning requirement…"
> "Attempting to remediate a structural disk partitioning requirement…"
> "Attempting to remediate structural disk partitioning requirements…"
> "Attempting to remediate structural disk partitioning requirements (LVM, fdisk, loopback mounts)…"

A human reader flags these as the same reflection. The string matcher sees four distinct sentences. Same problem. Different words. 0% plateau rate on a run where the real rate was probably 60–80%.

The top 10 near-duplicate patterns across the full run:

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

The fix is semantic similarity — fuzzy match via RapidFuzz or a small local embedding model — not string equality. When the Reflector is saying the same thing with synonyms, the loop needs to know.

## Finding 3: The sudo deadlock the loop couldn't see

Forty-two of the 226 reflections cited sudo or privilege escalation as the root cause. Framings like "infinite loop of privilege escalation failures" and "total execution blockade" started appearing partway through the run and kept appearing. Something about the environment had changed, and the loop couldn't tell.

Two candidate causes, both plausible:

1. **Non-tty sudo.** The SSH tool uses `asyncssh` with no pseudo-terminal. A fix that touched `/etc/sudoers` with `requiretty` settings could have broken sudo for subsequent non-tty calls, and the revert may not have fully undone the damage.
2. **Incomplete revert.** Revert scripts are generated by the Worker before the fix runs. If a fix partially executed (like the `APPLIED` call at 3500s we're about to see), the revert doesn't cover state it didn't know existed.

Root cause aside, **the loop had no environment sanity check.** The architecture assumed every rule starts from a known-good VM state. Nothing ever verified it. When sudo was provably broken for the remaining 80% of the run, the right response was either (a) halt and alert the operator, or (b) preemptively escalate every remaining rule as environment-broken. The loop did neither. It kept grinding.

The fix is a 200ms `sudo true` probe before every attempt. Cheap to run. Fatal not to have.

## Finding 4: The Worker had a hidden retry loop that made every other metric lie

This one reframes the entire run.

At 3753 seconds, the first context overflow fired. The prompt was 16,385 tokens; the model's limit was 16,384. One token over. My pre-run hypothesis for how prompts would blow up was "accumulating episodic and semantic memory." I went looking for the accumulation.

The logs told a different story. Between 3403s and 3753s — five minutes and fifty seconds of wall time — the Worker made **fifteen** `apply_fix` tool calls:

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
[3501s] tool_result             APPLIED     ← THIS ONE SUCCEEDED
[3523s] tool_call(apply_fix)    (Worker kept going anyway)
[3524s] tool_result             APPLY_FAILED
... 9 more tool_call/result pairs ...
[3753s] tool_call(apply_fix)    → HTTP 400 context overflow (16,385 > 16,384)
```

Fifteen tool calls. Fifteen tool results.

!!! quote ""
    **Zero `agent_response` events.** All of it happening inside a single call to `_run_agent_turn()`.

From the harness's perspective, this was *one attempt on one rule*. The event log said one attempt. The Reflector wouldn't be invoked until this turn ended. Episodic memory wouldn't update. Semantic memory wouldn't see the fourteen failed retries.

From the Worker's perspective, it had just tried to fix the same rule fifteen times, in the same session, each failure visible to the next attempt. The fix succeeded at attempt 5 — the one marked `APPLIED`. *The Worker kept going anyway*, because nothing in the system told it to stop. Ten more tool calls on a rule it had already fixed. The in-turn conversation history got big enough to push the prompt past the model's context limit and crash the turn with HTTP 400.

The architecture assumes every call to `_run_agent_turn()` is one discrete attempt. Fifteen internal retries inside one "attempt" breaks every assumption downstream:

- The **"229 total attempts"** count is a massive undercount. Real count was probably 500–1,000+.
- The **Reflector** only runs between outer attempts. The Worker was retrying *without reflection* fourteen times out of every fifteen. The reflexion compounding story never got to compound.
- The **"fresh context per turn"** principle — Ralph's entire reason to exist — was violated inside the turn itself. Each internal retry saw the full history of prior failed retries. Exactly the context pollution Ralph is designed to avoid.
- The **attempt counter** didn't match the Worker's actual experience of the rule. The event stream said "attempt 6 on rule X." The Worker had seen this rule fail sixty times already, across the hidden internal retries on attempts 1 through 6.

Reconstructing the failing prompt:

| Component | Est. tokens |
|---:|---|
| System prompt (Worker) | ~1,500 |
| Tool schema (`apply_fix` definition) | ~500 |
| Initial user message (rule + architect plan + episodic + semantic) | ~5,000 |
| **15 tool_call + tool_result pairs accumulated in turn** | **~6,000** |
| LLM reasoning text between tool calls | ~1,300 |
| Total | ~14,300 |

The episodic memory was not the culprit. The semantic memory was not the culprit. The Worker LLM, trapped in its own tool-calling loop, was the culprit. The context bomb was entirely internal to a single "attempt."

### Root cause

The Worker's prompt (`skills/stig-rhel9/prompts/worker.md`) said:

> YOUR JOB:
> 1. Read the Architect's plan from the conversation history.
> 2. Call apply_fix with the fix_script, revert_script, and description.
>
> Call apply_fix now. Do not output scripts as text — use the tool.

It did not say "call `apply_fix` exactly once and return." The LLM's default behavior on a tool-call failure is to retry with tweaked arguments — which is usually what you want. It's just not what you want when there is an entire outer harness loop designed to retry *with reflection between attempts*.

The harness expected the Worker to behave like a function. The Worker behaved like an autonomous retry loop. Both behaviors are reasonable in isolation. Stacked on top of each other, they produce a silent 15-to-1 discrepancy between what the harness thinks is happening and what's actually happening.

## Fixes, priority order

### 1. Stop the Worker's internal retry loop. Two independent controls.

**Prompt side** (`worker.md`):

```
Call apply_fix EXACTLY ONCE and return a brief text summary of the outcome.
If apply_fix returns APPLY_FAILED, do NOT call it again — the harness will
handle the retry with reflection. Do NOT call apply_fix more than once per turn.
```

**Harness side** (`_run_agent_turn`): count tool_call events per turn. Force the turn to end after the first tool_call completes. The prompt tells the Worker not to retry. The harness enforces it anyway, in case the prompt fails. Two independent controls so one can catch what the other misses.

This is the critical fix. Everything else is incremental. Finding 4 alone justified the overnight run.

### 2. Architect re-engagement

Already proposed in [improvements/01](../improvements/01-architect-reengagement.md). The twenty-reflection partition escalation would have resolved correctly at reflection 2 or 3 if the Architect had been re-invoked with the Reflector's guidance and the verb set `{CONTINUE, PIVOT, ESCALATE}`.

### 3. Deterministic context budget

Even after fix #1, the prompt needs an explicit token budget assembled in priority order (system → rule → episodic → semantic → architect plan → tool results). Drop lowest-priority pieces if the estimate exceeds the budget. Rough estimates beat YOLO.

### 4. Semantic plateau detection

Replace first-sentence string match with either fuzzy matching (RapidFuzz ratio > 0.85) or a small sentence-transformer (`all-MiniLM-L6-v2`, 6 MB, local). Feed the signal into the Architect re-engagement logic.

### 5. Environment sanity check

Run `sudo true` before every attempt. If it fails, snapshot-restore and retry once; if that fails, halt the run. Forty-two sudo-failure reflections all trace back to a broken environment the loop couldn't recognize.

## What the run taught me

Two successful AIDE installs would have told me nothing. Twenty-six failures told me everything needed to build v3 of the loop.

The thing that surprised me most wasn't that the Worker had a hidden retry loop. It was that the instrumentation looked healthy while the most important behavior in the system was happening *outside* the instrumented boundary. The harness was lying to me through the event log. Not on purpose — it was faithfully reporting what it could see. It just couldn't see what mattered. If I had shipped v2 to a demo based on what the logs showed, I would have demoed a system where ninety percent of the Worker's behavior was invisible.

The Reflector was right from the first minute, on every rule that escalated. A smarter Reflector was not the fix. An Architect that could listen was.

The context bomb wasn't where I thought. It wasn't accumulated memory between turns. It was the tool-call history *inside* a single turn — a mechanism I didn't know was accumulating because I hadn't written the loop that was accumulating it.

Plateau detection saw 0% when the real rate was 60–80%. A false-negative confidence signal is worse than no signal at all, because it makes the loop look healthy while it grinds.

And sudo was broken for eight of ten hours, unnoticed. A production system that can't tell when its own environment is broken is one SSH session away from silent failure.

Failures teach. Successes don't. The overnight run fixed nothing. The next six commits, informed by the overnight run, fixed everything that mattered.

## Related

- [`journey/13`](13-ralph-persistence-retry-budget.md) — the time-budget change that enabled this run to expose these flaws
- [`improvements/01`](../improvements/01-architect-reengagement.md) — architect re-engagement, empirically validated by the partition rule
- [`improvements/02`](../improvements/02-worker-single-action-enforcement.md) — the Worker single-action enforcement fix
- [`improvements/03`](../improvements/03-context-budget-assembly.md) — the deterministic prompt budget
- Raw run log: `runs/run-20260411-013326.jsonl`
- Run duration: 10h 4m
