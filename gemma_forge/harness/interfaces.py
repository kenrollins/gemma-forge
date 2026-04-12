"""Harness/skill interface boundary.

The Ralph loop harness operates on five abstract concerns. Each skill
implements these interfaces for its specific domain. This separation
means the harness code (loop logic, evaluation triage, task graph,
parallelism, conversation management) is skill-agnostic.

The five concerns:
  1. WorkQueue — produces work items from an initial scan/analysis
  2. Executor — applies a fix/change to the target
  3. Evaluator — determines whether the change succeeded
  4. Checkpoint — saves/restores target state for safe revert
  5. WorkItem — a single unit of work with identity and metadata

Skills implement these via a SkillRuntime that bundles all five.

Design notes:
  - Protocols, not ABCs — duck typing is fine, no registration needed
  - All methods are async — even if some implementations are sync
  - The harness never imports skill-specific modules directly
  - Evaluator returns a structured EvalResult, not a bool — this
    enables evaluation triage in the harness layer
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable


class FailureMode(Enum):
    """Harness-level failure classification for evaluation triage.

    The harness uses these to decide the response — not the skill.
    A skill's Evaluator maps its domain-specific signals to these
    categories. The harness then routes:
      HEALTH_FAILURE → immediate revert
      EVALUATOR_GAP  → count toward early escalation
      FALSE_NEGATIVE → accept the change (override the noise)
      CLEAN_FAILURE  → normal revert + reflect cycle
    """
    HEALTH_FAILURE = "health_failure"   # target is broken
    EVALUATOR_GAP = "evaluator_gap"     # target healthy but evaluator says fail
    FALSE_NEGATIVE = "false_negative"   # evaluator says pass but noise caused revert
    CLEAN_FAILURE = "clean_failure"     # normal failure, safe to retry


@dataclass
class EvalResult:
    """Structured evaluation result from a skill's Evaluator.

    The harness reads `passed` and `failure_mode` to decide what to do.
    The skill populates `signals` with domain-specific detail that the
    harness logs but doesn't interpret.
    """
    passed: bool
    failure_mode: FailureMode = FailureMode.CLEAN_FAILURE
    summary: str = ""
    signals: dict = field(default_factory=dict)  # domain-specific detail


@dataclass
class WorkItem:
    """A single unit of work that the harness processes.

    Skills produce these from their WorkQueue. The harness manages
    their lifecycle (queued → active → completed/escalated).
    """
    id: str
    title: str
    category: str = "uncategorized"
    metadata: dict = field(default_factory=dict)
    # Resources this item touches — used for conflict detection
    # in parallel execution (e.g., ["/etc/ssh/sshd_config", "sshd.service"])
    resources: list[str] = field(default_factory=list)
    # Declared dependencies — item IDs that must complete before this one
    depends_on: list[str] = field(default_factory=list)


@runtime_checkable
class WorkQueue(Protocol):
    """Produces the initial set of work items from a scan/analysis."""

    async def scan(self) -> list[WorkItem]:
        """Scan the target and return all work items to process."""
        ...


@runtime_checkable
class Executor(Protocol):
    """Applies changes to the target system."""

    async def apply(self, item: WorkItem, fix_script: str,
                    revert_script: str, description: str) -> str:
        """Apply a fix. Returns the execution output."""
        ...

    def get_agent_tools(self) -> list:
        """Return the ADK-compatible tool functions for agent turns.

        These are the functions the Worker agent can call. The harness
        wires them into the Worker's tool list.
        """
        ...


@runtime_checkable
class Evaluator(Protocol):
    """Determines whether a change succeeded."""

    async def evaluate(self, item: WorkItem) -> EvalResult:
        """Evaluate the current state of the target for this work item.

        Must return an EvalResult with:
          - passed: did the item's objective succeed?
          - failure_mode: harness-level classification
          - signals: domain-specific detail for logging
        """
        ...


@runtime_checkable
class Checkpoint(Protocol):
    """Saves and restores target state for safe revert."""

    async def exists(self, name: str) -> bool:
        """Check if a named checkpoint exists."""
        ...

    async def save(self, name: str) -> tuple[bool, str]:
        """Save a checkpoint. Returns (success, detail)."""
        ...

    async def restore(self, name: str) -> tuple[bool, str]:
        """Restore to a checkpoint. Returns (success, detail)."""
        ...

    async def delete(self, name: str) -> tuple[bool, str]:
        """Delete a checkpoint. Returns (success, detail)."""
        ...


@runtime_checkable
class SkillRuntime(Protocol):
    """Bundles all five interfaces for a skill.

    The harness receives a SkillRuntime and uses it to drive the loop
    without knowing the skill's domain.
    """

    @property
    def work_queue(self) -> WorkQueue: ...

    @property
    def executor(self) -> Executor: ...

    @property
    def evaluator(self) -> Evaluator: ...

    @property
    def checkpoint(self) -> Checkpoint: ...

    def get_scan_tool(self):
        """Return the ADK tool function for the Architect's scan capability."""
        ...
