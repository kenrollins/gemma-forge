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

function outcomeColor(outcome: Outcome, skillUI: SkillUI): string {
  const match = skillUI.outcomes?.find((o) => o.type === outcome);
  return match?.color || FALLBACK_OUTCOME_COLOR[outcome];
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

  const cellCount = Math.max(total, resolved.length, 1);
  const remaining = Math.max(cellCount - resolved.length, 0);

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
          const isLatest = r !== null && i === resolved.length - 1;
          const isHover = i === hoverIdx;
          const color = r ? outcomeColor(r.outcome, skillUI) : undefined;

          return (
            <button
              key={i}
              onMouseEnter={() => setHoverIdx(i)}
              className="relative flex-1 h-6 rounded-sm transition-colors"
              style={{
                background: r
                  ? isHover
                    ? color
                    : `${color}DD`
                  : "#15181F",
                outline: isHover ? `1px solid ${color}FF` : undefined,
                minWidth: 2,
              }}
              title={
                r
                  ? `${hoveredLabelFor(r, skillUI)} · ${r.outcome} · ${formatTime(r.elapsed_s)}`
                  : "pending"
              }
            >
              {isLatest && (
                <span
                  className="absolute -top-1 left-1/2 -translate-x-1/2 w-1.5 h-1.5 rounded-full"
                  style={{
                    background: color,
                    boxShadow: `0 0 6px ${color}, 0 0 12px ${color}60`,
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
