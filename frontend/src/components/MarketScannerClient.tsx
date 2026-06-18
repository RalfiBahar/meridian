"use client";

import { useState } from "react";
import Link from "next/link";
import { useWs } from "@/lib/ws";
import type { MarketSummary, WsEvent } from "@/types/api";

function pct(v: number | null | undefined): string {
  if (v == null) return "—";
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
      <td style={{ color: midColor, fontVariantNumeric: "tabular-nums" }}>{pct(m.p_mid)}</td>
      <td style={{ fontVariantNumeric: "tabular-nums" }}>{pct(m.p_bid)}</td>
      <td style={{ fontVariantNumeric: "tabular-nums" }}>{pct(m.p_ask)}</td>
      <td style={{ color: "var(--muted)", fontVariantNumeric: "tabular-nums" }}>
        {spread(m.p_bid, m.p_ask)}
      </td>
      <td style={{ color: "var(--muted)", fontVariantNumeric: "tabular-nums" }}>
        {pct(m.microprice)}
      </td>
      <td style={{ color: "var(--muted)" }}>
        {m.closes_at ? new Date(m.closes_at).toLocaleDateString() : "—"}
      </td>
    </tr>
  );
}

interface Props {
  initialMarkets: MarketSummary[];
  initialTotal: number;
  status: string;
  category?: string;
}

export default function MarketScannerClient({ initialMarkets, initialTotal, status, category }: Props) {
  const [markets, setMarkets] = useState<MarketSummary[]>(initialMarkets);
  const [total, setTotal] = useState(initialTotal);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);

  const wsPath = category
    ? `/ws/markets?status=${status}&category=${encodeURIComponent(category)}`
    : `/ws/markets?status=${status}`;

  const { status: wsStatus } = useWs({
    path: wsPath,
    onMessage: (ev: WsEvent) => {
      if (ev.type === "snapshot") {
        setMarkets(ev.markets);
        setTotal(ev.total);
        setLastUpdate(new Date());
      }
    },
  });

  return (
    <>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          marginBottom: 12,
          color: "var(--muted)",
          fontSize: 11,
        }}
      >
        <span className={`status-dot ${wsStatus}`} />
        <span>{wsStatus === "open" ? "live" : wsStatus}</span>
        {lastUpdate && <span>· updated {lastUpdate.toLocaleTimeString()}</span>}
        <span style={{ marginLeft: "auto" }}>{total.toLocaleString()} markets</span>
      </div>

      {markets.length === 0 ? (
        <div className="empty-state">No markets found.</div>
      ) : (
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
              {markets.map((m) => (
                <MarketRow key={m.id} m={m} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
