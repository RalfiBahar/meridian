"use client";

import { useState } from "react";
import { useWs } from "@/lib/ws";
import type { MarketDetail, TickRow, WsEvent } from "@/types/api";

function pct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(2)}¢`;
}

const MAX_TICKS = 200;

interface Props {
  initialDetail: MarketDetail;
}

export default function MarketDetailClient({ initialDetail }: Props) {
  const [ticks, setTicks] = useState<TickRow[]>(initialDetail.recent_ticks);
  const [latestEvent, setLatestEvent] = useState<Record<string, unknown> | null>(null);

  const { status: wsStatus } = useWs({
    path: `/ws/markets/${initialDetail.id}`,
    onMessage: (ev: WsEvent) => {
      if (ev.type === "tick") {
        setLatestEvent(ev.data);
        const incoming = ev.data as unknown as TickRow;
        setTicks((prev) => [incoming, ...prev].slice(0, MAX_TICKS));
      }
    },
  });

  // Derive live bid/ask from latest event if available
  const liveBid =
    latestEvent !== null && "bid" in latestEvent
      ? (latestEvent.bid as number | null)
      : initialDetail.signals.p_bid;
  const liveAsk =
    latestEvent !== null && "ask" in latestEvent
      ? (latestEvent.ask as number | null)
      : initialDetail.signals.p_ask;

  return (
    <>
      <div
        style={{
          display: "flex",
          gap: 10,
          alignItems: "center",
          marginBottom: 16,
          fontSize: 11,
          color: "var(--muted)",
        }}
      >
        <span className={`status-dot ${wsStatus}`} />
        <span>{wsStatus === "open" ? "live ticks" : wsStatus}</span>
        {liveBid !== null && (
          <>
            <span>·</span>
            <span style={{ color: "var(--green)" }}>B {pct(liveBid)}</span>
            <span style={{ color: "var(--red)" }}>A {pct(liveAsk)}</span>
          </>
        )}
      </div>

      <div style={{ marginBottom: 24 }}>
        <div style={{ color: "var(--muted)", fontSize: 11, marginBottom: 10 }}>LIVE TICK FEED</div>
        <div style={{ overflowX: "auto", maxHeight: 400, overflowY: "auto" }}>
          <table>
            <thead
              style={{
                position: "sticky",
                top: 0,
                background: "var(--bg)",
                zIndex: 1,
              }}
            >
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
                <tr key={i} style={i === 0 ? { background: "rgba(59,130,246,0.08)" } : {}}>
                  <td style={{ color: "var(--muted)" }}>
                    {new Date(t.event_ts).toLocaleTimeString()}
                  </td>
                  <td>
                    <span className={`badge ${t.kind === "trade" ? "badge-yellow" : ""}`}>
                      {t.kind}
                    </span>
                  </td>
                  <td style={{ color: "var(--green)", fontVariantNumeric: "tabular-nums" }}>
                    {pct(t.bid)}
                  </td>
                  <td style={{ color: "var(--red)", fontVariantNumeric: "tabular-nums" }}>
                    {pct(t.ask)}
                  </td>
                  <td style={{ fontVariantNumeric: "tabular-nums" }}>{pct(t.trade_price)}</td>
                  <td style={{ color: "var(--muted)" }}>{t.trade_size ?? "—"}</td>
                </tr>
              ))}
              {ticks.length === 0 && (
                <tr>
                  <td colSpan={6} className="empty-state">
                    Waiting for ticks…
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
