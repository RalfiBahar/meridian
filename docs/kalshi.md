# Kalshi integration notes

Everything I know about the Kalshi API as of 2026-05-28. The official
documentation is at <https://docs.kalshi.com>; this file is the
project-specific distillation, with the quirks we discovered the hard way.

---

## Environments

| Env | REST | WebSocket |
|---|---|---|
| **Production** | `https://api.elections.kalshi.com/trade-api/v2` | `wss://api.elections.kalshi.com/trade-api/ws/v2` |
| **Demo** | `https://demo-api.kalshi.co/trade-api/v2` | `wss://demo-api.kalshi.co/trade-api/ws/v2` |

The demo and production accounts are **separate**. Sign up on the demo site
to use demo; same for prod.

### Demo vs production — and why we use production

The demo environment is a sandbox for testing **order placement**, not a
mirror of production prices. We confirmed this empirically: out of ~4,000
demo markets sampled with `status=open`, every single one returned
`yes_bid=None, yes_ask=None, volume_24h=None`. The orderbook endpoint
returned `{"yes": [], "no": []}`. Demo is **useless for reading market
data**.

For a read-only research platform like Meridian, production is the only
option. Production **read-only** endpoints (everything Meridian calls)
cannot place orders or move money. Only the `POST /portfolio/orders`
endpoint can, and we never call it.

**`MERIDIAN_KALSHI_ENV=prod`** is the right setting for any real work. Keep
your demo keypair around only if you ever want to test order-placement code
paths (which Meridian does not have).

---

## Authentication

Every authenticated request requires three headers:

```
KALSHI-ACCESS-KEY        <your API key ID, an opaque UUID-like string>
KALSHI-ACCESS-TIMESTAMP  <Unix milliseconds, as a string>
KALSHI-ACCESS-SIGNATURE  <base64 of RSA-PSS signature over the payload>
```

### Keypair

- **Type:** 2048-bit RSA, PKCS#8 or PKCS#1 (`-----BEGIN PRIVATE KEY-----`
  or `-----BEGIN RSA PRIVATE KEY-----` PEM). Generated in your account's
  API Keys section. **Shown exactly once** — save it immediately.
- **Storage:** Local file on disk, `chmod 600`. Path is set via
  `MERIDIAN_KALSHI_PRIVATE_KEY_PATH` (default
  `~/.config/meridian/kalshi-prod-private-key.pem`). The path may use
  `~`; the signer expands it.
- **Public key registration:** Kalshi already has the public key on file
  from your account UI; you do nothing more.

### Signing payload

```
message = f"{timestamp_ms}{METHOD_UPPER}{path}".encode()
```

- `timestamp_ms` — your local clock in milliseconds since the Unix epoch
  (e.g. `1779946509623`).
- `METHOD_UPPER` — HTTP method in uppercase (`GET`, `POST`, ...).
- `path` — full path including the `/trade-api/v2` prefix
  (e.g. `/trade-api/v2/markets`). Query string is **not** included.
- For WebSocket: sign `f"{timestamp_ms}GET/trade-api/ws/v2"`.

### Algorithm

```
signature = RSA-PSS(
    message,
    private_key,
    padding = PSS(MGF1(SHA256), salt_length=SHA256.digest_size),
    hash    = SHA256,
)
header_value = base64.b64encode(signature).decode("ascii")
```

The signature is randomized (random salt), so two consecutive signs of the
same message produce different bytes. That's expected — verification uses
the public key + the same padding params and accepts either.

Reference implementation: [src/meridian/kalshi/auth.py](../src/meridian/kalshi/auth.py).
Reference tests: [tests/test_kalshi_auth.py](../tests/test_kalshi_auth.py),
which generate a throwaway keypair and verify the signature with the
matching public key.

---

## REST endpoints we use

All paths below are appended to the env-specific REST base
(`api.elections.kalshi.com/trade-api/v2` for prod). All require auth headers.

### `GET /exchange/status`

Returns whether the exchange and trading systems are up.

```json
{ "exchange_active": true, "trading_active": true }
```

This is our auth-handshake smoke test. If you can call this, your
credentials work end-to-end.

### `GET /markets`

Lists markets, paginated by cursor.

Query params:

- `limit` — page size (default 100, max ~1000).
- `cursor` — opaque pagination token from previous response.
- `status` — filter. Vocabulary: `unopened`, `open`, `closed`, `settled`,
  comma-separated for multiple.
- `event_ticker` — filter to one event (e.g. `KXFED-26JUN`).
- `series_ticker` — filter to one series (e.g. `KXFED`).

Response:

```json
{
  "markets": [
    { "ticker": "KXFED-26JUN-T3.75",
      "event_ticker": "KXFED-26JUN",
      "title": "Will Fed funds rate be above 3.75% in June 2026?",
      "status": "active",
      "yes_bid_dollars": "0.0200",
      "yes_ask_dollars": "0.0300",
      "no_bid_dollars":  "0.9700",
      "no_ask_dollars":  "0.9800",
      "last_price_dollars": "0.0250",
      "volume_24h_fp": "98506.75",
      "volume_fp":     "968366.40",
      "open_interest_fp": "843189.54",
      ...
    },
    ...
  ],
  "cursor": "Cgw..."
}
```

### `GET /markets/{ticker}`

Returns one market, wrapped under `"market"`.

### `GET /markets/{ticker}/orderbook`

Returns the L2 order book.

```json
{
  "orderbook_fp": {
    "yes_dollars": [
      ["0.0200", "1500.00"],
      ["0.0100", "5000.00"]
    ],
    "no_dollars": [
      ["0.9700", "1200.00"],
      ["0.9600", "3000.00"]
    ]
  }
}
```

The book is wrapped under `"orderbook_fp"` (with `_fp` suffix; this is
distinct from REST market price fields which use `_dollars` without the
suffix). Each side is a list of `[price_dollars_str, size_fp_str]` tuples.

`yes_dollars` lists **bids** on the YES side; `no_dollars` lists **bids**
on the NO side. Asks are not posted directly; the YES ask is inferred:

```
yes_ask = 1 - max(no_bid)
```

This is the canonical binary-contract convention: buying YES and buying NO
together pays exactly $1 at settlement, so their prices must sum to $1
under no-arbitrage. See [concepts.md](concepts.md#implied-probability-from-an-order-book).

---

## Wire format quirks

These are the things that bite you on first integration. Documented in the
hope you (or future me) won't relearn them.

### Prices are decimal strings, not integer cents

REST and WS both return prices as decimal strings: `"0.0200"`, `"0.9700"`.
*Not* integer cents (`2`, `97`). Some older API documentation describes
the integer-cent format; that format is no longer in use.

Our `KalshiMarket` and `KalshiOrderbook` models alias these to Python
`Decimal` attributes via Pydantic's `Field(alias=...)`. Read more in
[src/meridian/kalshi/models.py](../src/meridian/kalshi/models.py).

### Size and volume fields use `_fp` suffix

`yes_bid_size_fp`, `volume_24h_fp`, `open_interest_fp` — all decimal
strings. Kalshi supports fractional trading, so sizes are not necessarily
integers (`"19.00"`, `"301950.00"`, `"1500.50"`).

### Status-filter vocabulary

The `?status=` *query parameter* uses one set of values; the `status`
*response field* uses a different set.

| Context | Allowed values |
|---|---|
| `?status=` query param | `unopened`, `open`, `closed`, `settled` (comma-separated allowed) |
| `market.status` response field | `initialized`, `active`, `closed`, `settled`, `deactivated` |

We don't try to unify them. The CLI's `--status` flag passes the string
through to the API (no enum validation), and our `KalshiMarketStatus` enum
covers the response vocabulary only.

### Liquidity is uneven

A generic `?status=open` page request returns markets in an order that
heavily favors multivariate sports parlays (`KXMVE*` tickers). For
known-liquid contracts, use `event_ticker` or `series_ticker` filters.
Liquid series prefixes worth knowing:

| Prefix | Theme |
|---|---|
| `KXFED-*` | Fed funds rate decisions |
| `KXCPI-*` | CPI release outcomes |
| `KXPRES-*` | Presidential election (currently dormant; will return for 2028) |
| `KXTEMP*` | Major-city daily temperatures |
| `KXSPY*` | S&P 500 closing band |
| `KXMLB*`, `KXNBA*` | Major-league sports |

### Kalshi's `market_id` in WS payloads is not ours

The `market_id` field in WebSocket messages is Kalshi's *internal* UUID
for the market. We deliberately do not use it as our PK. Our `markets.id`
is a Meridian-controlled UUID; we key everything by `(venue,
market_ticker)` and look up our own ID at ingestion. See
[architecture.md](architecture.md#markets) for the rationale.

---

## WebSocket protocol

To be exercised in Phase 1c. Verified message shapes below.

### Connect

Same handshake as a REST GET on `/trade-api/ws/v2`: three signed headers.
After connect, send a JSON subscribe message:

```json
{
  "id": 1,
  "cmd": "subscribe",
  "params": {
    "channels": ["orderbook_delta", "ticker", "trade", "market_lifecycle_v2"],
    "market_tickers": ["KXFED-26JUN-T3.75", "KXFED-26JUN-T3.50"]
  }
}
```

### Channels

**Public** (subscribable without account-scoped permissions):

- `ticker` — top-of-book + last trade digest.
- `trade` — fills.
- `market_lifecycle_v2` — open/close/halt/settle transitions.
- `multivariate_market_lifecycle`, `multivariate` — multivariate-event
  events. We don't subscribe.

**Private** (still need auth headers; "private" here means "account-scoped"
even though the data is public):

- `orderbook_delta` — incremental L2 changes. The workhorse channel.
- `fill`, `market_positions`, `communications`, `order_group_updates` —
  user-scoped order/portfolio events. Meridian doesn't use these.

### Message envelope

Every server-sent message has shape:

```json
{ "type": "<message-type>", "sid": <subscription-id>, "seq": <number?>, "msg": {...} }
```

- `sid` is the subscription id, returned in the `subscribed` ack. It's
  per-channel: subscribing to `orderbook_delta` and `ticker` returns two
  separate `sid` values.
- `seq` is a **monotonic counter per `sid`**, not per market. Gap detection
  runs on the stream, not per ticker.

### `subscribed` ack

```json
{ "type": "subscribed", "id": 1, "msg": { "channel": "orderbook_delta", "sid": 1 } }
```

One per channel per subscribe command. The `id` echoes the client-supplied
id from the subscribe message.

### `orderbook_snapshot`

Sent once per market on subscribe to `orderbook_delta`. Carries the full
L2 book.

```json
{
  "type": "orderbook_snapshot",
  "sid": 1,
  "seq": 1,
  "msg": {
    "market_ticker": "KXFED-26JUN-T3.75",
    "market_id": "2906b4ee-0cba-4152-aa0f-1f6161908f47",
    "yes_dollars_fp": [
      ["0.0100", "289061.03"],
      ["0.0200", "3192.31"]
    ],
    "no_dollars_fp": [
      ["0.0100", "25921.23"],
      ...
    ]
  }
}
```

Snapshots do **not** carry `ts`. Use ingest time + `seq` for ordering.

Note the field naming difference vs REST orderbook:

- REST: `orderbook_fp.{yes_dollars, no_dollars}`
- WS: `msg.{yes_dollars_fp, no_dollars_fp}` (`_fp` appears on the side key, not on the wrapper)

### `orderbook_delta`

Signed change at one `(side, price)` level.

```json
{
  "type": "orderbook_delta",
  "sid": 1,
  "seq": 63,
  "msg": {
    "market_ticker": "KXFED-27JAN-T4.00",
    "market_id": "b635b4d7-7a60-4a4e-beba-c389360b00dd",
    "price_dollars": "0.6300",
    "delta_fp": "-13.00",
    "side": "no",
    "ts": "2026-05-28T05:35:09.623423Z",
    "ts_ms": 1779946509623
  }
}
```

`delta_fp` is *signed*: negative shrinks the level, positive grows it. If
the resulting size at that level is ≤ 0, drop the level entirely.

Applied to a local book, this is the standard **L2 delta application**
loop:

```python
def apply_delta(book, side, price, delta):
    book[side][price] = book[side].get(price, Decimal(0)) + delta
    if book[side][price] <= 0:
        del book[side][price]
```

### `ticker`

Composite top-of-book + last-trade digest.

```json
{
  "type": "ticker",
  "sid": 2,
  "msg": {
    "market_id": "...",
    "market_ticker": "KXFED-27JAN-T4.00",
    "price_dollars": "0.3400",
    "yes_bid_dollars": "0.3100",
    "yes_ask_dollars": "0.3700",
    "yes_bid_size_fp": "11.00",
    "yes_ask_size_fp": "16.00",
    "last_trade_size_fp": "1.00",
    "volume_fp": "6495.96",
    "open_interest_fp": "1019.80",
    "dollar_volume": 3247,
    "dollar_open_interest": 509,
    "ts": 1779946509,
    "ts_ms": 1779946509637,
    "time": "2026-05-28T05:35:09.637768Z"
  }
}
```

Strictly redundant with `orderbook_delta` + `trade`, but cheaper to consume
when you only need top-of-book + summary stats.

### `trade` and `market_lifecycle_v2`

Confirmed during the Phase 1c live run; shapes documented when observed.

---

## Rate limits

Per Kalshi's official documentation, REST has a per-key rate limit (single
digits per second on public endpoints; higher on authenticated). The
WebSocket connection has no per-message rate limit, but the number of
concurrent connections per key is limited. For Phase 1c's single
ingestion worker per environment we are nowhere near any limit.

In practice, we hit 429s only when running aggressive scan scripts (e.g.
paginating thousands of `?status=open` pages with no delay). Solution:
respect cursor pagination and avoid tight retry loops.

---

## When the API changes

Kalshi has historically renamed routes (the old `trading-api.readme.io`
host now redirects). When something breaks unexpectedly:

1. Check the live response shape by running:
   ```
   uv run python -c "import asyncio, json; ..."
   ```
   (or similar inline probe via `KalshiClient._request`).
2. Compare to what `KalshiMarket` / `KalshiOrderbook` expect.
3. Update the model aliases in [models.py](../src/meridian/kalshi/models.py)
   and the tests in [test_kalshi_client.py](../tests/test_kalshi_client.py).
4. Update this doc with the new shape and the date discovered.
