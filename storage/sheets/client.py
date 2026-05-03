"""
Thin wrapper for opening a Google Sheets workbook via gspread OAuth.
"""
from __future__ import annotations

import gspread


def open_spreadsheet(sheet_id: str):
    """Open the Google Sheets workbook identified by `sheet_id` via gspread OAuth.

    Assumes the user already has OAuth credentials configured (typically
    `~/.config/gspread/credentials.json` + a cached token).
    """
    gc = gspread.oauth()
    return gc.open_by_key(sheet_id)
