// Tab identity for the home dashboard. "live" stays as the key for
// the mission-control view even though the chrome label says "Mission",
// so older code (and any future event-tagged URLs) keep working.
export type Tab = "live" | "memory" | "runs" | "events";

// Server response from /api/state. Drives the auto-connect logic on
// the home page so the dashboard never shows an empty "Disconnected"
// page on first load.
export interface DashboardState {
  live: boolean;
  live_run_filename: string | null;
  demo_run: string | null;
  demo_speed: number;
}

export interface GpuState {
  index: number;
  name: string;
  memory_used_mib: number;
  memory_total_mib: number;
  utilization_pct: number;
  temperature_c?: number;
  power_w?: number;
  role?: string;
  model?: string;
}

// vLLM Prometheus metrics snapshot. Optional per-event — runs from
// before the capture was wired up (Run 1–4) simply don't have it,
// and the dashboard hides the Model-pressure row when absent.
export interface VllmState {
  running: number;
  waiting: number;
  kv_cache_pct: number; // already scaled to 0-100 server-side
  prefix_hit_rate?: number; // 0-1, cumulative across vLLM server lifetime
  prefix_queries_total?: number;
  prefix_hits_total?: number;
}

export interface RunEvent {
  timestamp: string;
  elapsed_s: number;
  event_type: string;
  agent: string;
  iteration: number;
  data: Record<string, any>;
  gpu_state?: GpuState[];
  vllm_state?: VllmState;
}

export interface AgentMetrics {
  tokPerSec: number;
  ttft: number;
  turnElapsed: number;
  promptTokens: number;
  completionTokens: number;
  model: string;
}

export const AGENT_COLORS: Record<string, string> = {
  architect: "#3B82F6",
  worker: "#F59E0B",
  reflector: "#A855F7",
  harness: "#6B7280",
  system: "#6B7280",
};

export const AGENT_MODELS: Record<string, string> = {
  architect: "Gemma 4 31B bf16 · TP=4",
  worker: "Gemma 4 31B bf16 · TP=4",
  reflector: "Gemma 4 31B bf16 · TP=4",
  harness: "Deterministic · Python",
};

export const AGENT_GPUS: Record<string, string> = {
  architect: "All 4 GPUs",
  worker: "All 4 GPUs",
  reflector: "All 4 GPUs",
  harness: "CPU",
};

export interface OutcomeDef {
  type: string;  // "fixed" | "escalated" | "skipped"
  label: string;
  color: string;
}

export interface SkillUI {
  title: string;
  work_item: string;
  work_item_plural: string;
  id_prefix_strip: string;
  fixed_label: string;
  outcomes: OutcomeDef[];
}

// -- Cross-run learning data (from cross_run_hydration event) --

export interface CategoryStat {
  category: string;
  success_rate: number;
  avg_attempts: number;
  total_items: number;
}

export interface CrossRunData {
  prior_runs: number;
  loaded_bans: number;
  loaded_lessons: number;
  category_stats: CategoryStat[];
}

// -- Work item detail (assembled from multiple events for FocusPanel) --

export interface WorkItemDetail {
  id: string;
  title: string;
  category: string;
  state: string;
  attempts: number;
  wall_time_s: number;
  escalation_reason?: string;
  approaches_tried: string[];
  reflections: Array<{ text: string; attempt: number; plateaued: boolean }>;
  reengagements: Array<{ verdict: string; trigger: string; attempt: number }>;
}

// -- Graph node (from graph_state events) --

export interface GraphNode {
  id: string;
  title: string;
  category: string;
  state: "queued" | "blocked" | "active" | "completed" | "escalated" | "skipped";
  attempts: number;
  wall_time_s: number;
  escalation_reason?: string | null;
}

export const DEFAULT_SKILL_UI: SkillUI = {
  title: "Awaiting Mission",
  work_item: "work item",
  work_item_plural: "work items",
  id_prefix_strip: "",
  fixed_label: "Fixed",
  outcomes: [
    { type: "fixed", label: "Fixed", color: "#22C55E" },
    { type: "escalated", label: "Escalated", color: "#F59E0B" },
    { type: "skipped", label: "Skipped", color: "#6B7280" },
  ],
};
