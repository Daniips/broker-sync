"""--insights: print net worth, MWR and other portfolio insights to stdout."""
from datetime import datetime, timedelta

from tr_sync import (
    ASSET_CURRENCIES,
    BENCHMARK_ISIN,
    BENCHMARK_LABEL,
    CONCENTRATION_LIMITS,
    CONCENTRATION_THRESHOLD,
    CRYPTO_ISINS,
    TIMEZONE,
    _ensure_tr_session,
    _make_snapshot_store,
    is_feature_enabled,
    log,
    open_spreadsheet,
    snapshot_value_at,
)


def sync_insights(verbose: bool = False, *, refresh: bool = False):
    """Print investment insights to the console. Doesn't touch the Sheet.

    Always-on blocks:
      1. CURRENT NET WORTH: cash + positions.
      2. RETURN — CURRENT POSITIONS: two readings of the same cost basis,
         with and without saveback discounted.
      3. RETURN — FULL HISTORY: annualized all-time MWR (includes
         dividends received) in two modes (saveback as income vs as
         contribution).
      4. MONTHLY CONTRIBUTIONS: this month vs the trailing 12-month average.

    Extra block when verbose=True:
      - PER-POSITION: detailed table per ISIN for diagnostics / Excel
        reconciliation.

    MWR YTD / 12m is omitted when there's no historical snapshot at the
    start of the period (next iteration).
    """
    if not is_feature_enabled("insights"):
        log.info("Feature 'insights' deshabilitada (config o broker).")
        return

    from core.metrics import (
        benchmark_return,
        concentration,
        contribution_vs_average,
        cost_basis_total as _cb_total,
        cost_basis_user_paid_per_isin,
        currency_exposure,
        monthly_contributions,
        mwr,
        per_position_attribution,
        total_invested,
        unrealized_return,
        unrealized_return_user_paid,
    )

    benchmark_isins = (BENCHMARK_ISIN,) if BENCHMARK_ISIN else ()
    snapshot, txs, benchmarks = _ensure_tr_session(refresh=refresh, benchmark_isins=benchmark_isins)
    log.info(f"   {len(txs)} transacciones, {len(snapshot.positions)} posiciones.")

    # Persist snapshot + load history for MWR YTD/12m.
    snapshot_history: list[dict] = []
    try:
        spreadsheet = open_spreadsheet()
        store = _make_snapshot_store(spreadsheet)
        store.append(snapshot, _cb_total(snapshot))
        snapshot_history = store.load_history()
        log.info(f"   snapshot guardado. histórico: {len(snapshot_history)} entradas.\n")
    except Exception as e:
        log.warning(f"   ⚠ no se pudo persistir/cargar snapshots ({e}); MWR YTD/12m se omite.\n")

    now = datetime.now(tz=TIMEZONE)

    bar = "═" * 64

    def fmt_pct(x, *, anual=False, sign=True):
        if x is None:
            return "n/a"
        s = f"{x*100:+.2f}" if sign else f"{x*100:.2f}"
        return f"{s} %" + (" anual" if anual else "")

    def fmt_eur(x):
        s = f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{s} €"

    crypto_value = sum(p.net_value_eur for p in snapshot.positions if p.isin in CRYPTO_ISINS)
    etf_value = snapshot.positions_value_eur - crypto_value

    print(bar)
    print("  PATRIMONIO ACTUAL")
    print(bar)
    if crypto_value > 0:
        print(f"  Cartera (ETFs/acciones): {fmt_eur(etf_value):>16}")
        print(f"  Cripto:                  {fmt_eur(crypto_value):>16}")
    else:
        print(f"  Posiciones: {fmt_eur(etf_value):>16}")
    print(f"  Cash:                    {fmt_eur(snapshot.cash_eur):>16}")
    print(f"  TOTAL:                   {fmt_eur(snapshot.total_eur):>16}")
    print()

    print(bar)
    print("  RENTABILIDAD — POSICIONES ACTUALES")
    print(bar)
    up = unrealized_return_user_paid(snapshot, txs)
    ur = unrealized_return(snapshot, txs=txs)
    if up and ur:
        print(f"  Cost basis sin saveback:   {fmt_eur(up['cost_basis']):>14}  ← lo que tú pusiste")
        print(f"  Cost basis con saveback:   {fmt_eur(ur['cost_basis']):>14}  ← averageBuyIn API bruto")
        print(f"  Valor actual:              {fmt_eur(up['value']):>14}")
        print(f"  Plusvalía sobre tu dinero: {fmt_eur(up['pnl_eur']):>14}  ({fmt_pct(up['pnl_pct'])})  ← matchea Excel y TR app")
        print(f"  Plusvalía sobre bruto:     {fmt_eur(ur['pnl_eur']):>14}  ({fmt_pct(ur['pnl_pct'])})  ← saveback incluido como coste")
        if ur["positions_with_cost"] < ur["positions_total"]:
            missing = ur["positions_total"] - ur["positions_with_cost"]
            print(f"  ⚠  {missing} posición(es) sin averageBuyIn; excluida(s).")
    elif ur:
        print(f"  Cost basis (con saveback): {fmt_eur(ur['cost_basis']):>14}")
        print(f"  Valor actual:              {fmt_eur(ur['value']):>14}")
        print(f"  Plusvalía latente:         {fmt_eur(ur['pnl_eur']):>14}  ({fmt_pct(ur['pnl_pct'])})")
    else:
        print("  (sin cost basis disponible — broker no devolvió averageBuyIn)")
    print()

    print(bar)
    print("  RENTABILIDAD — HISTÓRICO COMPLETO (incluye ventas y dividendos)")
    print(bar)

    ytd_start = datetime(now.year, 1, 1, tzinfo=TIMEZONE)
    twelvem_start = now - timedelta(days=365)
    ytd_value = snapshot_value_at(snapshot_history, ytd_start) if snapshot_history else None
    twelvem_value = snapshot_value_at(snapshot_history, twelvem_start) if snapshot_history else None

    for label, mode in (
        ("Mi dinero (saveback como income — default)", "income"),
        ("Incluyendo saveback como aportación", "deposit"),
    ):
        invested = total_invested(txs, bonus_as=mode)
        mwr_all = mwr(txs, snapshot, bonus_as=mode)
        mwr_ytd = mwr(txs, snapshot, bonus_as=mode, start=ytd_start, start_value=ytd_value) if ytd_value else None
        mwr_12m = mwr(txs, snapshot, bonus_as=mode, start=twelvem_start, start_value=twelvem_value) if twelvem_value else None
        print(f"  ── {label} ──")
        print(f"    Aportado neto (BUYs − SELLs):  {fmt_eur(invested):>16}")
        print(f"    MWR all-time:                  {fmt_pct(mwr_all, anual=True):>16}")
        print(f"    MWR YTD ({now.year}):                  {fmt_pct(mwr_ytd, anual=True):>16}")
        print(f"    MWR 12 meses:                  {fmt_pct(mwr_12m, anual=True):>16}")
        print()

    if not ytd_value and not twelvem_value:
        print(f"  ℹ MWR YTD / 12m saldrán n/a hasta que haya un snapshot anterior")
        print(f"    al inicio del periodo. Cada `make insights/sync/portfolio` añade uno.")
        print()

    # Benchmark vs MWR
    if BENCHMARK_ISIN and benchmarks.get(BENCHMARK_ISIN):
        bench_history = benchmarks[BENCHMARK_ISIN]
        first_tx_ts = txs[0].ts if txs else None
        windows = []
        # All-time: from the user's first transaction up to now.
        if first_tx_ts:
            br = benchmark_return(bench_history, first_tx_ts, now)
            mwr_all_inc = mwr(txs, snapshot, bonus_as="income")
            if br and mwr_all_inc is not None:
                windows.append(("all-time", mwr_all_inc, br["annualized_return"]))
        # YTD
        if ytd_value:
            br = benchmark_return(bench_history, ytd_start, now)
            mwr_y = mwr(txs, snapshot, bonus_as="income", start=ytd_start, start_value=ytd_value)
            if br and mwr_y is not None:
                windows.append((f"YTD ({now.year})", mwr_y, br["annualized_return"]))
        # 12m
        if twelvem_value:
            br = benchmark_return(bench_history, twelvem_start, now)
            mwr_12 = mwr(txs, snapshot, bonus_as="income", start=twelvem_start, start_value=twelvem_value)
            if br and mwr_12 is not None:
                windows.append(("12 meses", mwr_12, br["annualized_return"]))

        if windows:
            print(bar)
            print(f"  RENTABILIDAD VS BENCHMARK ({BENCHMARK_LABEL})")
            print(bar)
            print(f"  {'Periodo':<14} {'Tu MWR (income)':>18}  {'Benchmark':>16}  {'Δ vs benchmark':>18}")
            print(f"  {'-'*14} {'-'*18}  {'-'*16}  {'-'*18}")
            for label, mwr_v, bench_v in windows:
                delta_pp = (mwr_v - bench_v) * 100
                marker = " ✓" if delta_pp > 0 else ("  " if delta_pp == 0 else "  ")
                print(
                    f"  {label:<14} "
                    f"{mwr_v*100:>+15.2f} %  "
                    f"{bench_v*100:>+13.2f} %  "
                    f"{delta_pp:>+15.2f} pp{marker}"
                )
            print()
    elif BENCHMARK_ISIN:
        print(f"  ℹ Benchmark {BENCHMARK_ISIN} no disponible (sin histórico de precios).")
        print()

    print(bar)
    print("  APORTACIONES MENSUALES (compras brutas, incluye saveback/regalos)")
    print(bar)
    monthly = monthly_contributions(txs)
    cmp = contribution_vs_average(txs, now.year, now.month)
    if cmp:
        delta_str = "n/a" if cmp["delta_pct"] is None else f"{cmp['delta_pct']*100:+.1f}%"
        print(f"  Este mes ({now.year}-{now.month:02d}):       {fmt_eur(cmp['this_month']):>16}")
        print(f"  Media últimos {cmp['window_months_used']}m:        {fmt_eur(cmp['avg']):>16}")
        print(f"  Δ vs media:              {delta_str:>16}")
    elif monthly:
        last = sorted(monthly.items())[-3:]
        print("  (sin histórico suficiente para comparar; últimos meses con aportación):")
        for (y, m), v in last:
            print(f"    {y}-{m:02d}:  {fmt_eur(v):>16}")
    else:
        print("  (sin aportaciones registradas)")
    print()

    print(bar)
    if CONCENTRATION_LIMITS and CONCENTRATION_THRESHOLD is not None:
        header = f"  CONCENTRACIÓN (% sobre posiciones, límites por activo + threshold global {CONCENTRATION_THRESHOLD*100:.0f}%)"
    elif CONCENTRATION_LIMITS:
        header = f"  CONCENTRACIÓN (% sobre posiciones, alerta solo en activos con límite explícito)"
    elif CONCENTRATION_THRESHOLD is not None:
        header = f"  CONCENTRACIÓN (% sobre posiciones, alerta a >{CONCENTRATION_THRESHOLD*100:.0f}%)"
    else:
        header = f"  CONCENTRACIÓN (% sobre posiciones, sin alertas)"
    print(header)
    print(bar)
    conc = concentration(
        snapshot,
        limits=CONCENTRATION_LIMITS or None,
        default_threshold=CONCENTRATION_THRESHOLD,
    )
    if conc:
        max_bar = 18
        max_pct = max(c["pct"] for c in conc)
        exceeded_count = 0
        # The "(global)" tag is only useful in mixed mode: when some ISINs
        # have explicit limits and others fall back. In pure-global or
        # pure-explicit, suppress.
        has_per_asset = bool(CONCENTRATION_LIMITS)
        for entry in conc:
            pct = entry["pct"]
            bar_len = int(round(pct * max_bar / max_pct))
            bar_str = "█" * bar_len
            title = (entry["title"] or entry["isin"])[:28]
            limit = entry["limit"]
            margin = entry["margin_pp"]
            if limit is None:
                trail = ""
            else:
                has_explicit = has_per_asset and entry["isin"] in CONCENTRATION_LIMITS
                source = " (global)" if (has_per_asset and not has_explicit) else ""
                if entry["exceeded"]:
                    trail = f"  límite {limit*100:>4.0f}%{source}, EXCEDIDO en {abs(margin):>4.1f} pp"
                    exceeded_count += 1
                else:
                    trail = f"  límite {limit*100:>4.0f}%{source}, margen {margin:>+5.1f} pp"
            print(f"  {title:<28} {pct*100:>6.2f}%  {bar_str:<{max_bar}}{trail}")
        print()
        if exceeded_count == 0:
            if any(e["limit"] is not None for e in conc):
                print(f"  ✓ Todas las posiciones dentro de su límite.")
        else:
            print(f"  ⚠ {exceeded_count} posición(es) por encima de su límite individual.")
            print(f"    Considera rebalancear si quieres ajustarlas.")
    print()

    # Per-position attribution
    print(bar)
    print("  ATRIBUCIÓN DE RENDIMIENTO POR POSICIÓN (MWR per-ISIN, modo income)")
    print(bar)
    attr = per_position_attribution(snapshot, txs, bonus_as="income")
    if attr:
        label_w = max((len(p["title"] or "") for p in attr), default=10)
        label_w = min(max(label_w, 14), 32)
        print(f"  {'Activo':<{label_w}} {'valor':>12} {'peso':>6} {'MWR pos.':>11} {'aporta':>11}")
        print(f"  {'-'*label_w} {'-'*12} {'-'*6} {'-'*11} {'-'*11}")
        sum_contrib = 0.0
        for entry in attr:
            title = (entry["title"] or entry["isin"])[:label_w]
            print(
                f"  {title:<{label_w}} "
                f"{fmt_eur(entry['value']):>12} "
                f"{entry['value_pct']*100:>5.1f}% "
                f"{entry['position_mwr']*100:>+9.2f} % "
                f"{entry['contribution_pp']:>+8.2f} pp"
            )
            sum_contrib += entry["contribution_pp"]
        print(f"  {'-'*label_w} {'-'*12} {'-'*6} {'-'*11} {'-'*11}")
        print(f"  {'TOTAL contribuciones':<{label_w}} {' ':>12} {' ':>6} {' ':>11} {sum_contrib:>+8.2f} pp")
        print()
        print(f"  ── Suma ≈ rentabilidad anualizada de las posiciones vivas (no incluye")
        print(f"     ventas pasadas). Para el MWR all-time del portfolio completo, mira")
        print(f"     el bloque 'RENTABILIDAD — HISTÓRICO COMPLETO' arriba.")
    else:
        print("  (sin atribución disponible — posiciones sin flujos suficientes)")
    print()

    # Currency exposure
    if ASSET_CURRENCIES:
        print(bar)
        print("  EXPOSICIÓN POR DIVISA (sobre patrimonio total, incluye cash)")
        print(bar)
        exposure = currency_exposure(snapshot, ASSET_CURRENCIES, cash_currency="EUR")
        if exposure:
            max_bar = 22
            max_pct = max(x["pct"] for x in exposure)
            for entry in exposure:
                pct = entry["pct"]
                bar_len = int(round(pct * max_bar / max_pct)) if max_pct > 0 else 0
                bar_str = "█" * bar_len
                cur = entry["currency"]
                detail = f"{entry['n_positions']} pos." if entry["n_positions"] else "cash"
                print(f"  {cur:<8} {fmt_eur(entry['value_eur']):>14}  ({pct*100:>5.1f}%)  {bar_str:<{max_bar}}  {detail}")
            unknown = [x for x in exposure if x["currency"] == "UNKNOWN"]
            if unknown:
                print()
                print(f"  ⚠ {unknown[0]['n_positions']} posición(es) sin divisa mapeada en `asset_currencies` del config.")
        print()

    if verbose:
        print(bar)
        print("  POR POSICIÓN (--verbose)")
        print(bar)
        cb_user_per_isin = cost_basis_user_paid_per_isin(snapshot, txs)
        label_w = max((len(p.title or "") for p in snapshot.positions), default=10)
        label_w = min(max(label_w, 12), 28)
        print(f"  {'Activo':<{label_w}} {'valor':>12} {'cb propio':>12} {'Δ propio':>9} {'cb bruto':>12} {'Δ bruto':>9}")
        print(f"  {'-'*label_w} {'-'*12} {'-'*12} {'-'*9} {'-'*12} {'-'*9}")
        sum_value = 0.0
        sum_cb_user = 0.0
        sum_cb_tr = 0.0
        for p in sorted(snapshot.positions, key=lambda x: -x.net_value_eur):
            cb_user = cb_user_per_isin.get(p.isin)
            cb_tr = p.cost_basis_eur
            d_user = (p.net_value_eur - cb_user) / cb_user if cb_user and cb_user > 0 else None
            d_tr = (p.net_value_eur - cb_tr) / cb_tr if cb_tr and cb_tr > 0 else None
            title = (p.title or p.isin)[:label_w]
            cb_user_s = fmt_eur(cb_user) if cb_user else "      n/a"
            cb_tr_s = fmt_eur(cb_tr) if cb_tr else "      n/a"
            d_user_s = fmt_pct(d_user) if d_user is not None else "n/a"
            d_tr_s = fmt_pct(d_tr) if d_tr is not None else "n/a"
            print(f"  {title:<{label_w}} {fmt_eur(p.net_value_eur):>12} {cb_user_s:>12} {d_user_s:>9} {cb_tr_s:>12} {d_tr_s:>9}")
            sum_value += p.net_value_eur
            if cb_user: sum_cb_user += cb_user
            if cb_tr: sum_cb_tr += cb_tr
        print(f"  {'-'*label_w} {'-'*12} {'-'*12} {'-'*9} {'-'*12} {'-'*9}")
        d_user_t = (sum_value - sum_cb_user) / sum_cb_user if sum_cb_user > 0 else None
        d_tr_t = (sum_value - sum_cb_tr) / sum_cb_tr if sum_cb_tr > 0 else None
        d_user_total_s = fmt_pct(d_user_t) if d_user_t is not None else 'n/a'
        d_tr_total_s = fmt_pct(d_tr_t) if d_tr_t is not None else 'n/a'
        print(f"  {'TOTAL':<{label_w}} {fmt_eur(sum_value):>12} {fmt_eur(sum_cb_user):>12} {d_user_total_s:>9} {fmt_eur(sum_cb_tr):>12} {d_tr_total_s:>9}")
        print()
