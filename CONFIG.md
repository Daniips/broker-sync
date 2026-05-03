# `config.yaml` reference

All per-user configuration lives in `config.yaml`. This file **is not committed** (it's in `.gitignore`). The clean template is `config.example.yaml`.

> Convention: keys marked as **(required)** must have a real value for the script to start. Those marked as **(optional)** have a sensible default.

---

## Summary

| Section | Field | Type | Description |
|---|---|---|---|
| `sheet_id` | string | required | Google Sheet ID |
| `sheets.expenses` | string | required | Name of the expenses tab |
| `sheets.income` | string | required | Name of the income tab |
| `sheets.investments_year_format` | string | required | Pattern with `{year}` for the investments tab |
| `sheets.investments_year` | int / null | optional | Pins the fiscal year; null = current year |
| `sheets.portfolio` | string | required | Name of the portfolio tab |
| `sheets.status` | string | required | Name of the status tab |
| `sheets.sync_state` | string | required | Name of the hidden dedup tab |
| `summary_markers.expenses` | list[string] | required | Strings that mark the summary block in Gastos |
| `summary_markers.income` | list[string] | required | Same for Ingresos |
| `status_labels` | dict | required | Labels for the Estado sync tab |
| `asset_name_map` | dict | required | TR-name → display-name for investments |
| `portfolio_cell_map` | list[obj] | required | ISIN+label in the vertical order of the range |
| `portfolio_value_range` | string | required | A1 range where netValues are written |
| `crypto_isins` | list[string] | optional | ISINs treated as crypto |
| `gift_cost_overrides` | dict | optional | Override for unparseable gifts |
| `ignore_events` | dict | optional | Filters to discard events from the sync |
| `default_buffer_days` | int | optional | Buffer days when downloading events |
| `timezone` | string | optional | Time zone (default `Europe/Madrid`) |
| `saveback_label` | string | optional | Label of the Saveback row in Investments (default `SAVEBACK`) |
| `init_sheet_headers` | dict | optional | Headers written by `make init-sheet` |
| `subtitle_translations` | dict | optional | German → user language translations (merged with default Spanish) |
| `renta_classification` | dict | optional | Subtitles to classify dividends/bonds in `make renta` |
| `features` | dict | optional | Per-feature toggles (`insights`, `concentration`, `backfill_snapshots`, etc.) — see Features section |
| `concentration_threshold` | float | optional | Global % above which a position is flagged "high concentration" when it has no explicit limit (default `0.35`) |
| `concentration_limits` | dict | optional | Per-ISIN cap (override of the global). `{ISIN: 0.50, ...}` |
| `asset_currencies` | dict | optional | Mapping `{ISIN: "USD"\|"EUR"\|"CRYPTO"\|...}` for the currency exposure block in `make insights`. ISINs without an entry → "UNKNOWN" |
| `benchmark_isin` | string | optional | ISIN to compare your MWR against (typically an S&P 500 ETF). Activates the "RETURN VS BENCHMARK" block in `make insights`. |
| `benchmark_label` | string | optional | Human-readable label of the benchmark shown in the output (default: the ISIN itself). |
| `sheets.snapshots` | string | optional | Name of the hidden tab with aggregated snapshots (default `_snapshots`) |
| `sheets.snapshots_positions` | string | optional | Name of the hidden tab with per-position snapshots (default `_snapshots_positions`) |

---

## `sheet_id` (required)

Google Sheet ID, taken from the URL: `docs.google.com/spreadsheets/d/<THIS_ID>/edit`.

```yaml
sheet_id: "1AbcDef..."
```

---

## `sheets` (required)

Names of the tabs the script will read/write. If your Sheet has names different from the example, adjust them here.

```yaml
sheets:
  expenses: "Gastos"
  income: "Ingresos"
  investments_year_format: "Dinero invertido {year}"
  investments_year: null      # null = current year; e.g. 2025 to pin it
  portfolio: "Calculo ganancias"
  status: "Estado sync"
  sync_state: "_sync_state"
```

| Field | Notes |
|---|---|
| `expenses` | Expenses tab. Layout configurable with `expenses_layout`. |
| `income` | Income tab. Layout configurable with `income_layout`. |
| `expenses_layout` / `income_layout` | `monthly_columns` (default; months as Concept+Amount column pairs + summary block) or `ledger` (one row per event with Date/Concept/Amount). |
| `ledger_headers` | Only if you use `ledger`. List of 3 strings with the headers (default `["Fecha", "Concepto", "Importe"]`). |
| `ledger_columns` | Only if you use `ledger`. `{ date, concept, amount }` with the A1 column letter for each field (default A/B/C). Can be non-contiguous. |
| `month_header_amount` / `month_header_concept` | Only if you use `monthly_columns`. Templates with `{month}` and `{year}` for month headers (defaults `"{month} {year}"` and `"Concepto {month}"`). |
| `investments_year_format` | String with `{year}` that gets substituted by the active fiscal year (e.g. `"Dinero invertido 2026"`). |
| `investments_year` | If `null`, the script uses the current year. If you set a year, it's pinned (useful for backwards compatibility or investments in past years). You can also override it with the env var `TR_SYNC_INVESTMENTS_YEAR`. |
| `portfolio` | Tab with the per-asset value snapshot. |
| `status` | The script creates this tab if it doesn't exist; shows timestamps of the last successful sync. |
| `sync_state` | Hidden tab where the script stores the IDs of already-written events for dedup. **Don't edit it by hand.** |
| `snapshots` | Hidden tab with one snapshot per execution of `make insights`/`portfolio`/`backfill-snapshots`. Default `_snapshots`. **Don't edit it by hand.** |
| `snapshots_positions` | Hidden tab with per-position breakdown of each snapshot. Default `_snapshots_positions`. **Don't edit it by hand.** |

---

## `summary_markers` (required)

Strings (case-insensitive) that the script searches in the concept column of each month to detect the final summary block. When inserting new rows, it places them right above the first marker it finds.

```yaml
summary_markers:
  expenses:
    - "gastos innecesarios"
    - "gastos totales"
    - "extraordinarios"
  income:
    - "ingresos totales"
    - "ingresos"
    - "totales"
```

> If your sheet doesn't have a summary block, leave the list empty. The script will then write at the end of each column without shifting anything.

---

## `status_labels` (required)

Labels that appear in column A of the "Estado sync" tab.

```yaml
status_labels:
  portfolio: "Portfolio"
  sync: "Sync completo"
```

---

## `asset_name_map` (required for investments)

Mapping from the name TR uses for each asset (key) to the name you have in the "Dinero invertido" tab (value). If TR sends an asset that's not in this map, the script creates a new row with the original title and warns.

```yaml
asset_name_map:
  "MSCI India USD (Acc)": "MSCI India"
  "Solana": "Solana"
  "Core MSCI Europe EUR (Acc)": "MSCI EUR"
  "Core MSCI EM IMI USD (Acc)": "MSCI EM IMI"
  "Core S&P 500 USD (Acc)": "SP 500"
  "MSCI World Small Cap USD (Acc)": "SMALL CAPS"
```

To discover the exact name TR uses, run:
```bash
.venv/bin/python inspect_events.py --eventtype TRADING_SAVINGSPLAN_EXECUTED
```

---

## `portfolio_cell_map` (required)

Ordered list of the ISINs whose current value you want written to the "Calculo ganancias" tab. **Order matters**: the first entry goes to the first row of the range, etc.

```yaml
portfolio_cell_map:
  - { isin: "IE00B5BMR087", label: "sp500" }
  - { isin: "IE00B3WJKG14", label: "sp500 tech" }
  - { isin: "IE00BKM4GZ66", label: "em imi" }
  - { isin: "IE00BF4RFH31", label: "small caps" }
  - { isin: "IE00B4K48X80", label: "msci eur" }
  - { isin: "IE00BZCQB185", label: "india" }
  - { isin: "XF000SOL0012", label: "solana" }
```

The `label` is only used for the console log; what matters is the ISIN and the order.

---

## `portfolio_value_range` (required)

A1 range where the script writes the current `netValue`s. Must be a single column with as many cells as entries in `portfolio_cell_map`.

```yaml
portfolio_value_range: "C2:C8"
```

---

## `crypto_isins` (optional)

ISINs that the script treats as cryptocurrencies for the "Posición cripto" section of the IRPF report (informative for Modelo 721).

```yaml
crypto_isins:
  - "XF000SOL0012"
```

---

## `gift_cost_overrides` (optional)

Manual override for gifts (`GIFTING_RECIPIENT_ACTIVITY` / `GIFTING_LOTTERY_PRIZE_ACTIVITY`) whose details the script can't parse. Fill the cost and shares with the exact data from TR's "Jährlicher Steuerbericht" PDF.

```yaml
gift_cost_overrides:
  LU1681048804:
    shares: 0.222311
    cost_eur: 25.00
```

---

## `ignore_events` (optional)

Patterns to discard events from the sync. Useful for salaries and auto-transfers you already handle manually. Match is **case-insensitive** and by **substring**.

```yaml
ignore_events:
  income:
    title_contains:
      - "your name"           # incoming auto-transfers
      - "imagin"
    subtitle_contains: []
  expenses:
    title_contains: []
    subtitle_contains: []
```

When an event matches, the script logs it in detail (date + amount + title) during the sync. Read [README → Ignoring events](README.md#ignoring-events) for examples.

---

## `default_buffer_days` (optional, default `7`)

Buffer days before the start of the current month when downloading events. Useful to catch events from the last day of the previous month that may have taken time to appear.

```yaml
default_buffer_days: 7
```

---

## `timezone` (optional, default `Europe/Madrid`)

Time zone in which TR timestamps (which come in UTC) are interpreted. Any valid IANA zone.

```yaml
timezone: "Europe/Madrid"
```

---

## `saveback_label` (optional, default `"SAVEBACK"`)

Text that appears in column A of the "Dinero invertido" tab for the row that aggregates Saveback. If your tab has another label for that row (e.g. `"Cashback ETF"`), put the same string here.

```yaml
saveback_label: "SAVEBACK"
```

---

## `init_sheet_headers` (optional)

Headers that `make init-sheet` writes when it creates empty tabs the first time. Useful if you want to use the script in another language or with different terminology.

```yaml
init_sheet_headers:
  investments_asset_column: "Activo"      # col A header in Investments tab
  portfolio_asset_column: "Activo"        # labels col header in Portfolio tab
  portfolio_value_column: "Valor (€)"     # values col header in Portfolio tab
```

---

## `subtitle_translations` (optional)

Additional dictionary of German → user-language translations. The TR API always responds in German; here you put the translations you want to see in console and in the Sheet. Your config is **merged** with the default (Spanish), so you only need to list the ones you change.

```yaml
subtitle_translations:
  "Bardividende": "Cash dividend"           # example: use English
  "Verkaufsorder": "Sell order"
  "Endgültige Fälligkeit": "Final maturity"
```

If TR introduces a new subtitle that's not in the default, add it here.

---

## `features` (optional)

Per-feature toggles. By default **all features are enabled**; only add entries here if you want to turn one off.

```yaml
features:
  expenses: true              # expenses sync
  income: true                # income sync
  investments: true           # sync of "Dinero invertido YYYY"
  portfolio: true             # snapshot to "Calculo ganancias"
  renta: true                 # IRPF report
  insights: true              # net worth + returns + concentration
  concentration: true         # concentration block within insights
  snapshot_persist: true      # save automatic snapshots on each run
  backfill_snapshots: true    # historical reconstruction of snapshots
  saveback_metrics: true      # unrealized return discounting saveback
```

A feature only runs if **(a)** it's enabled here AND **(b)** the active broker supports its capabilities. To see the current state: `make features`.

```
$ make features
Feature                Config   Support   Effective  Description
---------------------- -------- --------- ---------- ----------------------------------------
insights               ✓        ✓         ✓ ON       Net worth, TR-style returns + own, MWR…
concentration          ✓        ✓         ✓ ON       Per-position portfolio distribution + alert…
saveback_metrics       ✓        ✓         ✓ ON       Unrealized return discounting saveback (when broker has saveback)
…
```

When a second broker arrives that lacks (e.g.) saveback, `saveback_metrics` will show `Support ✗ → Effective ✗ off` automatically.

---

## `concentration_threshold` (optional)

Float between 0 and 1, or `null`. Defines the global % above which a position is flagged as "high concentration" in `make insights`. Default `0.35` (35%). Only applies to ISINs without an entry in `concentration_limits`.

```yaml
concentration_threshold: 0.40   # 40%
concentration_threshold: null   # turns off the global; only ISINs with explicit limit will alert
```

`null` is useful when you only care about the concentration of **a few specific assets** (typical: crypto) and you don't mind how the rest are distributed.

---

## `concentration_limits` (optional)

Dict `{ISIN: float}` with the individual cap of each asset in the portfolio. Overrides `concentration_threshold` for the listed ISINs. Useful when you think of the portfolio with different tolerances per asset (high for core ETFs, low for crypto).

```yaml
concentration_limits:
  IE00B5BMR087: 0.50    # Core SP500: 50% (it's core, high tolerance)
  IE00B3WJKG14: 0.20    # SP500 Tech: 20%
  IE00BKM4GZ66: 0.20    # EM IMI: 20%
  IE00BF4RFH31: 0.15    # Small Cap: 15%
  IE00B4K48X80: 0.15    # MSCI Europe: 15%
  IE00BZCQB185: 0.10    # MSCI India: 10%
  XF000SOL0012: 0.08    # Solana: 8% (crypto, low tolerance)
```

Behavior in `make insights`:

```
CONCENTRACIÓN (% sobre posiciones, límites por activo + threshold global 35%)
  Core S&P 500 USD (Acc)        44.63%  ███████████  límite  50%, margen  +5.4 pp
  S&P 500 Information Tech USD  14.50%  ████         límite  20%, margen  +5.5 pp
  Solana                         9.20%  ███          límite   8%, EXCEDIDO en  1.2 pp
  ...
  ⚠ 1 posición(es) por encima de su límite individual.
```

ISINs **without an entry** fall back to the global `concentration_threshold` (marked as "(global)" in the output so you can tell them apart).

---

## `asset_currencies` (optional)

Mapping `{ISIN: currency}` that `make insights` uses to show your exposure by denomination currency. Without this, the block isn't shown.

```yaml
asset_currencies:
  IE00B5BMR087: USD     # Core S&P 500
  IE00B3WJKG14: USD     # S&P 500 Information Tech
  IE00BKM4GZ66: USD     # MSCI EM IMI
  IE00BF4RFH31: USD     # MSCI World Small Cap
  IE00B4K48X80: EUR     # Core MSCI Europe
  IE00BZCQB185: USD     # MSCI India
  XF000SOL0012: CRYPTO  # Solana
```

ISINs without an entry fall into the "UNKNOWN" bucket and a warning is printed. The cash in the TR account is always counted as EUR.

Expected output in `make insights`:

```
EXPOSICIÓN POR DIVISA (sobre patrimonio total, incluye cash)
  EUR        13.923,23 €  ( 61.7%)  ██████████████████████   1 pos.
  USD         8.347,12 €  ( 37.0%)  █████████████            5 pos.
  CRYPTO        278,28 €  (  1.2%)  ▌                        1 pos.
```

---

## `benchmark_isin` (optional) and `benchmark_label`

ISIN to compare your MWR against. Activates the "RETURN VS BENCHMARK" block in `make insights`. Useful to answer "am I beating the market or is it dragging me?" — without this, the MWR is in a vacuum.

```yaml
benchmark_isin: IE00B5BMR087
benchmark_label: "Core S&P 500 USD"
```

The script downloads the benchmark's price history via the same API used by backfill (`aggregateHistoryLight`), computes its annualized return over the same periods as your MWR (all-time / YTD / 12m), and shows a table with the difference in percentage points.

It assumes the benchmark is **accumulating** (reinvests dividends in price). If it were distributing, the displayed return would be conservative (it wouldn't include collected dividends).

Expected output:

```
RENTABILIDAD VS BENCHMARK (Core S&P 500 USD)
  Periodo            Tu MWR (income)         Benchmark      Δ vs benchmark
  -------------- ------------------  ----------------  ------------------
  all-time              +23.08 %         +18.40 %          +4.68 pp ✓
  YTD (2026)            +17.97 %         +12.30 %          +5.67 pp ✓
  12 meses              +25.17 %         +18.90 %          +6.27 pp ✓
```

`✓` appears when you beat the benchmark in that period. No mark = equal or below.

---

## `renta_classification` (optional)

German subtitles that TR uses in `SSP_CORPORATE_ACTION_CASH` events to classify the event (dividend, bond coupon, maturity). If TR changes or adds a new subtitle, patch here without touching code.

```yaml
renta_classification:
  dividend_subtitles:
    - "Bardividende"
    - "Aktienprämiendividende"
    - "Kapitalertrag"
  bond_cash_subtitles:
    - "Zinszahlung"
    - "Kupon"
  bond_maturity_subtitles:
    - "Endgültige Fälligkeit"
```

You only need to redefine the ones you want to change; the rest fall back to the default.

---

## Environment variables that override config

These variables take priority over what you put in `config.yaml`:

| Variable | Overrides |
|---|---|
| `TR_SYNC_INVESTMENTS_YEAR` | `sheets.investments_year` |
