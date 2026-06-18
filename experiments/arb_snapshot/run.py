"""arb_snapshot — daily no-arbitrage violation snapshot.

Usage (via CLI):
    meridian experiment run arb_snapshot
    meridian experiment run arb_snapshot --params '{"threshold_bps": 10}'

Reads:
    EXPERIMENT_PARAMS env var (JSON dict)

Prints:
    Formatted violation table, then a JSON metrics object on the last line.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from decimal import Decimal

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, os.path.join(_REPO, "src"))


async def main() -> None:
    params = json.loads(os.environ.get("EXPERIMENT_PARAMS", "{}"))
    threshold_bps = Decimal(str(params.get("threshold_bps", 5)))
    write_signals: bool = bool(params.get("write_signals", False))

    from meridian.analytics.arb import run_cross_venue_monitor, run_partition_monitor
    from meridian.config import get_settings
    from meridian.db.postgres import pool_context

    settings = get_settings()

    async with pool_context(settings) as pool:
        partition_hits = await run_partition_monitor(
            pool, threshold_bps=threshold_bps, write_signals=write_signals
        )
        cross_hits = await run_cross_venue_monitor(
            pool, threshold_bps=threshold_bps, write_signals=write_signals
        )

    max_bps = 0.0
    if partition_hits or cross_hits:
        print(f"Found {len(partition_hits)} partition + {len(cross_hits)} cross-venue violations")
        for r in partition_hits:
            bps = float(r.violation_bps)
            max_bps = max(max_bps, bps)
            depth_flag = "DEPTH-OK" if r.depth_feasible else "depth-limited"
            print(f"  {r.group_label[:40]}: {bps:.1f} bps [{r.direction}] [{depth_flag}]")
        for cv in cross_hits:
            bps = float(cv.divergence_bps)
            max_bps = max(max_bps, bps)
            print(f"  {cv.venue_a}<>{cv.venue_b} ({cv.group_id}): {bps:.1f} bps")
    else:
        print(f"No violations above {threshold_bps} bps.")

    metrics = {
        "n_partition_violations": len(partition_hits),
        "n_cross_venue_violations": len(cross_hits),
        "max_violation_bps": max_bps,
    }
    print(json.dumps(metrics))


if __name__ == "__main__":
    asyncio.run(main())
