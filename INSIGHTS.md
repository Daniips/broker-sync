# Guide to `make insights`

This guide explains **block by block** what comes out of `make insights`, why there are several readings of the same idea, and what question each number answers.

---

## TL;DR — which number to look at

| Question | Number that answers it |
|---|---|
| How much do I have in total? | **Patrimonio → TOTAL** |
| How are my live positions doing? | **Plusvalía sobre tu dinero (%)** ← matches TR app and Excel |
| What real annualized return is my investment generating? | **MWR all-time / 12 months** |
| Am I saving more or less than usual? | **Monthly contributions: Δ vs average** |
| Am I too concentrated in one position? | **Concentration + `⚠ alta` alert** |

The rest of the lines are context / sub-readings to help you understand where each figure comes from.

---

## Block 1 — CURRENT NET WORTH

```
PATRIMONIO ACTUAL
  Cartera (ETFs/acciones):       9.145,50 €
  Cripto:                          278,28 €
  Cash:                         13.127,46 €
  TOTAL:                        22.551,24 €
```

- **Cartera (ETFs/acciones)** = sum of `netValue` of positions that are **not** in `crypto_isins` of the config.
- **Cripto** = sum of `netValue` of positions marked as crypto.
- **Cash** = EUR balance of your TR account.
- **TOTAL** = everything above.

We separate portfolio and crypto because the TR app shows them in distinct blocks. Their sum equals the total balance you see in TR (give or take a few € from price moves between when you look and when the script runs).

---

## Block 2 — RETURNS — CURRENT POSITIONS

```
RENTABILIDAD — POSICIONES ACTUALES
  Cost basis sin saveback:       8.229,78 €  ← lo que tú pusiste
  Cost basis con saveback:       8.424,20 €  ← averageBuyIn API bruto
  Valor actual:                  9.423,78 €
  Plusvalía sobre tu dinero:     1.194,00 €  (+14,51 %)  ← matchea Excel y TR app
  Plusvalía sobre bruto:           999,58 €  (+11,87 %)  ← saveback incluido como coste
```

Here we show **two readings** of the same unrealized gain, depending on which cost basis you use as the denominator.

### Cost basis without saveback (recommended)

It's what YOU put out of pocket into the positions you currently hold:

```
own_cost_basis = TR averageBuyIn × shares − Σ saveback received from the ISIN
```

Saveback is money TR gives you (~1% of card spend) that enters your portfolio as shares. **You didn't pay it**, so it shouldn't inflate the cost you use to measure your return.

This reading **matches**:
- The "Ganancia" % you have in your manual Excel (if you keep one).
- The "Rendimiento" % the TR app shows.

### Cost basis with saveback (TR API gross)

It's directly `averageBuyIn × shares` returned by TR. It includes the value of the saveback shares at the market price at delivery time. It's the "technical cost basis" — what the broker considers you paid, without distinguishing whether it was your money or a perk.

### Which of the two is "the right one"?

It depends on the question:

- "**How is the money I put in doing?**" → without saveback (+14.51%).
- "**How much is the portfolio worth vs the cost of acquiring it?**" → with saveback (+11.87%).

We show both so you understand the difference. The gap between the two %s tells you **how much saveback is contributing**: here ~2.6 percentage points of free boost.

---

## Block 3 — RETURNS — FULL HISTORY

```
RENTABILIDAD — HISTÓRICO COMPLETO (incluye ventas y dividendos)
  ── Mi dinero (saveback como income — default) ──
    Aportado neto (BUYs − SELLs):        9.942,22 €
    MWR all-time:                    +23,08 % anual
    MWR YTD (2026):                  +17,97 % anual
    MWR 12 meses:                    +25,17 % anual

  ── Incluyendo saveback como aportación ──
    Aportado neto (BUYs − SELLs):       10.136,64 €
    MWR all-time:                    +19,66 % anual
    MWR YTD (2026):                  +17,22 % anual
    MWR 12 meses:                    +22,72 % anual
```

Here we change the angle: instead of "value vs cost of the live portfolio", we compute **MWR (XIRR)** over all historical flows. Sales, dividends, interest, gifts — everything is in.

### What MWR is

**Money-Weighted Return** = the annualized IRR over your cash flows. It answers:

> "If I put in a spreadsheet every euro I deposited (with its date) and every euro I withdrew (idem) and at the end the current value of the portfolio, at what annual rate would my euros need to grow for the math to check out?"

It's the honest number, comparable against benchmarks (S&P, MSCI World). The simple % ("I put in 9,942, it's worth 9,423, I lost 5%") is misleading because it ignores **when** each euro entered: money you put in 18 months ago has had more time to compound than money you put in last month.

### Why two modes: income vs deposit

When you receive saveback, there are two ways to treat it in the calculation:

- **`income` (default)**: saveback is "broker income", like a bonus. It does **not** count as your contribution. The MWR ignores these shares as an external flow and what they brought in lifts the return → higher MWR.
- **`deposit`**: we treat saveback as another contribution from your pocket. It counts as invested capital without bringing extra return → lower MWR (more capital, same return).

```
MWR all-time income:    +23,08 %  ← answers "what is MY savings effort returning?"
MWR all-time deposit:   +19,66 %  ← answers "what does any invested euro return?"
```

Both are mathematically correct. The difference between the two tells you how much saveback is pushing.

### Why 3 horizons (all-time / YTD / 12m)

| Horizon | What it answers |
|---|---|
| **all-time** | Annualized return since the first day of your account. Stable but drags old context. |
| **YTD** | Annualized return since January 1st of the current year. **Comparable** to "the S&P is up +X% YTD". |
| **12 months** | Annualized return over the last 365 days. Better for "how is my portfolio doing **lately**" because it smooths year-boundary effects. |

All three are computed with XIRR but over different periods. For the sub-periods (YTD / 12m) we need the **portfolio value at the start** of the period, which comes from the hidden `_snapshots` tab. If you don't yet have snapshots before the period, it shows `n/a` with a note.

> **Tip:** run `make backfill-snapshots` once to reconstruct a year of weekly history so YTD/12m MWRs come out from day one.

### Net contributed vs value

```
Aportado neto (BUYs − SELLs):        9.942,22 €
```

It's the sum of all your purchases minus the money recovered through sales, all-time. It's **not** the cost basis of your current positions (that would be FIFO over the live shares). Useful as "this is the net money still inside the portfolio or that flowed through it".

---

## Block — PERFORMANCE ATTRIBUTION PER POSITION

Table with the individual MWR of each position you currently hold, and how many percentage points it contributes to the weighted return of your portfolio:

```
ATRIBUCIÓN DE RENDIMIENTO POR POSICIÓN (MWR per-ISIN, modo income)
  Activo                          valor   peso    MWR pos.       aporta
  ----------------------- ------------ ------ ----------- -----------
  Core S&P 500 USD (Acc)    4.205,81 €  44.6%   +28.50 %     +12.71 pp
  S&P 500 Information Tech  1.366,66 €  14.5%   +35.20 %      +5.10 pp
  Core MSCI Europe EUR        796,82 €   8.5%   +12.40 %      +1.05 pp
  Solana                      278,21 €   3.0%   -42.10 %      -1.24 pp
  ...
  TOTAL contribuciones                                        +XX.XX pp
```

**How to read it:**
- **valor** and **peso**: how much the position weighs in your current investment portfolio.
- **MWR pos.**: annualized return of that individual position, computed with XIRR over all its flows (BUYs, partial SELLs, dividends, current value). Honest, not a "% from my average price".
- **aporta**: contribution of that position to the aggregate return = `MWR × weight`. Summing all → annualized return **of live positions**.

**Important:** The sum of contributions **doesn't match** your all-time MWR from the historical block. Reason: only live positions count here; the all-time MWR also includes flows from positions you already sold (NVIDIA, Tesla, Apple, etc.).

**When it helps:** see at a glance which positions drive the return and which drag. If a small position has catastrophic MWR (Solana -42%), it probably doesn't deserve more weight. If a large one is slow but steady (Europe +12%), you know it contributes little but diversifies.

---

## Optional block — RETURN VS BENCHMARK

Only appears if you have `benchmark_isin` defined in `config.yaml`. Compares your MWR (`income` mode) against the annualized return of a reference ETF over the same periods:

```
RENTABILIDAD VS BENCHMARK (Core S&P 500 USD)
  Periodo            Tu MWR (income)         Benchmark      Δ vs benchmark
  all-time              +23.08 %         +18.40 %          +4.68 pp ✓
  YTD (2026)            +17.97 %         +12.30 %          +5.67 pp ✓
  12 meses              +25.17 %         +18.90 %          +6.27 pp ✓
```

- **`✓`** appears when you beat the benchmark in that period.
- **Positive Δ in pp** (percentage points): you beat the index by X pp.
- **Negative Δ**: you're below. Not necessarily bad — depends on your strategy (more conservative, more diversified, etc.).

Without this line, "+23%" annual doesn't tell you whether you're doing well. With benchmark it does: if the S&P returned +18% in the same period and you do +23%, you've added +5pp of "alpha" over the market. If it had done +30% and you do +23%, you're missing -7pp — maybe you're holding too much cash or poorly diversified.

---

## Block 4 — MONTHLY CONTRIBUTIONS

```
APORTACIONES MENSUALES (compras brutas, incluye saveback/regalos)
  Este mes (2026-04):               915,64 €
  Media últimos 12m:                505,01 €
  Δ vs media:                        +81.3%
```

- **Este mes** = sum of all BUYs in the current month (savings plan + manual + saveback + gifts). Matches the "Dinero invertido YYYY" tab in your Sheet.
- **Media últimos 12m** = simple average of the months with BUYs > 0 in the last 12 (it doesn't dilute with months without activity).
- **Δ vs media** = `(this_month − average) / average`. Positive = this month you invest more than usual.

If there's not enough history to compare (new account), it shows the last 3 months with contribution.

> **Why include saveback/gifts**: to match your manual Excel. If you want "only MY money this month", calculate by hand: `915.64 − saveback_april`.

---

## Block 5 — CONCENTRATION

Distribution of your positions' value (excludes cash) by asset, sorted from heaviest to lightest. The bars are visual — same ratio as the numeric %.

### Simple mode (no per-asset limits)

If you don't define `concentration_limits` in config, all assets are compared against the global `concentration_threshold` (default 35%):

```
CONCENTRACIÓN (% sobre posiciones, alerta a >35%)
  Core S&P 500 USD (Acc)        44.63%  ███████████  límite  35% (global), margen  −9.6 pp
  Core MSCI EM IMI USD (Acc)    15.88%  ████         límite  35% (global), margen +19.1 pp
  ...
  ⚠ 1 posición(es) por encima de su límite individual.
```

### Per-asset mode (with individual limits)

If you define `concentration_limits` in `config.yaml`, each ISIN is evaluated against its own cap. Useful when you have reasonable per-asset tolerances (high for core ETFs, low for crypto):

```yaml
concentration_limits:
  IE00B5BMR087: 0.50    # SP500 — core, high tolerance
  XF000SOL0012: 0.08    # Solana — crypto, low tolerance
```

Output:

```
CONCENTRACIÓN (% sobre posiciones, límites por activo + threshold global 35%)
  Core S&P 500 USD (Acc)        44.63%  ███████████  límite  50%, margen  +5.4 pp
  S&P 500 Information Tech USD  14.50%  ████         límite  20%, margen  +5.5 pp
  Solana                         2.95%  █            límite   8%, margen  +5.1 pp
  MSCI India USD (Acc)           2.67%  █            límite  35% (global), margen +32.3 pp
  ...
  ✓ Todas las posiciones dentro de su límite.
```

### "I only care about X assets" mode

Typical case: you only want an alert for Solana (or any crypto), the rest don't matter. Set `concentration_threshold: null` and only the ISINs with an entry in `concentration_limits` will show an alert line:

```yaml
concentration_threshold: null
concentration_limits:
  XF000SOL0012: 0.08
```

Output:

```
CONCENTRACIÓN (% sobre posiciones, alerta solo en activos con límite explícito)
  Core S&P 500 USD (Acc)        44.63%  ██████████████████
  Core MSCI EM IMI USD (Acc)    15.88%  ██████
  S&P 500 Information Tech USD  14.50%  ██████
  MSCI World Small Cap USD (Ac  10.91%  ████
  Core MSCI Europe EUR (Acc)     8.46%  ███
  Solana                         2.95%  █                   límite   8%, margen  +5.1 pp
  MSCI India USD (Acc)           2.67%  █

  ✓ Todas las posiciones dentro de su límite.
```

Only Solana shows a limit line. The rest go "clean" with no possible alerts.

### When "(global)" is shown

Indicates that the asset **has no entry** in `concentration_limits` and is being compared against the global `concentration_threshold`. Useful to detect if you forgot to add a limit to some position.

### Meaning of the alerts

- **EXCEDIDO en X pp**: the position is above its limit. Negative margin in the ratio.
- **margen +X pp**: the position is below the limit with X percentage points of headroom.
- **✓ Todas dentro de su límite** (at the end): nothing to rebalance.
- **⚠ N posiciones por encima**: how many assets you're over.

It's not a recommendation to change anything — it's a signal to **check** whether that concentration is comfortable for you. A 100% S&P 500 portfolio is perfectly concentrated and many prefer it that way.

---

## Optional block — CURRENCY EXPOSURE

Only appears if you have `asset_currencies` defined in `config.yaml`. Shows how your total net worth (positions + cash) is split by denomination currency:

```
EXPOSICIÓN POR DIVISA (sobre patrimonio total, incluye cash)
  EUR        13.923,23 €  ( 61.7%)  ██████████████████████   1 pos.
  USD         8.347,12 €  ( 37.0%)  █████████████            5 pos.
  CRYPTO        278,28 €  (  1.2%)  ▌                        1 pos.
```

When it helps: if most of your investments are USD-denominated ETFs but your cash stays in EUR, you realize that your **effective USD exposure** is lower than it seems — uninvested cash is weighing towards EUR.

Unmapped ISINs go to the `UNKNOWN` bucket with a warning. To map them, edit `config.yaml > asset_currencies`.

---

## Optional block — PER POSITION (`--verbose`)

```
$ python tr_sync.py --insights --verbose
```

Activates a per-asset table with own + gross cost basis and unrealized gain on each metric. Diagnostic only — useful if you see a weird number in the previous blocks and want to see which position explains it.

---

## How history is accumulated (`_snapshots`)

Each `make insights`, `make portfolio`, and `make backfill-snapshots` adds a row to the hidden `_snapshots` tab:

| Column | Content |
|---|---|
| `ts` | ISO timestamp |
| `cash_eur` | Cash in TR |
| `positions_value_eur` | Value of all positions |
| `cost_basis_eur` | TR cost basis with saveback (when applicable) |
| `total_eur` | Cash + positions |

The `_snapshots_positions` tab adds a row per (snapshot, ISIN) with shares and net_value. Useful for per-asset evolution charts.

Both tabs are **hidden** by default. To see them in Google Sheets: menu `View → Hidden sheets`.

---

## FAQ

**Q: My all-time MWR is +23%. Isn't that very high?**

Depends on what you've held. If your portfolio is concentrated in S&P 500 + tech ETFs during a bull market (2024-2026 has been strong) and you've bought on dips, +20-25% annualized is plausible. Numbers like that don't last indefinitely — markets normalize. Compare against the S&P 500 YTD/12m of the equivalent period.

**Q: TR app gives me +14.08% but `make insights` gives +14.51%. Where does the difference come from?**

Small differences (<1pp) usually come from:
- My saveback subtraction uses the event's `amount_eur`; TR uses internal `value_at_delivery` (there can be a USD/EUR spread of cents per saveback share).
- Price movement between when you opened the app and when the script runs.

If the difference is >2pp, there's possibly a position with a weird `cost_basis`: run with `--verbose` and check the per-position table.

**Q: Why doesn't the "Aportado neto" block match my own cost basis?**

Because they're different things:
- **Aportado neto** = `Σ BUYs − Σ SELLs` historical (includes what you sold and no longer hold).
- **Own cost basis** = what you paid for the shares you currently have (FIFO with saveback at cost 0).

If you've made profitable or losing sales, the two numbers diverge.

**Q: When does it refresh data? How often should I run `make insights`?**

Each run goes to TR and downloads everything from scratch (transactions + portfolio). With the cache TTL=5min, two consecutive runs reuse the data without going back to TR.

For normal use: once a day / week. Each run saves a snapshot in `_snapshots`, so the history grows on its own.

**Q: I find metric X misleading, can I disable it?**

Yes. In `config.yaml > features` set the feature to `false`:

```yaml
features:
  concentration: false   # turn off the concentration block
  saveback_metrics: false   # turn off the two unrealized-return readings
```

Full list: `make features`.

**Q: How do I verify the MWR isn't a numeric bug?**

Run `make mwr-flows` and paste the output (TSV) into a new Sheets/Excel sheet. Apply `=XIRR(B:B, A:A)` and the result should match the all-time MWR from the historical block (`income` mode). If it diverges significantly, it's a bug — open an issue.

To verify the `deposit` mode (with saveback as contribution):

```bash
make mwr-flows BONUS=deposit
```

**Q: I want an MWR for a specific period that's not YTD/12m.**

Today it's not exposed in the CLI. You have the pure function `core.metrics.mwr()` that accepts arbitrary `start` and `end`; use it like:

```python
from core.metrics import mwr
from datetime import datetime
from zoneinfo import ZoneInfo
TZ = ZoneInfo("Europe/Madrid")
mwr(txs, snapshot, start=datetime(2025, 6, 1, tzinfo=TZ), start_value=4500.0)
```

If you need it as a CLI flag, open an issue.
