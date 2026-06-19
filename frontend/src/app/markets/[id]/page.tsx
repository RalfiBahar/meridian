import { notFound } from "next/navigation";
import Link from "next/link";
import { fetchMarket } from "@/lib/api";
import MarketDetailClient from "@/components/MarketDetailClient";
import type { BookLevel } from "@/types/api";

export const revalidate = 0;

function pct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(2)}¢`;
}

function BookTable({ levels }: { levels: BookLevel[] }) {
  const bids = levels
    .filter((l) => l.side === "bid" || l.side === "yes")
    .sort((a, b) => b.price - a.price);
  const asks = levels
    .filter((l) => l.side === "ask" || l.side === "no")
    .sort((a, b) => a.price - b.price);

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
      {[
        { label: "BIDS", rows: bids, color: "var(--green)" },
        { label: "ASKS", rows: asks, color: "var(--red)" },
      ].map(({ label, rows, color }) => (
        <div key={label}>
          <div style={{ color: "var(--muted)", fontSize: 11, marginBottom: 6 }}>{label}</div>
          <table>
            <thead>
              <tr>
                <th>Lvl</th>
                <th>Price</th>
                <th>Size</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={`${label}-${r.side}-${r.level}-${r.price}-${i}`}>
                  <td>{r.level}</td>
                  <td style={{ color, fontVariantNumeric: "tabular-nums" }}>{pct(r.price)}</td>
                  <td style={{ fontVariantNumeric: "tabular-nums" }}>{r.size}</td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={3} style={{ color: "var(--muted)" }}>
                    empty
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      ))}
    </div>
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
        {market.closes_at && (
          <span style={{ marginLeft: "auto", color: "var(--muted)", fontSize: 11 }}>
            closes {new Date(market.closes_at).toLocaleDateString()}
          </span>
        )}
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
              <div style={{ fontSize: 16, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
                {pct(val)}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section style={{ marginBottom: 24 }}>
        <div style={{ color: "var(--muted)", fontSize: 11, marginBottom: 10 }}>
          ORDER BOOK (snapshot)
        </div>
        <BookTable levels={market.book} />
      </section>

      <section>
        <MarketDetailClient initialDetail={market} />
      </section>
    </div>
  );
}
