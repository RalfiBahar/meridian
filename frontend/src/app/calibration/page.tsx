import Link from "next/link";
import { fetchCalibration } from "@/lib/api";
import type { ReliabilityBin } from "@/types/api";

export const revalidate = 300;

const CATEGORIES = ["fed", "econ", "politics", "crypto", "sports"];

function ReliabilityChart({ bins }: { bins: ReliabilityBin[] }) {
  const maxCount = Math.max(...bins.map((b) => b.count), 1);

  return (
    <div>
      <div
        style={{
          display: "flex",
          alignItems: "flex-end",
          gap: 3,
          height: 120,
          padding: "0 0 2px",
          borderBottom: "1px solid var(--border)",
          position: "relative",
        }}
      >
        {/* Perfect calibration diagonal reference */}
        <div
          style={{
            position: "absolute",
            inset: 0,
            backgroundImage:
              "linear-gradient(to top right, transparent calc(50% - 0.5px), var(--border) calc(50%), transparent calc(50% + 0.5px))",
            pointerEvents: "none",
          }}
        />
        {bins.map((b) => {
          const height = Math.round((b.count / maxCount) * 100);
          const diff = b.mean_predicted - b.mean_realized;
          const color =
            Math.abs(diff) < 0.05
              ? "var(--green)"
              : Math.abs(diff) < 0.1
                ? "var(--yellow)"
                : "var(--red)";
          return (
            <div
              key={`${b.lower}-${b.upper}`}
              title={`Predicted: ${(b.mean_predicted * 100).toFixed(1)}%\nRealized: ${(b.mean_realized * 100).toFixed(1)}%\nn = ${b.count}`}
              style={{
                flex: 1,
                height: `${height}%`,
                background: color,
                opacity: 0.75,
                minHeight: 2,
                borderRadius: "2px 2px 0 0",
                position: "relative",
                zIndex: 1,
              }}
            />
          );
        })}
      </div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          color: "var(--muted)",
          fontSize: 10,
          marginTop: 4,
        }}
      >
        <span>0%</span>
        <span>Predicted probability (bucket height = # resolved)</span>
        <span>100%</span>
      </div>
    </div>
  );
}

export default async function CalibrationPage({
  searchParams,
}: {
  searchParams: Promise<{ category?: string }>;
}) {
  const sp = await searchParams;
  const category = sp.category ?? "fed";

  let data;
  let error: string | null = null;
  try {
    data = await fetchCalibration({ category });
  } catch (e) {
    const msg = String(e);
    error = msg.includes("404")
      ? "No resolved markets found for this category."
      : msg;
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <span className="panel-title">Calibration</span>
        <div style={{ display: "flex", gap: 4 }}>
          {CATEGORIES.map((c) => (
            <Link
              key={c}
              href={`/calibration?category=${c}`}
              style={{
                padding: "2px 8px",
                borderRadius: 4,
                fontSize: 11,
                background: c === category ? "var(--accent)" : "var(--surface-2)",
                color: c === category ? "white" : "var(--muted)",
              }}
            >
              {c}
            </Link>
          ))}
        </div>
        {data && (
          <span className="badge" style={{ marginLeft: "auto" }}>
            {data.n_markets} resolved
          </span>
        )}
      </div>

      {error && <div className="error-box">{error}</div>}

      {data && (
        <>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(3, 1fr)",
              gap: 12,
              marginBottom: 24,
              maxWidth: 420,
            }}
          >
            {(
              [
                ["Brier Score", data.brier_score.toFixed(4), data.brier_score < 0.1 ? "var(--green)" : "var(--red)"],
                ["Log Loss", data.log_loss.toFixed(4), data.log_loss < 0.3 ? "var(--green)" : "var(--red)"],
                ["Resolved", data.n_markets.toLocaleString(), "var(--text)"],
              ] as [string, string, string][]
            ).map(([label, val, color]) => (
              <div
                key={label}
                style={{
                  background: "var(--surface)",
                  border: "1px solid var(--border)",
                  borderRadius: 6,
                  padding: "10px 14px",
                }}
              >
                <div style={{ color: "var(--muted)", fontSize: 10, marginBottom: 4 }}>
                  {label}
                </div>
                <div style={{ fontSize: 18, fontWeight: 600, color }}>{val}</div>
              </div>
            ))}
          </div>

          <div style={{ maxWidth: 520, marginBottom: 24 }}>
            <div style={{ color: "var(--muted)", fontSize: 11, marginBottom: 10 }}>
              RELIABILITY DIAGRAM
            </div>
            <ReliabilityChart bins={data.reliability_bins} />
          </div>

          {data.ece != null && (
            <div
              style={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: 6,
                padding: "10px 14px",
                maxWidth: 200,
                marginBottom: 24,
              }}
            >
              <div style={{ color: "var(--muted)", fontSize: 10, marginBottom: 4 }}>
                ECE (10-bin)
              </div>
              <div
                style={{
                  fontSize: 18,
                  fontWeight: 600,
                  color: data.ece < 0.05 ? "var(--green)" : data.ece < 0.1 ? "var(--yellow)" : "var(--red)",
                }}
              >
                {data.ece.toFixed(4)}
              </div>
              <div style={{ color: "var(--muted)", fontSize: 10, marginTop: 2 }}>
                expected calibration error
              </div>
            </div>
          )}

          <div style={{ marginBottom: 24 }}>
            <div style={{ color: "var(--muted)", fontSize: 11, marginBottom: 8 }}>
              ROLLING CALIBRATION DRIFT (30-day window)
            </div>
            <div
              style={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: 6,
                padding: "10px 14px",
                fontSize: 11,
                color: "var(--muted)",
              }}
            >
              Rolling Brier/ECE drift tracking requires ≥30 days of settled market history.
              Computed via <code>GET /api/v1/calibration/summary?lookback=30</code>.
              Once settled-market backfill is complete the chart will render here.
            </div>
          </div>

          <div>
            <div style={{ color: "var(--muted)", fontSize: 11, marginBottom: 10 }}>
              BIN TABLE
            </div>
            <table style={{ maxWidth: 480 }}>
              <thead>
                <tr>
                  <th>Bin</th>
                  <th>Predicted</th>
                  <th>Realized</th>
                  <th>Δ (pp)</th>
                  <th>n</th>
                </tr>
              </thead>
              <tbody>
                {data.reliability_bins.map((b) => {
                  const delta = b.mean_predicted - b.mean_realized;
                  return (
                    <tr key={`${b.lower}-${b.upper}`}>
                      <td>
                        {(b.lower * 100).toFixed(0)}–{(b.upper * 100).toFixed(0)}%
                      </td>
                      <td style={{ fontVariantNumeric: "tabular-nums" }}>
                        {(b.mean_predicted * 100).toFixed(1)}%
                      </td>
                      <td style={{ fontVariantNumeric: "tabular-nums" }}>
                        {(b.mean_realized * 100).toFixed(1)}%
                      </td>
                      <td
                        style={{
                          fontVariantNumeric: "tabular-nums",
                          color: Math.abs(delta) < 0.05 ? "var(--green)" : "var(--red)",
                        }}
                      >
                        {delta >= 0 ? "+" : ""}
                        {(delta * 100).toFixed(1)}
                      </td>
                      <td style={{ color: "var(--muted)" }}>{b.count}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
