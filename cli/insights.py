"""--insights: print net worth, MWR and other portfolio insights to stdout."""
from datetime import datetime, timedelta
from typing import Optional

from tr_sync import (
    ASSET_CURRENCIES,
    BENCHMARK_ISIN,
    BENCHMARK_LABEL,
    CASH_TARGET_MAX_SPEC,
    CASH_TARGET_MIN_SPEC,
    CONCENTRATION_LIMITS,
    CONCENTRATION_THRESHOLD,
    CRYPTO_ISINS,
    MONTHLY_EXPENSES_EUR,
    MONTHLY_INCOME_EUR,
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
        log.info("Feature 'insights' disabled (config or broker).")
        return

    from core.metrics import (
        alpha_beta,
        benchmark_monthly_returns,
        benchmark_return,
        cash_excess,
        cash_runway,
        concentration,
        contribution_vs_average,
        cost_basis_total as _cb_total,
        cost_basis_user_paid_per_isin,
        currency_exposure,
        max_drawdown,
        monthly_contributions,
        monthly_deposits,
        monthly_returns,
        monthly_withdrawals,
        mwr,
        per_position_attribution,
        savings_efficiency,
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
    log.info(f"   {len(txs)} transactions, {len(snapshot.positions)} positions.")

    # Persist snapshot + load history for MWR YTD/12m.
    snapshot_history: list[dict] = []
    try:
        spreadsheet = open_spreadsheet()
        store = _make_snapshot_store(spreadsheet)
        store.append(snapshot, _cb_total(snapshot))
        snapshot_history = store.load_history()
        log.info(f"   snapshot saved. history: {len(snapshot_history)} entries.\n")
    except Exception as e:
        log.warning(f"   ⚠ could not persist/load snapshots ({e}); MWR YTD/12m omitted.\n")

    now = datetime.now(tz=TIMEZONE)

    bar = "═" * 64

    def fmt_pct(x, *, anual=False, sign=True):
        if x is None:
            return "n/a"
        s = f"{x*100:+.2f}" if sign else f"{x*100:.2f}"
        return f"{s} %" + (" annual" if anual else "")

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
    print("  CURRENT NET WORTH")
    print(bar)
    if crypto_value > 0:
        print(f"  Portfolio (ETFs/stocks): {fmt_eur(etf_value):>16}")
        print(f"  Crypto:                  {fmt_eur(crypto_value):>16}")
    else:
        print(f"  Positions: {fmt_eur(etf_value):>16}")
    print(f"  Cash:                    {fmt_eur(snapshot.cash_eur):>16}")
    print(f"  TOTAL:                   {fmt_eur(snapshot.total_eur):>16}")
    print()

    print(bar)
    print("  RETURN — CURRENT POSITIONS")
    print(bar)
    up = unrealized_return_user_paid(snapshot, txs)
    ur = unrealized_return(snapshot, txs=txs)
    if up and ur:
        print(f"  Cost basis without saveback: {fmt_eur(up['cost_basis']):>14}  ← what you actually put in")
        print(f"  Cost basis with saveback:    {fmt_eur(ur['cost_basis']):>14}  ← raw averageBuyIn API")
        print(f"  Current value:               {fmt_eur(up['value']):>14}")
        print(f"  P&L on your money:           {fmt_eur(up['pnl_eur']):>14}  ({fmt_pct(up['pnl_pct'])})  ← matches Excel and the TR app")
        print(f"  P&L on gross cost:           {fmt_eur(ur['pnl_eur']):>14}  ({fmt_pct(ur['pnl_pct'])})  ← saveback counted as cost")
        if ur["positions_with_cost"] < ur["positions_total"]:
            missing = ur["positions_total"] - ur["positions_with_cost"]
            print(f"  ⚠  {missing} position(s) without averageBuyIn; excluded.")
    elif ur:
        print(f"  Cost basis (with saveback):  {fmt_eur(ur['cost_basis']):>14}")
        print(f"  Current value:               {fmt_eur(ur['value']):>14}")
        print(f"  Unrealized P&L:              {fmt_eur(ur['pnl_eur']):>14}  ({fmt_pct(ur['pnl_pct'])})")
    else:
        print("  (no cost basis available — broker did not return averageBuyIn)")
    print()

    print(bar)
    print("  RETURN — FULL HISTORY (includes sales and dividends)")
    print(bar)

    ytd_start = datetime(now.year, 1, 1, tzinfo=TIMEZONE)
    twelvem_start = now - timedelta(days=365)
    ytd_value = snapshot_value_at(snapshot_history, ytd_start) if snapshot_history else None
    twelvem_value = snapshot_value_at(snapshot_history, twelvem_start) if snapshot_history else None

    mwr_all_income: Optional[float] = None
    for label, mode in (
        ("My money (saveback as income — default)", "income"),
        ("Including saveback as a contribution", "deposit"),
    ):
        invested = total_invested(txs, bonus_as=mode)
        mwr_all = mwr(txs, snapshot, bonus_as=mode)
        if mode == "income":
            mwr_all_income = mwr_all
        mwr_ytd = mwr(txs, snapshot, bonus_as=mode, start=ytd_start, start_value=ytd_value) if ytd_value else None
        mwr_12m = mwr(txs, snapshot, bonus_as=mode, start=twelvem_start, start_value=twelvem_value) if twelvem_value else None
        print(f"  ── {label} ──")
        print(f"    Net contributed (BUYs − SELLs): {fmt_eur(invested):>16}")
        print(f"    MWR all-time:                   {fmt_pct(mwr_all, anual=True):>16}")
        print(f"    MWR YTD ({now.year}):                   {fmt_pct(mwr_ytd, anual=True):>16}")
        print(f"    MWR 12 months:                  {fmt_pct(mwr_12m, anual=True):>16}")
        print()

    if not ytd_value and not twelvem_value:
        print(f"  ℹ MWR YTD / 12m will be n/a until there is a snapshot before")
        print(f"    the start of the period. Every `make insights/sync/portfolio` adds one.")
        print()

    # Benchmark vs MWR + TWR. MWR reflects "what MY money did" (includes
    # timing); TWR reflects "what the strategy did" (no timing) — that's
    # the honest comparison against the benchmark.
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
                windows.append(("12 months", mwr_12, twr_12, br["annualized_return"]))

        if windows:
            print(bar)
            print(f"  RETURN VS BENCHMARK ({BENCHMARK_LABEL})")
            print(bar)
            print(f"  {'Period':<14} {'Your MWR':>10} {'Your TWR':>10}  {'Benchmark':>11}  {'Δ MWR':>10}  {'Δ TWR':>10}")
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
            print(f"  ℹ TWR is the honest indicator vs benchmark (no timing bias).")
            print(f"    High MWR + similar TWR = doing better than it looks; low MWR + high TWR = timing against you.")
            print()
    elif BENCHMARK_ISIN:
        print(f"  ℹ Benchmark {BENCHMARK_ISIN} not available (no price history).")
        print()

    # ── RISK AND EFFICIENCY ─────────────────────────────────────────────
    bench_monthly = (
        benchmark_monthly_returns(benchmarks[BENCHMARK_ISIN])
        if BENCHMARK_ISIN and benchmarks.get(BENCHMARK_ISIN) else []
    )
    n_months = len(port_monthly_returns)
    print(bar)
    print("  RISK AND EFFICIENCY")
    print(bar)
    if n_months < 2:
        print(f"  ℹ At least 2 months of snapshots are required for these metrics (you have {n_months}).")
        print(f"    Each `make insights/sync/portfolio` adds a snapshot. Come back in a few months.")
    else:
        # Always-on (need ≥2 months)
        twr_all = twr(txs, snapshot_history, snapshot)
        mwr_all = mwr_all_income
        if twr_all is not None:
            mwr_str = f"{mwr_all*100:+.2f} %" if mwr_all is not None else "n/a"
            print(
                f"  TWR all-time:               {fmt_pct(twr_all, anual=True):>14}"
                f"   (vs MWR {mwr_str} — the difference is contribution timing)"
            )
        mdd = max_drawdown(port_monthly_returns)
        if mdd:
            recov = (
                f"recovered in {mdd['days_to_recovery']} d"
                if mdd["recovery_ts"] else "not yet recovered"
            )
            print(
                f"  Max drawdown (TWR):         {-mdd['max_dd_pct']*100:>13.2f} %"
                f"   ({mdd['peak_ts'].strftime('%Y-%m')} → {mdd['trough_ts'].strftime('%Y-%m')}, {recov})"
            )

        # Gated: ≥6 months for vol/Sharpe/TE/alpha (with caveat <12, none <6)
        if n_months >= 6:
            caveat = f"  ℹ N={n_months} months (noisy, ideally ≥24)" if n_months < 24 else ""
            vol = volatility_annualized(port_monthly_returns)
            sharpe = sharpe_ratio(twr_all, vol, risk_free=0.02)
            print()
            print(f"  ── Volatility and efficiency ──{caveat}")
            if vol is not None:
                print(f"  Annual volatility:          {vol*100:>13.2f} %")
            if sharpe is not None:
                print(f"  Sharpe (rf=2 %):            {sharpe:>13.2f}     (>1 good, >2 excellent)")

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
                    print(f"  Annual tracking error:      {te['tracking_error']*100:>13.2f} %"
                          f"   (how much you deviate from the benchmark)")
                if ab_reliable:
                    print(f"  Beta:                       {ab['beta']:>13.2f}     (1.0 = same risk as the bench)")
                    sign_word = "above" if ab["alpha_annual"] >= 0 else "below"
                    print(f"  Annual alpha:               {ab['alpha_annual']*100:>+12.2f} pp"
                          f"   ({sign_word} what your beta on the bench predicts)")
                elif ab is not None:
                    reason = []
                    if ab["n_months"] < 24:
                        reason.append(f"N={ab['n_months']}<24")
                    if abs(ab["beta"]) > 1.8:
                        reason.append(f"|β|={abs(ab['beta']):.1f}>1.8 (unreliable with this N)")
                    print(f"  Alpha / Beta:               hidden ({', '.join(reason)})")
        else:
            print()
            print(f"  ℹ Volatility / Sharpe / Tracking error / Alpha:")
            print(f"    require ≥6 months of monthly returns (you have {n_months}).")
            print(f"    They'll show up automatically once enough data is available.")
    print()

    print(bar)
    print("  MONTHLY CONTRIBUTIONS (gross BUYs, includes saveback/gifts)")
    print(bar)
    cmp = contribution_vs_average(txs, now.year, now.month)
    if cmp:
        delta_str = "n/a" if cmp["delta_pct"] is None else f"{cmp['delta_pct']*100:+.1f}%"
        print(f"  This month ({now.year}-{now.month:02d}):     {fmt_eur(cmp['this_month']):>16}")
        print(f"  Avg last {cmp['window_months_used']}m:           {fmt_eur(cmp['avg']):>16}")
        print(f"  Δ vs avg:                {delta_str:>16}")
    elif monthly:
        last = sorted(monthly.items())[-3:]
        print("  (insufficient history to compare; last months with contributions):")
        for (y, m), v in last:
            print(f"    {y}-{m:02d}:  {fmt_eur(v):>16}")
    else:
        print("  (no contributions recorded)")
    print()

    # ── SAVINGS RATIO ──────────────────────────────────────────────────
    sr = savings_ratio(monthly, deposits_by_month, now=now, months_window=6)
    if sr:
        print(bar)
        print(f"  SAVINGS RATIO (last {sr['months_used']} months)")
        print(bar)
        ratio_pct = sr["ratio"] * 100
        cash_pct = 100 - ratio_pct
        print(f"  Net deposited:            {fmt_eur(sr['deposited']):>14}")
        print(f"  Invested (BUYs):          {fmt_eur(sr['invested']):>14}   ({ratio_pct:.1f} %)")
        print(f"  Accumulated as cash:      {fmt_eur(sr['cash_pile']):>14}   ({cash_pct:.1f} %)")
        if sr["cash_pile"] > 0 and ratio_pct < 70:
            cost_op = sr["cash_pile"] * 0.07
            print()
            print(f"  ⚠ You're holding {cash_pct:.0f} % as cash. Approximate opportunity")
            print(f"    cost (at 7 % annual on the accumulated cash): {fmt_eur(cost_op)}/year.")
        print()

    # ── SAVINGS EFFICIENCY ─────────────────────────────────────────────
    # Share of total income that ends up invested. Detects under-investing
    # when cash accumulates without flowing into the portfolio. Income
    # defaults to gross broker DEPOSITs (incoming transfers); override via
    # `monthly_income_eur` when income arrives outside the broker.
    income_value: Optional[float] = None
    income_source: Optional[str] = None
    if MONTHLY_INCOME_EUR and MONTHLY_INCOME_EUR > 0:
        income_value = MONTHLY_INCOME_EUR
        income_source = "config.monthly_income_eur"
    else:
        # 6m gross deposits (no withdrawal netting — we want inflow, not
        # net flow). Skip the in-progress current month. Divide by the
        # full window length, without filtering zero months: a month with
        # no transfer is real data and must not inflate the mean.
        gross_deposits = monthly_deposits(txs, net_of_withdrawals=False)
        dep_window: list[float] = []
        dy, dm = now.year, now.month
        for _ in range(6):
            dm -= 1
            if dm == 0:
                dm = 12
                dy -= 1
            dep_window.append(gross_deposits.get((dy, dm), 0.0))
        if dep_window and sum(dep_window) > 0:
            income_value = sum(dep_window) / len(dep_window)
            income_source = f"avg income last {len(dep_window)} m"

    if income_value and income_value > 0:
        # 6-month average of invested contributions (last 6 completed months).
        eff_window: list[float] = []
        ey, em = now.year, now.month
        for _ in range(6):
            em -= 1
            if em == 0:
                em = 12
                ey -= 1
            eff_window.append(monthly.get((ey, em), 0.0))
        avg_invested = sum(eff_window) / len(eff_window) if eff_window else 0.0
        eff = savings_efficiency(avg_invested, income_value)
        if eff:
            print(bar)
            print(f"  SAVINGS EFFICIENCY (invested / income, 6m average)")
            print(bar)
            ratio_pct = eff["ratio"] * 100
            print(f"  Monthly income:           {fmt_eur(eff['income']):>14}   ({income_source})")
            print(f"  Average invested:         {fmt_eur(eff['invested']):>14}")
            print(f"  Efficiency:               {ratio_pct:>12.1f} %")
            if ratio_pct >= 50:
                verdict = "✓ ≥50 %: very high efficiency (little room to push higher)"
            elif ratio_pct >= 30:
                verdict = "✓ 30-50 %: healthy zone for someone aiming at FIRE"
            elif ratio_pct >= 15:
                verdict = "○ 15-30 %: decent saving; check whether the rest stays as cash for no reason"
            else:
                verdict = "⚠ <15 %: you're under-investing relative to your income"
            print(f"  {verdict}")
            # Breakdown of where the income goes. Sums exactly to income
            # when auto-derived (gross broker deposits): the residual after
            # expenses and investment is the cash that piles up. With a
            # config override, income may include money that never reaches
            # the broker, so the breakdown is informative, not exact.
            wd_for_eff = monthly_withdrawals(txs)
            exp_window: list[float] = []
            gy, gm = now.year, now.month
            for _ in range(6):
                gm -= 1
                if gm == 0:
                    gm = 12
                    gy -= 1
                exp_window.append(wd_for_eff.get((gy, gm), 0.0))
            avg_expense = sum(exp_window) / len(exp_window) if exp_window else 0.0
            cash_residual = eff["income"] - avg_expense - eff["invested"]
            print()
            if income_source.startswith("config"):
                spent_or_kept = eff["income"] - eff["invested"]
                print(f"  ℹ Of the {fmt_eur(eff['income']).strip()}/m of declared income:")
                print(f"      · {fmt_eur(eff['invested']):>12}/m → investment")
                print(f"      · {fmt_eur(spent_or_kept):>12}/m → spending + cash (TR + other accounts)")
            else:
                print(f"  ℹ Of the {fmt_eur(eff['income']).strip()}/m flowing into TR:")
                print(f"      · {fmt_eur(avg_expense):>12}/m → expenses (TR)")
                print(f"      · {fmt_eur(eff['invested']):>12}/m → investment")
                print(f"      · {fmt_eur(cash_residual):>12}/m → accumulates as cash")
            print()

    # ── CASH RUNWAY ────────────────────────────────────────────────────
    # Pick a monthly-expense source: explicit config override wins; otherwise
    # fall back to a 6-month average of broker WITHDRAWALs (broker-visible
    # spending only).
    expense_value: Optional[float] = None
    expense_source: Optional[str] = None
    expense_is_tr_only = False
    if MONTHLY_EXPENSES_EUR is not None and MONTHLY_EXPENSES_EUR > 0:
        expense_value = MONTHLY_EXPENSES_EUR
        expense_source = "config.monthly_expenses_eur"
    else:
        wd = monthly_withdrawals(txs)
        # Use the last 6 *completed* months (skip the in-progress current month).
        window: list[float] = []
        wy, wm = now.year, now.month
        for _ in range(6):
            wm -= 1
            if wm == 0:
                wm = 12
                wy -= 1
            window.append(wd.get((wy, wm), 0.0))
        nonzero = [v for v in window if v > 0]
        if nonzero:
            expense_value = sum(nonzero) / len(nonzero)
            expense_source = f"avg expenses last {len(nonzero)} m (TR)"
            expense_is_tr_only = True

    runway = cash_runway(snapshot.cash_eur, expense_value) if expense_value else None
    if runway:
        print(bar)
        print("  CASH RUNWAY (months covered by cash alone, without touching investments)")
        print(bar)
        print(f"  Available cash:           {fmt_eur(runway['cash']):>14}")
        print(f"  Assumed monthly expense:  {fmt_eur(runway['monthly_expense']):>14}   ({expense_source})")
        months = runway["months"]
        print(f"  Runway:                   {months:>12.1f} m")
        if months >= 24:
            verdict = "⚠ >24 m: likely excess liquidity, consider investing part of it"
        elif months >= 12:
            verdict = "⚠ 12-24 m: comfortable, but increasing opportunity cost"
        elif months >= 6:
            verdict = "✓ 6-12 m: comfortable zone (emergency + buffer)"
        elif months >= 3:
            verdict = "✓ 3-6 m: basic emergency cushion covered"
        else:
            verdict = "⚠ <3 m: little margin for unexpected events"
        print(f"  {verdict}")
        if expense_is_tr_only:
            print()
            print(f"  ℹ Estimated only with spending that flows through TR (card, bizum, outgoing transfers).")
            print(f"    If you spend from another account, declare your real monthly expense:")
            print(f"      python tr_sync.py config set monthly_expenses_eur <€>")
            print(f"    (or run `make reconfigure` to use the wizard)")
        print()

    # ── CASH TARGET ────────────────────────────────────────────────────
    # Compare current cash against the configured target range. Each bound
    # may be a scalar or a {date: value} schedule resolved to the entry
    # whose date is <= today (useful for stepping the ceiling every N
    # months without editing config by hand).
    from core.utils import resolve_dated_schedule
    cash_min = resolve_dated_schedule(CASH_TARGET_MIN_SPEC, now)
    cash_max = resolve_dated_schedule(CASH_TARGET_MAX_SPEC, now)
    if cash_min is not None or cash_max is not None:
        ce = cash_excess(snapshot.cash_eur, min_eur=cash_min, max_eur=cash_max)
        print(bar)
        print("  CASH TARGET (current cash vs range defined in config)")
        print(bar)
        print(f"  Current cash:             {fmt_eur(snapshot.cash_eur):>14}")
        # Show "(schedule)" when the active value comes from a dated schedule
        # so it's obvious why the target is what it is on a given run.
        min_tag = "  (schedule)" if isinstance(CASH_TARGET_MIN_SPEC, dict) else ""
        max_tag = "  (schedule)" if isinstance(CASH_TARGET_MAX_SPEC, dict) else ""
        if ce["min_eur"] is not None:
            print(f"  Minimum:                  {fmt_eur(ce['min_eur']):>14}{min_tag}")
        if ce["max_eur"] is not None:
            print(f"  Maximum:                  {fmt_eur(ce['max_eur']):>14}{max_tag}")
        if ce["status"] == "over":
            print(f"  Structural surplus:       {fmt_eur(ce['gap_eur']):>14}")
            print(f"  ⚠ You're {fmt_eur(ce['gap_eur']).strip()} above the maximum. That excess")
            print(f"    is sitting idle: at 7 % annual you'd lose ~{fmt_eur(ce['gap_eur'] * 0.07).strip()}/year in opportunity")
            print(f"    cost. Consider moving it into the portfolio.")
        elif ce["status"] == "under":
            print(f"  Shortfall:                {fmt_eur(ce['gap_eur']):>14}")
            print(f"  ⚠ You're {fmt_eur(abs(ce['gap_eur'])).strip()} below the minimum. Consider")
            print(f"    increasing cash before investing the surplus further.")
        else:
            print(f"  ✓ Within the target range.")
        print()

    print(bar)
    print("  WEALTH PROJECTION (compounded + cash savings)")
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
        return_source = f"your all-time MWR ({annual_return*100:.2f} %)"
    else:
        annual_return = 0.06
        return_source = "fallback 6 % (MWR not computable yet)"

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

    print(f"  Current net worth:          {fmt_eur(proj['current']):>14}")
    print(f"    · Portfolio (compounds):  {fmt_eur(proj['current_positions']):>14}")
    print(f"    · Cash:                   {fmt_eur(proj['current_cash']):>14}")
    print()
    print(
        f"  Monthly contribution:       {fmt_eur(proj['monthly_contribution']):>14}"
        f"   ({prev_y}-{prev_m:02d}, last completed month)"
    )
    print(
        f"  Net cash flowing into TR:   {fmt_eur(proj['monthly_deposit']):>14}"
        f"   (DEPOSITs − expenses, 3m avg)"
    )
    leftover = proj["monthly_cash_flow"]
    leftover_label = "→ accumulates as cash" if leftover >= 0 else "→ invested from cash"
    print(f"  Δ monthly cash:             {fmt_eur(leftover):>14}   {leftover_label}")
    print(f"  Assumed annual return:      {return_source}")
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
            f"  {'Target':<12} {'Current':>22}     "
            f"{'If you invest ' + str(int(alt_pct*100)) + ' % (' + fmt_eur(alt_contrib).strip() + '/m)':>34}"
        )
        print(f"  {'-'*12} {'-'*22}     {'-'*34}")
    else:
        print(f"  {'Target':<12} {'Missing':>14}  {'ETA':>26}")
        print(f"  {'-'*12} {'-'*14}  {'-'*26}")

    def _fmt_eta(entry):
        if entry["status"] == "reached":
            return "already reached"
        if entry["status"] == "non_reachable":
            return ">100 y"
        months = entry["months"]
        eta = entry["eta"]
        if months < 12:
            when = f"~{months:.1f} m"
        else:
            when = f"~{months/12:.1f} y ({months:.0f} m)"
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
        print(f"  ℹ You currently invest {last_month_contrib/avg_deposit_3m*100:.0f} % of your income into TR;")
        print(f"    raising it to 80 % visibly shortens the distant ETAs. The extra cash")
        print(f"    of {fmt_eur(avg_deposit_3m - last_month_contrib).strip()}/m is worth more invested than sitting idle.")
    else:
        print("  ℹ Model: portfolio compounds at the annual return; cash grows linearly")
        print("    with (income − contribution). Assumes the pace of last month's BUYs")
        print("    and the 3-month transfer average hold steady.")
    print()

    print(bar)
    if CONCENTRATION_LIMITS and CONCENTRATION_THRESHOLD is not None:
        header = f"  CONCENTRATION (% over positions, per-asset limits + global threshold {CONCENTRATION_THRESHOLD*100:.0f}%)"
    elif CONCENTRATION_LIMITS:
        header = f"  CONCENTRATION (% over positions, alerts only on assets with explicit limits)"
    elif CONCENTRATION_THRESHOLD is not None:
        header = f"  CONCENTRATION (% over positions, alert at >{CONCENTRATION_THRESHOLD*100:.0f}%)"
    else:
        header = f"  CONCENTRATION (% over positions, no alerts)"
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
                    trail = f"  limit {limit*100:>4.0f}%{source}, EXCEEDED by {abs(margin):>4.1f} pp"
                    exceeded_count += 1
                else:
                    trail = f"  limit {limit*100:>4.0f}%{source}, margin {margin:>+5.1f} pp"
            print(f"  {title:<28} {pct*100:>6.2f}%  {bar_str:<{max_bar}}{trail}")
        print()
        if exceeded_count == 0:
            if any(e["limit"] is not None for e in conc):
                print(f"  ✓ All positions within their limit.")
        else:
            print(f"  ⚠ {exceeded_count} position(s) above their individual limit.")
            print(f"    Consider rebalancing if you want to adjust them.")
    print()

    # Per-position attribution
    print(bar)
    print("  PER-POSITION ATTRIBUTION (per-ISIN MWR, income mode)")
    print(bar)
    attr = per_position_attribution(snapshot, txs, bonus_as="income")
    if attr:
        label_w = max((len(p["title"] or "") for p in attr), default=10)
        label_w = min(max(label_w, 14), 32)
        print(f"  {'Asset':<{label_w}} {'value':>12} {'weight':>6} {'pos. MWR':>11} {'contrib':>11}")
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
        print(f"  {'TOTAL contributions':<{label_w}} {' ':>12} {' ':>6} {' ':>11} {sum_contrib:>+8.2f} pp")
        print()
        print(f"  ── Sum ≈ annualized return of the live positions (does not include")
        print(f"     past sales). For the full portfolio's all-time MWR, see the")
        print(f"     'RETURN — FULL HISTORY' block above.")
    else:
        print("  (no attribution available — positions with insufficient flows)")
    print()

    # Currency exposure
    if ASSET_CURRENCIES:
        print(bar)
        print("  CURRENCY EXPOSURE (over total wealth, includes cash)")
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
                print(f"  ⚠ {unknown[0]['n_positions']} position(s) without a currency mapped in config's `asset_currencies`.")
        print()

    if verbose:
        print(bar)
        print("  PER POSITION (--verbose)")
        print(bar)
        cb_user_per_isin = cost_basis_user_paid_per_isin(snapshot, txs)
        label_w = max((len(p.title or "") for p in snapshot.positions), default=10)
        label_w = min(max(label_w, 12), 28)
        print(f"  {'Asset':<{label_w}} {'value':>12} {'cb own':>12} {'Δ own':>9} {'cb gross':>12} {'Δ gross':>9}")
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
