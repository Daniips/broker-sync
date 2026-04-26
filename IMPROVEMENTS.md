# Mejoras pendientes

Lista priorizada de cosas a mejorar en el proyecto, escritas para que dentro de 3 meses (o cuando lleguen colaboradores) no haga falta repensarlas desde cero.

---

## ✅ Hecho recientemente

- **INSIGHTS.md** — doc explicando bloque a bloque el output de `make insights`.
- **Cache TR** — `core/cache.py` con TTL=5min, evita re-fetch entre comandos. `--refresh` para invalidar.
- **Tests del adapter TR** — `test_adapter.py` con 24 tests cubriendo cada eventType (parser mockeado).
- **Per-asset concentration limits** — campo `concentration_limits: {ISIN: float}` en config. Soporta `concentration_threshold: null` para apagar el global y solo alertar lo configurado.

---

## Prioridad alta

### 1. `tr_sync.py` sigue en ~2300 líneas

El elefante restante es **`sync_renta`** (~500 líneas, FIFO + dividendos + intereses + bonos + Modelo 720, todo Spain-specific). Sacarlo a `reports/renta_es.py` deja `tr_sync.py` ~1700 líneas y formaliza que Renta es un report **plug-in** específico de España. Cuando llegue otro régimen fiscal (UK ISA / DE Steuerbericht / PT IRS) cuadra el patrón.

**Esfuerzo**: ~1-2h. **Beneficio**: separación de concerns; ningún cambio de comportamiento; abre la puerta a otros report types.

### 2. Sanity check del MWR all-time

El MWR all-time actual (+23,08% income / +19,66% deposit) **huele alto** para una cartera 60% S&P en 18 meses. Posibles causas legítimas: comprar en bajadas + dividendos + ventas con beneficio. Posibles causas técnicas: bug sutil en `mwr()` que las pruebas sintéticas no atrapan.

**Acción**: copiar los flujos de SP500 (BUYs + dividendos + valor actual) a una hoja nueva con XIRR formula nativa de Sheets/Excel. Comparar contra el +23%. Si discrepa significativamente, debug. Si cuadra, documentarlo.

**Esfuerzo**: ~30min de Excel. **Beneficio**: confianza real en el número.

### 3. Solana en backfill

Solana no se cubre en `backfill-snapshots` porque `aggregateHistoryLight` con LSX/BTLX/BSF no devuelve datos. Investigar:
- Si pytr expone `instrumentDetails(isin)` que indique los exchanges disponibles por ISIN.
- Si TR tiene un topic distinto para cripto (`cryptoOhlcHistory`, `cryptoPriceHistory`).
- Fallback: Coingecko vía API gratuita (mapping ISIN ↔ Coingecko ID en config).

**Esfuerzo**: ~1-2h investigando + integración. **Beneficio**: backfill cubre 100% de la cartera. Crítico si llega un usuario crypto-pesado.

---

## Prioridad media

### 4. Snapshot manual override

Cuando un snapshot reconstruido es claramente erróneo o quieres añadir uno manual de antes del backfill, no hay manera. Añadir:

```bash
python tr_sync.py --snapshot-set 2024-08-22 --positions 100 --cash 5000
python tr_sync.py --snapshot-delete 2025-04-26
```

Escribe directo a `_snapshots`. Útil para correcciones puntuales.

**Esfuerzo**: ~30min. **Beneficio**: control fino sobre el histórico.

### 5. Benchmark comparison vs índice de referencia

Mostrar el MWR de tu cartera **al lado del rendimiento de un benchmark** (S&P 500, MSCI World, etc.) en el mismo periodo. Responde a: "¿estoy batiendo al mercado o me estoy comiendo costes/decisiones malas?".

```
RENTABILIDAD VS BENCHMARK (12 meses)
  Tu cartera (MWR):       +25,17 %
  S&P 500 (€):            +18,40 %
  Δ vs benchmark:          +6,77 pp  ← bates al índice
```

Implementación: configurar un `benchmark_isin` (ej. iShares Core S&P 500 EUR), descargar su histórico de precios con `aggregateHistoryLight` (ya lo soportamos), calcular el rendimiento del benchmark en YTD/12m/all-time desde su precio inicial.

**Esfuerzo**: ~1h. **Beneficio**: contexto real para tu MWR — sin esto, no sabes si +25% es bueno o malo.

### 6. Currency exposure breakdown

Tu cartera tiene exposición a USD (ETFs USA), EUR (MSCI Europe, cash) y otros (cripto). El riesgo de divisa no es obvio en `make insights`. Añadir un pequeño bloque:

```
EXPOSICIÓN POR DIVISA
  USD       7.430,28 €  (78,9 %)   ← S&P, tech, EM IMI, India, Small Cap
  EUR       2.069,77 €  (21,9 %)   ← MSCI Europe + cash en EUR
  OTROS       278,28 €  ( 3,0 %)   ← Solana
```

Implementación: mapping ISIN → divisa de denominación en config (`asset_currencies: {ISIN: "USD"}`), agrupar `net_value_eur` por divisa.

**Esfuerzo**: ~30min. **Beneficio**: ver de un vistazo si tienes demasiada exposición a una divisa concreta.

### 7. Telemetría / logging persistente

Tú detectas bugs porque ejecutas en consola y lees el output. Si lo usa otra persona y algo falla silenciosamente (parser que no extrae shares, exchange equivocado en backfill, snapshot con precio raro, rate limit no manejado), no te enteras.

Propuesta mínima:
- Logger a `~/.broker-sync/logs/broker-sync.log` con rotación (último 7 días).
- Niveles WARN/ERROR contables.
- `make logs-tail` que hace `tail -f` al log.

**Esfuerzo**: ~1h. **Beneficio**: observabilidad básica antes de que algo se rompa silenciosamente.

### 8. Refactor del adapter TR (registry pattern)

`raw_event_to_tx` tiene un `if/elif` largo por cada `eventType`. Cuando añadas más event types o cuando un futuro broker (IBKR) requiera código similar, se vuelve frágil. Refactor a un dict de handlers:

```python
EVENT_HANDLERS = {
    "TRADING_TRADE_EXECUTED": _handle_trade,
    "TRADING_SAVINGSPLAN_EXECUTED": _handle_trade,
    "SAVEBACK_AGGREGATE": _handle_saveback,
    ...
}
```

**Esfuerzo**: ~1h. **Beneficio**: más fácil añadir event types nuevos; tests por handler en vez de por función gigante.

### 9. Performance attribution por posición

Hoy sabes que tu cartera rinde +25% MWR pero no qué posición lo aporta. Mostrar contribución de cada activo al rendimiento total:

```
ATRIBUCIÓN DE RENDIMIENTO (12m)
  S&P 500             +14,2 pp   ████████████████
  SP500 Tech          +5,8 pp    ████████
  MSCI Europe         +1,4 pp    ██
  Solana              −1,3 pp    ─
  TOTAL              +25,2 pp
```

Implementación: cada posición con su MWR individual, ponderado por % de cartera. Requiere más esfuerzo: segregar flujos por ISIN y calcular XIRR per-position.

**Esfuerzo**: ~2-3h. **Beneficio**: te dice qué posiciones están funcionando y cuáles arrastran.

---

## Prioridad baja

### 10. Mecanismo de alertas

Mencionado en el menú original pero pospuesto. Idea: alertas semanales por email/Telegram cuando:
- Una posición cae >5% en una semana.
- Aportación del mes es <50% de la media.
- Concentración aumenta >10pp en una semana.

Ejecutado via cron (GitHub Actions, mismo workflow que `sync.yml`). Salida a Telegram bot o SMTP.

**Esfuerzo**: ~2-3h. **Beneficio**: empuje al hábito de revisar sin abrir el script.

### 11. Web UI mínima

Solo cuando se valide que los insights aportan valor a alguien externo. La consola es suficiente para uso personal. Una web mínima sería: render del output en HTML estático generado por GitHub Pages, con auth básica.

**Esfuerzo**: 1-2 días. **Beneficio**: solo justificable si pivota a producto.

### 12. Multi-broker real (IBKR / DEGIRO)

Solo cuando haya un caso real (alguien que use IBKR + quiera unificar con TR). El diseño actual lo soporta — falta el código del broker.

**Esfuerzo**: 2-3 días por broker. **Beneficio**: solo si hay demanda real.

### 13. Sesión TR cloud / SaaS

El bloqueador real para uso público es la sesión TR de 2.5h. Para resolverlo necesitarías un backend cloud que mantenga la sesión TR del usuario, auth propia, DB para persistir snapshots/txs/config por usuario, frontend.

Eso ya es un **producto SaaS de verdad**. Solo a considerar si la idea de monetizar pasa de hipótesis a validación.

**Esfuerzo**: semanas. **Beneficio**: producto público viable. No empezar sin validación previa.

### 14. Mejoras de CI

- Type checking con `mypy --strict` sobre `core/`.
- Coverage report en CI (apuntar a >80% en `core/`).
- Pre-commit hook con `ruff` para style.
- Test matrix Python 3.11 / 3.12 / 3.14.

**Esfuerzo**: ~1-2h. **Beneficio**: detectar regresiones más temprano.

### 15. Documentación arquitectura

`ARCHITECTURE.md` describe el patrón pero no tiene un diagrama visual. Añadir uno (ASCII o imagen) ayuda al primer vistazo.

**Esfuerzo**: ~30min. **Beneficio**: onboarding más rápido.

### 16. Proyección Monte Carlo / "¿en cuánto tendré X €?"

Dado tu ritmo de aportación actual y la distribución de tu cartera, simular escenarios futuros:

```
PROYECCIÓN A 10 AÑOS (Monte Carlo, 1000 simulaciones)
  Aportación mensual:     915 € (la del mes actual)
  Mediana esperada:    180.000 €
  Percentil 10:        130.000 €
  Percentil 90:        260.000 €
```

Requiere asumir distribuciones de retorno (típico: log-normal con μ y σ por activo). Útil pero ruidoso — los retornos pasados no son indicativos. Yo lo trataría como entretenimiento, no como decisión.

**Esfuerzo**: ~3-4h. **Beneficio**: visualización a largo plazo + factor "wow" si lo enseñas a otros.

---

## Cosas que dejaría como están

- ✅ Arquitectura `core/brokers/storage/` — limpia, no la toques.
- ✅ Feature registry — ya hace su trabajo.
- ✅ Tests unitarios de `core/` — cobertura buena (143 tests).
- ✅ Sync gastos/ingresos/inversiones — funciona, no migrar a otro layout.
- ✅ Cache TR — simple y suficiente.
- ✅ Snapshot store — abstracción correcta.

---

## Cómo decidir qué hacer siguiente

Si **vas a usar la herramienta tú durante un tiempo** sin más cambios estructurales: dejá los items 1-9 para cuando te lo pidan los datos.

Si **quieres seguir iterando técnica**: ataca #1 (saca renta a su módulo) — es el cleanup más obvio y libera espacio mental.

Si **quieres validar producto**: NO hagas más mejoras técnicas, enseña la herramienta a 2-3 personas que usen TR y observa su fricción real.

Si **quieres ver "el pulso" de tu inversión**: #5 (benchmark) o #9 (attribution) te dan contexto que ahora mismo no tienes.

Si **algo se rompe en producción**: #7 (telemetría) primero para entender qué se rompió, luego el fix.
