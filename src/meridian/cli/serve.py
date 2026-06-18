"""CLI command: `meridian serve` — start the FastAPI gateway with uvicorn."""

from __future__ import annotations

import click


@click.command(name="serve")
@click.option("--host", default="0.0.0.0", show_default=True, help="Bind host.")
@click.option("--port", default=8000, show_default=True, type=int, help="Bind port.")
@click.option("--workers", default=1, show_default=True, type=int, help="Uvicorn workers.")
@click.option("--reload", is_flag=True, default=False, help="Auto-reload on code changes.")
@click.option(
    "--metrics-port",
    default=9093,
    show_default=True,
    type=int,
    help="Prometheus /metrics port (0 to disable).",
)
def serve(
    host: str,
    port: int,
    workers: int,
    reload: bool,
    metrics_port: int,
) -> None:
    """Start the Meridian FastAPI gateway.

    Exposes REST and WebSocket endpoints for the quant terminal frontend.
    Prometheus metrics are served on a separate port.
    """
    import uvicorn

    if metrics_port:
        from meridian.metrics import start_metrics_server

        start_metrics_server(metrics_port)

    uvicorn.run(
        "meridian.api.app:app",
        host=host,
        port=port,
        workers=workers,
        reload=reload,
    )
