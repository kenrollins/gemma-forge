---
id: journey-17-v3-fix-pass
type: journey
title: "The v3 Fix Pass: Five Architectural Changes in One Push"
date: 2026-04-11
tags: [L4-orchestration, reflexion-loop, tool-calling, context-management, snapshot-revert, refactor]
related:
  - journey/14-overnight-run-findings
  - journey/15-the-test-as-architecture-discovery
  - architecture/01-reflexive-agent-harness-failure-modes
  - improvements/01-architect-reengagement
  - improvements/02-worker-single-action-enforcement
  - improvements/03-context-budget-assembly
  - improvements/04-snapshot-based-revert
one_line: "After the overnight run revealed multiple architectural flaws, I implemented five fixes in sequence — Worker single-action enforcement, context budget assembler, semantic plateau detection, architect re-engagement, and snapshot-based revert with diagnostic capture — each one addressing a specific failure mode from the postmortem."
---

# The v3 Fix Pass: Five Architectural Changes in One Push

## The story in one sentence
Once the overnight run postmortem (see
[`journey/14`](14-overnight-run-findings.md)) had surfaced four
architectural flaws — and a fifth that emerged while fixing the first
four — I stopped, renamed the loop revision "v3," and implemented the
five changes in sequence, each one testable in isolation before moving
on to the next.

## Why this is its own entry

The individual fixes each have their own improvement document with
design and rationale. This entry is the *narrative* — the order I
worked in, the discoveries that came out of each fix, and the
interactions between them. It's the layer of the story that the
per-fix documents deliberately don't cover.

## The five fixes in the order I did them

### Fix #1: Worker single-action enforcement

**Problem**: the Worker agent was running internal tool-retry loops
inside a single agent turn — 15 consecutive `apply_fix` calls observed
in one turn from the overnight run. This bypassed the entire outer
reflexion loop and accumulated context inside the turn until the
prompt overflowed.

**Change**: two-layer fix.
1. **Prompt layer**: tell the Worker explicitly "call apply_fix
   EXACTLY ONCE and return a brief text summary of the outcome. Do
   NOT call it again even if it fails."
2. **Harness layer**: defensive cap in `_run_agent_turn()`. Count
   tool calls, intercept the second one, synthesize a closing text
   response, end the turn.

**Why the harness cap matters even with the prompt**: the prompt
alone is a 90%-effective fix. The harness cap is defense in depth —
it guarantees the invariant holds even if the LLM decides to ignore
the prompt. In any production context, "the model was told not to"
is not a safety argument.

**Verified with**: a live LLM smoke test. A Worker agent given a
loose prompt ("if it fails, try something different") tried to
retry on failure, and the harness cap fired at call #2 exactly as
designed. With the strict prompt, the Worker voluntarily stopped
after one call and the cap never needed to fire.

This was the highest-priority fix because **nothing else mattered
until it was in place**. If the Worker is silently running its own
retry loop inside a turn, then the outer reflexion loop's retry
logic, its plateau detection, and its architect re-engagement are
all bypassed. All the other fixes assume one action per turn. This
one had to be first.

See [`improvements/02-worker-single-action-enforcement`](../improvements/02-worker-single-action-enforcement.md).

### Fix #2: Context budget assembler

**Problem**: prompt assembly was ad-hoc string concatenation that
grew unboundedly as the run progressed. Episodic memory for a rule
grew per attempt. Semantic memory grew per ban added. The run state
summary for the Architect grew per remediated/escalated rule. No
single prompt component was catastrophically large; together they
could exceed 16k tokens.

**Change**: deterministic token-budget-aware assembly.
- `est_tokens(text)` — rough 4-chars-per-token estimate, good enough
  for budget decisions without needing a real tokenizer
- `assemble_prompt(sections, budget_tokens)` — takes priority-ordered
  sections, includes them in priority order, truncates the
  highest-priority section that doesn't fit, drops anything lower
- `EpisodicMemory.summary(max_attempts=N)` — replaced unbounded
  summary with a cap, preferring distilled per-attempt lessons over
  raw approach/result/reflection text
- **Distilled lessons**: Reflector output now includes a
  `DISTILLED:` field, a one-sentence summary of the attempt and
  what was learned. Episodic memory stores the distilled version;
  the raw reflection text stays in the event log for post-run
  analysis but doesn't pollute subsequent prompts.

**Side effect**: `summary_for_architect()` changed signature from
returning a string to returning `(text, meta)` where `meta` records
which sections were included, truncated, or dropped. This
instrumentation goes into `prompt_assembled` events in the run log.

See [`improvements/03-context-budget-assembly`](../improvements/03-context-budget-assembly.md).

### Fix #3: Semantic plateau detection

**Problem**: the plateau detector compared the first sentence of
consecutive reflections via literal string match. Across 226
reflections in the overnight run, it flagged **0%** as plateaued.
Manual inspection showed the real rate was more like 76%. The
reflections were cosmetically different ("structural disk
partitioning requirement" vs "structural disk partitioning
requirements") but semantically identical, and string equality can't
see that.

**Change**: keyword-set intersection instead of string equality.

```python
def _keyword_set(text: str) -> frozenset[str]:
    # normalize: lowercase, strip punctuation, drop stopwords,
    # strip trailing 's' for plural collapse, drop 1-2 char tokens
    ...

def detect_plateau(reflections, window=3, min_shared=3):
    sets = [_keyword_set(r) for r in reflections[-window:]]
    if any(len(s) < min_shared for s in sets): return False
    intersection = set(sets[0])
    for s in sets[1:]: intersection &= s
    return len(intersection) >= min_shared
```

**Validated with**: a replay of the overnight run's reflections.
The new detector flagged **76%** of rules with 3+ reflections as
plateaued — consistent with manual inspection. The partition rule
that repeated itself 20 times was flagged starting at reflection
number 3, exactly where you'd want.

This is the smallest fix of the five but one of the most
satisfying. A 20-line algorithm change turned a 0%-accurate
detector into a 76%-accurate one, just by asking the right question.

Noted for future: the keyword-set approach still has a calibration
gap — it doesn't collapse stems like `config` vs `configuration`.
For tighter accuracy, a small embedding model (e.g.,
`sentence-transformers/all-MiniLM-L6-v2`, 6 MB local) with cosine
similarity would be the next step. Deferred until there's evidence
the simpler version misses things it needs to catch.

### Fix #4: Architect re-engagement

**Problem**: the Reflector was producing accurate diagnoses
("this rule cannot be fixed at runtime, STOP trying") 20 times in
a row for the partition rule, and the Worker kept trying because
the Worker's instructions were scoped to "fix this rule with a
bash script," and there was no mechanism for the Reflector's
insights to escalate up to the strategy layer. The Architect was
never consulted between rule selection and rule completion, so
its initial strategy persisted unchanged even when the Reflector
knew it was wrong.

**Change**: periodic architect re-engagement during the inner
retry loop.

```yaml
loop:
  architect_reengage_every_n_attempts: 3
  architect_reengage_on_plateau: true
```

After every `N` failed attempts, OR whenever the plateau detector
fires (which is now accurate thanks to Fix #3), the Architect is
re-invoked in a new "re-engagement mode" with the full failure
history for the current rule. It returns one of three verdicts:

- **CONTINUE** — strategy is sound, keep grinding
- **PIVOT** — strategy is wrong, here is a different approach
- **ESCALATE** — this rule cannot be solved, preemptively give up

The verdict is parsed by a dedicated function
`parse_architect_verdict()` that handles many plausible LLM output
formats (plain, markdown-wrapped, lowercase, extra whitespace,
prefixed prose, reordered fields). If parsing fails, the default
is `CONTINUE` — the safer fallback, because giving up wrongly is
worse than grinding wrongly.

**Verified with**: exhaustive parser tests against hand-crafted
model output variations (all 24 pass). The full re-engagement loop
is exercised by the integration test in Tier 5 of the test suite.

See [`improvements/01-architect-reengagement`](../improvements/01-architect-reengagement.md).

### Fix #5: Snapshot-based revert with diagnostic capture

**Problem**: the original revert mechanism was script-based — the
Worker generated both a fix script and a revert script, and the
harness ran the revert via SSH+sudo when a fix failed. This
assumed (a) the revert script was correct, (b) sudo still worked
after the fix, and (c) the filesystem was still writable. None of
those assumptions held under adversarial conditions. In the
overnight run, a STIG fix modified `/etc/sudoers` to remove
`NOPASSWD`, passing the STIG rule check. The revert script needed
sudo to restore sudoers. Sudo was broken. The revert silently
failed. From that point on, every subsequent attempt saw broken
sudo and reported "privilege escalation deadlock" — 42 times
before the run was stopped.

**Change**: authoritative revert via libvirt snapshots, with
structured diagnostic capture *before* the revert.

1. **Snapshot lifecycle**: two snapshots, `baseline` (pristine
   initial state, never modified) and `progress` (rolling,
   advanced after each successful remediation). Failed attempts
   restore to `progress` if it exists, else `baseline`.

2. **Pre-revert diagnostics**: before restoring the snapshot, run
   `gather_environment_diagnostics()` which captures a structured
   forensic snapshot of the target:
   - Sudo probe (`sudo -n whoami`)
   - Service status for nginx, postgres, sshd, chronyd
   - Mission healthcheck result
   - Recent auth failures from journalctl
   - `/etc/sudoers` state (size, mtime, permissions)
   - `/etc/pam.d` state
   - Filesystem mount state
   - Recent errors from the journal

3. **Post-mortem event**: all of the above gets emitted as a
   structured `post_mortem` event in the run log. The Reflector
   now reasons from real forensics instead of just
   `"APPLY_FAILED"`.

4. **Snapshot restore**: via `virsh snapshot-revert`, at the
   hypervisor level, bypassing the guest's sudo/auth/filesystem
   entirely.

5. **Post-restore verification**: after the restore, a direct
   sudo probe confirms the target is recovered. If it isn't, an
   `environment_unrecoverable` event fires and the loop halts.

**The "why not scripts" point**: the fundamental issue with
script-based revert is that **the revert has to run on the guest
that the fix just broke**. If the fix can break sudo, the revert
needs sudo, and you lose. Any out-of-band channel (a hypervisor-
level snapshot, in this case) is strictly better because nothing
the agent does inside the guest can defeat it.

**A known limitation**: GemmaForge also has a virsh console
fallback for diagnostic gathering when SSH+sudo is broken, but
the current implementation of the console fallback has a bug
("Connection lost" during the virsh console subprocess
protocol). The diagnostic gather correctly detects that sudo is
broken regardless (the primary sudo probe returns false with
high confidence), and the snapshot restore works correctly
because it operates at the libvirt level. The console path
would give richer forensics when sudo is down, but it is not
currently load-bearing. Documented honestly in
[`architecture/01-reflexive-agent-harness-failure-modes`](../architecture/01-reflexive-agent-harness-failure-modes.md)
under "known limitations." A future fix would replace the
console protocol with the QEMU guest agent, which is cleaner.

See [`improvements/04-snapshot-based-revert`](../improvements/04-snapshot-based-revert.md).

## The interactions between fixes

The five fixes weren't independent. A few interactions worth
noting:

- **Fix #1 enables Fix #2.** If the Worker is running internal
  retries, then context-budget assembly inside the harness can't
  see the retries, and the prompt will still overflow. Fix #1
  had to land first or Fix #2 was pointless.
- **Fix #3 enables Fix #4.** Architect re-engagement fires on
  "every N attempts OR when plateau is detected." If plateau
  detection is broken (0% accuracy), the plateau trigger never
  fires and the re-engagement logic is half-dead. Fix #3 had to
  land before Fix #4 was fully useful.
- **Fix #5 is independent of the others.** Snapshot-based revert
  works the same whether the Worker is well-behaved or not,
  whether prompt assembly is budgeted or not, whether plateau
  detection is accurate or not. It's a pure infrastructure
  improvement that could have been done at any time. It just
  happened to fit naturally with the other four.

## The test discipline

All five fixes went through the same discipline:

1. Write the property being claimed ("agent turns are bounded in
   tool calls") as a test.
2. Watch it fail against the old code.
3. Implement the fix.
4. Watch it pass.
5. Add more tests for edge cases and related properties.
6. Move on to the next fix.

The test suite that came out of this process — 99 property tests
across 7 tiers, all passing — is itself a statement about what
"correct" means for this system. The tests *are* the
specification. See
[`journey/15-the-test-as-architecture-discovery`](15-the-test-as-architecture-discovery.md)
for the discipline, and the test files themselves
(`tests/test_*.py`) for the actual assertions.

## What the v3 loop looks like now

Stepping back, the v3 loop's structure is:

```
for each outer iteration:
    heartbeat event (run elapsed, memory tier sizes, rates)
    architect picks a rule (seeing full run state, within budget)
    rule_selected event

    rule_start_wall = now()
    for each inner attempt (unbounded, time-budget limited):
        if elapsed > time_budget: escalate (time_budget)
        if attempt > ceiling: escalate (retry_ceiling, safety only)
        attempt_start event

        worker assembles prompt from priority sections + budget
        prompt_assembled event
        worker makes exactly one tool call (capped)
        worker returns text

        evaluate_fix (deterministic, no LLM)
        if pass:
            save progress snapshot
            remediated event; break
        else:
            gather forensic diagnostics
            post_mortem event (structured)
            snapshot-restore to progress (or baseline)
            verify post-restore; halt if unrecoverable
            revert event

            reflector analyzes (with full episodic)
            parse DISTILLED, BANNED, PREFERRED, LESSON
            ban_added events as patterns accrete
            plateau check (semantic keyword intersection)
            reflection event

            if (attempts since arch touch >= N) or plateau:
                architect re-engages with full history
                parse verdict (CONTINUE/PIVOT/ESCALATE)
                architect_reengaged event
                if ESCALATE: break out of inner loop

    rule_complete event (rich, per-rule summary)
```

Each line is a property that has at least one test. Each new event
type has a consumer downstream (either the dashboard, the replay
logic, the postmortem analysis, or the architect re-engagement
decision). The whole loop is observable from the outside via the
structured event stream in `runs/run-*.jsonl`.

## What's still open

A few things explicitly deferred:

- **Semantic plateau detection v2** — the current keyword-set
  approach works at 76% accuracy against the overnight run. A
  proper sentence-transformer similarity would likely push that
  higher. Deferred until evidence shows the current version
  misses things in practice.
- **Console fallback rewrite** — the virsh console path is
  currently broken. Replacing it with the QEMU guest agent or a
  hypervisor-side sidecar would fix it. Deferred because the
  primary recovery path (snapshot restore) doesn't depend on it.
- **Architect re-engagement with a richer verdict vocabulary** —
  currently CONTINUE/PIVOT/ESCALATE. There's a case for adding a
  fourth verdict like "SKIP_UNTIL_DEPS" for cases where the
  Architect wants to defer a rule until other rules have been
  remediated first. Deferred until the dependency patterns are
  clear enough to name properly.

## Reading list

- [`journey/14-overnight-run-findings`](14-overnight-run-findings.md)
  — the run that produced the data these fixes address
- [`journey/15-the-test-as-architecture-discovery`](15-the-test-as-architecture-discovery.md)
  — the discipline that framed the fix pass
- [`architecture/01-reflexive-agent-harness-failure-modes`](../architecture/01-reflexive-agent-harness-failure-modes.md)
  — the generalized taxonomy that came out of the fix pass
- Each of the four improvement documents linked in the relation
  section at the top of this entry
