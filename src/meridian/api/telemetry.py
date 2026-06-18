"""OpenTelemetry setup for the Meridian API gateway.

Instruments FastAPI automatically; exports traces to the OTLP endpoint
configured via the `OTEL_EXPORTER_OTLP_ENDPOINT` environment variable
(defaults to `http://localhost:4317` for a local Jaeger or OTEL Collector).

If the endpoint is unreachable, tracing is silently disabled so the API
continues to function normally.
"""

from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def configure_telemetry(service_name: str = "meridian-api") -> None:
    """Set up the global TracerProvider.

    Call once at application startup.  Silently disables tracing if no
    OTLP endpoint is reachable or if the exporter package is missing.
    """
    resource = Resource(attributes={SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
            provider.add_span_processor(BatchSpanProcessor(exporter))
        except Exception:
            pass  # network unavailable or package missing — fail silently

    trace.set_tracer_provider(provider)


def instrument_fastapi(app: object) -> None:
    """Auto-instrument a FastAPI app with OpenTelemetry."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)  # type: ignore[arg-type]
    except Exception:
        pass  # instrumentation package missing — fail silently
