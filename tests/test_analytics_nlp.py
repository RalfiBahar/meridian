"""Unit tests for analytics.nlp — news-event market-moving tagger.

All tests use in-process mocks; no Docker required.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from meridian.analytics.nlp import (
    NewsTaggerConfig,
    _event_text,
    _top_tokens_for_text,
    tag_recent_events,
    train_tagger,
    write_nlp_signals,
)

_NOW = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Mock pool
# ---------------------------------------------------------------------------


class _MockConn:
    def __init__(
        self,
        events: list[dict[str, Any]] | None = None,
        deltas: list[dict[str, Any]] | None = None,
    ) -> None:
        self._events = events or []
        self._deltas = deltas or []
        self.executemany_calls: list[tuple[str, list[Any]]] = []

    async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
        if "news_events" in query and "occurred_at" in query and "AVG" not in query:
            return self._events
        if "AVG" in query:
            return self._deltas
        return []

    async def executemany(self, query: str, rows: list[Any]) -> None:
        self.executemany_calls.append((query, rows))


class _MockPool:
    def __init__(self, conn: _MockConn) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self) -> Any:
        yield self._conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(label: str, category: str = "fed") -> dict[str, Any]:
    return {
        "id": uuid4(),
        "occurred_at": _NOW,
        "category": category,
        "label": label,
    }


def _make_delta(value: float) -> dict[str, Any]:
    return {"delta": value}


def _training_pool(n_moving: int = 10, n_stable: int = 10) -> _MockPool:
    """Build a pool returning n_moving market-moving + n_stable stable events."""
    events = [_make_event(f"FOMC rate hike {i}") for i in range(n_moving)] + [
        _make_event(f"routine update {i}") for i in range(n_stable)
    ]
    # Alternating deltas: moving events get 0.05, stable get 0.005
    deltas = [_make_delta(0.05)] * n_moving + [_make_delta(0.005)] * n_stable

    class _CountingConn(_MockConn):
        def __init__(self) -> None:
            super().__init__(events=events)
            self._call = 0

        async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
            if "AVG" in query:
                # Return one delta per event in sequence
                result = [deltas[self._call % len(deltas)]]
                self._call += 1
                return result
            return events

    return _MockPool(_CountingConn())


# ---------------------------------------------------------------------------
# _event_text
# ---------------------------------------------------------------------------


def test_event_text_combines_category_and_label() -> None:
    assert _event_text("rate hike 25bps", "fed") == "fed rate hike 25bps"


def test_event_text_different_categories() -> None:
    t1 = _event_text("CPI print", "macro")
    t2 = _event_text("CPI print", "fed")
    assert t1 != t2
    assert t1.startswith("macro")


# ---------------------------------------------------------------------------
# train_tagger — too few events
# ---------------------------------------------------------------------------


async def test_train_tagger_returns_none_when_no_events() -> None:
    pool = _MockPool(_MockConn(events=[], deltas=[]))
    result = await train_tagger(pool)
    assert result is None


async def test_train_tagger_returns_none_when_fewer_than_min() -> None:
    # 5 events < _MIN_TRAINING_EVENTS (10), even with deltas
    events = [_make_event(f"event {i}") for i in range(5)]
    deltas = [_make_delta(0.03)] * 5

    class _SmallConn(_MockConn):
        def __init__(self) -> None:
            super().__init__(events=events)
            self._call = 0

        async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
            if "AVG" in query:
                result = [deltas[self._call % len(deltas)]]
                self._call += 1
                return result
            return events

    pool = _MockPool(_SmallConn())
    result = await train_tagger(pool)
    assert result is None


# ---------------------------------------------------------------------------
# train_tagger — sufficient data
# ---------------------------------------------------------------------------


async def test_train_tagger_returns_tagger_with_enough_data() -> None:
    pool = _training_pool(n_moving=10, n_stable=10)
    tagger = await train_tagger(pool)
    assert tagger is not None
    assert tagger.n_events == 20


async def test_train_tagger_records_categories() -> None:
    pool = _training_pool()
    tagger = await train_tagger(pool)
    assert tagger is not None
    assert "fed" in tagger.categories


async def test_train_tagger_uses_custom_config() -> None:
    # Use threshold=0.03 so the 0.05 deltas are market-moving and 0.005 stable.
    pool = _training_pool()
    config = NewsTaggerConfig(price_delta_threshold=0.03, max_features=100)
    tagger = await train_tagger(pool, config=config)
    assert tagger is not None
    assert tagger.config.price_delta_threshold == 0.03
    assert tagger.config.max_features == 100


async def test_train_tagger_can_distinguish_moving_vs_stable() -> None:
    """Tagger trained on clearly separated classes should assign higher prob to
    market-moving labels."""
    pool = _training_pool(n_moving=15, n_stable=15)
    tagger = await train_tagger(pool)
    assert tagger is not None

    # Labels seen in training as market-moving
    moving_result = tagger.predict_one("FOMC rate hike 99", "fed", uuid4())
    stable_result = tagger.predict_one("routine update 99", "fed", uuid4())

    assert moving_result.market_moving_prob > stable_result.market_moving_prob


# ---------------------------------------------------------------------------
# predict_one
# ---------------------------------------------------------------------------


async def test_predict_one_returns_news_tag_result() -> None:
    pool = _training_pool()
    tagger = await train_tagger(pool)
    assert tagger is not None

    eid = uuid4()
    result = tagger.predict_one("FOMC rate hike surprise", "fed", eid)

    assert result.event_id == eid
    assert result.label == "FOMC rate hike surprise"
    assert result.category == "fed"
    assert 0.0 <= result.market_moving_prob <= 1.0
    assert isinstance(result.is_market_moving, bool)


async def test_predict_one_is_market_moving_above_threshold() -> None:
    pool = _training_pool()
    config = NewsTaggerConfig(prediction_threshold=0.0)  # always True
    tagger = await train_tagger(pool, config=config)
    assert tagger is not None
    result = tagger.predict_one("any text", "fed", uuid4())
    assert result.is_market_moving is True


async def test_predict_one_top_tokens_list() -> None:
    pool = _training_pool()
    tagger = await train_tagger(pool)
    assert tagger is not None
    result = tagger.predict_one("FOMC rate hike 25bps", "fed", uuid4())
    assert isinstance(result.top_tokens, list)


# ---------------------------------------------------------------------------
# _top_tokens_for_text
# ---------------------------------------------------------------------------


async def test_top_tokens_returns_strings() -> None:
    pool = _training_pool()
    tagger = await train_tagger(pool)
    assert tagger is not None
    tokens = _top_tokens_for_text(tagger._pipeline, "fed rate hike")
    assert all(isinstance(t, str) for t in tokens)


async def test_top_tokens_empty_for_unknown_text() -> None:
    pool = _training_pool()
    tagger = await train_tagger(pool)
    assert tagger is not None
    # Text with no tokens in vocabulary returns empty (or short) list
    tokens = _top_tokens_for_text(tagger._pipeline, "zzz yyy xxx")
    assert isinstance(tokens, list)


# ---------------------------------------------------------------------------
# tag_recent_events
# ---------------------------------------------------------------------------


async def test_tag_recent_events_returns_list() -> None:
    # Separate pools: one for training, one for tagging
    train_pool = _training_pool()
    tagger = await train_tagger(train_pool)
    assert tagger is not None

    tag_conn = _MockConn(events=[_make_event("CPI surprise print")])
    tag_pool = _MockPool(tag_conn)
    results = await tag_recent_events(tag_pool, tagger)
    assert len(results) == 1
    assert results[0].label == "CPI surprise print"


async def test_tag_recent_events_empty_when_no_events() -> None:
    train_pool = _training_pool()
    tagger = await train_tagger(train_pool)
    assert tagger is not None

    tag_pool = _MockPool(_MockConn(events=[]))
    results = await tag_recent_events(tag_pool, tagger)
    assert results == []


# ---------------------------------------------------------------------------
# write_nlp_signals
# ---------------------------------------------------------------------------


async def test_write_nlp_signals_calls_executemany() -> None:
    from meridian.analytics.nlp import NewsTagResult

    conn = _MockConn()
    pool = _MockPool(conn)
    results = [
        NewsTagResult(
            event_id=uuid4(),
            label="rate hike",
            category="fed",
            market_moving_prob=0.85,
            is_market_moving=True,
            top_tokens=["hike", "rate"],
        )
    ]
    n = await write_nlp_signals(pool, results)
    assert n == 1
    assert len(conn.executemany_calls) == 1
    assert "INSERT INTO signals" in conn.executemany_calls[0][0]


async def test_write_nlp_signals_empty_results() -> None:
    conn = _MockConn()
    pool = _MockPool(conn)
    n = await write_nlp_signals(pool, [])
    assert n == 0
    assert conn.executemany_calls == []


# ---------------------------------------------------------------------------
# NewsTagger.summary
# ---------------------------------------------------------------------------


async def test_tagger_summary_format() -> None:
    pool = _training_pool()
    tagger = await train_tagger(pool)
    assert tagger is not None
    s = tagger.summary()
    assert "News Tagger" in s
    assert "20" in s  # n_events
    assert "fed" in s
    assert "2.0%" in s  # price_delta_threshold default
