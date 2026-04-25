"""
Pure utilities: number parsers and A1 range helpers, no config dependencies.
Utilities puras: parsers de números y helpers de rangos A1, sin dependencias de config.
"""
import re


def parse_de_number(s):
    """
    Parse German-style numbers: '1.234,56 €' → 1234.56, '1,035444' → 1.035444.
    Returns None on invalid input.

    Parsea números estilo alemán: '1.234,56 €' → 1234.56, '1,035444' → 1.035444.
    Devuelve None si la entrada no es válida.
    """
    if s is None:
        return None
    s = str(s).replace("\xa0", "").replace("€", "").strip()
    if not s:
        return None
    # If both '.' and ',' are present, '.' is thousands and ',' is decimal (DE convention).
    # Si están los dos, '.' = miles y ',' = decimal (convención alemana).
    if "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_a1_column_range(rng):
    """
    Parse an A1 single-column range like 'C2:C8' → ('C', 2, 8).
    Returns (None, None, None) if input is not a single-column range.

    Parsea un rango A1 de columna única tipo 'C2:C8' → ('C', 2, 8).
    Devuelve (None, None, None) si no es un rango de columna única.
    """
    m = re.match(r"^([A-Z]+)(\d+):([A-Z]+)(\d+)$", str(rng or ""))
    if not m:
        return None, None, None
    col_start, row_start, col_end, row_end = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
    if col_start != col_end:
        return None, None, None
    return col_start, row_start, row_end


def column_letter_to_index(letter):
    """
    Convert an A1 column letter to its 1-based index. A→1, B→2, ..., Z→26, AA→27, ...

    Convierte una letra de columna A1 a su índice 1-based.
    """
    n = 0
    for c in letter:
        n = n * 26 + (ord(c) - ord('A') + 1)
    return n
