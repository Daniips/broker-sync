"""
Pure utilities: number parsers and A1 range helpers, no config dependencies.
"""
import re
from datetime import date, datetime
from typing import Optional, Union


def resolve_dated_schedule(
    spec: Union[None, int, float, dict],
    when: Union[date, datetime],
) -> Optional[float]:
    """Resolve a scalar OR dated schedule against a given moment.

    Accepted forms:
      - None → returns None
      - scalar (int/float) → returned as float
      - dict {date_or_iso_string: numeric_value} → returns the value of the
        latest entry whose date is <= `when`. Returns None if no entry
        applies yet (i.e. all dates are in the future).

    Used for config values that step up over time (e.g. cash targets that
    grow every 6 months, contribution caps that increase yearly).
    """
    if spec is None:
        return None
    if isinstance(spec, (int, float)):
        return float(spec)
    if not isinstance(spec, dict):
        return None
    today = when.date() if isinstance(when, datetime) else when
    parsed: list[tuple[date, float]] = []
    for k, v in spec.items():
        if isinstance(k, datetime):
            d = k.date()
        elif isinstance(k, date):
            d = k
        elif isinstance(k, str):
            try:
                d = datetime.fromisoformat(k).date()
            except ValueError:
                continue
        else:
            continue
        try:
            parsed.append((d, float(v)))
        except (TypeError, ValueError):
            continue
    parsed.sort(key=lambda x: x[0])
    effective: Optional[float] = None
    for d, val in parsed:
        if d <= today:
            effective = val
        else:
            break
    return effective


def parse_de_number(s):
    """
    Parse German-style numbers: '1.234,56 €' → 1234.56, '1,035444' → 1.035444.
    Returns None on invalid input.
    """
    if s is None:
        return None
    s = str(s).replace("\xa0", "").replace("€", "").strip()
    if not s:
        return None
    # If both '.' and ',' are present, '.' is thousands and ',' is decimal (DE convention).
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
    """
    n = 0
    for c in letter:
        n = n * 26 + (ord(c) - ord('A') + 1)
    return n
