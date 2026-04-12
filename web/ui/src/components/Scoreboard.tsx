"use client";

import { GpuState, RunEvent, SkillUI, DEFAULT_SKILL_UI } from "./types";

function StatCard({ label, value, unit, color }: { label: string; value: string | number; unit?: string; color: string }) {
  return (
    <div className="bg-[#12141A] border border-[#2A2F3A] rounded px-3 py-1.5 flex flex-col items-center justify-center min-w-[72px]">
      <div className="flex items-baseline gap-0.5">
        <span className="font-mono text-xl font-light leading-none tabular-nums" style={{ color }}>{value}</span>
        {unit && <span className="text-[8px] text-[#4B5563]">{unit}</span>}
      </div>
      <div className="text-[8px] uppercase tracking-[0.12em] text-[#4B5563] mt-0.5">{label}</div>
    </div>
  );
}

function GpuStrip({ gpus, color, hasTelemetry }: { gpus: GpuState[]; color: string; hasTelemetry: boolean }) {
  const totalUsed = gpus.reduce((s, g) => s + g.memory_used_mib, 0);
  const totalCap = gpus.reduce((s, g) => s + g.memory_total_mib, 0);
  const pct = totalCap > 0 ? Math.round((totalUsed / totalCap) * 100) : 0;
  const avgTemp = gpus.length > 0 ? Math.round(gpus.reduce((s, g) => s + (g.temperature_c || 0), 0) / gpus.length) : 0;
  const totalPower = gpus.reduce((s, g) => s + (g.power_w || 0), 0);
  const avgUtil = gpus.length > 0 ? Math.round(gpus.reduce((s, g) => s + (g.utilization_pct || 0), 0) / gpus.length) : 0;

  return (
    <div className="bg-[#12141A] border border-[#1C1F26] rounded-sm px-3 py-2 flex-1">
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-2">
          <span className="text-[9px] font-mono text-[#6B7280]">GPU 0+1+2+3</span>
          <span className="text-[8px] font-mono px-1 py-0.5 bg-[#1A1D24] rounded-sm text-[#6B7280]">TP=4 bf16</span>
          {!hasTelemetry && (
            <span className="text-[8px] font-mono px-1 py-0.5 bg-[#1A1D24] rounded-sm text-[#6B7280]">NO TELEMETRY</span>
          )}
        </div>
        <div className="flex items-center gap-3 text-[9px] font-mono text-[#6B7280]">
          {hasTelemetry ? (
            <>
              <span>{(totalUsed / 1024).toFixed(1)}/{(totalCap / 1024).toFixed(1)} GiB</span>
              <span>{avgUtil}% util</span>
              <span>{avgTemp}°C</span>
              <span>{totalPower.toFixed(0)}W</span>
            </>
          ) : (
            <span className="italic">replay mode — GPU data unavailable</span>
          )}
        </div>
      </div>
      <div className="h-2.5 bg-[#1A1D24] rounded-sm overflow-hidden relative">
        <div
          className="h-full rounded-sm transition-all duration-700"
          style={{ width: `${hasTelemetry ? pct : 0}%`, background: `linear-gradient(90deg, ${color}cc, ${color})` }}
        />
        <span className="absolute inset-0 flex items-center justify-center text-[8px] font-mono font-bold text-white/80">
          {hasTelemetry ? `${pct}%` : "—"}
        </span>
      </div>
      <div className="flex gap-1.5 mt-1.5">
        {gpus.map(g => {
          const gPct = hasTelemetry && g.memory_total_mib > 0 ? Math.round((g.memory_used_mib / g.memory_total_mib) * 100) : 0;
          return (
            <div key={g.index} className="flex-1">
              <div className="h-1 bg-[#1A1D24] rounded-sm overflow-hidden">
                <div className="h-full rounded-sm transition-all duration-500" style={{ width: `${gPct}%`, background: color }} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function Scoreboard({ events, gpus, connected, elapsed, skillUI = DEFAULT_SKILL_UI }: {
  events: RunEvent[]; gpus: GpuState[]; connected: boolean; elapsed: number; skillUI?: SkillUI;
}) {
  // GPU has telemetry if any GPU has non-zero memory or the event stream has gpu_state
  const hasGpuTelemetry = gpus.some(g => g.memory_used_mib > 0 || g.utilization_pct > 0 || (g.temperature_c ?? 0) > 0);
  const iterations = events.filter(e => e.event_type === "iteration_start").length;
  const fixed = events.filter(e => e.event_type === "remediated").length;
  const reverted = events.filter(e => e.event_type === "revert").length;
  const escalated = events.filter(e => e.event_type === "escalated").length;
  const skipped = events.filter(e => e.event_type === "skip").length;
  const reflections = events.filter(e => e.event_type === "reflection").length;

  const remaining = events.filter(e => e.event_type === "iteration_start").slice(-1)[0]?.data?.failing || "\u2014";

  const totalTokens = events
    .filter(e => e.event_type === "agent_response" && e.data.tokens)
    .reduce((sum, e) => sum + ((e.data.tokens as any)?.completion || 0) + ((e.data.tokens as any)?.prompt || 0), 0);

  // Average tok/s across all LLM calls
  const timedResponses = events.filter(e => e.event_type === "agent_response" && e.data.timing);
  const avgTps = timedResponses.length > 0
    ? (timedResponses.reduce((s, e) => s + ((e.data.timing as any)?.tok_per_sec || 0), 0) / timedResponses.length).toFixed(1)
    : "\u2014";

  const mins = Math.floor(elapsed / 60);
  const secs = Math.floor(elapsed % 60);

  return (
    <div className="shrink-0 px-4 py-2.5 border-b border-[#1C1F26] bg-[#0D0F14]">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-3">
          <h2 className="text-xs font-semibold uppercase tracking-[0.15em] text-[#6B7280]">Ralph Loop — {skillUI.title}</h2>
          <span className="text-[10px] font-mono font-bold px-2 py-0.5 rounded-sm" style={{
            background: connected ? "rgba(34,197,94,0.12)" : "rgba(107,114,128,0.12)",
            color: connected ? "#22C55E" : "#6B7280",
          }}>{connected ? "\u25CF LIVE" : "OFFLINE"}</span>
        </div>
        <span className="text-[10px] font-mono text-[#4B5563]">Gemma 4 31B bf16 · TP=4 · 4×L4</span>
      </div>

      <div className="flex gap-2 items-stretch">
        {/* Stats row */}
        <StatCard label="Iteration" value={iterations} color="#3B82F6" />
        <StatCard label={skillUI.fixed_label} value={fixed} color="#22C55E" />
        <StatCard label="Retries" value={reverted} color="#F59E0B" />
        <StatCard label="Escalated" value={escalated} color="#EF4444" />
        <StatCard label="Reflections" value={reflections} color="#A855F7" />
        <StatCard label="Remaining" value={remaining} color="#9CA3AF" />
        <StatCard label="Avg tok/s" value={avgTps} color="#3B82F6" />
        <StatCard label="Tokens" value={totalTokens > 1000 ? `${(totalTokens/1000).toFixed(0)}k` : totalTokens} color="#9CA3AF" />
        <StatCard label="Elapsed" value={`${mins}:${secs.toString().padStart(2,'0')}`} color="#9CA3AF" />

        {/* GPU strip — compact, inline with stats */}
        <GpuStrip gpus={gpus} color="#3B82F6" hasTelemetry={hasGpuTelemetry} />
      </div>
    </div>
  );
}
