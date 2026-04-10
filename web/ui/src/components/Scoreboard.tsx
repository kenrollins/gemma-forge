"use client";

import { GpuState, RunEvent } from "./types";

function StatCard({ label, value, unit, color }: { label: string; value: string | number; unit?: string; color: string }) {
  return (
    <div className="bg-[#12141A] border border-[#1C1F26] rounded-sm p-3 flex flex-col items-center justify-center min-w-[110px]">
      <div className="flex items-baseline gap-1">
        <span className="font-mono text-3xl font-bold leading-none" style={{ color }}>{value}</span>
        {unit && <span className="text-[10px] text-[#6B7280]">{unit}</span>}
      </div>
      <div className="text-[10px] font-semibold uppercase tracking-wider text-[#6B7280] mt-1.5">{label}</div>
    </div>
  );
}

function GpuPairCard({ gpus, label, model, color, parallelism }: {
  gpus: GpuState[]; label: string; model: string; color: string; parallelism: string;
}) {
  const totalUsed = gpus.reduce((s, g) => s + g.memory_used_mib, 0);
  const totalCap = gpus.reduce((s, g) => s + g.memory_total_mib, 0);
  const pct = totalCap > 0 ? Math.round((totalUsed / totalCap) * 100) : 0;
  const avgTemp = gpus.length > 0 ? Math.round(gpus.reduce((s, g) => s + (g.temperature_c || 0), 0) / gpus.length) : 0;
  const totalPower = gpus.reduce((s, g) => s + (g.power_w || 0), 0);
  const gpuLabel = gpus.map(g => g.index).join("+");

  return (
    <div className="bg-[#12141A] border border-[#1C1F26] rounded-sm p-3 flex-1">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-mono text-[#6B7280]">GPU {gpuLabel}</span>
          <span className="text-[9px] font-mono px-1 py-0.5 bg-[#1A1D24] rounded-sm text-[#6B7280]">{parallelism}</span>
        </div>
        <span className="text-[10px] font-bold uppercase tracking-wider" style={{ color }}>{label}</span>
      </div>
      <div className="h-3 bg-[#1A1D24] rounded-sm overflow-hidden mb-1.5 relative">
        <div className="h-full rounded-sm transition-all duration-700" style={{ width: `${pct}%`, background: `linear-gradient(90deg, ${color}cc, ${color})` }} />
        <span className="absolute inset-0 flex items-center justify-center text-[9px] font-mono font-bold text-white/80">{pct}%</span>
      </div>
      <div className="flex justify-between text-[10px] font-mono text-[#6B7280]">
        <span>{(totalUsed / 1024).toFixed(1)} / {(totalCap / 1024).toFixed(1)} GiB</span>
        <span>{avgTemp}°C</span>
        <span>{totalPower.toFixed(0)}W</span>
      </div>
      <div className="text-[9px] font-mono text-[#4B5563] mt-1 truncate">{model}</div>
      <div className="flex gap-1.5 mt-2">
        {gpus.map(g => {
          const gPct = g.memory_total_mib > 0 ? Math.round((g.memory_used_mib / g.memory_total_mib) * 100) : 0;
          return (
            <div key={g.index} className="flex-1">
              <div className="text-[8px] font-mono text-[#4B5563] mb-0.5">GPU {g.index} · {g.temperature_c || 0}°C</div>
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

export default function Scoreboard({ events, gpus, connected, elapsed }: {
  events: RunEvent[]; gpus: GpuState[]; connected: boolean; elapsed: number;
}) {
  const iterations = events.filter(e => e.event_type === "iteration_start").length;
  const fixed = events.filter(e => e.event_type === "agent_response" && ((e.data.text as string) || "").includes("AUDIT_PASS")).length;
  const reverted = events.filter(e => e.event_type === "revert").length;
  const skipped = events.filter(e => e.event_type === "skip").length;
  const reflections = events.filter(e => e.event_type === "reflection").length;
  
  const remaining = events.filter(e => e.event_type === "iteration_start").slice(-1)[0]?.data?.failing || "—";
  
  const totalTokens = events
    .filter(e => e.event_type === "agent_response" && e.data.tokens)
    .reduce((sum, e) => sum + ((e.data.tokens as any)?.completion || 0) + ((e.data.tokens as any)?.prompt || 0), 0);

  const mins = Math.floor(elapsed / 60);
  const secs = Math.floor(elapsed % 60);

  return (
    <div className="shrink-0 p-4 border-b border-[#1C1F26] bg-[#0D0F14]">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <h2 className="text-xs font-semibold uppercase tracking-[0.15em] text-[#6B7280]">Ralph Loop — STIG Remediation</h2>
          <span className="text-[10px] font-mono font-bold px-2 py-0.5 rounded-sm" style={{
            background: connected ? "rgba(34,197,94,0.12)" : "rgba(107,114,128,0.12)",
            color: connected ? "#22C55E" : "#6B7280",
          }}>{connected ? "● LIVE" : "OFFLINE"}</span>
        </div>
        <span className="text-[10px] font-mono text-[#4B5563]">Dell PowerEdge XR7620 · 4×NVIDIA L4 · No NVLink</span>
      </div>

      <div className="flex gap-2 mb-3">
        <StatCard label="Iteration" value={iterations} color="#3B82F6" />
        <StatCard label="Remediated" value={fixed} color="#22C55E" />
        <StatCard label="Reverted" value={reverted} color="#EF4444" />
        <StatCard label="Skipped" value={skipped} color="#6B7280" />
        <StatCard label="Reflections" value={reflections} color="#A855F7" />
        <StatCard label="Remaining" value={remaining} color="#F59E0B" />
        <StatCard label="Tokens" value={totalTokens > 1000 ? `${(totalTokens/1000).toFixed(1)}k` : totalTokens} color="#9CA3AF" />
        <StatCard label="Elapsed" value={`${mins}:${secs.toString().padStart(2,'0')}`} color="#9CA3AF" />
      </div>

      <div className="flex gap-3">
        <GpuPairCard gpus={gpus.filter(g => g.index <= 1)} label="Architect / Worker / Reflector" model="Gemma 4 31B-IT NVFP4" color="#3B82F6" parallelism="TP=2" />
        <GpuPairCard gpus={gpus.filter(g => g.index >= 2)} label="Auditor" model="Nemotron-3-Nano-30B NVFP4" color="#22C55E" parallelism="PP=2" />
      </div>
    </div>
  );
}
