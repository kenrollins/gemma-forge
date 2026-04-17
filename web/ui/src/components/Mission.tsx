"use client";

import { RunEvent, AGENT_COLORS, SkillUI, DEFAULT_SKILL_UI } from "./types";

function shortId(id: string, prefix: string): string {
  if (!id) return "—";
  if (prefix && id.startsWith(prefix)) return id.slice(prefix.length);
  return id;
}

// Parse structured reflection content: Pattern / Root cause / Recommendation + tags
function parseReflection(text: string): {
  pattern?: string;
  rootCause?: string;
  recommendation?: string;
  banned?: string;
  preferred?: string;
  lesson?: string;
} {
  const result: {
    pattern?: string;
    rootCause?: string;
    recommendation?: string;
    banned?: string;
    preferred?: string;
    lesson?: string;
  } = {};

  // Normalize and extract sections. The Reflector tends to produce Markdown-ish output.
  const lines = text.split("\n").map(l => l.trim());
  const joined = text.replace(/\n/g, " ");

  const patternMatch = joined.match(/pattern identified:\s*([^\n\.]*\.?[^\n]*?)(?=\s*(?:root cause|strategic|recommendation|BANNED|PREFERRED|LESSON|$))/i);
  const rootMatch = joined.match(/root cause:\s*([^\n]*?)(?=\s*(?:strategic|recommendation|BANNED|PREFERRED|LESSON|$))/i);
  const recMatch = joined.match(/(?:strategic recommendation|recommendation):\s*([^\n]*?)(?=\s*(?:BANNED|PREFERRED|LESSON|$))/i);

  if (patternMatch) result.pattern = patternMatch[1].trim().slice(0, 240);
  if (rootMatch) result.rootCause = rootMatch[1].trim().slice(0, 240);
  if (recMatch) result.recommendation = recMatch[1].trim().slice(0, 240);

  for (const line of lines) {
    if (line.match(/^BANNED:/i)) result.banned = line.replace(/^BANNED:\s*/i, "");
    if (line.match(/^PREFERRED:/i)) result.preferred = line.replace(/^PREFERRED:\s*/i, "");
    if (line.match(/^LESSON:/i)) result.lesson = line.replace(/^LESSON:\s*/i, "");
  }

  // If nothing structured was found, use the first non-empty content as pattern
  if (!result.pattern && !result.rootCause && !result.recommendation) {
    const firstLine = lines.find(l => l.length > 10 && !l.startsWith("```"));
    if (firstLine) result.pattern = firstLine.slice(0, 300);
  }

  return result;
}

function buildOutcomes(events: RunEvent[]): Array<{ type: string; ruleId: string; attempts?: number; elapsed_s: number }> {
  const outcomes: Array<{ type: string; ruleId: string; attempts?: number; elapsed_s: number }> = [];
  for (const e of events) {
    if (e.event_type === "remediated") {
      outcomes.push({ type: "fixed", ruleId: e.data.rule_id as string, attempts: e.data.attempt as number, elapsed_s: e.elapsed_s });
    } else if (e.event_type === "escalated") {
      outcomes.push({ type: "escalated", ruleId: e.data.rule_id as string, attempts: e.data.attempts as number, elapsed_s: e.elapsed_s });
    } else if (e.event_type === "skip") {
      outcomes.push({ type: "skipped", ruleId: e.data.rule_id as string, elapsed_s: e.elapsed_s });
    }
  }
  return outcomes;
}

// Given the latest events, summarize what's happening RIGHT NOW in one punchy phrase
function describeCurrentAction(
  events: RunEvent[],
  skillUI: SkillUI,
): { text: string; agent: string; icon: string } | null {
  if (events.length === 0) return null;

  // Walk back up to 5 events to find the most informative action
  const window = events.slice(-5).reverse();

  for (const e of window) {
    if (e.event_type === "tool_call") {
      const tool = e.data.tool as string;
      const agent = e.agent;
      if (tool === "apply_fix") {
        return { text: "Applying fix to target VM…", agent, icon: "▶" };
      }
      if (tool === "run_stig_scan") {
        return { text: "Scanning for STIG compliance failures…", agent, icon: "◎" };
      }
      if (tool === "check_health") {
        return { text: "Health-checking the mission app…", agent, icon: "♥" };
      }
      if (tool === "revert_last_fix") {
        return { text: "Reverting the last fix…", agent, icon: "⟲" };
      }
      return { text: `Calling tool: ${tool}`, agent, icon: "→" };
    }

    if (e.event_type === "evaluation") {
      const d = e.data;
      if (d.passed) {
        return { text: `PASSED \u2014 ${skillUI.work_item} ${skillUI.fixed_label.toLowerCase()}, mission app healthy`, agent: "harness", icon: "✓" };
      }
      return {
        text: `FAILED \u2014 health=${d.health_ok ? "✓" : "✗"} ${skillUI.work_item}=${d.rule_ok ? "✓" : "✗"} journal=${d.journal_clean ? "✓" : "✗"}`,
        agent: "harness",
        icon: "✗",
      };
    }

    if (e.event_type === "revert") {
      return { text: "Reverting — fix did not pass evaluation", agent: "harness", icon: "⟲" };
    }

    if (e.event_type === "reflection") {
      return { text: "Reflecting on the failure, updating episodic memory…", agent: "reflector", icon: "◆" };
    }

    if (e.event_type === "remediated") {
      return { text: `${skillUI.work_item} ${skillUI.fixed_label.toLowerCase()} on attempt ${e.data.attempt || "?"}`, agent: "harness", icon: "✓" };
    }

    if (e.event_type === "escalated") {
      return { text: `Escalated after ${e.data.attempts || "?"} failed attempts`, agent: "harness", icon: "✗" };
    }

    if (e.event_type === "rule_selected") {
      return { text: `Architect selected a new ${skillUI.work_item} to work on`, agent: "architect", icon: "◉" };
    }

    if (e.event_type === "attempt_start") {
      return { text: `Starting attempt ${e.data.attempt}/${e.data.max_attempts}`, agent: "harness", icon: "▶" };
    }

    if (e.event_type === "agent_response") {
      const text = (e.data.text as string) || "";
      // Show the first meaningful line of the agent's response
      const firstLine = text.split("\n").find(l => l.trim().length > 20) || text.slice(0, 100);
      return {
        text: firstLine.slice(0, 140),
        agent: e.agent,
        icon: "●",
      };
    }
  }

  return null;
}

export default function Mission({ events, skillUI = DEFAULT_SKILL_UI }: { events: RunEvent[]; skillUI?: SkillUI }) {
  const lastReflection = [...events].reverse().find(e => e.event_type === "reflection");
  const reflection = lastReflection ? parseReflection((lastReflection.data.text as string) || "") : null;

  const outcomes = buildOutcomes(events);
  const recentOutcomes = outcomes.slice(-24);

  const currentAction = describeCurrentAction(events, skillUI);
  const actionColor = currentAction ? (AGENT_COLORS[currentAction.agent] || "#6B7280") : "#6B7280";

  if (events.length === 0) {
    return (
      <div className="p-10 text-center text-[#4B5563] text-sm h-full flex items-center justify-center">
        Waiting for first event — connect to live or select a run to replay
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Top: Current action strip — what's happening RIGHT NOW */}
      {currentAction && (
        <div
          className="shrink-0 border-b px-4 py-2 flex items-center gap-3"
          style={{
            background: `linear-gradient(90deg, ${actionColor}15, transparent 60%)`,
            borderColor: `${actionColor}33`,
          }}
        >
          <span className="text-lg font-bold" style={{ color: actionColor }}>
            {currentAction.icon}
          </span>
          <div className="flex-1 min-w-0">
            <div className="text-[9px] font-semibold uppercase tracking-wider text-[#6B7280]">
              Now · {currentAction.agent}
            </div>
            <div className="text-[13px] font-mono text-[#E8EAED] truncate">
              {currentAction.text}
            </div>
          </div>
        </div>
      )}

      {/* Middle: Latest reflection (structured display) */}
      <div className="flex-1 min-h-0 border-b border-[#1C1F26] bg-[#120a1e] px-4 py-3 overflow-hidden flex flex-col">
        <div className="flex items-center gap-2 mb-2 shrink-0">
          <span className="text-[#A855F7] text-base">◆</span>
          <span className="text-[10px] font-semibold uppercase tracking-wider text-[#A855F7]">
            Latest Reflection
          </span>
          {lastReflection ? (
            <span className="text-[9px] text-[#4B5563] ml-auto font-mono">
              {lastReflection.elapsed_s.toFixed(0)}s · Reflector
            </span>
          ) : (
            <span className="text-[9px] text-[#4B5563] ml-auto italic">
              fires after the first failure
            </span>
          )}
        </div>
        {reflection ? (
          <div className="flex-1 min-h-0 overflow-y-auto space-y-1.5">
            {reflection.pattern && (
              <div className="text-[11px]">
                <span className="text-[#A855F7] font-bold uppercase tracking-wider text-[9px] mr-2">Pattern</span>
                <span className="text-[#C4B5FD] leading-relaxed">{reflection.pattern}</span>
              </div>
            )}
            {reflection.rootCause && (
              <div className="text-[11px]">
                <span className="text-[#F59E0B] font-bold uppercase tracking-wider text-[9px] mr-2">Root Cause</span>
                <span className="text-[#9CA3AF] leading-relaxed">{reflection.rootCause}</span>
              </div>
            )}
            {reflection.recommendation && (
              <div className="text-[11px]">
                <span className="text-[#3B82F6] font-bold uppercase tracking-wider text-[9px] mr-2">Recommendation</span>
                <span className="text-[#9CA3AF] leading-relaxed">{reflection.recommendation}</span>
              </div>
            )}
            {(reflection.banned || reflection.preferred || reflection.lesson) && (
              <div className="mt-2 pt-2 border-t border-[#2a1540] space-y-0.5 text-[11px] font-mono">
                {reflection.banned && (
                  <div><span className="text-[#EF4444] font-bold">BANNED:</span> <span className="text-[#9CA3AF]">{reflection.banned}</span></div>
                )}
                {reflection.preferred && (
                  <div><span className="text-[#22C55E] font-bold">PREFER:</span> <span className="text-[#9CA3AF]">{reflection.preferred}</span></div>
                )}
                {reflection.lesson && (
                  <div><span className="text-[#3B82F6] font-bold">LESSON:</span> <span className="text-[#9CA3AF]">{reflection.lesson}</span></div>
                )}
              </div>
            )}
          </div>
        ) : (
          <div className="text-[11px] text-[#4B5563] italic">
            No reflections yet — the Reflector fires after the first failed attempt.
          </div>
        )}
      </div>

      {/* Bottom: Recent outcomes timeline (small) */}
      <div className="shrink-0 px-4 py-1.5 bg-[#0D0F14]">
        <div className="flex items-center justify-between mb-1">
          <div className="text-[9px] font-semibold uppercase tracking-wider text-[#6B7280]">
            Recent Outcomes ({outcomes.length} total)
          </div>
          <div className="flex items-center gap-2 text-[9px] text-[#6B7280]">
            {skillUI.outcomes.map(o => (
              <span key={o.type} className="flex items-center gap-1">
                <div className="w-1.5 h-1.5 rounded-sm" style={{ background: o.color }} />
                {o.label}
              </span>
            ))}
          </div>
        </div>
        <div className="flex gap-1 flex-wrap">
          {recentOutcomes.length === 0 ? (
            <span className="text-[10px] text-[#4B5563] italic">No outcomes yet</span>
          ) : (
            recentOutcomes.map((o, i) => {
              const outcomeDef = skillUI.outcomes.find(x => x.type === o.type);
              const color = outcomeDef?.color
                || (o.type === "fixed" ? "#22C55E" : o.type === "escalated" ? "#EF4444" : "#6B7280");
              const label = outcomeDef?.label || o.type;
              const icon = o.type === "fixed" ? "✓"
                : o.type === "escalated" ? "✗"
                : "⊘";
              return (
                <div
                  key={i}
                  className="px-1.5 py-0.5 rounded-sm text-[10px] font-mono font-bold flex items-center gap-1"
                  style={{
                    background: `${color}15`,
                    color,
                    border: `1px solid ${color}33`,
                  }}
                  title={`${label}: ${o.ruleId}${o.attempts ? ` (${o.attempts} attempts)` : ""}`}
                >
                  <span>{icon}</span>
                  <span className="max-w-[160px] truncate">{shortId(o.ruleId, skillUI.id_prefix_strip).slice(0, 32)}</span>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
