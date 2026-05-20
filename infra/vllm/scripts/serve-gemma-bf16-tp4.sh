#!/usr/bin/env bash
# NOTE: This script is no longer the active source for the systemd unit.
# On 2026-04-26 the service was decoupled into /data/triton/. The active copy is
# /data/triton/scripts/serve-gemma4-31b-bf16-tp4.sh, invoked by
# /etc/systemd/system/gemma4-31b-vllm.service. This file is kept for reference
# and is mirrored from the active copy so the repo documents production config.
#
# GemmaForge — Gemma 4 31B bf16 full precision on all 4 L4s (TP=4) + MTP.
#
# 2026-05-20: vLLM 0.21.0 + Gemma 4 MTP speculative decoding (drafter
# google/gemma-4-31B-it-assistant, num_speculative_tokens=2). Measured 2.74×
# tok/s on 4× L4 (15 → 41 tok/s) at ~98% MTP acceptance. KV cache 45,336
# tokens. max_model_len=16384.
#
# Rollback: VLLM_IMAGE=gemma4-vllm:pre-mtp systemctl restart gemma4-31b-vllm

set -euo pipefail

VLLM_IMAGE="${VLLM_IMAGE:-gemma4-vllm:latest}"
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
    --max-num-batched-tokens 4096 \
    --gpu-memory-utilization 0.92 \
    --dtype bfloat16 \
    --enforce-eager \
    --max-num-seqs 8 \
    --enable-auto-tool-choice \
    --tool-call-parser gemma4 \
    --speculative-config '{"method":"mtp","model":"/weights/gemma-4-31B-it-assistant","num_speculative_tokens":2}'
