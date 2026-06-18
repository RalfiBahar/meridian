"""kalshi_fed_pmf — implied Fed-rate PMF from Kalshi KXFED contracts.

Usage (via CLI):
    meridian experiment run kalshi_fed_pmf
    meridian experiment run kalshi_fed_pmf --params '{"fomc_date": "2026-07-30"}'
    meridian experiment run kalshi_fed_pmf --params '{"fetch_cme": true}'

Reads:
    EXPERIMENT_PARAMS env var (JSON dict)

Prints:
    Human-readable PMF table, then on the last line a JSON metrics object.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date

# Resolve repo root so we can import meridian without installing.
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, os.path.join(_REPO, "src"))


async def main() -> None:
    params = json.loads(os.environ.get("EXPERIMENT_PARAMS", "{}"))
    fomc_date_str: str | None = params.get("fomc_date")
    fetch_cme: bool = bool(params.get("fetch_cme", False))

    from meridian.analytics.fedwatch import build_kalshi_pmf, fetch_cme_fedwatch
    from meridian.config import get_settings
    from meridian.db.postgres import pool_context

    settings = get_settings()
    fomc_date: date | None = (
        date.fromisoformat(fomc_date_str) if fomc_date_str else None
    )

    async with pool_context(settings) as pool:
        if fomc_date is None:
            # Auto-detect nearest KXFED close date.
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT DATE(closes_at) AS d
                    FROM markets m
                    JOIN venues v ON v.id = m.venue_id
                    WHERE v.code = 'kalshi'
                      AND m.external_id LIKE 'KXFED-%%'
                      AND m.resolution_status = 'open'
                      AND m.closes_at > now()
                    ORDER BY m.closes_at ASC
                    LIMIT 1
                    """
                )
            if row:
                fomc_date = row["d"]

        if fomc_date is None:
            print("No open KXFED markets found.", file=sys.stderr)
            sys.exit(1)

        pmf = await build_kalshi_pmf(pool, fomc_date)

    if pmf is None:
        print(f"No KXFED p_mid signals for {fomc_date}.", file=sys.stderr)
        sys.exit(1)

    print(pmf.summary())

    if fetch_cme:
        cme = await fetch_cme_fedwatch(fomc_date)
        if cme:
            print("\n--- CME FedWatch ---")
            print(cme.summary())

    # Metrics JSON on the final line (picked up by the experiment runner).
    metrics = {
        "expected_rate": pmf.expected_rate(),
        "entropy_bits": pmf.entropy(),
        "n_strikes": len(pmf.strikes),
        "fomc_date": str(fomc_date),
    }
    print(json.dumps(metrics))


if __name__ == "__main__":
    asyncio.run(main())
