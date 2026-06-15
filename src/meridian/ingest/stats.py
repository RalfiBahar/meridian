"""Counters tracked over an ingestion run."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IngestStats:
    received: int = 0
    normalized: int = 0
    control: int = 0
    rows_written: int = 0
    new_markets: int = 0
    gaps_detected: int = 0
    reconnects: int = 0
    events_published: int = 0
    unknown_types: dict[str, int] = field(default_factory=dict)

    def as_log_fields(self) -> dict[str, object]:
        return {
            "received": self.received,
            "normalized": self.normalized,
            "control": self.control,
            "rows_written": self.rows_written,
            "new_markets": self.new_markets,
            "gaps_detected": self.gaps_detected,
            "reconnects": self.reconnects,
            "events_published": self.events_published,
            "unknown_types": dict(self.unknown_types),
        }
