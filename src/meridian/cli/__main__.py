"""Entry point for `python -m meridian.cli`."""

from __future__ import annotations

import click

from meridian.cli.analytics import analytics
from meridian.cli.arb import arb
from meridian.cli.health import health
from meridian.cli.ingest import ingest
from meridian.cli.kalshi import kalshi
from meridian.cli.markets import markets
from meridian.cli.migrate import migrate
from meridian.cli.polymarket import polymarket


@click.group()
def cli() -> None:
    """Meridian command-line interface."""


cli.add_command(analytics)
cli.add_command(arb)
cli.add_command(health)
cli.add_command(ingest)
cli.add_command(migrate)
cli.add_command(kalshi)
cli.add_command(polymarket)
cli.add_command(markets)


if __name__ == "__main__":
    cli()
