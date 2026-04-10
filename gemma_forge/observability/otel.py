"""OpenTelemetry setup for GemmaForge.

Initializes the OTel SDK with an OTLP exporter pointed at the
collector in the compose stack. All spans use the OpenTelemetry
GenAI semantic conventions (gen_ai.* attributes) for token accounting.

Usage:
    from gemma_forge.observability.otel import init_telemetry, get_tracer
    init_telemetry()
    tracer = get_tracer()
"""

import logging
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)

_initialized = False


def init_telemetry(
    service_name: str = "gemma-forge.harness",
    otlp_endpoint: str | None = None,
) -> None:
    """Initialize OpenTelemetry with OTLP export to the collector.

    Safe to call multiple times — only initializes once.
    Set OTEL_EXPORTER_OTLP_ENDPOINT env var to override the endpoint.
    """
    global _initialized
    if _initialized:
        return

    endpoint = otlp_endpoint or os.environ.get(
        "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"
    )

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": "0.0.1",
            "deployment.environment": "demo",
        }
    )

    provider = TracerProvider(resource=resource)

    try:
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        logger.info("OTel initialized: exporting to %s", endpoint)
    except Exception as e:
        logger.warning(
            "OTel export failed to connect to %s: %s (traces will be local only)",
            endpoint, e,
        )

    trace.set_tracer_provider(provider)
    _initialized = True


def get_tracer(name: str = "gemma_forge") -> trace.Tracer:
    """Get a named tracer for creating spans."""
    return trace.get_tracer(name)


def span_attributes_for_llm_call(
    model: str,
    role: str,
    skill: str = "",
    run_id: str = "",
) -> dict:
    """Build the standard span attributes for an LLM call.

    Uses the OpenTelemetry GenAI semantic conventions (gen_ai.*).
    """
    attrs = {
        "gen_ai.system": "vllm",
        "gen_ai.request.model": model,
        "forge.role": role,
    }
    if skill:
        attrs["forge.skill"] = skill
    if run_id:
        attrs["forge.run_id"] = run_id
    return attrs


def record_token_usage(
    span: trace.Span,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    """Record token usage on a span using GenAI semantic conventions."""
    span.set_attribute("gen_ai.usage.input_tokens", prompt_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", completion_tokens)
    span.set_attribute(
        "gen_ai.usage.total_tokens", prompt_tokens + completion_tokens
    )
