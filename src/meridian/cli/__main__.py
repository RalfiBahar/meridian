"""Entry point for `python -m meridian.cli`."""

from __future__ import annotations

import click

from meridian.cli.health import health


@click.group()
def cli() -> None:
    """Meridian command-line interface."""


cli.add_command(health)


if __name__ == "__main__":
    cli()
