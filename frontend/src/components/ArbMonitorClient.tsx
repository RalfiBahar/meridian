"use client";

import { useState } from "react";
import { useWs } from "@/lib/ws";
import type { ArbViolationsResponse, CrossVenueDivergence, PartitionViolation, WsEvent } from "@/types/api";

function parseCheckedAt(value?: string): Date | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

function formatBps(bps: number): string {
  return `${bps.toLocaleString(undefined, { maximumFractionDigits: 1 })} bps`;
}

function severityClass(bps: number): string {
  return bps >= 50 ? "badge-red" : bps >= 20 ? "badge-yellow" : "badge";
}

function PartitionViolationRow({ v }: { v: PartitionViolation }) {
  return (
    <tr>
      <td>
        <span
          className={`badge ${severityClass(v.violation_bps)}`}
          style={{ fontVariantNumeric: "tabular-nums" }}
        >
          {formatBps(v.violation_bps)}
        </span>
      </td>
      <td>{v.group_label}</td>
      <td>
        <span className={`badge ${v.depth_feasible ? "badge-red" : ""}`}>
          {v.depth_feasible ? "feasible" : "thin"}
        </span>
      </td>
      <td style={{ color: "var(--muted)", fontSize: 11 }}>
        {v.direction} · {v.n_contracts} contracts · ask {v.min_ask_sum.toFixed(2)} / bid{" "}
        {v.max_bid_sum.toFixed(2)}
      </td>
    </tr>
  );
}

function CrossVenueRow({ v }: { v: CrossVenueDivergence }) {
  return (
    <tr>
      <td>
        <span
          className={`badge ${severityClass(v.divergence_bps)}`}
          style={{ fontVariantNumeric: "tabular-nums" }}
        >
          {formatBps(v.divergence_bps)}
        </span>
      </td>
      <td>
        {v.venue_a} ↔ {v.venue_b}
      </td>
      <td style={{ color: "var(--muted)" }}>—</td>
      <td style={{ color: "var(--muted)", fontSize: 11 }}>
        {(v.p_mid_a * 100).toFixed(1)}% vs {(v.p_mid_b * 100).toFixed(1)}%
      </td>
    </tr>
  );
}

interface Props {
  initialData: ArbViolationsResponse | null;
}

export default function ArbMonitorClient({ initialData }: Props) {
  const [data, setData] = useState<ArbViolationsResponse | null>(initialData);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(
    parseCheckedAt(initialData?.checked_at),
  );

  const { status: wsStatus } = useWs({
    path: "/api/v1/ws/arb",
    onMessage: (ev: WsEvent) => {
      if (ev.type === "arb_snapshot") {
        setData(ev.data);
        setLastUpdate(parseCheckedAt(ev.data.checked_at) ?? new Date());
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
                <PartitionViolationRow key={v.group_id} v={v} />
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
                <CrossVenueRow key={v.group_id} v={v} />
              ))}
            </tbody>
          </table>
        </section>
      )}
    </>
  );
}
