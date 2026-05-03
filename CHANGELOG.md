# Changelog

Follows the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format and [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — Investment insights & analytics

- **`make insights`** (`tr_sync.py --insights`): console output with net worth, returns, and contributions. Doesn't touch the Sheet (except the hidden `_snapshots` tabs). Blocks:
  - Current net worth: ETFs/stocks, crypto, cash, total (separated as in the TR app).
  - Returns: cost basis without/with saveback, unrealized return on your own money (matches Excel and the TR app), unrealized return on gross cost basis.
  - Historical returns: net contributed + MWR (XIRR) all-time, YTD and trailing 12 months annualized, in two modes (saveback as income vs as contribution).
  - Monthly contributions: this month vs the average of the last 12 months, with `Δ vs average`.
  - Concentration: per-position distribution with bar chart + `⚠ alta` alert if any position exceeds the threshold (configurable, default 35%).
- **`make backfill-snapshots`** (`tr_sync.py --backfill-snapshots`): reconstructs historical snapshots via TR's `aggregateHistoryLight`. Supports `--start YYYY-MM-DD` and `--frequency weekly|biweekly|monthly`. Unlocks YTD/12m MWR without waiting for snapshots to accumulate.
- **`make features`** (`tr_sync.py --features`): table with all product features, their config status, and the active broker's support.
- **`tr_sync.py --debug-isin ISIN`**: lists every transaction the adapter extracts for a given ISIN. Useful to reconcile against external sources (manual Excel).

### Added — Architecture

- **`core/types.py`**: broker-agnostic model (Transaction, Position, PortfolioSnapshot, TxKind). Frozen dataclasses with documented sign convention and fields for `is_bonus`, `from_cash`, `cost_basis_eur`, `exchange_id`.
- **`core/metrics.py`**: pure functions over the agnostic model — `xirr`, `mwr`, `simple_return`, `unrealized_return`, `unrealized_return_user_paid`, `cost_basis_user_paid_per_isin`, `concentration`, `monthly_contributions`, `contribution_vs_average`.
- **`core/backfill.py`**: pure historical reconstruction — `shares_at`, `cash_at`, `reconstruct_snapshot_at`. No I/O.
- **`core/snapshot_store.py`**: `SnapshotStore` protocol + schema and pure conversion `snapshot_to_rows` + helper `snapshot_value_at`.
- **`core/features.py`**: `FEATURE_REGISTRY` registry with each feature and its required capabilities. Functions `is_feature_enabled`, `is_feature_supported`, `feature_status`.
- **`brokers/tr/__init__.py`**: declares the `CAPABILITIES` (set of strings) that TR supports. Other brokers will export their own set.
- **`brokers/tr/adapter.py`**: `fetch_transactions`, `fetch_snapshot`, `fetch_price_history`, `fetch_price_history_with_fallback` (tries multiple exchanges for crypto ISINs), `raw_event_to_tx`. Maps TR event types to `TxKind`. Marks saveback (`is_bonus=True`, `from_cash=False`) and gifts (`is_bonus=False`, `from_cash=False`).
- **`storage/sheets/`**: backend-specific implementations for Google Sheets — `client.py` (open_spreadsheet), `status_store.py` (StatusStore), `sync_state_store.py` (SyncStateStore dedup), `snapshot_store.py` (SheetsSnapshotStore aggregated + per position).
- **New hidden tabs** in the Sheet:
  - `_snapshots`: one row per snapshot with `ts | cash_eur | positions_value_eur | cost_basis_eur | total_eur`. Automatic append on each `make insights / portfolio` and on backfill.
  - `_snapshots_positions`: one row per (snapshot, position) with `ts | isin | title | shares | net_value_eur | cost_basis_eur` for per-asset evolution.

### Added — Config

- **`features.{insights, concentration, snapshot_persist, backfill_snapshots, saveback_metrics, ...}`**: individual toggles per feature. The feature is disabled either if the config turns it off or if the broker doesn't support it.
- **`concentration_threshold`** (default 0.35): % above which a position is flagged as "high concentration" in the corresponding block.
- **`sheets.snapshots`** (default `"_snapshots"`) and **`sheets.snapshots_positions`** (default `"_snapshots_positions"`): names of the hidden tabs for history.

### Changed

- Repo renamed from `tr-sync` to `broker-sync` to reflect future multi-broker support. URL: https://github.com/Daniips/broker-sync.
- Refactored to a modular layout: `core/` (pure), `brokers/<x>/` (data sources), `storage/<backend>/` (sinks). `tr_sync.py` drops to ~2270 lines (pre-refactor: ~2400).
- Code comments and docstrings in `core/`, `brokers/`, `storage/` are in English.
- `ARCHITECTURE.md` rewritten to reflect the current structure and the core/brokers/storage pattern.

### Changed — `sync_renta` extracted to `reports/renta_es.py`

- ~600 lines of Spanish IRPF logic moved to `reports/renta_es.py`. `tr_sync.py` drops from ~2380 to ~1925 lines (-455).
- Config-derived constants (`DIVIDEND_SUBTITLES`, `BOND_*_SUBTITLES`, `GIFT_COST_OVERRIDES`, `CRYPTO_ISINS`) stay in `tr_sync.py` and `reports.renta_es` references them via `tr_sync.X`.
- Compat shims in `tr_sync.py` (`_collect_*`, `_build_lots_and_sales`, `_retentions_by_country`) so existing tests and external consumers don't break. Lazy import from the shim → zero import cycle.
- New `reports/` folder for future tax regimes (UK ISA, DE Steuerbericht, PT IRS, etc.) — each as a sibling module consuming `core.fifo` and `brokers/tr/parser.py`.

### Added — Performance attribution per position

- **`core.metrics.per_position_attribution()`**: for each live position, computes its individual MWR (XIRR over the ISIN's flows: BUYs, SELLs, DIVIDENDs + current value) and its weighted contribution to portfolio return (`MWR × value_pct`).
- **"PERFORMANCE ATTRIBUTION PER POSITION" block** in `make insights`. Table sorted by absolute contribution. Shows which positions drive the return and which drag.
- 4 new tests in `test_metrics.py`.

### Added — Benchmark comparison

- **`benchmark_isin`** and **`benchmark_label`** (new config): activates the "RETURN VS BENCHMARK" block in `make insights`. Compares your MWR (income mode) against the benchmark's annualized return over all-time / YTD / 12m with Δ in pp.
- **`core.metrics.benchmark_return()`**: pure function that computes the annualized return of a benchmark between two dates from its price history.
- **Extended cache** (`core.cache.py` v2): `(snapshot, txs, benchmarks)` instead of just `(snapshot, txs)`. A single login + download per session, also for benchmarks. Version bumped to invalidate old caches automatically.
- 6 new tests in `test_metrics.py` for `benchmark_return` (including extrapolated short periods, negative returns, edge cases).

### Added — Currency exposure & MWR sanity export

- **`asset_currencies`** (new config): dict `{ISIN: currency}`. Activates the "CURRENCY EXPOSURE" block in `make insights` that groups total net worth (cash + positions) by denomination currency. ISINs without an entry go to "UNKNOWN".
- **`core.metrics.currency_exposure()`**: pure broker-agnostic function.
- **`make mwr-flows`** (`tr_sync.py --mwr-flows [--bonus-as deposit]`): exports MWR cash flows in TSV so you can paste into Sheets/Excel and verify with native `=XIRR`. Useful as a sanity check of all-time MWR.
- 3 new tests in `test_metrics.py` for currency exposure.

### Added — Solana in backfill

- **TR exposes Solana on exchange `BHS`** (Bitstamp Handelssystem). The field comes from `compactPortfolio.exchangeIds`, already captured by the adapter since the previous refactor.
- **`fetch_instrument_exchanges()`** in the TR adapter: queries `instrument_details(isin)` to discover non-hardcoded exchanges. Used as the last fallback in `fetch_price_history_with_fallback`.
- **Determinism in backfill**: timestamps normalized to `T12:00:00`. Re-running `make backfill-snapshots` no longer generates duplicate rows (dedup by ts now works).

### Added — Per-asset concentration limits

- **`concentration_limits`** (new config): dict `{ISIN: float}` to define an individual cap per asset. Overrides the global `concentration_threshold`. Useful for setting different tolerances per asset type (high for core ETFs, low for crypto).
- **`core.metrics.concentration()`** now accepts `limits` and `default_threshold`. Each result entry includes `limit`, `margin_pp`, and `exceeded` so the caller can decide how to present the info.
- **`make insights` display**: each position shows its effective limit and margin (or "EXCEEDED by X pp"). Summary at the end: "✓ All within limit" or "⚠ N over".
- 3 new tests in `test_metrics.py` covering: per-ISIN limits, fallback to default_threshold, no-limit when none provided.

### Added — Performance & docs (post-refactor)

- **`core/cache.py`** + flag `--refresh`: pickle cache of `(snapshot, txs)` with TTL=5min. Chains `make insights` / `make portfolio` / `make backfill-snapshots` without unnecessary re-fetch. The TR login is fully avoided when there's a fresh cache.
- **`INSIGHTS.md`**: doc explaining the `make insights` output block by block, the 2 readings of cost basis, the 3 MWR horizons, the income/deposit toggle, and FAQ about common questions.
- **`IMPROVEMENTS.md`**: prioritized roadmap of pending improvements (renta extraction, per-asset limits, crypto backfill, telemetry, alerts, etc.).
- **`test_adapter.py`**: 24 TR adapter tests covering each `eventType` (BUY/SELL/SAVEBACK/GIFT/DIVIDEND/INTEREST/DEPOSIT/WITHDRAWAL/CANCELED/missing-data) with a mocked parser. Total suite goes from 116 to 140 tests.

### Added

- Toggles `features.{expenses,income,investments,portfolio}` in `config.yaml` to disable parts of the sync.
- Toggles `renta.{fifo,dividends,interest,bonds,summary_by_box,retentions,saveback,crypto,modelo720}` to customize the IRPF report sections.
- Configurable `month_names` (list of 12 month names) to support languages other than Spanish.
- `config.yaml` validation at startup with clear messages if a field is missing or has an incorrect format.
- `CONTRIBUTING.md` with a guide for new contributors.
- `CHANGELOG.md` (this file).
- `make init-sheet` (`tr_sync.py --init-sheet`): bootstraps tabs in the Google Sheet, idempotent. Pre-fills the labels of portfolio_cell_map next to the portfolio_value_range.
- `make doctor` (`tr_sync.py --doctor`): health check that verifies config, pytr session, gspread OAuth, Sheet accessibility, required tabs, and consistency portfolio_cell_map ↔ portfolio_value_range. Exits with code 1 if errors are found.
- CI workflow `.github/workflows/tests.yml` that runs the tests on each push/PR (Python 3.11 and 3.12).
- README in English (`README.md`); Spanish moved to `README.es.md` with cross-link between the two.
- `ledger` layout for Gastos/Ingresos as an alternative to the original `monthly_columns`. One row per event with Date/Concept/Amount columns. Configurable per tab via `sheets.expenses_layout` / `sheets.income_layout`. Customizable headers with `sheets.ledger_headers`. `init-sheet` creates the headers automatically when the layout is `ledger`.
- More fields extracted to config to make the script truly reusable:
  - `saveback_label` (label of the Saveback row in Investments).
  - `init_sheet_headers` (headers written by `init-sheet`).
  - `subtitle_translations` (German → user language translations, merged with the default Spanish).
  - `renta_classification.dividend_subtitles` / `bond_cash_subtitles` / `bond_maturity_subtitles` (classification of TR subtitles for the IRPF report; lets you add variants without touching code).
- Full cell configurability:
  - `sheets.ledger_columns`: A1 columns for date/concept/amount in `ledger` layout (defaults A/B/C). Can be non-contiguous.
  - `sheets.month_header_amount` and `sheets.month_header_concept`: month-header patterns in `monthly_columns` layout. Support `{month}` and `{year}` for internationalization.
- **Interactive config CLI** (`config_cli.py`, new dep: `questionary`):
  - `make config-init`: step-by-step wizard to create `config.yaml` from scratch (asks for sheet_id, layouts, tabs, portfolio, asset_name_map, etc.).
  - `make config-show` / `config-validate` / `config-features` for inspection and toggles.
  - `python tr_sync.py config set KEY VALUE` (dot-notation) to change a specific field.
  - `python tr_sync.py config add-asset ISIN LABEL` / `remove-asset` to manage `portfolio_cell_map` without touching YAML.
  - `python tr_sync.py config add-ignore SECTION TEXT` / `remove-ignore` to manage `ignore_events`.
  - The `config` subcommand short-circuits at import time: starts without requiring a pre-existing `config.yaml` (ideal for first setup) and without loading `pytr`/`gspread`.

## [0.1.0] — 2026-04

First public release of the project.

### Features

- Sync of expenses / income / investments for the last month from Trade Republic to a Google Sheet.
- Portfolio snapshot: writes the current `netValue` of each asset to a configurable range.
- IRPF report (`make renta`):
  - Capital gains/losses with FIFO per ISIN.
  - Support for stocks, ETFs, gifts (`ETF-Geschenk`), lottery (`Verlosung`), foreign bonds with coupon + maturity.
  - Dividends with gross/withholding/net and breakdown by country of origin for double-taxation deduction.
  - Interest, saveback, crypto position, and total balance for Modelo 720/721.
  - Simultaneous dump to console and to a `Renta YYYY` tab in the Sheet.
- Configuration in `config.yaml` (gitignored). Template in `config.example.yaml`.
- `ignore_events` filters to discard events you already manage manually (auto-transfers, salary).
- Manual `gift_cost_overrides` override for gifts whose details TR doesn't parse.
- `inspect_events.py` utility to inspect raw events by type, ISIN, or title.
- Optional GitHub Actions workflow for automated sync.
- Documentation: `README.md`, `CONFIG.md`, `SHEET_TEMPLATE.md`, `RENTA.md`.
- 53 unit tests on pure logic (parsers, FIFO, aggregators) without network.
- MIT License.
