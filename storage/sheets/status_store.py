"""
Status sheet persistence: a small visible tab that shows the timestamp of
the last successful sync for each process (sync, portfolio, etc.).
"""
from __future__ import annotations

from datetime import datetime
from typing import Mapping
from zoneinfo import ZoneInfo

import gspread


class StatusStore:
    """Wrapper around the status tab.

    `labels`: mapping of internal keys (e.g. 'portfolio', 'sync') to the
    human-readable labels shown in column A. Configurable.
    `tz`: time zone used for the timestamps written.
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
        """Write `now` to the row corresponding to `key`. Creates the row if missing."""
        label = self.labels[key]
        ws = self._get_or_create_ws()
        col_a = ws.col_values(1)
        row = next((i for i, v in enumerate(col_a, start=1) if v.strip() == label), None)
        if row is None:
            row = len(col_a) + 1
            ws.update_cell(row, 1, label)
        now = datetime.now(tz=self.tz).strftime("%Y-%m-%d %H:%M:%S")
        ws.update_cell(row, 2, now)
