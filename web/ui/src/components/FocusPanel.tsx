"use client";

// React hooks not needed — auto-scroll uses callback ref
import { RunEvent, SkillUI, DEFAULT_SKILL_UI, AGENT_COLORS, WorkItemDetail } from "./types";

function shortId(id: string, prefix: string): string {
  if (!id) return "\u2014";
  if (prefix && id.startsWith(prefix)) return id.slice(prefix.length);
  return id;
}

function formatTime(s: number): string {
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

// -- Ported from Mission.tsx --

function parseReflection(text: string): {
  pattern?: string;
  rootCause?: string;
  recommendation?: string;
  banned?: string;
  preferred?: string;
  lesson?: string;
} {
  const result: Record<string, string | undefined> = {};
  const lines = text.split("\n").map(l => l.trim());
  const joined = text.replace(/\n/g, " ");

  const patternMatch = joined.match(/pattern identified:\s*([^\n.]*\.?[^\n]*?)(?=\s*(?:root cause|strategic|recommendation|BANNED|PREFERRED|LESSON|$))/i);
  const rootMatch = joined.match(/root cause:\s*([^\n]*?)(?=\s*(?:strategic|recommendation|BANNED|PREFERRED|LESSON|$))/i);
  const recMatch = joined.match(/(?:strategic recommendation|recommendation|strategy):\s*([^\n]*?)(?=\s*(?:BANNED|PREFERRED|LESSON|$))/i);

  if (patternMatch) result.pattern = patternMatch[1].trim().slice(0, 240);
  if (rootMatch) result.rootCause = rootMatch[1].trim().slice(0, 240);
  if (recMatch) result.recommendation = recMatch[1].trim().slice(0, 240);

  for (const line of lines) {
    if (line.match(/^BANNED:/i)) result.banned = line.replace(/^BANNED:\s*/i, "");
    if (line.match(/^PREFERRED:/i)) result.preferred = line.replace(/^PREFERRED:\s*/i, "");
    if (line.match(/^LESSON:/i)) result.lesson = line.replace(/^LESSON:\s*/i, "");
  }

  if (!result.pattern && !result.rootCause && !result.recommendation) {
    const firstLine = lines.find(l => l.length > 10 && !l.startsWith("```"));
    if (firstLine) result.pattern = firstLine.slice(0, 300);
  }

  return result;
}

function describeCurrentAction(events: RunEvent[]): { text: string; agent: string; icon: string } | null {
  if (events.length === 0) return null;
  const window = events.slice(-5).reverse();
  for (const e of window) {
    if (e.event_type === "tool_call") {
      const tool = e.data.tool as string;
      if (tool === "apply_fix") return { text: "Applying fix to target VM\u2026", agent: e.agent, icon: "\u25B6" };
      if (tool === "run_stig_scan") return { text: "Scanning for compliance failures\u2026", agent: e.agent, icon: "\u25CE" };
      if (tool === "check_health") return { text: "Health-checking the mission app\u2026", agent: e.agent, icon: "\u2665" };
      if (tool === "revert_last_fix") return { text: "Reverting the last fix\u2026", agent: e.agent, icon: "\u27F2" };
      return { text: `Calling tool: ${tool}`, agent: e.agent, icon: "\u2192" };
    }
    if (e.event_type === "evaluation") {
      if (e.data.passed) return { text: "PASSED \u2014 evaluation succeeded", agent: "harness", icon: "\u2713" };
      return { text: `FAILED \u2014 ${(e.data.summary as string || "").slice(0, 100)}`, agent: "harness", icon: "\u2717" };
    }
    if (e.event_type === "revert") return { text: "Reverting \u2014 fix did not pass evaluation", agent: "harness", icon: "\u27F2" };
    if (e.event_type === "reflection") return { text: "Reflecting on the failure\u2026", agent: "reflector", icon: "\u25C6" };
    if (e.event_type === "remediated") return { text: `Remediated on attempt ${e.data.attempt || "?"}`, agent: "harness", icon: "\u2713" };
    if (e.event_type === "escalated") return { text: `Escalated after ${e.data.attempts || "?"} attempts`, agent: "harness", icon: "\u2717" };
    if (e.event_type === "rule_selected") return { text: "Selecting next work item\u2026", agent: "architect", icon: "\u25C9" };
    if (e.event_type === "attempt_start") return { text: `Starting attempt ${e.data.attempt}`, agent: "harness", icon: "\u25B6" };
    if (e.event_type === "agent_response") {
      const text = (e.data.text as string) || "";
      const firstLine = text.split("\n").find(l => l.trim().length > 20) || text.slice(0, 100);
      return { text: firstLine.slice(0, 140), agent: e.agent, icon: "\u25CF" };
    }
  }
  return null;
}

// Build WorkItemDetail from events for a specific item
function buildItemDetail(itemId: string, events: RunEvent[], skillUI: SkillUI): WorkItemDetail | null {
  let title = "";
  let category = "";
  let state = "queued";
  let attempts = 0;
  let wallTime = 0;
  let escalationReason: string | undefined;
  const approachesTried: string[] = [];
  const reflections: { text: string; attempt: number; plateaued: boolean }[] = [];
  const reengagements: { verdict: string; trigger: string; attempt: number }[] = [];

  for (const e of events) {
    const rid = e.data?.rule_id as string;
    if (rid !== itemId) continue;

    if (e.event_type === "rule_selected") {
      title = e.data.title as string || "";
      category = e.data.category as string || "";
    }
    if (e.event_type === "rule_complete") {
      state = e.data.outcome as string || "unknown";
      attempts = e.data.attempts as number || 0;
      wallTime = e.data.wall_time_s as number || 0;
      escalationReason = e.data.escalation_reason as string | undefined;
      const tried = e.data.approaches_tried as string[];
      if (tried) approachesTried.push(...tried);
    }
    if (e.event_type === "remediated") {
      state = "completed";
      attempts = e.data.attempt as number || 1;
      wallTime = e.data.wall_time_s as number || 0;
    }
    if (e.event_type === "escalated") {
      state = "escalated";
      attempts = e.data.attempts as number || 0;
      wallTime = e.data.wall_time_s as number || 0;
      escalationReason = e.data.reason as string;
    }
    if (e.event_type === "reflection") {
      reflections.push({
        text: e.data.text as string || "",
        attempt: e.data.attempt as number || 0,
        plateaued: e.data.plateaued as boolean || false,
      });
    }
    if (e.event_type === "architect_reengaged") {
      reengagements.push({
        verdict: e.data.verdict as string || "?",
        trigger: e.data.trigger as string || "?",
        attempt: e.data.attempt as number || 0,
      });
    }
  }

  if (!title && !category) return null;

  return {
    id: itemId,
    title,
    category,
    state,
    attempts,
    wall_time_s: wallTime,
    escalation_reason: escalationReason,
    approaches_tried: approachesTried,
    reflections,
    reengagements,
  };
}

// -- Sub-components --

function AgentInsight({ events }: { events: RunEvent[] }) {
  // Find the current rule being worked on
  let currentRuleId = "";
  let currentTitle = "";
  let currentCategory = "";
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].event_type === "rule_selected") {
      currentRuleId = events[i].data.rule_id as string;
      currentTitle = events[i].data.title as string || "";
      currentCategory = events[i].data.category as string || "";
      break;
    }
  }

  // Gather the full agent conversation for this rule
  interface ConvoEntry {
    agent: string;
    type: string;
    text: string;
    elapsed_s: number;
    tokens?: { prompt?: number; completion?: number };
    tokPerSec?: number;
    attempt?: number;
  }
  const conversation: ConvoEntry[] = [];
  let inCurrentRule = false;
  let latestReflectionText = "";
  let totalTokens = { prompt: 0, completion: 0 };
  let currentAttempt = 0;
  let timeBudgetS = 0;
  let ruleElapsedS = 0;

  for (const e of events) {
    if (e.event_type === "rule_selected") {
      if (e.data.rule_id === currentRuleId) {
        inCurrentRule = true;
        conversation.length = 0;
        totalTokens = { prompt: 0, completion: 0 };
        latestReflectionText = "";
        currentAttempt = 0;
      } else if (inCurrentRule) {
        break;
      }
    }
    if (!inCurrentRule) continue;

    if (e.event_type === "agent_response" && e.data?.text) {
      const tokens = e.data.tokens as { prompt?: number; completion?: number } | undefined;
      const timing = e.data.timing as { tok_per_sec?: number } | undefined;
      if (tokens?.prompt) totalTokens.prompt += tokens.prompt;
      if (tokens?.completion) totalTokens.completion += tokens.completion;
      conversation.push({
        agent: e.agent,
        type: "response",
        text: e.data.text as string,
        elapsed_s: e.elapsed_s,
        tokens,
        tokPerSec: timing?.tok_per_sec,
      });
    } else if (e.event_type === "evaluation") {
      const passed = e.data.passed as boolean;
      conversation.push({
        agent: "eval",
        type: passed ? "eval_pass" : "eval_fail",
        text: passed
          ? `PASSED \u2014 ${(e.data.summary as string || "evaluation succeeded").slice(0, 200)}`
          : `FAILED \u2014 ${(e.data.summary as string || "").slice(0, 200)}`,
        elapsed_s: e.elapsed_s,
      });
    } else if (e.event_type === "revert") {
      conversation.push({
        agent: "harness",
        type: "revert",
        text: `Checkpoint restored: ${(e.data.reason as string || "").slice(0, 150)}`,
        elapsed_s: e.elapsed_s,
      });
    } else if (e.event_type === "attempt_start") {
      currentAttempt = e.data.attempt as number;
      timeBudgetS = e.data.time_budget_s as number || timeBudgetS;
      ruleElapsedS = e.data.rule_elapsed_s as number || 0;
      conversation.push({
        agent: "harness",
        type: "attempt",
        text: `Attempt ${e.data.attempt} \u2014 ${Math.round(ruleElapsedS)}s / ${timeBudgetS}s`,
        elapsed_s: e.elapsed_s,
        attempt: e.data.attempt as number,
      });
    } else if (e.event_type === "architect_reengaged") {
      const verdict = e.data.verdict as string;
      conversation.push({
        agent: "architect",
        type: "verdict",
        text: e.data.full_response as string || `Verdict: ${verdict}`,
        elapsed_s: e.elapsed_s,
      });
    } else if (e.event_type === "reflection") {
      latestReflectionText = e.data.text as string || "";
    } else if (e.event_type === "remediated") {
      conversation.push({
        agent: "harness",
        type: "success",
        text: `REMEDIATED on attempt ${e.data.attempt} (${Math.round(e.data.wall_time_s as number || 0)}s)`,
        elapsed_s: e.elapsed_s,
      });
    } else if (e.event_type === "escalated") {
      conversation.push({
        agent: "harness",
        type: "escalated",
        text: `ESCALATED after ${e.data.attempts} attempts \u2014 ${e.data.reason}`,
        elapsed_s: e.elapsed_s,
      });
    }
  }

  // Determine what's happening NOW
  const action = describeCurrentAction(events);
  const actionColor = action ? (AGENT_COLORS[action.agent] || "#6B7280") : "#6B7280";

  if (conversation.length === 0 && !action) {
    return (
      <div className="px-4 py-4 text-[11px] text-[#4B5563] italic border-b border-[#1C1F26]">
        Waiting for activity\u2026
      </div>
    );
  }

  // Type-based styling
  const entryBorder = (entry: ConvoEntry) => {
    if (entry.type === "eval_pass" || entry.type === "success") return "#22C55E";
    if (entry.type === "eval_fail" || entry.type === "revert" || entry.type === "escalated") return "#EF4444";
    if (entry.type === "verdict") return "#3B82F6";
    return AGENT_COLORS[entry.agent] || "#6B7280";
  };

  // Add reflections into the conversation timeline inline
  // Re-walk events to insert reflections in sequence
  const enrichedConversation: ConvoEntry[] = [];
  let inRule = false;
  for (const e of events) {
    if (e.event_type === "rule_selected") {
      if (e.data.rule_id === currentRuleId) {
        inRule = true;
        enrichedConversation.length = 0;
      } else if (inRule) break;
    }
    if (!inRule) continue;

    // Already have these from conversation, but we need reflections too
    if (e.event_type === "reflection") {
      const parsed = parseReflection(e.data.text as string || "");
      const parts: string[] = [];
      if (parsed.pattern) parts.push(`Pattern: ${parsed.pattern}`);
      if (parsed.rootCause) parts.push(`Cause: ${parsed.rootCause}`);
      if (parsed.lesson) parts.push(`Lesson: ${parsed.lesson}`);
      if (parsed.banned) parts.push(`BAN: ${parsed.banned}`);
      enrichedConversation.push({
        agent: "reflector",
        type: "reflection",
        text: parts.join("\n") || (e.data.text as string || "").slice(0, 400),
        elapsed_s: e.elapsed_s,
        tokens: e.data.tokens as { prompt?: number; completion?: number } | undefined,
      });
    }
  }

  // Merge reflections into the main conversation at the right positions
  // Strategy: rebuild from conversation + enrichedConversation by elapsed_s
  const allEntries = [...conversation];
  for (const ref of enrichedConversation) {
    // Insert reflection after the last entry with elapsed_s <= ref.elapsed_s
    let insertIdx = allEntries.length;
    for (let j = allEntries.length - 1; j >= 0; j--) {
      if (allEntries[j].elapsed_s <= ref.elapsed_s) {
        insertIdx = j + 1;
        break;
      }
    }
    allEntries.splice(insertIdx, 0, ref);
  }

  // Auto-scroll: use a callback ref on the last entry instead of useRef+useEffect
  const bottomCallback = (el: HTMLDivElement | null) => {
    if (el) el.scrollIntoView({ behavior: "smooth", block: "end" });
  };

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      {/* Sticky header: prominent status + rule context */}
      <div className="shrink-0 border-b border-[#1C1F26]">
        {/* HERO STATUS — big, obvious, the first thing your eye hits */}
        {action && (
          <div
            className="px-4 py-3 flex items-center gap-3"
            style={{
              background: `linear-gradient(135deg, ${actionColor}18, ${actionColor}06 60%, transparent)`,
              borderBottom: `2px solid ${actionColor}40`,
            }}
          >
            <span
              className="text-2xl shrink-0"
              style={{ color: actionColor, filter: `drop-shadow(0 0 6px ${actionColor}60)` }}
            >
              {action.icon}
            </span>
            <div className="flex-1 min-w-0">
              <div className="text-[13px] font-mono font-bold text-[#E8EAED] truncate">
                {action.text}
              </div>
              <div className="text-[10px] uppercase tracking-wider mt-0.5" style={{ color: actionColor }}>
                {action.agent}
              </div>
            </div>
            <div
              className="w-2 h-2 rounded-full animate-pulse shrink-0"
              style={{ background: actionColor, boxShadow: `0 0 8px ${actionColor}` }}
            />
          </div>
        )}
        {/* Rule + attempt + countdown + tokens — all in one clear block */}
        <div className="px-4 py-2 bg-[#0D0F14] flex flex-col gap-1.5">
          {/* Rule title + category */}
          <div className="flex items-center gap-2">
            {currentTitle && (
              <span className="text-[12px] text-[#E8EAED] font-semibold truncate">{currentTitle}</span>
            )}
            {currentCategory && (
              <span className="px-1.5 py-0.5 rounded-sm text-[9px] font-bold uppercase shrink-0"
                style={{ background: "rgba(59,130,246,0.15)", color: "#60A5FA" }}>
                {currentCategory}
              </span>
            )}
          </div>
          {/* Attempt + countdown bar + tokens — one horizontal row */}
          <div className="flex items-center gap-3 text-[10px]">
            {currentAttempt > 0 && (
              <span className="text-[#E8EAED] font-bold tabular-nums">Attempt {currentAttempt}</span>
            )}
            {timeBudgetS > 0 && (() => {
              const remaining = Math.max(0, timeBudgetS - ruleElapsedS);
              const pct = ruleElapsedS / timeBudgetS;
              const timeColor = pct >= 0.8 ? "#EF4444" : pct >= 0.5 ? "#F59E0B" : "#22C55E";
              return (
                <div className="flex items-center gap-1.5 flex-1">
                  <div className="flex-1 h-1.5 rounded-full bg-[#1A1D24] overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-500"
                      style={{ width: `${Math.min(pct * 100, 100)}%`, background: timeColor }}
                    />
                  </div>
                  <span className="tabular-nums font-bold shrink-0" style={{ color: timeColor }}>
                    {formatTime(remaining)}
                  </span>
                  <span className="text-[#4B5563] shrink-0">of {formatTime(timeBudgetS)}</span>
                </div>
              );
            })()}
            {totalTokens.prompt > 0 && (
              <span className="text-[#4B5563] tabular-nums shrink-0">
                {totalTokens.prompt.toLocaleString()} prompt + {totalTokens.completion.toLocaleString()} completion
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Console — auto-scrolling conversation timeline */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-1.5 bg-[#080A0E]"
        style={{
          boxShadow: "inset 0 2px 8px rgba(0,0,0,0.4), inset 0 0 1px rgba(0,0,0,0.6)",
          borderTop: "1px solid #000",
        }}>
        {allEntries.map((entry, i) => {
          const borderColor = entryBorder(entry);
          const agentColor = AGENT_COLORS[entry.agent] || "#6B7280";
          const isResponse = entry.type === "response";
          const isReflection = entry.type === "reflection";
          const isAttemptDivider = entry.type === "attempt";
          const isOutcome = entry.type === "success" || entry.type === "escalated";

          if (isAttemptDivider) {
            return (
              <div key={i} className="flex items-center gap-2 py-1 mt-1">
                <div className="h-px flex-1 bg-[#2A2E38]" />
                <span className="text-[9px] text-[#6B7280] font-mono font-semibold">{entry.text}</span>
                <div className="h-px flex-1 bg-[#2A2E38]" />
              </div>
            );
          }

          if (isOutcome) {
            return (
              <div
                key={i}
                className="rounded-sm px-3 py-2 text-center"
                style={{ background: `${borderColor}15`, border: `1px solid ${borderColor}40` }}
              >
                <span className="text-[12px] font-bold font-mono" style={{ color: borderColor }}>
                  {entry.text}
                </span>
              </div>
            );
          }

          if (isReflection) {
            return (
              <div
                key={i}
                className="rounded-sm px-2.5 py-2 bg-[#110a1a]"
                style={{ borderLeft: "2px solid #A855F7" }}
              >
                <div className="flex items-center gap-1.5 mb-1">
                  <span className="text-[#A855F7] text-xs">{"\u25C6"}</span>
                  <span className="text-[9px] uppercase tracking-wider font-semibold text-[#A855F7]">
                    reflector
                  </span>
                  {entry.tokens && (
                    <span className="text-[8px] text-[#4B5563] tabular-nums">
                      {entry.tokens.prompt}p+{entry.tokens.completion}c
                    </span>
                  )}
                  <span className="text-[9px] text-[#4B5563] ml-auto tabular-nums">
                    {entry.elapsed_s.toFixed(0)}s
                  </span>
                </div>
                <pre className="text-[11px] text-[#C4B5FD] font-mono whitespace-pre-wrap leading-relaxed">
                  {entry.text}
                </pre>
              </div>
            );
          }

          // Agent-specific visual treatment
          const isArchitect = entry.agent === "architect";
          const isWorker = entry.agent === "worker";
          const isEval = entry.type === "eval_pass" || entry.type === "eval_fail";
          const isRevert = entry.type === "revert";

          // Eval results get a compact inline treatment
          if (isEval || isRevert) {
            const isPass = entry.type === "eval_pass";
            const evalColor = isPass ? "#22C55E" : "#EF4444";
            return (
              <div
                key={i}
                className="flex items-center gap-2 px-2.5 py-1.5 rounded-sm"
                style={{ background: `${evalColor}08`, borderLeft: `2px solid ${evalColor}` }}
              >
                <span className="text-sm" style={{ color: evalColor }}>{isPass ? "\u2713" : isRevert ? "\u27F2" : "\u2717"}</span>
                <span className="text-[11px] font-mono flex-1" style={{ color: evalColor }}>
                  {entry.text.slice(0, 200)}
                </span>
                <span className="text-[9px] text-[#4B5563] tabular-nums shrink-0">{entry.elapsed_s.toFixed(0)}s</span>
              </div>
            );
          }

          return (
            <div
              key={i}
              className={`rounded-sm ${isArchitect ? "px-3 py-2.5 mt-1" : "px-2.5 py-2"}`}
              style={{
                borderLeft: `${isArchitect ? 3 : 2}px solid ${borderColor}`,
                background: isArchitect ? `${borderColor}10` : isWorker ? `${borderColor}08` : `${borderColor}06`,
              }}
            >
              {/* Agent header */}
              <div className="flex items-center gap-1.5 mb-1">
                <div className={`${isArchitect ? "w-2 h-2" : "w-1.5 h-1.5"} rounded-full`} style={{ background: agentColor }} />
                <span className={`uppercase tracking-wider font-bold ${isArchitect ? "text-[10px]" : "text-[9px]"}`} style={{ color: agentColor }}>
                  {entry.agent}
                </span>
                {entry.tokPerSec && (
                  <span className="text-[9px] text-[#22D3EE] tabular-nums">{entry.tokPerSec.toFixed(1)} tok/s</span>
                )}
                {entry.tokens && (
                  <span className="text-[9px] text-[#4B5563] tabular-nums">
                    {entry.tokens.prompt}p + {entry.tokens.completion}c
                  </span>
                )}
                <span className="text-[9px] text-[#4B5563] ml-auto tabular-nums">
                  {entry.elapsed_s.toFixed(0)}s
                </span>
              </div>
              {/* Content — no artificial clipping for architect */}
              {isResponse ? (
                <pre className={`font-mono whitespace-pre-wrap leading-relaxed overflow-y-auto
                  ${isArchitect ? "text-[12px] text-[#E8EAED] max-h-64" : "text-[11px] text-[#C9D1D9] max-h-40"}`}>
                  {entry.text.split("\n").filter(l => l.trim()).join("\n")}
                </pre>
              ) : (
                <div className="text-[11px] font-mono" style={{ color: borderColor }}>
                  {entry.text.slice(0, 400)}
                </div>
              )}
            </div>
          );
        })}
        {/* Scroll anchor — auto-scrolls as new entries arrive */}
        <div ref={bottomCallback} />
      </div>
    </div>
  );
}

function ReflectionCard({ events }: { events: RunEvent[] }) {
  const lastReflection = [...events].reverse().find(e => e.event_type === "reflection");
  const reflection = lastReflection ? parseReflection((lastReflection.data.text as string) || "") : null;

  return (
    <div className="px-3 py-2 border-b border-[#1C1F26] bg-[#110a1a]">
      <div className="flex items-center gap-1.5 mb-1.5">
        <span className="text-[#A855F7] text-sm">{"\u25C6"}</span>
        <span className="text-[9px] font-semibold uppercase tracking-wider text-[#A855F7]">
          Latest Reflection
        </span>
      </div>
      {reflection ? (
        <div className="space-y-1">
          {reflection.pattern && (
            <div className="text-[11px]">
              <span className="text-[#A855F7] font-bold uppercase text-[9px] inline-block w-16">Pattern</span>
              <span className="text-[#C4B5FD]">{reflection.pattern}</span>
            </div>
          )}
          {reflection.rootCause && (
            <div className="text-[11px]">
              <span className="text-[#F59E0B] font-bold uppercase text-[9px] inline-block w-16">Cause</span>
              <span className="text-[#9CA3AF]">{reflection.rootCause}</span>
            </div>
          )}
          {(reflection.banned || reflection.preferred || reflection.lesson) && (
            <div className="mt-1 pt-1 border-t border-[#2a1540] space-y-0.5 text-[10px] font-mono">
              {reflection.banned && (
                <div><span className="text-[#EF4444] font-bold">BAN</span> <span className="text-[#6B7280]">{reflection.banned}</span></div>
              )}
              {reflection.lesson && (
                <div><span className="text-[#3B82F6] font-bold">LESSON</span> <span className="text-[#6B7280]">{reflection.lesson}</span></div>
              )}
            </div>
          )}
        </div>
      ) : (
        <div className="text-[10px] text-[#4B5563] italic">
          Reflector fires after the first failure
        </div>
      )}
    </div>
  );
}

function OutcomeFeed({
  events,
  skillUI,
  onSelectItem,
}: {
  events: RunEvent[];
  skillUI: SkillUI;
  onSelectItem: (id: string) => void;
}) {
  const outcomes: Array<{
    type: string;
    ruleId: string;
    attempts?: number;
    wallTime?: number;
    reason?: string;
    elapsed_s: number;
  }> = [];

  for (const e of events) {
    if (e.event_type === "remediated") {
      outcomes.push({
        type: "fixed",
        ruleId: e.data.rule_id as string,
        attempts: e.data.attempt as number,
        wallTime: e.data.wall_time_s as number,
        elapsed_s: e.elapsed_s,
      });
    } else if (e.event_type === "escalated") {
      outcomes.push({
        type: "escalated",
        ruleId: e.data.rule_id as string,
        attempts: e.data.attempts as number,
        wallTime: e.data.wall_time_s as number,
        reason: e.data.reason as string,
        elapsed_s: e.elapsed_s,
      });
    } else if (e.event_type === "skip") {
      outcomes.push({
        type: "skipped",
        ruleId: e.data.rule_id as string,
        elapsed_s: e.elapsed_s,
      });
    }
  }

  const recent = outcomes.slice(-30).reverse();

  return (
    <div className="flex-1 min-h-0 overflow-y-auto px-3 py-2">
      <div className="text-[9px] uppercase tracking-wider text-[#4B5563] mb-1.5">
        Outcomes ({outcomes.length} total)
      </div>
      {recent.length === 0 ? (
        <div className="text-[10px] text-[#4B5563] italic">Awaiting first outcome\u2026</div>
      ) : (
        <div className="space-y-1">
          {recent.map((o, i) => {
            const outcomeDef = skillUI.outcomes.find(x => x.type === o.type);
            const color = outcomeDef?.color || (o.type === "fixed" ? "#22C55E" : o.type === "escalated" ? "#EF4444" : "#6B7280");
            const icon = o.type === "fixed" ? "\u2713" : o.type === "escalated" ? "\u2717" : "\u2298";
            return (
              <button
                key={`${o.ruleId}-${i}`}
                onClick={() => onSelectItem(o.ruleId)}
                className="w-full text-left rounded-sm px-2 py-1.5 hover:bg-[#1A1D24] transition-colors group"
                style={{ borderLeft: `2px solid ${color}` }}
              >
                <div className="flex items-center gap-1.5">
                  <span className="text-xs font-bold" style={{ color }}>{icon}</span>
                  <span className="text-[11px] font-mono text-[#E8EAED] truncate flex-1">
                    {shortId(o.ruleId, skillUI.id_prefix_strip)}
                  </span>
                  {o.attempts && (
                    <span className="text-[9px] text-[#6B7280] tabular-nums shrink-0">
                      {o.attempts}att
                    </span>
                  )}
                  {o.wallTime && (
                    <span className="text-[9px] text-[#6B7280] tabular-nums shrink-0">
                      {formatTime(o.wallTime)}
                    </span>
                  )}
                </div>
                {o.reason && (
                  <div className="text-[9px] text-[#6B7280] mt-0.5 pl-4 truncate">
                    {o.reason}
                  </div>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ItemDetail({
  detail,
  skillUI,
  onClose,
}: {
  detail: WorkItemDetail;
  skillUI: SkillUI;
  onClose: () => void;
}) {
  const stateColors: Record<string, string> = {
    completed: "#22C55E",
    remediated: "#22C55E",
    escalated: "#EF4444",
    skipped: "#6B7280",
    active: "#22D3EE",
    queued: "#4B5563",
  };
  const color = stateColors[detail.state] || "#6B7280";

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      {/* Header */}
      <div className="px-3 py-2 border-b border-[#1C1F26] sticky top-0 bg-[#12141A] z-10">
        <div className="flex items-center justify-between mb-1">
          <span
            className="px-1.5 py-0.5 rounded-sm text-[9px] font-bold uppercase"
            style={{ background: `${color}20`, color }}
          >
            {detail.state}
          </span>
          <button
            onClick={onClose}
            className="text-[#6B7280] hover:text-[#E8EAED] text-sm px-1"
          >
            {"\u2715"}
          </button>
        </div>
        <div className="text-[13px] font-mono font-bold text-[#E8EAED] truncate">
          {shortId(detail.id, skillUI.id_prefix_strip)}
        </div>
        {detail.title && (
          <div className="text-[11px] text-[#9CA3AF] mt-0.5">{detail.title}</div>
        )}
        <div className="flex items-center gap-3 mt-1.5 text-[10px] text-[#6B7280]">
          <span className="px-1.5 py-0.5 rounded-sm bg-[#1A1D24]">{detail.category}</span>
          <span className="tabular-nums">{detail.attempts} attempts</span>
          <span className="tabular-nums">{formatTime(detail.wall_time_s)}</span>
        </div>
      </div>

      {/* Escalation reason */}
      {detail.escalation_reason && (
        <div className="mx-3 mt-2 px-2 py-1.5 rounded-sm text-[11px]" style={{ background: "#EF444415", borderLeft: "2px solid #EF4444" }}>
          <span className="text-[#EF4444] font-bold text-[9px] uppercase mr-1.5">Escalation</span>
          <span className="text-[#F87171]">{detail.escalation_reason}</span>
        </div>
      )}

      {/* Approaches tried */}
      {detail.approaches_tried.length > 0 && (
        <div className="px-3 py-2 border-b border-[#1C1F26]">
          <div className="text-[9px] uppercase tracking-wider text-[#4B5563] mb-1.5">
            Approaches Tried ({detail.approaches_tried.length})
          </div>
          <div className="space-y-1">
            {detail.approaches_tried.map((approach, i) => (
              <div key={i} className="flex gap-2 text-[11px]">
                <span className="text-[#4B5563] shrink-0 tabular-nums w-4 text-right">{i + 1}.</span>
                <span className="text-[#9CA3AF] leading-relaxed">{approach}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Reflections */}
      {detail.reflections.length > 0 && (
        <div className="px-3 py-2 border-b border-[#1C1F26]">
          <div className="text-[9px] uppercase tracking-wider text-[#A855F7] mb-1.5">
            Reflections ({detail.reflections.length})
          </div>
          <div className="space-y-2">
            {detail.reflections.map((ref, i) => {
              const parsed = parseReflection(ref.text);
              return (
                <div key={i} className="text-[10px] bg-[#110a1a] rounded-sm px-2 py-1.5">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-[#A855F7] text-[9px]">att {ref.attempt}</span>
                    {ref.plateaued && (
                      <span className="text-[#F59E0B] text-[8px] uppercase">plateau</span>
                    )}
                  </div>
                  {parsed.pattern && <div className="text-[#C4B5FD]">{parsed.pattern}</div>}
                  {parsed.lesson && <div className="text-[#6B7280] mt-0.5">Lesson: {parsed.lesson}</div>}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Architect reengagements */}
      {detail.reengagements.length > 0 && (
        <div className="px-3 py-2">
          <div className="text-[9px] uppercase tracking-wider text-[#3B82F6] mb-1.5">
            Architect Verdicts ({detail.reengagements.length})
          </div>
          <div className="space-y-1">
            {detail.reengagements.map((r, i) => {
              const vColor = r.verdict === "ESCALATE" ? "#EF4444" : r.verdict === "PIVOT" ? "#F59E0B" : "#22C55E";
              return (
                <div key={i} className="flex items-center gap-2 text-[10px]">
                  <span className="font-bold tabular-nums" style={{ color: vColor }}>
                    {r.verdict}
                  </span>
                  <span className="text-[#6B7280]">
                    at attempt {r.attempt} ({r.trigger})
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// -- Main FocusPanel --

export default function FocusPanel({
  events,
  skillUI = DEFAULT_SKILL_UI,
  selectedItemId,
  onSelectItem,
  onClose,
}: {
  events: RunEvent[];
  skillUI: SkillUI;
  selectedItemId: string | null;
  onSelectItem: (id: string | null) => void;
  onClose: () => void;
}) {
  // Build detail for selected item
  const detail = selectedItemId ? buildItemDetail(selectedItemId, events, skillUI) : null;

  return (
    <div className="flex-1 min-w-0 bg-[#12141A] flex flex-col overflow-hidden">
      {detail ? (
        <ItemDetail detail={detail} skillUI={skillUI} onClose={onClose} />
      ) : (
        <>
          {/* Agent conversation — the hero, takes all available space */}
          <AgentInsight events={events} />
        </>
      )}
    </div>
  );
}
