"""Utilidad para inspeccionar los eventos brutos de Trade Republic.

Uso:
    python inspect_events.py               # resumen + ventas del año anterior
    python inspect_events.py --raw         # además vuelca el JSON de la 1ª venta
    python inspect_events.py --year 2025   # fuerza un año concreto
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
                   help="Año fiscal a inspeccionar (default: año actual - 1)")
    p.add_argument("--raw", action="store_true",
                   help="Vuelca el JSON completo de la primera venta encontrada")
    p.add_argument("--isin", type=str, default=None,
                   help="Lista todos los eventos con ese ISIN + JSON de una compra")
    p.add_argument("--eventtype", type=str, default=None,
                   help="Vuelca JSON de los eventos con ese eventType (e.g. GIFTING_RECIPIENT_ACTIVITY)")
    p.add_argument("--title", type=str, default=None,
                   help="Filtra eventos cuyo título contenga este texto (case-insensitive)")
    args = p.parse_args()

    year = args.year or (datetime.now(tz=TIMEZONE).year - 1)

    tr = login()
    events = asyncio.run(fetch_tr_events(tr))

    by_type = defaultdict(list)
    for e in events:
        by_type[e.get("eventType")].append(e)

    print(f"\nTotal eventos: {len(events)}\n")
    for et, evs in sorted(by_type.items(), key=lambda x: -len(x[1])):
        sample = evs[0]
        print(f"{str(et):<40} {len(evs):>4}  ej: {sample.get('title')!r}  ts={sample.get('timestamp')}")

    # Ventas = TRADING_TRADE_EXECUTED con amount.value > 0
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

    print(f"\n--- ventas de {year} (TRADING_TRADE_EXECUTED con amount>0): {len(sells_of_year)} ---")
    for e in sells_of_year:
        print(f"  {e.get('timestamp')}  {e.get('title')!r}  importe={(e.get('amount') or {}).get('value')}  id={e.get('id')}")

    if args.raw and sells_of_year:
        print("\n--- JSON completo de la primera venta ---")
        print(json.dumps(sells_of_year[0], indent=2, ensure_ascii=False, default=str))
    elif args.raw:
        # si no hay ventas, al menos enseña una compra como referencia
        buys = [e for e in events if e.get("eventType") == "TRADING_TRADE_EXECUTED"]
        if buys:
            print("\n--- no hay ventas ese año; JSON de una compra como referencia ---")
            print(json.dumps(buys[0], indent=2, ensure_ascii=False, default=str))

    if args.title:
        from tr_sync import _extract_trade_details
        needle = args.title.lower()
        matching = [e for e in events if needle in (e.get("title") or "").lower()]
        print(f"\n--- eventos cuyo título contiene '{args.title}': {len(matching)} ---")
        for e in matching:
            d = _extract_trade_details(e)
            amt = (e.get("amount") or {}).get("value")
            print(f"  {e.get('timestamp')}  {e.get('eventType'):<32} {e.get('subtitle') or '':<24} "
                  f"amount={amt!r:<10} isin={d.get('isin')!r}  title={e.get('title')!r}")
        if matching:
            print("\n--- JSON del primer evento encontrado ---")
            print(json.dumps(matching[0], indent=2, ensure_ascii=False, default=str))

    if args.eventtype:
        matching = [e for e in events if e.get("eventType") == args.eventtype]
        print(f"\n--- eventos con eventType={args.eventtype}: {len(matching)} ---")
        for i, e in enumerate(matching):
            print(f"\n--- JSON #{i+1} ---")
            print(json.dumps(e, indent=2, ensure_ascii=False, default=str))

    if args.isin:
        from tr_sync import _extract_trade_details
        target = args.isin.upper()
        print(f"\n--- eventos con ISIN {target} ---")
        hits = []
        for e in events:
            d = _extract_trade_details(e)
            icon = (e.get("icon") or "") + " " + str((e.get("avatar") or {}).get("asset") or "")
            if d.get("isin") == target or target in icon:
                hits.append(e)
                amt = (e.get("amount") or {}).get("value")
                status = "OK" if (d.get("shares") is not None) else "SIN SHARES"
                print(f"  {e.get('timestamp')}  {e.get('eventType'):<32} {e.get('subtitle') or '':<18} "
                      f"amount={amt!r:<10} shares={d.get('shares')!r:<12} [{status}]  title={e.get('title')!r}")
        if hits:
            # Vuelca un JSON por cada eventType distinto (primero que encontremos de cada uno)
            dumped_types = set()
            for e in hits:
                et = e.get("eventType")
                if et in dumped_types:
                    continue
                dumped_types.add(et)
                print(f"\n--- JSON de un evento '{et}' con ese ISIN ---")
                print(json.dumps(e, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
