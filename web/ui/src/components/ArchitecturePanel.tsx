"use client";

/**
 * ArchitecturePanel — "this is all running on one box" at a glance.
 *
 * Four GPU bars with live utilization + service indicator lights for
 * the components that make the sovereign-edge story concrete: vLLM,
 * the VM target, Postgres (semantic memory), Neo4j (reflective
 * memory). When a presenter says "no cloud, no phone-home, everything
 * local," they can gesture at this panel and the audience sees the
 * stack.
 *
 * Deliberately compact: fits inside the right sidebar (340px) above
 * the TaskMap without stealing vertical real estate.
 */

import { useMemo } from "react";
import type { GpuState, RunEvent } from "./types";

export interface ArchitecturePanelProps {
  gpus: GpuState[];
  events: RunEvent[];
  connected: boolean;
}

export default function ArchitecturePanel({ gpus, events, connected }: ArchitecturePanelProps) {
  // Derive service health signals from the event stream. All services
  // run on the same host as the harness, so if we're connected and
  // events are flowing, they're nominal. A more thorough check could
  // ask /api/health per service, but that's overkill for a demo panel
  // — if vLLM is down the whole harness stalls and tok/s drops to 0.
  const health = useMemo(() => {
    const last = events.length > 0 ? events[events.length - 1] : null;
    const lastEventAgeMs = last ? Date.now() - new Date(last.timestamp).getTime() : Infinity;

    // Any recent tok/s signal from the last agent_response tells us
    // vLLM is answering. Stale signal = stale harness, not necessarily
    // a down service, but we mark it as idle either way.
    let recentTokPerSec = 0;
    for (let i = events.length - 1; i >= Math.max(0, events.length - 20); i--) {
      const e = events[i];
      if (e.event_type === "agent_response" && e.data?.timing) {
        recentTokPerSec = (e.data.timing as { tok_per_sec?: number })?.tok_per_sec || 0;
        if (recentTokPerSec > 0) break;
      }
    }

    // SSH activity on the VM: tool_call or tool_result events imply
    // the VM responded.
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
      // Postgres and Neo4j are touched at hydration + dream-pass time,
      // not continuously. If the run hydrated (we can see the event)
      // then Postgres is up. Neo4j is nominal as long as the dashboard
      // itself is up — it's where the dream pass writes.
      postgres: { ok: events.some((e) => e.event_type === "cross_run_hydration") },
      neo4j: { ok: true }, // TODO: wire a real health check when the reflective tier is queried at prompt time
    };
  }, [events, connected]);

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

      {/* GPU grid — one row per L4 with utilization and role */}
      <div className="flex flex-col gap-1.5 mb-3">
        {gpus.map((g) => {
          const pct = g.utilization_pct || 0;
          const memPct = g.memory_total_mib > 0 ? (g.memory_used_mib / g.memory_total_mib) * 100 : 0;
          const util = pct > 0;
          return (
            <div key={g.index} className="flex items-center gap-2">
              <span className="text-[9px] text-[#4B5563] font-mono shrink-0 w-10">
                GPU {g.index}
              </span>
              <div className="flex-1 h-1.5 rounded-full bg-[#151921] overflow-hidden relative">
                <div
                  className="h-full transition-all duration-500"
                  style={{
                    width: `${Math.min(pct, 100)}%`,
                    background: util ? "#F59E0B" : "#23262E",
                    boxShadow: util ? "0 0 6px rgba(245,158,11,0.4)" : undefined,
                  }}
                  title={`Compute: ${pct}% · Mem: ${Math.round(memPct)}%`}
                />
              </div>
              <span
                className="text-[9px] font-mono tabular-nums shrink-0 w-8 text-right"
                style={{ color: util ? "#E8EAED" : "#4B5563" }}
              >
                {pct}%
              </span>
            </div>
          );
        })}
      </div>

      {/* Service indicator lights */}
      <div className="flex items-center gap-3 text-[9px] font-mono uppercase tracking-wider">
        <ServiceDot label="vLLM" ok={health.vllm.ok} title="Gemma 4 31B via vLLM on all 4 GPUs" />
        <ServiceDot label="VM" ok={health.vm.ok} title="Target Rocky 9 VM (via libvirt)" />
        <ServiceDot label="Postgres" ok={health.postgres.ok} title="Shared Supabase — episodic + semantic memory" />
        <ServiceDot label="Neo4j" ok={health.neo4j.ok} title="Reflective tier (Graphiti) for the dream pass" />
      </div>
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
