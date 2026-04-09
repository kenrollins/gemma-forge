#!/usr/bin/env bash
#
# serve.sh — start a vLLM OpenAI-compatible server for one GemmaForge role.
#
# Usage:
#   serve.sh <container_name> <model_path> <gpu_spec> <host_port> [extra_vllm_args...]
#
# Examples:
#   serve.sh gemma-forge-architect /weights/Gemma-4-31B-IT-NVFP4 0,1 8050 --tensor-parallel-size 2
#   serve.sh gemma-forge-auditor  /weights/gemma-4-E4B-it        2   8060
#   serve.sh gemma-forge-sentry   /weights/gemma-4-E2B-it        3   8070
#
# Called by the gemma-forge-{architect,auditor,sentry}.service systemd units.
# See infra/vllm/README.md.

set -euo pipefail

CONTAINER_NAME="${1:?usage: serve.sh <name> <model> <gpus> <port> [vllm_args...]}"
MODEL_PATH="${2:?}"
GPU_SPEC="${3:?}"
HOST_PORT="${4:?}"
shift 4
EXTRA_ARGS=("$@")

VLLM_IMAGE="${VLLM_IMAGE:-gemma-forge/vllm:latest}"
WEIGHTS_DIR="${WEIGHTS_DIR:-/data/triton/weights}"
LOG_DIR="${LOG_DIR:-/data/triton/logs}"
SHM_SIZE="${SHM_SIZE:-2g}"

# Figure out CUDA_VISIBLE_DEVICES for inside the container.
# Docker --gpus exposes only the requested GPUs; inside the container
# they're renumbered starting at 0.
IFS=',' read -ra GPU_ARRAY <<< "${GPU_SPEC}"
NUM_GPUS=${#GPU_ARRAY[@]}
if [[ ${NUM_GPUS} -eq 1 ]]; then
    CUDA_INSIDE="0"
else
    # e.g., 2 GPUs → "0,1"
    CUDA_INSIDE=$(seq -s, 0 $((NUM_GPUS - 1)))
fi

# Idempotent cleanup
docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

exec docker run --rm \
    --name "${CONTAINER_NAME}" \
    --gpus "\"device=${GPU_SPEC}\"" \
    -e CUDA_VISIBLE_DEVICES="${CUDA_INSIDE}" \
    -p "${HOST_PORT}:8000" \
    -v "${WEIGHTS_DIR}:/weights:ro" \
    --shm-size="${SHM_SIZE}" \
    --ulimit memlock=-1 \
    --ulimit stack=67108864 \
    "${VLLM_IMAGE}" \
    --model "${MODEL_PATH}" \
    --gpu-memory-utilization 0.90 \
    --dtype auto \
    --enable-auto-tool-choice \
    --tool-call-parser gemma4 \
    "${EXTRA_ARGS[@]}"
