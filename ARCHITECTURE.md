# Architecture

The repo follows a three-layer pattern that keeps broker integrations, business logic, and persistence backends independent so each can evolve at its own pace.

---

## Layout

```
broker-sync/
├── tr_sync.py              ← Entry point (CLI orchestration) for Trade Republic
├── inspect_events.py       ← Raw TR event explorer
├── config_cli.py           ← Interactive config CLI
│
├── core/                   ← Pure logic, no I/O
│   ├── types.py            ← Transaction, Position, PortfolioSnapshot, TxKind
│   ├── metrics.py          ← MWR/XIRR, unrealized return, concentration, contributions
│   ├── fifo.py             ← Generic FIFO matcher
│   ├── backfill.py         ← Historical state reconstruction
│   ├── snapshot_store.py   ← SnapshotStore protocol + schema (interface only)
│   ├── features.py         ← Feature registry + capability check
│   └── utils.py            ← Number parsers, A1 helpers
│
├── brokers/                ← Data sources
│   └── tr/
│       ├── __init__.py     ← TR CAPABILITIES set
│       ├── adapter.py      ← TR raw events ↔ core.types
│       └── parser.py       ← TR event field extractors
│
├── storage/                ← Persistence backends (sinks)
│   └── sheets/
│       ├── client.py       ← open_spreadsheet helper
│       ├── status_store.py ← Visible "Estado sync" tab
│       ├── sync_state_store.py ← Hidden tab for event dedup
│       └── snapshot_store.py   ← SheetsSnapshotStore (impl of core protocol)
│
├── test_metrics.py         ← Unit tests for core.metrics (synthetic data)
├── test_backfill.py        ← Unit tests for core.backfill
├── test_tr_sync.py         ← Tests for sync logic in tr_sync.py
│
└── ... (config, docs, Makefile, requirements)
```

`tr_sync.py` retains the orchestration logic (CLI dispatcher, sync flow, IRPF report generation, `init_sheet`, doctor health-check). The pure modules in `core/` and the storage adapters in `storage/` are documented in English.

---

## Three-layer pattern

```
   ┌────────────┐  fetch_*  ┌────────────┐  metrics  ┌────────────┐
   │  brokers/  │ ────────▶ │   core/    │ ────────▶ │  output    │
   │  (TR, …)   │           │ (logic +   │           │  (CLI, …)  │
   └────────────┘           │  types)    │           └────────────┘
                            └─────┬──────┘
                                  │ persist via
                                  ▼  protocol
                            ┌────────────┐
                            │ storage/   │
                            │ (Sheets, …)│
                            └────────────┘
```

### Why three layers

- **`brokers/<x>/`** owns the broker-specific protocol (TR's WebSocket, Kraken's REST, etc.) and translates raw events to `core.types.Transaction` / `Position` / `PortfolioSnapshot`. Each broker also declares its `CAPABILITIES` so features can degrade gracefully when running against a broker that doesn't support them.

- **`core/`** is pure: takes `Transaction[]` and `PortfolioSnapshot` in, returns numbers / dicts out. No imports from `brokers/` or `storage/`. Tested with synthetic data, no network.

- **`storage/<backend>/`** implements the persistence protocols defined in `core/` (e.g. `SnapshotStore`). Today only Google Sheets; tomorrow you could add SQLite or JSON without touching `core/` or `brokers/`.

---

## Features and capabilities

Each product feature (`insights`, `concentration`, `backfill_snapshots`, `saveback_metrics`, …) is declared in `core/features.py` with the broker capabilities it requires:

```python
"saveback_metrics": Feature(
    name="saveback_metrics",
    description="Unrealized return discounting saveback (when the broker has saveback)",
    requires=("fetch_snapshot", "fetch_transactions", "saveback"),
)
```

Each broker exports its capabilities in `brokers/<x>/__init__.py`:

```python
# brokers/tr/__init__.py
CAPABILITIES = frozenset({
    "fetch_transactions", "fetch_snapshot", "fetch_price_history",
    "expense_tracking", "saveback", "gifts", "tax_renta_es",
})
```

`is_feature_enabled(name)` returns True only if (a) the user hasn't disabled the feature in `config.yaml > features` AND (b) the active broker supports all required capabilities. `make features` prints the resulting table.

This means: when a future broker (e.g. IBKR) lacks `saveback`, the `saveback_metrics` feature auto-degrades to off without code changes. The user sees in `make features` why each feature is on/off.

---

## Adding a new broker

Suppose you want to add **Kraken**.

### 1. `brokers/kraken/__init__.py` — declare capabilities

```python
CAPABILITIES = frozenset({
    "fetch_transactions",
    "fetch_snapshot",
    "fetch_price_history",
    # No 'expense_tracking', 'saveback', 'gifts', 'tax_renta_es' — Kraken doesn't have those
})
```

Features that depend on missing capabilities (`saveback_metrics`, `renta`, `expenses`/`income`) auto-disable.

### 2. `brokers/kraken/adapter.py` — translate to `core.types`

Implement:

```python
async def fetch_transactions(client, *, tz, since=None) -> list[Transaction]: ...
async def fetch_snapshot(client, *, tz) -> PortfolioSnapshot: ...
async def fetch_price_history(client, isin, *, range_str="1y") -> list[dict]: ...
```

Mapping rules:
- BUY/SELL → `TxKind.BUY` / `TxKind.SELL`. Set `from_cash=True` when paid by user.
- Bonuses / referrals → `is_bonus=True` and `from_cash=False`.
- Cash flows (deposits/withdrawals) → `TxKind.DEPOSIT` / `WITHDRAWAL`.

### 3. `kraken_sync.py` — entry point

Mirror `tr_sync.py` but `from brokers.kraken import CAPABILITIES as BROKER_CAPABILITIES` and `from brokers.kraken.adapter import fetch_*`. Reuse `core.metrics`, `core.backfill`, `storage.sheets.*` as-is.

### 4. `make kraken-sync`, `make kraken-insights` — Makefile targets

```makefile
kraken-insights:
	$(PYTHON) kraken_sync.py --insights
```

### 5. Tests

Unit tests for the new adapter (raw event fixture → expected `Transaction`). `core/` tests don't change (they're broker-agnostic by construction).

---

## Adding a new storage backend

To persist snapshots to SQLite instead of Sheets:

1. Create `storage/sqlite/snapshot_store.py` with a class `SqliteSnapshotStore` that implements the `core.snapshot_store.SnapshotStore` protocol (`append`, `append_batch`, `load_history`, `load_timestamps`).
2. In `tr_sync.py`, swap `_make_snapshot_store` to return `SqliteSnapshotStore(...)` instead of `SheetsSnapshotStore(...)`.

That's it. Nothing in `core/` or `brokers/` changes.

---

## Why this structure

- **No forced abstract `Broker` interface.** Each broker has its own quirks (TR uses German subtitles + WebSocket cookies; Kraken uses pairs + API key/secret). A universal interface would be lossy. Instead each broker exposes its own functions whose signatures are fixed by the protocols `core/` expects (`Transaction`, `PortfolioSnapshot`).

- **Pure `core/`** is testable with synthetic data, runs in 2ms, gives you confidence in the math before any broker call.

- **Storage as protocol.** `core/` defines what it needs (`SnapshotStore.append`, `SnapshotStore.load_history`); the implementation can be Sheets, SQLite, JSON, in-memory for tests, etc.

- **Capabilities + features.** When a feature can't run against the current broker, it disables itself with a clear message instead of crashing mid-execution.

---

## What's still in `tr_sync.py`

These pieces are deliberately not extracted — they're TR-flavoured today and the seams aren't clean enough to abstract without a second consumer:

- Sync writers for monthly_columns / ledger layouts (deeply tied to Spanish month names, summary blocks, etc.).
- IRPF report generator (`sync_renta`): Spain-specific, would need redesign to support other tax regimes.
- `init_sheet`: bootstraps the user's Sheet structure. Could move when there's a second sink.
- `doctor`: health-check covering config + pytr + gspread + Sheet tabs.

When a second broker arrives, these will likely move to a `core/sync/` layer or a `tax_reports/` package per regime.
