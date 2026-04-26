"""
Status sheet persistence: a small visible tab that shows the timestamp of
the last successful sync for each process (sync, portfolio, etc.).

Persistencia de la pestaña 'Estado sync': pestaña pequeña visible donde se
escribe el timestamp del último sync OK por proceso (sync, portfolio…).
"""
from __future__ import annotations

from datetime import datetime
from typing import Mapping
from zoneinfo import ZoneInfo

import gspread


class StatusStore:
    """Wrapper sobre la pestaña de estado.

    `labels`: mapping de claves internas (p.ej. 'portfolio', 'sync') a las
    etiquetas humanas que se muestran en la columna A. Configurable.
    `tz`: zona horaria para los timestamps escritos.
    """

    def __init__(
        self,
        spreadsheet,
        *,
        sheet_name: str,
        labels: Mapping[str, str],
        tz: ZoneInfo,
    ):
        self.spreadsheet = spreadsheet
        self.sheet_name = sheet_name
        self.labels = dict(labels)
        self.tz = tz

    def _get_or_create_ws(self):
        try:
            return self.spreadsheet.worksheet(self.sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            ws = self.spreadsheet.add_worksheet(title=self.sheet_name, rows=10, cols=2)
            ws.update(values=[["Proceso", "Último OK"]], range_name="A1:B1")
            for i, lbl in enumerate(self.labels.values(), start=2):
                ws.update_cell(i, 1, lbl)
            return ws

    def write(self, key: str) -> None:
        """Escribe `now` en la fila correspondiente a `key`. Crea la fila si falta."""
        label = self.labels[key]
        ws = self._get_or_create_ws()
        col_a = ws.col_values(1)
        row = next((i for i, v in enumerate(col_a, start=1) if v.strip() == label), None)
        if row is None:
            row = len(col_a) + 1
            ws.update_cell(row, 1, label)
        now = datetime.now(tz=self.tz).strftime("%Y-%m-%d %H:%M:%S")
        ws.update_cell(row, 2, now)
