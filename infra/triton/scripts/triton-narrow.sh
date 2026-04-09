#!/usr/bin/env bash
#
# triton-narrow.sh — run a single-GPU Triton+vLLM instance pinned to one L4.
#
# Called by the systemd template unit triton@.service with the GPU index
# as the only argument. The unit's instance specifier (%i) is the GPU index:
#   triton@0.service -> GPU 0
#   triton@1.service -> GPU 1
#   triton@2.service -> GPU 2
#   triton@3.service -> GPU 3
#
# Per-GPU port allocation (host-side):
#   HTTP    = 8000 + i*10   (e.g., GPU 2 -> 8020)
#   gRPC    = 8001 + i*10
#   Metrics = 8002 + i*10
#
# Inside the container, the GPU is always exposed as device index 0
# (the container's view), so CUDA_VISIBLE_DEVICES=0 inside the container.
# This is the workaround for triton-inference-server/server#7786.
# See ADR-0013 and ADR-0014.

set -euo pipefail

GPU_INDEX="${1:?usage: triton-narrow.sh <gpu_index>}"

if ! [[ "${GPU_INDEX}" =~ ^[0-9]+$ ]]; then
    echo "triton-narrow.sh: GPU index must be a non-negative integer, got: ${GPU_INDEX}" >&2
    exit 64
fi

HTTP_PORT=$((8000 + GPU_INDEX * 10))
GRPC_PORT=$((8001 + GPU_INDEX * 10))
METRICS_PORT=$((8002 + GPU_INDEX * 10))

# Shared defaults — TRITON_IMAGE, TRITON_MODEL_REPO, TRITON_LOG_DIR, TRITON_LOG_VERBOSE
# shellcheck disable=SC1091
source /data/triton/config/triton-defaults.env

CONTAINER_NAME="triton-gpu${GPU_INDEX}"

# Idempotent: if a stale container is around (e.g., from a crash), remove it.
docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

exec docker run --rm \
    --name "${CONTAINER_NAME}" \
    --gpus "device=${GPU_INDEX}" \
    -e CUDA_VISIBLE_DEVICES=0 \
    -p "${HTTP_PORT}:8000" \
    -p "${GRPC_PORT}:8001" \
    -p "${METRICS_PORT}:8002" \
    -v "${TRITON_MODEL_REPO}:/models:ro" \
    -v "${TRITON_LOG_DIR}:/logs" \
    --shm-size=1g \
    --ulimit memlock=-1 \
    --ulimit stack=67108864 \
    "${TRITON_IMAGE}" \
    tritonserver \
        --model-repository=/models \
        --model-control-mode=explicit \
        --strict-model-config=false \
        --log-verbose="${TRITON_LOG_VERBOSE}"
