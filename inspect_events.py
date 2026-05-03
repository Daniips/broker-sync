"""Utility to inspect raw Trade Republic events.

Usage:
    python inspect_events.py               # summary + sales for the previous year
    python inspect_events.py --raw         # also dumps the JSON of the 1st sale
    python inspect_events.py --year 2025   # force a specific year
"""
import argparse
import asyncio
import json
from collections import defaultdict
from datetime import datetime

from pytr.account import login
from tr_sync import TIMEZONE, fetch_tr_events


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--year", type=int, default=None,
                   help="Fiscal year to inspect (default: current year - 1)")
    p.add_argument("--raw", action="store_true",
                   help="Dumps the full JSON of the first sale found")
    p.add_argument("--isin", type=str, default=None,
                   help="Lists every event with that ISIN + JSON of one purchase")
    p.add_argument("--eventtype", type=str, default=None,
                   help="Dumps JSON of the events with that eventType (e.g. GIFTING_RECIPIENT_ACTIVITY)")
    p.add_argument("--title", type=str, default=None,
                   help="Filters events whose title contains this text (case-insensitive)")
    args = p.parse_args()

    year = args.year or (datetime.now(tz=TIMEZONE).year - 1)

    tr = login()
    events = asyncio.run(fetch_tr_events(tr))

    by_type = defaultdict(list)
    for e in events:
        by_type[e.get("eventType")].append(e)

    print(f"\nTotal events: {len(events)}\n")
    for et, evs in sorted(by_type.items(), key=lambda x: -len(x[1])):
        sample = evs[0]
        print(f"{str(et):<40} {len(evs):>4}  e.g.: {sample.get('title')!r}  ts={sample.get('timestamp')}")

    # Sales = TRADING_TRADE_EXECUTED with amount.value > 0
    sells_of_year = []
    for e in events:
        if e.get("eventType") != "TRADING_TRADE_EXECUTED":
            continue
        val = (e.get("amount") or {}).get("value")
        if val is None or val <= 0:
            continue
        ts = e.get("timestamp") or ""
        if not ts.startswith(str(year)):
            continue
        sells_of_year.append(e)

    print(f"\n--- sales for {year} (TRADING_TRADE_EXECUTED with amount>0): {len(sells_of_year)} ---")
    for e in sells_of_year:
        print(f"  {e.get('timestamp')}  {e.get('title')!r}  amount={(e.get('amount') or {}).get('value')}  id={e.get('id')}")

    if args.raw and sells_of_year:
        print("\n--- full JSON of the first sale ---")
        print(json.dumps(sells_of_year[0], indent=2, ensure_ascii=False, default=str))
    elif args.raw:
        # if there are no sales, at least show one purchase as a reference
        buys = [e for e in events if e.get("eventType") == "TRADING_TRADE_EXECUTED"]
        if buys:
            print("\n--- no sales for that year; JSON of one purchase as reference ---")
            print(json.dumps(buys[0], indent=2, ensure_ascii=False, default=str))

    if args.title:
        from tr_sync import _extract_trade_details
        needle = args.title.lower()
        matching = [e for e in events if needle in (e.get("title") or "").lower()]
        print(f"\n--- events whose title contains '{args.title}': {len(matching)} ---")
        for e in matching:
            d = _extract_trade_details(e)
            amt = (e.get("amount") or {}).get("value")
            print(f"  {e.get('timestamp')}  {e.get('eventType'):<32} {e.get('subtitle') or '':<24} "
                  f"amount={amt!r:<10} isin={d.get('isin')!r}  title={e.get('title')!r}")
        if matching:
            print("\n--- JSON of the first event found ---")
            print(json.dumps(matching[0], indent=2, ensure_ascii=False, default=str))

    if args.eventtype:
        matching = [e for e in events if e.get("eventType") == args.eventtype]
        print(f"\n--- events with eventType={args.eventtype}: {len(matching)} ---")
        for i, e in enumerate(matching):
            print(f"\n--- JSON #{i+1} ---")
            print(json.dumps(e, indent=2, ensure_ascii=False, default=str))

    if args.isin:
        from tr_sync import _extract_trade_details
        target = args.isin.upper()
        print(f"\n--- events with ISIN {target} ---")
        hits = []
        for e in events:
            d = _extract_trade_details(e)
            icon = (e.get("icon") or "") + " " + str((e.get("avatar") or {}).get("asset") or "")
            if d.get("isin") == target or target in icon:
                hits.append(e)
                amt = (e.get("amount") or {}).get("value")
                status = "OK" if (d.get("shares") is not None) else "NO SHARES"
                print(f"  {e.get('timestamp')}  {e.get('eventType'):<32} {e.get('subtitle') or '':<18} "
                      f"amount={amt!r:<10} shares={d.get('shares')!r:<12} [{status}]  title={e.get('title')!r}")
        if hits:
            # Dump one JSON per distinct eventType (the first one found of each)
            dumped_types = set()
            for e in hits:
                et = e.get("eventType")
                if et in dumped_types:
                    continue
                dumped_types.add(et)
                print(f"\n--- JSON of an event '{et}' with that ISIN ---")
                print(json.dumps(e, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
