"""
Generic FIFO algorithm by ISIN for tax reports. Broker-agnostic.
"""
from collections import defaultdict


def apply_fifo(buy_lots, sales):
    """
    Apply FIFO per-ISIN: match each sale against the oldest buys of the same ISIN.

    Inputs:
        buy_lots: list of dicts with keys {timestamp, isin, title, shares, cost_eur}.
        sales:    list of dicts with keys {timestamp, isin, title, shares, proceeds_eur}.

    Returns: list of {sale, shares_matched, shares_unmatched, cost_basis, gain_loss, matched_lots}.
    """
    # Group buy lots by ISIN, keeping their order (assumed chronological by caller).
    lots_by_isin = defaultdict(list)
    for l in buy_lots:
        lots_by_isin[l["isin"]].append({
            "timestamp": l["timestamp"],
            "shares_remaining": l["shares"],
            "unit_cost": l["cost_eur"] / l["shares"] if l["shares"] > 0 else 0.0,
        })

    results = []
    for sale in sales:
        lots = lots_by_isin.get(sale["isin"], [])
        remaining = sale["shares"]
        matched = []
        cost_basis = 0.0
        for lot in lots:
            if remaining <= 1e-12:
                break
            if lot["shares_remaining"] <= 1e-12:
                continue
            take = min(lot["shares_remaining"], remaining)
            cost_part = take * lot["unit_cost"]
            matched.append({
                "buy_ts": lot["timestamp"],
                "shares": take,
                "unit_cost": lot["unit_cost"],
                "cost_part": cost_part,
            })
            lot["shares_remaining"] -= take
            remaining -= take
            cost_basis += cost_part

        results.append({
            "sale": sale,
            "shares_matched": sale["shares"] - remaining,
            "shares_unmatched": remaining,
            "cost_basis": cost_basis,
            "gain_loss": sale["proceeds_eur"] - cost_basis,
            "matched_lots": matched,
        })
    return results
