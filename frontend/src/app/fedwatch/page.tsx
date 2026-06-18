import Link from "next/link";
import { fetchFedWatch } from "@/lib/api";
import type { FedPMF } from "@/types/api";

export const revalidate = 300;

// Approximate next-3-FOMC calendar (hardcoded for demo; real impl would query markets)
const UPCOMING_FOMC = [
  { label: "Jul 2026", date: "2026-07-30" },
  { label: "Sep 2026", date: "2026-09-17" },
  { label: "Nov 2026", date: "2026-11-05" },
];

function PmfBar({ pmf, color }: { pmf: FedPMF; color: string }) {
  const maxP = Math.max(...pmf.probabilities, 0.01);
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 4, height: 100 }}>
      {pmf.strikes.map((strike, i) => {
        const p = pmf.probabilities[i] ?? 0;
        const h = Math.round((p / maxP) * 100);
        return (
          <div
            key={strike}
            style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center" }}
          >
            <div
              title={`${strike.toFixed(3)}% — ${(p * 100).toFixed(1)}¢`}
              style={{
                width: "100%",
                height: `${h}%`,
                background: color,
                opacity: p > 0.3 ? 0.9 : 0.4,
                minHeight: p > 0 ? 2 : 0,
                borderRadius: "2px 2px 0 0",
              }}
            />
          </div>
        );
      })}
    </div>
  );
}

function PmfLabels({ pmf }: { pmf: FedPMF }) {
  return (
    <div style={{ display: "flex", gap: 4, marginTop: 4 }}>
      {pmf.strikes.map((strike, i) => {
        const p = pmf.probabilities[i] ?? 0;
        return (
          <div
            key={strike}
            style={{ flex: 1, textAlign: "center", fontSize: 9, color: "var(--muted)", overflow: "hidden" }}
          >
            <div>{strike.toFixed(2)}</div>
            <div>{(p * 100).toFixed(0)}¢</div>
          </div>
        );
      })}
    </div>
  );
}

interface MeetingCardProps {
  label: string;
  date: string;
  isActive: boolean;
}

async function MeetingCard({ label, date, isActive }: MeetingCardProps) {
  let data;
  try {
    data = await fetchFedWatch({ fomc_date: date, cme: true });
  } catch {
    return (
      <div
        style={{
          flex: 1,
          background: "var(--surface)",
          border: `1px solid ${isActive ? "var(--accent)" : "var(--border)"}`,
          borderRadius: 8,
          padding: 16,
          minWidth: 0,
        }}
      >
        <div style={{ fontWeight: 600, marginBottom: 4 }}>{label}</div>
        <div style={{ color: "var(--muted)", fontSize: 11 }}>{date}</div>
        <div style={{ color: "var(--muted)", fontSize: 11, marginTop: 8 }}>No data</div>
      </div>
    );
  }

  const kalshi = data.kalshi;
  const cme = data.cme;

  return (
    <div
      style={{
        flex: 1,
        background: "var(--surface)",
        border: `1px solid ${isActive ? "var(--accent)" : "var(--border)"}`,
        borderRadius: 8,
        padding: 16,
        minWidth: 0,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <span style={{ fontWeight: 600 }}>{label}</span>
        <span style={{ color: "var(--muted)", fontSize: 11 }}>{date}</span>
        <span style={{ marginLeft: "auto", color: "var(--muted)", fontSize: 11 }}>
          E[r] = {kalshi.expected_rate.toFixed(3)}%
        </span>
      </div>

      <div style={{ marginBottom: 4 }}>
        <div style={{ fontSize: 10, color: "var(--accent)", marginBottom: 2 }}>KALSHI</div>
        <PmfBar pmf={kalshi} color="var(--accent)" />
      </div>

      {cme && (
        <div style={{ marginBottom: 4 }}>
          <div style={{ fontSize: 10, color: "var(--orange)", marginBottom: 2 }}>CME</div>
          <PmfBar pmf={cme} color="var(--orange)" />
        </div>
      )}
      <PmfLabels pmf={kalshi} />

      <div
        style={{
          display: "flex",
          gap: 12,
          marginTop: 12,
          fontSize: 11,
          color: "var(--muted)",
        }}
      >
        <span>H = {kalshi.entropy_bits.toFixed(2)} bits</span>
        {cme && (
          <span>
            Δ CME = {((kalshi.expected_rate - cme.expected_rate) * 100).toFixed(1)} bps
          </span>
        )}
      </div>
    </div>
  );
}

export default async function FedWatchPage({
  searchParams,
}: {
  searchParams: Promise<{ date?: string }>;
}) {
  const sp = await searchParams;
  const activeDate = sp.date ?? UPCOMING_FOMC[0]?.date;

  return (
    <div className="panel">
      <div className="panel-header">
        <span className="panel-title">Fed-Rate Distribution</span>
        <div style={{ display: "flex", gap: 4 }}>
          {UPCOMING_FOMC.map(({ label, date }) => (
            <Link
              key={date}
              href={`/fedwatch?date=${date}`}
              style={{
                padding: "2px 8px",
                borderRadius: 4,
                fontSize: 11,
                background: date === activeDate ? "var(--accent)" : "var(--surface-2)",
                color: date === activeDate ? "white" : "var(--muted)",
              }}
            >
              {label}
            </Link>
          ))}
        </div>
      </div>

      <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        {UPCOMING_FOMC.map(({ label, date }) => (
          <MeetingCard key={date} label={label} date={date} isActive={date === activeDate} />
        ))}
      </div>

      {activeDate && (
        <ActiveDetail date={activeDate} />
      )}
    </div>
  );
}

async function ActiveDetail({ date }: { date: string }) {
  let data;
  try {
    data = await fetchFedWatch({ fomc_date: date, cme: true });
  } catch {
    return null;
  }

  return (
    <div style={{ marginTop: 32 }}>
      <div style={{ color: "var(--muted)", fontSize: 11, marginBottom: 12 }}>
        STRIKE TABLE — {date}
      </div>
      <table style={{ maxWidth: 500 }}>
        <thead>
          <tr>
            <th>Rate</th>
            <th style={{ color: "var(--accent)" }}>Kalshi</th>
            {data.cme && <th style={{ color: "var(--orange)" }}>CME</th>}
            {data.cme && <th>Δ (bps)</th>}
          </tr>
        </thead>
        <tbody>
          {data.kalshi.strikes.map((strike, i) => {
            const kp = data.kalshi.probabilities[i] ?? 0;
            const cp = data.cme?.probabilities[i];
            const delta = cp != null ? (kp - cp) * 100 : null;
            return (
              <tr key={strike}>
                <td style={{ fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
                  {strike.toFixed(3)}%
                </td>
                <td style={{ color: "var(--accent)", fontVariantNumeric: "tabular-nums" }}>
                  {(kp * 100).toFixed(1)}¢
                </td>
                {data.cme && (
                  <td style={{ color: "var(--orange)", fontVariantNumeric: "tabular-nums" }}>
                    {cp != null ? `${(cp * 100).toFixed(1)}¢` : "—"}
                  </td>
                )}
                {data.cme && (
                  <td
                    style={{
                      fontVariantNumeric: "tabular-nums",
                      color:
                        delta == null
                          ? "var(--muted)"
                          : Math.abs(delta) < 5
                            ? "var(--muted)"
                            : delta > 0
                              ? "var(--green)"
                              : "var(--red)",
                    }}
                  >
                    {delta != null ? `${delta >= 0 ? "+" : ""}${delta.toFixed(1)}` : "—"}
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
