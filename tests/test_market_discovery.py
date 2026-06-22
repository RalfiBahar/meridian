"""Tests for ingest market discovery helpers."""

from meridian.ingest.market_discovery import _pick_event_strikes, _pick_top_volume
from meridian.kalshi.models import KalshiMarket, KalshiMarketStatus


def _mkt(ticker: str, vol: str, event: str | None = None) -> KalshiMarket:
    return KalshiMarket(
        ticker=ticker,
        event_ticker=event or ticker.rsplit("-", 1)[0],
        status=KalshiMarketStatus.ACTIVE,
        volume=vol,  # type: ignore[arg-type]
    )


def test_pick_event_strikes_groups_by_event() -> None:
    markets = [
        _mkt("KXFED-26JUN-T5.00", "100", "KXFED-26JUN"),
        _mkt("KXFED-26JUN-T4.75", "200", "KXFED-26JUN"),
        _mkt("KXFED-26APR-T4.00", "999", "KXFED-26APR"),
    ]
    tickers = _pick_event_strikes(markets, limit=10)
    assert tickers == ["KXFED-26APR-T4.00"]


def test_pick_top_volume() -> None:
    markets = [_mkt("A", "10"), _mkt("B", "50"), _mkt("C", "30")]
    assert _pick_top_volume(markets, 2) == ["B", "C"]
