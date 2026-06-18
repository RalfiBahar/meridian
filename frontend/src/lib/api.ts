import type {
  ArbViolationsResponse,
  CalibrationResponse,
  FedWatchResponse,
  MarketDetail,
  MarketsResponse,
} from "@/types/api";

// In server components, calls go directly to the backend via Next.js rewrites.
// In client components, use the same paths — the rewrite proxy forwards them.
const AUTH_HEADER: Record<string, string> =
  process.env.NEXT_PUBLIC_API_KEY
    ? { "X-API-Key": process.env.NEXT_PUBLIC_API_KEY }
    : {};

async function apiFetch<T>(
  path: string,
  params?: Record<string, string | number | undefined>,
  options?: RequestInit,
): Promise<T> {
  const base =
    typeof window === "undefined"
      ? (process.env.MERIDIAN_API_URL ?? "http://localhost:8000")
      : "";
  const url = new URL(path, base || "http://n");
  if (base === "") url.protocol = "http:";
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined) url.searchParams.set(k, String(v));
  }
  const href = base ? url.toString() : path + url.search;
  const res = await fetch(href, {
    headers: { ...AUTH_HEADER },
    next: { revalidate: 10 },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export function fetchMarkets(params?: {
  status?: string;
  category?: string;
  limit?: number;
  offset?: number;
}): Promise<MarketsResponse> {
  return apiFetch<MarketsResponse>("/api/v1/markets", params);
}

export function fetchMarket(id: string): Promise<MarketDetail> {
  return apiFetch<MarketDetail>(`/api/v1/markets/${id}`);
}

export function fetchArbViolations(): Promise<ArbViolationsResponse> {
  return apiFetch<ArbViolationsResponse>("/api/v1/arb/violations");
}

export function fetchCalibration(params?: {
  category?: string;
}): Promise<CalibrationResponse> {
  return apiFetch<CalibrationResponse>("/api/v1/calibration", params);
}

export function fetchFedWatch(params?: {
  fomc_date?: string;
  cme?: boolean;
}): Promise<FedWatchResponse> {
  const p: Record<string, string> = {};
  if (params?.fomc_date) p.fomc_date = params.fomc_date;
  if (params?.cme) p.cme = "true";
  return apiFetch<FedWatchResponse>("/api/v1/fedwatch", p);
}
