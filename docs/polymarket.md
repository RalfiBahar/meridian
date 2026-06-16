# Polymarket integration notes

Everything we know about the Polymarket CLOB API as of 2026-06-16, gathered
from <https://docs.polymarket.com> and live probes against the production
endpoints. This file is the project-specific distillation, mirroring
[kalshi.md](kalshi.md) in structure.

---

## There is no demo environment

Unlike Kalshi, Polymarket's CLOB has a single production deployment. There
is no sandbox to point at by mistake — every endpoint below is live, real
money data. That's fine for Meridian: we are read-only and never place
orders.

---

## Authentication — not needed for what we do

Polymarket's CLOB uses a two-level auth model, but **both levels are only
required for trading** (placing/cancelling orders):

- **L1** — EIP-712 signing with a Polygon wallet private key. Proves wallet
  ownership, used to derive L2 credentials.
- **L2** — `apiKey` / `secret` / `passphrase` derived from L1, used to sign
  trading requests with HMAC-SHA256 over `POLY_*` headers.

The CLOB's **read endpoints** (`/markets`, `/markets/{condition_id}`,
`/book`) and the **market data WebSocket channel** require **no
authentication at all** — confirmed empirically: every probe below was an
unauthenticated `curl`/WS connect and returned real data. Meridian's
`polymarket/` module therefore has no `auth.py` equivalent to Kalshi's
`KalshiSigner` — there is nothing to sign. If Meridian ever needs the user
channel (order/trade fills for an account) or trading, L1/L2 auth would need
to be added then; out of scope for read-only ingestion.

---

## Identity model: `condition_id` vs `token_id`

This is the biggest structural difference from Kalshi and worth understanding
before touching the code:

- A Polymarket **market** (`condition_id`) is the logical question (e.g.
  "Will X happen?"). It groups N **outcomes** (usually 2: Yes/No, but
  categorical markets have more).
- Each outcome is a separate **token** (`token_id`, also called `asset_id`
  in WS messages, a huge decimal-string integer) with **its own independent
  CLOB order book**. The WebSocket market channel subscribes by `asset_ids`
  (plural, list of token IDs), not by `condition_id`.
- For a binary Yes/No market, "Yes" and "No" are two separately-traded
  tokens whose prices are *expected* to sum to ~$1 (no-arb), but Polymarket
  does not enforce or even necessarily quote them as mirror images the way
  Kalshi's single yes/no book does — they really are two independent books.

**Decision**: Meridian treats each **`token_id`** as the unit of identity —
analogous to a Kalshi `ticker`. `external_market_id` = the token_id string,
and `market_id = uuid5(POLYMARKET_UUID_NAMESPACE, token_id)`. This matches
how the WS protocol subscribes and how the REST `/book` endpoint is keyed.
The parent `condition_id` / `question` / outcome label are stored as
`markets` metadata (via REST enrichment), not as part of the identity key.
See ADR-015 in `DECISIONS.md`.

---

## REST endpoints we use

Base URL: `https://clob.polymarket.com` (no version prefix, no auth headers).

### `GET /markets?next_cursor=<cursor>`

Paginated list of all markets (active, closed, and archived — filter
client-side). Cursor is a base64 string; the **first page uses
`next_cursor=` (empty)**, not an omitted parameter. The end of pagination is
signaled by `next_cursor` looping back to `"LTE="` (the encoded sentinel
`-1`) rather than an empty/absent value — when you see that value, stop.

```json
{
  "data": [
    {
      "condition_id": "0x5eed579ff6763914d78a966c83473ba2485ac8910d0a0914eef6d9fcb33085de",
      "question_id": "0x2d5ddf657e4a090bc22921bf6865bcdb741a7b96ce45eb583be041756fad04a0",
      "question": "NCAAB: Arizona State Sun Devils vs. Nevada Wolf Pack 2023-03-15",
      "market_slug": "ncaab-arst-nev-2023-03-15",
      "end_date_iso": "2023-03-15T00:00:00Z",
      "active": true,
      "closed": true,
      "accepting_orders": false,
      "enable_order_book": false,
      "minimum_tick_size": 0.01,
      "minimum_order_size": 15,
      "neg_risk": false,
      "tokens": [
        { "token_id": "734705413...461644", "outcome": "Arizona State", "price": 1, "winner": true },
        { "token_id": "563937617...892311293", "outcome": "Nevada", "price": 0, "winner": false }
      ]
    }
  ],
  "next_cursor": "MTAwMA==",
  "limit": 1000,
  "count": 1000
}
```

### `GET /markets/{condition_id}`

Same shape as one element of the `data` array above, unwrapped (no
envelope).

### `GET /book?token_id={token_id}`

L2 snapshot for one token's order book. Same shape as the WS `book` event
(minus `event_type`):

```json
{
  "market": "0x1fad72fae204143ff1c3035e99e7c0f65ea8d5cd9bd1070987bd1a3316f772be",
  "asset_id": "98022490269692409998126496127597032490334070080325855126491859374983463996227",
  "timestamp": "1781586414352",
  "hash": "6ddb005ee385ee5d7f90d8d55b6755ad5955bc10",
  "bids": [{ "price": "0.01", "size": "2462.11" }, ...],
  "asks": [{ "price": "0.52", "size": "25" }, ...]
}
```

Returns `{"error": "No orderbook exists for the requested token id"}` (still
HTTP 200) for closed/inactive tokens — treat a missing `bids`/`asks` key as
"no book," not as an error.

### Gamma API (market discovery, not CLOB)

`https://gamma-api.polymarket.com/markets?active=true&closed=false` is a
separate, friendlier discovery API used by the Polymarket frontend. It
returns human-readable fields (`conditionId`, `clobTokenIds` as a
JSON-encoded string, `outcomes` as a JSON-encoded string) and is useful for
finding "what's currently active" since the CLOB `/markets` list above is
not filterable by active/closed server-side in a convenient way and returns
markets in an opaque (apparently creation-order) sequence. Meridian's
`PolymarketClient` does not wrap Gamma — only the CLOB REST/WS surface,
matching "all canonical events must use the same `CanonicalEvent` model" —
but it's worth knowing about for ad hoc market discovery.

---

## WebSocket protocol

### Connect

```
wss://ws-subscriptions-clob.polymarket.com/ws/market
```

No auth headers. After connecting, send a subscribe message:

```json
{
  "assets_ids": ["<token_id_1>", "<token_id_2>"],
  "type": "market",
  "custom_feature_enabled": true
}
```

(`custom_feature_enabled: true` opts into the `best_bid_ask` /
`new_market` / `market_resolved` events; we don't currently subscribe to
those, but the flag is harmless to send.)

### Heartbeat

The client must send the literal text frame `PING` every ~10 seconds; the
server replies `PONG`. Miss this and the server disconnects after ~10s of
silence. This is unlike Kalshi, which has no required client-side
heartbeat. `PolymarketWebSocketClient` runs a background task for this.

### Message envelope

Unlike Kalshi's uniform `{type, sid, seq, msg}` envelope, each Polymarket
message is a flat dict discriminated by `event_type`, and **carries no
sequence number at all** — only a millisecond `timestamp` string. See
"No gap detection" below for the consequence.

### `book` — full snapshot

Sent on subscribe and again whenever the book is republished from a trade
that crosses many levels.

```json
{
  "event_type": "book",
  "asset_id": "65818619657568813474341868652308942079804919287380422192892211131408793125422",
  "market": "0xbd31dc8a20211944f6b70f31557f1001557b59905b7738480ca09bd4532f84af",
  "bids": [{ "price": ".48", "size": "30" }, { "price": ".49", "size": "20" }],
  "asks": [{ "price": ".52", "size": "25" }, { "price": ".53", "size": "60" }],
  "timestamp": "123456789000",
  "hash": "0x0...."
}
```

Maps directly onto our existing `BookEvent` (`bid`/`ask` sides — Polymarket
has no Kalshi-style yes/no book duality since each outcome already has its
own independent book).

### `price_change` — level update(s)

```json
{
  "market": "0x5f65177b394277fd294cd75650044e32ba009a95022d88a0c1d565897d72f8f1",
  "price_changes": [
    {
      "asset_id": "71321045679252212594626385532706912750332728571942532289631379312455583992563",
      "price": "0.5",
      "size": "200",
      "side": "BUY",
      "best_bid": "0.5",
      "best_ask": "1"
    }
  ],
  "timestamp": "1757908892351",
  "event_type": "price_change"
}
```

One message may batch several `price_changes` entries (different
asset_ids/prices). **Important wire quirk**: `size` is the *new absolute
resting size* at that `(asset_id, side, price)` level after the update —
**not an incremental delta** like Kalshi's `delta_fp`. `side` is `"BUY"`
(bid side of the book) or `"SELL"` (ask side), confusingly using
order-side rather than book-side vocabulary; we map `BUY -> bid`,
`SELL -> ask`.

Our canonical `BookDeltaEvent.delta` is defined as a *signed increment*
(matching Kalshi's native semantics). To populate it from Polymarket's
absolute sizes without baking statefulness into the otherwise-pure
`normalize_polymarket_message()`, the **caller** maintains a small
per-`(asset_id, side, price)` size cache (`PolymarketBookState`,
in `polymarket/normalize.py`) and passes the previous size in; the
normalizer computes `delta = new_size - previous_size` and the caller
updates its cache. See ADR-016 in `DECISIONS.md`.

### `last_trade_price` — trade execution

```json
{
  "asset_id": "114122071509644379678018727908709560226618148003371446110114509806601493071694",
  "event_type": "last_trade_price",
  "fee_rate_bps": "0",
  "market": "0x6a67b9d828d53862160e470329ffea5246f338ecfffdf2cab45211ec578b0347",
  "price": "0.456",
  "side": "BUY",
  "size": "219.217767",
  "timestamp": "1750428146322"
}
```

Maps directly to `TradeEvent`; `side` (`BUY`/`SELL`) becomes
`aggressor` (`buy`/`sell`).

### `tick_size_change`

```json
{
  "event_type": "tick_size_change",
  "asset_id": "...",
  "market": "...",
  "old_tick_size": "0.01",
  "new_tick_size": "0.001",
  "timestamp": "100000000"
}
```

Informational only (affects how finely the book can quote going forward).
Meridian's normalizer returns no `CanonicalEvent` for this — analogous to
Kalshi's control messages.

### `best_bid_ask`, `new_market`, `market_resolved`

Only sent when `custom_feature_enabled: true`. Not yet consumed by
Meridian's normalizer (no payload builder registered) — they would arrive
and be silently skipped like an unknown Kalshi message type.

---

## No gap detection (unlike Kalshi)

Kalshi's `GapDetector` (`ingest/gap.py`) works because every message carries
a monotonic `seq` per subscription. Polymarket's market channel publishes
**no sequence number of any kind** — only a wall-clock `timestamp`. There is
therefore no reliable way to detect a dropped message client-side; a missed
`price_change` is indistinguishable from "no change happened." The
`PolymarketIngestWorker` does not use `GapDetector` and `ingest_gaps_total`
stays at 0 for the Polymarket venue. The periodic `book` snapshot (resent
whenever the book changes substantially) is the closest thing to
self-healing this protocol offers; the worker still resets its in-memory
`PolymarketBookState` on every `book` message to bound any drift.

---

## Rate limits

Undocumented precisely; in practice the REST `/markets` list endpoint is
generous (we paginated thousands of rows with no delay during research with
no 429s). The WS connection has no documented per-message limit beyond the
PING/PONG heartbeat requirement above.

---

## When the API changes

1. Probe the live shape directly: `curl -s https://clob.polymarket.com/markets?next_cursor= | python3 -m json.tool | head`.
2. Compare to `PolymarketMarket` / `PolymarketOrderbook` in
   [polymarket/models.py](../src/meridian/polymarket/models.py).
3. Update the normalizer in
   [polymarket/normalize.py](../src/meridian/polymarket/normalize.py) and
   the tests in
   [test_polymarket_normalize.py](../tests/test_polymarket_normalize.py).
4. Update this doc with the new shape and the date discovered.
