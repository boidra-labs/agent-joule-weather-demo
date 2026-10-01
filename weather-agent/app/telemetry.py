"""Basic OpenTelemetry setup.

Primary path: `sap_cloud_sdk` `auto_instrument()` — wires OTLP export and
auto-instruments LiteLLM, LangChain and httpx in one call.

Fallback path (if auto_instrument is unavailable or fails): plain OTel SDK with
an OTLP/HTTP exporter when OTEL_EXPORTER_OTLP_ENDPOINT is set, otherwise a
console exporter when OTEL_CONSOLE_EXPORTER=true (handy for local demos).

Also exposes a few shared metric instruments used by the agent and tools.
"""

from __future__ import annotations

import logging
import os

from opentelemetry import metrics, trace

logger = logging.getLogger(__name__)

SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "weather-agent")


def _fallback_sdk_setup() -> None:
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
        SimpleSpanProcessor,
    )

    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))

    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        # Endpoint + headers are read from OTEL_EXPORTER_OTLP_* env vars
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        logger.info("OTel fallback: OTLP/HTTP span exporter enabled")
    elif os.environ.get("OTEL_CONSOLE_EXPORTER", "").lower() == "true":
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        logger.info("OTel fallback: console span exporter enabled")

    trace.set_tracer_provider(provider)

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except Exception:
        logger.debug("httpx instrumentation not available")


def init_telemetry() -> None:
    """Initialize tracing. Must run BEFORE LangChain / LiteLLM are imported."""
    # auto_instrument() silently disables itself without an OTLP endpoint,
    # so use the plain SDK when only console output is wanted (local demo).
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        if os.environ.get("OTEL_CONSOLE_EXPORTER", "").lower() == "true":
            _fallback_sdk_setup()
        else:
            logger.info("No OTEL_EXPORTER_OTLP_ENDPOINT — tracing disabled")
        return
    try:
        from sap_cloud_sdk.core.telemetry import auto_instrument

        auto_instrument()
        logger.info("OTel initialized via sap_cloud_sdk auto_instrument()")
    except Exception:
        logger.warning("auto_instrument() failed — using plain OTel SDK fallback", exc_info=True)
        _fallback_sdk_setup()


# ---------------------------------------------------------------------------
# Shared instruments (no-op until a MeterProvider is configured)
# ---------------------------------------------------------------------------

_meter = metrics.get_meter(SERVICE_NAME)

agent_requests = _meter.create_counter(
    "agent.requests", unit="1", description="A2A requests handled by the agent"
)
tool_calls = _meter.create_counter(
    "agent.tool.calls", unit="1", description="Tool invocations by tool name and outcome"
)
tool_duration = _meter.create_histogram(
    "agent.tool.duration", unit="ms", description="Tool execution time"
)
