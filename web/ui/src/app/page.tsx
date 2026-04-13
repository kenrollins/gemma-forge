"use client";

import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { GpuState, RunEvent, SkillUI, DEFAULT_SKILL_UI, CrossRunData } from "../components/types";
import HeroStrip from "../components/HeroStrip";
import TaskMap from "../components/TaskMap";
import FocusPanel from "../components/FocusPanel";
import EventLog from "../components/EventLog";

function getApiBase(): string {
  if (typeof window === "undefined") return "http://localhost:8080";
  return `http://${window.location.hostname}:8080`;
}

// --- Run info from API ---
interface RunInfo {
  filename: string;
  events: number;
  start: string;
  elapsed_s: number;
  summary: Record<string, unknown>;
}

// --- Mode selector bar ---
function ModeBar({
  mode,
  setMode,
  runs,
  selectedRun,
  setSelectedRun,
  replaySpeed,
  setReplaySpeed,
  connected,
  onConnect,
}: {
  mode: "live" | "replay";
  setMode: (m: "live" | "replay") => void;
  runs: RunInfo[];
  selectedRun: string;
  setSelectedRun: (r: string) => void;
  replaySpeed: number;
  setReplaySpeed: (s: number) => void;
  connected: boolean;
  onConnect: () => void;
}) {
  return (
    <div className="flex items-center gap-3 px-4 py-2 border-b border-[#1C1F26] bg-[#0D0F14]">
      {/* Mode toggle */}
      <div className="flex bg-[#12141A] rounded-sm border border-[#1C1F26] overflow-hidden">
        <button
          onClick={() => setMode("live")}
          className={`px-3 py-1 text-[10px] font-bold uppercase tracking-wider transition-colors ${
            mode === "live"
              ? "bg-[#22C55E]/15 text-[#22C55E] border-r border-[#1C1F26]"
              : "text-[#6B7280] hover:text-[#9CA3AF] border-r border-[#1C1F26]"
          }`}
        >
          Live
        </button>
        <button
          onClick={() => setMode("replay")}
          className={`px-3 py-1 text-[10px] font-bold uppercase tracking-wider transition-colors ${
            mode === "replay"
              ? "bg-[#3B82F6]/15 text-[#3B82F6]"
              : "text-[#6B7280] hover:text-[#9CA3AF]"
          }`}
        >
          Replay
        </button>
      </div>

      {/* Run selector (replay mode) */}
      {mode === "replay" && (
        <>
          <select
            value={selectedRun}
            onChange={(e) => setSelectedRun(e.target.value)}
            className="bg-[#12141A] border border-[#1C1F26] rounded-sm px-2 py-1 text-[10px] font-mono text-[#9CA3AF] outline-none focus:border-[#3B82F6]"
          >
            <option value="">Select a run...</option>
            {runs.map((r) => {
              const date = r.start ? new Date(r.start).toLocaleString() : r.filename;
              const mins = Math.floor(r.elapsed_s / 60);
              return (
                <option key={r.filename} value={r.filename}>
                  {date} ({r.events} events, {mins}m)
                </option>
              );
            })}
          </select>

          {/* Speed control */}
          <div className="flex items-center gap-1.5">
            <span className="text-[9px] text-[#4B5563] uppercase">Speed:</span>
            {[1, 5, 20, 100].map((s) => (
              <button
                key={s}
                onClick={() => setReplaySpeed(s)}
                className={`px-1.5 py-0.5 text-[9px] font-mono rounded-sm transition-colors ${
                  replaySpeed === s
                    ? "bg-[#3B82F6]/20 text-[#3B82F6]"
                    : "text-[#6B7280] hover:text-[#9CA3AF]"
                }`}
              >
                {s}x
              </button>
            ))}
          </div>
        </>
      )}

      {/* Connect button */}
      <button
        onClick={onConnect}
        className={`ml-auto px-3 py-1 text-[10px] font-bold uppercase tracking-wider rounded-sm transition-colors ${
          connected
            ? "bg-[#22C55E]/15 text-[#22C55E]"
            : "bg-[#3B82F6]/15 text-[#3B82F6] hover:bg-[#3B82F6]/25"
        }`}
      >
        {connected ? "Connected" : "Connect"}
      </button>

      {/* Connection status indicator */}
      <div
        className={`w-2 h-2 rounded-full ${connected ? "bg-[#22C55E] animate-pulse" : "bg-[#6B7280]"}`}
      />
    </div>
  );
}

// --- Activity Ticker (mission control heartbeat) ---
function ActivityTicker({ events, skillUI, connected }: {
  events: RunEvent[];
  skillUI: SkillUI;
  connected: boolean;
}) {
  const stripId = (id: string) => {
    if (skillUI.id_prefix_strip && id.startsWith(skillUI.id_prefix_strip))
      return id.slice(skillUI.id_prefix_strip.length);
    return id;
  };

  // Find the most recent meaningful event for the ticker
  const tickerText = (() => {
    if (!connected || events.length === 0) return null;
    for (let i = events.length - 1; i >= Math.max(0, events.length - 20); i--) {
      const e = events[i];
      const d = e.data || {};
      const rid = d.rule_id ? stripId(d.rule_id as string) : "";
      switch (e.event_type) {
        case "remediated":
          return { color: "#10B981", text: `Remediated ${rid} in ${d.attempt || "?"} attempt${(d.attempt as number) > 1 ? "s" : ""} (${Math.round(d.wall_time_s as number || 0)}s)` };
        case "escalated":
          return { color: "#F59E0B", text: `Escalated ${rid} after ${d.attempts || "?"} attempts — ${d.reason || "budget exhausted"}` };
        case "rule_selected":
          return { color: "#22D3EE", text: `Architect selected ${rid}: ${d.title || ""}` };
        case "evaluation":
          return { color: d.failure_mode === "health_failure" ? "#EF4444" : "#8B95A5", text: `Eval: ${d.summary || "checking..."}` };
        case "scanner_gap_detected":
          return { color: "#F59E0B", text: `Scanner gap on ${rid}: ${d.distinct_approaches || "?"} approaches tried, evaluator keeps rejecting` };
        case "architect_reengaged":
          return { color: "#A855F7", text: `Architect re-engaged on ${rid} — verdict: ${d.verdict || "?"}` };
        case "cross_run_hydration":
          return { color: "#22D3EE", text: `Cross-run memory: loaded ${d.loaded_lessons || 0} lessons, ${d.loaded_bans || 0} bans from ${d.prior_runs || 0} prior runs` };
        case "clutch_initialized":
          return { color: "#22D3EE", text: `Clutch: ${d.reason || "initialized"}` };
        default:
          continue;
      }
    }
    return null;
  })();

  return (
    <div className="h-7 shrink-0 overflow-hidden border-t border-[#2A2F3A] bg-[#12151A] px-4 flex items-center gap-3">
      <span className="text-[9px] uppercase tracking-[0.15em] text-[#4B5563] shrink-0">
        LIVE
      </span>
      {connected ? (
        <span className="w-1.5 h-1.5 rounded-full bg-[#22D3EE] animate-pulse shrink-0" />
      ) : (
        <span className="w-1.5 h-1.5 rounded-full bg-[#4B5563] shrink-0" />
      )}
      {tickerText ? (
        <span className="text-[11px] font-mono truncate" style={{ color: tickerText.color }}>
          {tickerText.text}
        </span>
      ) : (
        <span className="text-[11px] font-mono text-[#4B5563] italic">
          {connected ? "Waiting for activity..." : "Disconnected"}
        </span>
      )}
    </div>
  );
}

// --- Main Dashboard ---
export default function Dashboard() {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [mode, setMode] = useState<"live" | "replay">("live");
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [selectedRun, setSelectedRun] = useState("");
  const [replaySpeed, setReplaySpeed] = useState(20);
  const evtSourceRef = useRef<EventSource | null>(null);

  const defaultGpus: GpuState[] = [
    { index: 0, name: "L4", memory_used_mib: 0, memory_total_mib: 23034, utilization_pct: 0, temperature_c: 0, power_w: 0, role: "gemma", model: "Gemma-4-31B-it bf16" },
    { index: 1, name: "L4", memory_used_mib: 0, memory_total_mib: 23034, utilization_pct: 0, temperature_c: 0, power_w: 0, role: "gemma", model: "Gemma-4-31B-it bf16" },
    { index: 2, name: "L4", memory_used_mib: 0, memory_total_mib: 23034, utilization_pct: 0, temperature_c: 0, power_w: 0, role: "gemma", model: "Gemma-4-31B-it bf16" },
    { index: 3, name: "L4", memory_used_mib: 0, memory_total_mib: 23034, utilization_pct: 0, temperature_c: 0, power_w: 0, role: "gemma", model: "Gemma-4-31B-it bf16" },
  ];
  const [gpus, setGpus] = useState<GpuState[]>(defaultGpus);

  // Fetch available runs on mount
  useEffect(() => {
    const apiBase = getApiBase();
    fetch(`${apiBase}/api/runs`)
      .then(r => r.json())
      .then(data => {
        if (Array.isArray(data)) {
          setRuns(data);
          if (data.length > 0 && !selectedRun) {
            setSelectedRun(data[0].filename);
          }
        }
      })
      .catch(() => {});
  }, []);

  // Track elapsed from events
  useEffect(() => {
    if (events.length > 0) setElapsed(events[events.length - 1].elapsed_s);
  }, [events]);

  // Update GPU state from events
  useEffect(() => {
    const gpuEvents = events.filter(e => e.gpu_state && e.gpu_state.length > 0);
    if (gpuEvents.length > 0) {
      const latest = gpuEvents[gpuEvents.length - 1].gpu_state!;
      setGpus(latest.map((g) => ({ ...g, role: "gemma", model: "Gemma-4-31B-it bf16" })));
    }
  }, [events]);

  // Extract skill UI config from skill_manifest event (emitted at run start).
  // Falls back to DEFAULT_SKILL_UI for older runs that don't emit it.
  const skillUI: SkillUI = (() => {
    const manifestEvent = events.find(e => e.event_type === "skill_manifest");
    if (manifestEvent && manifestEvent.data?.ui) {
      return { ...DEFAULT_SKILL_UI, ...(manifestEvent.data.ui as Partial<SkillUI>) };
    }
    // Backwards-compatibility: if we're replaying an older STIG run, hydrate with STIG labels
    // so those runs still look meaningful without re-running them.
    const hasSTIGRule = events.some(e =>
      typeof e.data?.rule_id === "string" && (e.data.rule_id as string).includes("xccdf_org.ssgproject")
    );
    if (hasSTIGRule) {
      return {
        title: "STIG Remediation",
        work_item: "STIG rule",
        work_item_plural: "STIG rules",
        id_prefix_strip: "xccdf_org.ssgproject.content_rule_",
        fixed_label: "Remediated",
        outcomes: [
          { type: "fixed", label: "Remediated", color: "#22C55E" },
          { type: "escalated", label: "Escalated", color: "#EF4444" },
          { type: "skipped", label: "Skipped", color: "#6B7280" },
        ],
      };
    }
    return DEFAULT_SKILL_UI;
  })();

  // Disconnect any existing stream
  const disconnect = useCallback(() => {
    if (evtSourceRef.current) {
      evtSourceRef.current.close();
      evtSourceRef.current = null;
    }
    setConnected(false);
  }, []);

  // Connect to stream
  const connect = useCallback(() => {
    disconnect();
    setEvents([]);

    const apiBase = getApiBase();
    let url: string;

    if (mode === "live") {
      url = `${apiBase}/api/live-stream?poll_interval=1`;
    } else {
      if (!selectedRun) return;
      url = `${apiBase}/api/runs/${selectedRun}/stream?speed=${replaySpeed}`;
    }

    const evtSource = new EventSource(url);
    evtSourceRef.current = evtSource;

    evtSource.onopen = () => setConnected(true);
    evtSource.onmessage = (msg) => {
      try {
        const event: RunEvent = JSON.parse(msg.data);
        if (event.event_type === "stream_end") {
          evtSource.close();
          evtSourceRef.current = null;
          setConnected(false);
          return;
        }
        setEvents(prev => [...prev, event]);
      } catch {}
    };
    evtSource.onerror = () => {
      evtSource.close();
      evtSourceRef.current = null;
      setConnected(false);
    };
  }, [mode, selectedRun, replaySpeed, disconnect]);

  // Auto-connect when mode or run changes
  useEffect(() => {
    return () => disconnect();
  }, [disconnect]);

  // GPU polling (live mode only)
  useEffect(() => {
    if (mode !== "live") return;

    const apiBase = getApiBase();
    const gpuInterval = setInterval(async () => {
      try {
        const res = await fetch(`${apiBase}/api/gpu`);
        const data = await res.json();
        if (Array.isArray(data) && data.length >= 4) {
          setGpus(data.map((g: GpuState) => ({ ...g, role: "gemma", model: "Gemma-4-31B-it bf16" })));
        }
      } catch {}
    }, 5000);

    return () => clearInterval(gpuInterval);
  }, [mode]);

  // -- Derived data for new components --

  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [eventLogExpanded, setEventLogExpanded] = useState(false);

  const crossRunData: CrossRunData | null = useMemo(() => {
    const evt = events.find(e => e.event_type === "cross_run_hydration");
    if (evt && evt.data?.prior_runs) return evt.data as unknown as CrossRunData;
    return null;
  }, [events]);

  return (
    <div className="flex flex-col h-[calc(100vh-48px)]">
      {/* Mode selector bar */}
      <ModeBar
        mode={mode}
        setMode={(m) => { disconnect(); setMode(m); setEvents([]); }}
        runs={runs}
        selectedRun={selectedRun}
        setSelectedRun={setSelectedRun}
        replaySpeed={replaySpeed}
        setReplaySpeed={setReplaySpeed}
        connected={connected}
        onConnect={connect}
      />

      {/* Activity ticker — right below mode bar where it's visible */}
      <ActivityTicker events={events} skillUI={skillUI} connected={connected} />

      {/* Hero strip — progress bar, metrics, current item */}
      <HeroStrip events={events} skillUI={skillUI} connected={connected} elapsed={elapsed} />

      {/* Main content: FocusPanel (hero) + TaskMap (sidebar) */}
      <div className="flex-1 flex overflow-hidden min-h-0">
        <FocusPanel
          events={events}
          skillUI={skillUI}
          selectedItemId={selectedItemId}
          onSelectItem={setSelectedItemId}
          onClose={() => setSelectedItemId(null)}
        />
        <div className="w-[340px] shrink-0 border-l border-[#1C1F26] overflow-hidden">
          <TaskMap
            events={events}
            skillUI={skillUI}
            selectedItemId={selectedItemId}
            onSelectItem={setSelectedItemId}
            crossRunData={crossRunData}
          />
        </div>
      </div>

      {/* Event log — collapsible */}
      <div
        className={`shrink-0 border-t border-[#1C1F26] flex flex-col overflow-hidden transition-all duration-300 ${
          eventLogExpanded ? "h-[400px]" : "h-[180px]"
        }`}
      >
        <div
          className="px-3 py-1 border-b border-[#1C1F26] bg-[#0D0F14] flex items-center gap-2 cursor-pointer select-none"
          onClick={() => setEventLogExpanded(!eventLogExpanded)}
        >
          <span className="text-[9px] font-semibold uppercase tracking-wider text-[#4B5563]">
            Events ({events.length})
          </span>
          <span className="text-[10px] text-[#4B5563] ml-auto">
            {eventLogExpanded ? "\u25B2" : "\u25BC"}
          </span>
        </div>
        <div className="flex-1 overflow-hidden">
          <EventLog events={events} />
        </div>
      </div>

    </div>
  );
}
