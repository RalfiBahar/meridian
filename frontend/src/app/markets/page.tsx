import Link from "next/link";
import { fetchMarkets } from "@/lib/api";
import MarketScannerClient from "@/components/MarketScannerClient";

export const revalidate = 0;

const STATUSES = ["open", "closed", "settled"];

export default async function MarketsPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; category?: string; limit?: string; offset?: string }>;
}) {
  const sp = await searchParams;
  const status = sp.status ?? "open";
  const category = sp.category;
  const limit = Number(sp.limit ?? 100);
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
        <div style={{ display: "flex", gap: 4 }}>
          {STATUSES.map((s) => (
            <Link
              key={s}
              href={`/markets?status=${s}`}
              style={{
                padding: "2px 8px",
                borderRadius: 4,
                fontSize: 11,
                background: s === status ? "var(--accent)" : "var(--surface-2)",
                color: s === status ? "white" : "var(--muted)",
              }}
            >
              {s}
            </Link>
          ))}
        </div>
        {category && (
          <span className="badge">
            {category}{" "}
            <Link href={`/markets?status=${status}`} style={{ color: "var(--muted)" }}>
              ×
            </Link>
          </span>
        )}
      </div>

      {error && <div className="error-box">{error}</div>}

      {data && (
        <MarketScannerClient
          initialMarkets={data.markets}
          initialTotal={data.total}
          status={status}
          category={category}
        />
      )}
    </div>
  );
}
