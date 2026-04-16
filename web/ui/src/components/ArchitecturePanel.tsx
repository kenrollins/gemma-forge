"use client";

/**
 * ArchitecturePanel — "this is all running on one box" at a glance.
 *
 * Four L4 GPUs serving Gemma 4 + four local services. The earlier
 * version rendered per-GPU utilization bars, but in practice those
 * bars sit near 100% whenever the harness is actively generating
 * tokens (which is most of the run), so they conveyed no information
 * and took real estate. Replaced with aggregate hardware stats
 * (VRAM, power, temp) that actually vary, plus a service row that
 * reinforces the sovereignty story (vLLM + VM + Postgres + Neo4j
 * all local).
 */

import { useMemo } from "react";
import type { GpuState, RunEvent, VllmState } from "./types";

export interface ArchitecturePanelProps {
  gpus: GpuState[];
  vllm?: VllmState | null;
  events: RunEvent[];
  connected: boolean;
}

export default function ArchitecturePanel({ gpus, vllm, events, connected }: ArchitecturePanelProps) {
  // Derive service health signals from the event stream. All services
  // run on the same host as the harness, so if we're connected and
  // events are flowing they're nominal. A more thorough check could
  // ask /api/health per service, but that's overkill for a demo panel
  // — if vLLM is down the whole harness stalls and tok/s drops to 0.
  const health = useMemo(() => {
    const last = events.length > 0 ? events[events.length - 1] : null;
    const lastEventAgeMs = last ? Date.now() - new Date(last.timestamp).getTime() : Infinity;

    let recentTokPerSec = 0;
    for (let i = events.length - 1; i >= Math.max(0, events.length - 20); i--) {
      const e = events[i];
      if (e.event_type === "agent_response" && e.data?.timing) {
        recentTokPerSec = (e.data.timing as { tok_per_sec?: number })?.tok_per_sec || 0;
        if (recentTokPerSec > 0) break;
      }
    }

    let recentVmActivity = false;
    for (let i = events.length - 1; i >= Math.max(0, events.length - 12); i--) {
      const e = events[i];
      if (e.event_type === "tool_call" || e.event_type === "tool_result") {
        recentVmActivity = true;
        break;
      }
    }

    return {
      vllm: { ok: connected && (recentTokPerSec > 0 || lastEventAgeMs < 30_000) },
      vm: { ok: connected && recentVmActivity },
      postgres: { ok: events.some((e) => e.event_type === "cross_run_hydration") },
      neo4j: { ok: true }, // TODO: real health check when the reflective tier is queried at prompt time
    };
  }, [events, connected]);

  // Aggregate hardware stats across the 4 GPUs. VRAM usage and power
  // draw are the stats that actually vary from moment to moment —
  // per-GPU utilization sits at 100% whenever the model is generating
  // and 0% between turns, which flips fast enough to be useless.
  const agg = useMemo(() => {
    const n = gpus.length || 1;
    const vramUsed = gpus.reduce((s, g) => s + (g.memory_used_mib || 0), 0);
    const vramTotal = gpus.reduce((s, g) => s + (g.memory_total_mib || 0), 0);
    const powerW = gpus.reduce((s, g) => s + (g.power_w || 0), 0);
    const avgTemp = gpus.reduce((s, g) => s + (g.temperature_c || 0), 0) / n;
    const hasTelemetry = gpus.some((g) => g.memory_used_mib > 0 || g.power_w > 0);
    return {
      vramUsedGb: vramUsed / 1024,
      vramTotalGb: vramTotal / 1024,
      vramPct: vramTotal > 0 ? (vramUsed / vramTotal) * 100 : 0,
      powerW,
      avgTemp: Math.round(avgTemp),
      hasTelemetry,
    };
  }, [gpus]);

  return (
    <div className="border-b border-[#1C1F26] bg-[#0A0C10] px-4 py-3">
      <div className="flex items-center gap-2 mb-2.5">
        <span className="text-[9px] font-semibold tracking-[0.2em] uppercase text-[#6B7280]">
          Hardware
        </span>
        <span className="text-[9px] text-[#4B5563] font-mono">XR7620 · 4×L4</span>
        <span
          className="ml-auto text-[9px] font-mono tracking-wider uppercase"
          style={{ color: connected ? "#22C55E" : "#4B5563" }}
          title="All components run on this single host. No cloud, no phone-home."
        >
          {connected ? "● local" : "○ idle"}
        </span>
      </div>

      {/* Single row: what's loaded, not how busy it is right now. */}
      <div className="flex items-baseline gap-2 mb-2">
        <span className="text-[11px] font-bold text-[#E8EAED]">Gemma 4 31B</span>
        <span className="text-[9px] font-mono text-[#6B7280] uppercase tracking-wider">
          bf16 · TP=4 · 96 GiB VRAM pool
        </span>
      </div>

      {/* Aggregate hardware stats that actually vary during a run. */}
      {agg.hasTelemetry ? (
        <div className="flex items-center gap-3 text-[10px] font-mono mb-3">
          <HwStat
            label="VRAM"
            value={`${agg.vramUsedGb.toFixed(1)} / ${agg.vramTotalGb.toFixed(0)} GiB`}
            pct={agg.vramPct}
            color="#3B82F6"
          />
          <HwStat label="Power" value={`${Math.round(agg.powerW)} W`} color="#F59E0B" />
          <HwStat label="Temp" value={`${agg.avgTemp}°C`} color="#22D3EE" />
        </div>
      ) : (
        <div className="text-[10px] text-[#4B5563] italic mb-3 font-mono">
          GPU telemetry available in live mode
        </div>
      )}

      {/* Model pressure (vLLM metrics). Optional — only present on
          runs that were logged with vllm_state. Explains WHY tok/s
          varies: high KV cache % means long contexts in flight;
          queue depth > 0 means back-pressure; prefix hit rate
          quantifies how much system-prompt reuse is saving us. */}
      {vllm && (
        <div className="flex items-center gap-3 text-[10px] font-mono mb-3">
          <HwStat
            label="KV Cache"
            value={`${vllm.kv_cache_pct.toFixed(1)}%`}
            pct={vllm.kv_cache_pct}
            color="#A855F7"
          />
          <HwStat
            label="Queue"
            value={`${vllm.running} · ${vllm.waiting}w`}
            color={vllm.waiting > 0 ? "#F59E0B" : "#22C55E"}
          />
          {vllm.prefix_hit_rate !== undefined && (
            <HwStat
              label="Prefix Hit"
              value={`${Math.round(vllm.prefix_hit_rate * 100)}%`}
              pct={vllm.prefix_hit_rate * 100}
              color="#22C55E"
            />
          )}
        </div>
      )}

      {/* Service indicator lights */}
      <div className="flex items-center gap-3 text-[9px] font-mono uppercase tracking-wider">
        <ServiceDot label="vLLM" ok={health.vllm.ok} title="Gemma 4 31B served locally via vLLM" />
        <ServiceDot label="VM" ok={health.vm.ok} title="Target Rocky 9 VM via libvirt" />
        <ServiceDot label="Postgres" ok={health.postgres.ok} title="Shared Supabase — episodic + semantic memory" />
        <ServiceDot label="Neo4j" ok={health.neo4j.ok} title="Reflective tier (Graphiti) for the dream pass" />
      </div>
    </div>
  );
}

function HwStat({
  label,
  value,
  pct,
  color,
}: {
  label: string;
  value: string;
  pct?: number;
  color: string;
}) {
  return (
    <div className="flex flex-col gap-0.5 flex-1 min-w-0">
      <span className="text-[8px] uppercase tracking-wider text-[#4B5563]">{label}</span>
      <span className="tabular-nums text-[#E8EAED] truncate" title={value}>
        {value}
      </span>
      {pct !== undefined && (
        <div className="h-0.5 rounded-full bg-[#151921] overflow-hidden">
          <div
            className="h-full transition-all duration-500"
            style={{ width: `${Math.min(pct, 100)}%`, background: color }}
          />
        </div>
      )}
    </div>
  );
}

function ServiceDot({ label, ok, title }: { label: string; ok: boolean; title: string }) {
  return (
    <span className="flex items-center gap-1" title={title}>
      <span
        className="block w-1.5 h-1.5 rounded-full"
        style={{
          background: ok ? "#22C55E" : "#4B5563",
          boxShadow: ok ? "0 0 6px rgba(34,197,94,0.6)" : undefined,
        }}
      />
      <span style={{ color: ok ? "#9CA3AF" : "#4B5563" }}>{label}</span>
    </span>
  );
}
