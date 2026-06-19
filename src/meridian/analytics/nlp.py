"""News → price NLP tagger: classifies news events as market-moving or not.

Trains a TF-IDF + LogisticRegression pipeline on historical (label, category) →
abs(price_delta) pairs.  A news event is "market-moving" when the mean p_mid
delta across related markets in the first `post_window_hours` exceeds
`price_delta_threshold`.

The trained tagger can classify new events without re-querying the signals table.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import numpy as np
from numpy.typing import NDArray
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score
from sklearn.pipeline import Pipeline

_MIN_TRAINING_EVENTS = 10
_DEFAULT_WINDOW = timedelta(days=90)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration and result types
# ---------------------------------------------------------------------------


@dataclass
class NewsTaggerConfig:
    """Training and prediction parameters for the NLP tagger."""

    price_delta_threshold: float = 0.02  # |delta_p| >= this → market-moving label
    prediction_threshold: float = 0.5  # probability cut-off for is_market_moving
    max_features: int = 500
    ngram_range: tuple[int, int] = (1, 2)
    post_window_hours: int = 1  # how far post-event to measure price delta
    cv_folds: int = 3  # cross-validation folds for accuracy estimate


@dataclass
class NewsTagResult:
    """Classification result for a single news event."""

    event_id: UUID
    label: str
    category: str
    market_moving_prob: float
    is_market_moving: bool
    top_tokens: list[str] = field(default_factory=list)


@dataclass
class NewsTagger:
    """Trained tagger; call predict_one() or pass to tag_recent_events()."""

    config: NewsTaggerConfig
    _pipeline: Pipeline
    n_events: int
    categories: list[str]
    cv_accuracy: float | None

    def predict_one(self, label: str, category: str, event_id: UUID) -> NewsTagResult:
        """Classify a single event given its label and category."""
        text = _event_text(label, category)
        prob = float(self._pipeline.predict_proba([text])[0][1])
        is_mm = prob >= self.config.prediction_threshold
        tokens = _top_tokens_for_text(self._pipeline, text)
        return NewsTagResult(
            event_id=event_id,
            label=label,
            category=category,
            market_moving_prob=prob,
            is_market_moving=is_mm,
            top_tokens=tokens,
        )

    def summary(self) -> str:
        lines = [
            "News Tagger",
            f"Trained on:       {self.n_events} events",
            f"Categories:       {', '.join(sorted(self.categories))}",
            f"Price threshold:  {self.config.price_delta_threshold:.1%}",
            f"Prediction cut:   {self.config.prediction_threshold:.0%}",
            f"Ngrams:           {self.config.ngram_range}",
            f"Max features:     {self.config.max_features}",
        ]
        if self.cv_accuracy is not None:
            lines.append(f"CV accuracy:      {self.cv_accuracy:.1%}")
        else:
            lines.append("CV accuracy:      n/a (too few events for CV)")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def train_tagger(
    pool: Any,
    *,
    category: str | None = None,
    window: timedelta = _DEFAULT_WINDOW,
    config: NewsTaggerConfig | None = None,
) -> NewsTagger | None:
    """Fit the NLP pipeline on historical events.

    Fetches news events in the window and measures the mean p_mid delta in
    the first post_window_hours for markets in the same category.  Returns
    None when fewer than _MIN_TRAINING_EVENTS labeled examples exist.
    """
    if config is None:
        config = NewsTaggerConfig()
    since = datetime.now(tz=UTC) - window
    rows = await _fetch_training_rows(pool, since, category, config.post_window_hours)
    if len(rows) < _MIN_TRAINING_EVENTS:
        return None

    texts = [_event_text(r["label"], r["category"]) for r in rows]
    labels_arr: NDArray[np.int64] = np.array(
        [1 if r["abs_delta"] >= config.price_delta_threshold else 0 for r in rows],
        dtype=np.int64,
    )
    # Need at least one example of each class to fit a binary classifier.
    if int(labels_arr.sum()) == 0 or int((labels_arr == 0).sum()) == 0:
        return None
    categories = sorted({r["category"] for r in rows})

    pipeline = Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    max_features=config.max_features,
                    ngram_range=config.ngram_range,
                    sublinear_tf=True,
                ),
            ),
            ("clf", LogisticRegression(max_iter=1000, random_state=42, class_weight="balanced")),
        ]
    )

    cv_accuracy: float | None = None
    if len(texts) >= config.cv_folds * 2:
        folds = min(config.cv_folds, int(labels_arr.sum()), int((labels_arr == 0).sum()))
        if folds >= 2:
            try:
                scores = cross_val_score(pipeline, texts, labels_arr, cv=folds, scoring="accuracy")
                cv_accuracy = float(scores.mean())
            except Exception:
                pass

    pipeline.fit(texts, labels_arr)

    return NewsTagger(
        config=config,
        _pipeline=pipeline,
        n_events=len(rows),
        categories=categories,
        cv_accuracy=cv_accuracy,
    )


async def tag_recent_events(
    pool: Any,
    tagger: NewsTagger,
    *,
    category: str | None = None,
    window: timedelta = timedelta(days=7),
) -> list[NewsTagResult]:
    """Classify all news events in the recent window."""
    since = datetime.now(tz=UTC) - window
    events = await _fetch_news_events(pool, since, category)
    return [tagger.predict_one(r["label"], r["category"], UUID(str(r["id"]))) for r in events]


async def write_nlp_signals(
    pool: Any,
    results: list[NewsTagResult],
) -> int:
    """Persist market_moving_prob signals to the signals table.

    Writes one row per result using the event_id as the market_id reference
    in the metadata field; the signal value is the probability in [0, 1].
    """
    if not results:
        return 0
    now = datetime.now(tz=UTC)
    rows = [
        (
            now,
            r.event_id,
            "market_moving_prob",
            r.market_moving_prob,
            {
                "label": r.label,
                "category": r.category,
                "is_market_moving": r.is_market_moving,
                "top_tokens": r.top_tokens,
            },
        )
        for r in results
    ]
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO signals
                (signal_ts, market_id, signal_type, value, metadata)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT DO NOTHING
            """,
            rows,
        )
    return len(rows)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _event_text(label: str, category: str) -> str:
    return f"{category} {label}"


def _top_tokens_for_text(pipeline: Pipeline, text: str, n: int = 5) -> list[str]:
    """Return the n highest-weight TF-IDF tokens present in `text` that the
    classifier associates with the market-moving class.
    """
    tfidf: TfidfVectorizer = pipeline.named_steps["tfidf"]
    clf: LogisticRegression = pipeline.named_steps["clf"]
    feature_names = tfidf.get_feature_names_out()
    coef: NDArray[np.float64] = clf.coef_[0]

    vec = tfidf.transform([text])
    present: NDArray[np.bool_] = np.asarray(vec[0].todense() > 0).flatten()
    masked: NDArray[np.float64] = np.where(present, coef, -np.inf)
    top_n = min(n, int(present.sum()))
    if top_n == 0:
        return []
    indices = np.argsort(masked)[-top_n:][::-1]
    return [str(feature_names[i]) for i in indices]


async def _fetch_training_rows(
    pool: Any,
    since: datetime,
    category: str | None,
    post_window_hours: int,
) -> list[dict[str, Any]]:
    """Fetch news events paired with measured abs(price_delta)."""
    async with pool.acquire() as conn:
        events = await conn.fetch(
            """
            SELECT ne.id, ne.occurred_at, ne.category, ne.label
            FROM   news_events ne
            WHERE  ne.occurred_at >= $1
              AND  ($2::text IS NULL OR ne.category = $2)
            ORDER BY ne.occurred_at
            """,
            since,
            category,
        )
        if not events:
            return []

        rows: list[dict[str, Any]] = []
        post_td = timedelta(hours=post_window_hours)
        for evt in events:
            occurred = evt["occurred_at"]
            cat = evt["category"]
            post_end = occurred + post_td
            # Compute mean price delta across markets in category over post window.
            delta_rows = await conn.fetch(
                """
                SELECT AVG(s.value) - pre.pre_mean AS delta
                FROM   signals s
                CROSS JOIN LATERAL (
                    SELECT AVG(sp.value) AS pre_mean
                    FROM   signals sp
                    WHERE  sp.market_id = s.market_id
                      AND  sp.signal_type = 'p_mid'
                      AND  sp.signal_ts BETWEEN $3 - INTERVAL '1 hour' AND $3
                ) pre
                WHERE  s.signal_type = 'p_mid'
                  AND  s.signal_ts BETWEEN $3 AND $4
                  AND  s.market_id IN (
                      SELECT id FROM markets
                      WHERE  category = $2
                        AND  settled_value IS NULL
                  )
                GROUP BY s.market_id
                HAVING COUNT(*) > 0 AND pre.pre_mean IS NOT NULL
                """,
                None,  # unused placeholder ($1 kept consistent)
                cat,
                occurred,
                post_end,
            )
            if not delta_rows:
                continue
            abs_delta = float(
                np.mean([abs(float(r["delta"])) for r in delta_rows if r["delta"] is not None])
            )
            rows.append(
                {
                    "id": evt["id"],
                    "label": evt["label"],
                    "category": cat,
                    "abs_delta": abs_delta,
                }
            )
    return rows


async def _fetch_news_events(
    pool: Any,
    since: datetime,
    category: str | None,
) -> list[dict[str, Any]]:
    """Fetch raw news event rows for classification."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, occurred_at, category, label
            FROM   news_events
            WHERE  occurred_at >= $1
              AND  ($2::text IS NULL OR category = $2)
            ORDER BY occurred_at
            """,
            since,
            category,
        )
    return [dict(r) for r in rows]
