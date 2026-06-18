import Link from "next/link";
import { fetchMarkets } from "@/lib/api";
import type { MarketSummary } from "@/types/api";

export const revalidate = 10;

function pct(v: number | null): string {
  if (v === null) return "—";
  return `${(v * 100).toFixed(1)}¢`;
}

function spread(bid: number | null, ask: number | null): string {
  if (bid === null || ask === null) return "—";
  return `${((ask - bid) * 100).toFixed(1)}¢`;
}

function MarketRow({ m }: { m: MarketSummary }) {
  const midColor =
    m.p_mid === null
      ? "var(--muted)"
      : m.p_mid >= 0.6
        ? "var(--green)"
        : m.p_mid <= 0.4
          ? "var(--red)"
          : "var(--text)";

  return (
    <tr>
      <td>
        <Link href={`/markets/${m.id}`} style={{ color: "var(--text)" }}>
          {m.external_id}
        </Link>
      </td>
      <td style={{ color: "var(--muted)" }}>{m.venue}</td>
      <td
        style={{
          maxWidth: 380,
          overflow: "hidden",
          textOverflow: "ellipsis",
          color: "var(--muted)",
        }}
      >
        {m.question}
      </td>
      <td style={{ color: midColor }}>{pct(m.p_mid)}</td>
      <td>{pct(m.p_bid)}</td>
      <td>{pct(m.p_ask)}</td>
      <td style={{ color: "var(--muted)" }}>{spread(m.p_bid, m.p_ask)}</td>
      <td style={{ color: "var(--muted)" }}>{pct(m.microprice)}</td>
      <td style={{ color: "var(--muted)" }}>
        {m.closes_at ? new Date(m.closes_at).toLocaleDateString() : "—"}
      </td>
    </tr>
  );
}

export default async function MarketsPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; category?: string; limit?: string; offset?: string }>;
}) {
  const sp = await searchParams;
  const status = sp.status ?? "open";
  const category = sp.category;
  const limit = Number(sp.limit ?? 50);
  const offset = Number(sp.offset ?? 0);

  let data;
  let error: string | null = null;
  try {
    data = await fetchMarkets({ status, category, limit, offset });
  } catch (e) {
    error = String(e);
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <span className="panel-title">Market Scanner</span>
        {data && (
          <span className="badge">
            {data.total.toLocaleString()} markets
          </span>
        )}
        <span style={{ marginLeft: "auto", color: "var(--muted)", fontSize: 11 }}>
          status: {status}
        </span>
      </div>

      {error && <div className="error-box">{error}</div>}

      {data && data.markets.length === 0 && (
        <div className="empty-state">No markets found.</div>
      )}

      {data && data.markets.length > 0 && (
        <>
          <div style={{ overflowX: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Venue</th>
                  <th>Question</th>
                  <th>Mid</th>
                  <th>Bid</th>
                  <th>Ask</th>
                  <th>Spread</th>
                  <th>μPrice</th>
                  <th>Closes</th>
                </tr>
              </thead>
              <tbody>
                {data.markets.map((m) => (
                  <MarketRow key={m.id} m={m} />
                ))}
              </tbody>
            </table>
          </div>

          <div
            style={{
              display: "flex",
              gap: 12,
              marginTop: 16,
              color: "var(--muted)",
              fontSize: 12,
            }}
          >
            {offset > 0 && (
              <Link href={`/markets?status=${status}&offset=${Math.max(0, offset - limit)}`}>
                ← prev
              </Link>
            )}
            {offset + limit < data.total && (
              <Link href={`/markets?status=${status}&offset=${offset + limit}`}>next →</Link>
            )}
          </div>
        </>
      )}
    </div>
  );
}
