import { fetchFedWatch } from "@/lib/api";
import type { FedPMF } from "@/types/api";

export const revalidate = 60;

function PmfChart({ pmf, color }: { pmf: FedPMF; color: string }) {
  const maxP = Math.max(...pmf.probabilities);
  return (
    <div>
      <div
        style={{
          display: "flex",
          alignItems: "flex-end",
          gap: 6,
          height: 80,
          marginBottom: 4,
        }}
      >
        {pmf.strikes.map((strike, i) => {
          const p = pmf.probabilities[i];
          const height = p === undefined ? 0 : Math.round((p / maxP) * 100);
          return (
            <div
              key={strike}
              style={{ display: "flex", flexDirection: "column", alignItems: "center", flex: 1 }}
            >
              <div
                title={`${(strike).toFixed(2)}% — ${((p ?? 0) * 100).toFixed(1)}%`}
                style={{
                  width: "100%",
                  height: `${height}%`,
                  background: color,
                  opacity: 0.8,
                  minHeight: 2,
                  borderRadius: "2px 2px 0 0",
                }}
              />
            </div>
          );
        })}
      </div>
      <div
        style={{
          display: "flex",
          gap: 6,
        }}
      >
        {pmf.strikes.map((strike, i) => {
          const p = pmf.probabilities[i] ?? 0;
          return (
            <div
              key={strike}
              style={{
                flex: 1,
                textAlign: "center",
                color: "var(--muted)",
                fontSize: 10,
                overflow: "hidden",
              }}
            >
              <div>{strike.toFixed(2)}%</div>
              <div style={{ color: p >= 0.3 ? color : "var(--muted)" }}>
                {(p * 100).toFixed(0)}¢
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function PmfCard({ pmf, label, color }: { pmf: FedPMF; label: string; color: string }) {
  return (
    <div
      style={{
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: 8,
        padding: 16,
        flex: 1,
        minWidth: 0,
      }}
    >
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 16 }}>
        <span style={{ fontWeight: 600, fontSize: 13 }}>{label}</span>
        <span className="badge">{pmf.source}</span>
        <span style={{ marginLeft: "auto", color: "var(--muted)", fontSize: 11 }}>
          E[r] = {pmf.expected_rate.toFixed(3)}%
        </span>
        <span style={{ color: "var(--muted)", fontSize: 11 }}>
          H = {pmf.entropy_bits.toFixed(2)} bits
        </span>
      </div>
      <PmfChart pmf={pmf} color={color} />
    </div>
  );
}

export default async function FedWatchPage({
  searchParams,
}: {
  searchParams: Promise<{ date?: string }>;
}) {
  const sp = await searchParams;
  const fomcDate = sp.date;

  let data;
  let error: string | null = null;
  try {
    data = await fetchFedWatch({ fomc_date: fomcDate, cme: true });
  } catch (e) {
    const msg = String(e);
    if (msg.includes("404")) {
      error = "No KXFED contracts found. Run `meridian arb group-fed` first.";
    } else {
      error = msg;
    }
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <span className="panel-title">Fed-Rate Distribution</span>
        {data && (
          <span className="badge">{data.kalshi.fomc_date}</span>
        )}
        {data?.cme === null && (
          <span className="badge" style={{ color: "var(--muted)" }}>
            CME unavailable
          </span>
        )}
      </div>

      {error && <div className="error-box">{error}</div>}

      {data && (
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          <PmfCard pmf={data.kalshi} label="Kalshi (KXFED)" color="var(--accent)" />
          {data.cme && (
            <PmfCard pmf={data.cme} label="CME FedWatch" color="var(--orange)" />
          )}
        </div>
      )}

      {data && (
        <div style={{ marginTop: 24 }}>
          <table style={{ maxWidth: 480 }}>
            <thead>
              <tr>
                <th>Rate</th>
                <th>Kalshi</th>
                {data.cme && <th>CME</th>}
                {data.cme && <th>Δ (pp)</th>}
              </tr>
            </thead>
            <tbody>
              {data.kalshi.strikes.map((strike, i) => {
                const kp = data.kalshi.probabilities[i] ?? 0;
                const cp = data.cme?.probabilities[i];
                const delta = cp != null ? kp - cp : null;
                return (
                  <tr key={strike}>
                    <td style={{ fontWeight: 600 }}>{strike.toFixed(3)}%</td>
                    <td style={{ color: "var(--accent)" }}>{(kp * 100).toFixed(1)}¢</td>
                    {data.cme && (
                      <td style={{ color: "var(--orange)" }}>
                        {cp != null ? `${(cp * 100).toFixed(1)}¢` : "—"}
                      </td>
                    )}
                    {data.cme && (
                      <td
                        style={{
                          color:
                            delta == null
                              ? "var(--muted)"
                              : Math.abs(delta) < 0.05
                                ? "var(--muted)"
                                : delta > 0
                                  ? "var(--green)"
                                  : "var(--red)",
                        }}
                      >
                        {delta != null
                          ? `${delta >= 0 ? "+" : ""}${(delta * 100).toFixed(1)}`
                          : "—"}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
