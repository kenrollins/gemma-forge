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
from typing import Literal, Protocol, runtime_checkable


# -- Skill-agnostic outcome signal -------------------------------------------
#
# Phase E of the V2 memory architecture. The harness historically had two
# outcome notions: a boolean ``EvalResult.passed`` and the ``failure_mode``
# enum used for triage. That collapse threw away gradations the memory
# system needed (an attempt that escalates after 1 try is a worse outcome
# than one that escalates after 5; a graded test-suite skill has no clean
# pass/fail at all).
#
# ``OutcomeSignal`` is the unit the memory system reads. ``EvalResult``
# stays as the harness-level triage object — both flow out of the same
# Evaluator. See docs/drafts/v2-architecture-plan.md §2.2.


SignalType = Literal["binary", "graded", "judgment", "behavioral"]
ConfidenceTier = Literal["high", "medium", "low"]
EvaluationCost = Literal["cheap", "moderate", "expensive"]


@dataclass(frozen=True)
class OutcomeSignal:
    """Skill-agnostic graded outcome for memory utility tracking.

    ``value`` is the success score in [0, 1] (binary skills emit 0.0 or 1.0).
    ``confidence`` is how trustworthy the value is, also in [0, 1] (binary
    deterministic skills like STIG emit 1.0; an LLM judge emits something
    lower). ``utility_contribution = value * confidence`` is the per-retrieval
    weight a tip accrues toward its running utility average; it generalizes
    cleanly across binary, graded, judgment, and behavioral signals.
    """
    value: float
    confidence: float
    metadata: dict = field(default_factory=dict)

    @property
    def utility_contribution(self) -> float:
        return self.value * self.confidence


@dataclass(frozen=True)
class EvaluatorMetadata:
    """Skill-declared characteristics of its outcome signal.

    These parameterize the V2 memory curation policy (eviction threshold,
    retrieval-count gate, same-run damping). A binary-deterministic skill
    can fire eviction after a few retrievals at a low threshold; a graded
    or judgment-based skill needs more samples and a higher floor.
    """
    signal_type: SignalType
    expected_confidence: ConfidenceTier
    cost_per_evaluation: EvaluationCost
    # Curation-policy knobs (defaults are conservative — graded/judgment
    # shape; binary skills override to lower values in their declaration).
    min_retrievals_before_eviction: int = 10
    eviction_threshold: float = 0.5
    # Deferred-verification modes. If a skill's Evaluator returns a
    # FailureMode whose .value is in this list, the harness defers the
    # item instead of escalating — no revert, no reflect. After the
    # main loop, the harness calls runtime.resolve_deferred() grouped
    # by reason, then re-evaluates each item. Empty list (default)
    # means no deferred modes — every failure escalates normally.
    # CVE declares ["needs_reboot"]; STIG declares [].
    deferrable_failure_modes: list[str] = field(default_factory=list)


def outcome_signal_from_eval_result(
    result: "EvalResult", *, confidence: float = 1.0,
) -> OutcomeSignal:
    """Default projection used by binary deterministic evaluators.

    Maps ``passed`` → ``value`` (1.0 / 0.0) and forwards the failure-mode
    plus signals into ``metadata``. Skills with graded outcomes (test
    coverage, LLM-judge scores) override their ``signal_for`` instead of
    using this helper.
    """
    return OutcomeSignal(
        value=1.0 if result.passed else 0.0,
        confidence=confidence,
        metadata={"failure_mode": result.failure_mode.value, **result.signals},
    )


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
    # CVE-skill additions (entry 33). Harness routing:
    #   NEEDS_REBOOT      → partial success; defer via deferrable_reboot ordering
    #   RPM_CONFLICT      → clean failure with a specific diagnostic angle
    #   POLICY_VIOLATION  → immediate revert + ban the approach (e.g., dnf remove as "fix")
    NEEDS_REBOOT = "needs_reboot"
    RPM_CONFLICT = "rpm_conflict"
    POLICY_VIOLATION = "policy_violation"


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
    """Determines whether a change succeeded.

    Two outputs flow from a single evaluation:

      ``evaluate(item) -> EvalResult`` for harness-level triage
      (success/failure-mode routing).

      ``signal_for(result) -> OutcomeSignal`` for the memory system's
      utility math (value × confidence). Binary deterministic skills
      can delegate to ``outcome_signal_from_eval_result``; graded /
      judgment skills override.

    ``metadata`` declares the skill's signal characteristics so the
    eviction/retrieval policy in Phase H can parameterize correctly
    without hardcoding STIG assumptions.
    """

    metadata: EvaluatorMetadata

    async def evaluate(self, item: WorkItem) -> EvalResult:
        """Evaluate the current state of the target for this work item.

        Must return an EvalResult with:
          - passed: did the item's objective succeed?
          - failure_mode: harness-level classification
          - signals: domain-specific detail for logging
        """
        ...

    def signal_for(self, result: EvalResult) -> OutcomeSignal:
        """Project an EvalResult into the graded OutcomeSignal."""
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

    async def resolve_deferred(
        self,
        reason: str,
        items: list,
    ) -> tuple[bool, str]:
        """Resolve a deferred-verification condition for a batch of items.

        Called by the harness's post-loop phase when items were deferred
        (not escalated) because their FailureMode was in the skill's
        ``deferrable_failure_modes``. The skill owns the resolution
        mechanics — e.g., rebooting a VM and waiting for SSH (CVE),
        restarting a service (network-reconfig), waiting for propagation
        (crypto-recovery).

        Args:
            reason: The FailureMode.value that caused deferral
                (e.g., ``"needs_reboot"``).
            items: The deferred WorkItems to resolve as a batch.

        Returns:
            (success, detail): whether the resolution action succeeded
            (e.g., VM rebooted and came back healthy) and a human-
            readable detail string for logging.

        Default: no-op returning ``(True, "no deferred items")``.
        Skills that declare ``deferrable_failure_modes`` MUST override.
        """
        return (True, "no deferred items — skill has no resolve_deferred implementation")
