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
  RunEvent,
  SkillUI,
  DEFAULT_SKILL_UI,
  CrossRunData,
  Tab,
  DashboardState,
} from "../components/types";
import ChromeBar, { ReplaySpeed, SPEEDS } from "../components/ChromeBar";
import HeroStrip from "../components/HeroStrip";
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
  const [replaySpeed, setReplaySpeed] = useState<ReplaySpeed>(20);
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

  // GPU state and the live nvidia-smi poll were previously wired here
  // but were already dead code (Scoreboard was the only consumer and is
  // not rendered after the prior refactor). UI-4's Architecture panel
  // will re-add the polling and pass GPU data into the new component.

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
      opts?: { seekSeconds?: number; preserveEvents?: boolean },
    ) => {
      // `preserveEvents` = true means this is a resume/seek (e.g. speed
      // change) — keep the existing state, reconnect at the seek point.
      // Otherwise clear state so a new run/mode starts fresh.
      const preserve = opts?.preserveEvents ?? false;
      const seek = opts?.seekSeconds ?? 0;

      if (evtSourceRef.current) evtSourceRef.current.close();
      eventBufferRef.current = [];
      if (flushFrameRef.current !== null) {
        cancelAnimationFrame(flushFrameRef.current);
        flushFrameRef.current = null;
      }
      if (!preserve) setEvents([]);

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
            // Demo loop: when a replay finishes, kick off another
            // round of the same run so the page stays alive.
            if (m === "replay") {
              setTimeout(() => openStream("replay", run, speed), 1500);
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
