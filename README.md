# tr-sync

> 🇪🇸 [Versión en español](README.es.md) · 🇬🇧 you are reading the English version.

Syncs **Trade Republic** events (card transactions, incoming/outgoing transfers, savings plan executions, dividends, interest, etc.) with a personal **Google Sheet** budgeting / portfolio tracker. Also generates a Spanish **IRPF tax report** automatically: FIFO capital gains/losses, dividends, interest, bond returns, foreign withholding by country, crypto snapshot and total balance for Modelo 720/721.

> Built for personal use. Published under MIT so anyone can adapt it to their own Sheet — sensitive data (Sheet ID, ISINs, asset mappings) lives in `config.yaml`, which is gitignored.

> **Documentation note**: this README is in English, but the in-depth docs (`CONFIG.md`, `SHEET_TEMPLATE.md`, `RENTA.md`) are in Spanish, since the IRPF tax report is Spain-specific. Translations welcome via PR.

---

## Table of contents

- [What it does](#what-it-does)
- [Requirements](#requirements)
- [Quick install](#quick-install)
- [Configuration](#configuration)
- [Usage](#usage)
- [Available `make` commands](#available-make-commands)
- [Ignoring events](#ignoring-events)
- [IRPF report (`make renta`)](#irpf-report-make-renta)
- [GitHub Actions automation](#github-actions-automation-optional)
- [Raw event inspection (debug)](#raw-event-inspection-debug)
- [Repo layout](#repo-layout)
- [FAQ and troubleshooting](#faq-and-troubleshooting)
- [License and disclaimer](#license-and-disclaimer)

---

## What it does

- **Monthly sync** of expenses / income / investments to your Google Sheet, organising last-month events into tabs like "Gastos", "Ingresos" and "Dinero invertido <year>". Detects the summary block at the bottom of each month and **inserts new rows just above it** without overwriting.
- **Portfolio snapshot** that writes the current value of each asset to a configurable range of the "Calculo ganancias" tab.
- **Investment insights** (`make insights`): patrimonio, two reads of unrealized return (your money vs broker-cost-basis-with-saveback), all-time / YTD / 12-month MWR (XIRR) annualized, monthly contributions vs 12-month average, concentration alert per position. Console output, no Sheet writes.
- **Historical snapshot backfill** (`make backfill-snapshots`): reconstructs past portfolio states using TR's price-history API to enable proper YTD/12m MWR from day one (instead of waiting weeks for snapshots to accumulate).
- **Full IRPF tax report** for last year (or any year you specify): automatic FIFO for capital gains/losses, dividends with retentions per country (double-taxation deduction), interest, net yield from foreign bonds, crypto snapshot (informative for Spain's Modelo 721) and total TR balance (orientative Modelo 720). Written to a `Renta YYYY` tab.
- **Feature toggles + capability map** (`make features`): each product feature declares the broker capabilities it needs; if you switch to a broker that lacks them (e.g. saveback only exists in TR), the feature auto-disables instead of crashing.
- **Configurable filters** to ignore events you already manage manually (e.g. salary received in another bank account and then transferred to TR — to avoid duplication).
- **Automatic dedup** of already-synced events (via a hidden `_sync_state` tab).
- **`--dry-run`** mode to verify before writing.

---

## Requirements

- **Python 3.11+**
- A Trade Republic account
- A Google Cloud Console project with the **Google Sheets API** enabled and OAuth credentials downloaded.
- (Optional) A GitHub account if you want to automate sync with Actions.

---

## Quick install

```bash
git clone https://github.com/<your_user>/tr-sync.git
cd tr-sync
make setup
make config-init     # interactive wizard: asks step by step (sheet_id, layout, ISINs...)
make login           # first time: SMS verification
make init-sheet      # creates required tabs in your Sheet
make doctor          # health check before first sync
make verify          # portfolio dry-run to confirm OAuth
```

> If you prefer to hand-edit the YAML: `cp config.example.yaml config.yaml` and fill following [CONFIG.md](CONFIG.md) (Spanish).

If `make verify` shows your asset netValues without writing anything, you're good to go.

---

## Configuration

Two relevant files:

- **`config.yaml`** — your personal config (gitignored). The only file you **must edit**. Full reference of every field in [CONFIG.md](CONFIG.md) (Spanish).
- **`Makefile.local`** — optional, gitignored. For environment-specific variables (e.g. `REPO := your_user/your_repo` for GitHub Actions targets).

### Sheet structure

The script assumes a specific tab and column structure. Read [SHEET_TEMPLATE.md](SHEET_TEMPLATE.md) (Spanish) **before** running `make sync` for the first time, or use `make init-sheet` to bootstrap the tabs automatically.

### Google Sheets OAuth (first time)

1. Go to [Google Cloud Console](https://console.cloud.google.com/), create a project and enable the **Google Sheets API**.
2. Create OAuth credentials of type **Desktop app**.
3. Download the JSON and save it as `~/.config/gspread/credentials.json`.
4. The first time the script opens the Sheet, gspread will pop a browser; authorize with the Google account that owns your Sheet. The token is stored in `~/.config/gspread/authorized_user.json`.

### Trade Republic — login

```bash
make login
```

You'll be prompted for phone, PIN and SMS. The session lives in `~/.pytr/`. While the cookie is valid (~2 days normal, up to a month with `--store_credentials`), no SMS is needed.

---

## Usage

```bash
make sync              # monthly sync of expenses / income / investments
make portfolio         # portfolio snapshot
make verify            # portfolio dry-run (no Sheet writes)
make renta             # IRPF report for last year
make renta YEAR=2024   # IRPF report for a specific year
make inspect           # raw event debug
make test              # unit tests
```

If TR cookie expires, run `make login` again.

---

## Available `make` commands

### Setup

| Command | What it does |
|---|---|
| `make setup` | Creates `.venv`, installs dependencies |
| `make login` | Logs into TR (SMS if needed) |
| `make config-init` | Interactive wizard to create `config.yaml` (recommended for new users) |
| `make init-sheet` | Creates missing tabs in your Google Sheet (idempotent) |
| `make doctor` | Health check: verifies setup is ready before syncing |

### Daily use

| Command | What it does |
|---|---|
| `make sync` | Syncs last-month expenses/income/investments |
| `make portfolio` | Portfolio snapshot (current value per asset) |
| `make verify` | Local portfolio dry-run |
| `make insights` | Patrimonio + rentabilidad (MWR all-time/YTD/12m) + concentración + aportaciones (no Sheet writes) |
| `make insights --verbose` | Same + per-position breakdown for diagnosis |
| `make features` | Table: every product feature, config toggle, broker support |
| `make renta` | IRPF report for last year |
| `make renta YEAR=N` | IRPF report for a specific year |
| `make backfill-snapshots` | Reconstruct past snapshots (1 year weekly default) for YTD/12m MWR. Optional `START=YYYY-MM-DD` and `FREQ=weekly\|biweekly\|monthly`. |
| `make inspect` | Inspect raw TR events (debug) |
| `python tr_sync.py --debug-isin ISIN` | Dump every parsed transaction for one ISIN — reconcile against an external Excel/spreadsheet |
| `make test` | Unit tests (no network, deterministic) |

### GitHub Actions (optional)

These targets require `gh` CLI authenticated and `REPO=user/repo` (the cleanest way: define `REPO := user/repo` in `Makefile.local`).

| Command | What it does |
|---|---|
| `make all` | login → sync → portfolio → upload-secret → clear-cache |
| `make refresh-cookie` | login → verify → upload-secret → clear-cache |
| `make upload-secret` | Bundles `~/.pytr/` and uploads it as `PYTR_KEYS_B64` secret |
| `make clear-cache` | Deletes `pytr-session-*` Actions caches |

---

## Config CLI (no YAML editing required)

A `config` subcommand lets you manage `config.yaml` entirely from the terminal, no need to open the file by hand.

### Available commands

| Command | What it does |
|---|---|
| `make config-init` | Interactive step-by-step wizard (first setup) |
| `make config-show` | Prints current `config.yaml` |
| `make config-validate` | Validates config and reports errors clearly |
| `make config-features` | Checkbox wizard to toggle features |
| `python tr_sync.py config set KEY VALUE` | Change a single value. Dot-notation: `set sheets.expenses Gastos` |
| `python tr_sync.py config add-asset ISIN LABEL` | Append entry to `portfolio_cell_map` |
| `python tr_sync.py config remove-asset ISIN` | Remove an entry |
| `python tr_sync.py config add-ignore SECTION TEXT` | Append pattern to `ignore_events.{income,expenses}.title_contains` |
| `python tr_sync.py config remove-ignore SECTION TEXT` | Remove a pattern |

### Notes

- The `config init` wizard asks sheet_id, features, layouts, tab names, portfolio (ISINs+labels), `asset_name_map`, crypto, timezone, etc. Takes <2 minutes.
- `set` / `add-*` regenerate the YAML on save and **lose comments** from the file. If you want them preserved, edit by hand.
- All `config` subcommands work **without an existing `config.yaml`** or `pytr`/`gspread` loaded — ideal for first-time setup.

---

## Ignoring events

Got incoming or outgoing transfers you **already manage manually** in your Sheet and don't want the sync to duplicate? E.g. salary received in another account and moved to TR, or personal refunds between your accounts.

In `config.yaml`:

```yaml
ignore_events:
  income:
    title_contains:
      - "your name"            # incoming auto-transfers
      - "your other bank"
    subtitle_contains: []
  expenses:
    title_contains: []
    subtitle_contains: []
```

Match is **case-insensitive** and by **substring**, on both `title` and `subtitle` of the event. If an event matches, it's discarded from the sync and logged in detail:

```
[Ingresos] 1 event(s) ignored by config.yaml → ignore_events:
   - 2026-04-30   1850.00 €  'Your Name Surname'
```

Not sure what string to use? Run:
```bash
.venv/bin/python inspect_events.py --eventtype BANK_TRANSACTION_INCOMING
```

It dumps all incoming transfers raw with their `title`/`subtitle` so you can pick a pattern.

---

## IRPF report (`make renta`)

Detailed guide at **[RENTA.md](RENTA.md)** (Spanish, since the report is Spain-specific). Quick summary:

- 9 sections: capital G/L (FIFO), dividends, interest, bonds, summary by tax box, retentions per country, saveback, crypto snapshot, total Modelo 720 balance.
- Supports stocks, ETFs, gifts (`ETF-Geschenk`), lottery (`Verlosung`), foreign bonds with coupons + maturity.
- Output to console and to a `Renta YYYY` tab in the Sheet.
- Important: **always verify** the figures against TR's official PDF "Jährlicher Steuerbericht YYYY" before submitting.

```bash
make renta              # current year − 1
make renta YEAR=2024    # specific year
```

---

## GitHub Actions automation (optional)

The repo ships `.github/workflows/sync.yml`, which can sync daily without your intervention. To enable:

1. Push this repo to GitHub (private is fine).
2. Under **Settings → Secrets**, add:
   - **`PYTR_KEYS_B64`**: contents of `~/.pytr/` packed and base64-encoded. Refreshed via `make upload-secret`.
   - **`GSPREAD_AUTH_B64`**: contents of `~/.config/gspread/` packed and base64-encoded. Manual upload (one-off):
     ```bash
     tar -czf /tmp/gspread.tgz -C $HOME .config/gspread/
     gh secret set GSPREAD_AUTH_B64 --repo $REPO --body "$(base64 -i /tmp/gspread.tgz)"
     ```
3. Set `REPO := your_user/your_repo` in `Makefile.local`.
4. Trigger the workflow from the Actions tab or schedule a cron in `sync.yml`.

> If the TR cookie expires, the workflow will fail and open an issue in your repo. Just run `make refresh-cookie` locally to upload a new one.

---

## Raw event inspection (debug)

`inspect_events.py` helps explore the TR API when something looks off or when you want to know exactly what TR sends:

```bash
.venv/bin/python inspect_events.py                              # summary + last year sales
.venv/bin/python inspect_events.py --year 2024                  # other year
.venv/bin/python inspect_events.py --raw                        # JSON of first sale of the year
.venv/bin/python inspect_events.py --isin US0378331005          # events for a given ISIN
.venv/bin/python inspect_events.py --eventtype INTEREST_PAYOUT  # JSONs of an eventType
.venv/bin/python inspect_events.py --title "Feb. 2025"          # search by title
```

---

## Repo layout

```
tr_sync.py             — Trade Republic entry point (sync + portfolio + insights + renta)
inspect_events.py      — raw event inspection utility (TR-specific)
config_cli.py          — interactive CLI for managing config.yaml

core/                  — pure logic, no I/O / lógica pura, sin I/O
  types.py             — Transaction, Position, PortfolioSnapshot, TxKind
  metrics.py           — MWR/XIRR, plusvalía, concentración, aportaciones
  fifo.py              — generic FIFO matcher
  backfill.py          — historical state reconstruction
  snapshot_store.py    — SnapshotStore protocol + schema
  features.py          — feature registry + capability check
  utils.py             — number / A1 range helpers

brokers/               — data sources
  tr/__init__.py       — TR CAPABILITIES set
  tr/adapter.py        — TR raw events ↔ core.types
  tr/parser.py         — TR event field extractors

storage/                — persistence backends (sinks)
  sheets/client.py      — open_spreadsheet
  sheets/status_store.py    — visible "Estado sync" tab
  sheets/sync_state_store.py — hidden dedup tab
  sheets/snapshot_store.py   — hidden snapshot tabs (agg + per-position)

test_metrics.py        — unit tests for core.metrics (synthetic data)
test_backfill.py       — unit tests for core.backfill
test_tr_sync.py        — unit tests for sync logic

config.example.yaml    — config template (committed)
config.yaml            — YOUR personal config (gitignored)
Makefile               — make targets
Makefile.local         — environment-local variables (gitignored, optional)
README.md              — this file (English)
README.es.md           — Spanish version
ARCHITECTURE.md        — three-layer architecture, adding brokers/storage (bilingual)
CONFIG.md              — reference of every config.yaml field (Spanish)
SHEET_TEMPLATE.md      — expected Google Sheet structure (Spanish)
RENTA.md               — detailed IRPF report guide (Spanish)
LICENSE                — MIT
.github/workflows/     — optional sync workflow + tests CI
```

---

## FAQ and troubleshooting

**Q: TR cookie expired.**
A: `make login` (you'll be asked for SMS). If using Actions, also `make upload-secret` afterwards.

**Q: Sync can't find a tab.**
A: Verify the exact name in `config.yaml > sheets` matches the tab name in your Sheet (case, accents, spaces).

**Q: Salary keeps slipping through despite `ignore_events`.**
A: The pattern must match the actual `title`/`subtitle` from TR, not your alias. Run `inspect_events.py --eventtype BANK_TRANSACTION_INCOMING` to see the actual title and adjust.

**Q: `make renta` says "X shares unmatched — buy history missing".**
A: The parser couldn't find earlier purchases of that ISIN. Common causes: a gift/lottery without parseable metadata (fill `gift_cost_overrides` in `config.yaml` with data from Jährlicher Steuerbericht), or an old purchase with an unsupported event structure (inspect with `inspect_events.py --isin <ISIN>`).

**Q: Why does the script show subtitles in German?**
A: The TR API always replies in German to scripts (it doesn't honour the app language). There's a `SUBTITLE_ES` dict in `tr_sync.py` translating the most common ones for display. If you see something untranslated, add it to the dict.

**Q: Is it safe to publish my `config.yaml`?**
A: No. It's in `.gitignore` for a reason. It contains your Sheet ID (which is useless without your Google permissions, but better not exposed) and the list of ISINs/assets you hold — non-critical but personal info.

**Q: Sheet ID still in old git history?**
A: If it bothers you, rewrite history with `git filter-repo --replace-text`. The ID alone grants no access — Google requires the visitor to have explicit permissions.

---

## License and disclaimer

MIT — see [LICENSE](LICENSE).

> This software is provided as-is, no warranties. The IRPF report numbers are **orientative**: always verify against TR's official Jährlicher Steuerbericht before submitting your tax return. The author is not liable for tax filing errors derived from using this tool.
