---
id: journey-18-second-overnight-run
type: journey
title: "The Second Overnight Run: 93 Rules, and What the Other 26 Teach"
date: 2026-04-12
tags: [L4-orchestration, reflexion-loop, context-management, snapshot-revert, postmortem]
related:
  - journey/14-overnight-run-findings
  - journey/17-v3-fix-pass
  - architecture/01-reflexive-agent-harness-failure-modes
one_line: "v3 ran for 9.5 hours and remediated 93 of 120 STIG rules autonomously — a 78% fix rate, up from 7% in the first overnight run — but 69% of wall time was spent on the 26 rules that ultimately escalated, revealing three architectural patterns worth addressing."
---

# The Second Overnight Run: 93 Rules, and What the Other 26 Teach

## The story in one sentence

The v3 harness ran 9.5 hours on the same STIG workload that v2 barely
dented, autonomously remediating 93 of 120 rules (78%), but the 26
escalated rules consumed 69% of wall time — and the patterns in those
failures point to three harness-level architectural improvements that
aren't about STIG at all.

## Why this is its own entry

The first overnight run
([`journey/14`](14-overnight-run-findings.md)) found the flaws that
produced the v3 fix pass. This second run validates that those fixes
worked — and then reveals the *next layer* of architectural questions.
The pattern of "ship, observe, analyze, improve" is the Ralph loop
applied to the harness itself.

---

## What the numbers say

### The scoreboard

| Metric | v2 Run | v3 Run | Delta |
|--------|--------|--------|-------|
| Duration | 10h | 9.5h | — |
| Rules attempted | 28 | 120 | +330% |
| Remediated | 2 (7%) | 93 (78%) | +4,550% |
| Escalated | 26 (93%) | 26 (22%) | −71pp |
| Throughput | 2.8/hr | 12.5/hr | +346% |

The v3 fixes didn't just improve the fix rate — they changed the
character of the run. v2 was a harness that mostly failed. v3 is a
harness that mostly succeeds and fails *informatively* on the rest.

### The efficiency story

**First-try success rate: 79%.** 74 of 94 remediations needed exactly
one attempt. The model knows how to fix most STIG rules when given a
clean prompt and the right tools. Median time-to-remediation: 34
seconds.

The distribution has a long tail: after the 74 one-shot fixes, there's
a cliff to 5 rules at 2 attempts, then a smattering of hard cases out
to 19 attempts. The hard-but-eventually-successful rules include:

- `file_permission_user_init_files_root` — 19 attempts, 1159s
- `rsyslog_encrypt_offload_defaultnetstreamdriver` — 17 attempts, 1199s
- `rsyslog_remote_access_monitoring` — 13 attempts, 1004s
- `rsyslog_encrypt_offload_actionsendstreamdrivermode` — 12 attempts, 864s

These four barely scraped in under the time budget. They represent
"eventually correct" rules where the model needed many pivots to find
the right incantation.

### The time-waste ratio

This is the headline finding:

| | Rules | Wall Time | Time/Rule |
|---|---|---|---|
| Remediated | 94 (78%) | 2.7h (31%) | 34s median |
| Escalated | 26 (22%) | 6.1h (69%) | 959s median |

**69% of the run was spent on rules that ultimately failed.** The
harness is fast when it works and slow when it doesn't, and it doesn't
know the difference early enough.

### Category performance

| Category | Fix Rate | Avg Time | Notes |
|----------|----------|----------|-------|
| authentication | 100% | 46s | PAM faillock, passwords — the sweet spot |
| service-config | 100% | 17s | Trivial one-liners |
| cryptography | 100% | 21s | Package installs + config |
| kernel | 89% | 94s | sysctl params; 4 failures are impossible at runtime |
| package-management | 88% | 132s | Mostly `dnf install` |
| logging | 73% | 509s | rsyslog config is hard |
| filesystem | 71% | 181s | Permissions mostly fine; partitioning impossible |
| integrity-monitoring | 29% | 824s | AIDE dependency chain |
| user-account | 14% | 1009s | Scanner semantic gap |
| banner | 0% | 1209s | Scanner semantic gap |

---

## What the v3 fixes did

Each of the five v3 fixes is visible in the data:

1. **Worker single-action enforcement** — 0 tool-call-cap events. The
   prompt-driven approach worked without the hard cap ever firing. The
   model is voluntarily constraining itself to one tool call per turn.

2. **Context budget assembler** — No sections were ever truncated or
   dropped. Maximum utilization was 54% for rule selection. The budgets
   are generous enough.

3. **Semantic plateau detection** — Not directly visible (0 explicit
   plateau events), but the architect is escalating based on pattern
   recognition before the time budget runs out — 19 of 26 escalations
   were `architect_preemptive` rather than `time_budget`.

4. **Architect re-engagement** — 181 re-engagements across the run.
   The architect is actively managing the loop: 89% PIVOTs, 10.5%
   ESCALATEs, 0.5% CONTINUEs. ESCALATE accuracy is 100%.

5. **Snapshot-based revert + diagnostics** — 430 reverts, all
   cleanly executed. 430 post-mortems, each with structured diagnostic
   capture. The revert-on-failure mechanism is the backbone of the loop.

---

## Three architectural findings

### Finding 1: Conversation history overflow

**The problem fixed at the prompt level exists at the conversation
level.**

On high-attempt rules (13+ attempts), accumulated tool call/result
pairs push the vLLM context past the 16K token limit. 8 errors, all
identical: `PromptTooLongError`. The prompt budget assembler controls
the *instruction* portion, but the *conversation history* — SSH
commands and their multi-line output — grows unbounded within a rule.

This is the same class of problem as the episodic memory distillation,
applied to the within-rule conversation. The harness needs a sliding
window or summarization mechanism: keep the last N turns verbatim,
compress earlier turns to a one-line summary.

**Impact**: Only 8 errors (low), but those errors hit rules that were
already hard cases, making them harder. More importantly, this is a
ticking bomb — any rule that reaches 15+ attempts will hit it.

### Finding 2: Evaluation should triage, not just pass/fail

The current evaluation logic is binary: either the rule passes and
the mission app is healthy, or we revert. The data reveals three
distinct failure modes that should drive *different responses*:

**Mode A — Health failure (2.3% of reverts):** The fix broke
something. Revert immediately. This is correct today.

**Mode B — Scanner gap (88% of reverts):** Health is fine, but the
scanner says the rule still fails. The model writes technically
correct config that the scanner doesn't recognize. After 3+ clean
attempts with different approaches that all pass health but fail the
scanner, the harness should recognize this as a *knowledge gap* and
escalate early rather than grinding to 15+ attempts.

**Mode C — False-negative revert (2.1% of reverts):** The rule
*actually passed* but journal noise (warnings, non-fatal errors)
caused the harness to revert a working fix. 9 reverts threw away good
work. The harness then had to re-discover the same fix on a later
attempt.

Journal noise on a passing rule check should not trigger a revert.
A passing scanner result should be authoritative.

**Impact**: Early scanner-gap detection alone would save ~4 hours of
wall time. Eliminating false-negative reverts would save the 9 wasted
re-discovery cycles.

### Finding 3: Rule dependency awareness

Five AIDE rules all depend on having a working AIDE database. The
architect treats them as independent, so each one independently
discovers and fails on the same prerequisite. That's 5 × ~1000s =
83 minutes wasted on a problem that should have been solved once.

The architect doesn't need a full dependency graph. Even a simple
heuristic — "if 2+ rules fail for the same root cause mentioned in
their post-mortems, try the most fundamental one first" — would
capture this pattern.

**Impact**: Not large in absolute time (83 minutes), but large in
*principle*. Any skill with prerequisite chains will hit this same
pattern. It's a harness-level concern, not a STIG concern.

---

## What the v3 fixes *didn't* fix (and shouldn't)

Five rules are **architecturally impossible** at runtime:
`partition_for_var` (disk layout), `grub2_disable_interactive_boot`
(read-only boot partition), `sysctl_kernel_kexec_load_disabled`
(kernel compile-time), `kernel_module_atm_disabled` (compiled into
kernel), `installed_OS_is_vendor_supported` (vendor subscription).
The architect correctly identifies these in 2–5 attempts and
escalates preemptively. This is the right behavior — fast fail on
impossible tasks.

---

## The Ralph loop observation

The harness applied the Ralph loop to 120 STIG rules. I'm now
applying the Ralph loop to the harness itself:

1. **Fail** — v2 ran overnight and remediated 2 rules.
2. **Diagnose** — the overnight postmortem found 5 architectural flaws.
3. **Fix** — v3 implemented all 5 fixes.
4. **Observe** — v3 ran overnight and remediated 93 rules.
5. **Diagnose** — the analysis above found 3 more architectural patterns.
6. **Next** — v4 will address conversation management, evaluation
   triage, and dependency awareness.

This is the meta-pattern. The same persistence-and-reflection
discipline that makes the harness work on STIG rules also makes the
harness itself improvable in the same cadence.

---

## Related

- [`journey/14`](14-overnight-run-findings.md) — the first overnight
  run postmortem that produced the v3 fix pass.
- [`journey/17`](17-v3-fix-pass.md) — the five fixes, in sequence.
- [`architecture/01`](../architecture/01-reflexive-agent-harness-failure-modes.md) —
  the failure-mode taxonomy. Finding 1 is a new instance of FM-3
  (context overflow). Finding 2 extends FM-1 (misdiagnosis). Finding 3
  is a new failure mode: prerequisite blindness.
