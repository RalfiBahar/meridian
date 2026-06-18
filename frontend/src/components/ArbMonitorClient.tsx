"use client";

import { useState } from "react";
import { useWs } from "@/lib/ws";
import type { ArbViolationsResponse, CrossVenueDivergence, PartitionViolation, WsEvent } from "@/types/api";

function ViolationRow({ v }: { v: PartitionViolation | CrossVenueDivergence }) {
  const bps = "excess_bps" in v ? v.excess_bps : v.divergence_bps;
  const sevClass = bps >= 50 ? "badge-red" : bps >= 20 ? "badge-yellow" : "badge";
  return (
    <tr>
      <td>
        <span className={`badge ${sevClass}`} style={{ fontVariantNumeric: "tabular-nums" }}>
          {bps.toFixed(1)} bps
        </span>
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

interface Props {
  initialData: ArbViolationsResponse | null;
}

export default function ArbMonitorClient({ initialData }: Props) {
  const [data, setData] = useState<ArbViolationsResponse | null>(initialData);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(
    initialData ? new Date(initialData.checked_at) : null,
  );

  const { status: wsStatus } = useWs({
    path: "/ws/arb",
    onMessage: (ev: WsEvent) => {
      if (ev.type === "arb_snapshot") {
        setData(ev.data);
        setLastUpdate(new Date(ev.data.checked_at));
      }
    },
  });

  const total = data
    ? data.partition_violations.length + data.cross_venue_divergences.length
    : 0;

  return (
    <>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          marginBottom: 12,
          fontSize: 11,
          color: "var(--muted)",
        }}
      >
        <span className={`status-dot ${wsStatus}`} />
        <span>{wsStatus === "open" ? "live" : wsStatus}</span>
        {lastUpdate && <span>· checked {lastUpdate.toLocaleTimeString()}</span>}
        <span className={`badge ${total > 0 ? "badge-yellow" : ""}`} style={{ marginLeft: "auto" }}>
          {total} violations
        </span>
      </div>

      {!data && <div className="empty-state">Waiting for data…</div>}

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
    </>
  );
}
