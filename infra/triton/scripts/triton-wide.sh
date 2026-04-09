#!/usr/bin/env bash
#
# triton-wide.sh — run the wide (multi-GPU tensor-parallel) Triton+vLLM
# instance dedicated to the Gemma 4 31B-IT model.
#
# This instance spans GPUs 0+1 with tensor_parallel_size=2 per the
# official vLLM Gemma 4 recipe (the 31B-IT model does not fit on a
# single L4 at bf16). Architect and Worker share this engine because
# they execute sequentially within a Ralph loop iteration. See ADR-0015.
#
# Called by the systemd unit triton@wide-01.service.
#
# Wide instance port allocation (host-side):
#   HTTP    = 8040
#   gRPC    = 8041
#   Metrics = 8042
#
# These deliberately sit ABOVE the per-GPU narrow allocation (8000-8032)
# so the narrow and wide units can never accidentally collide.
#
# IMPORTANT: when this unit is enabled, triton@0 and triton@1 must NOT
# be enabled — they would compete for the same physical GPUs. The
# install script enforces this by default.
#
# See ADR-0013, ADR-0014, ADR-0015.

set -euo pipefail

INSTANCE_NAME="${1:-wide-01}"

HTTP_PORT=8040
GRPC_PORT=8041
METRICS_PORT=8042

# shellcheck disable=SC1091
source /data/triton/config/triton-defaults.env

CONTAINER_NAME="triton-${INSTANCE_NAME}"

# Idempotent cleanup of any stale instance.
docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

exec docker run --rm \
    --name "${CONTAINER_NAME}" \
    --gpus '"device=0,1"' \
    -e CUDA_VISIBLE_DEVICES=0,1 \
    -p "${HTTP_PORT}:8000" \
    -p "${GRPC_PORT}:8001" \
    -p "${METRICS_PORT}:8002" \
    -v "${TRITON_MODEL_REPO}:/models:ro" \
    -v "${TRITON_LOG_DIR}:/logs" \
    --shm-size=2g \
    --ulimit memlock=-1 \
    --ulimit stack=67108864 \
    "${TRITON_IMAGE}" \
    tritonserver \
        --model-repository=/models \
        --model-control-mode=explicit \
        --strict-model-config=false \
        --log-verbose="${TRITON_LOG_VERBOSE}"
