import { fetchArbViolations } from "@/lib/api";
import type { CrossVenueDivergence, PartitionViolation } from "@/types/api";

export const revalidate = 30;

function ViolationRow({ v }: { v: PartitionViolation | CrossVenueDivergence }) {
  const bps = "excess_bps" in v ? v.excess_bps : v.divergence_bps;
  const sevClass = bps >= 50 ? "badge-red" : bps >= 20 ? "badge-yellow" : "badge";
  return (
    <tr>
      <td>
        <span className={`badge ${sevClass}`}>{bps.toFixed(1)} bps</span>
      </td>
      <td>{v.group_label}</td>
      <td>
        <span className={`badge ${v.depth_feasible ? "badge-red" : ""}`}>
          {v.depth_feasible ? "feasible" : "thin"}
        </span>
      </td>
      <td style={{ color: "var(--muted)", fontSize: 11 }}>{v.summary}</td>
    </tr>
  );
}

export default async function ArbPage() {
  let data;
  let error: string | null = null;
  try {
    data = await fetchArbViolations();
  } catch (e) {
    error = String(e);
  }

  const total =
    data ? data.partition_violations.length + data.cross_venue_divergences.length : 0;

  return (
    <div className="panel">
      <div className="panel-header">
        <span className="panel-title">Arb Monitor</span>
        {data && (
          <span className={`badge ${total > 0 ? "badge-yellow" : ""}`}>
            {total} violations
          </span>
        )}
        {data && (
          <span style={{ marginLeft: "auto", color: "var(--muted)", fontSize: 11 }}>
            checked {new Date(data.checked_at).toLocaleTimeString()}
          </span>
        )}
      </div>

      {error && <div className="error-box">{error}</div>}

      {data && total === 0 && (
        <div className="empty-state">No arbitrage violations detected.</div>
      )}

      {data && data.partition_violations.length > 0 && (
        <section style={{ marginBottom: 24 }}>
          <div style={{ color: "var(--muted)", fontSize: 11, marginBottom: 10 }}>
            PARTITION VIOLATIONS ({data.partition_violations.length})
          </div>
          <table>
            <thead>
              <tr>
                <th>Excess</th>
                <th>Group</th>
                <th>Depth</th>
                <th>Summary</th>
              </tr>
            </thead>
            <tbody>
              {data.partition_violations.map((v) => (
                <ViolationRow key={v.market_group_id} v={v} />
              ))}
            </tbody>
          </table>
        </section>
      )}

      {data && data.cross_venue_divergences.length > 0 && (
        <section>
          <div style={{ color: "var(--muted)", fontSize: 11, marginBottom: 10 }}>
            CROSS-VENUE DIVERGENCES ({data.cross_venue_divergences.length})
          </div>
          <table>
            <thead>
              <tr>
                <th>Divergence</th>
                <th>Group</th>
                <th>Depth</th>
                <th>Summary</th>
              </tr>
            </thead>
            <tbody>
              {data.cross_venue_divergences.map((v) => (
                <ViolationRow key={v.market_group_id} v={v} />
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
