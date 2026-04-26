# Architecture · Arquitectura

> 🇬🇧 English first, 🇪🇸 español a continuación.

The repo follows a three-layer pattern that keeps broker integrations, business logic, and persistence backends independent so each can evolve at its own pace.

El repo sigue un patrón de tres capas que mantiene las integraciones de broker, la lógica de negocio y los backends de persistencia independientes para que cada uno evolucione a su ritmo.

---

## Layout · Estructura

```
broker-sync/
├── tr_sync.py              ← Entry point (CLI orchestration) for Trade Republic
│                              Punto de entrada (orquestación CLI) para TR
├── inspect_events.py       ← Raw TR event explorer / Explorador de eventos TR
├── config_cli.py           ← Interactive config CLI / CLI interactiva del config
│
├── core/                   ← Pure logic, no I/O / Lógica pura, sin I/O
│   ├── types.py            ← Transaction, Position, PortfolioSnapshot, TxKind
│   ├── metrics.py          ← MWR/XIRR, plusvalía, concentración, aportaciones
│   ├── fifo.py             ← Generic FIFO matcher / Motor FIFO genérico
│   ├── backfill.py         ← Historical state reconstruction / Reconstrucción histórica
│   ├── snapshot_store.py   ← SnapshotStore protocol + schema (interface only)
│   ├── features.py         ← Feature registry + capability check
│   └── utils.py            ← Number parsers, A1 helpers / Parsers de números
│
├── brokers/                ← Data sources / Fuentes de datos
│   └── tr/
│       ├── __init__.py     ← TR CAPABILITIES set
│       ├── adapter.py      ← TR raw events ↔ core.types
│       └── parser.py       ← TR event field extractors
│
├── storage/                ← Persistence backends (sinks) / Backends de persistencia
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

`tr_sync.py` retains the orchestration logic (CLI dispatcher, sync flow, IRPF report generation, `init_sheet`, doctor health-check) and **stays bilingual-comment free** (it is the integration layer). The pure modules in `core/` and the storage adapters in `storage/` are documented bilingually.

`tr_sync.py` retiene la lógica de orquestación (dispatcher CLI, flujo sync, generación del informe IRPF, `init_sheet`, health-check del doctor) y **se mantiene sin comentarios bilingües** (es la capa de integración). Los módulos puros en `core/` y los adapters de storage en `storage/` están documentados bilingüe.

---

## Three-layer pattern · Patrón de tres capas

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

### Why three layers · Por qué tres capas

- **`brokers/<x>/`** owns the broker-specific protocol (TR's WebSocket, Kraken's REST, etc.) and translates raw events to `core.types.Transaction` / `Position` / `PortfolioSnapshot`. Each broker also declares its `CAPABILITIES` so features can degrade gracefully when running against a broker that doesn't support them.
- **`brokers/<x>/`** posee el protocolo broker-específico (WebSocket de TR, REST de Kraken, etc.) y traduce eventos raw a `core.types.Transaction` / `Position` / `PortfolioSnapshot`. Cada broker declara también sus `CAPABILITIES` para que las features degraden bien cuando el broker no las soporta.

- **`core/`** is pure: takes `Transaction[]` and `PortfolioSnapshot` in, returns numbers / dicts out. No imports from `brokers/` or `storage/`. Tested with synthetic data, no network.
- **`core/`** es puro: recibe `Transaction[]` y `PortfolioSnapshot`, devuelve números/dicts. Sin imports de `brokers/` ni `storage/`. Tests con datos sintéticos, sin red.

- **`storage/<backend>/`** implements the persistence protocols defined in `core/` (e.g. `SnapshotStore`). Today only Google Sheets; tomorrow you could add SQLite or JSON without touching `core/` or `brokers/`.
- **`storage/<backend>/`** implementa los protocolos definidos en `core/` (ej. `SnapshotStore`). Hoy solo Google Sheets; mañana podrías añadir SQLite o JSON sin tocar `core/` ni `brokers/`.

---

## Features and capabilities · Features y capabilities

Each product feature (`insights`, `concentration`, `backfill_snapshots`, `saveback_metrics`, …) is declared in `core/features.py` with the broker capabilities it requires:

Cada feature del producto se declara en `core/features.py` con las capabilities que necesita del broker:

```python
"saveback_metrics": Feature(
    name="saveback_metrics",
    description="Plusvalía descontando saveback (cuando el broker tiene saveback)",
    requires=("fetch_snapshot", "fetch_transactions", "saveback"),
)
```

Each broker exports its capabilities in `brokers/<x>/__init__.py`:

Cada broker exporta sus capabilities en `brokers/<x>/__init__.py`:

```python
# brokers/tr/__init__.py
CAPABILITIES = frozenset({
    "fetch_transactions", "fetch_snapshot", "fetch_price_history",
    "expense_tracking", "saveback", "gifts", "tax_renta_es",
})
```

`is_feature_enabled(name)` returns True only if (a) the user hasn't disabled the feature in `config.yaml > features` AND (b) the active broker supports all required capabilities. `make features` prints the resulting table.

`is_feature_enabled(name)` devuelve True solo si (a) el usuario no la ha desactivado en `config.yaml > features` Y (b) el broker activo soporta todas las capabilities requeridas. `make features` imprime la tabla resultante.

This means: when a future broker (e.g. IBKR) lacks `saveback`, the `saveback_metrics` feature auto-degrades to off without code changes. The user sees in `make features` why each feature is on/off.

Esto significa: cuando un futuro broker (ej. IBKR) no tenga `saveback`, la feature `saveback_metrics` se auto-desactiva sin cambios de código. El usuario ve en `make features` por qué cada feature está on/off.

---

## Adding a new broker · Añadir un broker nuevo

Suppose you want to add **Kraken**.

Supongamos que quieres añadir **Kraken**.

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

Las features que dependen de capabilities ausentes (`saveback_metrics`, `renta`, `expenses`/`income`) se autodesactivan.

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

Reglas de mapeo: BUY/SELL ordinario → `from_cash=True`. Bonus/referidos → `is_bonus=True, from_cash=False`. Movimientos de cash → `DEPOSIT`/`WITHDRAWAL`.

### 3. `kraken_sync.py` — entry point

Mirror `tr_sync.py` but `from brokers.kraken import CAPABILITIES as BROKER_CAPABILITIES` and `from brokers.kraken.adapter import fetch_*`. Reuse `core.metrics`, `core.backfill`, `storage.sheets.*` as-is.

Imita `tr_sync.py` pero importando de `brokers.kraken.*`. Reutiliza `core.metrics`, `core.backfill`, `storage.sheets.*` tal cual.

### 4. `make kraken-sync`, `make kraken-insights` — Makefile targets

```makefile
kraken-insights:
	$(PYTHON) kraken_sync.py --insights
```

### 5. Tests

Unit tests for the new adapter (raw event fixture → expected `Transaction`). `core/` tests don't change (they're broker-agnostic by construction).

---

## Adding a new storage backend · Añadir un backend de storage

To persist snapshots to SQLite instead of Sheets:

Para persistir snapshots en SQLite en vez de Sheets:

1. Create `storage/sqlite/snapshot_store.py` with a class `SqliteSnapshotStore` that implements the `core.snapshot_store.SnapshotStore` protocol (`append`, `append_batch`, `load_history`, `load_timestamps`).
2. In `tr_sync.py`, swap `_make_snapshot_store` to return `SqliteSnapshotStore(...)` instead of `SheetsSnapshotStore(...)`.

That's it. Nothing in `core/` or `brokers/` changes.

Eso es todo. Nada cambia en `core/` o `brokers/`.

---

## Why this structure · Por qué esta estructura

- **No forced abstract `Broker` interface.** Each broker has its own quirks (TR uses German subtitles + WebSocket cookies; Kraken uses pairs + API key/secret). A universal interface would be lossy. Instead each broker exposes its own functions whose signatures are fixed by the protocols `core/` expects (`Transaction`, `PortfolioSnapshot`).
- **Sin interfaz `Broker` abstracta forzada.** Cada broker tiene sus particularidades. Una interfaz universal sería pérdida. En su lugar, cada broker expone sus funciones cuyas firmas están fijadas por los protocolos que `core/` espera.

- **Pure `core/`** is testable with synthetic data, runs in 2ms, gives you confidence in the math before any broker call.
- **`core/` puro** se testea con datos sintéticos, corre en 2ms, da confianza en la matemática antes de cualquier llamada al broker.

- **Storage as protocol.** `core/` defines what it needs (`SnapshotStore.append`, `SnapshotStore.load_history`); the implementation can be Sheets, SQLite, JSON, in-memory for tests, etc.
- **Storage como protocolo.** `core/` define lo que necesita; la implementación puede ser Sheets, SQLite, JSON, en memoria para tests…

- **Capabilities + features.** When a feature can't run against the current broker, it disables itself with a clear message instead of crashing mid-execution.
- **Capabilities + features.** Cuando una feature no puede correr contra el broker actual, se desactiva sola con mensaje claro en vez de petar a media ejecución.

---

## What's still in `tr_sync.py` · Qué queda en `tr_sync.py`

These pieces are deliberately not extracted — they're TR-flavoured today and the seams aren't clean enough to abstract without a second consumer:

Estas piezas no están extraídas a propósito — hoy son TR-flavoured y las costuras no están claras para abstraer sin un segundo consumidor:

- Sync writers for monthly_columns / ledger layouts (deeply tied to Spanish month names, summary blocks, etc.).
- IRPF report generator (`sync_renta`): Spain-specific, would need redesign to support other tax regimes.
- `init_sheet`: bootstraps the user's Sheet structure. Could move when there's a second sink.
- `doctor`: health-check covering config + pytr + gspread + Sheet tabs.

When a second broker arrives, these will likely move to a `core/sync/` layer or a `tax_reports/` package per regime.

Cuando llegue un segundo broker, esto probablemente se moverá a una capa `core/sync/` o un paquete `tax_reports/` por régimen.
