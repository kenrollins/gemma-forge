# GemmaForge vLLM serving image — Gemma 4 ready, MTP-capable.
#
# vllm/vllm-openai:latest from 2026-05-15 onward ships vLLM 0.21.0 +
# transformers 5.8.1, which is sufficient for both base Gemma 4 inference
# and the Gemma 4 MTP speculative decoding path (PR #41745, merged
# 2026-05-06 — requires transformers >= 5.8.0.dev0).
#
# This image is used by the systemd runner scripts at /data/triton/scripts/
# and by any ad-hoc `vllm serve` commands during development.
#
# Build:
#   docker build -t gemma4-vllm:latest -f infra/vllm/Dockerfile .

FROM vllm/vllm-openai:latest

# Defensive floor: MTP requires transformers >= 5.8.0 for the
# gemma4_assistant model type. Base image already satisfies; this is
# documentation + a safety net for future base-image churn.
RUN pip install --no-cache-dir 'transformers>=5.8.0' && \
    python3 -c "import vllm; print('vLLM', vllm.__version__); import transformers; print('transformers', transformers.__version__)"

# The base image's entrypoint is `vllm serve` — we keep that.
