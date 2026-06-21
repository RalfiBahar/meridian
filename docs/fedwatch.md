# FedWatch — Kalshi vs CME comparison

The `/fedwatch` page overlays the **Kalshi-implied Fed funds probability mass
function** (PMF) against the **CME FedWatch** strip for each upcoming FOMC meeting.

---

## Data sources

| Source | Endpoint | Auth |
|--------|----------|------|
| Kalshi KXFED | `GET /v3/markets?ticker_contains=KXFED` | API key |
| CME FedWatch | `analytics/fedwatch.py:fetch_cme_fedwatch()` | public (network) |

---

## How the Kalshi PMF is built

1. Group all KXFED contracts for the target FOMC date into a `market_group`
   (via `meridian arb group-fed`).
2. Sort contracts by strike (ascending target rate).
3. Interpret cumulative contract prices as `P(rate ≤ k)` and take differences
   to produce a PMF over discrete outcomes.
4. Entropy `H = -Σ p_i log₂(p_i)` and expected rate `E[r] = Σ p_i * strike_i`
   are reported alongside the PMF.

---

## CME FedWatch graceful degrade

CME FedWatch data requires a live network fetch from CME's public endpoint.

**Fixture mode** (no network): if the CME endpoint is unreachable, the page
renders the Kalshi PMF only and marks the CME column as unavailable.  The
`fetchFedWatch` API function returns `cme: null` in this case and the frontend
renders gracefully with "CME data unavailable".

To test with fixture data:
```bash
# Force fixture mode by setting an invalid CME URL in dev:
CME_FEDWATCH_URL=http://localhost:1/invalid meridian analytics fedwatch --date 2026-07-30
```

---

## CLI

```bash
# Kalshi PMF for next FOMC meeting:
meridian analytics fedwatch --date 2026-07-30

# With CME comparison:
meridian analytics fedwatch --date 2026-07-30 --cme

# Event-response analysis around a news event:
meridian analytics event-response <event-uuid>
```

---

## Interpretation

| Metric | Meaning |
|--------|---------|
| `E[r]` | Market-implied expected Fed funds rate |
| `H` (bits) | PMF entropy — high H = high uncertainty |
| `Δ CME` (bps) | Kalshi expected rate minus CME expected rate |

A positive `Δ CME` means Kalshi is pricing a higher rate than CME; negative
means Kalshi is more dovish.  Sustained divergence >10 bps may signal a
cross-venue opportunity or differences in how each market accounts for meeting-
to-meeting carry.
