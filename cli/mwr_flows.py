"""--mwr-flows: dump the cash flows that `mwr()` uses to stdout as TSV."""
from datetime import datetime

from tr_sync import _ensure_tr_session


def dump_mwr_flows(
    *,
    bonus_as: str = "income",
    refresh: bool = False,
    locale: str = "us",
):
    """Print the cash flows used by `mwr()` to compute the all-time MWR.

    Output is TSV (date<TAB>amount<TAB>kind<TAB>title) ready to paste into
    Google Sheets or Excel and apply XIRR / TIR.NO.PER as a sanity check.

    Sign convention (matches mwr()):
      - BUY: negative amount (money out of pocket).
      - SELL: positive amount (money back into pocket).
      - DIVIDEND: positive amount (cash received).
      - Final snapshot: positive amount equal to positions_value_eur.

    `locale="us"` (default): `.` decimal separator. Works in Excel and in
    US-locale Sheets.
    `locale="es"`: `,` decimal separator. For Spanish-locale Sheets without
    having to reformat each cell.

    `bonus_as="income"` (default): saveback excluded as contribution. Use
    `bonus_as="deposit"` to include it.
    """
    from core.types import TxKind

    snapshot, txs, _ = _ensure_tr_session(refresh=refresh)

    flows: list[tuple[datetime, float, str, str]] = []
    for tx in txs:
        if tx.kind == TxKind.BUY:
            if tx.is_bonus and bonus_as == "income":
                continue
            flows.append((tx.ts, -abs(tx.amount_eur), "BUY", tx.title))
        elif tx.kind == TxKind.SELL:
            flows.append((tx.ts, abs(tx.amount_eur), "SELL", tx.title))
        elif tx.kind == TxKind.DIVIDEND:
            flows.append((tx.ts, abs(tx.amount_eur), "DIVIDEND", tx.title))
    flows.sort(key=lambda x: x[0])
    flows.append((snapshot.ts, snapshot.positions_value_eur, "FINAL", "Posiciones actuales"))

    def fmt_amount(x: float) -> str:
        s = f"{x:.2f}"
        return s.replace(".", ",") if locale == "es" else s

    formula_es = "=TIR.NO.PER(B2:B{n}; A2:A{n})".format(n=len(flows) + 1)
    formula_us = "=XIRR(B2:B{n}, A2:A{n})".format(n=len(flows) + 1)

    print(f"# MWR cash flows (bonus_as={bonus_as}, locale={locale})")
    if locale == "es":
        print(f"# Pega en Sheets ES y aplica:  {formula_es}")
    else:
        print(f"# Pega en Sheets/Excel y aplica:  {formula_us}")
    print(f"# {len(flows)-1} flujos + 1 valor final = {len(flows)} filas.")
    print()
    print("date\tamount\tkind\ttitle")
    for ts, amount, kind, title in flows:
        print(f"{ts.date().isoformat()}\t{fmt_amount(amount)}\t{kind}\t{title}")
