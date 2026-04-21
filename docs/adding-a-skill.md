---
title: Adding a Skill
---

# Adding a Skill to gemma-forge

A **skill** is a self-contained package that teaches the Ralph loop
harness how to do a new kind of work. Two skills ship today:

- [`skills/stig-rhel9/`](https://github.com/kenrollins/gemma-forge/tree/main/skills/stig-rhel9) — the anchor skill. DISA STIG compliance
  on Rocky Linux 9 via OpenSCAP scan + bash fix scripts. The **hard
  case**: 270 rules, multi-attempt fixes, genuine reflexion cycles.
- [`skills/cve-response/`](https://github.com/kenrollins/gemma-forge/tree/main/skills/cve-response) — the second skill. Autonomous CVE
  advisory remediation via Vuls scan + `dnf upgrade --advisory=<ID>`,
  with per-package-family reboot batching. The **easy case**: 44
  advisories remediated in 35 min, every one first-try.

Adding a third skill is a folder, a manifest, a few prompt files, and
a `runtime.py` that implements five Protocol interfaces. No changes
to the Ralph loop. The extension points below are how you bend the
harness around your skill's shape without touching its core.

## Quick start

```bash
# 1. Copy either shipped skill as a template
cp -r skills/stig-rhel9 skills/my-new-skill
#   or: cp -r skills/cve-response skills/my-new-skill

# 2. Edit the manifest
$EDITOR skills/my-new-skill/skill.yaml

# 3. Edit the agent prompts
$EDITOR skills/my-new-skill/prompts/architect.md
$EDITOR skills/my-new-skill/prompts/worker.md
$EDITOR skills/my-new-skill/prompts/auditor.md
# (optionally prompts/reflector.md — falls back to the harness default)

# 4. Implement the five interfaces in runtime.py
$EDITOR skills/my-new-skill/runtime.py

# 5. Register the skill directory mapping in
#    gemma_forge/skills/loader.py if your folder name differs from
#    the short key you'll pass on the command line

# 6. Run it
./bin/forge run my-new-skill --config config/your-config.yaml
```

## Skill directory layout

```
skills/my-new-skill/
├── skill.yaml              # Manifest (required)
├── runtime.py              # Five Protocol interfaces (required)
├── prompts/
│   ├── architect.md        # Architect system prompt
│   ├── worker.md           # Worker system prompt
│   ├── auditor.md          # Evaluator-facing prompt
│   └── reflector.md        # (Optional) — harness default applies otherwise
└── validators/             # (Optional) — declarative health checks
    └── my-check.yaml
```

## The manifest (`skill.yaml`)

The manifest declares identity, prompts, tool lists, validators, and
optional ordering constraints. Both shipped skills use the same shape:

```yaml
name: "My New Skill"
description: "One-line summary of what this skill autonomously does."
version: "0.1.0"
target_os: "Rocky Linux 9"

prompts:
  architect: "prompts/architect.md"
  worker: "prompts/worker.md"
  auditor: "prompts/auditor.md"
  # reflector: "prompts/reflector.md"  # optional

# The skill's runtime owns its tools via get_agent_tools()
# and get_scan_tool(). These names are the human-readable labels
# the manifest loader expects; the actual function objects come
# from runtime.py.
tools:
  architect:
    - run_my_scan
  worker:
    - apply_my_fix
  auditor:
    - check_health
    - revert_last_fix

# Optional: declarative health checks the Evaluator can consult
validators:
  - name: "mission-app-health"
    command: "/usr/local/bin/mission-healthcheck.sh"
    success_pattern: "HEALTHY"
    failure_pattern: "UNHEALTHY"

# Optional: ordering constraints that hold specific work items
# back from the candidate pool until a predicate fires. STIG uses
# this to defer `audit_rules_immutable` until its category is
# nearly complete. CVE declares `deferrable_reboot` on reboot-
# required advisories.
ordering_constraints:
  - rule_id: "some_rule_that_must_run_last"
    defer_until:
      predicate: category_nearly_complete
```

## The five Protocol interfaces

The harness operates on five abstract interfaces defined in
[`gemma_forge/harness/interfaces.py`](https://github.com/kenrollins/gemma-forge/blob/main/gemma_forge/harness/interfaces.py).
Your `runtime.py` implements all five and bundles them in a
`SkillRuntime`:

| Interface | Purpose | STIG | CVE |
|---|---|---|---|
| `WorkQueue` | Produce the initial work items | OpenSCAP scan | Vuls scan |
| `Executor` | Apply a fix / change to the target | SSH + bash | SSH + `dnf upgrade --advisory` |
| `Evaluator` | Decide if the change succeeded | OpenSCAP + health | `dnf updateinfo` + health |
| `Checkpoint` | Save / restore target state | libvirt snapshots | libvirt snapshots |
| `SkillRuntime` | Bundle the above for the harness | `StigSkillRuntime` | `CveSkillRuntime` |

Each is a `typing.Protocol` with `@runtime_checkable` — you don't
inherit from a base class, you just define the methods. The harness
duck-types your `SkillRuntime` and uses it for everything.

### Example: WorkQueue

```python
class MyWorkQueue:
    async def scan(self) -> list[WorkItem]:
        """Scan the target and return every work item to process.

        WorkItem carries id, title, category, metadata, resources,
        and depends_on. Use metadata for skill-specific fields the
        Architect + Worker + Evaluator need to reason about.
        """
        items = await my_scan_tool(self._target)
        return [
            WorkItem(
                id=i.id,
                title=i.title,
                category=i.category,
                metadata={"severity": i.severity, "requires_reboot": i.reboot},
            )
            for i in items
        ]
```

### Example: Executor

```python
class MyExecutor:
    async def apply(self, item: WorkItem, fix_script: str,
                    revert_script: str, description: str) -> str:
        """Apply the fix. Return a Worker-facing log string."""
        result = await my_apply_tool(self._target, fix_script)
        return f"applied {item.id}: exit={result.exit_code}"

    def get_agent_tools(self) -> list:
        """Return the ADK tool functions the Worker can call."""
        return [apply_my_fix]
```

### Example: Evaluator (with metadata declaring deferrable modes)

```python
class MyEvaluator:
    metadata = EvaluatorMetadata(
        signal_type="binary",          # or "graded" / "judgment" / "behavioral"
        expected_confidence="high",
        cost_per_evaluation="cheap",
        # V2 memory curation policy
        min_retrievals_before_eviction=3,
        eviction_threshold=0.3,
        # Opt into deferred-verification for specific failure modes.
        # Empty list (default) means every failure escalates normally;
        # STIG declares []. CVE declares ["needs_reboot"].
        deferrable_failure_modes=["needs_external_event"],
    )

    async def evaluate(self, item: WorkItem) -> EvalResult:
        ok, summary, signals = await my_eval_tool(self._target, item)
        if not ok and signals.get("needs_reboot"):
            return EvalResult(
                passed=False,
                failure_mode=FailureMode.NEEDS_REBOOT,  # or a skill-added mode
                summary=summary,
                signals=signals,
            )
        return EvalResult(passed=ok, summary=summary, signals=signals)

    def signal_for(self, result: EvalResult) -> OutcomeSignal:
        return outcome_signal_from_eval_result(result, confidence=1.0)
```

### Example: Checkpoint (if you need anything beyond libvirt)

```python
class MyCheckpoint:
    async def exists(self, name: str) -> bool: ...
    async def save(self, name: str) -> tuple[bool, str]: ...
    async def restore(self, name: str) -> tuple[bool, str]: ...
    async def delete(self, name: str) -> tuple[bool, str]: ...
```

The shipped skills delegate to libvirt via
`gemma_forge/harness/tools/ssh.py`'s `_run_snapshot_cmd`. If your
target isn't a libvirt VM, this is where you plug in git refs,
database transactions, AWS CloudFormation, etc.

### Example: SkillRuntime

```python
class MySkillRuntime:
    def __init__(self, ...):
        self._work_queue = MyWorkQueue(...)
        self._executor   = MyExecutor(...)
        self._evaluator  = MyEvaluator(...)
        self._checkpoint = MyCheckpoint(...)

    @property
    def work_queue(self) -> WorkQueue: return self._work_queue
    @property
    def executor(self)   -> Executor:  return self._executor
    @property
    def evaluator(self)  -> Evaluator: return self._evaluator
    @property
    def checkpoint(self) -> Checkpoint: return self._checkpoint

    def get_scan_tool(self):
        """The ADK tool the Architect calls to scan the target."""
        return run_my_scan
```

## Optional: deferred verification

If your skill has failure modes that can't be verified in the
moment — a reboot, a certificate propagation wait, a service
restart — declare them in `deferrable_failure_modes` and implement
`resolve_deferred`:

```python
async def resolve_deferred(
    self,
    reason: str,
    items: list,
    emit: Optional[EmitEvent] = None,
) -> tuple[bool, str, list[DeferredItemOutcome]]:
    """Run the skill's resolution mechanics and return per-item outcomes.

    The harness calls this once per deferral reason after the main
    loop drains. Return a DeferredItemOutcome for every item you
    were given. Items with passed=True go to remediated; items with
    passed=False go to escalated with reason=outcome.reason.

    No re-evaluation happens on the harness side — your outcome IS
    the verdict.
    """
    emit = emit or (lambda _e, _d: None)

    emit("my_resolve_start", {"item_count": len(items)})
    # ... do the thing (reboot, wait, restart, etc.) ...
    emit("my_resolve_complete", {"passed": n_passed})

    return (True, summary_str, [
        DeferredItemOutcome(
            rule_id=item.id,
            passed=verified,
            reason="my_verified" if verified else "my_still_failing",
            metadata={"wall_time_s": t},
        )
        for item in items
    ])
```

See CVE's `resolve_deferred` in
[`skills/cve-response/runtime.py`](https://github.com/kenrollins/gemma-forge/blob/main/skills/cve-response/runtime.py)
for a full implementation with per-family batching, snapshot
rollback per family, and `emit` events for every phase boundary.

The harness's post-loop phase in
[`gemma_forge/harness/ralph.py`](https://github.com/kenrollins/gemma-forge/blob/main/gemma_forge/harness/ralph.py)
handles routing your per-item outcomes into remediated vs.
escalated — you don't need to touch it.

## Optional: adding a new FailureMode

If your skill has a failure shape the existing enum doesn't cover,
add a value to `FailureMode` in `gemma_forge/harness/interfaces.py`
and document the harness-level response. CVE added three:
`NEEDS_REBOOT`, `RPM_CONFLICT`, and `POLICY_VIOLATION`. See
[entry 33](journal/journey/33-second-skill-cve-pivot.md) and
[entry 37](journal/journey/37-per-family-reboot-batching-landed.md)
for the discussion of when this was the right call.

The convention: the enum value documents the *response* the harness
should take, not the domain-specific symptom. `NEEDS_REBOOT` is a
harness concept meaning "defer via `resolve_deferred`," not a
kernel-specific one. A crypto-rotation skill could reuse it for
"DNS propagation pending."

## Optional: validators

Declarative health checks listed in `validators:` get exposed to
your Evaluator via a utility. The STIG skill uses
`mission-app-health` to decide whether a fix broke the application
even though OpenSCAP says the rule passed.

## What NOT to do

- **Don't modify `gemma_forge/harness/ralph.py` to hardcode your
  skill's behavior.** The harness is skill-agnostic by design. If
  you find yourself wanting to add an `if skill_name == "foo"`, stop
  and either add an extension point (new Protocol method, new enum
  value) or move the behavior into your skill's `runtime.py`.
- **Don't take ownership of memory-store keys.** The V2 memory
  system operates on your items' `id` and `category` automatically.
  Writing custom keys into the DB from your skill defeats the
  cross-skill analytics.
- **Don't add tools to a shared registry.** Tools live on your
  skill's `Executor.get_agent_tools()` and
  `SkillRuntime.get_scan_tool()`. They're yours; no other skill
  sees them.

## Verifying your skill

```bash
# Quick manifest + runtime load check
./bin/forge run my-new-skill --config config/minimal-smoke.yaml --dry-run

# A minimal run against a short work queue
./bin/forge run my-new-skill --config config/harness-smoke-reboot.yaml

# Full run
./bin/forge run my-new-skill --config config/ralph-main.yaml
```

## Further reading

- [Architecture Brief](brief.md) — the one-doc overview including
  the skill-agnostic thesis and current extension points.
- [Architecture Overview](journal/architecture/00-system-architecture.md) —
  the 5-layer map, with skill-authoring called out as the L5
  pattern.
- [Failure Modes in Reflexive Agent Harnesses](journal/architecture/01-reflexive-agent-harness-failure-modes.md) —
  the project-agnostic taxonomy your skill's Evaluator will be
  classifying against.
- [Entry 33 — The Second Skill: CVE Response](journal/journey/33-second-skill-cve-pivot.md) — the
  pivot decision, including which ATLANTIS patterns were adopted
  vs. deliberately skipped.
- [Entry 35 — Building the CVE Skill in a Day](journal/journey/35-building-cve-in-a-day.md) — the build-log
  for how the second skill actually landed, including the three
  harness extension points it added.
- [Entry 37 — Per-Family Reboot Batching Lands](journal/journey/37-per-family-reboot-batching-landed.md) — the full
  `resolve_deferred` + `DeferredItemOutcome` + `EmitEvent` design in
  production.
