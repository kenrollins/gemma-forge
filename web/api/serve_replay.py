"""Minimal FastAPI server to feed JSONL run data to the frontend.

Serves run events via Server-Sent Events (SSE) for real-time-feeling
replay, and a REST endpoint for the full event list.

Usage:
    uvicorn web.api.serve_replay:app --host 0.0.0.0 --port 8080
"""

import json
import asyncio
from pathlib import Path
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
async def stream_run(filename: str, speed: float = Query(default=5.0)):
    """Stream run events as SSE at accelerated speed.

    Speed multiplier: 1.0 = real-time, 5.0 = 5x faster, etc.
    """
    path = RUNS_DIR / filename
    if not path.exists():
        return {"error": "not found"}

    events = []
    for line in path.read_text().strip().split("\n"):
        if line.strip():
            events.append(json.loads(line))

    async def event_generator():
        last_elapsed = 0.0
        for event in events:
            elapsed = event.get("elapsed_s", 0)
            delay = (elapsed - last_elapsed) / speed
            if delay > 0 and delay < 10:  # cap max delay at 10s even at 1x
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
