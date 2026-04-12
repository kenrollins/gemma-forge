---
id: gotcha-agent-instructions-tool-calling
type: gotcha
title: "Gotcha: Agent instructions must explicitly say "use the tool" for tool calling"
date: 2026-04-09
tags: [L4-orchestration, tool-calling, discovery]
related:
  - journey/06-tool-calling
  - improvements/02-worker-single-action-enforcement
one_line: "Agent system prompts must explicitly tell the model to call its tools; without that instruction, the model describes what it would do in prose and never actually invokes the tool."
---

# Gotcha: Agent instructions must explicitly say "use the tool" for tool calling

## Symptom
Agent outputs text describing what it would do (e.g., "FIX_SCRIPT:
dnf install aide") instead of making a structured tool call.

## Root cause
Instructions written for text-generation mode ("output the scripts")
don't translate to tool-calling mode. The model follows instructions
literally: if you say "output a bash script," it outputs text. If you
say "call the apply_fix tool," it makes a structured tool call.

## Example

**Before (text generation):**
```
You are a Worker. Generate a fix script and a revert script.
Respond ONLY with the two scripts.
```
→ Model outputs: "FIX_SCRIPT: ```bash\ndnf install aide\n```"

**After (tool calling):**
```
You are a Worker. Call the apply_fix tool with three arguments:
fix_script, revert_script, and description.
Do not output scripts as text — use the tool.
```
→ Model makes: `tool_calls: [{function: apply_fix, arguments: {...}}]`

## Key principles for tool-calling instructions

1. **Name the tools explicitly.** "Use the check_health tool" not
   "check if the system is healthy."

2. **Describe the expected flow.** "First call check_health, then
   if UNHEALTHY, call revert_last_fix."

3. **Say what NOT to do.** "Do not output scripts as text — use the
   tool." Models follow negative instructions too.

4. **List available tools with their signatures.** The model has the
   tool schemas from the API, but reinforcing them in the instruction
   improves reliability.

5. **Keep instructions concise.** Long instructions eat into the
   context window (see context-window-edge.md).

## Environment
- Gemma 4 31B-IT NVFP4 and E4B-IT
- Google ADK Agent with FunctionTool
- vLLM with --tool-call-parser gemma4
