---
id: improvement-05-conversation-history-management
type: improvement
title: "Improvement #5: Conversation History Management"
date: 2026-04-12
tags: [L4-orchestration, context-management, reflexion-loop]
related:
  - journey/18-second-overnight-run
  - improvements/03-context-budget-assembly
  - architecture/01-reflexive-agent-harness-failure-modes
---

# Improvement #5: Conversation History Management

## Problem

On high-attempt rules (13+ attempts), accumulated tool call/result
pairs push past vLLM's 16K context limit, causing `PromptTooLongError`.
The prompt budget assembler (Improvement #3) controls instruction
tokens, but conversation history — SSH commands and their multi-line
output — grows unbounded within a rule attempt sequence.

8 errors in the second overnight run, all on rules at attempt 11+.
`install_smartcard_packages` hit 5 consecutive context errors in
5 minutes, making attempts 11–16 completely wasted.

## Root cause

Each worker turn produces a tool call (the SSH command) and a tool
result (the command output). Tool results from `ssh_apply` can be
50–200 tokens (command output, error messages, multi-line config
diffs). After 12 turns, the accumulated history can reach 3,000–5,000
tokens — enough to push the total prompt past 16K when combined with
the system instruction and the current turn.

## Proposed mechanism

**Sliding window with compression.** Before each worker turn:

1. Count total conversation tokens (system + history + current turn).
2. If under budget (e.g., 12K of 16K), proceed normally.
3. If over budget, compress the oldest turns:
   - Keep the **last 3 turns** verbatim (the model needs recent
     context to avoid repeating itself).
   - Replace earlier turns with a one-line summary:
     `"[Turn 4: ran 'sed -i ...' on /etc/aide.conf — failed, config
     parse error]"`
   - This is the same distillation pattern as episodic memory, but
     applied to within-rule conversation.

## What this is not

This is not about increasing `max_model_len` — that costs VRAM and
doesn't solve the underlying problem of unbounded history growth.
It's also not about the prompt assembler, which already works
correctly for its scope.

## Verification

- Inject a rule that requires 20+ attempts in a test.
- Confirm no `PromptTooLongError` after turn 15.
- Confirm the model can still avoid banned patterns (it has access
  to the ban list in the instruction, not in conversation history).
