"use client";

import Link from "next/link";

const FEATURES = [
  {
    href: "/markets",
    title: "Market Scanner",
    stat: "Live L2",
    description:
      "Browse Kalshi & Polymarket contracts with implied mid, microprice, spread, and depth. Drill into tick history and order-book snapshots.",
    methods: ["p_mid", "microprice", "depth-weighted prob"],
  },
  {
    href: "/calibration",
    title: "Calibration",
    stat: "Brier · Murphy",
    description:
      "Reliability diagrams and Brier/Murphy decomposition on resolved markets. Isotonic recalibration and rolling drift (Phase 11).",
    methods: ["Brier score", "log loss", "ECE", "isotonic regression"],
  },
  {
    href: "/arb",
    title: "Arb Monitor",
    stat: "LP checks",
    description:
      "No-arbitrage violations across Fed-rate partitions and cross-venue links. Severity in bps after fees; aggregate stats (Phase 11).",
    methods: ["scipy/cvxpy LP", "depth feasibility", "cross-venue divergence"],
  },
  {
    href: "/fedwatch",
    title: "FedWatch",
    stat: "Implied PMF",
    description:
      "Implied rate distribution from Kalshi strike strips vs CME FedWatch. Event-response latency around FOMC/CPI.",
    methods: ["cumulative strip PMF", "news event windows"],
  },
] as const;

const PIPELINE = [
  "WebSocket ingest",
  "Canonical events",
  "TimescaleDB",
  "Signal pipeline",
  "FastAPI",
  "Quant Terminal",
];

type HomeStats = {
  markets?: number;
  healthy?: boolean;
};

export default function HomeClient({ stats }: { stats: HomeStats }) {
  return (
    <div style={{ maxWidth: 960, margin: "0 auto", padding: "32px 20px 48px" }}>
      <section style={{ marginBottom: 40 }}>
        <p
          style={{
            fontSize: 11,
            letterSpacing: "0.12em",
            color: "var(--muted)",
            marginBottom: 8,
          }}
        >
          PREDICTION-MARKET RESEARCH ENGINE
        </p>
        <h1
          style={{
            fontSize: 28,
            fontWeight: 700,
            lineHeight: 1.2,
            marginBottom: 12,
            letterSpacing: "-0.02em",
          }}
        >
          Meridian Quant Terminal
        </h1>
        <p style={{ color: "var(--muted)", maxWidth: 640, lineHeight: 1.7, marginBottom: 20 }}>
          Read-only analytics platform for prediction markets. Ingests live order books,
          scores calibration against realized outcomes, detects no-arb violations, and
          surfaces microstructure & Fed-rate signals — with walk-forward backtests and
          full experiment provenance.
        </p>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
          <Link
            href="/markets"
            style={{
              padding: "8px 16px",
              background: "var(--accent)",
              color: "white",
              borderRadius: 6,
              fontWeight: 600,
              fontSize: 12,
            }}
          >
            Open terminal →
          </Link>
          <a
            href="https://github.com/RalfiBahar/meridian"
            target="_blank"
            rel="noopener noreferrer"
            style={{ fontSize: 12, color: "var(--muted)" }}
          >
            GitHub
          </a>
          {stats.healthy !== undefined && (
            <span
              style={{
                fontSize: 11,
                color: stats.healthy ? "var(--green)" : "var(--orange)",
              }}
            >
              API {stats.healthy ? "healthy" : "offline"}
            </span>
          )}
          {stats.markets !== undefined && (
            <span style={{ fontSize: 11, color: "var(--muted)" }}>
              {stats.markets.toLocaleString()} markets indexed
            </span>
          )}
        </div>
      </section>

      <section style={{ marginBottom: 36 }}>
        <h2
          style={{
            fontSize: 11,
            letterSpacing: "0.1em",
            color: "var(--muted)",
            marginBottom: 12,
          }}
        >
          PIPELINE
        </h2>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          {PIPELINE.map((step, i) => (
            <span key={step} style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span
                style={{
                  padding: "4px 10px",
                  background: "var(--surface-2)",
                  borderRadius: 4,
                  fontSize: 11,
                  border: "1px solid var(--border)",
                }}
              >
                {step}
              </span>
              {i < PIPELINE.length - 1 && (
                <span style={{ color: "var(--border)", fontSize: 10 }}>→</span>
              )}
            </span>
          ))}
        </div>
      </section>

      <section>
        <h2
          style={{
            fontSize: 11,
            letterSpacing: "0.1em",
            color: "var(--muted)",
            marginBottom: 16,
          }}
        >
          MODULES
        </h2>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
            gap: 12,
          }}
        >
          {FEATURES.map((f) => (
            <Link
              key={f.href}
              href={f.href}
              style={{
                display: "block",
                padding: 16,
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                color: "inherit",
                textDecoration: "none",
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "baseline",
                  marginBottom: 8,
                }}
              >
                <span style={{ fontWeight: 600, fontSize: 13 }}>{f.title}</span>
                <span style={{ fontSize: 10, color: "var(--accent)" }}>{f.stat}</span>
              </div>
              <p
                style={{
                  fontSize: 11,
                  color: "var(--muted)",
                  lineHeight: 1.6,
                  marginBottom: 10,
                }}
              >
                {f.description}
              </p>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {f.methods.map((m) => (
                  <span
                    key={m}
                    style={{
                      fontSize: 9,
                      padding: "2px 6px",
                      background: "var(--surface-2)",
                      borderRadius: 3,
                      color: "var(--muted)",
                    }}
                  >
                    {m}
                  </span>
                ))}
              </div>
            </Link>
          ))}
        </div>
      </section>

      <section
        style={{
          marginTop: 36,
          padding: 16,
          border: "1px solid var(--border)",
          borderRadius: 8,
          background: "var(--surface)",
        }}
      >
        <h2 style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>Research & backtests</h2>
        <p style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.6, marginBottom: 8 }}>
          Experiment tracker with code SHA and params JSONB. Walk-forward evaluation,
          Markowitz portfolio optimizer (Ledoit-Wolf), market-making simulator with
          annualized Sharpe, HMM regimes, isolation-forest anomalies, NLP news tagger.
        </p>
        <p style={{ fontSize: 10, color: "var(--muted)" }}>
          Reports:{" "}
          <code style={{ color: "var(--text)" }}>docs/research/fed-calibration-report.md</code>
          {" · "}
          <code style={{ color: "var(--text)" }}>docs/resume-packaging.md</code>
        </p>
      </section>

      <p
        style={{
          marginTop: 32,
          fontSize: 10,
          color: "var(--muted)",
          borderTop: "1px solid var(--border)",
          paddingTop: 16,
        }}
      >
        Not a trading bot — research only. No orders are placed.
      </p>
    </div>
  );
}
