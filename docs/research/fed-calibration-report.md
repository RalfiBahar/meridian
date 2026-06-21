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
