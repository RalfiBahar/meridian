"use client";

import { useState } from "react";
import { useWs } from "@/lib/ws";
import type { MarketDetail, TickRow, WsEvent } from "@/types/api";

function pct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(2)}¢`;
}

function fmtDelta(v: number | null | undefined): string {
  if (v == null) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(0)}`;
}

function tickCells(t: TickRow): { side: string; price: string; size: string } {
  if (t.kind === "book_delta") {
    return {
      side: t.side ?? "—",
      price: pct(t.book_price),
      size: fmtDelta(t.book_delta),
    };
  }
  if (t.kind === "trade") {
    return { side: "—", price: pct(t.trade_price), size: t.trade_size?.toFixed(0) ?? "—" };
  }
  if (t.kind === "quote") {
    return {
      side: "—",
      price: `${pct(t.bid)} / ${pct(t.ask)}`,
      size: `${t.bid_size?.toFixed(0) ?? "—"} / ${t.ask_size?.toFixed(0) ?? "—"}`,
    };
  }
  return { side: "—", price: "—", size: "—" };
}

const MAX_TICKS = 200;

interface Props {
  initialDetail: MarketDetail;
}

export default function MarketDetailClient({ initialDetail }: Props) {
  const [ticks, setTicks] = useState<TickRow[]>(initialDetail.recent_ticks);
  const [latestEvent, setLatestEvent] = useState<Record<string, unknown> | null>(null);

  const { status: wsStatus } = useWs({
    path: `/api/v1/ws/markets/${initialDetail.id}`,
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
                <th>Side</th>
                <th>Price</th>
                <th>Δ Size</th>
              </tr>
            </thead>
            <tbody>
              {ticks.map((t, i) => {
                const cells = tickCells(t);
                return (
                <tr key={`${t.sequence_no}-${i}`} style={i === 0 ? { background: "rgba(59,130,246,0.08)" } : {}}>
                  <td style={{ color: "var(--muted)" }}>
                    {new Date(t.event_ts).toLocaleTimeString()}
                  </td>
                  <td>
                    <span className={`badge ${t.kind === "trade" ? "badge-yellow" : t.kind === "book_delta" ? "badge-green" : ""}`}>
                      {t.kind}
                    </span>
                  </td>
                  <td style={{ color: "var(--muted)" }}>{cells.side}</td>
                  <td style={{ fontVariantNumeric: "tabular-nums" }}>{cells.price}</td>
                  <td style={{ color: "var(--muted)", fontVariantNumeric: "tabular-nums" }}>{cells.size}</td>
                </tr>
              );})}
              {ticks.length === 0 && (
                <tr>
                  <td colSpan={5} className="empty-state">
                    {wsStatus === "open"
                      ? "No tick activity for this market yet — try KXHIGHNY-26JUN19-B83.5 from the scanner."
                      : "Waiting for ticks…"}
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
