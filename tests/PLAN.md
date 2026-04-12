# Test Plan — Reflexive Harness Property Verification

**Status:** Plan complete, execution pending
**Source:** Conversation with Ken on 2026-04-11 after the v3 fixes pass
**Companion docs:**
- `docs/whitepaper/journey/14-overnight-run-findings.md` — empirical evidence
- `docs/whitepaper/journey/15-the-test-as-architecture-discovery.md` — discipline
- `docs/whitepaper/architecture/01-reflexive-agent-harness-failure-modes.md` — abstractions

## Discipline (read first)

**Tests assert properties of the harness, not absence of specific bugs.**

Test names are statements like `test_property_agent_turns_are_bounded_in_actions`,
not actions like `test_worker_doesnt_retry_apply_fix`.

**Checkpoint after each tier.** At the end of each tier, we ask:

> Did the failures we observed point to specific bugs, or to a missing
> abstraction?

If bugs: fix and continue. If missing abstraction: **stop, refactor, then
continue.** Do not power through to a green test suite if the architecture
is wrong.

## File header convention

Every test file starts with:

```python
"""
Tests for harness property: <STATEMENT>

Why: <one paragraph explaining the empirical motivation, with citation
to the journey doc>

This is a property of the harness, not of any specific agent or skill.
"""
```

## Test name convention

```python
def test_property_<short_statement_in_snake_case>():
    """<full statement, falsifiable, not action-oriented>"""
```

Avoid:
- `test_worker_cap_works`  # too vague
- `test_apply_fix_doesnt_retry`  # bound to specific tool
- `test_fix_1`  # bound to a specific bug
- `test_overnight_partition_rule`  # bound to a specific failure case

Prefer:
- `test_property_agent_turn_caps_at_max_tool_calls`
- `test_property_excess_tool_call_terminates_turn_with_synthetic_response`
- `test_property_assembler_output_within_budget_for_all_inputs`
- `test_property_plateau_detector_distinguishes_semantic_sameness`

---

## Tier 1 — Pure helper property tests (fast, no I/O)

**File:** `tests/test_harness_helpers.py`
**Estimated runtime:** < 5 seconds total
**Dependencies:** none (pure Python)

### Properties to test

1. **`assemble_prompt` output never exceeds budget.**
   - Empty sections list → empty output, used_tokens=0
   - Single section under budget → fits unchanged
   - Single section over budget → truncated, output ≤ budget
   - Multiple sections, all fit → all included
   - Multiple sections, some don't fit → priority-ordered drop, lowest-priority dropped first
   - Multiple sections, none fit → header truncated, rest dropped
   - **Invariant**: `est_tokens(output) <= budget_tokens` for ALL inputs

2. **`est_tokens` is monotonic and non-negative.**
   - Empty string → 0
   - Single char → 1
   - Adding chars never decreases the estimate
   - Always returns int ≥ 0

3. **`detect_plateau` distinguishes semantic sameness from semantic difference.**
   - Three reflections about partition rules in different words → True
   - Three reflections about three completely different topics → False
   - Two reflections only (window of 3 not met) → False
   - Empty reflection list → False
   - Three reflections with one shared keyword and many different ones → False
   - Boundary case: exactly 3 shared keywords → True
   - Three identical strings → True

4. **`_keyword_set` is robust to whitespace, case, and punctuation.**
   - Same content with different formatting → same set
   - "The structural disk partitioning requirement" and "structural disk
     partitioning REQUIREMENTS." → same content keywords (after plural strip)
   - Stopwords filtered out
   - Two-letter tokens filtered out

5. **`is_similar` is symmetric.**
   - `is_similar(a, b)` == `is_similar(b, a)` for all a, b

6. **`EpisodicMemory.summary()` output is bounded.**
   - 100 attempts → output is no larger than for 5 attempts (capped at last 5)
   - 0 attempts → returns "No prior attempts."
   - With and without distilled lessons

7. **`RunState.summary_for_architect()` respects token budget.**
   - 1000 failing rules + 100 escalated + 50 remediated → output within budget
   - Returns tuple `(text, meta)` with sections_dropped populated when over budget
   - Always preserves the header section regardless of budget

8. **`categorize_rule` covers known categories.**
   - Each STIG family resolves to expected category
   - Unknown rule → "other"
   - Empty string → "other"
   - The order of checks matters (e.g., aide_*  → integrity-monitoring even
     though it contains "package_aide_*")

9. **`reflection_first_sentence` extracts the pattern claim.**
   - Markdown-wrapped reflection → unwrapped first sentence
   - Reflection without "Pattern identified:" marker → falls back to first
     non-empty line
   - Empty input → empty output

### Property NOT yet enforced — flag for redesign

If a test cannot be written cleanly because the abstraction is missing
or wrong, **add it here** instead of writing a hacky test. Examples we
might find:

- "Action-bounding is a property of agents, but it's currently a
  parameter of `_run_agent_turn` — should be a property of an
  `Agent` wrapper class"
- "Token budget is a property of prompt assembly, but the budget
  number is hard-coded in many places — should be a property of an
  `AgentBudget` config object"

(none yet)

---

## Tier 2 — Verdict parser property tests (synthetic LLM responses)

**File:** `tests/test_architect_verdict_parsing.py`
**Estimated runtime:** < 1 second total
**Dependencies:** none — uses hand-crafted strings

The architect re-engagement parser is the most fragile bit of the v3
fixes because we never tested it against real model output. Tier 2
exhaustively tests the parser against every plausible model output
format BEFORE we hand it real model output in Tier 4.

### Properties to test

1. **Verdict parser handles every plausible format.**
   Test inputs:
   - Clean: `"VERDICT: ESCALATE\nREASONING: ..."`
   - Markdown wrapper: ` "```\nVERDICT: ESCALATE\n```" `
   - Lowercase: `"verdict: escalate"`
   - Extra whitespace: `"  VERDICT:    ESCALATE  "`
   - Reordered: `"REASONING: ...\nVERDICT: ESCALATE\nNEW_PLAN: ..."`
   - Inline: `"My VERDICT: ESCALATE because ..."`
   - Repeated: two `VERDICT:` lines, take the first
   - Missing NEW_PLAN: should still extract verdict
   - Mis-keyed: `"FINAL VERDICT: ESCALATE"` (we currently won't match this — flag if so)
   - With explanatory prefix: `"After analysis, VERDICT: PIVOT"`

2. **Verdict parser falls back to CONTINUE on unparseable input.**
   - Empty string → CONTINUE (default — keep grinding is the safer fallback)
   - Pure prose with no VERDICT marker → CONTINUE
   - Garbage → CONTINUE

3. **NEW_PLAN extraction is bounded.**
   - Long NEW_PLAN → truncated to a reasonable size (define what)
   - Multi-line NEW_PLAN → only the first line is captured (or all of it?
     decide based on what the assembler can fit)
   - Missing NEW_PLAN with verdict CONTINUE → empty plan, log a warning

### Currently the verdict parsing is inline in `ralph.py` (not a function).

**Refactor decision before testing**: extract `parse_architect_verdict(text) -> dict`
into a module-level function in `ralph.py` so it can be unit tested. This
is a small refactor but it's the right shape — verdict parsing is a
property of the harness, not of the inner loop body.

---

## Tier 3 — Target-layer property tests (VM only, no LLM)

**File:** `tests/test_target_layer.py`
**Estimated runtime:** 1-3 minutes (real VM operations)
**Dependencies:** running VM, libvirt, valid SSH key

### Properties to test

1. **`gather_environment_diagnostics` correctly identifies HEALTHY state.**
   - On baseline VM → sudo_ok=True, services_ok=True, mission_healthy=True
   - All sections present in output
   - No exceptions raised

2. **`gather_environment_diagnostics` correctly identifies BROKEN state — multiple distinct breakages.**
   This is the critical test that the overnight run pointed at. We need
   the diagnostic capture to identify environmental failure regardless
   of which specific thing broke.

   Test scenarios (each performed via direct SSH-as-root setup, then probed):
   - Break A: stop nginx → `services_ok=False`, service_status mentions inactive nginx
   - Break B: stop postgres → `services_ok=False`, service_status mentions inactive postgres
   - Break C: introduce sudoers password requirement → `sudo_ok=False`
   - Break D: chmod 000 /etc/sudoers → sudoers_state shows the broken permissions
   - Break E: fill /tmp until write fails → fs_state shows /tmp not writable
   - Break F: combination of A+C (multiple broken at once) → both flags False

   For each, verify the right boolean flag is False AND the relevant section
   contains a meaningful diagnostic string.

3. **`snapshot_save_progress` followed by `snapshot_restore_progress` is idempotent on healthy state.**
   - Start at baseline
   - Save progress
   - Verify VM unchanged (run probe, should still be healthy)
   - Restore progress
   - Verify VM still healthy
   - Cleanup

4. **`snapshot_restore_progress` recovers from each break in test #2.**
   For each break A-F:
   - Verify break is detected (flag = False)
   - Restore from progress (or baseline)
   - Verify post-restore probe shows healthy state
   - This is the **authoritative recovery** property

5. **`progress` snapshot preserves accumulated changes.**
   - Start at baseline
   - Apply harmless change A (e.g., create /etc/forge_test_a)
   - Save progress
   - Apply harmless change B (e.g., create /etc/forge_test_b)
   - Restore from progress
   - Verify A still exists, B does not
   - Cleanup

6. **Diagnostic capture works via virsh console fallback when SSH is broken.**
   - Snapshot the VM, then break SSH from inside (e.g., `systemctl stop sshd`)
   - Run `gather_environment_diagnostics`
   - Expect: SSH fails → falls back to virsh console → still gets some sections populated
   - Restore snapshot, restore SSH
   - This is the **out-of-band channel** property

### Property NOT yet enforced — flag for redesign

(filled in during execution)

---

## Tier 4 — Agent-behavior property tests (real LLM, no VM)

**File:** `tests/test_agent_behavior.py`
**Estimated runtime:** 2-5 minutes (real LLM calls)
**Dependencies:** vLLM running on :8050

### Properties to test

1. **Agent turns cap at max_tool_calls regardless of which tool.**
   - Build a synthetic agent with a dummy `echo_tool` that always returns "FAIL"
   - Loose prompt that encourages retry
   - Verify cap fires at call #2
   - Verify the synthetic response is returned
   - **Bonus**: repeat with a different tool function — verify the cap is
     not bound to apply_fix specifically

2. **Agent with zero tools runs to text response.**
   - Agent with empty tools list, no cap interaction
   - Just verify the path completes cleanly

3. **`max_tool_calls=0` blocks all tool calls.**
   - Agent that wants to call a tool, max_tool_calls=0
   - Cap fires immediately on first call
   - Synthetic response returned

4. **Voluntary stop with strict prompt — cap never fires.**
   - Agent with strict "EXACTLY ONCE" prompt
   - Verify the LLM voluntarily stops after one call
   - Verify cap counter never exceeded

5. **Reflector produces DISTILLED field with new prompt.**
   - Build a Reflector agent with the production prompt
   - Send a synthetic episodic history (failed attempts on a fake rule)
   - Verify the response contains a `DISTILLED:` line
   - Verify our parser extracts a non-empty distilled lesson
   - This is the property: **Reflector outputs are parseable for distilled lessons**

---

## Tier 5 — Inner loop integration on one rule (real everything)

**File:** `tests/test_loop_integration.py`
**Estimated runtime:** 5-15 minutes (real LLM + VM, one rule from scan to completion)
**Dependencies:** vLLM, VM, baseline snapshot

This is the test that proves the new code paths actually compose correctly.
Run ralph against ONE rule and verify the full event sequence.

### Properties to test

1. **The full event sequence fires for a remediation.**
   - Pick an "easy" rule expected to succeed (e.g., `package_aide_installed`)
   - Verify in order: `snapshot_preflight → scan_complete → iteration_start →
     architect agent_response → rule_selected (with category) → attempt_start
     (with rule_elapsed_s) → worker agent_response (tool_calls=1) → tool_call →
     tool_result → evaluation (passed=True) → remediated (snapshot_saved=True) →
     rule_complete (outcome=remediated, attempts=1, architect_reengagements=0)`
   - Each event has the expected fields
   - Worker turn has `tool_calls=1, capped=False`
   - Snapshot save advances the progress snapshot
   - Kill the run after rule_complete

2. **The full event sequence fires for an escalation with re-engagement.**
   - Pick a hard rule expected to fail (e.g., `partition_for_var_log_audit`)
   - Verify in order: ...attempts go through cycle... `post_mortem` (with
     populated diagnostics) → `revert (method=snapshot_restore)` → `reflection
     (with DISTILLED, plateaued=False initially)` → ... eventually `architect_reengaged
     (verdict=ESCALATE)` → `escalated (reason=architect_preemptive)` →
     `rule_complete (outcome=escalated, escalation_reason=architect_preemptive,
     architect_reengagements>=1)`
   - Verify the architect verdict is parseable (real model output → real parser path)
   - Verify the snapshot_restore actually returned the VM to clean state between
     attempts (post-restore probe is healthy)

3. **A failed attempt followed by a successful retry advances progress correctly.**
   - This may not happen naturally; consider a synthetic case if needed.
   - The property: progress snapshot reflects only successful remediations,
     never failed ones.

---

## Tier 6 — Fault injection (mostly synthetic)

**File:** `tests/test_harness_fault_paths.py`
**Estimated runtime:** 1-3 minutes
**Dependencies:** varies

### Properties to test

1. **Run start fails cleanly when baseline snapshot is missing.**
   - Mock or temporarily rename the baseline snapshot
   - Verify run_ralph raises a clear, instructive error
   - Verify no LLM tokens are spent
   - Restore the baseline

2. **Run start fails cleanly when vLLM is unreachable.**
   - Temporarily point models.yaml endpoint at a dead port
   - Verify graceful failure
   - Restore config

3. **Run start fails cleanly when VM is unreachable.**
   - Stop the VM (or use a config pointing at a dead IP)
   - Verify graceful failure with helpful message
   - Restart VM if needed

4. **`gather_environment_diagnostics` exception is non-fatal.**
   - Mock the SSH layer to raise
   - Verify the inner loop catches it, emits a tool_error event, and uses
     `{"sudo_ok": False, ...}` defaults so the post_mortem still emits

5. **`snapshot_restore_progress` exception is non-fatal but emits `tool_error`.**
   - Mock the snapshot helper to fail
   - Verify the loop continues but the failure is logged

6. **`environment_unrecoverable` event fires when post-restore sudo probe fails.**
   - Tricky to test cleanly without breaking the VM. Skip for now? Or use
     a mocked test only.

---

## Tier 7 — Frontend smoke (no LLM, just rendering)

**File:** none — this is a manual visual check via Playwright
**Estimated runtime:** 2 minutes

### What to verify

1. Point the dashboard at the Tier 5 integration run log
2. Verify it renders without crashing (no console errors)
3. Verify the new event types appear in the event log (they may use the
   generic fallback rendering, which is fine for now)
4. Screenshot for the journal

This isn't a "property" test in the same sense — it's a "we didn't break
anything" check.

---

## Execution order

1. **Tier 1** (helpers, fast) — start here. Catches the most bugs per second.
2. **Checkpoint #1** — review failures, decide bugs vs missing abstractions.
3. **Tier 2** (verdict parsing) — uses synthetic inputs, very fast. May
   require extracting `parse_architect_verdict` into a function first
   (acceptable refactor — small, well-scoped).
4. **Checkpoint #2**.
5. **Tier 3** (VM target layer) — slower but isolated from LLM.
6. **Checkpoint #3** — this is where we'll likely find the most surprises.
7. **Tier 4** (agent behavior with real LLM, no VM) — exercises the LLM-prompt
   contract independently of the VM.
8. **Checkpoint #4**.
9. **Tier 5** (full integration) — the moment of truth.
10. **Checkpoint #5**.
11. **Tier 6** (fault injection) — defensive testing.
12. **Tier 7** (frontend smoke).
13. **Final checkpoint**: write up findings, update the failure-modes doc
    with anything new, decide whether v4 is needed before the next
    overnight run.

## Outputs of this test pass

When we're done:
- A test suite that reads as a specification (`tests/test_*.py`)
- An updated harness that demonstrably has the abstract properties
- New entries in the failure-modes doc for any modes we discovered
- A journey entry summarizing what testing taught us
- Either confidence to run another overnight, or a clear v4 spec
