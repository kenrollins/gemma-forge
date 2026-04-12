"use client";

import { RunEvent, AGENT_COLORS, SkillUI, DEFAULT_SKILL_UI } from "./types";

function shortId(id: string, prefix: string): string {
  if (!id) return "—";
  if (prefix && id.startsWith(prefix)) return id.slice(prefix.length);
  return id;
}

function PipelineNode({ role, label, active, subtitle, isHarness }: {
  role: string; label: string; active: boolean; subtitle: string; isHarness?: boolean;
}) {
  const color = isHarness ? "#6B7280" : (AGENT_COLORS[role] || "#6B7280");
  return (
    <div className="flex flex-col items-center gap-0.5">
      <div
        className="w-20 h-11 rounded border-2 flex flex-col items-center justify-center transition-all duration-300"
        style={{
          borderColor: active ? color : "#2A2E38",
          background: active ? `color-mix(in srgb, ${color} 12%, #12141A)` : "#12141A",
          color: active ? color : "#6B7280",
          boxShadow: active ? `0 0 12px ${color}40` : "none",
        }}
      >
        <span className="text-[10px] font-bold uppercase tracking-wide">{label}</span>
        <span className="text-[7px] text-[#4B5563]">{subtitle}</span>
      </div>
      {active && <div className="w-1 h-1 rounded-full animate-pulse" style={{ background: color }} />}
    </div>
  );
}

function Arrow({ active }: { active?: boolean }) {
  return <div className={`text-sm px-0.5 ${active ? "text-[#9CA3AF]" : "text-[#2A2E38]"}`}>{"\u2192"}</div>;
}

export default function MissionHeader({
  events,
  skillUI = DEFAULT_SKILL_UI,
}: {
  events: RunEvent[];
  skillUI?: SkillUI;
}) {
  // ---- Current rule + attempt detection ----
  //
  // Find the most recent rule-related event. Its type tells us the state:
  //   - rule_selected / attempt_start: we're actively working on this rule
  //   - revert: we're retrying this rule
  //   - remediated / escalated: a rule just finished, we're selecting next
  //   - architect response with rule pattern in text: best fallback for old logs
  let currentRuleId = "";
  let currentAttempt = 0;
  let maxAttempts = 3;
  let ruleClosed = false;

  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    const eRuleId = typeof e.data?.rule_id === "string" ? e.data.rule_id : "";

    if (e.event_type === "attempt_start" && eRuleId) {
      currentRuleId = eRuleId;
      currentAttempt = (e.data.attempt as number) || 1;
      maxAttempts = (e.data.max_attempts as number) || 3;
      break;
    }

    if (e.event_type === "rule_selected" && eRuleId) {
      currentRuleId = eRuleId;
      break;
    }

    if (e.event_type === "revert" && eRuleId) {
      currentRuleId = eRuleId;
      // Will be working on this rule; attempt count backfills below
      break;
    }

    if ((e.event_type === "remediated" || e.event_type === "escalated") && eRuleId) {
      // Most recent activity is a closed rule. We're between rules (architect about to pick next)
      ruleClosed = true;
      break;
    }

    // Architect text fallback for runs without any rule_id-tagged events yet
    if (e.event_type === "agent_response" && e.agent === "architect") {
      const text = (e.data.text as string) || "";
      const match = text.match(/xccdf_org\.ssgproject\.content_rule_[a-z0-9_]+/);
      if (match) {
        currentRuleId = match[0];
        break;
      }
    }
  }

  // Backfill attempt count by counting reverts for this rule since the last rule_selected
  if (currentRuleId && !currentAttempt) {
    let reverts = 0;
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i];
      if (e.event_type === "rule_selected" && e.data.rule_id === currentRuleId) break;
      if (e.event_type === "remediated" && e.data.rule_id === currentRuleId) break;
      if (e.event_type === "escalated" && e.data.rule_id === currentRuleId) break;
      if (e.event_type === "revert" && e.data.rule_id === currentRuleId) reverts++;
    }
    currentAttempt = reverts + 1;
  }

  // ---- Active agent ----
  const latest = events[events.length - 1];
  const activeAgent = latest?.agent || "none";
  const agentColor = AGENT_COLORS[activeAgent] || "#6B7280";

  // Pipeline node active states
  const architectActive = activeAgent === "architect";
  const workerActive = activeAgent === "worker";
  const evalActive = activeAgent === "harness" || activeAgent === "system";
  const reflectorActive = activeAgent === "reflector";
  const hasRevert = events.some(e => e.event_type === "revert");

  // ---- Iteration + remaining ----
  const iterEvents = events.filter(e => e.event_type === "iteration_start");
  const currentIterData = iterEvents.length > 0 ? iterEvents[iterEvents.length - 1].data : {};

  // ---- Performance metrics (last LLM call) ----
  const lastTimed = [...events].reverse().find(
    e => e.event_type === "agent_response" && e.data.timing
  );
  const timing = lastTimed?.data?.timing as Record<string, number> | undefined;
  const timedAgent = lastTimed?.agent || "";
  const timedAgentColor = AGENT_COLORS[timedAgent] || "#3B82F6";

  const displayRuleId = ruleClosed
    ? "(selecting next rule...)"
    : currentRuleId
      ? shortId(currentRuleId, skillUI.id_prefix_strip)
      : "—";

  return (
    <div className="shrink-0 border-b border-[#1C1F26] bg-[#0D0F14] px-4 py-2.5">
      <div className="flex items-stretch gap-4">
        {/* Left: Current work item + attempt */}
        <div className="flex-1 min-w-0 max-w-[380px]">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[9px] font-semibold uppercase tracking-wider text-[#6B7280]">
              Current {skillUI.work_item}
            </span>
            {activeAgent !== "none" && (
              <span className="flex items-center gap-1">
                <div className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: agentColor }} />
                <span className="text-[9px] font-bold uppercase tracking-wider" style={{ color: agentColor }}>
                  {activeAgent}
                </span>
              </span>
            )}
          </div>
          <div className="font-mono text-sm font-bold text-[#E8EAED] truncate">
            {displayRuleId}
          </div>
          <div className="flex items-center gap-3 mt-1">
            <span className="text-[10px] text-[#4B5563]">
              Iter {currentIterData.iteration || "—"} · {currentIterData.failing || "—"} remaining
            </span>
            {currentRuleId && !ruleClosed && (
              <div className="flex items-center gap-1.5">
                <span className="text-[9px] text-[#6B7280] uppercase tracking-wider">Attempt {Math.min(currentAttempt || 1, maxAttempts)}/{maxAttempts}</span>
                <div className="flex gap-0.5">
                  {Array.from({ length: maxAttempts }, (_, i) => i + 1).map(n => (
                    <div
                      key={n}
                      className="w-2 h-2 rounded-full border"
                      style={{
                        borderColor: n <= (currentAttempt || 1) ? "#F59E0B" : "#2A2E38",
                        background: n < (currentAttempt || 1) ? "#F59E0B" : n === (currentAttempt || 1) ? "#F59E0B66" : "transparent",
                      }}
                    />
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Middle: Pipeline (left-aligned inside its bounds) */}
        <div className="flex items-center gap-1 shrink-0">
          <PipelineNode role="architect" label="Architect" active={architectActive} subtitle="Gemma 31B" />
          <Arrow active={workerActive} />
          <PipelineNode role="worker" label="Worker" active={workerActive} subtitle="Gemma 31B" />
          <Arrow active={evalActive} />
          <PipelineNode role="harness" label="Eval" active={evalActive} subtitle="Python" isHarness />
          <Arrow active={reflectorActive} />
          <PipelineNode role="reflector" label="Reflector" active={reflectorActive} subtitle="Gemma 31B" />
          {hasRevert && (
            <div className="flex items-center gap-1 ml-1">
              <span className="text-sm text-[#EF4444]">{"\u21A9"}</span>
              <span className="text-[8px] text-[#EF4444] font-bold uppercase">retry</span>
            </div>
          )}
        </div>

        {/* Right: Performance metrics */}
        <div className="flex items-center gap-2 ml-auto shrink-0">
          {/* Primary: tok/s */}
          <div className="bg-[#12141A] border border-[#1C1F26] rounded-sm px-3 py-1.5 text-center min-w-[80px]">
            <div className="font-mono text-xl font-bold leading-none" style={{ color: timedAgentColor }}>
              {timing?.tok_per_sec?.toFixed(1) || "—"}
            </div>
            <div className="text-[8px] text-[#6B7280] mt-0.5 uppercase tracking-wider">tok/s {timedAgent}</div>
          </div>
          {/* Secondary: TTFT + Turn */}
          <div className="flex flex-col gap-0.5">
            <div className="bg-[#12141A] border border-[#1C1F26] rounded-sm px-2 py-0.5 text-right min-w-[70px] flex items-center justify-between gap-2">
              <span className="text-[8px] text-[#6B7280] uppercase">TTFT</span>
              <span className="font-mono text-[11px] font-bold text-[#9CA3AF]">{timing?.ttft_s?.toFixed(1) || "—"}s</span>
            </div>
            <div className="bg-[#12141A] border border-[#1C1F26] rounded-sm px-2 py-0.5 text-right min-w-[70px] flex items-center justify-between gap-2">
              <span className="text-[8px] text-[#6B7280] uppercase">Turn</span>
              <span className="font-mono text-[11px] font-bold text-[#9CA3AF]">{timing?.turn_elapsed_s?.toFixed(1) || "—"}s</span>
            </div>
          </div>
          {/* Model */}
          <div className="bg-[#12141A] border border-[#1C1F26] rounded-sm px-2.5 py-1.5 text-right">
            <div className="text-[9px] font-mono font-bold text-[#9CA3AF] leading-tight">Gemma 4 31B bf16</div>
            <div className="text-[8px] font-mono text-[#6B7280] leading-tight">TP=4 · 4×L4 · Full Precision</div>
          </div>
        </div>
      </div>
    </div>
  );
}
