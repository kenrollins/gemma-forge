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

// Build WorkItemDetail from events for a specific item.
// Walks the full event stream sequentially, tracking which rule is active,
// so we capture full agent_response text (not the truncated summaries).
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

  // Track which rule is currently active so we can capture agent_response
  // events that don't carry rule_id themselves
  let inTargetRule = false;
  let currentAttemptNum = 0;

  for (const e of events) {
    // rule_selected marks the boundary — entering or leaving our target rule
    if (e.event_type === "rule_selected") {
      if (e.data.rule_id === itemId) {
        inTargetRule = true;
        title = e.data.title as string || "";
        category = e.data.category as string || "";
      } else if (inTargetRule) {
        // A different rule was selected — we've left our target's window
        break;
      }
      continue;
    }

    if (!inTargetRule) continue;

    // Events with rule_id — direct match
    if (e.event_type === "rule_complete") {
      state = e.data.outcome as string || "unknown";
      attempts = e.data.attempts as number || 0;
      wallTime = e.data.wall_time_s as number || 0;
      escalationReason = e.data.escalation_reason as string | undefined;
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
    if (e.event_type === "attempt_start") {
      currentAttemptNum = e.data.attempt as number || 0;
    }
    if (e.event_type === "reflection") {
      reflections.push({
        text: e.data.text as string || "",
        attempt: e.data.attempt as number || currentAttemptNum,
        plateaued: e.data.plateaued as boolean || false,
      });
    }
    if (e.event_type === "architect_reengaged") {
      reengagements.push({
        verdict: e.data.verdict as string || "?",
        trigger: e.data.trigger as string || "?",
        attempt: e.data.attempt as number || currentAttemptNum,
      });
    }

    // agent_response — full untruncated text. These don't have rule_id
    // but we know they belong to our rule because we're inside its window.
    if (e.event_type === "agent_response" && e.data?.text) {
      if (e.agent === "worker") {
        // Each worker response is an approach — capture the full text
        approachesTried.push(e.data.text as string);
      }
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
      // Skip reflector agent_response — the structured reflection event
      // captures the same content in parsed form. Also skip architect
      // responses that will be replaced by architect_reengaged events
      // (we can't know yet, so we add them and pop later).
      if (e.agent === "reflector") {
        // Don't add to conversation — the reflection event handles it
      } else {
        conversation.push({
          agent: e.agent,
          type: "response",
          text: e.data.text as string,
          elapsed_s: e.elapsed_s,
          tokens,
          tokPerSec: timing?.tok_per_sec,
        });
      }
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
      // The architect reengagement text was already captured as an
      // agent_response event (same turn). Remove that duplicate and
      // replace with the structured verdict display.
      const lastIdx = conversation.length - 1;
      if (lastIdx >= 0 && conversation[lastIdx].agent === "architect" && conversation[lastIdx].type === "response") {
        conversation.pop();
      }
      const verdict = e.data.verdict as string;
      conversation.push({
        agent: "architect",
        type: "verdict",
        text: e.data.full_response as string || `Verdict: ${verdict}`,
        elapsed_s: e.elapsed_s,
      });
    } else if (e.event_type === "reflection") {
      latestReflectionText = e.data.text as string || "";
      // Reflector agent_response is already skipped above, so no
      // duplicate to pop. Just add the structured version.
      const parsed = parseReflection(latestReflectionText);
      const parts: string[] = [];
      if (parsed.pattern) parts.push(`Pattern: ${parsed.pattern}`);
      if (parsed.rootCause) parts.push(`Cause: ${parsed.rootCause}`);
      if (parsed.lesson) parts.push(`Lesson: ${parsed.lesson}`);
      if (parsed.banned) parts.push(`BAN: ${parsed.banned}`);
      conversation.push({
        agent: "reflector",
        type: "reflection",
        text: parts.join("\n") || latestReflectionText.slice(0, 400),
        elapsed_s: e.elapsed_s,
      });
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
    if (entry.type === "eval_fail" || entry.type === "revert") return "#EF4444";
    if (entry.type === "escalated") return "#F59E0B";
    if (entry.type === "verdict") return "#3B82F6";
    return AGENT_COLORS[entry.agent] || "#6B7280";
  };

  // allEntries is just the conversation — reflections are already inline
  // from the main loop (added when reflection events are encountered,
  // with the duplicate agent_response popped).
  const allEntries = conversation;

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

          // Architect verdict — structured display
          if (entry.type === "verdict") {
            // Parse VERDICT / REASONING / NEW_PLAN from the text
            const verdictMatch = entry.text.match(/VERDICT:\s*(CONTINUE|PIVOT|ESCALATE)/i);
            const reasoningMatch = entry.text.match(/REASONING:\s*([\s\S]*?)(?=NEW_PLAN:|$)/i);
            const planMatch = entry.text.match(/NEW_PLAN:\s*([\s\S]*?)$/i);
            const verdict = verdictMatch ? verdictMatch[1].toUpperCase() : "?";
            const reasoning = reasoningMatch ? reasoningMatch[1].trim() : "";
            const plan = planMatch ? planMatch[1].trim() : "";
            const vColor = verdict === "ESCALATE" ? "#EF4444" : verdict === "PIVOT" ? "#F59E0B" : "#22C55E";

            return (
              <div
                key={i}
                className="rounded px-3 py-2.5 mt-1"
                style={{ borderLeft: `3px solid ${vColor}`, background: `${vColor}08` }}
              >
                <div className="flex items-center gap-2 mb-2">
                  <div className="w-2 h-2 rounded-full" style={{ background: "#3B82F6" }} />
                  <span className="text-[10px] uppercase tracking-wider font-bold text-[#3B82F6]">architect re-engagement</span>
                  <span className="font-bold font-mono text-[12px] px-2 py-0.5 rounded" style={{ color: vColor, background: `${vColor}20` }}>
                    {verdict}
                  </span>
                  <span className="text-[9px] text-[#4B5563] ml-auto tabular-nums">{entry.elapsed_s.toFixed(0)}s</span>
                </div>
                {reasoning && (
                  <div className="text-[12px] text-[#C9D1D9] leading-relaxed mb-2">{reasoning}</div>
                )}
                {plan && (
                  <div className="mt-2 pt-2 border-t border-[#2A2E38]">
                    <div className="text-[10px] uppercase tracking-wider text-[#6B7280] font-semibold mb-1">New Plan</div>
                    <pre className="text-[12px] text-[#E8EAED] font-mono whitespace-pre-wrap leading-relaxed">{plan}</pre>
                  </div>
                )}
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
              {/* Content — colored per agent identity */}
              {isResponse ? (
                <pre
                  className={`font-mono whitespace-pre-wrap leading-relaxed overflow-y-auto
                    ${isArchitect ? "text-[12px] max-h-64" : "text-[11px] max-h-40"}`}
                  style={{ color: `color-mix(in srgb, ${agentColor} 40%, #E8EAED)` }}
                >
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
            const color = outcomeDef?.color || (o.type === "fixed" ? "#22C55E" : o.type === "escalated" ? "#F59E0B" : "#6B7280");
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
    escalated: "#F59E0B",
    skipped: "#6B7280",
    active: "#22D3EE",
    queued: "#4B5563",
  };
  const color = stateColors[detail.state] || "#6B7280";

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      {/* Header — prominent, full width */}
      <div className="px-5 py-4 border-b border-[#1C1F26] sticky top-0 bg-[#12141A] z-10">
        <div className="flex items-start justify-between">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 mb-2">
              <span
                className="px-2 py-1 rounded text-[11px] font-bold uppercase"
                style={{ background: `${color}20`, color }}
              >
                {detail.state}
              </span>
              <span className="px-2 py-1 rounded text-[11px] font-semibold bg-[#1A1D24] text-[#60A5FA]">
                {detail.category}
              </span>
              <span className="text-[12px] text-[#6B7280] tabular-nums">{detail.attempts} attempt{detail.attempts !== 1 ? "s" : ""}</span>
              <span className="text-[12px] text-[#6B7280] tabular-nums">{formatTime(detail.wall_time_s)}</span>
            </div>
            <div className="text-[16px] font-mono font-bold text-[#E8EAED]">
              {shortId(detail.id, skillUI.id_prefix_strip)}
            </div>
            {detail.title && (
              <div className="text-[13px] text-[#9CA3AF] mt-1">{detail.title}</div>
            )}
          </div>
          <button
            onClick={onClose}
            className="text-[#6B7280] hover:text-[#E8EAED] text-lg px-2 py-1 shrink-0"
          >
            {"\u2715"}
          </button>
        </div>
      </div>

      {/* Escalation reason — prominent banner */}
      {detail.escalation_reason && (
        <div className="mx-5 mt-3 px-4 py-3 rounded" style={{ background: "#F59E0B12", borderLeft: "3px solid #F59E0B" }}>
          <div className="text-[10px] text-[#F59E0B] font-bold uppercase mb-1">Escalation Reason</div>
          <div className="text-[13px] text-[#FBBF24]">{detail.escalation_reason}</div>
        </div>
      )}

      {/* Approaches tried */}
      {detail.approaches_tried.length > 0 && (
        <div className="px-5 py-3 border-b border-[#1C1F26]">
          <div className="text-[11px] uppercase tracking-wider text-[#6B7280] font-semibold mb-2">
            Approaches Tried ({detail.approaches_tried.length})
          </div>
          <div className="space-y-2">
            {detail.approaches_tried.map((approach, i) => (
              <div key={i} className="flex gap-3 text-[13px]">
                <span className="text-[#4B5563] shrink-0 tabular-nums w-5 text-right font-bold">{i + 1}.</span>
                <pre className="text-[#C9D1D9] leading-relaxed font-mono whitespace-pre-wrap flex-1">{approach}</pre>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Reflections */}
      {detail.reflections.length > 0 && (
        <div className="px-5 py-3 border-b border-[#1C1F26]">
          <div className="text-[11px] uppercase tracking-wider text-[#A855F7] font-semibold mb-2">
            Reflections ({detail.reflections.length})
          </div>
          <div className="space-y-3">
            {detail.reflections.map((ref, i) => {
              const parsed = parseReflection(ref.text);
              return (
                <div key={i} className="bg-[#110a1a] rounded px-4 py-3" style={{ borderLeft: "2px solid #A855F7" }}>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-[#A855F7] text-[11px] font-semibold">Attempt {ref.attempt}</span>
                    {ref.plateaued && (
                      <span className="text-[#F59E0B] text-[10px] uppercase px-1.5 py-0.5 rounded bg-[#F59E0B15]">plateau</span>
                    )}
                  </div>
                  {parsed.pattern && (
                    <div className="text-[12px] mb-1">
                      <span className="text-[#A855F7] font-bold text-[10px] inline-block w-16">Pattern</span>
                      <span className="text-[#C4B5FD]">{parsed.pattern}</span>
                    </div>
                  )}
                  {parsed.rootCause && (
                    <div className="text-[12px] mb-1">
                      <span className="text-[#F59E0B] font-bold text-[10px] inline-block w-16">Cause</span>
                      <span className="text-[#9CA3AF]">{parsed.rootCause}</span>
                    </div>
                  )}
                  {parsed.lesson && (
                    <div className="text-[11px] font-mono mt-1">
                      <span className="text-[#3B82F6] font-bold">LESSON</span>{" "}
                      <span className="text-[#6B7280]">{parsed.lesson}</span>
                    </div>
                  )}
                  {parsed.banned && (
                    <div className="text-[11px] font-mono">
                      <span className="text-[#EF4444] font-bold">BAN</span>{" "}
                      <span className="text-[#6B7280]">{parsed.banned}</span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Architect reengagements */}
      {detail.reengagements.length > 0 && (
        <div className="px-5 py-3">
          <div className="text-[11px] uppercase tracking-wider text-[#3B82F6] font-semibold mb-2">
            Architect Verdicts ({detail.reengagements.length})
          </div>
          <div className="space-y-2">
            {detail.reengagements.map((r, i) => {
              const vColor = r.verdict === "ESCALATE" ? "#EF4444" : r.verdict === "PIVOT" ? "#F59E0B" : "#22C55E";
              return (
                <div key={i} className="flex items-center gap-3 text-[12px] px-3 py-2 rounded bg-[#0D0F14]">
                  <span className="font-bold font-mono px-2 py-0.5 rounded" style={{ color: vColor, background: `${vColor}15` }}>
                    {r.verdict}
                  </span>
                  <span className="text-[#9CA3AF]">
                    at attempt {r.attempt}
                  </span>
                  <span className="text-[#6B7280]">
                    ({r.trigger})
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
