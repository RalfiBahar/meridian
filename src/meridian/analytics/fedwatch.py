"""Phase 5 analytics: implied Fed-rate PMF + CME FedWatch comparison + event response.

Data flow
---------
1. `build_kalshi_pmf(pool, fomc_date)` reads the latest `p_mid` for every KXFED
   contract whose `closes_at` falls on `fomc_date`. Each ticker `KXFED-*-T<k>`
   is treated as a strict above-strike contract: YES pays 1 iff the rate is
   above `k`, so `p_mid` is a survival probability `S(k) = P(rate > k)`.
   Interval probabilities are adjacent differences, including the two tails.
   Quotes are not renormalized to sum to 1. A non-monotonic or out-of-range
   curve is returned with `consistent=False` and no probability masses.

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
from collections.abc import Sequence
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


# Assumed spacing of the Fed funds target, used only to place probability
# mass that the quoted strikes do not bound (the open tails). It is not a
# term of the KXFED payoff.
FED_FUNDS_STEP = Decimal("0.25")

# The arb grouper and docs/concepts.md describe KXFED the same way. This
# module does not fetch Kalshi's rule text, so a renamed contract would
# still be differenced under this assumption.
KXFED_CONTRACT = (
    "KXFED YES pays 1 iff the Fed funds rate is strictly above the ticker "
    "strike. p_mid is S(k) = P(rate > k). Tails use a 25bp representative "
    "rate because the conditional expectation inside an open tail is not "
    "identified by the strip."
)


@dataclass(frozen=True)
class RateBucket:
    """One interval of a survival-curve PMF.

    For strict above-strike contracts, interior buckets are ``(k_i, k_{i+1}]``
    and the tails are ``rate <= k_min`` and ``rate > k_max``.
    """

    lower_exclusive: Decimal | None
    upper_inclusive: Decimal | None
    probability: float
    representative_rate: Decimal

    def label(self) -> str:
        if self.lower_exclusive is None and self.upper_inclusive is not None:
            return f"rate <= {self.upper_inclusive}"
        if self.upper_inclusive is None and self.lower_exclusive is not None:
            return f"rate > {self.lower_exclusive}"
        return f"{self.lower_exclusive} < rate <= {self.upper_inclusive}"


@dataclass
class FedPMF:
    """Implied distribution over a Fed-funds meeting.

    Kalshi KXFED results store the threshold strikes in ``threshold_strikes``
    and the survival quotes in ``raw_p_mid``. ``strikes`` and ``probabilities``
    are the displayed PMF: representative rate and interval probability.
    Point-mass inputs (the CME helper, and unit-test constructors) leave
    ``buckets`` empty and use ``strikes`` / ``probabilities`` directly.
    """

    fomc_date: date
    strikes: list[Decimal]
    probabilities: list[float]
    raw_p_mid: list[float]
    source: str  # "kalshi" or "cme_fedwatch"
    buckets: list[RateBucket] = field(default_factory=list)
    threshold_strikes: list[Decimal] = field(default_factory=list)
    consistent: bool = True
    issues: list[str] = field(default_factory=list)
    contract_definition: str = ""

    def expected_rate(self) -> float:
        """Probability-weighted representative rate.

        For a KXFED strip this is not the conditional expectation inside an
        open tail. Tail mass is placed half a Fed step beyond the outer strike.
        """
        if self.buckets:
            return sum(float(b.representative_rate) * b.probability for b in self.buckets)
        return sum(float(s) * p for s, p in zip(self.strikes, self.probabilities, strict=True))

    def entropy(self) -> float:
        """Shannon entropy in bits of the displayed probabilities."""
        probs = [b.probability for b in self.buckets] if self.buckets else self.probabilities
        return -sum(p * math.log2(p) for p in probs if p > 0)

    def summary(self) -> str:
        lines = [
            f"FOMC date:    {self.fomc_date}",
            f"Source:       {self.source}",
            f"Consistent:   {self.consistent}",
            f"Expected rate: {self.expected_rate():.3f}%",
            f"Entropy:      {self.entropy():.3f} bits",
        ]
        if self.contract_definition:
            lines.append(f"Contract:     {self.contract_definition}")
        if self.issues:
            lines.append("Issues:")
            lines.extend(f"  - {issue}" for issue in self.issues)
        if self.buckets:
            lines.extend(["", f"{'Interval':<32}  {'P':>8}  {'Rep.':>8}"])
            for bucket in self.buckets:
                lines.append(
                    f"{bucket.label():<32}  {bucket.probability:>8.4f}  "
                    f"{float(bucket.representative_rate):>7.3f}%"
                )
            return "\n".join(lines)
        lines.extend(
            [
                "",
                f"{'Strike':>8}  {'P(rate)':>10}  {'Raw p_mid':>10}",
                "-" * 34,
            ]
        )
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
    """Build an interval PMF from KXFED survival quotes closing on `fomc_date`.

    Reads the latest `p_mid` per contract. Returns None when no parsable
    contracts are found. A parsed but non-monotonic curve is returned with
    ``consistent`` false and empty probabilities; it is not renormalized.
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

    density = survival_to_density(pairs)
    displayed = density.buckets if density.consistent else []
    return FedPMF(
        fomc_date=fomc_date,
        strikes=[bucket.representative_rate for bucket in displayed],
        probabilities=[bucket.probability for bucket in displayed],
        raw_p_mid=density.survivals,
        source="kalshi",
        buckets=displayed,
        threshold_strikes=density.thresholds,
        consistent=density.consistent,
        issues=list(density.issues),
        contract_definition=KXFED_CONTRACT,
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


@dataclass(frozen=True)
class SurvivalDensity:
    """Interval masses derived from an ordered above-strike strip."""

    thresholds: list[Decimal]
    survivals: list[float]
    buckets: list[RateBucket]
    consistent: bool
    issues: tuple[str, ...]


def survival_to_density(
    quotes: Sequence[tuple[Decimal, float]],
) -> SurvivalDensity:
    """Turn strict above-strike quotes into interval probabilities.

    Contract assumed for every quote: the price is ``S(k) = P(rate > k)``,
    not ``P(rate = k)`` and not ``P(rate >= k)``.

    For ordered strikes ``k0 < k1 < ... < kn``:

    - ``P(rate <= k0) = 1 - S(k0)``
    - ``P(k_i < rate <= k_{i+1}) = S(k_i) - S(k_{i+1})``
    - ``P(rate > kn) = S(kn)``

    These three lines sum to 1 whenever every survival is a number. They are
    a probability distribution only when the curve is inside ``[0, 1]`` and
    non-increasing. Otherwise the result is marked inconsistent and no
    buckets are returned. Negative differences are not clipped and the quotes
    are not divided by their sum.

    A gap larger than 25bp is reported as a missing strike. The mass between
    the two quoted strikes is left on that wider interval. No strike is
    invented to fill the hole. Duplicate strikes with the same quote collapse.
    Duplicate strikes that disagree are inconsistent.
    """
    grouped: dict[Decimal, list[float]] = {}
    for strike, probability in quotes:
        grouped.setdefault(strike, []).append(probability)

    issues: list[str] = []
    thresholds = sorted(grouped)
    survivals: list[float] = []
    for strike in thresholds:
        values = grouped[strike]
        if max(values) - min(values) > 1e-9:
            issues.append(f"conflicting quotes at strike {strike}: {values}")
            survivals.append(values[0])
        else:
            survivals.append(values[0])

    monotonic = True
    in_unit_interval = True
    for index, strike in enumerate(thresholds):
        survival = survivals[index]
        if survival < -1e-12 or survival > 1.0 + 1e-12:
            in_unit_interval = False
            issues.append(f"survival {survival} at strike {strike} is outside [0, 1]")
        if index == 0:
            continue
        previous = thresholds[index - 1]
        if survival > survivals[index - 1] + 1e-12:
            monotonic = False
            issues.append(
                f"non-monotonic survival: S({previous})={survivals[index - 1]} "
                f"< S({strike})={survival}"
            )
        gap = strike - previous
        if gap > FED_FUNDS_STEP + Decimal("0.001"):
            issues.append(
                f"missing strike between {previous} and {strike}; mass stays on the wider interval"
            )

    consistent = (
        monotonic
        and in_unit_interval
        and not any(issue.startswith("conflicting quotes") for issue in issues)
    )
    buckets = _interval_buckets(thresholds, survivals) if consistent and thresholds else []
    return SurvivalDensity(
        thresholds=thresholds,
        survivals=survivals,
        buckets=buckets,
        consistent=consistent,
        issues=tuple(issues),
    )


def _interval_buckets(
    thresholds: list[Decimal],
    survivals: list[float],
) -> list[RateBucket]:
    half = FED_FUNDS_STEP / 2
    buckets: list[RateBucket] = []
    first = thresholds[0]
    buckets.append(
        RateBucket(
            lower_exclusive=None,
            upper_inclusive=first,
            probability=1.0 - survivals[0],
            representative_rate=first - half,
        )
    )
    for index in range(len(thresholds) - 1):
        left = thresholds[index]
        right = thresholds[index + 1]
        buckets.append(
            RateBucket(
                lower_exclusive=left,
                upper_inclusive=right,
                probability=survivals[index] - survivals[index + 1],
                representative_rate=(left + right) / 2,
            )
        )
    last = thresholds[-1]
    buckets.append(
        RateBucket(
            lower_exclusive=last,
            upper_inclusive=None,
            probability=survivals[-1],
            representative_rate=last + half,
        )
    )
    return buckets


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
