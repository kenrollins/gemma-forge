---
id: journey-07-skills-system
type: journey
title: "The Skills System: Making the Harness Worth More Than the Demo"
date: 2026-04-09
tags: [L4-orchestration, L5-application, refactor]
related:
  - journey/06-tool-calling
  - journey/20-the-interface-extraction
one_line: "The loop worked for STIG, but every variable said 'STIG.' Ken pushed for a skills architecture modeled on the industry pattern — pluggable, folder-per-skill, manifest-driven. The first version got us 80% there. The last 20% would take two more months and two overnight runs to find."
---

# The Skills System: Making the Harness Worth More Than the Demo

## The story in one sentence

We had a working STIG remediation loop, but the whole point of
GemmaForge was never STIG — it was the harness. If the harness
couldn't run a different skill without code changes, we didn't
have a platform. We had a script.

## The push

After the tool-calling refactor ([entry 06](06-tool-calling.md))
landed and the Ralph loop was genuinely working, there was a moment
to step back and ask: *what is this project actually proving?*

The answer couldn't be "we can harden a Linux box with an LLM."
That's a feature, not an architecture. The answer had to be: the
**harness pattern** — fail, revert, reflect, retry, learn across
runs — is general-purpose. STIG is the stress test. The harness
is the product.

But at that point, every line of code said STIG. The variable was
`stig_rules`. The function was `run_stig_scan`. The Architect's
system prompt opened with "You are remediating DISA STIG violations
on Rocky Linux 9." If someone asked "can this do CVE response?"
the honest answer was: not without rewriting half of it.

Skills are becoming a recognized pattern in the agent ecosystem.
Anthropic's MCP established tool-use protocols. LangChain and CrewAI
converged on pluggable task definitions. The idea that an agent
harness should separate *what it does* from *how it orchestrates*
has moved from novel to expected. GemmaForge needed to follow
that pattern — not because it was trendy, but because it was right.
A harness that can only run one demo is a prototype. A harness with
a skills interface is infrastructure.

## The design: folder-per-skill manifests

We designed around the emerging standards: each skill is a
self-contained directory with a YAML manifest and markdown prompts.

```
skills/stig-rhel9/
  skill.yaml              # name, description, tools, validators
  prompts/
    architect.md          # role prompts as plain markdown
    worker.md
    auditor.md
  validators/
    mission_app.yaml      # declarative health checks
  plugin.py               # optional Python escape hatch
```

A `SkillManifest` (Pydantic model) validates the manifest on load.
A `SkillLoader` discovers `skills/*/skill.yaml` at startup. The
loop reads prompts and tool assignments from the manifest instead
of hardcoded constants. `--skill stig-rhel9` on the command line
loads that skill. `--skill rotate-ssh-keys` loads a different one.
The harness code is identical — only the prompts and tool
assignments change.

The goal for the developer experience is simple:
`cp -r skills/stig-rhel9 skills/my-new-skill`, edit prompts, run.
No code changes for most scenarios. We captured that target in
`adding-a-skill.md` as the guide we're building toward.

## Thinking about what else this could do

To stress-test whether the manifest pattern was flexible enough,
we brainstormed other Federal-relevant scenarios that the harness
*could* tackle: CVE triage, service recovery, network failover,
log analysis, encrypted volume recovery. We saved these as stub
manifests in the repo — not implementations, just enough YAML to
see whether the skill.yaml schema could express them.

None of these are built. We've stayed focused on STIG as the first
skill because improving the harness architecture matters more right
now than breadth. But the exercise was useful: it confirmed the
manifest pattern is expressive enough for meaningfully different
use cases, and it gave us a backlog of ideas for when the harness
is ready for a second skill.

## What we knew was incomplete

The manifest system handled the 80% case: skills that differ in
prompts and tool selection. But we could already see the 20% it
couldn't reach. The SSH key rotation skill needed a different
*evaluator* — not `oscap` checking a STIG rule, but a script
verifying the old key is rejected and the new key authenticates.
The CVE response skill needed a different *work queue* — not
"scan for all failures" but "process this one CVE across affected
packages."

The manifest could swap *what the agents say*. It couldn't swap
*how the harness evaluates, reverts, or checkpoints*. Those
behavioral interfaces were still hardcoded in `ralph.py`, wearing
STIG-shaped assumptions.

We didn't try to solve that yet. The alternative was spending weeks
designing abstract interfaces from imagination, with no data about
what the right abstractions should be. Instead, we kept running the
system against STIG, collected failure data from real overnight
runs, and let the failures tell us where the skill boundary
actually needed to be.

That deeper extraction eventually happened in
[entry 20](20-the-interface-extraction.md). Two overnight runs
taught us exactly which decisions belong to the harness and which
belong to the skill — and the answer was deeper than prompts and
tool names. The manifest system became one layer of a more
complete architecture: five abstract protocols (WorkQueue, Executor,
Evaluator, Checkpoint, SkillRuntime) that a skill implements in
Python. That's where this story finishes. We weren't ready for it
yet.

---

## Related

- [`journey/06`](06-tool-calling.md) — the working loop that needed
  extraction.
- [`journey/20`](20-the-interface-extraction.md) — where the real
  interface boundary was drawn, two overnight runs later.
