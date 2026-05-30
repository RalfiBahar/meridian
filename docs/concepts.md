# Financial and statistical concepts

The math and intuition behind what Meridian computes. Each concept is
introduced with the minimum derivation needed to be defensible in an
interview, plus a pointer to where (and when) it's used in the codebase.

---

## Order book mechanics

A prediction-market contract is, mechanically, a **binary asset** that pays
exactly $1 if the event resolves YES and $0 if it resolves NO. The market
for this contract is a continuous double auction.

At any instant, there is a set of *resting* limit orders:

- **Bids** — people willing to buy YES at various prices. Sorted descending
  by price (best bid first).
- **Asks** — people willing to sell YES at various prices. Sorted ascending
  by price (best ask first).

Top of book (L1) is two numbers: best bid, best ask. Full book (L2) is the
sorted ladder on each side, with the resting size at each price level.

For Kalshi binaries, prices are quoted in USD `0.0001`–`0.9999`. By
no-arbitrage, the prices of YES and NO sum to exactly $1.00 (in the
absence of bid-ask spread), since holding one YES + one NO pays exactly $1
regardless of outcome.

```
   Asks (selling YES)               Bids (buying YES)
   ─────────────────               ─────────────────
   $0.68 × 500 contracts            $0.62 × 800 contracts
   $0.67 × 200 contracts            $0.61 × 1500 contracts
   $0.66 × 300 contracts            $0.60 × 2000 contracts
   ↑ best ask = $0.66               ↑ best bid = $0.62

   Spread = $0.66 - $0.62 = $0.04
   Mid    = ($0.66 + $0.62) / 2 = $0.64
```

### Kalshi's two-sided quoting convention

Kalshi doesn't post asks directly. Instead, both sides of the book are
quoted as **bids**:

- `yes_dollars`: people bidding to buy YES.
- `no_dollars`:  people bidding to buy NO.

To recover the YES ask, use the no-arbitrage relation `P(YES) + P(NO) =
$1.00`. The best ask on YES = `$1.00 - best bid on NO`. Symmetrically for
NO.

Concretely in the code:

```python
# meridian/kalshi/models.py
def yes_best_ask(self) -> Decimal | None:
    if not self.no:
        return None
    return Decimal(1) - max(level[0] for level in self.no)
```

---

## Implied probability from an order book

The headline claim from quant Twitter is "on a $0–$1 binary contract, price
*is* the implied probability." That's nearly right but understates the
nuance. In a risk-neutral market with infinitely tight spreads and
infinite liquidity:

$$p_{\text{implied}} = \text{price of YES contract}$$

Real markets violate each assumption. So Meridian computes **three**
implied probabilities per market:

- $p_{\text{bid}} = $ best YES bid — the probability *you can sell at right
  now*. A lower bound on the market's belief.
- $p_{\text{ask}} = $ best YES ask — the probability *you can buy at right
  now*. An upper bound.
- $p_{\text{mid}} = (p_{\text{bid}} + p_{\text{ask}}) / 2$ — conventional
  point estimate. **Biased** when depth is asymmetric (see microprice
  below).

The spread $p_{\text{ask}} - p_{\text{bid}}$ tells you the market's
uncertainty about its own belief. **Always display implied probabilities
as `[bid, ask]` intervals, never as point estimates.** A 1-cent spread on a
liquid contract reads totally different from a 10-cent spread on a thin
one. This is the single highest-signal framing for "this person knows what
they're talking about" in an interview.

### Risk-neutral vs physical probabilities

When you say "the market implies P(X) = 65%," you mean the **risk-neutral**
probability. It's the probability that makes the contract's price equal to
its expected payoff under that probability measure. For binary outcomes on
liquid markets with negligible carry costs and no systematic risk premium,
risk-neutral ≈ physical. For Fed rate decisions and economic data, this
approximation is excellent. For long-tail events or risky-asset
correlations, it isn't.

Phase 5 will return to this distinction when computing implied Fed rate
distributions.

---

## Microprice (your first piece of real microstructure math)

The mid is a bad point estimate when depth is asymmetric. The microprice
fixes it.

Define the **imbalance**:

$$I = \frac{Q_b}{Q_b + Q_a}$$

where $Q_b$ is the size at the best bid and $Q_a$ is the size at the best
ask. $I \in [0, 1]$. Larger $I$ = more pressure on the buy side.

The microprice is the size-weighted average of bid and ask, with the
weights **flipped**:

$$p_{\text{micro}} = I \cdot p_{\text{ask}} + (1 - I) \cdot p_{\text{bid}}$$

**Why flipped?** Intuition: when there's a giant bid stack just below the
mid (large $Q_b$, high $I$), the next thing to happen is probably someone
*lifting* an ask — pushing the next trade upward, toward the ask. The
microprice anticipates that.

**Why care?** Empirically, the microprice is a better predictor of the
*next mid* than the current mid is (Stoikov 2018, Cartea/Jaimungal). When
we compute derived signals in Phase 2, microprice is the canonical
top-of-book point estimate, not mid.

**Interview line:** *"Why microprice and not mid?"* — answer: because mid
weights both sides equally, but the next executable trade hits the side
with less posted size. Microprice corrects for the asymmetry.

---

## Depth-aware implied probability

Top-of-book numbers are misleading when you want to execute size. Walk the
ladder instead:

$$p_{\text{ask-exec}}(V) = \frac{\sum_{i=1}^{k} p_i \cdot q_i}{V},
  \quad \text{where } \sum_{i=1}^{k} q_i = V$$

For a target volume $V$, accumulate sizes from the best ask outward until
you've reached $V$; the volume-weighted price is your effective execution
price.

This is what makes Phase 3's arbitrage detection **real**: a 3-cent gap
between Kalshi and Polymarket isn't arbitrage if only 10 contracts are
available at the favorable price. **Apparent edge × executable depth =
real edge.** Phase 4's execution simulator walks the historical book this
way to estimate slippage.

---

## Bid-ask spreads and what they mean

The spread is not just transaction cost. It's a signal about:

- **Information.** Wider spreads when market-makers fear adverse
  selection — i.e., they think the next counterparty might know something.
- **Liquidity.** Wider when fewer market-makers are active.
- **Volatility.** Wider when expected near-term price changes are large.
- **Time-to-resolution.** Often tighter as a market approaches settlement,
  when uncertainty resolves.

Glosten-Milgrom (1985) is the canonical model deriving spreads from
adverse selection. We won't implement it directly, but the conceptual
framing — *the spread is the market-maker's compensation for trading
against possibly-informed counterparties* — is the right mental model.

---

## Sequence numbers and idempotency

Networks lose messages. Workers crash mid-write. Reconnect logic re-subscribes
and the venue helpfully resends the last N seconds.

Every Kalshi WS payload carries a `seq` value — monotonic per subscription.
Our `ticks` table has a UNIQUE constraint on `(market_id, sequence_no,
event_ts)`. Inserts use `ON CONFLICT DO NOTHING`. The second time you see
the same event, the database silently drops it.

The pattern: **at-least-once delivery + idempotent writes =
effectively-exactly-once semantics from the application's point of view.**
Same pattern Kafka deployments use. It's the reason reconnect-and-replay
is safe.

**Gap detection** is the other half. Track the last seen `seq` per
subscription. If next `seq` > last + 1, you missed events. Don't silently
ignore — emit a `signal_type='gap_detected'` row in `signals`, and kick a
REST backfill to recover the missing range. Calibration analysis should
discount any window with detected gaps.

In an interview: *"How did you handle duplicate ticks on reconnect?"* is a
real question. Your answer is the two paragraphs above.

---

## Calibration (Phase 2 preview)

Once a market settles, we know the realized outcome $o \in \{0, 1\}$. We
also have the full history of implied probability $p_t$ for $t \in [t_0,
t_{\text{settle}}]$. We can ask: *was the market well-calibrated?*

### Brier score

$$BS = \frac{1}{N} \sum_t (p_t - o)^2$$

Mean squared error of the probability forecast against the realized
outcome. Lower is better. Decomposes via the **Murphy decomposition** into
three terms:

$$BS = \underbrace{\text{Reliability}}_{\text{bias}} -
       \underbrace{\text{Resolution}}_{\text{informativeness}} +
       \underbrace{\text{Uncertainty}}_{\text{base rate}}$$

- **Reliability**: are forecasts of 70% actually right 70% of the time?
- **Resolution**: do the forecasts even *vary*, or are they all the same?
- **Uncertainty**: how uncertain is the base rate of the event itself?

The Murphy decomposition is the single most important calibration concept
to be able to draw on an interview whiteboard.

### Log loss

$$\text{LogLoss} = -(o \log p + (1 - o) \log(1 - p))$$

Penalizes confident-wrong predictions much more than Brier does. The
training loss of any binary classifier.

### Reliability diagrams

Bucket forecasts by predicted probability (e.g., 0-10%, 10-20%, ...). For
each bucket, plot bucket-midpoint vs realized YES rate. A perfectly
calibrated forecaster lies on $y = x$.

### Isotonic regression

A monotone-non-decreasing function fit to (raw probability → realized
rate) pairs. Used to **recalibrate** systematically biased probability
sources. Preferred over Platt scaling when you have enough data because it
makes no parametric assumption.

---

## No-arbitrage constraints (Phase 3 preview)

For any partition of an event's mutually-exclusive outcomes $\{A_1, A_2,
..., A_n\}$:

$$\sum_i p_i = 1$$

in the absence of bid-ask spread. With spread:

$$\sum_i \text{bid}_i \leq 1 \leq \sum_i \text{ask}_i$$

This defines the **no-arbitrage cone**. Prices outside this cone are
arbitrageable.

For *partial* partitions or *implication* constraints (e.g., "Trump wins
Pennsylvania" implies a constraint vs "Trump wins the presidency"), the
constraints are linear inequalities, and the consistency check is a
linear program. Phase 3 uses `cvxpy` to solve these.

**Execution feasibility:** flagging an arb only matters if you could
actually execute it. A 5-cent inconsistency with $10 of depth on the
favorable side isn't an arb. Phase 3's detector reports both the apparent
violation magnitude and the depth-feasible magnitude after fees.

---

## Implied probability distributions (Phase 5 preview)

This is the headline application for the Fed contracts.

Kalshi lists contracts indexed by strike: `KXFED-26JUN-T2.75`,
`KXFED-26JUN-T3.00`, `KXFED-26JUN-T3.25`, ..., `KXFED-26JUN-T5.25`. Each
contract pays $1 if the Fed funds rate at the June 2026 FOMC is **above**
that strike.

Reading prices off the order book gives us a **survival function**:

$$S(x) = P(\text{rate} > x)$$

Taking adjacent differences gives the **probability mass function**:

$$P(\text{rate} \in (x_i, x_{i+1}]) = S(x_i) - S(x_{i+1})$$

A live snapshot from 2026-05-28:

| Strike | $S(x)$ | $P(\text{rate in band})$ |
|---:|---:|---:|
| 2.75% | 99.5% | 0.0% (band: 2.75-3.00) |
| 3.00% | 99.5% | 0.0% (band: 3.00-3.25) |
| 3.25% | 99.5% | 3.0% (band: 3.25-3.50) |
| 3.50% | 96.5% | **94.0%** (band: 3.50-3.75) ← market's modal expectation |
| 3.75% |  2.5% | 2.0% (band: 3.75-4.00) |
| 4.00% |  0.5% | ... |

Total probability mass sums to ~100% (modulo bid-ask spread leakage). This
is the implied PMF over Fed funds rates. CME FedWatch computes the same
quantity from interest-rate-futures prices; cross-validating against it is
a great Phase 5 sanity check.

The **Bayesian event-response** part of Phase 5 watches what happens to
this distribution when a FOMC statement, CPI release, or jobs report hits
— quantifying how fast and how much the market updates.

---

## Portfolio math (Phase 6 preview)

Given:

- A vector $\mu$ of expected returns (here: detected edges from earlier
  phases).
- A covariance matrix $\Sigma$ of returns.
- A risk-aversion parameter $\lambda$.

The mean-variance optimizer solves:

$$\max_w \mu^T w - \lambda w^T \Sigma w
  \quad \text{s.t.} \quad \mathbf{1}^T w \leq B, \; w \geq 0, \; w \leq w_{\max}$$

with budget constraint and per-position caps. Classical Markowitz.

**Real-world wrinkles** Phase 6 will address:

- $\Sigma$ is estimated from finite data and is noisy. Use **Ledoit-Wolf
  shrinkage** to stabilize.
- Walk-forward evaluation, never naive train/test splits — finance has
  survivorship and look-ahead bias built in.
- Sizing via **fractional Kelly** rather than full Kelly (full Kelly
  assumes you've correctly estimated $\mu$, and you haven't).

---

## Glossary

- **Aggressor**: the side of a trade that crossed the spread to execute.
  Buy aggressor = took an ask; sell aggressor = hit a bid.
- **Best bid / best ask**: top price on each side of the book.
- **Binary contract**: pays $1 if YES, $0 if NO.
- **CDF**: cumulative distribution function.
- **Continuous aggregate**: TimescaleDB feature that materializes a
  time-bucketed view (1s/1m/1h OHLC, ...) and refreshes incrementally.
- **Depth**: total resting size on one side of the book, or at one price
  level.
- **Effective spread**: actual round-trip cost a taker pays, accounting
  for depth.
- **Hypertable**: TimescaleDB's transparently-partitioned table type.
- **Implied probability**: the market's (risk-neutral) probability of an
  event, derived from a contract's price.
- **L1 / L2**: order-book depth levels. L1 = top of book only. L2 = full
  ladder per side.
- **Microprice**: size-weighted average of bid and ask with flipped
  weights. See above.
- **Mid**: arithmetic mean of best bid and best ask.
- **OHLC**: open / high / low / close — standard time-bucketed market
  summary.
- **PMF / PDF**: probability mass / density function.
- **Settlement**: the moment a market resolves YES or NO and pays out.
- **Sequence number (`seq`)**: a monotonic counter from the venue used
  for ordering and gap detection.
- **Spread**: best ask minus best bid.
- **Tick**: in our system, any event from a venue — quote, trade, book
  update, status change.

---

## Recommended reading

- **Hasbrouck**, *Empirical Market Microstructure* — the standard text.
- **Stoikov 2018**, "The micro-price" — original microprice paper.
- **Murphy 1973**, "A new vector partition of the probability score" — the
  Brier decomposition.
- **Glosten & Milgrom 1985**, "Bid, ask, and transaction prices in a
  specialist market" — adverse-selection model of spreads.
- **Aldridge & Krawciw**, *Real-Time Risk* — book-replay-based risk
  estimation.
- **Cartea, Jaimungal, Penalva**, *Algorithmic and High-Frequency Trading*
  — modern coverage of microstructure and execution.
