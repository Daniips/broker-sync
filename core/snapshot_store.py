"""
Protocol and pure helpers for persisting historical PortfolioSnapshots.

The actual storage backend (Google Sheets, SQLite, JSON file…) lives outside
of `core/`; this module defines the interface and the pure conversion logic.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol, runtime_checkable

from core.types import PortfolioSnapshot


# Schema of the aggregate row (one per snapshot).
SNAPSHOT_AGG_HEADER: tuple[str, ...] = (
    "ts", "cash_eur", "positions_value_eur", "cost_basis_eur", "total_eur",
)

# Schema of the per-position row (one per (snapshot, position)).
SNAPSHOT_POSITIONS_HEADER: tuple[str, ...] = (
    "ts", "isin", "title", "shares", "net_value_eur", "cost_basis_eur",
)


def snapshot_to_rows(
    snapshot: PortfolioSnapshot,
    cost_basis_total: Optional[float],
) -> tuple[list, list[list]]:
    """Convert a snapshot into (agg_row, [pos_rows]) for serialization.

    Pure function, no I/O. Used by the Sheets backend and by any other
    backend we add (CSV, SQLite, etc.) — only the row-writing differs.
    """
    ts_iso = snapshot.ts.isoformat()
    agg = [
        ts_iso,
        round(snapshot.cash_eur, 2),
        round(snapshot.positions_value_eur, 2),
        round(cost_basis_total or 0.0, 2),
        round(snapshot.total_eur, 2),
    ]
    pos = [
        [
            ts_iso,
            p.isin or "",
            p.title or "",
            round(p.shares, 8) if p.shares else 0,
            round(p.net_value_eur, 2),
            round(p.cost_basis_eur, 2) if p.cost_basis_eur is not None else "",
        ]
        for p in snapshot.positions
    ]
    return agg, pos


def snapshot_value_at(snapshots: list[dict], target_ts: datetime) -> Optional[float]:
    """Return `positions_value_eur` of the latest snapshot with ts ≤ target_ts.

    Assumes `snapshots` is sorted ascending by ts (what `load_history`
    returns). Returns None if no earlier snapshot exists.
    """
    candidates = [s for s in snapshots if s["ts"] <= target_ts]
    if not candidates:
        return None
    return candidates[-1]["positions_value_eur"]


@runtime_checkable
class SnapshotStore(Protocol):
    """Interface every snapshot backend must implement.

    Methods perform I/O against the underlying storage. Implementations
    live in `storage/<backend>/snapshot_store.py`.
    """

    def append(
        self,
        snapshot: PortfolioSnapshot,
        cost_basis_total: Optional[float],
    ) -> None:
        """Append a snapshot to the store (aggregate + per position)."""

    def append_batch(
        self,
        records: list[tuple[PortfolioSnapshot, Optional[float]]],
        *,
        skip_existing: bool = True,
    ) -> int:
        """Write N snapshots at once (more efficient than N appends).

        `skip_existing=True`: skip snapshots whose ts is already present —
        enables idempotent backfill re-runs.

        Returns the number of snapshots actually written.
        """

    def load_history(self) -> list[dict]:
        """Read all aggregate snapshots sorted by ts ascending.

        Each entry: {ts, cash_eur, positions_value_eur, cost_basis_eur, total_eur}.
        """

    def load_timestamps(self) -> set[str]:
        """Return just the already-written ts (ISO strings). Useful for dedup."""
