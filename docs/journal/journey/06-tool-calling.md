---
id: journey-06-tool-calling
type: journey
title: "Tool Calling and the Ralph Loop — From Script to Agent"
date: 2026-04-09
tags: [L4-orchestration, tool-calling, refactor]
related:
  - gotchas/adk-future-annotations
  - gotchas/agent-instructions-tool-calling
  - gotchas/vllm-tool-call-parser
one_line: "I built a working STIG remediation loop, realized it was a script pretending to be an agent, tore it apart, and rebuilt it with real tool calling — discovering along the way that Gemma 4 function calling through vLLM is production-capable on edge hardware."
---

# Tool Calling and the Ralph Loop — From Script to Agent

I built a working STIG remediation loop, realized it was a script
pretending to be an agent, tore it apart, and rebuilt it with real
tool calling — discovering along the way that Gemma 4's function
calling through vLLM is genuinely production-capable on edge hardware.

## The wrong version came first

The first implementation (loop.py) worked. It scanned, fixed, audited,
and reverted. Three successful fixes, two reverts, mission app
protected. But there was a question that changed everything: does this
fit the overall strategy of staying as close to a real implementation
as possible? Is a custom harness actually better than the ADK
LoopAgent?

The honest answer was no. Here's what the first version actually did:

```python
# The "decision" to revert — Python, not an agent
if "APPLY_FAILED" in apply_result or "UNHEALTHY" in health:
    revert_result = await ssh_revert(config.vm)
```

That's automation, not autonomy. Any bash script with `if` and `ssh`
can do that. The models were generating text that looked like
decisions, but Python was making the actual decisions.

The Ralph loop story — "agents that fail, reason through failures,
and persist" — requires the AGENTS to reason, not the orchestrator.

## The architecture shift

The correct architecture uses ADK's LoopAgent with real tool calling:

```
LoopAgent("ralph_loop")
  ├── Architect (31B NVFP4)     tools: [run_stig_scan]
  │     Model calls the tool, reads results, reasons about which
  │     rule to pick. Sees previous failures in conversation history.
  │
  ├── Worker (31B NVFP4)        tools: [apply_fix]
  │     Model generates fix+revert scripts and calls the tool itself.
  │     Not "output scripts as text" — structured tool call.
  │
  └── Auditor (E4B)             tools: [check_health, revert_last_fix]
        Model calls healthcheck, reasons about the result, and
        DECIDES whether to call revert. The reasoning is visible.
```

The conversation history carries between iterations. When the Auditor
says "AUDIT_FAIL — nginx crashed because the SSH config change broke
port forwarding," the Architect SEES that reasoning on its next turn
and adapts. Not because a Python dict tracked it — because the model
reads the conversation.

## The tool calling discovery chain

Getting this to work required solving four problems in sequence:

### Problem 1: vLLM doesn't enable tool calling by default

First attempt:
```
Error: "auto" tool choice requires --enable-auto-tool-choice
and --tool-call-parser to be set
```

vLLM needs explicit flags to enable function calling, and each model
family has its own parser for the tool-call format. vLLM 0.19.0 ships
a dedicated `gemma4` parser:

```
--enable-auto-tool-choice --tool-call-parser gemma4
```

The available parsers in vLLM 0.19.0 include: gemma4, llama4_pythonic,
mistral, hermes, qwen3coder, granite, deepseekv3, and many others.
Each handles the model's specific tool-call output format.

### Problem 2: Tool schema extraction from ADK FunctionTool

The VllmLlm adapter needs to convert ADK's tool schemas to OpenAI
function definitions. The adapter's first version tried
`tool.get_function_declaration()` which doesn't exist in this ADK
version. Fixed by extracting schemas from the function's type
annotations and docstrings directly.

### Problem 3: `from __future__ import annotations` breaks ADK

This was the most subtle bug. ADK's `_parse_schema_from_parameter`
checks `_is_builtin_primitive_or_compound(param.annotation)`. With
PEP 563 (`from __future__ import annotations`), type annotations
become lazy STRINGS — `'str'` instead of `str`. ADK's check fails
because it expects the actual type object.

The function parsed fine in isolation (test scripts don't import the
future annotation). But inside the LoopAgent, the module's import
applied, and the parser failed. Debugging this required reading ADK's
source code line by line to find the exact check at line 13 of
`function_parameter_parse_util.py`.

Fix: remove `from __future__ import annotations` from any module that
defines ADK tool functions. Added a comment explaining why.

### Problem 4: STIG scan output blew the context window

The full STIG scan output was 71,000 characters (~20K+ tokens). With
max_model_len=4096, the conversation (system prompt + user message +
tool call + tool result) exceeded the limit on the second turn.

Fix: truncated the scan output to the first 20 failing rules with IDs
and titles (~2K chars). The LLM doesn't need 270 rules to pick one —
it needs a representative sample with enough context to make a good
choice.

Also increased max_model_len to 8192 for the Architect and Auditor
models. On the L4 with NVFP4, KV cache headroom is minimal (642 MiB
free per GPU), but 8192 tokens is achievable with FP8 KV cache
compression.

## The moment it worked

The successful run showed exactly the behavior the architecture was
designed for:

1. **Architect** calls `run_stig_scan`, gets 270 failing rules,
   reasons: "I will start with a low-risk package installation,"
   selects `package_aide_installed`

2. **Worker** calls `apply_fix(fix_script='dnf install -y aide',
   revert_script='dnf remove -y aide', description='Install AIDE')`
   — a real structured tool call, not text

3. **Auditor** calls `check_health`, gets "HEALTHY", responds
   "AUDIT_PASS. Installation successful, mission app remains healthy."

4. **Architect** calls `run_stig_scan` again — sees 269 rules now
   (one fixed!), picks `aide_build_database`

5. **Worker** calls `apply_fix(fix_script='sudo aideinit', ...)`
   — **APPLY_FAILED**: "aideinit: command not found"

6. **Worker REASONS about the failure**: "On Rocky Linux 9, the AIDE
   database is typically initialized using `aide --init`."

7. **Architect reads the failure** and adapts: "I will adjust the
   approach to use the standard `aide` binary"

8. **Worker** calls `apply_fix(fix_script='aide --init && mv ...', ...)`
   — **APPLIED** ✓

The Ralph moment was steps 5-8. The Worker failed, reasoned about WHY
(`aideinit` doesn't exist on Rocky 9), and the Architect adapted its
approach based on the Worker's reasoning in the conversation history.
No Python if/else — the models did the thinking.

## What I learned about Gemma 4's tool calling

1. **It works.** Both the 31B NVFP4 and E4B models generate proper
   structured `tool_calls` through vLLM's gemma4 parser. The models
   understand when to call tools, what arguments to pass, and how to
   reason about tool results.

2. **The instructions matter enormously.** The first set of
   instructions told the Worker to "output scripts as text." The
   models did exactly that — text output, not tool calls. Rewriting
   the instructions to say "call the apply_fix tool" changed the
   behavior completely. The model follows instructions about HOW
   to interact, not just WHAT to do.

3. **The quality is Architect-grade.** The 31B NVFP4 correctly:
   - Selected safe rules first (package installs before kernel changes)
   - Generated proper `dnf` commands (not `yum`, not `apt`)
   - Created backup commands before modifications
   - Adapted when a fix failed (aideinit → aide --init)
   - Reasoned about WHY the fix failed, not just THAT it failed

4. **Context management is the constraint on L4.** With 642 MiB free
   per GPU for KV cache, multi-turn conversations hit limits fast.
   Compact tool outputs and reasonable max_model_len (8192) are
   essential. This is a real edge-hardware constraint, not a bug.

## The bigger picture

The framing thesis: a Ralph loop that powers through a problem by
spending tokens liberally can reach the same result as a larger
state-of-the-art model. Prove that story at the edge and the whole
premise lands.

The Phase 3 run proved exactly that. A 31B model quantized to NVFP4,
running on 2× L4 GPUs without NVLink, using real structured tool
calling through vLLM's gemma4 parser, orchestrated by Google ADK's
LoopAgent — autonomously scanning a Rocky Linux 9 system for STIG
violations, generating fixes, applying them via SSH, verifying mission
app health, adapting when fixes fail, and persisting until the rules
are remediated.

Not a chatbot. Not a script with LLM-generated text in the middle.
An autonomous agent system with real tool use, real failure recovery,
and real reasoning — at the tactical edge.

## Key artifacts

- `gemma_forge/harness/ralph.py` — ADK LoopAgent with 3 sub-agents
- `gemma_forge/harness/agents.py` — tool-calling-aware instructions
- `gemma_forge/harness/tools/` — SSH, OpenSCAP, healthcheck tools
- `gemma_forge/models/vllm_llm.py` — ADK BaseLlm adapter for vLLM
- `gemma_forge/harness/loop.py` — original Python-orchestrated version
  (kept for reference and as a fallback)
- `infra/vllm/scripts/serve.sh` — updated with tool-call flags
- `docs/whitepaper/gotchas/adk-future-annotations.md`
- ADR-0002: Google ADK LoopAgent (now actually honored)
