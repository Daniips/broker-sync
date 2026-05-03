# Pending improvements

Prioritized list of things to improve in the project, written so that 3 months from now (or when contributors arrive) they don't need to be rethought from scratch.

---

## ✅ Done

Chronologically (most recent on top):

- **`sync_renta` extracted to `reports/renta_es.py`** (~600 lines moved; tr_sync.py drops to ~1925). The `reports/` folder is ready for future tax regimes (UK ISA, DE Steuerbericht, PT IRS).
- **Performance attribution per position** — `core.metrics.per_position_attribution()` + "PERFORMANCE ATTRIBUTION PER POSITION" block in `make insights`. Weighted per-ISIN MWR.
- **Benchmark comparison** — `benchmark_isin` in config. `make insights` compares MWR (income) against the benchmark's annualized return over all-time / YTD / 12m, with Δ in pp and `✓` if you beat the index.
- **Currency exposure** — `asset_currencies: {ISIN: currency}` in config + "CURRENCY EXPOSURE" block grouping total net worth by currency.
- **MWR sanity check (via `make mwr-flows`)** — exports flows in TSV to verify with native XIRR in Sheets/Excel. **Validated**: my home-grown `xirr()` converges to the same number as `=TIR.NO.PER` (0.02pp diff, rounding).
- **Solana in backfill** — exchange `BHS` captured via `compactPortfolio.exchangeIds`. Fallback `instrument_details(isin)` to discover non-hardcoded exchanges in future crypto ISINs.
- **Determinism in backfill** — timestamps normalized to `T12:00:00`. Re-running is idempotent (no duplicates).
- **Per-asset concentration limits** — `concentration_limits: {ISIN: float}` + support for `concentration_threshold: null` to turn off the global and only alert what's configured.
- **TR cache** — `core/cache.py` with TTL=5min. Chains commands without re-fetch. `--refresh` to invalidate. Cache extended to v2 to also support benchmark history.
- **TR adapter tests** — `test_adapter.py` with 24 tests covering each eventType (mocked parser).
- **INSIGHTS.md** — doc explaining the `make insights` output block by block, the 2 readings of cost basis, the 3 MWR horizons, the income/deposit toggle, and FAQ.
- **Refactor to `core/` + `brokers/` + `storage/`** — three-layer pattern. ARCHITECTURE.md updated.
- **Snapshot store + historical snapshot backfill** — hidden tabs `_snapshots` and `_snapshots_positions` with aggregated + per-position schema. Automatic persistence + retroactive reconstruction via TR price history.
- **Feature registry + capabilities** — `core/features.py` + `brokers/<x>/__init__.py: CAPABILITIES`. Each feature declares what it needs; `make features` shows what's active and why.

---

## Medium priority

Concrete items, real value, low-medium effort.

### 1. Snapshot manual override

When a reconstructed snapshot is clearly wrong or you want to add a manual one from before the backfill, there's no way. Add:

```bash
python tr_sync.py --snapshot-set 2024-08-22 --positions 100 --cash 5000
python tr_sync.py --snapshot-delete 2025-04-26
```

Writes directly to `_snapshots`. Useful for one-off corrections.

**Effort**: ~30min. **Benefit**: fine control over history.

### 2. Telemetry / persistent logging

You catch bugs because you run on the console and read the output. If someone else uses it and something fails silently (parser that doesn't extract shares, wrong exchange, snapshot with weird price, unhandled rate limit), you don't find out.

Minimal proposal:
- Logger to `~/.broker-sync/logs/broker-sync.log` with rotation (last 7 days).
- Countable WARN/ERROR levels.
- `make logs-tail` that does `tail -f` on the log.

**Effort**: ~1h. **Benefit**: basic observability before something breaks silently.

### 3. TR adapter refactor (registry pattern)

`raw_event_to_tx` has a long `if/elif` per `eventType`. When you add more event types or when a future broker (IBKR) requires similar code, it gets fragile. Refactor to a dict of handlers:

```python
EVENT_HANDLERS = {
    "TRADING_TRADE_EXECUTED": _handle_trade,
    "TRADING_SAVINGSPLAN_EXECUTED": _handle_trade,
    "SAVEBACK_AGGREGATE": _handle_saveback,
    ...
}
```

**Effort**: ~1h. **Benefit**: easier to add new event types; tests per handler instead of per giant function.

### 4. Auto-rebalance suggestions

Given the `concentration_limits` and the current portfolio, compute what to buy/sell to come back inside the limit (or close to the implicit target weight).

```
REBALANCE SUGGESTIONS
  Solana            current 9.1% > limit 8.0%
    → sell ~30 € to come back to 8%

  (To define finer target weights, add `target_weights` in config.)
```

Only activates if you have `concentration_limits` configured. With global `null`, it's skipped.

**Effort**: ~1h. **Benefit**: turns passive alerts into actionable suggestions.

### 5. Realized vs unrealized split in all-time MWR

The all-time MWR mixes:
- Realized capital gains from already-sold positions (NVIDIA, Apple, Tesla, etc.).
- Unrealized gains from current positions.

It would be useful to see the breakdown:

```
All-time MWR breakdown:
  From live positions:        +18.2%
  From past sales:             +4.9%   ← you realize NVIDIA pulled hard
  Total all-time:             +23.1%
```

**Effort**: ~1h (split flows by "ISIN currently live" vs "not"). **Benefit**: context on where the alpha comes from.

---

## Low priority

Big items or with dubious return until product validation exists.

### 6. Alerts mechanism

Weekly alerts via email/Telegram when:
- A position drops >5% in a week.
- The month's contribution is <50% or >150% of the average.
- Concentration grows >10pp in a week.

Run via cron (GitHub Actions). Output to Telegram bot or SMTP.

**Effort**: ~2-3h. **Benefit**: nudge towards reviewing without opening the script.

### 7. Minimal Web UI

Only when it's validated that the insights bring value to someone external. The console is enough for personal use.

**Effort**: 1-2 days. **Benefit**: only if it pivots to a product.

### 8. Real multi-broker (IBKR / DEGIRO)

Only when there's a real case (someone using IBKR + wanting to unify with TR). The current design supports it — the code is what's missing.

**Effort**: 2-3 days per broker. **Benefit**: only with real demand.

### 9. TR cloud / SaaS session

The real blocker for public use is the 2.5h TR session. To truly solve it: cloud backend, own auth, DB, frontend.

That's already a **SaaS product**. Only consider it if the monetization idea moves from hypothesis to validation.

**Effort**: weeks. **Benefit**: viable public product. Don't start without prior validation.

### 10. CI improvements

- Type checking with `mypy --strict` over `core/`.
- Coverage report in CI (target >80% in `core/`).
- Pre-commit hook with `ruff` for style.
- Test matrix Python 3.11 / 3.12 / 3.14.

**Effort**: ~1-2h. **Benefit**: catch regressions earlier.

### 11. Architecture docs

`ARCHITECTURE.md` describes the pattern but doesn't have a visual diagram. Adding one (ASCII or image) helps at first glance.

**Effort**: ~30min. **Benefit**: faster onboarding.

### 12. Tax loss harvesting suggestions

Positions with negative unrealized gains you could sell to realize the loss and offset gains from the same fiscal year (mind the Spanish 2-month wash sale rule if you re-buy the same thing).

```
TAX LOSS HARVESTING (orientative)
  Solana           unrealized loss: −127.82 €
    → Selling now offsets up to −127.82 € of gains.
    If you closed +200 € in 2026, you could reduce taxable base 64% × 127.82 = 81.80 €.
```

Orientative only, requires real tax advice.

**Effort**: ~2h. **Benefit**: practical reminder before year-end.

### 13. Monte Carlo projection

Simulate 1,000-10,000 future scenarios (10 years) given your monthly contribution and return assumptions (μ=8%, σ=16% typical S&P). Show 10/50/90 percentiles.

```
10-YEAR PROJECTION (Monte Carlo, 10000 simulations, μ=8%, σ=16%)
  Median (p50):        ~ 220,000 €
  Pessimistic (p10):   ~ 145,000 €
  Optimistic (p90):    ~ 340,000 €
```

**Effort**: ~3-4h. **Benefit**: long-term visualization + "wow" factor when shown to others. But the assumptions (μ, σ) **dominate the result**, so it's not decision-grade — entertainment.

### 14. Goal tracker

Define a goal (`100,000 € by 2030`) in config and show:
- Progress rate vs linear (with your current contribution + assumed return, do you make it?).
- How much you'd need to save/month to hit the target in X years.

Useful as motivation; depends on Monte Carlo (#13) to make it robust.

**Effort**: ~2h. **Benefit**: motivational framing. Only if you latch onto explicit goals.

---

## Things I'd leave as they are

- ✅ `core/brokers/storage/reports/` architecture — clean, don't touch.
- ✅ Feature registry — already does its job.
- ✅ Unit tests — 156 tests passing, good coverage.
- ✅ Expense/income/investment sync — works, don't migrate to another layout.
- ✅ TR cache — simple and sufficient.
- ✅ Snapshot store — correct abstraction.

---

## How to decide what to do next

- **For stable personal use**: nothing urgent. Items 1-5 are polish, not necessary.
- **If something annoys you in practice**: typically #1 (manual snapshot) or #2 (logging), in order of usefulness.
- **If you want to keep iterating technically**: #3 (registry pattern) or #5 (realized vs unrealized split).
- **If you want to validate a product**: STOP and show the tool to 2-3 people. The next priority comes from their feedback.
- **If something breaks in production**: #2 (telemetry) first to understand what broke.
