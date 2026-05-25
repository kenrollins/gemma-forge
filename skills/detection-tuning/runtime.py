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
    SdsCorpus,
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
    """Architect tool: report the current rule queue.

    Returns one line per configured work item: rule name, technique,
    and a short description. The Architect uses this to pick which
    rule to hand to the Worker next. Per-rule scores live in the
    state summary the harness assembles, not here — this tool is for
    the queue snapshot, the scores are passed in conversation context.
    """
    items = _skill_config.get("work_items", [])
    if not items:
        return "Detection-tuning queue is empty."
    lines = [f"Detection-tuning queue: {len(items)} rules"]
    for it in items:
        name = Path(it["rule_path"]).stem
        tech = it.get("technique_id", "?")
        desc = it.get("description", "")
        lines.append(f"- {name}  [{tech}]  {desc}")
    return "\n".join(lines)


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
    """Produces detection-tuning work items from the manifest's curated list.

    Iterates skill.yaml's `work_items:` list, one WorkItem per entry.
    Per-corpus ground-truth keywords are resolved here at scan time
    via the active corpus's `keywords_by_technique` map — that way
    swapping corpora doesn't require rebuilding the queue, the
    metadata changes per scan.

    Future: replace the manifest's curated list with a SigmaHQ-wide
    auto-scan. The curated-list shape stays valid; auto-scan would
    just be a different WorkQueue implementation.
    """

    def __init__(
        self,
        items_cfg: list[dict],
        keywords_by_technique: dict[str, list[str]],
    ):
        self._items_cfg = items_cfg
        self._kw_by_tech = keywords_by_technique

    async def scan(self) -> list[WorkItem]:
        items: list[WorkItem] = []
        for cfg in self._items_cfg:
            rule_path = cfg["rule_path"]
            rule_id = Path(rule_path).stem
            technique_id = cfg.get("technique_id", "")
            keywords = list(self._kw_by_tech.get(technique_id, []))
            items.append(WorkItem(
                id=rule_id,
                title=f"Tune {rule_id}",
                category="detection-rule",
                metadata={
                    "rule_path": rule_path,
                    "technique_id": technique_id,
                    "description": cfg.get("description", ""),
                    "positive_filename_keywords": keywords,
                },
                resources=[rule_path],
            ))
        return items


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
                failure_mode=FailureMode.RULE_PARSE_FAILURE,
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
                    failure_mode=FailureMode.CORPUS_GAP,
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
                failure_mode=FailureMode.RULE_PARSE_FAILURE,
                summary=f"rule uses unsupported construct: {exc}",
                signals={
                    "detection_failure_mode": "rule_parse_failure",
                    "error": str(exc),
                },
            )

        passed = (scores.precision >= self._pass_precision
                  and scores.recall >= self._pass_recall)

        # Failure mode routing per ADR-0022 §2:
        #   recall ok, precision low      → RULE_TOO_NOISY
        #   precision ok, recall low      → RULE_TOO_NARROW
        #   both axes below threshold     → RULE_TOO_NOISY (the "both" case),
        #                                   with signals["detection_failure_mode"]
        #                                   distinguishing for Reflector/Worker
        if passed:
            failure_mode = FailureMode.CLEAN_FAILURE  # ignored when passed=True
            detection_failure_mode = None
        elif scores.precision < self._pass_precision and scores.recall >= self._pass_recall:
            failure_mode = FailureMode.RULE_TOO_NOISY
            detection_failure_mode = "rule_too_noisy"
        elif scores.recall < self._pass_recall and scores.precision >= self._pass_precision:
            failure_mode = FailureMode.RULE_TOO_NARROW
            detection_failure_mode = "rule_too_narrow"
        else:
            failure_mode = FailureMode.RULE_TOO_NOISY
            detection_failure_mode = "rule_too_noisy_and_narrow"

        return EvalResult(
            passed=passed,
            failure_mode=failure_mode,
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
    """In-memory text snapshot of the currently-active rule's working file.

    Sigma rules are tiny YAML files (a few KB), so we hold snapshots in
    a dict keyed by snapshot name. Survives only the process lifetime —
    matches the harness's revert contract (snapshots are scoped to one
    attempt, not persisted across runs).

    Multi-item semantics: snapshots are global by name (e.g., "progress"),
    not per-item. The harness drives one item at a time; "progress" is
    always the snapshot of whatever item the Worker just applied to.
    This mirrors STIG's VM-state model — there's only one "current
    target," and the snapshot is named for the lifecycle phase rather
    than the item.

    ``baseline`` is treated as always-existing because we have an
    immutable upstream SigmaHQ checkout to fall back to per-item via
    the Evaluator's _resolve_rule_path; the harness's startup check
    is satisfied without storing the upstream contents up front.
    """

    def __init__(self, executor: DetectionExecutor, rules_repo: Path):
        self._executor = executor
        self._rules_repo = rules_repo
        self._snapshots: dict[str, str] = {}
        # Marker so exists("baseline") returns True at harness startup.
        self._baseline_available = True

    async def exists(self, name: str) -> bool:
        if name == "baseline":
            return self._baseline_available
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
        corpus,                       # CorpusLoader or SdsCorpus instance
        rules_repo: str,
        rule_workdir: str,
        work_items: list[dict],
        keywords_by_technique: dict[str, list[str]],
        corpus_name: str = "",
        pass_precision: float = 0.95,
        pass_recall: float = 0.80,
    ):
        global _skill_config
        _skill_config = {
            "work_items": work_items,
            "rule_workdir": rule_workdir,
            "rules_repo": rules_repo,
            "corpus_name": corpus_name,
            # Set per-apply by DetectionExecutor.apply — captures whichever
            # item the harness is currently driving so Checkpoint knows
            # which working file to snapshot/restore.
            "_current_work_file": None,
        }

        rules_repo_p = Path(rules_repo)
        workdir_p = Path(rule_workdir)
        workdir_p.mkdir(parents=True, exist_ok=True)

        # Work files are NOT pre-seeded from upstream — Evaluator's
        # _resolve_rule_path falls back to the rules_repo path on first
        # read, so initial evaluations score the pristine rule. Worker
        # apply() is what creates the per-item work file.

        self._corpus = corpus
        self._corpus_name = corpus_name
        self._executor = DetectionExecutor(workdir_p)
        self._work_queue = DetectionWorkQueue(work_items, keywords_by_technique)
        self._evaluator = DetectionEvaluator(
            self._corpus, self._executor,
            pass_precision=pass_precision,
            pass_recall=pass_recall,
            rules_repo=rules_repo_p,
        )
        self._checkpoint = DetectionCheckpoint(self._executor, rules_repo_p)

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


def build_runtime(harness_cfg: dict) -> "DetectionTuningSkillRuntime":
    """Manifest-declared builder. Called by the harness's _build_skill_runtime.

    The harness has no skill-specific knowledge — this function owns the
    config layout. Matches the shape of stig-rhel9 / cve-response
    `build_runtime()` after the 9ff8688 refactor.

    Config sources (in priority order):
      1. ``DT_CORPUS`` env var — operator override at run-time.
      2. ``harness_cfg['detection']`` — per-deployment overrides in
         config/harness.yaml (corpus selection, thresholds, etc.)
      3. ``skill.yaml``'s ``detection:`` block — curated skill content
         (work_items, corpora map, default_corpus). Loaded by reading
         skill.yaml co-located with this module.

    The split keeps work_items + corpora map (long curated content)
    in the skill definition where it belongs, while letting deployment
    knobs (which corpus to load, pass thresholds) live in harness.yaml
    or env vars so operators can swap corpora without editing skills/.
    """
    import os
    import yaml as _yaml

    skill_yaml = Path(__file__).parent / "skill.yaml"
    manifest_det_cfg: dict = {}
    if skill_yaml.is_file():
        with open(skill_yaml) as _f:
            raw = _yaml.safe_load(_f) or {}
        manifest_det_cfg = raw.get("detection", {}) or {}

    override = (harness_cfg or {}).get("detection", {}) or {}

    def cfg(key, fallback):
        if key in override:
            return override[key]
        if key in manifest_det_cfg:
            return manifest_det_cfg[key]
        return fallback

    # Corpus selection. DT_CORPUS env wins (run-time override);
    # then harness_cfg.detection.corpus; then manifest's default_corpus.
    corpus_name = (
        os.environ.get("DT_CORPUS")
        or cfg("corpus", None)
        or cfg("default_corpus", "evtx-attack-samples")
    )

    corpora = manifest_det_cfg.get("corpora", {}) or {}
    if corpus_name not in corpora:
        raise RuntimeError(
            f"detection-tuning: unknown corpus {corpus_name!r}. "
            f"Available: {sorted(corpora)}. Edit skill.yaml's "
            "detection.corpora to add new corpora."
        )
    corpus_cfg = corpora[corpus_name]
    fmt = corpus_cfg.get("format", "evtx_csv")
    path = corpus_cfg.get("path", "")
    keywords_by_technique = corpus_cfg.get("keywords_by_technique", {}) or {}

    if fmt == "evtx_csv":
        corpus = CorpusLoader(path)
    elif fmt == "sds_ndjson":
        corpus = SdsCorpus(path)
    else:
        raise RuntimeError(
            f"detection-tuning: unknown corpus format {fmt!r} for {corpus_name!r}. "
            "Supported: evtx_csv, sds_ndjson."
        )

    logger.info(
        "detection-tuning build_runtime: corpus=%s format=%s path=%s "
        "techniques=%d work_items=%d",
        corpus_name, fmt, path, len(keywords_by_technique),
        len(cfg("work_items", [])),
    )

    return DetectionTuningSkillRuntime(
        corpus=corpus,
        corpus_name=corpus_name,
        rules_repo=cfg("rules_repo", "/tmp/dt-rules/sigma"),
        rule_workdir=cfg("rule_workdir", "/tmp/dt-work/rules"),
        work_items=cfg("work_items", []),
        keywords_by_technique=keywords_by_technique,
        pass_precision=cfg("pass_precision", 0.95),
        pass_recall=cfg("pass_recall", 0.80),
    )
