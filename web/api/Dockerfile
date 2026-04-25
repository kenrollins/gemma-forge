# Replay API — serves run logs + dashboard config to the UI over REST/SSE.
#
# Deliberately slim: this service only reads JSONL files and YAML config,
# so it does NOT install the full gemma-forge runtime (no vLLM client,
# no Graphiti, no NVML). Pinned to the same FastAPI / uvicorn / PyYAML
# versions declared in pyproject.toml.
#
# Build context is the repo root (docker-compose build-context: .), so
# paths are relative to the project root, not to web/api/.

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.111,<1.0" \
    "uvicorn[standard]>=0.30,<1.0" \
    "pyyaml>=6.0,<7.0"

# Only the module that actually serves traffic. Runs (JSONL) and
# dashboard config (YAML) are mounted at runtime so they can be
# updated without rebuilding the image. web/ and web/api/ resolve as
# PEP 420 namespace packages — no __init__.py needed.
COPY web/api/serve_replay.py /app/web/api/serve_replay.py

EXPOSE 8080

# Default data locations inside the container. Compose mounts host
# `runs/` and `config/` into these paths.
ENV FORGE_RUNS_DIR=/app/runs \
    FORGE_DASHBOARD_CONFIG=/app/config/dashboard.yaml

CMD ["uvicorn", "web.api.serve_replay:app", "--host", "0.0.0.0", "--port", "8080"]
