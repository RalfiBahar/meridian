"""Entry point for `python -m meridian.cli`."""

from __future__ import annotations

import click

from meridian.cli.health import health
from meridian.cli.ingest import ingest
from meridian.cli.kalshi import kalshi
from meridian.cli.migrate import migrate


@click.group()
def cli() -> None:
    """Meridian command-line interface."""


cli.add_command(health)
cli.add_command(ingest)
cli.add_command(migrate)
cli.add_command(kalshi)


if __name__ == "__main__":
    cli()
