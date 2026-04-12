---
id: journey-07-skills-system
type: journey
title: "Journey: Skills System — Making Demos a Folder Drop"
date: 2026-04-09
tags: [L4-orchestration, L5-application, refactor]
related:
  - journey/06-tool-calling
one_line: "We extracted the hardcoded STIG remediation logic into a pluggable skill manifest so adding a new use case is a folder copy and a prompt edit, not a code change."
---

# Journey: Skills System — Making Demos a Folder Drop

## The story in one sentence
We extracted the hardcoded STIG remediation logic into a pluggable
skill manifest so adding a new demo is `cp -r` + edit prompts, not
a code change.

## What was hardcoded

After Phase 3, the Ralph loop worked but everything was wired together:
- Agent system prompts lived in `agents.py` as Python constants
- Tool assignments were hardcoded in `build_ralph_loop()`
- The STIG profile and datastream were config values, not skill metadata
- Adding a second demo would mean either forking the loop or adding
  a pile of if/else branches

## The design

Hybrid: folder-per-skill manifest as the default, with an optional
Python plugin escape hatch for custom logic.

```
skills/stig-rhel9/
  skill.yaml              # name, description, tools, validators, stig config
  prompts/
    architect.md          # role-specific system prompts (plain markdown)
    worker.md
    auditor.md
  validators/
    mission_app.yaml      # declarative healthcheck config
  plugin.py               # OPTIONAL — custom Python if needed
```

The `SkillManifest` (pydantic model) validates the manifest on load.
The `SkillLoader` discovers `skills/*/skill.yaml` at startup. The
`TOOL_REGISTRY` maps tool names in the manifest to actual Python
functions.

## The extensibility proof

Created `skills/rotate-ssh-keys/` as a stub second skill. It has:
- Its own `skill.yaml` with different tool assignments
- Its own prompts (shorter, task-specific)
- No `plugin.py` (declarative-only)

Running `discover_skills()` finds both skills without code changes.
Running `--skill rotate-ssh-keys` would load the rotation prompts
instead of the STIG prompts. The harness code is identical — only
the prompts and tool assignments change.

## What we learned

1. **Manifest-first is the right default.** Most skills differ only
   in prompts and tool selection. A YAML manifest + markdown prompts
   covers 90% of cases.

2. **The plugin escape hatch is insurance.** We haven't needed it yet,
   but when a skill needs custom pre/post-loop logic (e.g., setting up
   a test environment), the plugin.py path is there.

3. **Tool names as strings in the manifest → resolved at runtime.**
   This decouples the skill definition from the Python code. The
   `TOOL_REGISTRY` is the bridge.

4. **The refactor was clean.** Zero behavioral changes to the Ralph
   loop — same scan, same fix, same audit, same revert. Just loaded
   from different files.

## Key artifacts

- `gemma_forge/skills/base.py` — SkillManifest, Skill class
- `gemma_forge/skills/loader.py` — discover_skills(), load_skill()
- `skills/stig-rhel9/` — the working STIG skill
- `skills/rotate-ssh-keys/` — stub proving extensibility
- `docs/adding-a-skill.md` — practitioner guide
- `gemma_forge/harness/ralph.py` — updated with --skill arg and TOOL_REGISTRY
