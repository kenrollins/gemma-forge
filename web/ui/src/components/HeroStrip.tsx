"use client";

import { RunEvent, SkillUI, DEFAULT_SKILL_UI, AGENT_COLORS, GraphNode } from "./types";

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

// Pipeline stage detection from recent events
type PipelineStage = "architect" | "worker" | "eval" | "reflector" | "idle";

function detectPipelineStage(events: RunEvent[]): { stage: PipelineStage; evalPassed?: boolean } {
  for (let i = events.length - 1; i >= Math.max(0, events.length - 8); i--) {
    const e = events[i];
    if (e.event_type === "reflection") return { stage: "reflector" };
    if (e.event_type === "evaluation") return { stage: "eval", evalPassed: e.data?.passed as boolean };
    if (e.event_type === "post_mortem" || e.event_type === "revert") return { stage: "eval", evalPassed: false };
    if (e.event_type === "tool_call" || e.event_type === "tool_result") return { stage: "worker" };
    if (e.agent === "worker" && e.event_type === "agent_response") return { stage: "worker" };
    if (e.agent === "reflector" && e.event_type === "agent_response") return { stage: "reflector" };
    if (e.agent === "architect" && e.event_type === "agent_response") return { stage: "architect" };
    if (e.event_type === "rule_selected" || e.event_type === "architect_reengaged") return { stage: "architect" };
    if (e.event_type === "attempt_start") return { stage: "worker" };
  }
  return { stage: "idle" };
}

function getStageColor(key: PipelineStage, evalPassed?: boolean): string {
  if (key === "eval") return evalPassed === false ? "#EF4444" : "#22C55E";
  if (key === "architect") return AGENT_COLORS.architect;
  if (key === "worker") return AGENT_COLORS.worker;
  if (key === "reflector") return AGENT_COLORS.reflector;
  return "#6B7280";
}

const STAGES: { key: PipelineStage; label: string }[] = [
  { key: "architect", label: "Arch" },
  { key: "worker", label: "Work" },
  { key: "eval", label: "Eval" },
  { key: "reflector", label: "Refl" },
];

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

  // Check if current rule is done (remediated or escalated since last rule_selected)
  let ruleClosed = false;
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.event_type === "remediated" || e.event_type === "escalated") {
      ruleClosed = true;
      break;
    }
    if (e.event_type === "rule_selected") break;
  }

  // Tok/s from latest agent_response
  let tokPerSec = 0;
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].event_type === "agent_response" && events[i].data?.timing) {
      tokPerSec = (events[i].data.timing as { tok_per_sec?: number })?.tok_per_sec || 0;
      break;
    }
  }

  // Pipeline stage
  const { stage, evalPassed } = detectPipelineStage(events);

  // Progress bar percentages
  const pctCompleted = total > 0 ? (completed / total) * 100 : 0;
  const pctEscalated = total > 0 ? (escalated / total) * 100 : 0;
  const pctSkipped = total > 0 ? (skipped / total) * 100 : 0;
  const pctActive = total > 0 ? (active / total) * 100 : 0;

  const showCurrentItem = currentRuleId && !ruleClosed && events.length > 0;

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

      {/* Bottom row: Current item + pipeline dots */}
      {showCurrentItem && (
        <div className="px-4 pb-2 flex items-center gap-3 text-[11px]">
          <span className="text-[9px] uppercase tracking-wider text-[#4B5563] shrink-0">Now</span>
          <span className="font-mono font-bold text-[#E8EAED] truncate max-w-[240px]">
            {shortId(currentRuleId, skillUI.id_prefix_strip)}
          </span>
          {currentCategory && (
            <span
              className="px-1.5 py-0.5 rounded-sm text-[9px] font-semibold shrink-0"
              style={{
                background: "rgba(59, 130, 246, 0.15)",
                color: "#60A5FA",
              }}
            >
              {currentCategory}
            </span>
          )}

          {/* Pipeline dots */}
          <div className="flex items-center gap-1 shrink-0 ml-auto">
            {STAGES.map((s, idx) => {
              const isActive = stage === s.key;
              const isPast = STAGES.findIndex(x => x.key === stage) > idx;
              const dotColor = getStageColor(s.key, evalPassed);
              return (
                <div key={s.key} className="flex items-center">
                  {idx > 0 && (
                    <div
                      className="w-3 h-px mx-0.5"
                      style={{ background: isPast || isActive ? dotColor + "60" : "#2A2E38" }}
                    />
                  )}
                  <div className="flex flex-col items-center">
                    <div
                      className={`w-2.5 h-2.5 rounded-full ${isActive ? "animate-pulse ring-2 ring-offset-1 ring-offset-[#0D0F14]" : ""}`}
                      style={{
                        background: isActive ? dotColor : isPast ? dotColor : "#2A2E38",
                        opacity: isPast ? 0.6 : 1,
                      }}
                      title={s.label}
                    />
                    <span
                      className="text-[8px] mt-0.5"
                      style={{ color: isActive ? dotColor : "#4B5563" }}
                    >
                      {s.label}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Attempt + elapsed */}
          {currentAttempt > 0 && (
            <span className="text-[10px] text-[#6B7280] tabular-nums shrink-0">
              att {currentAttempt} · {Math.round(ruleElapsed)}s
            </span>
          )}
        </div>
      )}
    </div>
  );
}
