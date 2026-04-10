#!/usr/bin/env bash
#
# serve-auditor.sh — start the Nemotron Auditor on GPUs 2+3 with PP=2.
#
# Called by gemma-forge-auditor.service.
# Uses pipeline parallelism (layers split, not matrices) which gives
# 120x more KV cache than tensor parallelism on the same hardware.

set -euo pipefail

VLLM_IMAGE="${VLLM_IMAGE:-gemma-forge/vllm:latest}"
WEIGHTS_DIR="${WEIGHTS_DIR:-/data/triton/weights}"
CONTAINER_NAME="gemma-forge-auditor"

# Idempotent cleanup
docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

exec docker run --rm \
    --name "${CONTAINER_NAME}" \
    --gpus '"device=2,3"' \
    -e CUDA_VISIBLE_DEVICES=0,1 \
    -p 8060:8000 \
    -v "${WEIGHTS_DIR}:/weights:ro" \
    --shm-size=2g \
    --ulimit memlock=-1 \
    --ulimit stack=67108864 \
    -w /weights/Nemotron-3-Nano-30B-A3B-NVFP4 \
    "${VLLM_IMAGE}" \
    --model /weights/Nemotron-3-Nano-30B-A3B-NVFP4 \
    --pipeline-parallel-size 2 \
    --max-model-len 32768 \
    --gpu-memory-utilization 0.90 \
    --dtype auto \
    --trust-remote-code \
    --enable-auto-tool-choice \
    --tool-call-parser qwen3_coder \
    --reasoning-parser-plugin nano_v3_reasoning_parser.py \
    --reasoning-parser nano_v3
