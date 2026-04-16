"use client";

/**
 * Home dashboard — Mission Control + auto-connect demo loop.
 *
 * Behavior on first load (no user interaction required):
 *   1. Fetch /api/state.
 *   2. If a run is live, connect to /api/live-stream.
 *   3. Otherwise, replay the configured demo run on a loop at the
 *      configured speed. The page is *never* in a "Disconnected,
 *      please click Connect" state.
 *
 * Mode and speed controls live in the in-page ChromeBar (see
 * components/ChromeBar.tsx). Tabs are local component state — they
 * deliberately do NOT use Next.js routes so that switching tabs does
 * not tear down the SSE connection.
 */

import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import {
  GpuState,
  VllmState,
  RunEvent,
  SkillUI,
  DEFAULT_SKILL_UI,
  CrossRunData,
  Tab,
  DashboardState,
} from "../components/types";
import ChromeBar, { ReplaySpeed, SPEEDS } from "../components/ChromeBar";
import HeroStrip from "../components/HeroStrip";
import PulseRibbon from "../components/PulseRibbon";
import ArchitecturePanel from "../components/ArchitecturePanel";
import MemoryPulsePanel from "../components/MemoryPulsePanel";
import TaskMap from "../components/TaskMap";
import FocusPanel from "../components/FocusPanel";
import EventLog from "../components/EventLog";

function getApiBase(): string {
  if (typeof window === "undefined") return "http://localhost:8080";
  return `http://${window.location.hostname}:8080`;
}

function isReplaySpeed(n: number): n is ReplaySpeed {
  return (SPEEDS as readonly number[]).includes(n);
}

/**
 * Friendly label for a replay run, e.g. "20260414 · 13.5h".
 * Falls back to the raw filename when summary metadata is missing.
 */
function replayLabelFor(filename: string | null, runs: RunInfo[]): string | undefined {
  if (!filename) return undefined;
  const info = runs.find((r) => r.filename === filename);
  if (!info) return filename.replace(/^run-|\.jsonl$/g, "");
  const ts = filename.replace(/^run-|\.jsonl$/g, "");
  const hours = info.elapsed_s / 3600;
  return hours >= 1
    ? `${ts.slice(0, 8)} · ${hours.toFixed(1)}h`
    : `${ts.slice(0, 8)} · ${Math.round(info.elapsed_s / 60)}m`;
}

interface RunInfo {
  filename: string;
  events: number;
  start: string;
  elapsed_s: number;
  summary: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Demo banner — small, only visible when we're auto-replaying so the viewer
// knows they're not watching live activity.
// ---------------------------------------------------------------------------
function DemoBanner({ replayLabel, onSwitchLive, liveAvailable }: {
  replayLabel?: string;
  onSwitchLive: () => void;
  liveAvailable: boolean;
}) {
  return (
    <div className="px-4 py-1 border-b border-[#1C1F26] bg-[#10131A] flex items-center gap-3 text-[10px]">
      <span className="font-semibold uppercase tracking-[0.18em] text-[#3B82F6]">
        Demo replay
      </span>
      {replayLabel && (
        <span className="font-mono text-[#9CA3AF]">{replayLabel}</span>
      )}
      <span className="text-[#6B7280]">
        — looping the designated demo run because no live run is active.
      </span>
      {liveAvailable && (
        <button
          onClick={onSwitchLive}
          className="ml-auto px-2 py-0.5 rounded text-[#22C55E] hover:bg-[#22C55E]/15 transition-colors uppercase tracking-wider font-semibold"
        >
          Switch to live ●
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab placeholder views. Live (Mission) tab is the existing dashboard
// content. The other three are stubbed for now and will fill in over
// subsequent UI phases.
// ---------------------------------------------------------------------------
function TabPlaceholder({ label, body }: { label: string; body: string }) {
  return (
    <div className="flex-1 flex items-center justify-center bg-[#0B0D11]">
      <div className="text-center max-w-md">
        <div className="text-[10px] font-semibold uppercase tracking-[0.2em] text-[#3F4451] mb-2">
          {label} tab
        </div>
        <div className="text-sm text-[#6B7280] leading-relaxed">{body}</div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Dashboard
// ---------------------------------------------------------------------------
export default function Dashboard() {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [mode, setMode] = useState<"live" | "replay">("replay");
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [activeRun, setActiveRun] = useState<string>("");
  const [replaySpeed, setReplaySpeed] = useState<ReplaySpeed>(100);
  const [liveAvailable, setLiveAvailable] = useState(false);
  const [activeTab, setActiveTab] = useState<Tab>("live");
  const [eventLogExpanded, setEventLogExpanded] = useState(false);
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);

  // Did we initialize from /api/state yet? Used to skip the first
  // render's effect cycle so we don't open a stream against a stale
  // (default) mode/run pair.
  const initialized = useRef(false);
  const evtSourceRef = useRef<EventSource | null>(null);
  const eventBufferRef = useRef<RunEvent[]>([]);
  const flushFrameRef = useRef<number | null>(null);

  // GPU state for the ArchitecturePanel. Hydrated from (a) gpu_state
  // snapshots embedded in stream events when the harness emits them
  // and (b) /api/gpu polling in live mode so the panel shows real
  // utilization during demos. Replay falls back to whatever the run
  // log captured.
  const defaultGpus: GpuState[] = [
    { index: 0, name: "L4", memory_used_mib: 0, memory_total_mib: 23034, utilization_pct: 0, temperature_c: 0, power_w: 0, role: "gemma", model: "Gemma-4-31B-it bf16" },
    { index: 1, name: "L4", memory_used_mib: 0, memory_total_mib: 23034, utilization_pct: 0, temperature_c: 0, power_w: 0, role: "gemma", model: "Gemma-4-31B-it bf16" },
    { index: 2, name: "L4", memory_used_mib: 0, memory_total_mib: 23034, utilization_pct: 0, temperature_c: 0, power_w: 0, role: "gemma", model: "Gemma-4-31B-it bf16" },
    { index: 3, name: "L4", memory_used_mib: 0, memory_total_mib: 23034, utilization_pct: 0, temperature_c: 0, power_w: 0, role: "gemma", model: "Gemma-4-31B-it bf16" },
  ];
  const [gpus, setGpus] = useState<GpuState[]>(defaultGpus);
  const latestGpuStateRef = useRef<GpuState[] | null>(null);
  // vLLM model-pressure snapshot from run_logger (Run 5+). Old runs
  // omit vllm_state so this stays null and the panel hides the row.
  const [vllm, setVllm] = useState<VllmState | null>(null);
  const latestVllmStateRef = useRef<VllmState | null>(null);

  // ----- Initial load: fetch dashboard state + run list ---------------
  useEffect(() => {
    const apiBase = getApiBase();
    Promise.all([
      fetch(`${apiBase}/api/state`).then((r) => r.json() as Promise<DashboardState>),
      fetch(`${apiBase}/api/runs`).then((r) => r.json()),
    ])
      .then(([state, runList]) => {
        if (Array.isArray(runList)) setRuns(runList);

        const speedFromServer = state.demo_speed;
        if (speedFromServer && isReplaySpeed(speedFromServer)) {
          setReplaySpeed(speedFromServer);
        }

        setLiveAvailable(state.live);

        if (state.live && state.live_run_filename) {
          setMode("live");
          setActiveRun(state.live_run_filename);
        } else if (state.demo_run) {
          setMode("replay");
          setActiveRun(state.demo_run);
        } else if (Array.isArray(runList) && runList.length > 0) {
          setMode("replay");
          setActiveRun(runList[0].filename);
        }
        initialized.current = true;
      })
      .catch(() => {
        initialized.current = true;
      });
  }, []);

  // ----- Periodic /api/state refresh so the LIVE button can light up
  // when a run starts mid-demo, without requiring a page reload. -------
  useEffect(() => {
    const apiBase = getApiBase();
    const t = setInterval(() => {
      fetch(`${apiBase}/api/state`)
        .then((r) => r.json())
        .then((state: DashboardState) => {
          setLiveAvailable(state.live);
        })
        .catch(() => {});
    }, 8000);
    return () => clearInterval(t);
  }, []);

  // Track elapsed from events
  const elapsed = events.length > 0 ? events[events.length - 1].elapsed_s : 0;

  // Lift GPU state from stream events when the harness embedded one
  // (replay runs that logged gpu_state snapshots keep their GPU bars
  // alive without needing the /api/gpu fallback). Same for the newer
  // vllm_state snapshot — both piggyback on the same include_gpu=True
  // event sites on the backend.
  useEffect(() => {
    let latestGpu: GpuState[] | null = null;
    let latestVllm: VllmState | null = null;
    for (let i = events.length - 1; i >= 0 && (!latestGpu || !latestVllm); i--) {
      const ev = events[i];
      if (!latestGpu && ev.gpu_state && ev.gpu_state.length > 0) {
        latestGpu = ev.gpu_state;
      }
      if (!latestVllm && ev.vllm_state) {
        latestVllm = ev.vllm_state;
      }
    }
    if (latestGpu && latestGpu !== latestGpuStateRef.current) {
      latestGpuStateRef.current = latestGpu;
      setGpus(latestGpu.map((g) => ({ ...g, role: "gemma", model: "Gemma-4-31B-it bf16" })));
    }
    if (latestVllm && latestVllm !== latestVllmStateRef.current) {
      latestVllmStateRef.current = latestVllm;
      setVllm(latestVllm);
    }
    // If we've moved to a stream that has no vllm_state at all (e.g.
    // replaying an old run), clear the stale state so the panel hides
    // the model row instead of lingering with Run 5's numbers.
    if (!latestVllm && events.length > 50 && latestVllmStateRef.current) {
      latestVllmStateRef.current = null;
      setVllm(null);
    }
  }, [events]);

  // Live GPU polling (only in live mode — replay relies on embedded
  // gpu_state in the event log above).
  useEffect(() => {
    if (mode !== "live") return;
    const apiBase = getApiBase();
    const t = setInterval(async () => {
      try {
        const res = await fetch(`${apiBase}/api/gpu`);
        const data = await res.json();
        if (Array.isArray(data) && data.length >= 4) {
          setGpus(data.map((g: GpuState) => ({ ...g, role: "gemma", model: "Gemma-4-31B-it bf16" })));
        }
      } catch {
        // transient — ignore
      }
    }, 5000);
    return () => clearInterval(t);
  }, [mode]);

  // Skill-UI hydration (unchanged from the prior version).
  const skillUI: SkillUI = (() => {
    const manifestEvent = events.find((e) => e.event_type === "skill_manifest");
    if (manifestEvent && manifestEvent.data?.ui) {
      return { ...DEFAULT_SKILL_UI, ...(manifestEvent.data.ui as Partial<SkillUI>) };
    }
    const hasSTIGRule = events.some(
      (e) =>
        typeof e.data?.rule_id === "string" &&
        (e.data.rule_id as string).includes("xccdf_org.ssgproject"),
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

  // ----- Stream lifecycle ---------------------------------------------

  const disconnect = useCallback(() => {
    if (evtSourceRef.current) {
      evtSourceRef.current.close();
      evtSourceRef.current = null;
    }
    if (flushFrameRef.current !== null) {
      cancelAnimationFrame(flushFrameRef.current);
      flushFrameRef.current = null;
    }
    setConnected(false);
  }, []);

  // Track current elapsed_s in a ref so the speed-change handler can
  // read it without going through the effect dependency chain.
  const currentElapsedRef = useRef(0);
  useEffect(() => {
    currentElapsedRef.current = events.length > 0 ? events[events.length - 1].elapsed_s : 0;
  }, [events]);

  const openStream = useCallback(
    (
      m: "live" | "replay",
      run: string,
      speed: ReplaySpeed,
      opts?: {
        seekSeconds?: number;
        preserveEvents?: boolean;
        // swapOnFirstEvent: keep the old events on screen until the
        // new stream's first event arrives, then replace them
        // atomically. Used for demo-loop restart so the pulse ribbon
        // doesn't flash blank between loops at fast speeds.
        swapOnFirstEvent?: boolean;
      },
    ) => {
      // `preserveEvents` = true means this is a resume/seek (e.g. speed
      // change) — keep the existing state, reconnect at the seek point.
      // `swapOnFirstEvent` = true means this is a demo-loop restart —
      // keep the existing state on screen until the new stream emits
      // its first event, then swap atomically. Both cases skip the
      // up-front setEvents([]) that would otherwise cause a blank
      // frame between the old stream ending and the new one starting.
      const preserve = opts?.preserveEvents ?? false;
      const swap = opts?.swapOnFirstEvent ?? false;
      const seek = opts?.seekSeconds ?? 0;

      if (evtSourceRef.current) evtSourceRef.current.close();
      eventBufferRef.current = [];
      if (flushFrameRef.current !== null) {
        cancelAnimationFrame(flushFrameRef.current);
        flushFrameRef.current = null;
      }
      if (!preserve && !swap) setEvents([]);

      const scheduleFlush = () => {
        if (flushFrameRef.current !== null) return;
        flushFrameRef.current = requestAnimationFrame(() => {
          flushFrameRef.current = null;
          const buffered = eventBufferRef.current;
          if (buffered.length === 0) return;
          eventBufferRef.current = [];
          setEvents((prev) => prev.concat(buffered));
        });
      };

      const apiBase = getApiBase();
      const url =
        m === "live"
          ? `${apiBase}/api/live-stream?poll_interval=1`
          : `${apiBase}/api/runs/${run}/stream?speed=${speed}` +
            (seek > 0 ? `&start_from=${seek}` : "");

      // Loop-transition buffer. When swapOnFirstEvent is true, we
      // accumulate the new stream's events into `swapBuf` instead of
      // showing them, keeping the OLD run's final state on screen.
      // The atomic swap fires once the new stream has produced enough
      // substance for the ribbon + hero to render meaningfully —
      // specifically, once we've seen a graph_state (gives total rule
      // count) OR accumulated a safety threshold. Swapping on the
      // very first event (usually run_start) produced a visible flash
      // where the ribbon briefly rendered with zero cells before real
      // data arrived; this waits until there's actual state to show.
      let swapPending = !!opts?.swapOnFirstEvent;
      let swapBuf: RunEvent[] = [];
      const SWAP_THRESHOLD_EVENTS = 40;

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
            // If a swap was still pending when the stream ended early
            // (e.g. a tiny run), commit whatever we buffered rather
            // than losing it on the next loop.
            if (swapPending && swapBuf.length > 0) {
              setEvents(swapBuf);
              swapBuf = [];
              swapPending = false;
            }
            // Demo loop: kick off the next round with
            // swapOnFirstEvent so the page never shows an empty
            // transition frame.
            if (m === "replay") {
              setTimeout(
                () =>
                  openStream("replay", run, speed, { swapOnFirstEvent: true }),
                800,
              );
            }
            return;
          }
          if (swapPending) {
            swapBuf.push(event);
            // Swap once we have real substance: either a graph_state
            // (knows the total rule count) or enough events to have
            // rebuilt the skill manifest + initial counts.
            const hasGraphState = event.event_type === "graph_state";
            if (hasGraphState || swapBuf.length >= SWAP_THRESHOLD_EVENTS) {
              const frozen = swapBuf;
              swapBuf = [];
              swapPending = false;
              setEvents(frozen);
            }
            return;
          }
          eventBufferRef.current.push(event);
          scheduleFlush();
        } catch {
          // Malformed line — ignore.
        }
      };
      evtSource.onerror = () => {
        evtSource.close();
        evtSourceRef.current = null;
        setConnected(false);
      };
    },
    [],
  );

  // ----- Auto-connect: open a fresh stream whenever mode or run changes.
  // Speed changes are handled separately (see setReplaySpeedPreserving
  // below) so that bumping speed during a demo doesn't reset the run
  // back to the beginning.
  useEffect(() => {
    if (!initialized.current) return;
    if (!activeRun && mode === "replay") return;
    openStream(mode, activeRun, replaySpeed);
    return () => disconnect();
    // intentionally NOT depending on replaySpeed — the speed handler
    // reconnects with `start_from` so the position is preserved.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, activeRun, openStream, disconnect]);

  // Speed change during a replay: reconnect at the current elapsed_s
  // so the event stream resumes at the new speed from exactly where
  // we are on screen. No visual reset, no event loss.
  const setReplaySpeedPreserving = useCallback(
    (s: ReplaySpeed) => {
      setReplaySpeed(s);
      if (mode === "replay" && activeRun) {
        const seek = Math.max(0, currentElapsedRef.current - 0.5);
        openStream("replay", activeRun, s, {
          seekSeconds: seek,
          preserveEvents: true,
        });
      }
    },
    [mode, activeRun, openStream],
  );

  // ----- Cross-run hydration data passed into TaskMap ------------------
  const crossRunData: CrossRunData | null = useMemo(() => {
    const evt = events.find((e) => e.event_type === "cross_run_hydration");
    if (evt && evt.data?.prior_runs) return evt.data as unknown as CrossRunData;
    return null;
  }, [events]);

  // ----- Mode-switch helpers shaped for the chrome bar -----------------
  const handleSetMode = useCallback(
    (m: "live" | "replay") => {
      if (m === mode) return;
      if (m === "live") {
        // Switch to live: pick the freshest live filename if we know it.
        const apiBase = getApiBase();
        fetch(`${apiBase}/api/state`)
          .then((r) => r.json())
          .then((s: DashboardState) => {
            if (s.live && s.live_run_filename) {
              setMode("live");
              setActiveRun(s.live_run_filename);
            }
          });
      } else {
        // Switch to replay: prefer the demo run, else the active one.
        const fallback =
          activeRun ||
          (runs.length > 0 ? runs[0].filename : "");
        if (!fallback) return;
        setMode("replay");
        setActiveRun(fallback);
      }
    },
    [mode, activeRun, runs],
  );

  // ----- Render --------------------------------------------------------
  const replayLabel = mode === "replay" ? replayLabelFor(activeRun, runs) : undefined;

  return (
    <div className="flex flex-col h-[calc(100vh-36px)]">
      <ChromeBar
        mode={mode}
        setMode={handleSetMode}
        liveAvailable={liveAvailable}
        replaySpeed={replaySpeed}
        setReplaySpeed={setReplaySpeedPreserving}
        connected={connected}
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        replayLabel={replayLabel}
        runs={runs}
        activeRun={activeRun}
        setActiveRun={(r: string) => {
          setActiveRun(r);
          // Explicit run change — clear and restart from zero.
          // The openStream call inside the mode/run effect handles this
          // automatically when activeRun changes.
        }}
      />

      {mode === "replay" && activeTab === "live" && (
        <DemoBanner
          replayLabel={replayLabel}
          liveAvailable={liveAvailable}
          onSwitchLive={() => handleSetMode("live")}
        />
      )}

      {activeTab === "live" && (
        <>
          <HeroStrip events={events} skillUI={skillUI} connected={connected} elapsed={elapsed} />
          <PulseRibbon events={events} skillUI={skillUI} />

          <div className="flex-1 flex overflow-hidden min-h-0">
            <FocusPanel
              events={events}
              skillUI={skillUI}
              selectedItemId={selectedItemId}
              onSelectItem={setSelectedItemId}
              onClose={() => setSelectedItemId(null)}
            />
            <div className="w-[340px] shrink-0 border-l border-[#1C1F26] overflow-y-auto">
              {/* Plain vertical stack — each child takes its natural
                  height and the whole column scrolls. Avoids the
                  flex-col trap where TaskMap's own internal scroll
                  competes with the sidebar scroll and neither wins. */}
              <ArchitecturePanel gpus={gpus} vllm={vllm} events={events} connected={connected} />
              <MemoryPulsePanel events={events} skillUI={skillUI} crossRunData={crossRunData} />
              <TaskMap
                events={events}
                skillUI={skillUI}
                selectedItemId={selectedItemId}
                onSelectItem={setSelectedItemId}
                crossRunData={crossRunData}
              />
            </div>
          </div>
        </>
      )}

      {activeTab === "memory" && (
        <TabPlaceholder
          label="Memory"
          body="The Memory view will live here in UI-6: a force-directed graph of lessons, attempts, and rules with retrievals lighting up edges as they happen. Coming after V2 ships per-prompt tip logging."
        />
      )}

      {activeTab === "runs" && (
        <TabPlaceholder
          label="Runs"
          body="A gallery of past runs with summary cards (date, fix rate, hero metric). Click into one to launch it as a replay. Coming next phase."
        />
      )}

      {activeTab === "events" && (
        <div className="flex-1 flex flex-col overflow-hidden min-h-0">
          <div className="px-3 py-1 border-b border-[#1C1F26] bg-[#0D0F14] flex items-center gap-2">
            <span className="text-[9px] font-semibold uppercase tracking-wider text-[#4B5563]">
              Events ({events.length})
            </span>
          </div>
          <div className="flex-1 overflow-hidden">
            <EventLog events={events} />
          </div>
        </div>
      )}

      {/* Collapsible event log on the Mission view, but with the home page
          no longer dominated by it. Engineers who want full event detail
          should switch to the Events tab. The strip stays for at-a-glance
          confirmation that data is flowing. */}
      {activeTab === "live" && (
        <div
          className={`shrink-0 border-t border-[#1C1F26] flex flex-col overflow-hidden transition-all duration-300 ${
            eventLogExpanded ? "h-[280px]" : "h-[28px]"
          }`}
        >
          <div
            className="px-3 py-1 border-b border-[#1C1F26] bg-[#0D0F14] flex items-center gap-2 cursor-pointer select-none"
            onClick={() => setEventLogExpanded(!eventLogExpanded)}
          >
            <span className="text-[9px] font-semibold uppercase tracking-wider text-[#4B5563]">
              Event tail ({events.length})
            </span>
            <span className="text-[10px] text-[#4B5563] ml-auto">
              {eventLogExpanded ? "\u25BC Collapse" : "\u25B2 Expand"}
            </span>
          </div>
          {eventLogExpanded && (
            <div className="flex-1 overflow-hidden">
              <EventLog events={events} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
