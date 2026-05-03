"""--debug-isin: print every transaction the adapter extracts for an ISIN."""
from collections import Counter

from tr_sync import _ensure_tr_session, log


def debug_isin(isin: str, *, refresh: bool = False):
    """Print every transaction the adapter extracts for one ISIN.

    Useful to reconcile against your own Excel: each BUY/SELL/DIVIDEND with
    its date, amount and shares is shown, so you can compare what TR
    actually emits. If your Excel has a number that doesn't appear here,
    that number is not in TR — it comes from somewhere else (manual,
    cash bonus, etc.).
    """
    _, txs, _ = _ensure_tr_session(refresh=refresh)
    matches = [t for t in txs if t.isin == isin]
    log.info(f"   {len(matches)} transactions for {isin}.\n")

    if not matches:
        log.warning("No transactions found for that ISIN. Check that it is correct.")
        return

    # Summary by kind
    by_kind = Counter(t.kind.value for t in matches)
    print(f"Summary by kind:")
    for k, n in sorted(by_kind.items()):
        total = sum(t.amount_eur for t in matches if t.kind.value == k)
        print(f"  {k:<12} count={n:>4}   sum={total:>12,.2f} €")
    print()

    # Sorted detail
    print(f"{'date':<12} {'kind':<10} {'shares':>10} {'amount':>12} {'bonus':>6}  title")
    print(f"{'-'*12} {'-'*10} {'-'*10} {'-'*12} {'-'*6}  {'-'*40}")
    for t in sorted(matches, key=lambda x: x.ts):
        ts = t.ts.strftime("%Y-%m-%d")
        sh = f"{t.shares:.6f}" if t.shares is not None else "n/a"
        bonus = "yes" if t.is_bonus else "no"
        print(f"{ts:<12} {t.kind.value:<10} {sh:>10} {t.amount_eur:>+12,.2f} {bonus:>6}  {t.title[:50]}")
    print()

    # Cost basis (FIFO, saveback at zero cost)
    from core.metrics import cost_basis_of_current_holdings
    cb = cost_basis_of_current_holdings(matches, bonus_at_zero_cost=True)
    cb_full = cost_basis_of_current_holdings(matches, bonus_at_zero_cost=False)
    print("Cost basis after FIFO:")
    print(f"  saveback at 0€:            {cb.get(isin, 0.0):>12,.2f} €")
    print(f"  saveback at market price:  {cb_full.get(isin, 0.0):>12,.2f} €")
