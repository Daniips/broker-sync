# Referencia de `config.yaml`

Toda la configuración por usuario vive en `config.yaml`. Este fichero **no se commitea** (está en `.gitignore`). La plantilla limpia es `config.example.yaml`.

> Convención: las claves marcadas como **(obligatorio)** deben tener un valor real para que el script arranque. Las marcadas como **(opcional)** tienen un valor por defecto razonable.

---

## Sumario

| Sección | Campo | Tipo | Descripción |
|---|---|---|---|
| `sheet_id` | string | obligatorio | ID del Google Sheet |
| `sheets.expenses` | string | obligatorio | Nombre de la pestaña de gastos |
| `sheets.income` | string | obligatorio | Nombre de la pestaña de ingresos |
| `sheets.investments_year_format` | string | obligatorio | Patrón con `{year}` para la pestaña de inversiones |
| `sheets.investments_year` | int / null | opcional | Fija el año fiscal; null = año actual |
| `sheets.portfolio` | string | obligatorio | Nombre de la pestaña de portfolio |
| `sheets.status` | string | obligatorio | Nombre de la pestaña de estado |
| `sheets.sync_state` | string | obligatorio | Nombre de la pestaña oculta de dedup |
| `summary_markers.expenses` | list[string] | obligatorio | Strings que marcan el bloque resumen en Gastos |
| `summary_markers.income` | list[string] | obligatorio | Idem para Ingresos |
| `status_labels` | dict | obligatorio | Etiquetas de la pestaña Estado sync |
| `asset_name_map` | dict | obligatorio | TR-name → display-name para inversiones |
| `portfolio_cell_map` | list[obj] | obligatorio | ISIN+label en orden vertical del rango |
| `portfolio_value_range` | string | obligatorio | Rango A1 donde se escriben los netValue |
| `crypto_isins` | list[string] | opcional | ISINs tratados como cripto |
| `gift_cost_overrides` | dict | opcional | Override para regalos no parseables |
| `ignore_events` | dict | opcional | Filtros para descartar eventos del sync |
| `default_buffer_days` | int | opcional | Días buffer al descargar eventos |
| `timezone` | string | opcional | Zona horaria (default `Europe/Madrid`) |
| `saveback_label` | string | opcional | Label de la fila Saveback en Inversiones (default `SAVEBACK`) |
| `init_sheet_headers` | dict | opcional | Cabeceras que escribe `make init-sheet` |
| `subtitle_translations` | dict | opcional | Traducciones alemán → idioma del usuario (se mergean con default castellano) |
| `renta_classification` | dict | opcional | Subtitles para clasificar dividendos/bonos en `make renta` |
| `features` | dict | opcional | Toggles individuales por feature (`insights`, `concentration`, `backfill_snapshots`, etc.) — ver sección Features |
| `concentration_threshold` | float | opcional | % a partir del cual una posición se marca "alta concentración" en `make insights` (default `0.35`) |
| `sheets.snapshots` | string | opcional | Nombre de la pestaña oculta de snapshots agregados (default `_snapshots`) |
| `sheets.snapshots_positions` | string | opcional | Nombre de la pestaña oculta de snapshots por posición (default `_snapshots_positions`) |

---

## `sheet_id` (obligatorio)

ID del Google Sheet, sale de la URL: `docs.google.com/spreadsheets/d/<ESTE_ID>/edit`.

```yaml
sheet_id: "1AbcDef..."
```

---

## `sheets` (obligatorio)

Nombres de las pestañas que el script va a leer/escribir. Si tu Sheet tiene nombres distintos a los del ejemplo, ajústalos aquí.

```yaml
sheets:
  expenses: "Gastos"
  income: "Ingresos"
  investments_year_format: "Dinero invertido {year}"
  investments_year: null      # null = año actual; ej. 2025 para fijarlo
  portfolio: "Calculo ganancias"
  status: "Estado sync"
  sync_state: "_sync_state"
```

| Campo | Notas |
|---|---|
| `expenses` | Pestaña con gastos. Layout configurable con `expenses_layout`. |
| `income` | Pestaña con ingresos. Layout configurable con `income_layout`. |
| `expenses_layout` / `income_layout` | `monthly_columns` (default; meses como pares de columnas Concepto+Importe + bloque resumen) o `ledger` (una fila por evento con Fecha/Concepto/Importe). |
| `ledger_headers` | Solo si usas `ledger`. Lista de 3 strings con las cabeceras (default `["Fecha", "Concepto", "Importe"]`). |
| `ledger_columns` | Solo si usas `ledger`. `{ date, concept, amount }` con la letra de columna A1 para cada campo (default A/B/C). Pueden no ser contiguas. |
| `month_header_amount` / `month_header_concept` | Solo si usas `monthly_columns`. Plantillas con `{month}` y `{year}` para los headers de mes (defaults `"{month} {year}"` y `"Concepto {month}"`). |
| `investments_year_format` | Cadena con `{year}` que se sustituye por el año fiscal en uso (p.ej. `"Dinero invertido 2026"`). |
| `investments_year` | Si es `null`, el script usa el año actual. Si pones un año, lo fija (útil para retrocompatibilidad o inversiones en años pasados). También puedes sobrescribirlo con la env var `TR_SYNC_INVESTMENTS_YEAR`. |
| `portfolio` | Pestaña con el snapshot de valor por activo. |
| `status` | El script crea esta pestaña si no existe; muestra timestamps del último sync correcto. |
| `sync_state` | Pestaña oculta donde el script guarda los IDs de eventos ya escritos para deduplicar. **No la edites a mano.** |
| `snapshots` | Pestaña oculta con un snapshot por ejecución de `make insights`/`portfolio`/`backfill-snapshots`. Default `_snapshots`. **No la edites a mano.** |
| `snapshots_positions` | Pestaña oculta con desglose por posición de cada snapshot. Default `_snapshots_positions`. **No la edites a mano.** |

---

## `summary_markers` (obligatorio)

Strings (case-insensitive) que el script busca en la columna de concepto de cada mes para detectar el bloque resumen final. Cuando inserta filas nuevas, las pone justo encima del primer marker que encuentra.

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

> Si tu hoja no tiene bloque resumen, deja la lista vacía. El script entonces escribirá al final de cada columna sin desplazar nada.

---

## `status_labels` (obligatorio)

Etiquetas que aparecen en la columna A de la pestaña "Estado sync".

```yaml
status_labels:
  portfolio: "Portfolio"
  sync: "Sync completo"
```

---

## `asset_name_map` (obligatorio para inversiones)

Mapeo del nombre que TR usa para cada activo (clave) al nombre que tienes en la pestaña "Dinero invertido" (valor). Si TR envía un asset que no está en este mapa, el script crea una nueva fila con el título original y avisa.

```yaml
asset_name_map:
  "MSCI India USD (Acc)": "MSCI India"
  "Solana": "Solana"
  "Core MSCI Europe EUR (Acc)": "MSCI EUR"
  "Core MSCI EM IMI USD (Acc)": "MSCI EM IMI"
  "Core S&P 500 USD (Acc)": "SP 500"
  "MSCI World Small Cap USD (Acc)": "SMALL CAPS"
```

Para descubrir el nombre exacto que TR usa, lánzalo:
```bash
.venv/bin/python inspect_events.py --eventtype TRADING_SAVINGSPLAN_EXECUTED
```

---

## `portfolio_cell_map` (obligatorio)

Lista ordenada de los ISINs cuyo valor actual quieres escribir en la pestaña "Calculo ganancias". El **orden** importa: la primera entrada va a la primera fila del rango, etc.

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

El `label` solo se usa para el log de consola; lo importante es el ISIN y el orden.

---

## `portfolio_value_range` (obligatorio)

Rango A1 donde el script escribe los `netValue` actuales. Debe ser una columna y tener tantas celdas como entradas en `portfolio_cell_map`.

```yaml
portfolio_value_range: "C2:C8"
```

---

## `crypto_isins` (opcional)

ISINs que el script trata como criptomonedas para la sección "Posición cripto" del informe IRPF (informativo Modelo 721).

```yaml
crypto_isins:
  - "XF000SOL0012"
```

---

## `gift_cost_overrides` (opcional)

Override manual para regalos (`GIFTING_RECIPIENT_ACTIVITY` / `GIFTING_LOTTERY_PRIZE_ACTIVITY`) cuyos detalles el script no sabe parsear. Rellena el coste y las shares con el dato exacto del PDF "Jährlicher Steuerbericht" de TR.

```yaml
gift_cost_overrides:
  LU1681048804:
    shares: 0.222311
    cost_eur: 25.00
```

---

## `ignore_events` (opcional)

Patrones para descartar eventos del sync. Útil para nóminas y autotransferencias que ya gestionas a mano. Match **case-insensitive** y por **substring**.

```yaml
ignore_events:
  income:
    title_contains:
      - "tu nombre"           # autotransferencias entrantes
      - "imagin"
    subtitle_contains: []
  expenses:
    title_contains: []
    subtitle_contains: []
```

Cuando un evento matchea, el script lo loggea con detalle (fecha + importe + título) durante el sync. Lee [README → Ignorar eventos](README.md#ignorar-eventos) para ejemplos.

---

## `default_buffer_days` (opcional, default `7`)

Días de buffer antes del inicio del mes actual al descargar eventos. Sirve para coger eventos del último día del mes anterior que pudieron tardar en aparecer.

```yaml
default_buffer_days: 7
```

---

## `timezone` (opcional, default `Europe/Madrid`)

Zona horaria con la que se interpretan los timestamps de TR (que vienen en UTC). Cualquier zona válida de IANA.

```yaml
timezone: "Europe/Madrid"
```

---

## `saveback_label` (opcional, default `"SAVEBACK"`)

Texto que aparece en la columna A de la pestaña "Dinero invertido" para la fila que agrega los Saveback. Si tu pestaña tiene otra etiqueta para esa fila (p.ej. `"Cashback ETF"`), pon el mismo string aquí.

```yaml
saveback_label: "SAVEBACK"
```

---

## `init_sheet_headers` (opcional)

Cabeceras que escribe `make init-sheet` cuando crea pestañas vacías la primera vez. Útil si quieres usar el script en otro idioma o con otra terminología.

```yaml
init_sheet_headers:
  investments_asset_column: "Activo"      # cabecera col A en pestaña Inversiones
  portfolio_asset_column: "Activo"        # cabecera col labels en pestaña Portfolio
  portfolio_value_column: "Valor (€)"     # cabecera col valores en pestaña Portfolio
```

---

## `subtitle_translations` (opcional)

Diccionario adicional de traducciones alemán → idioma del usuario. La API de TR responde siempre en alemán; aquí pones las traducciones que quieres ver en consola y en la Sheet. Tu config se **mergea** con el default (castellano), así que solo necesitas listar las que cambies.

```yaml
subtitle_translations:
  "Bardividende": "Cash dividend"           # ejemplo: usar inglés
  "Verkaufsorder": "Sell order"
  "Endgültige Fälligkeit": "Final maturity"
```

Si TR introduce un nuevo subtitle que no está en el default, añádelo aquí.

---

## `features` (opcional)

Toggles por feature. Por defecto **todas las features están activadas**; añade entradas aquí solo si quieres apagar alguna.

```yaml
features:
  expenses: true              # sync de gastos
  income: true                # sync de ingresos
  investments: true           # sync de "Dinero invertido YYYY"
  portfolio: true             # snapshot a "Calculo ganancias"
  renta: true                 # informe IRPF
  insights: true              # patrimonio + rentabilidad + concentración
  concentration: true         # bloque de concentración dentro de insights
  snapshot_persist: true      # guardar snapshots automáticos en cada ejecución
  backfill_snapshots: true    # reconstrucción histórica de snapshots
  saveback_metrics: true      # plusvalía descontando saveback
```

Una feature solo se ejecuta si **(a)** está activada aquí Y **(b)** el broker activo soporta sus capabilities. Para ver el estado actual: `make features`.

```
$ make features
Feature                Config   Soporte   Efectiva   Descripción
---------------------- -------- --------- ---------- ----------------------------------------
insights               ✓        ✓         ✓ ON       Patrimonio, rentabilidad TR-style + propio, MWR…
concentration          ✓        ✓         ✓ ON       Distribución de cartera por posición + alerta…
saveback_metrics       ✓        ✓         ✓ ON       Plusvalía descontando saveback (cuando el broker tiene saveback)
…
```

Cuando llegue un segundo broker que no tenga (p.ej.) saveback, `saveback_metrics` saldrá `Soporte ✗ → Efectiva ✗ off` automáticamente.

---

## `concentration_threshold` (opcional)

Float entre 0 y 1. Define el % a partir del cual una posición se marca como "alta concentración" en `make insights`. Default `0.35` (35%).

```yaml
concentration_threshold: 0.40   # 40%
```

---

## `renta_classification` (opcional)

Subtitles alemanes que TR usa en eventos `SSP_CORPORATE_ACTION_CASH` para clasificar el evento (dividendo, cupón de bono, amortización). Si TR cambia o añade un nuevo subtitle, parchea aquí sin tocar código.

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

Solo necesitas redefinir las que quieras cambiar; el resto cae al default.

---

## Variables de entorno que sobrescriben config

Estas variables tienen prioridad sobre lo que pongas en `config.yaml`:

| Variable | Sobreescribe |
|---|---|
| `TR_SYNC_INVESTMENTS_YEAR` | `sheets.investments_year` |
