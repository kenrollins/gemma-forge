"use client";

import { RunEvent, SkillUI, DEFAULT_SKILL_UI, AGENT_COLORS, GraphNode } from "./types";
import AgentFlow, { Stage } from "./AgentFlow";

function shortId(id: string, prefix: string): string {
  if (!id) return "\u2014";
  if (prefix && id.startsWith(prefix)) return id.slice(prefix.length);
  return id;
}

function formatTime(s: number): string {
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${h}h ${m}m`;
}

/**
 * Derive a one-line narrative describing what is happening right now,
 * derived from the most recent meaningful event. Color reflects the
 * outcome category so the eye picks up state at a glance even before
 * reading the words. Used by the AgentFlow widget below the cards.
 */
function deriveNarrative(events: RunEvent[], stage: Stage): { text: string; color: string } | undefined {
  for (let i = events.length - 1; i >= Math.max(0, events.length - 12); i--) {
    const e = events[i];
    const d = e.data || {};
    switch (e.event_type) {
      case "tool_call": {
        const fn = (d.tool || d.name || "tool") as string;
        return { text: `Worker called ${fn}`, color: AGENT_COLORS.worker };
      }
      case "tool_result": {
        return { text: "Worker received tool result", color: AGENT_COLORS.worker };
      }
      case "evaluation": {
        const passed = d.passed as boolean | undefined;
        if (passed === true) return { text: "Scan passed — rule satisfied", color: "#22C55E" };
        if (passed === false) return { text: "Scan failed — rule still failing", color: "#EF4444" };
        return { text: "Running OScap scan...", color: "#22D3EE" };
      }
      case "remediated": {
        return { text: `Remediated after ${d.attempt || "?"} attempt${(d.attempt as number) === 1 ? "" : "s"}`, color: "#22C55E" };
      }
      case "escalated": {
        return { text: `Escalated after ${d.attempts || "?"} attempts`, color: "#F59E0B" };
      }
      case "reflection": {
        return { text: "Reflector identified a new pattern", color: AGENT_COLORS.reflector };
      }
      case "ban_added": {
        return { text: "Reflector banned a failed approach", color: AGENT_COLORS.reflector };
      }
      case "agent_response": {
        if (e.agent === "architect") return { text: "Architect is reasoning about the plan", color: AGENT_COLORS.architect };
        if (e.agent === "worker") return { text: "Worker is preparing a fix", color: AGENT_COLORS.worker };
        if (e.agent === "reflector") return { text: "Reflector is analyzing the failure", color: AGENT_COLORS.reflector };
        break;
      }
      case "rule_selected":
        return { text: `Architect selected ${d.title || d.rule_id || "next rule"}`, color: AGENT_COLORS.architect };
      case "architect_reengaged":
        return { text: `Architect re-engaged: verdict ${d.verdict || "?"}`, color: AGENT_COLORS.architect };
      case "scanner_gap_detected":
        return { text: "Scanner-gap detected — multiple approaches rejected", color: "#F59E0B" };
      case "revert":
        return { text: "Reverting to last known-good snapshot", color: "#EF4444" };
      case "attempt_start":
        return { text: `Starting attempt ${d.attempt}`, color: "#9CA3AF" };
    }
  }
  // Fallback: stage-only hint
  if (stage === "architect") return { text: "Architect is reasoning about the plan", color: AGENT_COLORS.architect };
  if (stage === "worker") return { text: "Worker is preparing a fix", color: AGENT_COLORS.worker };
  if (stage === "reflector") return { text: "Reflector is analyzing the failure", color: AGENT_COLORS.reflector };
  if (stage === "eval") return { text: "Evaluating outcome", color: "#22D3EE" };
  return undefined;
}

// Pipeline stage detection from recent events
type PipelineStage = "architect" | "worker" | "eval" | "reflector" | "idle";

// Pipeline stage detection. Walks the most recent events backward and
// returns the first stage that matches. The order matters: more
// specific signals win over coarser ones.
//
// Notes for next maintainer:
//   - `attempt_start` is a HARNESS event (it just marks "we are about
//     to ask the worker to act"). We do NOT advance the stage on it,
//     because the worker hasn't actually done anything yet — the
//     prompt is still being assembled. Without this, the indicator
//     reads "Worker active" while the visible event log shows
//     "Starting attempt 1 — HARNESS", which is jarring.
//     The stage stays at architect (from the rule_selected event)
//     until the worker emits a real signal (agent_response or
//     tool_call). That matches what the audience sees in the timeline.
//   - Same logic applies to other harness-only bookkeeping events
//     (cross_run_hydration, clutch_initialized, snapshot_preflight,
//     etc.) — they don't advance the stage either.
function detectPipelineStage(events: RunEvent[]): { stage: PipelineStage; evalPassed?: boolean } {
  // Find the current stage in one backward pass, and the most recent
  // eval verdict in a second backward pass (so the past-eval card
  // keeps its green/red color even after the pipeline has moved on
  // to the reflector).
  let stage: PipelineStage = "idle";
  let evalInStage: boolean | undefined;
  let latestEvalVerdict: boolean | undefined;

  const recent = Math.max(0, events.length - 16);
  // Pass 1: pin the current stage using the most recent events only.
  // Same precedence rules as before.
  for (let i = events.length - 1; i >= Math.max(0, events.length - 8); i--) {
    const e = events[i];
    if (e.event_type === "reflection") { stage = "reflector"; break; }
    if (e.event_type === "evaluation") { stage = "eval"; evalInStage = e.data?.passed as boolean; break; }
    if (e.event_type === "post_mortem" || e.event_type === "revert") { stage = "eval"; evalInStage = false; break; }
    // tool_result = Worker has dispatched the fix and the harness is
    // now running the evaluator (oscap scan). We're in the eval stage
    // but don't know the outcome yet — evalPassed intentionally left
    // undefined so AgentFlow paints it with the in-progress neutral
    // color rather than the success/failure colors.
    if (e.event_type === "tool_result") { stage = "eval"; evalInStage = undefined; break; }
    if (e.event_type === "tool_call") { stage = "worker"; break; }
    if (e.agent === "worker" && e.event_type === "agent_response") { stage = "worker"; break; }
    if (e.agent === "reflector" && e.event_type === "agent_response") { stage = "reflector"; break; }
    if (e.agent === "architect" && e.event_type === "agent_response") { stage = "architect"; break; }
    if (e.event_type === "rule_selected" || e.event_type === "architect_reengaged") { stage = "architect"; break; }
    // Deliberately NOT matching attempt_start, cross_run_hydration,
    // clutch_initialized, snapshot_preflight, etc. — those are
    // harness-internal and the stage indicator should hold the most
    // recent agent-driven stage instead of jumping back to the worker.
  }

  // Pass 2: find the most recent evaluation verdict so the past-eval
  // card (when stage has moved on to reflector) keeps the right color
  // instead of defaulting to the in-progress cyan.
  for (let i = events.length - 1; i >= recent; i--) {
    const e = events[i];
    if (e.event_type === "evaluation") {
      latestEvalVerdict = e.data?.passed as boolean;
      break;
    }
    if (e.event_type === "post_mortem" || e.event_type === "revert") {
      latestEvalVerdict = false;
      break;
    }
  }

  // Prefer the in-stage verdict (current eval card) over the
  // historical one (past eval card's color).
  const evalPassed = evalInStage !== undefined ? evalInStage : latestEvalVerdict;
  return { stage, evalPassed };
}

export default function HeroStrip({
  events,
  skillUI = DEFAULT_SKILL_UI,
  connected,
  elapsed,
}: {
  events: RunEvent[];
  skillUI: SkillUI;
  connected: boolean;
  elapsed: number;
}) {
  // Extract counts from latest graph_state
  let completed = 0, escalated = 0, skipped = 0, active = 0, total = 0;
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].event_type === "graph_state" && events[i].data?.nodes) {
      const nodes = events[i].data.nodes as GraphNode[];
      total = nodes.length;
      for (const n of nodes) {
        if (n.state === "completed") completed++;
        else if (n.state === "escalated") escalated++;
        else if (n.state === "skipped") skipped++;
        else if (n.state === "active") active++;
      }
      break;
    }
  }
  const done = completed + escalated + skipped;
  const remaining = total - done - active;

  // Current item from latest rule_selected / attempt_start
  let currentRuleId = "";
  let currentTitle = "";
  let currentCategory = "";
  let currentAttempt = 0;
  let ruleElapsed = 0;
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.event_type === "attempt_start" && !currentAttempt) {
      currentAttempt = e.data.attempt as number;
      ruleElapsed = e.data.rule_elapsed_s as number || 0;
      currentRuleId = currentRuleId || (e.data.rule_id as string);
      currentCategory = currentCategory || (e.data.category as string);
    }
    if (e.event_type === "rule_selected") {
      currentRuleId = currentRuleId || (e.data.rule_id as string);
      currentTitle = e.data.title as string || "";
      currentCategory = currentCategory || (e.data.category as string);
      break;
    }
  }

  // Did the current rule just resolve? We keep the card visible
  // (so it doesn't flash out between rules at fast replay), but the
  // narrative below shifts to the outcome so the transition reads
  // as a beat, not a gap. ruleClosed is still used by the narrative
  // logic — not for hiding.
  let ruleClosed = false;
  let ruleOutcome: "remediated" | "escalated" | null = null;
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.event_type === "remediated") {
      ruleClosed = true;
      ruleOutcome = "remediated";
      break;
    }
    if (e.event_type === "escalated") {
      ruleClosed = true;
      ruleOutcome = "escalated";
      break;
    }
    if (e.event_type === "rule_selected") break;
  }
  // Silence unused warnings — ruleOutcome and ruleClosed inform
  // future styling (e.g. success/failure flash on the card).
  void ruleOutcome;
  void ruleClosed;

  // Tok/s from latest agent_response
  let tokPerSec = 0;
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].event_type === "agent_response" && events[i].data?.timing) {
      tokPerSec = (events[i].data.timing as { tok_per_sec?: number })?.tok_per_sec || 0;
      break;
    }
  }

  // Pipeline stage + the prose narrative for AgentFlow
  const { stage, evalPassed } = detectPipelineStage(events);
  const narrative = deriveNarrative(events, stage as Stage);

  // Progress bar percentages
  const pctCompleted = total > 0 ? (completed / total) * 100 : 0;
  const pctEscalated = total > 0 ? (escalated / total) * 100 : 0;
  const pctSkipped = total > 0 ? (skipped / total) * 100 : 0;
  const pctActive = total > 0 ? (active / total) * 100 : 0;

  // Keep the hero card visible as long as we know what rule we're on.
  // Previously this hid the section during the brief window between a
  // rule closing (remediated/escalated) and the next rule_selected
  // firing — at 1000x replay that gap became a visible flash every
  // few hundred ms. Holding the last rule's name and outcome keeps
  // the transition smooth; the narrative updates to the outcome
  // automatically via deriveNarrative().
  const showCurrentItem = currentRuleId && events.length > 0;

  return (
    <div className="border-b border-[#1C1F26] bg-[#0D0F14]">
      {/* Top row: Hero metrics + progress bar */}
      <div className="px-4 pt-3 pb-2 flex items-center gap-4">
        {/* Done count */}
        <div className="shrink-0 text-center min-w-[72px]">
          <div className="text-2xl font-bold tabular-nums text-[#E8EAED]">
            {done}<span className="text-sm text-[#6B7280] font-normal"> / {total}</span>
          </div>
          <div className="text-[9px] uppercase tracking-wider text-[#6B7280]">
            {total > 0 ? `${Math.round((done / total) * 100)}% done` : "waiting"}
          </div>
        </div>

        {/* Escalated */}
        <div className="shrink-0 text-center min-w-[56px]">
          <div className="text-xl font-bold tabular-nums" style={{ color: escalated > 0 ? "#F59E0B" : "#4B5563" }}>
            {escalated}
          </div>
          <div className="text-[9px] uppercase tracking-wider text-[#6B7280]">escalated</div>
        </div>

        {/* Tok/s */}
        <div className="shrink-0 text-center min-w-[56px]">
          <div className="text-xl font-bold tabular-nums text-[#22D3EE]">
            {tokPerSec > 0 ? tokPerSec.toFixed(1) : "\u2014"}
          </div>
          <div className="text-[9px] uppercase tracking-wider text-[#6B7280]">tok/s</div>
        </div>

        {/* Progress bar (compact) + run stats */}
        <div className="flex-1 min-w-0 flex items-center gap-4">
          {/* Thin progress bar */}
          <div className="w-32 shrink-0">
            <div className="h-2.5 rounded-full overflow-hidden flex bg-[#1A1D24]" title={`${completed} completed, ${escalated} escalated, ${skipped} skipped, ${active} active, ${remaining} remaining`}>
              {pctCompleted > 0 && (
                <div className="transition-all duration-700 ease-out" style={{ width: `${pctCompleted}%`, background: "#22C55E" }} />
              )}
              {pctEscalated > 0 && (
                <div className="transition-all duration-700 ease-out" style={{ width: `${pctEscalated}%`, background: "#F59E0B" }} />
              )}
              {pctSkipped > 0 && (
                <div className="transition-all duration-700 ease-out" style={{ width: `${pctSkipped}%`, background: "#4B5563" }} />
              )}
              {pctActive > 0 && (
                <div className="transition-all duration-500 animate-pulse" style={{ width: `${Math.max(pctActive, 0.5)}%`, background: "#22D3EE" }} />
              )}
            </div>
          </div>
          {/* Run summary stats */}
          <div className="flex items-center gap-3 text-[10px] text-[#6B7280] tabular-nums">
            <span><span className="text-[#22C55E]">{completed}</span> {skillUI.fixed_label.toLowerCase()}</span>
            <span><span className="text-[#F59E0B]">{escalated}</span> escalated</span>
            {skipped > 0 && <span>{skipped} skipped</span>}
            {remaining > 0 && <span>{remaining} remaining</span>}
            <span>{formatTime(elapsed)}</span>
            {done > 0 && elapsed > 0 && (
              <span>{(done * 3600 / elapsed).toFixed(1)} rules/hr</span>
            )}
          </div>
        </div>
      </div>

      {/* Rule headline + AgentFlow widget — the visual centerpiece.
          The rule name reads as a real headline (large, prominent),
          and the four-stage AgentFlow below it conveys what is
          happening RIGHT NOW. At 100x replay this is what carries
          the story — the rule name slot-machines through and the
          glowing stage chases the work across the page. */}
      {showCurrentItem && (
        <div className="px-4 pb-3 pt-1 border-t border-[#15181F] bg-[#0A0C10]">
          {/* Rule headline */}
          <div className="flex items-baseline gap-3 mb-2.5">
            <span className="text-[10px] uppercase tracking-[0.2em] text-[#4B5563] shrink-0 mt-1">
              Now working
            </span>
            <span
              className="text-[16px] font-bold text-[#E8EAED] truncate font-mono leading-tight"
              title={currentTitle || currentRuleId}
            >
              {currentTitle || shortId(currentRuleId, skillUI.id_prefix_strip)}
            </span>
            {currentCategory && (
              <span
                className="px-2 py-0.5 rounded-sm text-[9px] font-bold uppercase tracking-wider shrink-0"
                style={{
                  background: "rgba(59, 130, 246, 0.15)",
                  color: "#60A5FA",
                }}
                title={`Rule category: ${currentCategory}`}
              >
                {currentCategory}
              </span>
            )}
            {currentAttempt > 0 && (
              <span
                className="text-[11px] text-[#9CA3AF] tabular-nums shrink-0 ml-auto"
                style={{ minWidth: 110, textAlign: "right" }}
                title="Current attempt and elapsed time on this rule"
              >
                attempt <span className="text-[#E8EAED] font-bold">{currentAttempt}</span>
                <span className="text-[#3F4451] mx-1.5">·</span>
                {formatTime(ruleElapsed)}
              </span>
            )}
          </div>

          {/* AgentFlow — handoff cards with active-stage pulse + narrative */}
          <AgentFlow
            stage={stage as Stage}
            evalPassed={evalPassed}
            narrative={narrative}
          />
        </div>
      )}
    </div>
  );
}
