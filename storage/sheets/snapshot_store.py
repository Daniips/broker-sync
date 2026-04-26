"""
Google Sheets implementation of `core.snapshot_store.SnapshotStore`.

Escribe a dos pestañas ocultas:
  - <agg_sheet>: 1 fila por snapshot con cash + positions_value + cost_basis + total.
  - <positions_sheet>: 1 fila por (snapshot, posición) con shares + net_value + cost_basis.

Ambas se crean automáticamente si no existen, con header en fila 1 y la
pestaña marcada como hidden.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import gspread

from core.snapshot_store import (
    SNAPSHOT_AGG_HEADER,
    SNAPSHOT_POSITIONS_HEADER,
    snapshot_to_rows,
)
from core.types import PortfolioSnapshot
from core.utils import parse_de_number


class SheetsSnapshotStore:
    """Snapshot store backed by a single gspread Spreadsheet."""

    def __init__(
        self,
        spreadsheet,
        *,
        agg_sheet: str = "_snapshots",
        positions_sheet: str = "_snapshots_positions",
    ):
        self.spreadsheet = spreadsheet
        self.agg_sheet_name = agg_sheet
        self.positions_sheet_name = positions_sheet

    # ── Internals ────────────────────────────────────────────────────────

    def _create_hidden_sheet(self, title: str, header: tuple[str, ...]):
        ws = self.spreadsheet.add_worksheet(title=title, rows=1000, cols=len(header))
        end_col = chr(ord('A') + len(header) - 1)
        ws.update(values=[list(header)], range_name=f"A1:{end_col}1")
        self.spreadsheet.batch_update({"requests": [{
            "updateSheetProperties": {
                "properties": {"sheetId": ws.id, "hidden": True},
                "fields": "hidden",
            }
        }]})
        return ws

    def _agg_ws(self):
        try:
            return self.spreadsheet.worksheet(self.agg_sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            return self._create_hidden_sheet(self.agg_sheet_name, SNAPSHOT_AGG_HEADER)

    def _pos_ws(self):
        try:
            return self.spreadsheet.worksheet(self.positions_sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            return self._create_hidden_sheet(self.positions_sheet_name, SNAPSHOT_POSITIONS_HEADER)

    # ── SnapshotStore protocol ───────────────────────────────────────────

    def append(
        self,
        snapshot: PortfolioSnapshot,
        cost_basis_total: Optional[float],
    ) -> None:
        agg, pos = snapshot_to_rows(snapshot, cost_basis_total)
        self._agg_ws().append_rows([agg], value_input_option="RAW")
        if pos:
            self._pos_ws().append_rows(pos, value_input_option="RAW")

    def append_batch(
        self,
        records: list[tuple[PortfolioSnapshot, Optional[float]]],
        *,
        skip_existing: bool = True,
    ) -> int:
        if not records:
            return 0
        if skip_existing:
            existing = self.load_timestamps()
            records = [
                (s, cb) for s, cb in records
                if s.ts.isoformat() not in existing
            ]
        if not records:
            return 0
        agg_rows = []
        pos_rows: list[list] = []
        for snap, cb in records:
            ag, pos = snapshot_to_rows(snap, cb)
            agg_rows.append(ag)
            pos_rows.extend(pos)
        self._agg_ws().append_rows(agg_rows, value_input_option="RAW")
        if pos_rows:
            self._pos_ws().append_rows(pos_rows, value_input_option="RAW")
        return len(records)

    def load_timestamps(self) -> set[str]:
        try:
            ws = self.spreadsheet.worksheet(self.agg_sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            return set()
        rows = ws.get_all_values()
        return {row[0] for row in rows[1:] if row and row[0]}

    def load_history(self) -> list[dict]:
        try:
            ws = self.spreadsheet.worksheet(self.agg_sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            return []
        rows = ws.get_all_values()
        if len(rows) < 2:
            return []
        out: list[dict] = []
        for row in rows[1:]:
            if len(row) < 5 or not row[0]:
                continue
            try:
                ts = datetime.fromisoformat(row[0])
            except (ValueError, IndexError):
                continue
            out.append({
                "ts": ts,
                "cash_eur": parse_de_number(row[1]) or 0.0,
                "positions_value_eur": parse_de_number(row[2]) or 0.0,
                "cost_basis_eur": parse_de_number(row[3]) or 0.0,
                "total_eur": parse_de_number(row[4]) or 0.0,
            })
        out.sort(key=lambda x: x["ts"])
        return out
