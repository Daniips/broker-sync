# Expected Google Sheet structure

This script assumes a specific structure in your Google Sheet. Below is a description of which tabs it needs and how they should be organized.

> **Automatic shortcut**: once you have `config.yaml` with your `sheet_id` and have authenticated gspread, run:
> ```bash
> make init-sheet
> ```
> which will create any missing tabs with the minimum structure (idempotent: if they already exist, it doesn't touch them). The summary block of Gastos/Ingresos you add by hand if you want it.
>
> If you prefer to create everything by hand or customize the structure, follow the instructions below.

Tab names are configurable in `config.yaml` (`sheets` section); here the default values are used.

---

## 1. `Gastos` tab

**Months in columns** structure, not in rows. Each month occupies **two adjacent columns**: concept + amount.

Example:

| A (Concepto enero 2026) | B (Importe enero 2026) | C (Concepto febrero 2026) | D (Importe febrero 2026) | … |
|---|---|---|---|---|
| Mercadona | -32,50 | Apple | -9,99 | … |
| Carrefour | -45,00 | Spotify | -10,99 | … |
| ... | ... | ... | ... | … |
| **gastos innecesarios** | =SUM(...) | **gastos innecesarios** | =SUM(...) | … |
| **gastos totales** | =SUM(...) | **gastos totales** | =SUM(...) | … |
| **extraordinarios** | … | **extraordinarios** | … | … |

**Month headers (row 1)**: the script looks for headers in **row 1** with the pattern `"<month in Spanish> <year>"` for concept and `"importe <month> <year>"` (case-insensitive) for amount. Valid examples:
- `enero 2026` (concept column) and `importe enero 2026` (amount column)
- `Febrero 2026` and `Importe febrero 2026`

**Summary block at the end** (configurable): the script detects the end of each month by looking for any of these texts in the concept column:
- `gastos innecesarios`
- `gastos totales`
- `extraordinarios`

When syncing, it **inserts new rows just above** the summary block and shifts the summary down.

## 2. `Ingresos` tab

Same structure as Gastos, but the summary markers are:
- `ingresos totales`
- `ingresos`
- `totales`

## 3. `Dinero invertido {YYYY}` tab

Investment summary (savings plan + saveback + manual purchases) by month and by asset.

| A (Activo) | B | C | … (one month per column) |
|---|---|---|---|
| | enero 2026 | febrero 2026 | … |
| SP 500 | 240 | 240 | |
| MSCI EM IMI | 220 | 220 | |
| MSCI EUR | 80 | 80 | |
| SMALL CAPS | 160 | 160 | |
| MSCI India | 50 | 50 | |
| Solana | 50 | 50 | |
| SAVEBACK | 15,32 | | |

- **Row 1**: header with the format `<month> <year>` (the same months as in Gastos).
- **Column A**: asset name. Must match the value in `asset_name_map` in `config.yaml`. If TR sends an asset that is not mapped, the script creates a new row with the original title and warns.
- The script **only overwrites** the cells of the current month and later; past months are never touched.

The year of this tab is controlled in `config.yaml`:
```yaml
sheets:
  investments_year_format: "Dinero invertido {year}"
  investments_year: null   # null = current year
```

## 4. `Calculo ganancias` tab

Snapshot of the current value of each portfolio asset. The script **only writes** a specific range of column C.

| A (Asset) | B (something optional) | C (current value €) |
|---|---|---|
| sp500 | … | 1234,56 |
| sp500 tech | … | 567,89 |
| em imi | … | 890,12 |
| small caps | … | 345,67 |
| msci eur | … | 234,56 |
| india | … | 123,45 |
| solana | … | 67,89 |

- The **exact range** and the ISINs that get written are configured in `config.yaml`:
  ```yaml
  portfolio_value_range: "C2:C8"   # as many rows as entries in portfolio_cell_map
  portfolio_cell_map:
    - { isin: "IE00B5BMR087", label: "sp500" }
    - { isin: "IE00B3WJKG14", label: "sp500 tech" }
    - ...
  ```
- The script doesn't touch the headers or columns A/B; it only writes the netValue in the configured range.

## 5. `Estado sync` tab

Small tab with two columns that the script **creates automatically** if it doesn't exist. Serves as a visual reminder of the last time you synced.

| A (Process) | B (Last OK) |
|---|---|
| Portfolio | 2026-04-25 09:30:12 |
| Sync completo | 2026-04-25 09:31:45 |

Column A labels are configured in `config.yaml`:
```yaml
status_labels:
  portfolio: "Portfolio"
  sync: "Sync completo"
```

## 6. `_sync_state` tab (hidden)

Internal tab the script creates automatically to dedup already-synced events. **Don't delete or edit by hand.** It's hidden automatically.

## 6.1 `_snapshots` tab (hidden)

Tab the script creates automatically when running `make insights`, `make portfolio`, or `make backfill-snapshots`. One row per snapshot:

| ts | cash_eur | positions_value_eur | cost_basis_eur | total_eur |
|---|---|---|---|---|
| 2026-04-26T13:50:13.411535+02:00 | 13127,46 | 9423,89 | 8424,2 | 22551,35 |

Used to compute MWR YTD / 12 months (needs the value of positions at the start of the period). **Don't delete or edit by hand.** The name can be changed in `config.yaml > sheets.snapshots`.

## 6.2 `_snapshots_positions` tab (hidden)

Same pattern but with per-position breakdown. One row per (snapshot, ISIN):

| ts | isin | title | shares | net_value_eur | cost_basis_eur |
|---|---|---|---|---|---|
| 2026-04-26T13:50:13… | IE00B5BMR087 | Core S&P 500 USD | 6.97 | 4205,81 | 3577,44 |

Useful for per-asset evolution charts over time. The name can be changed in `config.yaml > sheets.snapshots_positions`.

## 7. `Renta YYYY` tab (created by `make renta`)

When you run `make renta`, the script creates/overwrites this tab with the full IRPF report (FIFO + dividends + interest + bonds + Modelo 720 position). No need to create it by hand.

---

## Tab summary

| Tab | Create by hand? | Does the script touch it? |
|---|---|---|
| `Gastos` | Yes (with headers + summary block) | Inserts rows |
| `Ingresos` | Yes (with headers + summary block) | Inserts rows |
| `Dinero invertido YYYY` | Yes (with month headers + asset rows) | Overwrites cells from current month onward |
| `Calculo ganancias` | Yes | Overwrites configured range |
| `Estado sync` | No (auto) | Reads/writes |
| `_sync_state` | No (auto, hidden) | Reads/writes |
| `_snapshots` | No (auto, hidden) | Reads/append (insights, portfolio, backfill) |
| `_snapshots_positions` | No (auto, hidden) | Append (insights, portfolio, backfill) |
| `Renta YYYY` | No (auto, created by `make renta`) | Overwrites entirely |

If your Sheet has another structure, **edit the tab names and markers in `config.yaml`** or adapt the code.
