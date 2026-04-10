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

export interface RunEvent {
  timestamp: string;
  elapsed_s: number;
  event_type: string;
  agent: string;
  iteration: number;
  data: Record<string, any>;
  gpu_state?: GpuState[];
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
  auditor: "#22C55E",
  reflector: "#A855F7",
  system: "#6B7280",
};

export const AGENT_MODELS: Record<string, string> = {
  architect: "Gemma 4 31B · TP=2",
  worker: "Gemma 4 31B · TP=2",
  reflector: "Gemma 4 31B · TP=2",
  auditor: "Nemotron 30B · PP=2",
};

export const AGENT_GPUS: Record<string, string> = {
  architect: "GPUs 0+1",
  worker: "GPUs 0+1",
  reflector: "GPUs 0+1",
  auditor: "GPUs 2+3",
};
