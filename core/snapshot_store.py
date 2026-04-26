"""
Protocol and pure helpers for persisting historical PortfolioSnapshots.

Protocolo y helpers puros para persistir PortfolioSnapshots históricos.

The actual storage backend (Google Sheets, SQLite, JSON file…) lives outside
of `core/`; this module defines the interface and the pure conversion logic.

El backend real (Sheets, SQLite, JSON…) vive fuera de `core/`; este módulo
define la interfaz y la lógica pura de conversión.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol, runtime_checkable

from core.types import PortfolioSnapshot


# Esquema del row agregado (uno por snapshot).
SNAPSHOT_AGG_HEADER: tuple[str, ...] = (
    "ts", "cash_eur", "positions_value_eur", "cost_basis_eur", "total_eur",
)

# Esquema del row por posición (uno por (snapshot, posición)).
SNAPSHOT_POSITIONS_HEADER: tuple[str, ...] = (
    "ts", "isin", "title", "shares", "net_value_eur", "cost_basis_eur",
)


def snapshot_to_rows(
    snapshot: PortfolioSnapshot,
    cost_basis_total: Optional[float],
) -> tuple[list, list[list]]:
    """Convierte un snapshot en (agg_row, [pos_rows]) para serialización.

    Función pura, sin I/O. La usa tanto el backend Sheets como cualquier otro
    que añadamos (CSV, SQLite, etc.) — solo cambia cómo se escriben las filas.
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
    """Devuelve `positions_value_eur` del último snapshot con ts ≤ target_ts.

    Asume `snapshots` ordenado ascendente por ts (lo que devuelve `load_history`).
    Devuelve None si no hay snapshot anterior.
    """
    candidates = [s for s in snapshots if s["ts"] <= target_ts]
    if not candidates:
        return None
    return candidates[-1]["positions_value_eur"]


@runtime_checkable
class SnapshotStore(Protocol):
    """Interfaz que cualquier backend de snapshots debe implementar.

    Los métodos hacen I/O contra el storage subyacente. Las implementaciones
    viven en `storage/<backend>/snapshot_store.py`.
    """

    def append(
        self,
        snapshot: PortfolioSnapshot,
        cost_basis_total: Optional[float],
    ) -> None:
        """Añade un snapshot al store (agregado + por posición)."""

    def append_batch(
        self,
        records: list[tuple[PortfolioSnapshot, Optional[float]]],
        *,
        skip_existing: bool = True,
    ) -> int:
        """Escribe N snapshots de una vez (más eficiente que N appends).

        `skip_existing=True`: omite snapshots con ts ya presente — permite
        re-ejecuciones idempotentes del backfill.

        Devuelve el número de snapshots realmente escritos.
        """

    def load_history(self) -> list[dict]:
        """Lee todos los snapshots agregados ordenados por ts ascendente.

        Cada entrada: {ts, cash_eur, positions_value_eur, cost_basis_eur, total_eur}.
        """

    def load_timestamps(self) -> set[str]:
        """Devuelve solo los ts (ISO strings) ya escritos. Útil para dedup."""
