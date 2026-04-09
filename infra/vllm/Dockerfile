# GemmaForge vLLM serving image — Gemma 4 ready.
#
# The stock vllm/vllm-openai:latest (vLLM 0.19.0) ships transformers 4.57.6,
# which predates the Gemma 4 release (2026-04-02). The `gemma4` model type
# requires transformers >= 4.58. This Dockerfile bakes in the upgrade so
# containers don't need pip install at startup.
#
# This image is used by the systemd runner scripts (Phase 0.5 infrastructure)
# and by any ad-hoc vllm serve commands during development. It does NOT
# include the Triton inference server — see infra/triton/ for the Triton
# integration path (blocked on Triton 26.04, see ADR-0014 / project memory).
#
# Build:
#   docker build -t gemma-forge/vllm:latest -f infra/vllm/Dockerfile .
#
# Usage (example — E2B on GPU 3):
#   docker run --rm --gpus device=3 -e CUDA_VISIBLE_DEVICES=0 \
#     -p 8030:8000 -v /data/triton/weights:/weights:ro \
#     gemma-forge/vllm:latest \
#     --model /weights/gemma-4-E2B-it --max-model-len 4096 \
#     --gpu-memory-utilization 0.85 --dtype bfloat16

FROM vllm/vllm-openai:latest

# Upgrade transformers to a version that recognizes the gemma4 model type.
# Pin to >=4.58 (the first release with Gemma 4 support).
RUN pip install --no-cache-dir 'transformers>=4.58' && \
    python3 -c "import vllm; print('vLLM', vllm.__version__); import transformers; print('transformers', transformers.__version__)"

# The base image's entrypoint is `vllm serve` — we keep that.
