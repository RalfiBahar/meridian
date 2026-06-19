"""Phase 5 analytics: implied Fed-rate PMF + CME FedWatch comparison + event response.

Data flow
---------
1. `build_kalshi_pmf(pool, fomc_date)` reads the latest `p_mid` signals for every
   contract in the KXFED partition group whose `closes_at` falls on `fomc_date`.
   Strikes are parsed from the ticker (e.g. KXFED-26JUN-T3.75 → 3.75%).
   Probabilities are normalized so they sum to 1.

2. `fetch_cme_fedwatch(fomc_date)` attempts a best-effort HTTP GET to the CME
   Group public quotes endpoint.  Returns `None` on any network or parse failure —
   callers must handle this gracefully.

3. `compute_event_response(pool, event_id, *, pre_window, post_window)` joins a
   `news_events` row to `p_mid` signals for markets in the same category; computes
   Δp = mean(post) - mean(pre) and the ratio of post-event variance to pre-event
   variance (information arrival proxy).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx

_CME_QUOTES_URL = (
    "https://www.cmegroup.com/CmeWS/mvc/Quotes/ContractsByNumber"
    "?productIds=305&contractsNumber=12&venue=G&type=FUTURE"
)
_CME_TIMEOUT = 8.0  # seconds


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class FedPMF:
    """Implied probability mass function over Fed-funds rate outcomes.

    `strikes` and `probabilities` are parallel lists sorted ascending by strike.
    `raw_p_mid` preserves the unnormalized Kalshi prices before normalization.
    """

    fomc_date: date
    strikes: list[Decimal]  # rate outcomes in %, ascending
    probabilities: list[float]  # normalized P(rate = strike)
    raw_p_mid: list[float]  # Kalshi p_mid before normalization
    source: str  # "kalshi" or "cme_fedwatch"

    def expected_rate(self) -> float:
        """Probability-weighted expected rate."""
        return sum(float(s) * p for s, p in zip(self.strikes, self.probabilities, strict=True))

    def entropy(self) -> float:
        """Shannon entropy in bits — measure of uncertainty."""
        return -sum(p * math.log2(p) for p in self.probabilities if p > 0)

    def summary(self) -> str:
        lines = [
            f"FOMC date:    {self.fomc_date}",
            f"Source:       {self.source}",
            f"Expected rate: {self.expected_rate():.3f}%",
            f"Entropy:      {self.entropy():.3f} bits",
            "",
            f"{'Strike':>8}  {'P(rate)':>10}  {'Raw p_mid':>10}",
            "─" * 34,
        ]
        for s, p, raw in zip(self.strikes, self.probabilities, self.raw_p_mid, strict=True):
            lines.append(f"{float(s):>8.2f}%  {p:>10.4f}  {raw:>10.4f}")
        return "\n".join(lines)


@dataclass
class EventResponse:
    """Market reaction to a single news event."""

    event_id: UUID
    event_label: str
    occurred_at: datetime
    category: str
    pre_window: timedelta
    post_window: timedelta
    n_markets: int
    mean_delta_p: float | None  # mean(post_p_mid) - mean(pre_p_mid)
    variance_ratio: float | None  # var(post) / var(pre) — > 1 signals new info
    per_market: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Event:         {self.event_label}  [{self.occurred_at.isoformat()}]",
            f"Category:      {self.category}",
            f"Window:        -{self.pre_window}  / +{self.post_window}",
            f"Markets:       {self.n_markets}",
        ]
        if self.mean_delta_p is not None:
            lines.append(f"Mean ΔP_mid:   {self.mean_delta_p:+.4f}")
        if self.variance_ratio is not None:
            lines.append(f"Var ratio:     {self.variance_ratio:.3f}")
        if self.per_market:
            lines.append("")
            lines.append(f"{'Ticker':<30}  {'ΔP':>8}  {'Pre μ':>8}  {'Post μ':>8}")
            lines.append("─" * 60)
            for m in self.per_market:
                lines.append(
                    f"{m.get('ticker', str(m['market_id']))[:29]:<30}  "
                    f"{m['delta_p']:>+8.4f}  "
                    f"{m['pre_mean']:>8.4f}  "
                    f"{m['post_mean']:>8.4f}"
                )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def build_kalshi_pmf(
    pool: Any,
    fomc_date: date,
) -> FedPMF | None:
    """Build an implied PMF from Kalshi KXFED contracts that settle on `fomc_date`.

    Reads the latest `p_mid` signal per contract. Returns `None` when no
    contracts are found for the requested date.
    """
    rows = await _kalshi_kxfed_signals(pool, fomc_date)
    if not rows:
        return None

    pairs: list[tuple[Decimal, float]] = []
    for r in rows:
        strike = _parse_kxfed_strike(r["external_id"])
        if strike is None:
            continue
        pairs.append((strike, float(r["p_mid"])))

    if not pairs:
        return None

    pairs.sort(key=lambda x: x[0])
    strikes = [s for s, _ in pairs]
    raw = [p for _, p in pairs]
    total = sum(raw)
    probs = [p / total for p in raw] if total > 0 else [1.0 / len(raw)] * len(raw)

    return FedPMF(
        fomc_date=fomc_date,
        strikes=strikes,
        probabilities=probs,
        raw_p_mid=raw,
        source="kalshi",
    )


async def fetch_cme_fedwatch(fomc_date: date) -> FedPMF | None:
    """Best-effort fetch of CME FedWatch probabilities for a given FOMC date.

    Uses the CME Group public 30-day Fed Funds futures quotes endpoint.
    Computes implied probabilities from settlement prices using the standard
    formula: implied_rate = 100 - price; Δrate / 25bps = move probability.

    Returns `None` on any network failure or if the data cannot be parsed.
    This is intentionally graceful — callers should always have a Kalshi PMF
    as the primary source.
    """
    try:
        async with httpx.AsyncClient(timeout=_CME_TIMEOUT) as client:
            resp = await client.get(_CME_QUOTES_URL)
            if resp.status_code != 200:
                return None
            data = resp.json()
    except Exception:
        return None

    return _parse_cme_response(data, fomc_date)


async def compute_event_response(
    pool: Any,
    event_id: UUID,
    *,
    pre_window: timedelta = timedelta(hours=1),
    post_window: timedelta = timedelta(hours=1),
) -> EventResponse | None:
    """Compute market reaction to a news event.

    Looks up the event in `news_events`, then for all open markets in the same
    category queries `p_mid` signals in the pre and post windows.

    Returns `None` if the event is not found.
    """
    event_row = await _fetch_news_event(pool, event_id)
    if event_row is None:
        return None

    occurred_at = event_row["occurred_at"]
    category = event_row["category"]
    label = event_row["label"]

    pre_start = occurred_at - pre_window
    pre_end = occurred_at
    post_start = occurred_at
    post_end = occurred_at + post_window

    market_rows = await _markets_by_category(pool, category)
    per_market: list[dict[str, Any]] = []

    for mrow in market_rows:
        mid = UUID(str(mrow["id"]))
        ticker = mrow.get("external_id", str(mid))
        pre_sigs = await _pmid_signals_window(pool, mid, pre_start, pre_end)
        post_sigs = await _pmid_signals_window(pool, mid, post_start, post_end)
        if not pre_sigs or not post_sigs:
            continue
        pre_mean = _mean(pre_sigs)
        post_mean = _mean(post_sigs)
        per_market.append(
            {
                "market_id": mid,
                "ticker": ticker,
                "pre_mean": pre_mean,
                "post_mean": post_mean,
                "delta_p": post_mean - pre_mean,
                "pre_n": len(pre_sigs),
                "post_n": len(post_sigs),
            }
        )

    if not per_market:
        mean_delta: float | None = None
        var_ratio: float | None = None
    else:
        deltas = [m["delta_p"] for m in per_market]
        mean_delta = _mean(deltas)
        pre_vals = [m["pre_mean"] for m in per_market]
        post_vals = [m["post_mean"] for m in per_market]
        pre_var = _var(pre_vals)
        post_var = _var(post_vals)
        var_ratio = (post_var / pre_var) if pre_var > 0 else None

    return EventResponse(
        event_id=event_id,
        event_label=label,
        occurred_at=occurred_at,
        category=category,
        pre_window=pre_window,
        post_window=post_window,
        n_markets=len(per_market),
        mean_delta_p=mean_delta,
        variance_ratio=var_ratio,
        per_market=per_market,
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _parse_kxfed_strike(external_id: str) -> Decimal | None:
    """Extract rate from KXFED ticker: 'KXFED-26JUN-T3.75' → Decimal('3.75')."""
    parts = external_id.split("-")
    if len(parts) < 3:
        return None
    rate_part = parts[-1]  # 'T3.75'
    if not rate_part.startswith("T"):
        return None
    try:
        return Decimal(rate_part[1:])
    except Exception:
        return None


def _parse_cme_response(data: Any, fomc_date: date) -> FedPMF | None:
    """Parse CME futures quote response into a FedPMF.

    CME returns 30-day Fed Funds futures with prices like 94.655, implying a
    rate of 100 - 94.655 = 5.345%.  We derive a discrete PMF by computing
    probabilities across 25-bps increments bracketing the implied rate.
    """
    try:
        quotes = data.get("quotes", [])
        if not quotes:
            return None

        # Find the contract closest to the FOMC date.
        target_month = fomc_date.strftime("%b %y").upper()  # e.g. "JUN 26"
        target_quote = None
        for q in quotes:
            label = q.get("expirationMonth", "") + " " + q.get("expirationYear", "")
            if label.upper() == target_month:
                target_quote = q
                break
        if target_quote is None:
            return None

        price_str = target_quote.get("last") or target_quote.get("settlement", "")
        if not price_str:
            return None
        futures_price = float(price_str)
        implied_rate = round((100.0 - futures_price) * 4) / 4  # round to 25-bps

        # Build a simple two-point PMF: implied_rate ± 25bps.
        step = 0.25
        low = implied_rate - step
        high = implied_rate
        raw_high = futures_price - (100.0 - high)
        # raw_high is how far price is above the low strike's par value.
        p_high = max(0.0, min(1.0, raw_high / step)) if step > 0 else 0.5
        p_low = 1.0 - p_high

        strikes = sorted([Decimal(str(low)), Decimal(str(high))])
        probs = [p_low, p_high] if Decimal(str(low)) < Decimal(str(high)) else [p_high, p_low]
        raw = probs[:]

        return FedPMF(
            fomc_date=fomc_date,
            strikes=strikes,
            probabilities=probs,
            raw_p_mid=raw,
            source="cme_fedwatch",
        )
    except Exception:
        return None


def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals)


def _var(vals: list[float]) -> float:
    if len(vals) < 2:
        return 0.0
    m = _mean(vals)
    return sum((v - m) ** 2 for v in vals) / (len(vals) - 1)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _kalshi_kxfed_signals(
    pool: Any,
    fomc_date: date,
) -> list[dict[str, Any]]:
    """Return the latest p_mid per KXFED contract closing on `fomc_date`."""
    date_start = datetime(fomc_date.year, fomc_date.month, fomc_date.day, 0, 0, tzinfo=UTC)
    date_end = date_start + timedelta(days=1)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (m.id)
                m.id,
                m.external_id,
                s.value AS p_mid
            FROM markets m
            JOIN venues v ON v.id = m.venue_id
            JOIN signals s ON s.market_id = m.id
            WHERE v.code = 'kalshi'
              AND m.external_id LIKE 'KXFED-%%'
              AND m.closes_at >= $1
              AND m.closes_at <  $2
              AND s.signal_type = 'p_mid'
            ORDER BY m.id, s.event_ts DESC
            """,
            date_start,
            date_end,
        )
    return [dict(r) for r in rows]


async def _fetch_news_event(pool: Any, event_id: UUID) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, occurred_at, category, label FROM news_events WHERE id = $1",
            event_id,
        )
    return dict(row) if row else None


async def _markets_by_category(pool: Any, category: str) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, external_id, ticker
            FROM markets
            WHERE category = $1
              AND resolution_status = 'open'
            """,
            category,
        )
    return [dict(r) for r in rows]


async def _pmid_signals_window(
    pool: Any,
    market_id: UUID,
    start: datetime,
    end: datetime,
) -> list[float]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT value
            FROM signals
            WHERE market_id = $1
              AND signal_type = 'p_mid'
              AND event_ts >= $2
              AND event_ts <  $3
            ORDER BY event_ts
            """,
            market_id,
            start,
            end,
        )
    return [float(r["value"]) for r in rows if r["value"] is not None]
