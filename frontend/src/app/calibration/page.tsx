import { fetchCalibration } from "@/lib/api";
import type { ReliabilityBin } from "@/types/api";

export const revalidate = 60;

function ReliabilityChart({ bins }: { bins: ReliabilityBin[] }) {
  const maxCount = Math.max(...bins.map((b) => b.count), 1);

  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-end",
        gap: 4,
        height: 100,
        marginBottom: 8,
        borderBottom: "1px solid var(--border)",
      }}
    >
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
            key={b.bin_center}
            title={`Predicted: ${(b.mean_predicted * 100).toFixed(1)}% | Realized: ${(b.mean_realized * 100).toFixed(1)}% | n=${b.count}`}
            style={{
              flex: 1,
              height: `${height}%`,
              background: color,
              opacity: 0.7,
              minHeight: 2,
              borderRadius: "2px 2px 0 0",
            }}
          />
        );
      })}
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
    if (msg.includes("404")) {
      error = "No resolved markets found for this category.";
    } else {
      error = msg;
    }
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <span className="panel-title">Calibration</span>
        {data && <span className="badge">{data.n_resolved} resolved</span>}
        <span style={{ marginLeft: "auto", color: "var(--muted)", fontSize: 11 }}>
          category: {category}
        </span>
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
                ["Brier Score", data.brier_score.toFixed(4)],
                ["Log Loss", data.log_loss.toFixed(4)],
                ["Resolved", data.n_resolved.toLocaleString()],
              ] as [string, string][]
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
                <div style={{ color: "var(--muted)", fontSize: 10, marginBottom: 4 }}>
                  {label}
                </div>
                <div style={{ fontSize: 18, fontWeight: 600 }}>{val}</div>
              </div>
            ))}
          </div>

          <div style={{ marginBottom: 8, color: "var(--muted)", fontSize: 11 }}>
            RELIABILITY DIAGRAM
          </div>
          <div style={{ maxWidth: 480 }}>
            <ReliabilityChart bins={data.reliability_bins} />
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                color: "var(--muted)",
                fontSize: 10,
              }}
            >
              <span>0¢</span>
              <span>Predicted probability</span>
              <span>100¢</span>
            </div>
          </div>

          <div style={{ marginTop: 24 }}>
            <table style={{ maxWidth: 560 }}>
              <thead>
                <tr>
                  <th>Bin</th>
                  <th>Predicted</th>
                  <th>Realized</th>
                  <th>Delta</th>
                  <th>Count</th>
                </tr>
              </thead>
              <tbody>
                {data.reliability_bins.map((b) => {
                  const delta = b.mean_predicted - b.mean_realized;
                  return (
                    <tr key={b.bin_center}>
                      <td>{(b.bin_center * 100).toFixed(0)}¢</td>
                      <td>{(b.mean_predicted * 100).toFixed(1)}%</td>
                      <td>{(b.mean_realized * 100).toFixed(1)}%</td>
                      <td
                        style={{
                          color:
                            Math.abs(delta) < 0.05
                              ? "var(--green)"
                              : "var(--red)",
                        }}
                      >
                        {delta >= 0 ? "+" : ""}
                        {(delta * 100).toFixed(1)}pp
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
