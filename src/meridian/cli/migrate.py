"""CLI subcommand: apply pending database migrations."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click

from meridian.config import get_settings
from meridian.db.migrate import MigrationError, apply_pending
from meridian.logging import configure_logging, get_logger


async def _run(migrations_dir: Path) -> int:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("meridian.migrate")

    log.info("migrate.start", dir=str(migrations_dir))
    try:
        applied = await apply_pending(settings, migrations_dir)
    except MigrationError as exc:
        log.error("migrate.failed", error=str(exc))
        return 1

    if applied:
        log.info("migrate.applied", versions=applied, count=len(applied))
    else:
        log.info("migrate.up_to_date")
    return 0


@click.command(name="migrate")
@click.option(
    "--dir",
    "migrations_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path("migrations"),
    show_default=True,
    help="Directory containing numbered .sql migration files.",
)
def migrate(migrations_dir: Path) -> None:
    """Apply pending database migrations idempotently."""
    sys.exit(asyncio.run(_run(migrations_dir)))
