import { notFound } from "next/navigation";
import Link from "next/link";
import { fetchMarket } from "@/lib/api";
import type { BookLevel, TickRow } from "@/types/api";

export const revalidate = 5;

function pct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(2)}¢`;
}

function BookTable({ levels }: { levels: BookLevel[] }) {
  const bids = levels.filter((l) => l.side === "bid" || l.side === "yes").sort((a, b) => b.price - a.price);
  const asks = levels.filter((l) => l.side === "ask" || l.side === "no").sort((a, b) => a.price - b.price);

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
      <div>
        <div style={{ color: "var(--muted)", fontSize: 11, marginBottom: 6 }}>BIDS</div>
        <table>
          <thead>
            <tr>
              <th>Level</th>
              <th>Price</th>
              <th>Size</th>
            </tr>
          </thead>
          <tbody>
            {bids.map((b) => (
              <tr key={`bid-${b.level}`}>
                <td>{b.level}</td>
                <td style={{ color: "var(--green)" }}>{pct(b.price)}</td>
                <td>{b.size}</td>
              </tr>
            ))}
            {bids.length === 0 && (
              <tr>
                <td colSpan={3} style={{ color: "var(--muted)" }}>
                  empty
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div>
        <div style={{ color: "var(--muted)", fontSize: 11, marginBottom: 6 }}>ASKS</div>
        <table>
          <thead>
            <tr>
              <th>Level</th>
              <th>Price</th>
              <th>Size</th>
            </tr>
          </thead>
          <tbody>
            {asks.map((a) => (
              <tr key={`ask-${a.level}`}>
                <td>{a.level}</td>
                <td style={{ color: "var(--red)" }}>{pct(a.price)}</td>
                <td>{a.size}</td>
              </tr>
            ))}
            {asks.length === 0 && (
              <tr>
                <td colSpan={3} style={{ color: "var(--muted)" }}>
                  empty
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function TicksTable({ ticks }: { ticks: TickRow[] }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Time</th>
          <th>Kind</th>
          <th>Bid</th>
          <th>Ask</th>
          <th>Trade</th>
          <th>Size</th>
        </tr>
      </thead>
      <tbody>
        {ticks.map((t, i) => (
          <tr key={i}>
            <td style={{ color: "var(--muted)" }}>
              {new Date(t.event_ts).toLocaleTimeString()}
            </td>
            <td>
              <span className={`badge ${t.kind === "trade" ? "badge-yellow" : ""}`}>{t.kind}</span>
            </td>
            <td style={{ color: "var(--green)" }}>{pct(t.bid)}</td>
            <td style={{ color: "var(--red)" }}>{pct(t.ask)}</td>
            <td>{pct(t.trade_price)}</td>
            <td style={{ color: "var(--muted)" }}>{t.trade_size ?? "—"}</td>
          </tr>
        ))}
        {ticks.length === 0 && (
          <tr>
            <td colSpan={6} className="empty-state">
              No ticks.
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

export default async function MarketDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let market;
  try {
    market = await fetchMarket(id);
  } catch {
    notFound();
  }

  const sigs = market.signals;

  return (
    <div className="panel">
      <div className="panel-header">
        <Link href="/markets" style={{ color: "var(--muted)", fontSize: 12 }}>
          ← Markets
        </Link>
        <span className="panel-title">{market.external_id}</span>
        <span className="badge">{market.venue}</span>
        <span
          className={`badge ${market.resolution_status === "open" ? "badge-green" : ""}`}
        >
          {market.resolution_status}
        </span>
      </div>

      <div style={{ color: "var(--muted)", marginBottom: 20, maxWidth: 600 }}>
        {market.question}
      </div>

      <section style={{ marginBottom: 24 }}>
        <div style={{ color: "var(--muted)", fontSize: 11, marginBottom: 10 }}>SIGNALS</div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
            gap: 12,
          }}
        >
          {(
            [
              ["Mid", sigs.p_mid],
              ["Bid", sigs.p_bid],
              ["Ask", sigs.p_ask],
              ["μPrice", sigs.microprice],
              ["DWIP", sigs.depth_weighted_prob],
              ["Spread", sigs.effective_spread],
              ["OBI", sigs.obi],
              ["Kyle λ", sigs.kyle_lambda],
              ["Amihud", sigs.amihud],
            ] as [string, number | null][]
          ).map(([label, val]) => (
            <div
              key={label}
              style={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: 6,
                padding: "10px 14px",
              }}
            >
              <div style={{ color: "var(--muted)", fontSize: 10, marginBottom: 4 }}>{label}</div>
              <div style={{ fontSize: 16, fontWeight: 600 }}>{pct(val)}</div>
            </div>
          ))}
        </div>
      </section>

      <section style={{ marginBottom: 24 }}>
        <div style={{ color: "var(--muted)", fontSize: 11, marginBottom: 10 }}>ORDER BOOK</div>
        <BookTable levels={market.book} />
      </section>

      <section>
        <div style={{ color: "var(--muted)", fontSize: 11, marginBottom: 10 }}>
          RECENT TICKS (last {market.recent_ticks.length})
        </div>
        <TicksTable ticks={market.recent_ticks} />
      </section>
    </div>
  );
}
