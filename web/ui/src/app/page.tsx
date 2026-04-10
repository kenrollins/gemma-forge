"use client";

import { useState, useEffect } from "react";
import { GpuState, RunEvent } from "../components/types";
import Scoreboard from "../components/Scoreboard";
import ActiveAgent from "../components/ActiveAgent";
import Pipeline from "../components/Pipeline";
import EventLog from "../components/EventLog";

function getApiBase(): string {
  if (typeof window === "undefined") return "http://localhost:8080";
  return `http://${window.location.hostname}:8080`;
}

export default function Dashboard() {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [elapsed, setElapsed] = useState(0);

  const defaultGpus: GpuState[] = [
    { index: 0, name: "L4", memory_used_mib: 0, memory_total_mib: 23034, utilization_pct: 0, temperature_c: 0, power_w: 0, role: "architect", model: "Gemma-4-31B-IT-NVFP4" },
    { index: 1, name: "L4", memory_used_mib: 0, memory_total_mib: 23034, utilization_pct: 0, temperature_c: 0, power_w: 0, role: "architect", model: "Gemma-4-31B-IT-NVFP4" },
    { index: 2, name: "L4", memory_used_mib: 0, memory_total_mib: 23034, utilization_pct: 0, temperature_c: 0, power_w: 0, role: "auditor", model: "Nemotron-3-Nano-30B" },
    { index: 3, name: "L4", memory_used_mib: 0, memory_total_mib: 23034, utilization_pct: 0, temperature_c: 0, power_w: 0, role: "auditor", model: "Nemotron-3-Nano-30B" },
  ];
  const [gpus, setGpus] = useState<GpuState[]>(defaultGpus);

  // Track elapsed time from first event
  useEffect(() => {
    if (events.length > 0) {
      setElapsed(events[events.length - 1].elapsed_s);
    }
  }, [events]);

  // Update GPU state from events
  useEffect(() => {
    const gpuEvents = events.filter(e => e.gpu_state && e.gpu_state.length > 0);
    if (gpuEvents.length > 0) {
      const latest = gpuEvents[gpuEvents.length - 1].gpu_state!;
      setGpus(
        latest.map((g, i) => ({
          ...g,
          role: defaultGpus[i]?.role || "unknown",
          model: defaultGpus[i]?.model || "",
        }))
      );
    }
  }, [events]);

  // Connect to live stream
  useEffect(() => {
    const apiBase = getApiBase();
    const url = `${apiBase}/api/live-stream?poll_interval=1`;

    const evtSource = new EventSource(url);

    evtSource.onopen = () => setConnected(true);

    evtSource.onmessage = (msg) => {
      try {
        const event: RunEvent = JSON.parse(msg.data);
        if (event.event_type === "stream_end") {
          evtSource.close();
          return;
        }
        setEvents(prev => [...prev, event]);
      } catch {}
    };

    evtSource.onerror = () => {
      evtSource.close();
      setConnected(false);
    };

    // Poll real GPU state
    const gpuInterval = setInterval(async () => {
      try {
        const res = await fetch(`${apiBase}/api/gpu`);
        const data = await res.json();
        if (Array.isArray(data) && data.length >= 4) {
          setGpus(data.map((g: GpuState, i: number) => ({
            ...g,
            role: defaultGpus[i]?.role || "unknown",
            model: defaultGpus[i]?.model || "",
          })));
        }
      } catch {}
    }, 5000);

    return () => { evtSource.close(); clearInterval(gpuInterval); };
  }, []);

  // Derive active agent
  const lastAgentEvent = [...events].reverse().find(
    e => e.event_type === "agent_response" || e.event_type === "tool_call"
  );
  const activeAgent = lastAgentEvent?.agent || "system";
  const hasRevert = events.some(e => e.event_type === "revert");

  return (
    <div className="flex flex-col h-[calc(100vh-48px)]">
      {/* Zone 1: Scoreboard + GPU Strip */}
      <Scoreboard events={events} gpus={gpus} connected={connected} elapsed={elapsed} />

      {/* Zone 2: Pipeline + Active Agent + Event Log */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Pipeline visualization */}
        <div className="shrink-0 border-b border-[#1C1F26] bg-[#0D0F14]">
          <Pipeline activeAgent={activeAgent} hasRevert={hasRevert} />
        </div>

        {/* Active Agent Card */}
        <div className="shrink-0 p-3 border-b border-[#1C1F26]">
          <ActiveAgent events={events} />
        </div>

        {/* Event Log — scrollable history */}
        <div className="flex-1 overflow-hidden flex flex-col">
          <div className="px-4 py-2 border-b border-[#1C1F26] text-[10px] font-semibold uppercase tracking-wider text-[#6B7280] bg-[#0D0F14] flex justify-between">
            <span>Event History ({events.length} events)</span>
            <span>Click to expand</span>
          </div>
          <EventLog events={events} />
        </div>
      </div>
    </div>
  );
}
