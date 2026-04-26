"""
Thin wrapper for opening a Google Sheets workbook via gspread OAuth.

Pequeño wrapper para abrir un Google Sheets vía gspread OAuth.
"""
from __future__ import annotations

import gspread


def open_spreadsheet(sheet_id: str):
    """Abre el Google Sheets identificado por `sheet_id` usando gspread OAuth.

    Asume que el usuario ya tiene credenciales OAuth configuradas (típicamente
    `~/.config/gspread/credentials.json` + token cacheado).
    """
    gc = gspread.oauth()
    return gc.open_by_key(sheet_id)
