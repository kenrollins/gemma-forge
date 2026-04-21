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
import RunsTab from "../components/RunsTab";
import MemoryTab from "../components/MemoryTab";
import TaskMap from "../components/TaskMap";
import FocusPanel from "../components/FocusPanel";
import EventLog from "../components/EventLog";

function getApiBase(): string {
  return "";
}

// SSE needs a direct origin, not a Next.js rewrite. Turbopack's
// rewrite proxy buffers streaming responses, which makes
// EventSource open-but-silent (UI says "live" but no events land).
// All non-streaming fetches still go through "" → rewrite → :8080,
// which preserves the same-origin benefits for CORS/devtunnels.
function getLiveStreamOrigin(): string {
  if (typeof window === "undefined") return "http://localhost:8080";
  return `${window.location.protocol}//${window.location.hostname}:8080`;
}

// Shallow value-equality check for GPU snapshots. The backend
// emits a fresh list on every gpu-bearing event (new reference
// every time), so we compare the fields that actually drive the UI:
// util, memory used, temperature, power. If none changed we can
// skip a setState call — at live-mode event rates the cumulative
// savings prevent React from tripping its re-render safety check.
function gpuSnapshotsEqual(a: GpuState[] | null, b: GpuState[] | null): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const x = a[i];
    const y = b[i];
    if (
      x.memory_used_mib !== y.memory_used_mib ||
      x.memory_total_mib !== y.memory_total_mib ||
      x.utilization_pct !== y.utilization_pct ||
      x.temperature_c !== y.temperature_c ||
      x.power_w !== y.power_w
    ) {
      return false;
    }
  }
  return true;
}

function vllmSnapshotsEqual(a: VllmState | null, b: VllmState | null): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  // Compare JSON as a cheap deep-ish equality — VllmState is a small
  // flat object and not hot enough to warrant per-field comparison.
  return JSON.stringify(a) === JSON.stringify(b);
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
  // Replay pause: freezes the local playback clock. Resume rebases
  // startWallMs to Date.now() at the current baseElapsed so the run
  // picks up exactly where it was. Speed-change uses the same rebase.
  const [paused, setPaused] = useState(false);
  // Loading: set while fetching a run's full event list. For large
  // (~20h) runs that's ~3 MB gzipped so the wait is visible on
  // high-latency links — we surface it in the chrome.
  const [loading, setLoading] = useState(false);

  // Did we initialize from /api/state yet? Used to skip the first
  // render's effect cycle so we don't open a stream against a stale
  // (default) mode/run pair.
  const initialized = useRef(false);
  const evtSourceRef = useRef<EventSource | null>(null);
  const eventBufferRef = useRef<RunEvent[]>([]);
  const flushFrameRef = useRef<number | null>(null);
  // In-flight run fetch — aborted when the user switches runs or mode
  // mid-download so we don't race two payloads into the same state.
  const fetchAbortRef = useRef<AbortController | null>(null);

  // Replay playback is driven client-side from an in-memory event
  // array. Pacing is done with a RAF loop against a local wall clock,
  // so network jitter never stalls or bursts the visible stream the
  // way server-paced SSE did over hotel wifi.
  type ReplayPlayback = {
    run: string;
    events: RunEvent[];
    nextIdx: number;
    baseElapsed: number;  // elapsed_s corresponding to startWallMs
    startWallMs: number;
    speed: number;
    rafId: number | null;
    // Swap-on-loop: keeps the old run's last frame on screen until
    // the first meaningful chunk of the restart has buffered, so the
    // ribbon doesn't flash blank between loops at 100x/1000x.
    swapPending: boolean;
    swapBuf: RunEvent[];
  };
  const replayRef = useRef<ReplayPlayback | null>(null);

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
    // Each event carrying gpu_state gets a FRESH array from the
    // backend (new reference every time), so reference-equality alone
    // would call setGpus on every such event — which at live-mode rate
    // can push React past its re-render safety threshold with a large
    // event log. Compare values instead: skip the state update if
    // every GPU's util/mem/temp/power is unchanged.
    if (latestGpu && !gpuSnapshotsEqual(latestGpu, latestGpuStateRef.current)) {
      latestGpuStateRef.current = latestGpu;
      setGpus(latestGpu.map((g) => ({ ...g, role: "gemma", model: "Gemma-4-31B-it bf16" })));
    }
    if (latestVllm && !vllmSnapshotsEqual(latestVllm, latestVllmStateRef.current)) {
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
    if (replayRef.current) {
      if (replayRef.current.rafId !== null) {
        cancelAnimationFrame(replayRef.current.rafId);
      }
      replayRef.current = null;
    }
    if (fetchAbortRef.current) {
      fetchAbortRef.current.abort();
      fetchAbortRef.current = null;
    }
    if (flushFrameRef.current !== null) {
      cancelAnimationFrame(flushFrameRef.current);
      flushFrameRef.current = null;
    }
    setConnected(false);
    setLoading(false);
  }, []);

  // Track current elapsed_s in a ref so the speed-change handler can
  // read it without going through the effect dependency chain.
  const currentElapsedRef = useRef(0);
  useEffect(() => {
    currentElapsedRef.current = events.length > 0 ? events[events.length - 1].elapsed_s : 0;
  }, [events]);

  // Mirror of the `paused` state for use inside the stable openStream
  // callback (which doesn't close over React state). Lets the
  // stream-end branch decide whether to auto-restart the demo loop.
  const pausedRef = useRef(paused);
  useEffect(() => {
    pausedRef.current = paused;
  }, [paused]);

  // Shared RAF-coalesced setState for event emission. Both modes push
  // into eventBufferRef and call scheduleFlush(); React gets at most
  // one update per frame regardless of how many events arrived.
  const scheduleFlush = useCallback(() => {
    if (flushFrameRef.current !== null) return;
    flushFrameRef.current = requestAnimationFrame(() => {
      flushFrameRef.current = null;
      const buffered = eventBufferRef.current;
      if (buffered.length === 0) return;
      eventBufferRef.current = [];
      setEvents((prev) => prev.concat(buffered));
    });
  }, []);

  // Local replay tick: advances through the in-memory event array
  // using a wall-clock driven `targetElapsed`. No network in the
  // loop — pausing, seeking, and speed-changes are all ref edits.
  const SWAP_THRESHOLD_EVENTS = 40;
  const tickReplay = useCallback(() => {
    const p = replayRef.current;
    if (!p) return;
    const wallSec = (Date.now() - p.startWallMs) / 1000;
    const target = p.baseElapsed + wallSec * p.speed;
    const emitted: RunEvent[] = [];
    while (
      p.nextIdx < p.events.length &&
      p.events[p.nextIdx].elapsed_s <= target
    ) {
      emitted.push(p.events[p.nextIdx++]);
    }
    if (emitted.length > 0) {
      if (p.swapPending) {
        p.swapBuf.push(...emitted);
        const hasGraph = emitted.some((e) => e.event_type === "graph_state");
        if (hasGraph || p.swapBuf.length >= SWAP_THRESHOLD_EVENTS) {
          const frozen = p.swapBuf;
          p.swapBuf = [];
          p.swapPending = false;
          setEvents(frozen);
        }
      } else {
        eventBufferRef.current.push(...emitted);
        scheduleFlush();
      }
    }
    if (p.nextIdx >= p.events.length) {
      // End of run. If a swap was still pending on a tiny run, commit
      // it now so the frame isn't empty on the next loop.
      if (p.swapPending && p.swapBuf.length > 0) {
        setEvents(p.swapBuf);
        p.swapBuf = [];
        p.swapPending = false;
      }
      p.rafId = null;
      setConnected(false);
      // Demo loop: restart this same run after a brief hold, with
      // swapOnFirstEvent so the chrome doesn't flash between loops.
      setTimeout(() => {
        if (pausedRef.current) return;
        const cur = replayRef.current;
        if (!cur || cur.run !== p.run) return;
        cur.nextIdx = 0;
        cur.baseElapsed = 0;
        cur.startWallMs = Date.now();
        cur.swapPending = true;
        cur.swapBuf = [];
        setConnected(true);
        cur.rafId = requestAnimationFrame(tickReplay);
      }, 800);
      return;
    }
    p.rafId = requestAnimationFrame(tickReplay);
  }, [scheduleFlush]);

  const openStream = useCallback(
    async (
      m: "live" | "replay",
      run: string,
      speed: ReplaySpeed,
      opts?: {
        seekSeconds?: number;
        preserveEvents?: boolean;
        // swapOnFirstEvent: keep the current events on screen until the
        // restart has buffered enough to render meaningfully. Avoids a
        // blank flash between loops at 100x/1000x.
        swapOnFirstEvent?: boolean;
      },
    ) => {
      const preserve = opts?.preserveEvents ?? false;
      const swap = opts?.swapOnFirstEvent ?? false;
      const seek = opts?.seekSeconds ?? 0;

      // Tear down anything currently running — SSE, RAF, in-flight fetch.
      if (evtSourceRef.current) {
        evtSourceRef.current.close();
        evtSourceRef.current = null;
      }
      if (replayRef.current?.rafId !== null && replayRef.current?.rafId !== undefined) {
        cancelAnimationFrame(replayRef.current.rafId);
      }
      if (fetchAbortRef.current) {
        fetchAbortRef.current.abort();
        fetchAbortRef.current = null;
      }
      eventBufferRef.current = [];
      if (flushFrameRef.current !== null) {
        cancelAnimationFrame(flushFrameRef.current);
        flushFrameRef.current = null;
      }
      if (!preserve && !swap) setEvents([]);

      if (m === "live") {
        // Live mode still uses SSE — it's an honest tail of a growing
        // file, not a pre-recorded replay, so local timing doesn't apply.
        replayRef.current = null;
        const url = `${getLiveStreamOrigin()}/api/live-stream?poll_interval=1`;
        const evtSource = new EventSource(url);
        evtSourceRef.current = evtSource;
        evtSource.onopen = () => setConnected(true);
        evtSource.onmessage = (msg) => {
          try {
            const event: RunEvent = JSON.parse(msg.data);
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
        return;
      }

      // Replay mode: fetch the full event list once, then pace it
      // locally. This decouples visual pacing from network delivery —
      // the key fix for bursty SSE over high-latency links.
      if (!run) return;

      // Fast path: resuming or seek-on-speed-change against the same
      // run that's already loaded. Skip the re-fetch.
      const cached = replayRef.current;
      const canReuse = preserve && cached && cached.run === run;
      let eventsAll: RunEvent[];
      if (canReuse) {
        eventsAll = cached!.events;
      } else {
        setLoading(true);
        setConnected(false);
        const ctrl = new AbortController();
        fetchAbortRef.current = ctrl;
        try {
          const res = await fetch(`/api/runs/${run}/events`, {
            signal: ctrl.signal,
          });
          if (!res.ok) {
            setLoading(false);
            return;
          }
          eventsAll = (await res.json()) as RunEvent[];
        } catch (err) {
          // Aborted or network error — either way we're done.
          if ((err as Error)?.name !== "AbortError") {
            setLoading(false);
            setConnected(false);
          }
          return;
        } finally {
          if (fetchAbortRef.current === ctrl) fetchAbortRef.current = null;
        }
        setLoading(false);
      }

      // Seek to the requested elapsed offset.
      let nextIdx = 0;
      while (
        nextIdx < eventsAll.length &&
        eventsAll[nextIdx].elapsed_s < seek
      ) {
        nextIdx++;
      }

      replayRef.current = {
        run,
        events: eventsAll,
        nextIdx,
        baseElapsed: seek,
        startWallMs: Date.now(),
        speed,
        rafId: null,
        swapPending: swap,
        swapBuf: [],
      };
      setConnected(true);
      replayRef.current.rafId = requestAnimationFrame(tickReplay);
    },
    [scheduleFlush, tickReplay],
  );

  // ----- Auto-connect: open a fresh stream whenever mode or run changes.
  // Speed changes are handled separately (see setReplaySpeedPreserving
  // below) so that bumping speed during a demo doesn't reset the run
  // back to the beginning. Changing mode or run also clears any active
  // pause — they're deliberate context shifts, so restart playback.
  useEffect(() => {
    if (!initialized.current) return;
    if (!activeRun && mode === "replay") return;
    setPaused(false);
    openStream(mode, activeRun, replaySpeed);
    return () => disconnect();
    // intentionally NOT depending on replaySpeed — the speed handler
    // reconnects with `start_from` so the position is preserved.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, activeRun, openStream, disconnect]);

  // Speed change during a replay: rebase the local clock so the run
  // keeps its position but paces at the new speed. Zero-cost — no
  // fetch, no re-render; the tick loop picks up the new `speed` field
  // on its next frame. If paused, just record the new speed for when
  // the user resumes.
  const setReplaySpeedPreserving = useCallback((s: ReplaySpeed) => {
    setReplaySpeed(s);
    const p = replayRef.current;
    if (!p) return;
    if (!paused) {
      const wallSec = (Date.now() - p.startWallMs) / 1000;
      p.baseElapsed = p.baseElapsed + wallSec * p.speed;
      p.startWallMs = Date.now();
    }
    p.speed = s;
  }, [paused]);

  // Pause / resume replay playback. Pause captures the current
  // elapsed_s into baseElapsed and cancels the RAF. Resume rebases
  // startWallMs to now, which makes target = baseElapsed at the
  // first tick — no position jump.
  const togglePause = useCallback(() => {
    if (mode !== "replay" || !activeRun) return;
    const p = replayRef.current;
    if (paused) {
      setPaused(false);
      if (p) {
        p.startWallMs = Date.now();
        if (p.rafId === null) {
          p.rafId = requestAnimationFrame(tickReplay);
        }
      }
    } else {
      if (p) {
        if (p.rafId !== null) cancelAnimationFrame(p.rafId);
        p.rafId = null;
        const wallSec = (Date.now() - p.startWallMs) / 1000;
        p.baseElapsed = p.baseElapsed + wallSec * p.speed;
      }
      setPaused(true);
    }
  }, [mode, activeRun, paused, tickReplay]);

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
        paused={paused}
        togglePause={togglePause}
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
        <MemoryTab events={events} skillUI={skillUI} />
      )}

      {activeTab === "runs" && (
        <RunsTab
          runs={runs}
          activeRun={activeRun}
          onLaunch={(filename) => {
            // Launching a past run from the gallery: switch to replay
            // mode on that file and flip back to the Mission view so
            // the presenter immediately sees the run playing.
            setMode("replay");
            setActiveRun(filename);
            setActiveTab("live");
          }}
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
