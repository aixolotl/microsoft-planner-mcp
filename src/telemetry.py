from __future__ import annotations
import logging
import os

"""Optional OpenTelemetry SDK initialisation.

FastMCP ships with native OTEL instrumentation via the opentelemetry-api
package (already a transitive dependency). Without an SDK configured, all
OTEL calls are silent no-ops with zero CPU/memory overhead. This module
activates export when OTEL_EXPORTER_OTLP_ENDPOINT is set, forwarding traces
to any OTLP-compatible backend (Jaeger, Datadog, Grafana Tempo, New Relic…).

FastMCP automatically produces spans for every tool call, resource read, and
prompt render — including auth attributes (enduser.id, enduser.scope) and
mcp.session.id. No manual span creation is required for basic observability.
Docs: https://gofastmcp.com/servers/telemetry

IMPORTANT: configure() must be called before any FastMCP symbols are used
(i.e. before `from fastmcp import FastMCP` in server.py). FastMCP resolves
the active TracerProvider at startup; calling set_tracer_provider() after
that has no effect.
"""

logger = logging.getLogger(__name__)


def configure() -> None:
    """Activate the OpenTelemetry SDK if the endpoint env var is set.

    Safe to call unconditionally — skips silently when:
    - OTEL_EXPORTER_OTLP_ENDPOINT is absent (default for local dev)
    - The opentelemetry-sdk package is not installed

    Without this function being called first, FastMCP's built-in
    instrumentation runs in no-op mode and no traces are exported, making it
    impossible to observe distributed latency or error rates in production.
    Docs: https://gofastmcp.com/servers/telemetry#enabling-telemetry
    """
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        # No backend configured — leave the zero-overhead no-op API active.
        return

    try:
        # These packages are optional (see pyproject.toml [dependency-groups.otel]).
        # Importing inside configure() avoids an ImportError at server startup
        # when the otel group is not installed, so the server still starts
        # without OTEL configured.
        from opentelemetry import trace  # noqa: PLC0415
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # noqa: PLC0415
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: PLC0415
    except ImportError:
        # SDK not installed — warn once so the operator knows why traces are
        # absent even though the env var is set.
        # Install with: uv add --group otel opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc
        logger.warning(
            "OTEL_EXPORTER_OTLP_ENDPOINT is set but the opentelemetry SDK is "
            "not installed. Traces will NOT be exported. "
            "Install the optional dep group: "
            "uv sync --group otel"
        )
        return

    service_name = os.getenv("OTEL_SERVICE_NAME", "microsoft-planner-mcp")
    provider = TracerProvider()
    provider.add_span_processor(
        # BatchSpanProcessor buffers spans and exports asynchronously. Without
        # it (e.g. using SimpleSpanProcessor), every span export would add
        # synchronous network latency to each tool call.
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
    )
    trace.set_tracer_provider(provider)

    logger.info(
        "OpenTelemetry tracing enabled",
        extra={"otlp_endpoint": endpoint, "service_name": service_name},
    )
