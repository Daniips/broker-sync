# Changelog

Sigue el formato de [Keep a Changelog](https://keepachangelog.com/es/1.1.0/) y [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
