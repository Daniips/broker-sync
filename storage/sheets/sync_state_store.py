"""
Hidden tab to dedup events across sync runs.

Pestaña oculta para deduplicar eventos entre ejecuciones del sync. Cada fila
es un event id ya procesado; antes de escribir un evento a la Sheet, miramos
si su id está aquí.
"""
from __future__ import annotations

from typing import Iterable

import gspread


class SyncStateStore:
    """Persistencia del set de event ids ya sincronizados."""

    def __init__(self, spreadsheet, *, sheet_name: str):
        self.spreadsheet = spreadsheet
        self.sheet_name = sheet_name

    def _get_or_create_ws(self):
        try:
            return self.spreadsheet.worksheet(self.sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            ws = self.spreadsheet.add_worksheet(title=self.sheet_name, rows=1000, cols=1)
            ws.update(values=[["tr_event_id"]], range_name="A1")
            self.spreadsheet.batch_update({"requests": [{
                "updateSheetProperties": {
                    "properties": {"sheetId": ws.id, "hidden": True},
                    "fields": "hidden",
                }
            }]})
            return ws

    def load(self) -> set[str]:
        """Devuelve el set de event ids ya escritos."""
        ws = self._get_or_create_ws()
        return set(ws.col_values(1)[1:])

    def append(self, new_ids: Iterable[str]) -> None:
        """Añade event ids al set (no deduplica internamente; el caller filtra)."""
        ids = list(new_ids)
        if not ids:
            return
        ws = self._get_or_create_ws()
        ws.append_rows([[x] for x in ids], value_input_option="RAW")
