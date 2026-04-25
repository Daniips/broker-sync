# Architecture · Arquitectura

> 🇬🇧 English first, 🇪🇸 español a continuación.

This repo is structured to support **multiple brokers** sharing the same Google Sheets writer, FIFO engine and config CLI.

Este repo está estructurado para soportar **múltiples brokers** compartiendo el mismo escritor de Google Sheets, motor FIFO y CLI de config.

---

## Layout · Estructura

```
broker-sync/
├── tr_sync.py              ← Entry point for Trade Republic / Punto de entrada para TR
├── inspect_events.py       ← Raw event explorer (TR-specific) / Explorador de eventos brutos (TR)
├── config_cli.py           ← Interactive config CLI / CLI interactiva del config
│
├── core/                   ← Broker-agnostic / Agnóstico del broker
│   ├── utils.py            ← Number parsers, A1 helpers / Parsers de números, helpers A1
│   └── fifo.py             ← Generic FIFO engine / Motor FIFO genérico
│
├── brokers/
│   ├── __init__.py
│   └── tr/                 ← Trade Republic specific / Específico de TR
│       ├── __init__.py
│       └── parser.py       ← TR event parsers / Parsers de eventos de TR
│
├── tests/ (test_tr_sync.py at root for now)
│   └── test_tr_sync.py
│
└── ... (config, docs, Makefile, requirements)
```

The current `tr_sync.py` still hosts most of the orchestration logic (CLI dispatch, sync flow, config loading, IRPF report generation) and re-exports the moved helpers under their original names with a `_` prefix, so existing callers and tests keep working.

`tr_sync.py` aún alberga la mayor parte de la lógica de orquestación (dispatcher CLI, flujo de sync, carga de config, generación del informe IRPF) y reexporta los helpers movidos bajo su nombre original con prefijo `_`, de modo que los llamadores y tests existentes siguen funcionando.

---

## Adding a new broker · Añadir un broker nuevo

Suppose you want to add **Kraken**. The work to do:

Supongamos que quieres añadir **Kraken**. El trabajo a hacer:

### 1. Create `brokers/kraken/parser.py` · Crea `brokers/kraken/parser.py`

Implement broker-specific extractors for buys/sells/dividends, returning the same shape as `brokers/tr/parser.py`:

Implementa extractores broker-specific para compras/ventas/dividendos, devolviendo la misma forma que `brokers/tr/parser.py`:

```python
def extract_trade_details(raw_kraken_trade): ...
def extract_dividend_details(raw_kraken_ledger_entry): ...
```

### 2. Create `brokers/kraken/fetcher.py` · Crea `brokers/kraken/fetcher.py`

Wrap the Kraken API (`krakenex`, `python-kraken-sdk` or raw HTTP) and expose a function that returns the broker's raw events.

Encapsula la API de Kraken (`krakenex`, `python-kraken-sdk` o HTTP en bruto) y expón una función que devuelva los eventos brutos del broker.

### 3. Create `kraken_sync.py` (entry point) · Crea `kraken_sync.py` (entry point)

Mirror `tr_sync.py` but pulling from `brokers.kraken.*`:

Imita `tr_sync.py` pero importando de `brokers.kraken.*`:

```python
from brokers.kraken.fetcher import fetch_kraken_ledger
from brokers.kraken.parser import extract_trade_details, extract_dividend_details
from core.fifo import apply_fifo
from core.utils import parse_a1_column_range, column_letter_to_index
# ... rest of orchestration / resto de orquestación
```

### 4. Add `make kraken-sync` to the Makefile · Añade `make kraken-sync` al Makefile

```makefile
kraken-sync:
	$(PYTHON) kraken_sync.py
```

### 5. Update `config.example.yaml` · Actualiza `config.example.yaml`

Add a `kraken:` section with API credentials and broker-specific settings.

Añade una sección `kraken:` con credenciales de API y settings específicos del broker.

### 6. Add tests · Añade tests

Mirror `test_tr_sync.py` style: pure-function tests for the parser, mock tests for the writer.

Imita el estilo de `test_tr_sync.py`: tests de funciones puras para el parser, tests con mocks para el writer.

---

## Why this structure · Por qué esta estructura

- **No forced abstract `Broker` interface**. Each broker has its own quirks (TR uses German subtitles + cookies; Kraken uses pairs + API key/secret). Forcing a universal interface would be lossy.
- **Sin interfaz abstracta `Broker` forzada**. Cada broker tiene sus particularidades (TR usa subtitles alemanes + cookies; Kraken usa pares + API key/secret). Imponer una interfaz universal sería pérdida.

- **Shared `core/`** for genuinely broker-agnostic code: number parsing, FIFO, A1 range math.
- **`core/` compartido** para código realmente agnóstico: parsing de números, FIFO, matemática de rangos A1.

- **Each `brokers/X/` is self-contained**: parser, fetcher, classifier. Adding/removing a broker doesn't touch the others.
- **Cada `brokers/X/` es autocontenido**: parser, fetcher, clasificador. Añadir/quitar un broker no toca los demás.

- **Each broker has its own entry point** (`tr_sync.py`, `kraken_sync.py`, ...) and its own config section. No magic dispatch.
- **Cada broker tiene su entry point** (`tr_sync.py`, `kraken_sync.py`, ...) y su sección de config. Sin dispatch mágico.

---

## What's left to fully extract · Qué queda por extraer del todo

Currently `tr_sync.py` still owns:

Actualmente `tr_sync.py` aún contiene:

- Config loading and validation (could move to `core/config.py`).
- Sheet writers (`monthly_columns` and `ledger` layouts → `core/sheet_writer.py`).
- Doctor health check (`core/doctor.py`).
- `init_sheet` (`core/init_sheet.py`).
- Renta orchestrator and writers (TR-flavoured today, but the FIFO output structure is generic).

These are deliberately not yet extracted to keep the refactor low-risk. They will be moved when a second broker is implemented and the seams become clear.

Estos no se han extraído aún a propósito para mantener el refactor de bajo riesgo. Se moverán cuando se implemente un segundo broker y las costuras se vuelvan claras.
