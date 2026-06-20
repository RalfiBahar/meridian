// TypeScript mirrors of the Pydantic response models in src/meridian/api/models.py

export interface MarketSignals {
  p_bid: number | null;
  p_ask: number | null;
  p_mid: number | null;
  microprice: number | null;
  depth_weighted_prob: number | null;
  effective_spread: number | null;
  obi: number | null;
  kyle_lambda: number | null;
  amihud: number | null;
}

export interface MarketSummary {
  id: string;
  external_id: string;
  venue: string;
  question: string;
  category: string | null;
  resolution_status: string;
  closes_at: string | null;
  p_bid: number | null;
  p_ask: number | null;
  p_mid: number | null;
  microprice: number | null;
}

export interface MarketsResponse {
  markets: MarketSummary[];
  total: number;
}

export interface TickRow {
  event_ts: string;
  sequence_no: number;
  kind: string;
  bid: number | null;
  ask: number | null;
  bid_size: number | null;
  ask_size: number | null;
  trade_price: number | null;
  trade_size: number | null;
  side?: string | null;
  book_price?: number | null;
  book_delta?: number | null;
}

export interface BookLevel {
  side: string;
  level: number;
  price: number;
  size: number;
}

export interface MarketDetail {
  id: string;
  external_id: string;
  venue: string;
  question: string;
  category: string | null;
  resolution_status: string;
  closes_at: string | null;
  signals: MarketSignals;
  book: BookLevel[];
  recent_ticks: TickRow[];
}

export interface PartitionViolation {
  group_id: string;
  group_label: string;
  n_contracts: number;
  violation_bps: number;
  direction: string;
  depth_feasible: boolean;
  min_ask_sum: number;
  max_bid_sum: number;
}

export interface CrossVenueDivergence {
  group_id: string;
  venue_a: string;
  p_mid_a: number;
  venue_b: string;
  p_mid_b: number;
  divergence_bps: number;
}

export interface ArbViolationsResponse {
  partition_violations: PartitionViolation[];
  cross_venue_divergences: CrossVenueDivergence[];
  checked_at?: string;
}

export interface ReliabilityBin {
  bin_center: number;
  mean_predicted: number;
  mean_realized: number;
  count: number;
}

export interface CalibrationResponse {
  brier_score: number;
  log_loss: number;
  n_resolved: number;
  reliability_bins: ReliabilityBin[];
}

export interface FedPMF {
  fomc_date: string;
  strikes: number[];
  probabilities: number[];
  expected_rate: number;
  entropy: number;
  source: string;
  raw_p_mid?: number[];
}

export interface FedWatchResponse {
  fomc_date: string;
  kalshi: FedPMF | null;
  cme: FedPMF | null;
}

// WebSocket event shapes from the EventHub
export type WsEvent =
  | { type: "snapshot"; total: number; markets: MarketSummary[] }
  | { type: "tick"; data: Record<string, unknown> }
  | { type: "ping" }
  | { type: "arb_snapshot"; data: ArbViolationsResponse };
