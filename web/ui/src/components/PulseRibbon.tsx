"use client";

/**
 * PulseRibbon — time-ordered seismograph of rule outcomes across a
 * run. Each cell is one rule, colored by its outcome (green = fixed,
 * amber = escalated, gray = skipped). Cells appear left-to-right in
 * the order the harness resolved them, so at 100x replay the whole
 * 12-hour run plays out as a 30-second visual fill. The NOW marker
 * pulses at the rightmost resolved position so the viewer's eye
 * tracks progress without hunting.
 *
 * Below the ribbon: summary counts + an elapsed/projected-end hint
 * that gives scale without stealing focus from the seismograph.
 *
 * Deliberately does NOT replace the category heatmap (TaskMap) —
 * they tell different stories. This one is "how has this run unfolded
 * over time," the waffle is "how are the categories doing." For the
 * demo this ribbon lives above the TaskMap in the Mission view.
 */

import { useMemo, useState } from "react";
import type { RunEvent, SkillUI, GraphNode } from "./types";

type Outcome = "fixed" | "escalated" | "skipped";

type Phase = "architect" | "worker" | "eval_pass" | "eval_fail" | "reflector" | "harness";

interface ResolvedRule {
  /** Sort order. Index into the time-ordered resolution sequence. */
  idx: number;
  ruleId: string;
  title?: string;
  category?: string;
  outcome: Outcome;
  /** Elapsed seconds at which this resolution occurred. */
  elapsed_s: number;
}

// Fallback colors when skillUI.outcomes doesn't define one — matches
// the ORIGINAL hardcoded palette so existing STIG demos look
// identical, but skills can override by listing `outcomes` entries
// with their own hex values.
const FALLBACK_OUTCOME_COLOR: Record<Outcome, string> = {
  fixed: "#22C55E",
  escalated: "#F59E0B",
  skipped: "#6B7280",
};

// Phase → dot color. Matches the agent palette used across the
// dashboard so the pulse on the ribbon stays visually consistent
// with the ARCHITECT/WORKER/EVAL/REFLECTOR cards above.
const PHASE_COLOR: Record<Phase, string> = {
  architect: "#3B82F6",
  worker: "#F59E0B",
  eval_pass: "#22C55E",
  eval_fail: "#EF4444",
  reflector: "#A855F7",
  harness: "#9CA3AF",
};

function outcomeColor(outcome: Outcome, skillUI: SkillUI): string {
  const match = skillUI.outcomes?.find((o) => o.type === outcome);
  return match?.color || FALLBACK_OUTCOME_COLOR[outcome];
}

// Walk the recent event tail to figure out which agent phase is
// currently active. First matching event type wins. Only events that
// represent an actual agent doing work count — harness boundary events
// like `attempt_start`, `iteration_start`, `prompt_assembled`,
// `graph_state` are skipped so the dot color stays aligned with which
// agent last actually emitted something. Returns null when the most
// recent activity was a rule resolving.
function detectActivePhase(events: RunEvent[]): Phase | null {
  const window = events.slice(-40);
  for (let i = window.length - 1; i >= 0; i--) {
    const e = window[i];
    switch (e.event_type) {
      case "remediated":
      case "escalated":
      case "skip":
        // The most recent item has already resolved — nothing in flight.
        return null;
      case "rule_selected":
        return "architect";
      case "architect_reengaged":
        return "architect";
      case "reflection":
        return "reflector";
      case "evaluation":
        return e.data?.passed ? "eval_pass" : "eval_fail";
      case "revert":
        return "harness";
      case "tool_call":
      case "tool_result":
        return "worker";
      case "agent_response":
        if (e.agent === "architect") return "architect";
        if (e.agent === "reflector") return "reflector";
        if (e.agent === "worker") return "worker";
        continue;
      default:
        // attempt_start, iteration_start, prompt_assembled, graph_state,
        // ban_added, tip_added, post_mortem, etc — none represent an
        // agent actively thinking, so we scan past them.
        continue;
    }
  }
  return null;
}

function formatTime(s: number): string {
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${h}h ${m}m`;
}

export default function PulseRibbon({
  events,
  skillUI,
}: {
  events: RunEvent[];
  skillUI: SkillUI;
}) {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  // Pull time-ordered resolutions + the expected total from graph_state.
  // The total is pinned to what the task graph knows; as resolutions
  // stream in, unfilled cells render as "upcoming" placeholders.
  const { resolved, total, latestElapsed } = useMemo(() => {
    const list: ResolvedRule[] = [];
    let total = 0;
    let latestElapsed = 0;

    for (const e of events) {
      if (e.event_type === "graph_state" && Array.isArray(e.data?.nodes)) {
        const n = (e.data.nodes as GraphNode[]).length;
        if (n > total) total = n;
      }
      if (e.elapsed_s > latestElapsed) latestElapsed = e.elapsed_s;

      let outcome: Outcome | null = null;
      if (e.event_type === "remediated") outcome = "fixed";
      else if (e.event_type === "escalated") outcome = "escalated";
      else if (e.event_type === "skip") outcome = "skipped";
      if (outcome == null) continue;

      const d = e.data || {};
      list.push({
        idx: list.length,
        ruleId: (d.rule_id as string) || "",
        title: d.title as string | undefined,
        category: d.category as string | undefined,
        outcome,
        elapsed_s: e.elapsed_s,
      });
    }
    return { resolved: list, total, latestElapsed };
  }, [events]);

  const counts = useMemo(() => {
    const c = { fixed: 0, escalated: 0, skipped: 0 } as Record<Outcome, number>;
    for (const r of resolved) c[r.outcome]++;
    return c;
  }, [resolved]);

  // Detect the active-phase marker so the pulse can sit on the
  // currently-working cell (rather than trailing on the last
  // resolved one) and wear the phase's color.
  const activePhase = useMemo(() => detectActivePhase(events), [events]);

  const cellCount = Math.max(total, resolved.length + (activePhase ? 1 : 0), 1);
  // Index of the cell representing the rule currently in flight,
  // if any. Always sits just past the last resolved cell — the
  // resolution sequence is strictly left-to-right.
  const inProgressIdx = activePhase && resolved.length < cellCount ? resolved.length : null;
  const remaining = Math.max(cellCount - resolved.length - (inProgressIdx !== null ? 1 : 0), 0);

  // Hover detail
  const hovered = hoverIdx !== null ? resolved[hoverIdx] : null;
  const hoveredLabel = hovered
    ? (hovered.title ||
      (skillUI.id_prefix_strip && hovered.ruleId.startsWith(skillUI.id_prefix_strip)
        ? hovered.ruleId.slice(skillUI.id_prefix_strip.length)
        : hovered.ruleId))
    : null;

  return (
    <div className="border-b border-[#1C1F26] bg-[#0A0C10] px-5 py-3">
      {/* Header row: title + legend + time scale */}
      <div className="flex items-center gap-4 mb-2">
        <span className="text-[10px] font-semibold tracking-[0.2em] uppercase text-[#6B7280]">
          Run pulse
        </span>

        <div className="flex items-center gap-3 text-[10px] text-[#9CA3AF]">
          <LegendSwatch color={outcomeColor("fixed", skillUI)} label={`${counts.fixed} ${skillUI.fixed_label.toLowerCase()}`} />
          <LegendSwatch color={outcomeColor("escalated", skillUI)} label={`${counts.escalated} escalated`} />
          {counts.skipped > 0 && (
            <LegendSwatch color={outcomeColor("skipped", skillUI)} label={`${counts.skipped} skipped`} />
          )}
          {remaining > 0 && (
            <LegendSwatch color="#23262E" label={`${remaining} pending`} outlined />
          )}
        </div>

        <span
          className="ml-auto text-[10px] font-mono tabular-nums text-[#6B7280]"
          title="Elapsed time in this run"
        >
          {formatTime(latestElapsed)} elapsed
        </span>
      </div>

      {/* The ribbon itself */}
      <div
        className="relative w-full flex items-stretch gap-[1px] select-none"
        onMouseLeave={() => setHoverIdx(null)}
      >
        {Array.from({ length: cellCount }).map((_, i) => {
          const r = i < resolved.length ? resolved[i] : null;
          const isInProgress = i === inProgressIdx;
          const isHover = i === hoverIdx;
          const color = r ? outcomeColor(r.outcome, skillUI) : undefined;
          const phaseColor = activePhase ? PHASE_COLOR[activePhase] : "#9CA3AF";

          // Background priority: resolved outcome > in-progress neutral > pending.
          // In-progress cells get a soft grey tint (tied to phase color at low
          // opacity) so they read as "something's happening" without committing
          // to an outcome.
          const background = r
            ? isHover
              ? color
              : `${color}DD`
            : isInProgress
            ? `${phaseColor}22`
            : "#15181F";

          return (
            <button
              key={i}
              onMouseEnter={() => setHoverIdx(i)}
              className="relative flex-1 h-6 rounded-sm transition-colors"
              style={{
                background,
                outline: isHover && color
                  ? `1px solid ${color}FF`
                  : isInProgress
                  ? `1px solid ${phaseColor}80`
                  : undefined,
                minWidth: 2,
              }}
              title={
                r
                  ? `${hoveredLabelFor(r, skillUI)} \u00b7 ${r.outcome} \u00b7 ${formatTime(r.elapsed_s)}`
                  : isInProgress
                  ? `in progress \u00b7 ${activePhase}`
                  : "pending"
              }
            >
              {isInProgress && (
                <span
                  className="absolute -top-1 left-1/2 -translate-x-1/2 w-1.5 h-1.5 rounded-full"
                  style={{
                    background: phaseColor,
                    boxShadow: `0 0 6px ${phaseColor}, 0 0 12px ${phaseColor}60`,
                    animation: "pulseRibbonNow 1.6s ease-in-out infinite",
                  }}
                />
              )}
            </button>
          );
        })}
      </div>

      {/* Hover detail */}
      <div className="h-4 mt-1.5 flex items-center">
        {hovered && hoveredLabel ? (
          <span
            className="text-[11px] font-mono truncate"
            style={{ color: outcomeColor(hovered.outcome, skillUI) }}
          >
            {hoveredLabel}
            <span className="text-[#4B5563] mx-1.5">·</span>
            <span className="text-[#9CA3AF]">{hovered.outcome}</span>
            <span className="text-[#4B5563] mx-1.5">·</span>
            <span className="text-[#6B7280] tabular-nums">at {formatTime(hovered.elapsed_s)}</span>
            {hovered.category && (
              <>
                <span className="text-[#4B5563] mx-1.5">·</span>
                <span className="text-[#6B7280]">{hovered.category}</span>
              </>
            )}
          </span>
        ) : resolved.length > 0 ? (
          <span className="text-[10px] text-[#4B5563] italic">
            Hover a cell to see which {skillUI.work_item}. The bright pulse marks the most recent resolution.
          </span>
        ) : (
          <span className="text-[10px] text-[#4B5563] italic">
            Waiting for the first {skillUI.work_item} to resolve...
          </span>
        )}
      </div>

      <style jsx>{`
        @keyframes pulseRibbonNow {
          0%, 100% { transform: translate(-50%, 0) scale(1); opacity: 1; }
          50%      { transform: translate(-50%, 0) scale(1.6); opacity: 0.55; }
        }
      `}</style>
    </div>
  );
}

function LegendSwatch({ color, label, outlined }: { color: string; label: string; outlined?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className="inline-block w-2 h-2 rounded-sm"
        style={{
          background: outlined ? "transparent" : color,
          border: outlined ? `1px solid ${color}` : undefined,
        }}
      />
      <span className="tabular-nums">{label}</span>
    </span>
  );
}

function hoveredLabelFor(r: ResolvedRule, skillUI: SkillUI): string {
  if (r.title) return r.title;
  if (skillUI.id_prefix_strip && r.ruleId.startsWith(skillUI.id_prefix_strip)) {
    return r.ruleId.slice(skillUI.id_prefix_strip.length);
  }
  return r.ruleId;
}
