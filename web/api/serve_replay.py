"""Minimal FastAPI server to feed JSONL run data to the frontend.

Serves run events via Server-Sent Events (SSE) for real-time-feeling
replay, and a REST endpoint for the full event list.

Usage:
    uvicorn web.api.serve_replay:app --host 0.0.0.0 --port 8080
"""

import asyncio
import json
import os
import time
from pathlib import Path

import yaml
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

app = FastAPI(title="GemmaForge API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

RUNS_DIR = Path("/data/code/gemma-forge/runs")
DASHBOARD_CONFIG = Path("/data/code/gemma-forge/config/dashboard.yaml")


def _load_dashboard_config() -> dict:
    """Read dashboard config on each request so changes take effect
    without an API restart. Returns sensible defaults if the file is
    missing or malformed."""
    defaults = {
        "demo_run": None,
        "demo_speed": 20,
        "live_mtime_threshold_s": 30,
    }
    if not DASHBOARD_CONFIG.is_file():
        return defaults
    try:
        loaded = yaml.safe_load(DASHBOARD_CONFIG.read_text()) or {}
    except yaml.YAMLError:
        return defaults
    return {**defaults, **loaded}


@app.get("/api/runs")
def list_runs():
    """List available run logs."""
    runs = []
    for f in sorted(RUNS_DIR.glob("run-*.jsonl"), reverse=True):
        events = f.read_text().strip().split("\n")
        first = json.loads(events[0]) if events else {}
        last = json.loads(events[-1]) if events else {}
        runs.append({
            "filename": f.name,
            "events": len(events),
            "start": first.get("timestamp", ""),
            "elapsed_s": last.get("elapsed_s", 0),
            "summary": last.get("data", {}) if last.get("event_type") == "run_complete" else {},
        })
    return runs


@app.get("/api/runs/{filename}/events")
def get_run_events(filename: str):
    """Get all events from a run log as a JSON array."""
    path = RUNS_DIR / filename
    if not path.exists():
        return {"error": "not found"}
    events = []
    for line in path.read_text().strip().split("\n"):
        if line.strip():
            events.append(json.loads(line))
    return events


@app.get("/api/runs/{filename}/stream")
async def stream_run(
    filename: str,
    speed: float = Query(default=5.0),
    start_from: float = Query(default=0.0, description="Skip to this elapsed_s mark before streaming"),
    resync: bool = Query(default=False, description="If true, emit skipped events instantly (no delay) up to start_from so the frontend can fast-forward its state"),
):
    """Stream run events as SSE at accelerated speed.

    Speed multiplier: 1.0 = real-time, 5.0 = 5x faster, etc.

    `start_from` and `resync` exist so the frontend can change the
    replay speed mid-stream without visually resetting the run: it
    passes the current elapsed_s and gets the new stream picking up
    at exactly that point. Without this, a speed change would restart
    the replay from the beginning (jarring during a demo).
    """
    path = RUNS_DIR / filename
    if not path.exists():
        return {"error": "not found"}

    events = []
    for line in path.read_text().strip().split("\n"):
        if line.strip():
            events.append(json.loads(line))

    async def event_generator():
        # Two phases:
        # 1. Skip (or burst-send) events before start_from.
        # 2. Resume normal speed-paced streaming from start_from onward.
        last_elapsed = start_from
        started = False
        for event in events:
            elapsed = event.get("elapsed_s", 0)
            if elapsed < start_from:
                if resync:
                    # Emit the skipped history so the client can rebuild state.
                    yield f"data: {json.dumps(event)}\n\n"
                continue
            if not started:
                started = True
                last_elapsed = elapsed
                yield f"data: {json.dumps(event)}\n\n"
                continue
            delay = (elapsed - last_elapsed) / speed
            # Skip micro-sleeps (<1ms) — asyncio.sleep has meaningful
            # scheduling overhead that dominates at high replay speeds
            # (1000x+), and the client won't perceive the difference.
            # Upper cap prevents a 1-hour-long real-time gap from
            # stalling the stream.
            if 0.001 < delay < 10:
                await asyncio.sleep(delay)
            last_elapsed = elapsed
            yield f"data: {json.dumps(event)}\n\n"
        yield f"data: {json.dumps({'event_type': 'stream_end'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/live-stream")
async def stream_live(poll_interval: float = Query(default=2.0)):
    """Stream the latest run's events as they're written (live mode).

    Watches the newest JSONL file and sends new lines as SSE events.
    """
    import os

    jsonl_files = sorted(RUNS_DIR.glob("run-*.jsonl"), reverse=True)
    if not jsonl_files:
        return {"error": "no runs found"}

    path = jsonl_files[0]

    async def live_generator():
        last_pos = 0
        idle_count = 0
        while True:
            current_size = os.path.getsize(path)
            if current_size > last_pos:
                with open(path) as f:
                    f.seek(last_pos)
                    for line in f:
                        line = line.strip()
                        if line:
                            yield f"data: {line}\n\n"
                    last_pos = f.tell()
                idle_count = 0
            else:
                idle_count += 1
                if idle_count > 300:  # 10 minutes with no data
                    yield f"data: {json.dumps({'event_type': 'stream_end'})}\n\n"
                    break
            await asyncio.sleep(poll_interval)

    return StreamingResponse(
        live_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/state")
def get_dashboard_state():
    """What should the dashboard show right now?

    Returns the live status (is a run actively writing?) and the
    designated demo run for replay-when-idle. The frontend uses this
    on page load to decide between auto-connecting to live or starting
    the demo replay loop — there is no "disconnected empty page" state
    by design (see journey/N for the rationale).
    """
    cfg = _load_dashboard_config()
    threshold = cfg["live_mtime_threshold_s"]

    jsonl_files = sorted(RUNS_DIR.glob("run-*.jsonl"), reverse=True)
    most_recent = jsonl_files[0] if jsonl_files else None

    live = False
    live_run_filename = None
    if most_recent is not None:
        age = time.time() - most_recent.stat().st_mtime
        if age < threshold:
            live = True
            live_run_filename = most_recent.name

    # Demo run selection: explicit config wins; fall back to the most
    # recent run that actually has a run_complete summary (so we don't
    # accidentally pick a smoke test).
    demo_run = cfg["demo_run"]
    if demo_run is None:
        for f in jsonl_files:
            try:
                last = json.loads(f.read_text().strip().split("\n")[-1])
                if last.get("event_type") == "run_complete":
                    demo_run = f.name
                    break
            except (json.JSONDecodeError, IndexError):
                continue

    # Validate the configured demo_run actually exists; if not, fall
    # back so the frontend never hits a 404 on auto-replay.
    if demo_run and not (RUNS_DIR / demo_run).is_file():
        demo_run = most_recent.name if most_recent else None

    return {
        "live": live,
        "live_run_filename": live_run_filename,
        "demo_run": demo_run,
        "demo_speed": cfg["demo_speed"],
    }


@app.get("/api/gpu")
def get_gpu_state():
    """Get current GPU state via nvidia-smi."""
    import subprocess
    try:
        result = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=index,name,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
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
    except Exception as e:
        return {"error": str(e)}
