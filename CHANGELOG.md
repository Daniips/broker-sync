# Changelog

Sigue el formato de [Keep a Changelog](https://keepachangelog.com/es/1.1.0/) y [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — Investment insights & analytics

- **`make insights`** (`tr_sync.py --insights`): consola con patrimonio, rentabilidad y aportaciones. No toca la Sheet (excepto los `_snapshots` ocultos). Bloques:
  - Patrimonio actual: ETFs/acciones, cripto, cash, total (separados como en TR app).
  - Rentabilidad: cost basis sin/con saveback, plusvalía sobre dinero propio (matchea Excel y TR app), plusvalía sobre cost basis bruto.
  - Rentabilidad histórica: aportado neto + MWR (XIRR) all-time, YTD y 12 meses anualizado, en dos modos (saveback como income vs como aportación).
  - Aportaciones mensuales: este mes vs media de los últimos 12m, con `Δ vs media`.
  - Concentración: distribución por posición con bar chart + alerta `⚠ alta` si una posición supera el threshold (configurable, default 35%).
- **`make backfill-snapshots`** (`tr_sync.py --backfill-snapshots`): reconstruye snapshots históricos vía `aggregateHistoryLight` de TR. Soporta `--start YYYY-MM-DD` y `--frequency weekly|biweekly|monthly`. Desbloquea MWR YTD/12m sin esperar a acumular semanas.
- **`make features`** (`tr_sync.py --features`): tabla con todas las features del producto, su estado en config y el soporte del broker activo.
- **`tr_sync.py --debug-isin ISIN`**: lista todas las transacciones que el adapter saca para un ISIN concreto. Útil para reconciliar contra fuentes externas (Excel manual).

### Added — Architecture

- **`core/types.py`**: modelo agnóstico de broker (Transaction, Position, PortfolioSnapshot, TxKind). Frozen dataclasses con convención de signos documentada y campos para `is_bonus`, `from_cash`, `cost_basis_eur`, `exchange_id`.
- **`core/metrics.py`**: funciones puras sobre el modelo agnóstico — `xirr`, `mwr`, `simple_return`, `unrealized_return`, `unrealized_return_user_paid`, `cost_basis_user_paid_per_isin`, `concentration`, `monthly_contributions`, `contribution_vs_average`.
- **`core/backfill.py`**: reconstrucción histórica pura — `shares_at`, `cash_at`, `reconstruct_snapshot_at`. No I/O.
- **`core/snapshot_store.py`**: protocolo `SnapshotStore` + esquema y conversión pura `snapshot_to_rows` + helper `snapshot_value_at`.
- **`core/features.py`**: registro `FEATURE_REGISTRY` con cada feature y sus capabilities requeridas. Funciones `is_feature_enabled`, `is_feature_supported`, `feature_status`.
- **`brokers/tr/__init__.py`**: declara `CAPABILITIES` (set de strings) que TR soporta. Otros brokers exportarán su set propio.
- **`brokers/tr/adapter.py`**: `fetch_transactions`, `fetch_snapshot`, `fetch_price_history`, `fetch_price_history_with_fallback` (prueba múltiples exchanges para ISINs cripto), `raw_event_to_tx`. Mapea event types de TR a `TxKind`. Marca saveback (`is_bonus=True`, `from_cash=False`) y regalos (`is_bonus=False`, `from_cash=False`).
- **`storage/sheets/`**: implementaciones backend-específicas para Google Sheets — `client.py` (open_spreadsheet), `status_store.py` (StatusStore), `sync_state_store.py` (SyncStateStore dedup), `snapshot_store.py` (SheetsSnapshotStore agregado + por posición).
- **Pestañas ocultas nuevas** en la Sheet:
  - `_snapshots`: una fila por snapshot con `ts | cash_eur | positions_value_eur | cost_basis_eur | total_eur`. Append automático en cada `make insights / portfolio` y en backfill.
  - `_snapshots_positions`: una fila por (snapshot, posición) con `ts | isin | title | shares | net_value_eur | cost_basis_eur` para evolución por activo.

### Added — Config

- **`features.{insights, concentration, snapshot_persist, backfill_snapshots, saveback_metrics, ...}`**: toggles individuales por feature. La feature se desactiva tanto si el config la apaga como si el broker no la soporta.
- **`concentration_threshold`** (default 0.35): % a partir del cual una posición se marca como "alta concentración" en el bloque correspondiente.
- **`sheets.snapshots`** (default `"_snapshots"`) y **`sheets.snapshots_positions`** (default `"_snapshots_positions"`): nombres de las pestañas ocultas para histórico.

### Changed

- Repo renamed from `tr-sync` to `broker-sync` to reflect future multi-broker support. URL: https://github.com/Daniips/broker-sync.
- Refactored to a modular layout: `core/` (puro), `brokers/<x>/` (data sources), `storage/<backend>/` (sinks). `tr_sync.py` baja a ~2270 líneas (pre-refactor: ~2400).
- Code comments and docstrings in `core/`, `brokers/`, `storage/` are bilingual (English + Spanish).
- `ARCHITECTURE.md` reescrito reflejando la estructura actual y el patrón core/brokers/storage.

### Added — Per-asset concentration limits

- **`concentration_limits`** (config nuevo): dict `{ISIN: float}` para definir un máximo individual por activo. Sobrescribe el `concentration_threshold` global. Útil para tener tolerancias distintas por tipo de activo (core ETFs alto, cripto bajo).
- **`core.metrics.concentration()`** acepta ahora `limits` y `default_threshold`. Cada entrada del resultado incluye `limit`, `margin_pp` y `exceeded` para que el caller decida cómo presentar la info.
- **Display de `make insights`**: cada posición muestra su límite efectivo y margen (o "EXCEDIDO en X pp"). Resumen al final: "✓ Todas dentro de su límite" o "⚠ N por encima".
- 3 tests nuevos en `test_metrics.py` cubriendo: límites por ISIN, fallback a default_threshold, no-limit cuando ninguno se provee.

### Added — Performance & docs (post-refactor)

- **`core/cache.py`** + flag `--refresh`: cache pickle de `(snapshot, txs)` con TTL=5min. Encadena `make insights` / `make portfolio` / `make backfill-snapshots` sin re-fetch innecesario. Login a TR se evita totalmente cuando hay cache fresco.
- **`INSIGHTS.md`**: doc en español explicando bloque a bloque el output de `make insights`, las 2 lecturas de cost basis, los 3 horizontes de MWR, el toggle income/deposit, y FAQ sobre las preguntas comunes.
- **`IMPROVEMENTS.md`**: roadmap priorizado de mejoras pendientes (renta extraction, per-asset limits, crypto backfill, telemetría, alertas, etc.).
- **`test_adapter.py`**: 24 tests del adapter TR cubriendo cada `eventType` (BUY/SELL/SAVEBACK/GIFT/DIVIDEND/INTEREST/DEPOSIT/WITHDRAWAL/CANCELED/missing-data) con parser mockeado. Suite total pasa de 116 a 140 tests.

### Added

- Toggles `features.{expenses,income,investments,portfolio}` en `config.yaml` para deshabilitar partes del sync.
- Toggles `renta.{fifo,dividends,interest,bonds,summary_by_box,retentions,saveback,crypto,modelo720}` para personalizar las secciones del informe IRPF.
- `month_names` configurable (lista de 12 nombres de mes) para soportar idiomas distintos al español.
- Validación de `config.yaml` al arrancar con mensajes claros si falta un campo o tiene formato incorrecto.
- `CONTRIBUTING.md` con guía para nuevos colaboradores.
- `CHANGELOG.md` (este fichero).
- `make init-sheet` (`tr_sync.py --init-sheet`): bootstrap de pestañas en la Google Sheet, idempotente. Pre-rellena los labels de portfolio_cell_map junto al portfolio_value_range.
- `make doctor` (`tr_sync.py --doctor`): health check que verifica config, sesión pytr, OAuth gspread, accesibilidad de la Sheet, pestañas requeridas y coherencia portfolio_cell_map ↔ portfolio_value_range. Sale con exit code 1 si encuentra errores.
- Workflow CI `.github/workflows/tests.yml` que corre los tests en cada push/PR (Python 3.11 y 3.12).
- README en inglés (`README.md`); el español se mueve a `README.es.md` con cross-link entre ambos.
- Layout `ledger` para Gastos/Ingresos como alternativa al `monthly_columns` original. Una fila por evento con columnas Fecha/Concepto/Importe. Configurable por pestaña vía `sheets.expenses_layout` / `sheets.income_layout`. Cabeceras personalizables con `sheets.ledger_headers`. `init-sheet` crea las cabeceras automáticamente cuando el layout es `ledger`.
- Más campos extraídos a config para hacer el script verdaderamente reutilizable:
  - `saveback_label` (label de la fila Saveback en Inversiones).
  - `init_sheet_headers` (cabeceras que escribe `init-sheet`).
  - `subtitle_translations` (traducciones alemán → idioma del usuario, mergeadas con default castellano).
  - `renta_classification.dividend_subtitles` / `bond_cash_subtitles` / `bond_maturity_subtitles` (clasificación de subtitles de TR para el informe IRPF; permite añadir variantes sin tocar código).
- Configurabilidad total de celdas:
  - `sheets.ledger_columns`: columnas A1 para fecha/concepto/importe en layout `ledger` (defaults A/B/C). Pueden no ser contiguas.
  - `sheets.month_header_amount` y `sheets.month_header_concept`: patrones de los headers de mes en layout `monthly_columns`. Soportan `{month}` y `{year}` para internacionalización.
- **CLI interactiva de configuración** (`config_cli.py`, dep nueva: `questionary`):
  - `make config-init`: wizard paso a paso para crear `config.yaml` desde cero (preguntando sheet_id, layouts, pestañas, portfolio, asset_name_map, etc.).
  - `make config-show` / `config-validate` / `config-features` para inspección y toggles.
  - `python tr_sync.py config set KEY VALUE` (dot-notation) para cambiar un campo concreto.
  - `python tr_sync.py config add-asset ISIN LABEL` / `remove-asset` para gestionar `portfolio_cell_map` sin tocar YAML.
  - `python tr_sync.py config add-ignore SECTION TEXT` / `remove-ignore` para gestionar `ignore_events`.
  - El subcomando `config` se shortcircuit a nivel de import: arranca sin requerir `config.yaml` previo (ideal para el primer setup) y sin cargar `pytr`/`gspread`.

## [0.1.0] — 2026-04

Primera versión pública del proyecto.

### Características

- Sincronización de gastos / ingresos / inversiones del último mes desde Trade Republic a un Google Sheet.
- Snapshot de portfolio: escribe el `netValue` actual de cada activo en un rango configurable.
- Informe IRPF (`make renta`):
  - Ganancias/pérdidas patrimoniales con FIFO por ISIN.
  - Soporte de acciones, ETFs, regalos (`ETF-Geschenk`), lotería (`Verlosung`), bonos extranjeros con cupón + amortización.
  - Dividendos con bruto/retención/neto y desglose por país de origen para deducción doble imposición.
  - Intereses, saveback, posición cripto y saldo total para Modelo 720/721.
  - Volcado simultáneo a consola y a una pestaña `Renta YYYY` de la Sheet.
- Configuración en `config.yaml` (gitignored). Plantilla en `config.example.yaml`.
- Filtros `ignore_events` para descartar eventos que ya gestionas a mano (autotransferencias, nóminas).
- Override manual `gift_cost_overrides` para regalos cuyos detalles TR no parsea.
- Utilidad `inspect_events.py` para inspeccionar eventos brutos por tipo, ISIN o título.
- Workflow opcional de GitHub Actions para sync automatizado.
- Documentación: `README.md`, `CONFIG.md`, `SHEET_TEMPLATE.md`, `RENTA.md`.
- 53 tests unitarios sobre la lógica pura (parsers, FIFO, agregadores) sin red.
- Licencia MIT.
