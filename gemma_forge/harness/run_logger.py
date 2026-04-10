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

        if include_gpu:
            entry["gpu_state"] = self._capture_gpu_state()

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
