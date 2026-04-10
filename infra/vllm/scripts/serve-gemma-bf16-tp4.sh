#!/usr/bin/env bash
# GemmaForge — Gemma 4 31B bf16 full precision on all 4 L4s (TP=4)
#
# No quantization. Full precision. 2× faster than GB10 DGX Spark.
# 17,968 token KV cache. max_model_len=16384.

set -euo pipefail

VLLM_IMAGE="${VLLM_IMAGE:-gemma-forge/vllm:latest}"
WEIGHTS_DIR="${WEIGHTS_DIR:-/data/triton/weights}"
CONTAINER_NAME="gemma-forge-gemma"

docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

exec docker run --rm \
    --name "${CONTAINER_NAME}" \
    --gpus '"device=0,1,2,3"' \
    -e CUDA_VISIBLE_DEVICES=0,1,2,3 \
    -p 8050:8000 \
    -v "${WEIGHTS_DIR}:/weights:ro" \
    --shm-size=4g \
    --ulimit memlock=-1 \
    --ulimit stack=67108864 \
    "${VLLM_IMAGE}" \
    --model /weights/gemma-4-31B-it \
    --tensor-parallel-size 4 \
    --max-model-len 16384 \
    --gpu-memory-utilization 0.92 \
    --dtype bfloat16 \
    --enforce-eager \
    --max-num-seqs 8 \
    --enable-auto-tool-choice \
    --tool-call-parser gemma4
