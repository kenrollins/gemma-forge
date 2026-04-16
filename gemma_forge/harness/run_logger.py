"""Structured run logger for the Ralph loop.

Captures every event from a Ralph loop run in a JSON-lines file that
serves two purposes:
  1. Post-run analysis (what happened, where it stalled, what worked)
  2. The data model for the frontend's "history replay" feature

Each line is a JSON object with:
  - timestamp (ISO 8601)
  - event_type (scan, architect_plan, worker_apply, auditor_check,
                revert, tool_call, tool_result, error, summary)
  - agent (architect, worker, auditor, sentry, system)
  - iteration (loop iteration number)
  - data (event-specific payload)
  - gpu_state (optional: VRAM/utilization snapshot for all 4 GPUs)
"""

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class RunLogger:
    """Logs Ralph loop events to a JSON-lines file."""

    def __init__(self, output_dir: str = "runs"):
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.output_dir / f"run-{self.run_id}.jsonl"
        self.iteration = 0
        self.start_time = time.time()
        self._file = open(self.log_path, "a")

        self.log("run_start", "system", {
            "run_id": self.run_id,
            "start_time": datetime.now(timezone.utc).isoformat(),
        })

    def log(
        self,
        event_type: str,
        agent: str,
        data: dict[str, Any],
        include_gpu: bool = False,
    ) -> None:
        """Write a structured event to the log."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "elapsed_s": round(time.time() - self.start_time, 2),
            "event_type": event_type,
            "agent": agent,
            "iteration": self.iteration,
            "data": data,
        }

        # Environmental snapshots piggyback on the same cadence — every
        # iteration_start / scan_complete / rule_complete / escalated /
        # revert / run_complete emits BOTH gpu_state and vllm_state so
        # the dashboard's Architecture panel can narrate model pressure
        # alongside hardware utilization. Older runs have gpu_state but
        # no vllm_state; the UI degrades gracefully.
        if include_gpu:
            entry["gpu_state"] = self._capture_gpu_state()
            vllm = self._capture_vllm_metrics()
            if vllm is not None:
                entry["vllm_state"] = vllm

        self._file.write(json.dumps(entry) + "\n")
        self._file.flush()

    def set_iteration(self, n: int) -> None:
        self.iteration = n

    def log_tool_call(self, agent: str, tool_name: str, args: dict) -> None:
        self.log("tool_call", agent, {
            "tool": tool_name,
            "args": {k: v[:200] if isinstance(v, str) else v for k, v in args.items()},
        })

    def log_tool_result(self, agent: str, tool_name: str, result: str) -> None:
        self.log("tool_result", agent, {
            "tool": tool_name,
            "result": result[:500],
        })

    def log_agent_response(self, agent: str, text: str, tokens: Optional[dict] = None) -> None:
        self.log("agent_response", agent, {
            "text": text[:1000],
            "tokens": tokens,
        })

    def log_revert(self, agent: str, reason: str, result: str) -> None:
        self.log("revert", agent, {
            "reason": reason,
            "result": result[:500],
        }, include_gpu=True)

    def log_error(self, agent: str, error: str) -> None:
        self.log("error", agent, {"error": error[:500]})

    def log_summary(self, data: dict) -> None:
        self.log("run_complete", "system", data, include_gpu=True)
        self._file.close()

    # vLLM Prometheus endpoint — configurable via env var so a relocated
    # serving layer doesn't require a code change. Defaults to the port
    # the Gemma 4 31B director runs on in the reference deployment.
    _VLLM_METRICS_URL = os.environ.get("VLLM_METRICS_URL", "http://localhost:8050/metrics")

    # The four gauges we actually care about for a snapshot. Each line
    # in the /metrics output looks like:
    #   vllm:kv_cache_usage_perc{engine="0",model_name="..."} 0.23
    # so we extract the value after the closing brace.
    _VLLM_GAUGE_NAMES = {
        "num_requests_running": "running",
        "num_requests_waiting": "waiting",
        "kv_cache_usage_perc": "kv_cache_pct",
    }

    # Prefix-cache hit rate is derived from two cumulative counters.
    _VLLM_CUM_NAMES = {
        "prefix_cache_queries_total": "prefix_queries",
        "prefix_cache_hits_total": "prefix_hits",
    }

    def _capture_vllm_metrics(self) -> Optional[dict]:
        """Snapshot of vLLM's /metrics endpoint.

        Returns ``None`` if vLLM isn't reachable — the caller treats
        the absent field as "no telemetry available," same convention
        as a failed nvidia-smi. Does NOT pull a full Prometheus parse;
        just greps the handful of lines we care about so this stays
        subsecond and adds negligible overhead to each logged event.
        """
        try:
            import urllib.request
            with urllib.request.urlopen(self._VLLM_METRICS_URL, timeout=2) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except Exception:
            return None

        gauges: dict[str, float] = {}
        cumul: dict[str, float] = {}
        for line in body.splitlines():
            if not line.startswith("vllm:"):
                continue
            # Strip the "vllm:" prefix to match against our short names,
            # then parse: name{labels} value
            prefixed, _, rest = line.partition("{")
            if not rest:
                continue
            name = prefixed[len("vllm:"):]
            _, _, tail = rest.partition("}")
            parts = tail.strip().split()
            if not parts:
                continue
            try:
                val = float(parts[0])
            except ValueError:
                continue
            if name in self._VLLM_GAUGE_NAMES:
                gauges[self._VLLM_GAUGE_NAMES[name]] = val
            elif name in self._VLLM_CUM_NAMES:
                cumul[self._VLLM_CUM_NAMES[name]] = val

        if not gauges and not cumul:
            return None

        snap: dict = {
            "running": int(gauges.get("running", 0)),
            "waiting": int(gauges.get("waiting", 0)),
            # vLLM reports kv_cache_usage_perc as a FRACTION in [0, 1],
            # not a percent. Convert to % once here so the dashboard
            # doesn't have to guess.
            "kv_cache_pct": round(gauges.get("kv_cache_pct", 0.0) * 100, 1),
        }
        q = cumul.get("prefix_queries", 0)
        h = cumul.get("prefix_hits", 0)
        if q > 0:
            snap["prefix_hit_rate"] = round(h / q, 3)
            snap["prefix_queries_total"] = int(q)
            snap["prefix_hits_total"] = int(h)
        return snap

    def _capture_gpu_state(self) -> list[dict]:
        """Snapshot GPU state via nvidia-smi."""
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            gpus = []
            for line in result.stdout.strip().split("\n"):
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 7:
                    gpus.append({
                        "index": int(parts[0]),
                        "name": parts[1],
                        "memory_used_mib": int(parts[2]),
                        "memory_total_mib": int(parts[3]),
                        "utilization_pct": int(parts[4]),
                        "temperature_c": int(parts[5]),
                        "power_w": float(parts[6]),
                    })
            return gpus
        except Exception:
            return []
