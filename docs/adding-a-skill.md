# Adding a skill to GemmaForge

A **skill** is a self-contained demo scenario that the Ralph loop can
execute. Adding a new skill is a `cp -r` + edit prompts exercise — no
code changes required for most scenarios.

## Quick start

```bash
# 1. Copy the STIG skill as a template
cp -r skills/stig-rhel9 skills/my-new-skill

# 2. Edit the manifest
$EDITOR skills/my-new-skill/skill.yaml

# 3. Edit the agent prompts
$EDITOR skills/my-new-skill/prompts/architect.md
$EDITOR skills/my-new-skill/prompts/worker.md
$EDITOR skills/my-new-skill/prompts/auditor.md

# 4. Run it
python -m gemma_forge.harness.ralph --skill my-new-skill
```

## Skill directory layout

```
skills/my-new-skill/
├── skill.yaml              # Manifest (required)
├── prompts/
│   ├── architect.md        # Architect system prompt
│   ├── worker.md           # Worker system prompt
│   └── auditor.md          # Auditor system prompt
├── validators/
│   └── my_check.yaml       # Optional: declarative validators
└── plugin.py               # Optional: custom Python logic
```

## The manifest (`skill.yaml`)

```yaml
name: "My New Skill"
description: "What this skill does in one sentence."
version: "0.1.0"
target_os: "Rocky Linux 9"

# Which prompts to load (relative to skill directory)
prompts:
  architect: "prompts/architect.md"
  worker: "prompts/worker.md"
  auditor: "prompts/auditor.md"

# Which tools each agent gets (references TOOL_REGISTRY in ralph.py)
tools:
  architect:
    - run_stig_scan       # Available tools: run_stig_scan
  worker:
    - apply_fix           # Available tools: apply_fix
  auditor:
    - check_health        # Available tools: check_health, revert_last_fix
    - revert_last_fix

# Optional: STIG-specific config (only for STIG-type skills)
stig:
  profile: "xccdf_org.ssgproject.content_profile_stig"
  datastream: "/usr/share/xml/scap/ssg/content/ssg-rl9-ds.xml"

# Optional: validators that run after each fix
validators:
  - name: "my-check"
    command: "/usr/local/bin/my-healthcheck.sh"
    success_pattern: "OK"
    failure_pattern: "FAIL"
```

## Writing agent prompts

Prompts are plain markdown files. They tell each agent what to do and
which tools to use. Key principles:

1. **Name the tools explicitly.** The model needs to know what tools
   it has and when to call them.
2. **Say "use the tool" not "output text."** Tool-calling models
   follow instructions literally.
3. **Keep prompts concise.** They eat into the context window.
4. **Reference the conversation history.** The LoopAgent carries
   history between iterations, so agents can learn from failures.

See `skills/stig-rhel9/prompts/` for working examples.

## Available tools

Tools are registered in `gemma_forge/harness/ralph.py:TOOL_REGISTRY`.
Current tools:

| Tool name | What it does | Used by |
|---|---|---|
| `run_stig_scan` | Runs OpenSCAP STIG scan on the target VM | Architect |
| `apply_fix` | Applies a bash fix + stores revert script | Worker |
| `check_health` | Runs the mission-app healthcheck | Auditor |
| `revert_last_fix` | Reverts the most recently applied fix | Auditor |

To add a new tool, define it as an async function in
`gemma_forge/harness/tools/` and add it to `TOOL_REGISTRY`.

## The optional plugin

If your skill needs logic that can't be expressed as prompts + tools,
add a `plugin.py` to the skill directory. The plugin module is imported
at runtime and can override or extend the skill's behavior.

```python
# skills/my-new-skill/plugin.py
class SkillPlugin:
    """Optional plugin for custom logic."""

    def pre_loop_hook(self, ssh_config):
        """Called before the Ralph loop starts."""
        pass

    def post_loop_hook(self, records):
        """Called after the Ralph loop completes."""
        pass
```

## Verifying your skill

```bash
# List all discovered skills
python -c "from gemma_forge.skills.loader import discover_skills; [print(s.name) for s in discover_skills()]"

# Load and inspect a specific skill
python -c "from gemma_forge.skills.loader import load_skill; s = load_skill('my-new-skill'); print(s.manifest.model_dump_json(indent=2))"

# Run it
python -m gemma_forge.harness.ralph --skill my-new-skill
```
