#!/usr/bin/env bash
# Bake-off ARM D — Gemma 4 12B FP8, TP=2, 2 L4s (no MTP).
# The KV-headroom / concurrency candidate: FP8 weights are ~6 GB/GPU, leaving
# a large KV budget — push max-model-len and batch concurrency. Its reason to
# exist is long-context + high-throughput, so MTP is omitted to keep the
# throughput measurement clean (add it back later if D is the survivor).
# ADR-0024 / futures/gemma4-12b-fleet-preflight.md
#
# Two instances span the box:
#     GPUS=0,1 PORT=8057 ./serve-12b-fp8-tp2.sh
#     GPUS=2,3 PORT=8058 ./serve-12b-fp8-tp2.sh
#
# Run the 31B down first (./stop-31b.sh). No MTP -> stable image is fine.

set -euo pipefail

GPUS="${GPUS:-0,1}"
PORT="${PORT:-8057}"
VLLM_IMAGE="${VLLM_IMAGE:-vllm/vllm-openai:latest}"
WEIGHTS_DIR="${WEIGHTS_DIR:-/data/triton/weights}"
MODEL="${MODEL:-/weights/gemma-4-12B-it-FP8}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-32768}"   # D's whole point: lean on KV headroom
CONTAINER_NAME="bakeoff-12b-fp8-tp2-gpu${GPUS//,/-}"

# --- preflight: weights present ---
host="${WEIGHTS_DIR}${MODEL#/weights}"
[[ -e "${host}" ]] || { echo "[arm-D] missing weights: ${host}"; echo "         pull per the preflight checklist."; exit 1; }

# --- preflight: target GPUs free (31B must be stopped first) ---
IFS=',' read -r -a gpu_arr <<< "${GPUS}"
for g in "${gpu_arr[@]}"; do
    used="$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i "${g}" | tr -d ' ')"
    if (( used > 2000 )); then
        echo "[arm-D] GPU${g} shows ${used} MiB used — is the 31B still up? Run ./stop-31b.sh first."
        exit 1
    fi
done

docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

echo "[arm-D] launching 12B FP8 TP=2 on GPUs ${GPUS} -> :${PORT} (len=${MAX_MODEL_LEN}, container ${CONTAINER_NAME})"
exec docker run --rm \
    --name "${CONTAINER_NAME}" \
    --gpus "\"device=${GPUS}\"" \
    -e CUDA_VISIBLE_DEVICES="${GPUS}" \
    -p "${PORT}:8000" \
    -v "${WEIGHTS_DIR}:/weights:ro" \
    --shm-size=4g \
    --ulimit memlock=-1 \
    --ulimit stack=67108864 \
    "${VLLM_IMAGE}" \
    --model "${MODEL}" \
    --tensor-parallel-size 2 \
    --quantization fp8 \
    --kv-cache-dtype fp8 \
    --max-model-len "${MAX_MODEL_LEN}" \
    --max-num-batched-tokens 8192 \
    --gpu-memory-utilization 0.90 \
    --max-num-seqs 16 \
    --enable-auto-tool-choice \
    --tool-call-parser gemma4
