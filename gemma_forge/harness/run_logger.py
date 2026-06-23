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

import contextlib
import json
import os
import subprocess
import threading
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar


# DEF-24 — rolling-window peak sampler for vLLM instantaneous gauges.
#
# vLLM's `kv_cache_usage_perc` and `num_requests_running` are read at
# scrape-time. Ralph is effectively single-stream, so when the harness
# calls _capture_vllm_metrics between LLM calls, the gauges read 0 —
# the in-flight request has already completed. The dashboard tile
# therefore renders "KV CACHE 0.0%" indefinitely, even though the KV
# cache is hot during every generation step.
#
# This sampler runs in a daemon thread that polls /metrics at a fast
# interval and remembers the peak value seen in a short rolling window.
# _capture_vllm_metrics reads the window peak instead of the instant
# gauge, so the tile reflects work that happened *recently* rather than
# work happening *right now between two queries*.
class _VllmGaugeSampler:
    def __init__(
        self,
        metrics_url: str,
        interval_s: float = 1.0,
        window_s: float = 5.0,
        timeout_s: float = 1.0,
    ) -> None:
        self.metrics_url = metrics_url
        self.interval_s = interval_s
        self.window_s = window_s
        self.timeout_s = timeout_s
        # Each entry is (timestamp, dict of gauge name -> float).
        self._samples: list[tuple[float, dict[str, float]]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="vllm-gauge-sampler")
        self._thread.start()

    def _loop(self) -> None:
        # Names we care about — instantaneous, the ones that go to 0
        # between Ralph turns. Cumulative metrics don't need the window.
        wanted = {"num_requests_running", "num_requests_waiting", "kv_cache_usage_perc"}
        while not self._stop.is_set():
            sample: dict[str, float] = {}
            try:
                with urllib.request.urlopen(self.metrics_url, timeout=self.timeout_s) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                for line in body.splitlines():
                    if not line.startswith("vllm:"):
                        continue
                    prefixed, _, rest = line.partition("{")
                    if not rest:
                        continue
                    name = prefixed[len("vllm:") :]
                    if name not in wanted:
                        continue
                    _, _, tail = rest.partition("}")
                    parts = tail.strip().split()
                    if not parts:
                        continue
                    with contextlib.suppress(ValueError):
                        sample[name] = float(parts[0])
            except Exception:
                pass  # network blip, skip this tick
            now = time.time()
            with self._lock:
                self._samples.append((now, sample))
                # Trim old samples outside the window.
                cutoff = now - self.window_s
                while self._samples and self._samples[0][0] < cutoff:
                    self._samples.pop(0)
            self._stop.wait(self.interval_s)

    def peak(self) -> dict[str, float]:
        """Return the maximum value seen for each gauge in the window."""
        with self._lock:
            out: dict[str, float] = {}
            for _, s in self._samples:
                for k, v in s.items():
                    if k not in out or v > out[k]:
                        out[k] = v
            return out

    def stop(self) -> None:
        self._stop.set()


class RunLogger:
    """Logs Ralph loop events to a JSON-lines file."""

    def __init__(self, output_dir: str = "runs"):
        self.run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.output_dir / f"run-{self.run_id}.jsonl"
        self.iteration = 0
        self.start_time = time.time()
        self._file = open(self.log_path, "a")  # noqa: SIM115 - lifetime-bound handle, closed in close()
        # DEF-24 — start the background gauge sampler so kv_cache_pct
        # and running_requests reflect the in-flight peak from the last
        # few seconds, not the post-call instantaneous reading that
        # always shows 0 for single-stream Ralph.
        self._gauge_sampler: _VllmGaugeSampler | None = None
        try:
            self._gauge_sampler = _VllmGaugeSampler(self._VLLM_METRICS_URL)
        except Exception:
            self._gauge_sampler = None  # never fatal — fall back to instant gauges

        self.log(
            "run_start",
            "system",
            {
                "run_id": self.run_id,
                "start_time": datetime.now(UTC).isoformat(),
            },
        )

    def log(
        self,
        event_type: str,
        agent: str,
        data: dict[str, Any],
        include_gpu: bool = False,
    ) -> None:
        """Write a structured event to the log."""
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
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
        self.log(
            "tool_call",
            agent,
            {
                "tool": tool_name,
                "args": {k: v[:200] if isinstance(v, str) else v for k, v in args.items()},
            },
        )

    def log_tool_result(self, agent: str, tool_name: str, result: str) -> None:
        self.log(
            "tool_result",
            agent,
            {
                "tool": tool_name,
                "result": result[:500],
            },
        )

    def log_agent_response(self, agent: str, text: str, tokens: dict | None = None) -> None:
        self.log(
            "agent_response",
            agent,
            {
                "text": text[:1000],
                "tokens": tokens,
            },
        )

    def log_revert(self, agent: str, reason: str, result: str) -> None:
        self.log(
            "revert",
            agent,
            {
                "reason": reason,
                "result": result[:500],
            },
            include_gpu=True,
        )

    def log_error(self, agent: str, error: str) -> None:
        self.log("error", agent, {"error": error[:500]})

    def log_summary(self, data: dict) -> None:
        self.log("run_complete", "system", data, include_gpu=True)
        if self._gauge_sampler is not None:
            self._gauge_sampler.stop()
        self._file.close()

    # vLLM Prometheus endpoint — configurable via env var so a relocated
    # serving layer doesn't require a code change. Defaults to the port
    # the Gemma 4 31B director runs on in the reference deployment.
    _VLLM_METRICS_URL = os.environ.get("VLLM_METRICS_URL", "http://localhost:8050/metrics")

    # The four gauges we actually care about for a snapshot. Each line
    # in the /metrics output looks like:
    #   vllm:kv_cache_usage_perc{engine="0",model_name="..."} 0.23
    # so we extract the value after the closing brace.
    _VLLM_GAUGE_NAMES: ClassVar[dict[str, str]] = {
        "num_requests_running": "running",
        "num_requests_waiting": "waiting",
        "kv_cache_usage_perc": "kv_cache_pct",
    }

    # Prefix-cache hit rate and MTP acceptance are derived from
    # cumulative counters. spec_decode_* are populated only when MTP
    # speculative decoding is enabled (vLLM 0.21+ with the gemma4
    # MTP drafter — see ADR-0018). Pre-MTP runs have these series at
    # zero; the snap dict omits the mtp_* fields then.
    _VLLM_CUM_NAMES: ClassVar[dict[str, str]] = {
        "prefix_cache_queries_total": "prefix_queries",
        "prefix_cache_hits_total": "prefix_hits",
        "spec_decode_num_drafts_total": "mtp_drafts",
        "spec_decode_num_draft_tokens_total": "mtp_drafted",
        "spec_decode_num_accepted_tokens_total": "mtp_accepted",
    }

    def _capture_vllm_metrics(self) -> dict | None:
        """Snapshot of vLLM's /metrics endpoint.

        Returns ``None`` if vLLM isn't reachable — the caller treats
        the absent field as "no telemetry available," same convention
        as a failed nvidia-smi. Does NOT pull a full Prometheus parse;
        just greps the handful of lines we care about so this stays
        subsecond and adds negligible overhead to each logged event.

        Instantaneous gauges (kv_cache_usage_perc, num_requests_running,
        num_requests_waiting) come from the background sampler's
        rolling-window peak (DEF-24) so they reflect recent in-flight
        work rather than the post-call zero state. Cumulative counters
        (prefix_*, spec_decode_*) read fine from the instant scrape.
        """
        try:
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
            name = prefixed[len("vllm:") :]
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

        # DEF-24 — prefer the rolling-window peak over the instant gauge
        # for instantaneous metrics. The sampler is running in a daemon
        # thread; .peak() is O(window samples) and lock-cheap.
        peak: dict[str, float] = {}
        if self._gauge_sampler is not None:
            peak = self._gauge_sampler.peak()

        def _gauge(short_name: str, vllm_name: str, default: float = 0.0) -> float:
            # Peak from sampler wins if any sample landed; instant gauge
            # is the fallback for the first few seconds before the
            # sampler has data.
            if vllm_name in peak:
                return peak[vllm_name]
            return gauges.get(short_name, default)

        snap: dict = {
            "running": int(_gauge("running", "num_requests_running")),
            "waiting": int(_gauge("waiting", "num_requests_waiting")),
            # vLLM reports kv_cache_usage_perc as a FRACTION in [0, 1],
            # not a percent. Convert to % once here so the dashboard
            # doesn't have to guess.
            "kv_cache_pct": round(_gauge("kv_cache_pct", "kv_cache_usage_perc") * 100, 1),
        }
        q = cumul.get("prefix_queries", 0)
        h = cumul.get("prefix_hits", 0)
        if q > 0:
            snap["prefix_hit_rate"] = round(h / q, 3)
            snap["prefix_queries_total"] = int(q)
            snap["prefix_hits_total"] = int(h)
        # DEF-25: MTP cumulative metrics for the dashboard tile.
        # acceptance and tokens_per_step are derived; raw totals kept
        # for any downstream analysis that wants per-event deltas.
        d = cumul.get("mtp_drafts", 0)
        dt_ = cumul.get("mtp_drafted", 0)
        a = cumul.get("mtp_accepted", 0)
        if d > 0:
            snap["mtp_acceptance"] = round(a / dt_, 3) if dt_ > 0 else None
            snap["mtp_tokens_per_step"] = round(1.0 + a / d, 3)
            snap["mtp_drafts_total"] = int(d)
            snap["mtp_accepted_total"] = int(a)
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
                    gpus.append(
                        {
                            "index": int(parts[0]),
                            "name": parts[1],
                            "memory_used_mib": int(parts[2]),
                            "memory_total_mib": int(parts[3]),
                            "utilization_pct": int(parts[4]),
                            "temperature_c": int(parts[5]),
                            "power_w": float(parts[6]),
                        }
                    )
            return gpus
        except Exception:
            return []
