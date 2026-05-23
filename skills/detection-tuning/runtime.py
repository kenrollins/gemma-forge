# NOTE: Do NOT add `from __future__ import annotations` to this module.
# ADK's FunctionTool parser requires real type objects, not lazy strings.
# (Same constraint as skills/stig-rhel9/runtime.py — copy the pattern.)

"""detection-tuning skill runtime — implements the five harness interfaces.

Friday-night vertical slice:
  - WorkQueue yields ONE hardcoded item (the LSASS memdump rule)
  - Executor writes Worker-supplied YAML to the per-item working file
  - Evaluator runs the working rule against the labeled corpus subset
    scoped to the rule's logsource; returns P/R/F1 in EvalResult.signals
  - Checkpoint snapshots the working-file text in-memory
  - SkillRuntime bundles them and implements DEF-28 worker_context() with
    the current rule YAML + a handful of positive sample events so the
    Worker sees what it's supposed to catch.

Out of scope tonight (Saturday morning):
  - WorkQueue.scan() against the full SigmaHQ rule library
  - Real prompts (these are placeholders)
  - Wiring into ralph._build_skill_runtime (touches harness; deferred so
    we don't risk the active Run 10's loop reload behavior)
  - Promoting RULE_TOO_NOISY/RULE_TOO_NARROW/RULE_PARSE_FAILURE/CORPUS_GAP
    to FailureMode enum values (currently signaled via signals[...])
"""

import json
import logging
import shutil
from pathlib import Path

import yaml

from gemma_forge.harness.interfaces import (
    Checkpoint,
    EvalResult,
    Evaluator,
    EvaluatorMetadata,
    Executor,
    FailureMode,
    OutcomeSignal,
    WorkItem,
    WorkQueue,
)
from gemma_forge.harness.tools.sigma.corpus_loader import (
    CorpusLoader,
    LogsourceScope,
    UnsupportedLogsource,
)
from gemma_forge.harness.tools.sigma.sigma_eval import (
    EvalScores,
    RuleParseError,
    evaluate_rule,
    load_rule,
    rule_logsource,
)

logger = logging.getLogger(__name__)


# -- Module-level config (avoids closures that break ADK parsing) ----------

_skill_config: dict = {}


def run_corpus_scan() -> str:
    """Architect tool: report the work queue.

    Friday-night slice: returns the single hardcoded item. Saturday
    morning replaces this with a real per-rule scan that returns all
    rules currently below the PASS threshold.
    """
    cfg = _skill_config
    item = cfg.get("hardcoded_work_item", {})
    return (
        f"Detection-tuning queue (Friday-night slice): 1 rule\n"
        f"- {item.get('rule_path', 'unknown')} "
        f"(technique={item.get('technique_id', 'unknown')})"
    )


def apply_rule_change(candidate_rule_yaml: str, description: str) -> str:
    """Worker tool: write a candidate Sigma rule to the work file.

    Args:
        candidate_rule_yaml: Full YAML text of the proposed rule.
        description: One-line summary of what the Worker changed and why.
    """
    work_path = Path(_skill_config["_current_work_file"])
    work_path.parent.mkdir(parents=True, exist_ok=True)
    work_path.write_text(candidate_rule_yaml)
    return f"wrote candidate to {work_path} — {description}"


def check_eval_health() -> str:
    """Auditor tool: confirm the eval pipeline is functioning.

    For this skill there is no separate "mission app health" — health
    means "the corpus loaded, the rule parsed, P/R/F1 came out as real
    numbers." That's asserted inside the Evaluator; this tool is a
    no-op confirmation for the Auditor's contract.
    """
    return "HEALTHY: detection-tuning eval pipeline reachable"


# -- Interface implementations ------------------------------------------------


class DetectionWorkQueue:
    """Produces detection-tuning work items.

    Friday-night: returns a single hardcoded item from the manifest.
    Saturday morning: scan SigmaHQ rules, return those below threshold.
    """

    def __init__(self, hardcoded_item_cfg: dict):
        self._item_cfg = hardcoded_item_cfg

    async def scan(self) -> list[WorkItem]:
        rule_path = self._item_cfg["rule_path"]
        rule_id = Path(rule_path).stem
        return [WorkItem(
            id=rule_id,
            title=f"Tune {rule_id}",
            category="detection-rule",
            metadata={
                "rule_path": rule_path,
                "technique_id": self._item_cfg.get("technique_id", ""),
                "positive_filename_keywords": list(
                    self._item_cfg.get("positive_filename_keywords", [])
                ),
            },
            resources=[rule_path],
        )]


class DetectionExecutor:
    """Writes Worker-supplied rule candidates to the per-item work file."""

    def __init__(self, rule_workdir: Path):
        self._workdir = rule_workdir

    def work_file_for(self, item: WorkItem) -> Path:
        return self._workdir / f"{item.id}.yml"

    async def apply(self, item: WorkItem, fix_script: str,
                    revert_script: str, description: str) -> str:
        # In this skill, fix_script IS the candidate Sigma rule YAML text.
        # revert_script is unused — Checkpoint handles revert.
        work_path = self.work_file_for(item)
        _skill_config["_current_work_file"] = str(work_path)
        return apply_rule_change(fix_script, description)

    def get_agent_tools(self) -> list:
        return [apply_rule_change]


class DetectionEvaluator:
    """P/R/F1 scoring of the working rule against the labeled corpus."""

    metadata = EvaluatorMetadata(
        # Graded: F1 lives in [0, 1] and the Worker has many partial-credit
        # paths. Conceptually different from STIG's pass/fail.
        signal_type="graded",
        # Deterministic — same rule + same corpus = same scores. No LLM
        # in the evaluation path.
        expected_confidence="high",
        # ~0.5s per rule on the EVTX-ATTACK-SAMPLES corpus (~10k events,
        # logsource-scoped to a few hundred).
        cost_per_evaluation="cheap",
        # Graded skills need more samples before confident eviction.
        # Start at the conservative default; tune after Run 1 data lands.
        min_retrievals_before_eviction=10,
        eviction_threshold=0.5,
    )

    def __init__(
        self,
        corpus: CorpusLoader,
        executor: DetectionExecutor,
        pass_precision: float,
        pass_recall: float,
        rules_repo: Path,
    ):
        self._corpus = corpus
        self._executor = executor
        self._pass_precision = pass_precision
        self._pass_recall = pass_recall
        self._rules_repo = rules_repo
        # Cached logsource scopes keyed by (category, product). Loading
        # the CSV is the expensive bit (~100ms); re-filtering is fast,
        # but caching the scope DataFrame and its positive_mask
        # together saves the label_positives call too.
        self._scope_cache: dict[tuple[str, str, tuple], tuple[LogsourceScope, "pd.Series"]] = {}

    def _resolve_rule_path(self, item: WorkItem) -> Path:
        """Return path to read the *current* version of the rule from.

        Prefers the Worker-edited working file; falls back to the
        pristine upstream rule on first call (before any apply).
        """
        work_path = self._executor.work_file_for(item)
        if work_path.is_file():
            return work_path
        return self._rules_repo / item.metadata["rule_path"]

    def signal_for(self, result: EvalResult, *, attempt_number: int = 1) -> OutcomeSignal:
        """Project the graded eval result into OutcomeSignal.

        For this skill, F1 is the natural utility score — it's already
        in [0, 1] and rewards balanced precision/recall. attempt_number
        damps utility for slow wins, mirroring STIG's pattern.
        """
        f1 = float(result.signals.get("f1", 0.0))
        if result.passed:
            if attempt_number <= 1:
                damp = 1.0
            elif attempt_number <= 3:
                damp = 0.9
            else:
                damp = 0.7
            value = damp * f1 if f1 > 0 else damp
        else:
            # Failed runs still emit F1 as the signal — a near-miss (F1=0.85)
            # is informative about *almost*-helpful tips even though it
            # didn't cross the PASS bar.
            value = f1
        return OutcomeSignal(
            value=value,
            confidence=1.0,
            metadata={
                "failure_mode": (
                    result.failure_mode.value
                    if hasattr(result.failure_mode, "value")
                    else str(result.failure_mode)
                ),
                "attempt_number": attempt_number,
                **result.signals,
            },
        )

    async def evaluate(self, item: WorkItem) -> EvalResult:
        rule_path = self._resolve_rule_path(item)
        try:
            rule = load_rule(rule_path)
            category, product = rule_logsource(rule)
        except (RuleParseError, FileNotFoundError, yaml.YAMLError) as exc:
            return EvalResult(
                passed=False,
                failure_mode=FailureMode.CLEAN_FAILURE,
                summary=f"rule parse failed: {exc}",
                signals={
                    "detection_failure_mode": "rule_parse_failure",
                    "error": str(exc),
                },
            )

        positives = tuple(item.metadata.get("positive_filename_keywords", []))
        cache_key = (category, product, positives)
        if cache_key not in self._scope_cache:
            try:
                scope = self._corpus.scope_for_logsource(category, product)
            except UnsupportedLogsource as exc:
                return EvalResult(
                    passed=False,
                    failure_mode=FailureMode.CLEAN_FAILURE,
                    summary=f"corpus gap: {exc}",
                    signals={
                        "detection_failure_mode": "corpus_gap",
                        "logsource_category": category,
                        "logsource_product": product,
                    },
                )
            positive_mask = self._corpus.label_positives(scope.events, positives)
            self._scope_cache[cache_key] = (scope, positive_mask)
        scope, positive_mask = self._scope_cache[cache_key]

        try:
            scores: EvalScores = evaluate_rule(rule_path, scope.events, positive_mask)
        except RuleParseError as exc:
            return EvalResult(
                passed=False,
                failure_mode=FailureMode.CLEAN_FAILURE,
                summary=f"rule uses unsupported construct: {exc}",
                signals={
                    "detection_failure_mode": "rule_parse_failure",
                    "error": str(exc),
                },
            )

        passed = (scores.precision >= self._pass_precision
                  and scores.recall >= self._pass_recall)

        if passed:
            detection_failure_mode = None
        elif scores.precision < self._pass_precision and scores.recall >= self._pass_recall:
            detection_failure_mode = "rule_too_noisy"
        elif scores.recall < self._pass_recall and scores.precision >= self._pass_precision:
            detection_failure_mode = "rule_too_narrow"
        else:
            detection_failure_mode = "rule_too_noisy_and_narrow"

        return EvalResult(
            passed=passed,
            failure_mode=FailureMode.CLEAN_FAILURE,
            summary=(
                f"P={scores.precision:.3f} R={scores.recall:.3f} F1={scores.f1:.3f} "
                f"(scope={len(scope.events)} events, "
                f"positives={int(positive_mask.sum())})"
            ),
            signals={
                "precision": scores.precision,
                "recall": scores.recall,
                "f1": scores.f1,
                "tp": scores.tp, "fp": scores.fp,
                "fn": scores.fn, "tn": scores.tn,
                "matched_count": scores.matched_count,
                "scope_event_count": len(scope.events),
                "positive_event_count": int(positive_mask.sum()),
                "detection_failure_mode": detection_failure_mode,
                "rule_path_used": str(rule_path),
            },
        )


class DetectionCheckpoint:
    """In-memory text snapshot of per-item working rule files.

    Sigma rules are tiny YAML files (a few KB), so we hold snapshots in
    a dict keyed by snapshot name. Survives only the process lifetime —
    matches the harness's revert contract (snapshots are scoped to one
    attempt, not persisted across runs).
    """

    def __init__(self, executor: DetectionExecutor, rules_repo: Path,
                 hardcoded_rule_path: str):
        self._executor = executor
        self._rules_repo = rules_repo
        # For the Friday-night slice with one item, we initialize a single
        # "baseline" entry from the pristine SigmaHQ source so the first
        # `restore("baseline")` works even before any apply has run.
        upstream = self._rules_repo / hardcoded_rule_path
        self._snapshots: dict[str, str] = {}
        if upstream.is_file():
            self._snapshots["baseline"] = upstream.read_text()

    async def exists(self, name: str) -> bool:
        return name in self._snapshots

    async def save(self, name: str) -> tuple[bool, str]:
        # Save what's currently in the (one) work file. For the
        # multi-item future, this generalizes to "save all work files."
        wf = Path(_skill_config.get("_current_work_file", ""))
        if not wf or not wf.is_file():
            return False, f"no working file to snapshot for {name}"
        self._snapshots[name] = wf.read_text()
        return True, f"snapshotted {wf.name} as {name}"

    async def restore(self, name: str) -> tuple[bool, str]:
        if name not in self._snapshots:
            return False, f"unknown snapshot: {name}"
        wf = Path(_skill_config.get("_current_work_file", ""))
        if not wf:
            return False, f"no current work file set; cannot restore {name}"
        wf.parent.mkdir(parents=True, exist_ok=True)
        wf.write_text(self._snapshots[name])
        return True, f"restored {wf.name} from {name}"

    async def delete(self, name: str) -> tuple[bool, str]:
        self._snapshots.pop(name, None)
        return True, f"deleted {name}"


class DetectionTuningSkillRuntime:
    """Bundles all detection-tuning interfaces for the harness."""

    def __init__(
        self,
        corpus_csv: str,
        rules_repo: str,
        rule_workdir: str,
        hardcoded_work_item: dict,
        pass_precision: float = 0.95,
        pass_recall: float = 0.80,
    ):
        global _skill_config
        _skill_config = {
            "hardcoded_work_item": hardcoded_work_item,
            "rule_workdir": rule_workdir,
            "rules_repo": rules_repo,
            "_current_work_file": None,
        }

        rules_repo_p = Path(rules_repo)
        workdir_p = Path(rule_workdir)
        workdir_p.mkdir(parents=True, exist_ok=True)

        # Seed the working file from the pristine rule on first init so
        # the first evaluate() call has something to read.
        item_id = Path(hardcoded_work_item["rule_path"]).stem
        upstream = rules_repo_p / hardcoded_work_item["rule_path"]
        work_path = workdir_p / f"{item_id}.yml"
        if upstream.is_file() and not work_path.is_file():
            shutil.copy(upstream, work_path)
        _skill_config["_current_work_file"] = str(work_path)

        self._corpus = CorpusLoader(corpus_csv)
        self._executor = DetectionExecutor(workdir_p)
        self._work_queue = DetectionWorkQueue(hardcoded_work_item)
        self._evaluator = DetectionEvaluator(
            self._corpus, self._executor,
            pass_precision=pass_precision,
            pass_recall=pass_recall,
            rules_repo=rules_repo_p,
        )
        self._checkpoint = DetectionCheckpoint(
            self._executor, rules_repo_p,
            hardcoded_work_item["rule_path"],
        )

    @property
    def work_queue(self) -> WorkQueue:
        return self._work_queue

    @property
    def executor(self) -> Executor:
        return self._executor

    @property
    def evaluator(self) -> Evaluator:
        return self._evaluator

    @property
    def checkpoint(self) -> Checkpoint:
        return self._checkpoint

    def get_scan_tool(self):
        return run_corpus_scan

    def worker_context(self, item: WorkItem) -> dict | None:
        """DEF-28: return what the Worker needs to author this rule.

        The Worker is going to propose a candidate Sigma rule. For that
        to be informed instead of guesswork, it needs:

          - description: what the rule is supposed to detect (from the
            rule's own `title` + `description` fields — the canonical
            spec of intent).
          - sample_events: 3 positive events from the labeled corpus —
            concrete records of what the rule must match. Worker sees
            actual field values (CommandLine, GrantedAccess, …) instead
            of guessing what the corpus looks like.
          - current_rule_yaml: the rule's current text — so the Worker
            iterates on the existing draft instead of rewriting from
            scratch each attempt.

        Returns None if the corpus hasn't loaded yet or the rule's
        logsource isn't supported — the harness handles None gracefully
        by skipping the work_item_context section.
        """
        try:
            rule_path = self._evaluator._resolve_rule_path(item)
            rule = load_rule(rule_path)
            category, product = rule_logsource(rule)
            scope = self._corpus.scope_for_logsource(category, product)
            positives = item.metadata.get("positive_filename_keywords", [])
            positive_mask = self._corpus.label_positives(scope.events, positives)
            positive_events = scope.events[positive_mask]
            sample_cols = [
                c for c in (
                    "EVTX_FileName", "EventID", "CommandLine", "Image",
                    "TargetImage", "SourceImage", "GrantedAccess", "CallTrace",
                )
                if c in positive_events.columns
            ]
            samples = (
                positive_events[sample_cols]
                .head(3)
                .to_dict(orient="records")
            )
            return {
                "description": (
                    f"{rule.get('title', item.id)}\n\n"
                    f"{rule.get('description', '')}"
                ),
                "sample_positive_events": json.dumps(samples, indent=2),
                "current_rule_yaml": rule_path.read_text(),
                "check_artifact": str(rule_path),
            }
        except Exception as exc:
            logger.warning("worker_context failed for %s: %s", item.id, exc)
            return None
