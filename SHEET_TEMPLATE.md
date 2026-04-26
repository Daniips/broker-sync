# Estructura esperada del Google Sheet

Este script asume una estructura específica en tu Google Sheet. Aquí se describe qué pestañas necesita y cómo deben estar organizadas.

> **Atajo automático**: una vez tengas `config.yaml` con tu `sheet_id` y hayas autenticado gspread, lanza:
> ```bash
> make init-sheet
> ```
> que creará las pestañas que falten con la estructura mínima (idempotente: si ya existen, no las toca). El bloque resumen de Gastos/Ingresos lo añades tú a mano si lo quieres.
>
> Si prefieres crearlo todo a mano o personalizar la estructura, sigue las instrucciones de abajo.

Los nombres de pestañas son configurables en `config.yaml` (sección `sheets`); aquí se usan los valores por defecto.

---

## 1. Pestaña `Gastos`

Estructura **por meses en columnas**, no en filas. Cada mes ocupa **dos columnas adyacentes**: concepto + importe.

Ejemplo:

| A (Concepto enero 2026) | B (Importe enero 2026) | C (Concepto febrero 2026) | D (Importe febrero 2026) | … |
|---|---|---|---|---|
| Mercadona | -32,50 | Apple | -9,99 | … |
| Carrefour | -45,00 | Spotify | -10,99 | … |
| ... | ... | ... | ... | … |
| **gastos innecesarios** | =SUMA(...) | **gastos innecesarios** | =SUMA(...) | … |
| **gastos totales** | =SUMA(...) | **gastos totales** | =SUMA(...) | … |
| **extraordinarios** | … | **extraordinarios** | … | … |

**Headers de mes (fila 1)**: el script busca cabeceras en la **fila 1** con el patrón `"<mes en español> <año>"` para concepto y `"importe <mes> <año>"` (case-insensitive) para importe. Ejemplos válidos:
- `enero 2026` (columna concepto) y `importe enero 2026` (columna importe)
- `Febrero 2026` y `Importe febrero 2026`

**Bloque resumen al final** (configurable): el script detecta el final de cada mes buscando alguno de estos textos en la columna de concepto:
- `gastos innecesarios`
- `gastos totales`
- `extraordinarios`

Cuando sincroniza, **inserta filas nuevas justo encima** del bloque resumen y desplaza el resumen hacia abajo.

## 2. Pestaña `Ingresos`

Misma estructura que Gastos, pero los markers de resumen son:
- `ingresos totales`
- `ingresos`
- `totales`

## 3. Pestaña `Dinero invertido {YYYY}`

Resumen de inversiones (savings plan + saveback + compras manuales) por mes y por activo.

| A (Activo) | B | C | … (un mes por columna) |
|---|---|---|---|
| | enero 2026 | febrero 2026 | … |
| SP 500 | 240 | 240 | |
| MSCI EM IMI | 220 | 220 | |
| MSCI EUR | 80 | 80 | |
| SMALL CAPS | 160 | 160 | |
| MSCI India | 50 | 50 | |
| Solana | 50 | 50 | |
| SAVEBACK | 15,32 | | |

- **Fila 1**: cabecera con el formato `<mes> <año>` (los mismos meses que en Gastos).
- **Columna A**: nombre del activo. Debe coincidir con el valor del `asset_name_map` en `config.yaml`. Si TR envía un asset que no está mapeado, el script crea una nueva fila con el título original y avisa.
- El script **solo sobrescribe** las celdas del mes actual y posteriores; los meses pasados nunca se tocan.

El año de esta pestaña se controla en `config.yaml`:
```yaml
sheets:
  investments_year_format: "Dinero invertido {year}"
  investments_year: null   # null = año actual
```

## 4. Pestaña `Calculo ganancias`

Snapshot del valor actual de cada activo de tu cartera. El script **solo escribe** un rango concreto de la columna C.

| A (Activo) | B (algo opcional) | C (valor actual €) |
|---|---|---|
| sp500 | … | 1234,56 |
| sp500 tech | … | 567,89 |
| em imi | … | 890,12 |
| small caps | … | 345,67 |
| msci eur | … | 234,56 |
| india | … | 123,45 |
| solana | … | 67,89 |

- El **rango exacto** y los ISINs que se escriben se configuran en `config.yaml`:
  ```yaml
  portfolio_value_range: "C2:C8"   # tantas filas como entradas en portfolio_cell_map
  portfolio_cell_map:
    - { isin: "IE00B5BMR087", label: "sp500" }
    - { isin: "IE00B3WJKG14", label: "sp500 tech" }
    - ...
  ```
- El script no toca los headers ni las columnas A/B; solo escribe los netValue en el rango configurado.

## 5. Pestaña `Estado sync`

Pestaña pequeña con dos columnas que el script **crea automáticamente** si no existe. Sirve como recordatorio visual de la última vez que sincronizaste.

| A (Proceso) | B (Último OK) |
|---|---|
| Portfolio | 2026-04-25 09:30:12 |
| Sync completo | 2026-04-25 09:31:45 |

Las etiquetas de la columna A se configuran en `config.yaml`:
```yaml
status_labels:
  portfolio: "Portfolio"
  sync: "Sync completo"
```

## 6. Pestaña `_sync_state` (oculta)

Pestaña interna que el script crea automáticamente para deduplicar eventos ya sincronizados. **No la borres ni la edites a mano**. Se oculta automáticamente.

## 6.1 Pestaña `_snapshots` (oculta)

Pestaña que el script crea automáticamente al ejecutar `make insights`, `make portfolio` o `make backfill-snapshots`. Una fila por snapshot:

| ts | cash_eur | positions_value_eur | cost_basis_eur | total_eur |
|---|---|---|---|---|
| 2026-04-26T13:50:13.411535+02:00 | 13127,46 | 9423,89 | 8424,2 | 22551,35 |

Se usa para calcular MWR YTD / 12 meses (necesita el valor de las posiciones al inicio del periodo). **No la borres ni la edites a mano.** El nombre se puede cambiar en `config.yaml > sheets.snapshots`.

## 6.2 Pestaña `_snapshots_positions` (oculta)

Mismo patrón pero con desglose por posición. Una fila por (snapshot, ISIN):

| ts | isin | title | shares | net_value_eur | cost_basis_eur |
|---|---|---|---|---|---|
| 2026-04-26T13:50:13… | IE00B5BMR087 | Core S&P 500 USD | 6.97 | 4205,81 | 3577,44 |

Útil para gráficas de evolución por activo a lo largo del tiempo. El nombre se puede cambiar en `config.yaml > sheets.snapshots_positions`.

## 7. Pestaña `Renta YYYY` (la crea `make renta`)

Cuando ejecutas `make renta`, el script crea/sobrescribe esta pestaña con el informe IRPF completo (FIFO + dividendos + intereses + bonos + posición Modelo 720). No hay que crearla a mano.

---

## Resumen de pestañas

| Pestaña | ¿Crear a mano? | ¿La toca el script? |
|---|---|---|
| `Gastos` | Sí (con headers + bloque resumen) | Inserta filas |
| `Ingresos` | Sí (con headers + bloque resumen) | Inserta filas |
| `Dinero invertido YYYY` | Sí (con headers de mes + filas de activos) | Sobrescribe celdas mes actual+ |
| `Calculo ganancias` | Sí | Sobrescribe rango configurado |
| `Estado sync` | No (auto) | Lee/escribe |
| `_sync_state` | No (auto, oculta) | Lee/escribe |
| `_snapshots` | No (auto, oculta) | Lee/append (insights, portfolio, backfill) |
| `_snapshots_positions` | No (auto, oculta) | Append (insights, portfolio, backfill) |
| `Renta YYYY` | No (auto, lo crea `make renta`) | Sobrescribe entera |

Si tu Sheet tiene otra estructura, **edita los nombres de pestañas y markers en `config.yaml`** o adapta el código.
