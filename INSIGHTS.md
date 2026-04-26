# Guía de `make insights`

Esta guía explica **bloque a bloque** lo que sale al ejecutar `make insights`, por qué hay varias lecturas de la misma idea, y qué pregunta responde cada número.

> 🇪🇸 Doc en español, igual que `CONFIG.md`, `RENTA.md` y `SHEET_TEMPLATE.md`.

---

## TL;DR — qué número mirar

| Pregunta | Número que responde |
|---|---|
| ¿Cuánto tengo en total? | **Patrimonio → TOTAL** |
| ¿Cómo van mis posiciones vivas? | **Plusvalía sobre tu dinero (%)** ← matchea TR app y Excel |
| ¿Qué rentabilidad real anualizada saca mi inversión? | **MWR all-time / 12 meses** |
| ¿Estoy ahorrando más o menos que de costumbre? | **Aportaciones mensuales: Δ vs media** |
| ¿Estoy demasiado concentrado en una posición? | **Concentración + alerta `⚠ alta`** |

El resto de líneas son contexto / sub-lecturas para entender de dónde sale cada cifra.

---

## Bloque 1 — PATRIMONIO ACTUAL

```
PATRIMONIO ACTUAL
  Cartera (ETFs/acciones):       9.145,50 €
  Cripto:                          278,28 €
  Cash:                         13.127,46 €
  TOTAL:                        22.551,24 €
```

- **Cartera (ETFs/acciones)** = suma del `netValue` de las posiciones que **no** están en `crypto_isins` del config.
- **Cripto** = suma del `netValue` de las posiciones marcadas como cripto.
- **Cash** = saldo EUR de tu cuenta TR.
- **TOTAL** = todo lo anterior.

Separamos cartera y cripto porque la app de TR las muestra en bloques distintos. Sumadas dan lo mismo que el balance total que ves en TR (modulo ±€ por movimientos de precio entre cuando miras y cuando corre el script).

---

## Bloque 2 — RENTABILIDAD — POSICIONES ACTUALES

```
RENTABILIDAD — POSICIONES ACTUALES
  Cost basis sin saveback:       8.229,78 €  ← lo que tú pusiste
  Cost basis con saveback:       8.424,20 €  ← averageBuyIn API bruto
  Valor actual:                  9.423,78 €
  Plusvalía sobre tu dinero:     1.194,00 €  (+14,51 %)  ← matchea Excel y TR app
  Plusvalía sobre bruto:           999,58 €  (+11,87 %)  ← saveback incluido como coste
```

Aquí mostramos **dos lecturas** de la misma plusvalía latente, según qué cost basis uses como denominador.

### Cost basis sin saveback (recomendado)

Es lo que TÚ has metido de tu bolsillo en las posiciones que tienes ahora vivas:

```
cost_basis_propio = TR averageBuyIn × shares − Σ saveback recibido del ISIN
```

El saveback es dinero que TR te regala (~1% del gasto con tarjeta) que entra a la cartera como acciones. **No lo pagaste tú**, así que no debería inflar el coste que usas para medir tu rendimiento.

Esta lectura **matchea**:
- El % "Ganancia" que llevas en tu Excel manual (si lo tienes).
- El % "Rendimiento" que muestra la app de TR.

### Cost basis con saveback (TR API bruto)

Es directamente `averageBuyIn × shares` que devuelve TR. Incluye el valor de las shares saveback al precio de mercado en el momento de entrega. Es el "cost basis técnico" — lo que el broker considera que pagaste, sin distinguir si fue tu dinero o un perk.

### ¿Cuál de los dos es "el bueno"?

Depende de la pregunta:

- "**¿Cómo va el dinero que YO puse?**" → sin saveback (+14,51 %).
- "**¿Cuánto vale la cartera vs lo que costó adquirirla?**" → con saveback (+11,87 %).

Mostramos los dos para que entiendas la diferencia. La diferencia entre ambos % te dice **cuánto te está aportando el saveback**: aquí ~2,6 puntos porcentuales de boost gratis.

---

## Bloque 3 — RENTABILIDAD — HISTÓRICO COMPLETO

```
RENTABILIDAD — HISTÓRICO COMPLETO (incluye ventas y dividendos)
  ── Mi dinero (saveback como income — default) ──
    Aportado neto (BUYs − SELLs):        9.942,22 €
    MWR all-time:                    +23,08 % anual
    MWR YTD (2026):                  +17,97 % anual
    MWR 12 meses:                    +25,17 % anual

  ── Incluyendo saveback como aportación ──
    Aportado neto (BUYs − SELLs):       10.136,64 €
    MWR all-time:                    +19,66 % anual
    MWR YTD (2026):                  +17,22 % anual
    MWR 12 meses:                    +22,72 % anual
```

Aquí cambiamos el ángulo: en vez de "valor vs coste de la cartera viva", calculamos **MWR (XIRR)** sobre todos los flujos históricos. Ventas, dividendos, intereses, regalos — todo entra.

### Qué es MWR

**Money-Weighted Return** = la TIR anualizada sobre tus flujos de dinero. Responde a la pregunta:

> "Si pongo en una hoja de cálculo cada euro que metí (con su fecha) y cada euro que saqué (idem) y al final el valor actual de la cartera, ¿a qué tasa anual tendrían que crecer mis euros para que cuadre?"

Es el número honesto y comparable contra benchmarks (S&P, MSCI World). El % simple ("metí 9.942, vale 9.423, perdí un 5%") es engañoso porque ignora **cuándo** entró cada euro: el dinero que metiste hace 18 meses ha tenido más tiempo de compoundear que el que metiste el mes pasado.

### Por qué dos modos: income vs deposit

Cuando recibes saveback, hay dos formas de tratarlo en el cálculo:

- **`income` (default)**: el saveback es "ingreso del bróker", como un bonus. **No** cuenta como aportación tuya. El MWR ignora estas shares como flujo externo y lo que aportaron sube el rendimiento → MWR más alto.
- **`deposit`**: tratamos el saveback como una aportación más de tu bolsillo. Cuenta como capital invertido sin aportar rendimiento extra → MWR más bajo (más capital, mismo retorno).

```
MWR all-time income:    +23,08 %  ← respuesta a "¿qué rinde MI esfuerzo de ahorro?"
MWR all-time deposit:   +19,66 %  ← respuesta a "¿qué rinde cualquier euro invertido?"
```

Ambos son matemáticamente correctos. La diferencia entre los dos te dice cuánto te está empujando el saveback.

### Por qué 3 horizontes (all-time / YTD / 12m)

| Horizonte | Qué responde |
|---|---|
| **all-time** | Rentabilidad anualizada desde el primer día de tu cuenta. Estable pero arrastra contexto antiguo. |
| **YTD** | Rentabilidad anualizada desde 1 enero del año actual. **Comparable** con "el S&P lleva +X% YTD". |
| **12 meses** | Rentabilidad anualizada de los últimos 365 días. Mejor para "cómo va mi cartera **últimamente**" porque suaviza efectos puntuales del año natural. |

Los tres se calculan con XIRR pero sobre periodos distintos. Para los sub-periodos (YTD / 12m) necesitamos el **valor de la cartera al inicio** del periodo, que se saca de la pestaña oculta `_snapshots`. Si aún no tienes snapshots anteriores al periodo, sale `n/a` con una nota.

> **Truco:** ejecuta `make backfill-snapshots` una vez para que reconstruya un año de histórico semanal y los MWR YTD/12m salgan desde el primer día.

### Aportado neto vs valor

```
Aportado neto (BUYs − SELLs):        9.942,22 €
```

Es la suma de todas tus compras menos el dinero que recuperaste por ventas, todo histórico. **No es** el cost basis de tus posiciones actuales (esto sería FIFO sobre las shares vivas). Útil como "este es el dinero neto que sigue dentro de la cartera o que circuló por ella".

---

## Bloque 4 — APORTACIONES MENSUALES

```
APORTACIONES MENSUALES (compras brutas, incluye saveback/regalos)
  Este mes (2026-04):               915,64 €
  Media últimos 12m:                505,01 €
  Δ vs media:                        +81.3%
```

- **Este mes** = suma de todas las BUYs del mes en curso (savings plan + manuales + saveback + regalos). Matchea la pestaña "Dinero invertido YYYY" de tu Sheet.
- **Media últimos 12m** = media simple de los meses con BUYs > 0 en los últimos 12 (no diluye con meses sin actividad).
- **Δ vs media** = `(este_mes − media) / media`. Positivo = este mes inviertes más de lo habitual.

Si no hay histórico suficiente para comparar (cuenta nueva), muestra los últimos 3 meses con aportación.

> **Por qué incluye saveback/regalos**: para matchear tu Excel manual. Si quieres "solo MI dinero del mes", calcula a mano: `915,64 − saveback_de_abril`.

---

## Bloque 5 — CONCENTRACIÓN

```
CONCENTRACIÓN (% sobre posiciones, alerta a >35%)
  Core S&P 500 USD (Acc)        44.63%  ████████████████████████████  ⚠ alta
  Core MSCI EM IMI USD (Acc)    15.88%  ██████████
  ...
  ⚠ Top-1 (Core S&P 500 USD (Acc)) representa 44.6% de la cartera.
    Considera diversificar si quieres reducir riesgo de concentración.
```

Distribución del valor de tus posiciones (excluye cash) por activo, ordenada de más a menos peso. Las barras son visuales — mismo ratio que el % numérico.

- **Threshold por defecto**: 35%. Configurable en `config.yaml > concentration_threshold`.
- **Alerta `⚠ alta`** aparece cuando una posición supera el threshold.
- **Aviso de Top-1** aparece debajo si la posición principal supera el threshold.

No es una recomendación de cambiar nada — es una señal de que **mires** si esa concentración te resulta cómoda. Una cartera 100% S&P 500 está perfectamente concentrada y muchos lo prefieren así.

---

## Bloque opcional — POR POSICIÓN (`--verbose`)

```
$ python tr_sync.py --insights --verbose
```

Activa una tabla por activo con cost basis propio + bruto y plusvalía en cada métrica. Solo para diagnóstico — útil si ves un número raro en los bloques anteriores y quieres ver qué posición lo explica.

---

## Cómo se acumula el histórico (`_snapshots`)

Cada `make insights`, `make portfolio` y `make backfill-snapshots` añaden una fila a la pestaña oculta `_snapshots`:

| Columna | Contenido |
|---|---|
| `ts` | Timestamp ISO |
| `cash_eur` | Cash en TR |
| `positions_value_eur` | Valor de todas las posiciones |
| `cost_basis_eur` | TR cost basis con saveback (si aplica) |
| `total_eur` | Cash + posiciones |

La pestaña `_snapshots_positions` añade una fila por (snapshot, ISIN) con shares y net_value. Útil para gráficas de evolución por activo.

Ambas pestañas están **ocultas** por defecto. Para verlas en Google Sheets: menú `Ver → Pestañas ocultas`.

---

## FAQ

**Q: Mi MWR all-time es +23%. ¿No es muy alto?**

Depende de qué hayas tenido. Si tu cartera está concentrada en S&P 500 + tech ETFs durante un mercado alcista (2024-2026 ha sido fuerte) y has comprado en bajadas, +20-25% anualizado es plausible. Cifras así no se mantienen indefinidamente — los mercados se normalizan. Compara contra el S&P 500 YTD/12m del periodo equivalente.

**Q: TR app me da +14,08% pero `make insights` me da +14,51%. ¿De dónde sale la diferencia?**

Pequeñas diferencias (<1pp) suelen venir de:
- Mi resta de saveback usa `amount_eur` del evento; TR usa `value_at_delivery` interno (puede haber un spread USD/EUR de céntimos por share saveback).
- Movimientos de precio entre el momento que abriste la app y el momento que ejecutas el script.

Si la diferencia es >2pp, posiblemente hay alguna posición con `cost_basis` raro: ejecuta con `--verbose` y revisa la tabla por posición.

**Q: ¿Por qué el bloque "Aportado neto" no matchea mi cost basis propio?**

Porque son cosas distintas:
- **Aportado neto** = `Σ BUYs − Σ SELLs` histórico (incluye lo que vendiste y ya no tienes).
- **Cost basis propio** = lo que pagaste por las shares que tienes vivas AHORA (FIFO con saveback a coste 0).

Si has hecho ventas con beneficio o pérdida, los dos números divergen.

**Q: ¿Cuándo refresca los datos? ¿Cada cuánto debo correr `make insights`?**

Cada ejecución va a TR y descarga todo de cero (transacciones + portfolio). Con el cache TTL=5min, dos ejecuciones seguidas reutilizan los datos sin volver a TR.

Para uso normal: una vez al día / a la semana. Cada ejecución guarda un snapshot en `_snapshots`, así que el histórico crece solo.

**Q: La métrica X me parece engañosa, ¿se puede desactivar?**

Sí. En `config.yaml > features` pones la feature en `false`:

```yaml
features:
  concentration: false   # apaga el bloque de concentración
  saveback_metrics: false   # apaga las dos lecturas de plusvalía
```

Lista completa: `make features`.

**Q: Quiero un MWR para un periodo concreto que no sea YTD/12m.**

Hoy no está expuesto en CLI. Tienes la función pura `core.metrics.mwr()` que acepta `start` y `end` arbitrarios; se usa así:

```python
from core.metrics import mwr
from datetime import datetime
from zoneinfo import ZoneInfo
TZ = ZoneInfo("Europe/Madrid")
mwr(txs, snapshot, start=datetime(2025, 6, 1, tzinfo=TZ), start_value=4500.0)
```

Si lo necesitas como flag CLI, abre un issue.
