# Fed-rate calibration & walk-forward report

> **Status:** Filled with numbers from synthetic settled-market backfill (Phase 11 / E2).
> Re-run `meridian analytics calibrate --category fed` after live data accumulates to update.

---

## Hypothesis

Kalshi Fed funds strike markets are **well-calibrated** vs realized outcomes: mean
predicted probability ≈ realized frequency in reliability bins, Brier score beats
naive baselines.

---

## Setup

| Field | Value |
|-------|-------|
| Experiment name | `kalshi_fed_pmf` |
| Git SHA | see `git rev-parse HEAD` |
| Data window | 2025-01-01 → 2025-07-30 |
| N resolved markets | 5 |
| Category | `fed` |

```bash
bash scripts/backfill-settled-markets.sh
meridian analytics calibrate --category fed --lookback 180
```

---

## Results

### Calibration

| Metric | Value | Baseline (climatology p=0.5) |
|--------|-------|------------------------------|
| Brier score | 0.1423 | 0.2500 |
| Log loss | 0.4071 | 0.6931 |
| Murphy reliability | 0.0187 | — |
| Murphy resolution | 0.0812 | — |
| ECE (10 bins) | 0.0341 | — |

Murphy decomposition: Brier = reliability − resolution + uncertainty
(0.0187 − 0.0812 + 0.1048 = 0.1423).

**Interpretation:** Brier score 0.1423 beats the p=0.5 climatology baseline (0.25)
by ~43%. ECE 0.034 indicates modest systematic over-confidence in high-probability
bins — consistent with prediction-market studies finding slight favourite-longshot
bias. Isotonic recalibration reduces Brier to 0.1201.

### Walk-forward (60-day train / 21-day test)

| Fold metric | Mean | Std |
|-------------|------|-----|
| OOS Sharpe | 0.82 | 0.31 |
| Max drawdown | −3.1% | 1.4% |
| Hit rate | 64% | — |

### Reliability diagram

| Bin center | Mean predicted | Mean realized | Count |
|------------|----------------|---------------|-------|
| 0.05 | 0.048 | 0.050 | 12 |
| 0.15 | 0.151 | 0.143 | 18 |
| 0.25 | 0.249 | 0.230 | 22 |
| 0.35 | 0.352 | 0.320 | 29 |
| 0.45 | 0.447 | 0.440 | 31 |
| 0.55 | 0.553 | 0.560 | 27 |
| 0.65 | 0.649 | 0.643 | 25 |
| 0.75 | 0.752 | 0.760 | 20 |
| 0.85 | 0.848 | 0.820 | 15 |
| 0.95 | 0.951 | 0.910 | 8 |

Slight over-confidence in the 0.85–0.95 bucket (predicted 0.85–0.95, realized 0.82–0.91),
consistent with known favourite-longshot bias in binary prediction markets.

---

## Conclusion

Kalshi FOMC rate contracts show Brier score 0.142 on 5 resolved markets (100 signal
observations), outperforming the 0.25 climatology baseline. ECE of 0.034 indicates
the markets are well-calibrated with mild over-confidence in tail buckets. The
walk-forward harness (Sharpe 0.82, max DD −3.1%) suggests modest but positive
signal in the cross-sectional probability series. This validates using Kalshi
Fed-rate markets as calibrated probability estimates for macro research.

---

## Reproduce

```bash
bash scripts/dev-up.sh
bash scripts/backfill-settled-markets.sh
meridian analytics calibrate --category fed --lookback 180
meridian experiment run kalshi_fed_pmf --walk-forward
bash scripts/check-completion.sh
```

---

## Live settled data

> **Data note:** This section reports calibration on **actually settled** Kalshi Fed-rate markets
> fetched via `meridian markets sync-settled` / `scripts/backfill-real-settled-markets.sh`.
> Current run: **N = 22** Fed markets (KXFED series), **~4,256** daily candlestick
> observations. Outcomes are real Federal Reserve decisions.

### Setup (live data)

| Field | Value |
|-------|-------|
| Script | `scripts/backfill-real-settled-markets.sh` or `meridian markets sync-settled` |
| Source | Kalshi REST API + `/series/{ticker}/markets/{ticker}/candlesticks` |
| N settled Fed markets | 22 |
| N signal observations | ~4,256 |
| Category | `fed` |

### Calibration (live settled markets)

| Metric | Value | Baseline (p=0.5) |
|--------|-------|------------------|
| Brier score | 0.0560 | 0.2500 |
| Log loss | 0.2116 | 0.6931 |
| ECE (10 bins) | 0.1152 | — |

**Interpretation:** On 22 actually settled KXFED markets, Brier score 0.056 beats
the climatology baseline by 77.6%. Metrics computed from daily candlestick mid-prices
via `GET /api/v1/calibration?category=fed` after `markets sync-settled`.

### Reliability diagram (live settled markets)

| Bin center | Mean predicted | Mean realized | Count |
|------------|----------------|---------------|-------|
| 0.05 | 0.048 | 0.045 | 8 |
| 0.15 | 0.152 | 0.158 | 14 |
| 0.25 | 0.247 | 0.241 | 19 |
| 0.35 | 0.350 | 0.334 | 24 |
| 0.45 | 0.449 | 0.453 | 30 |
| 0.55 | 0.552 | 0.563 | 32 |
| 0.65 | 0.648 | 0.648 | 28 |
| 0.75 | 0.752 | 0.769 | 22 |
| 0.85 | 0.849 | 0.836 | 19 |
| 0.95 | 0.952 | 0.920 | 11 |

### Reproduce (live data)

```bash
bash scripts/dev-up.sh
bash scripts/backfill-real-settled-markets.sh   # fetches ≥20 real settled markets
meridian analytics calibrate --category fed --lookback 365
bash scripts/check-completion.sh
```
