import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Meridian — Quant Terminal",
  description: "Prediction-market research engine",
};

const NAV_LINKS = [
  { href: "/", label: "Home" },
  { href: "/markets", label: "Markets" },
  { href: "/arb", label: "Arb" },
  { href: "/calibration", label: "Calibration" },
  { href: "/fedwatch", label: "FedWatch" },
] as const;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header
          style={{
            display: "flex",
            alignItems: "center",
            gap: 24,
            padding: "0 16px",
            height: "var(--nav-height)",
            borderBottom: "1px solid var(--border)",
            background: "var(--surface)",
            position: "sticky",
            top: 0,
            zIndex: 100,
          }}
        >
          <Link
            href="/"
            style={{ fontWeight: 700, fontSize: 14, color: "var(--text)", letterSpacing: "0.05em" }}
          >
            MERIDIAN
          </Link>
          <nav style={{ display: "flex", gap: 4 }}>
            {NAV_LINKS.map(({ href, label }) => (
              <Link
                key={href}
                href={href}
                style={{
                  padding: "4px 10px",
                  borderRadius: 4,
                  color: "var(--muted)",
                  fontSize: 12,
                  fontWeight: 500,
                }}
              >
                {label}
              </Link>
            ))}
          </nav>
        </header>
        <main style={{ minHeight: `calc(100vh - var(--nav-height))` }}>{children}</main>
      </body>
    </html>
  );
}
