"""--insights: print net worth, MWR and other portfolio insights to stdout."""
from datetime import datetime, timedelta
from typing import Optional

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
        alpha_beta,
        benchmark_monthly_returns,
        benchmark_return,
        concentration,
        contribution_vs_average,
        cost_basis_total as _cb_total,
        cost_basis_user_paid_per_isin,
        currency_exposure,
        max_drawdown,
        monthly_contributions,
        monthly_deposits,
        monthly_returns,
        mwr,
        per_position_attribution,
        savings_projection,
        savings_ratio,
        sharpe_ratio,
        total_invested,
        tracking_error_annualized,
        twr,
        unrealized_return,
        unrealized_return_user_paid,
        volatility_annualized,
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

    def _round_up_nice(x: float) -> float:
        """Round x up to a 'nice' round number (1, 1.5, 2, 2.5, 3, 5, 7.5 × 10^k)."""
        import math
        if x <= 0:
            return 0.0
        mag = 10 ** math.floor(math.log10(x))
        for n in (1, 1.5, 2, 2.5, 3, 5, 7.5, 10):
            cand = n * mag
            if cand >= x:
                return cand
        return 10 * mag

    def _compute_targets(current: float) -> list[float]:
        """Wealth milestones based on current total. 2x / 4x / 6x / 10x rounded."""
        if current <= 0:
            return [10_000.0, 25_000.0, 50_000.0, 100_000.0]
        out: list[float] = []
        for m in (2, 4, 6, 10):
            t = _round_up_nice(current * m)
            if not out or t > out[-1]:
                out.append(t)
        return out

    # Shared series used by the projection, savings ratio, and risk blocks.
    monthly = monthly_contributions(txs)
    deposits_by_month = monthly_deposits(txs)
    port_monthly_returns = monthly_returns(txs, snapshot_history, snapshot)

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

    mwr_all_income: Optional[float] = None
    for label, mode in (
        ("Mi dinero (saveback como income — default)", "income"),
        ("Incluyendo saveback como aportación", "deposit"),
    ):
        invested = total_invested(txs, bonus_as=mode)
        mwr_all = mwr(txs, snapshot, bonus_as=mode)
        if mode == "income":
            mwr_all_income = mwr_all
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

    # Benchmark vs MWR + TWR. MWR refleja "qué hizo MI dinero" (incluye
    # timing); TWR refleja "qué hizo la estrategia" (sin timing) — esta es
    # la comparación honesta contra benchmark.
    if BENCHMARK_ISIN and benchmarks.get(BENCHMARK_ISIN):
        bench_history = benchmarks[BENCHMARK_ISIN]
        first_tx_ts = txs[0].ts if txs else None
        windows = []
        # All-time
        if first_tx_ts:
            br = benchmark_return(bench_history, first_tx_ts, now)
            mwr_all_inc = mwr(txs, snapshot, bonus_as="income")
            twr_all = twr(txs, snapshot_history, snapshot)
            if br:
                windows.append(("all-time", mwr_all_inc, twr_all, br["annualized_return"]))
        # YTD
        if ytd_value:
            br = benchmark_return(bench_history, ytd_start, now)
            mwr_y = mwr(txs, snapshot, bonus_as="income", start=ytd_start, start_value=ytd_value)
            twr_y = twr(txs, snapshot_history, snapshot, start=ytd_start)
            if br:
                windows.append((f"YTD ({now.year})", mwr_y, twr_y, br["annualized_return"]))
        # 12m
        if twelvem_value:
            br = benchmark_return(bench_history, twelvem_start, now)
            mwr_12 = mwr(txs, snapshot, bonus_as="income", start=twelvem_start, start_value=twelvem_value)
            twr_12 = twr(txs, snapshot_history, snapshot, start=twelvem_start)
            if br:
                windows.append(("12 meses", mwr_12, twr_12, br["annualized_return"]))

        if windows:
            print(bar)
            print(f"  RENTABILIDAD VS BENCHMARK ({BENCHMARK_LABEL})")
            print(bar)
            print(f"  {'Periodo':<14} {'Tu MWR':>10} {'Tu TWR':>10}  {'Benchmark':>11}  {'Δ MWR':>10}  {'Δ TWR':>10}")
            print(f"  {'-'*14} {'-'*10} {'-'*10}  {'-'*11}  {'-'*10}  {'-'*10}")

            def _fmt_pct_short(x):
                return f"{x*100:>+8.2f} %" if x is not None else f"{'n/a':>10}"

            def _fmt_delta(port, bench):
                if port is None:
                    return f"{'n/a':>10}"
                d = (port - bench) * 100
                sign = "✓" if d > 0 else (" " if d == 0 else " ")
                return f"{d:>+7.2f} pp{sign}"

            for label, mwr_v, twr_v, bench_v in windows:
                print(
                    f"  {label:<14} "
                    f"{_fmt_pct_short(mwr_v):>10} {_fmt_pct_short(twr_v):>10}  "
                    f"{bench_v*100:>+8.2f} %  "
                    f"{_fmt_delta(mwr_v, bench_v):>10}  {_fmt_delta(twr_v, bench_v):>10}"
                )
            print()
            print(f"  ℹ TWR es el indicador honesto vs benchmark (sin sesgo de timing).")
            print(f"    MWR alto + TWR similar = vas mejor que parece; MWR bajo + TWR alto = timing en contra.")
            print()
    elif BENCHMARK_ISIN:
        print(f"  ℹ Benchmark {BENCHMARK_ISIN} no disponible (sin histórico de precios).")
        print()

    # ── RIESGO Y EFICIENCIA ────────────────────────────────────────────
    bench_monthly = (
        benchmark_monthly_returns(benchmarks[BENCHMARK_ISIN])
        if BENCHMARK_ISIN and benchmarks.get(BENCHMARK_ISIN) else []
    )
    n_months = len(port_monthly_returns)
    print(bar)
    print("  RIESGO Y EFICIENCIA")
    print(bar)
    if n_months < 2:
        print(f"  ℹ Se necesitan ≥2 meses de snapshots para estas métricas (tienes {n_months}).")
        print(f"    Cada `make insights/sync/portfolio` añade un snapshot. Vuelve en unos meses.")
    else:
        # Always-on (need ≥2 months)
        twr_all = twr(txs, snapshot_history, snapshot)
        mwr_all = mwr_all_income
        if twr_all is not None:
            mwr_str = f"{mwr_all*100:+.2f} %" if mwr_all is not None else "n/a"
            print(
                f"  TWR all-time:               {fmt_pct(twr_all, anual=True):>14}"
                f"   (vs MWR {mwr_str} — la diferencia es timing de aportaciones)"
            )
        mdd = max_drawdown(port_monthly_returns)
        if mdd:
            recov = (
                f"recuperado en {mdd['days_to_recovery']} d"
                if mdd["recovery_ts"] else "sin recuperar aún"
            )
            print(
                f"  Max drawdown (TWR):         {-mdd['max_dd_pct']*100:>13.2f} %"
                f"   ({mdd['peak_ts'].strftime('%Y-%m')} → {mdd['trough_ts'].strftime('%Y-%m')}, {recov})"
            )

        # Gated: ≥6 months for vol/Sharpe/TE/alpha (with caveat <12, none <6)
        if n_months >= 6:
            caveat = f"  ℹ N={n_months} meses (ruidoso, ideal ≥24)" if n_months < 24 else ""
            vol = volatility_annualized(port_monthly_returns)
            sharpe = sharpe_ratio(twr_all, vol, risk_free=0.02)
            print()
            print(f"  ── Volatilidad y eficiencia ──{caveat}")
            if vol is not None:
                print(f"  Volatilidad anual:          {vol*100:>13.2f} %")
            if sharpe is not None:
                print(f"  Sharpe (rf=2 %):            {sharpe:>13.2f}     (>1 bueno, >2 excelente)")

            if bench_monthly:
                te = tracking_error_annualized(port_monthly_returns, bench_monthly)
                ab = alpha_beta(port_monthly_returns, bench_monthly, min_months=6)
                # Gate alpha/beta on data quality. With <24 months or extreme
                # beta (|β|>1.8), the OLS regression is dominated by 1-2
                # outlier months and the numbers mislead more than inform.
                ab_reliable = (
                    ab is not None
                    and ab["n_months"] >= 24
                    and abs(ab["beta"]) <= 1.8
                )
                if te or ab_reliable:
                    print()
                    print(f"  ── Vs benchmark ({BENCHMARK_LABEL}) ──")
                if te:
                    print(f"  Tracking error anual:       {te['tracking_error']*100:>13.2f} %"
                          f"   (cuánto te separas del benchmark)")
                if ab_reliable:
                    print(f"  Beta:                       {ab['beta']:>13.2f}     (1.0 = igual riesgo que el bench)")
                    sign_word = "encima" if ab["alpha_annual"] >= 0 else "debajo"
                    print(f"  Alpha anual:                {ab['alpha_annual']*100:>+12.2f} pp"
                          f"   ({sign_word} de lo que tu beta sobre el bench predice)")
                elif ab is not None:
                    reason = []
                    if ab["n_months"] < 24:
                        reason.append(f"N={ab['n_months']}<24")
                    if abs(ab["beta"]) > 1.8:
                        reason.append(f"|β|={abs(ab['beta']):.1f}>1.8 (poco fiable con esta N)")
                    print(f"  Alpha / Beta:               ocultos ({', '.join(reason)})")
        else:
            print()
            print(f"  ℹ Volatilidad / Sharpe / Tracking error / Alpha:")
            print(f"    requieren ≥6 meses de retornos mensuales (tienes {n_months}).")
            print(f"    Saldrán automáticamente cuando haya datos suficientes.")
    print()

    print(bar)
    print("  APORTACIONES MENSUALES (compras brutas, incluye saveback/regalos)")
    print(bar)
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

    # ── RATIO DE AHORRO ────────────────────────────────────────────────
    sr = savings_ratio(monthly, deposits_by_month, now=now, months_window=6)
    if sr:
        print(bar)
        print(f"  RATIO DE AHORRO (últimos {sr['months_used']} meses)")
        print(bar)
        ratio_pct = sr["ratio"] * 100
        cash_pct = 100 - ratio_pct
        print(f"  Depositado neto:          {fmt_eur(sr['deposited']):>14}")
        print(f"  Invertido (compras):      {fmt_eur(sr['invested']):>14}   ({ratio_pct:.1f} %)")
        print(f"  Acumulado en cash:        {fmt_eur(sr['cash_pile']):>14}   ({cash_pct:.1f} %)")
        if sr["cash_pile"] > 0 and ratio_pct < 70:
            cost_op = sr["cash_pile"] * 0.07
            print()
            print(f"  ⚠ Estás reteniendo {cash_pct:.0f} % como cash. Coste de oportunidad")
            print(f"    aprox. (a 7 % anual sobre el cash acumulado): {fmt_eur(cost_op)}/año.")
        print()

    print(bar)
    print("  PROYECCIÓN DE PATRIMONIO (compuesto + cash savings)")
    print(bar)

    # Last completed month → contribution assumption.
    prev_y, prev_m = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
    last_month_contrib = monthly.get((prev_y, prev_m), 0.0)

    # Last 3 completed months → cash deposit (salary) assumption.
    deposit_window: list[float] = []
    y, m = now.year, now.month
    for _ in range(3):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
        deposit_window.append(deposits_by_month.get((y, m), 0.0))
    avg_deposit_3m = sum(deposit_window) / 3 if deposit_window else 0.0

    # All-time MWR → market growth assumption (fallback 6 % if unavailable).
    if mwr_all_income is not None:
        annual_return = mwr_all_income
        return_source = f"tu MWR all-time ({annual_return*100:.2f} %)"
    else:
        annual_return = 0.06
        return_source = "fallback 6 % (MWR no calculable aún)"

    targets = _compute_targets(snapshot.total_eur)
    proj = savings_projection(
        snapshot.cash_eur,
        snapshot.positions_value_eur,
        last_month_contrib,
        avg_deposit_3m,
        annual_return,
        targets,
        now=now,
    )

    print(f"  Patrimonio actual:          {fmt_eur(proj['current']):>14}")
    print(f"    · Cartera (compone):      {fmt_eur(proj['current_positions']):>14}")
    print(f"    · Cash:                   {fmt_eur(proj['current_cash']):>14}")
    print()
    print(
        f"  Aportación mensual:         {fmt_eur(proj['monthly_contribution']):>14}"
        f"   ({prev_y}-{prev_m:02d}, último mes completo)"
    )
    print(
        f"  Ingresos a TR (cash):       {fmt_eur(proj['monthly_deposit']):>14}"
        f"   (media DEPOSITs últimos 3 m)"
    )
    leftover = proj["monthly_cash_flow"]
    leftover_label = "→ se acumula como cash" if leftover >= 0 else "→ se invierte desde cash"
    print(f"  Δ cash mensual:             {fmt_eur(leftover):>14}   {leftover_label}")
    print(f"  Retorno anual asumido:      {return_source}")
    # Alt scenario: invest 80 % of average deposits. Useful when the
    # current contribution is well below that (i.e. cash is piling up).
    alt_pct = 0.80
    alt_contrib = avg_deposit_3m * alt_pct
    show_alt = alt_contrib > last_month_contrib + 1.0 and avg_deposit_3m > 0
    proj_alt = None
    if show_alt:
        proj_alt = savings_projection(
            snapshot.cash_eur,
            snapshot.positions_value_eur,
            alt_contrib,
            avg_deposit_3m,
            annual_return,
            targets,
            now=now,
        )

    print()
    if show_alt:
        print(
            f"  {'Objetivo':<12} {'Actual':>22}     "
            f"{'Si inviertes ' + str(int(alt_pct*100)) + ' % (' + fmt_eur(alt_contrib).strip() + '/m)':>34}"
        )
        print(f"  {'-'*12} {'-'*22}     {'-'*34}")
    else:
        print(f"  {'Objetivo':<12} {'Falta':>14}  {'ETA':>26}")
        print(f"  {'-'*12} {'-'*14}  {'-'*26}")

    def _fmt_eta(entry):
        if entry["status"] == "reached":
            return "ya alcanzado"
        if entry["status"] == "non_reachable":
            return ">100 a"
        months = entry["months"]
        eta = entry["eta"]
        if months < 12:
            when = f"~{months:.1f} m"
        else:
            when = f"~{months/12:.1f} a ({months:.0f} m)"
        eta_s = eta.strftime("%Y-%m") if eta else "?"
        return f"{when} → {eta_s}"

    for i, entry in enumerate(proj["targets"]):
        target_s = fmt_eur(entry["target"])
        actual_eta = _fmt_eta(entry)
        if show_alt:
            alt_entry = proj_alt["targets"][i]
            alt_eta = _fmt_eta(alt_entry)
            if entry["months"] is not None and alt_entry["months"] is not None:
                diff_m = entry["months"] - alt_entry["months"]
                diff_str = f"  (-{diff_m:.0f} m)" if diff_m > 0 else ""
            else:
                diff_str = ""
            print(f"  {target_s:<12} {actual_eta:>22}     {alt_eta + diff_str:>34}")
        else:
            print(f"  {target_s:<12} {fmt_eur(entry['remaining']):>14}  {actual_eta:>26}")
    print()
    if show_alt:
        cash_pct_now = (1 - last_month_contrib / avg_deposit_3m) * 100 if avg_deposit_3m > 0 else 0
        print(f"  ℹ Ahora inviertes {last_month_contrib/avg_deposit_3m*100:.0f} % de tus ingresos a TR;")
        print(f"    si subieras al 80 %, las ETAs lejanas se acortan visiblemente. El cash extra")
        print(f"    de {fmt_eur(avg_deposit_3m - last_month_contrib).strip()}/m vale más invertido que parado.")
    else:
        print("  ℹ Modelo: cartera compone al retorno anual; cash crece linealmente")
        print("    con (ingresos − aportación). Asume que el ritmo del último mes")
        print("    de compras y la media de 3 m de transferencias se mantienen.")
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
