# Mejoras pendientes

Lista priorizada de cosas a mejorar en el proyecto, escritas para que dentro de 3 meses (o cuando lleguen colaboradores) no haga falta repensarlas desde cero.

---

## ✅ Hecho

Cronológicamente (más reciente arriba):

- **`sync_renta` extraído a `reports/renta_es.py`** (~600 líneas movidas; tr_sync.py baja a ~1925). La carpeta `reports/` queda preparada para futuros regímenes fiscales (UK ISA, DE Steuerbericht, PT IRS).
- **Performance attribution per posición** — `core.metrics.per_position_attribution()` + bloque "ATRIBUCIÓN DE RENDIMIENTO POR POSICIÓN" en `make insights`. MWR per-ISIN ponderado.
- **Benchmark comparison** — `benchmark_isin` en config. `make insights` compara MWR (income) contra rendimiento anualizado del benchmark en all-time / YTD / 12m, con Δ en pp y `✓` si bates al índice.
- **Currency exposure** — `asset_currencies: {ISIN: divisa}` en config + bloque "EXPOSICIÓN POR DIVISA" agrupando patrimonio total por divisa.
- **MWR sanity check (vía `make mwr-flows`)** — exporta flujos en TSV para verificar con XIRR nativo de Sheets/Excel. **Validado**: mi `xirr()` casero converge al mismo número que `=TIR.NO.PER` (diferencia 0,02pp, rounding).
- **Solana en backfill** — exchange `BHS` capturado vía `compactPortfolio.exchangeIds`. Fallback `instrument_details(isin)` para descubrir exchanges no hardcoded en futuros ISINs cripto.
- **Determinismo en backfill** — timestamps normalizados a `T12:00:00`. Re-ejecutar es idempotente (no genera duplicados).
- **Per-asset concentration limits** — `concentration_limits: {ISIN: float}` + soporte de `concentration_threshold: null` para apagar el global y solo alertar lo configurado.
- **Cache TR** — `core/cache.py` con TTL=5min. Encadena comandos sin re-fetch. `--refresh` para invalidar. Cache extendida a v2 para soportar también histórico de benchmarks.
- **Tests del adapter TR** — `test_adapter.py` con 24 tests cubriendo cada eventType (parser mockeado).
- **INSIGHTS.md** — doc explicando bloque a bloque el output de `make insights`, las 2 lecturas de cost basis, los 3 horizontes de MWR, el toggle income/deposit, y FAQ.
- **Refactor a `core/` + `brokers/` + `storage/`** — patrón de tres capas. ARCHITECTURE.md actualizado.
- **Snapshot store + backfill snapshots históricos** — pestañas ocultas `_snapshots` y `_snapshots_positions` con esquema agregado + por posición. Persistencia automática + reconstrucción retroactiva via TR price history.
- **Feature registry + capabilities** — `core/features.py` + `brokers/<x>/__init__.py: CAPABILITIES`. Cada feature declara qué necesita; `make features` muestra qué está activo y por qué.

---

## Prioridad media

Items concretos, valor real, esfuerzo bajo-medio.

### 1. Snapshot manual override

Cuando un snapshot reconstruido es claramente erróneo o quieres añadir uno manual de antes del backfill, no hay manera. Añadir:

```bash
python tr_sync.py --snapshot-set 2024-08-22 --positions 100 --cash 5000
python tr_sync.py --snapshot-delete 2025-04-26
```

Escribe directo a `_snapshots`. Útil para correcciones puntuales.

**Esfuerzo**: ~30min. **Beneficio**: control fino sobre el histórico.

### 2. Telemetría / logging persistente

Tú detectas bugs porque ejecutas en consola y lees el output. Si lo usa otra persona y algo falla silenciosamente (parser que no extrae shares, exchange equivocado, snapshot con precio raro, rate limit no manejado), no te enteras.

Propuesta mínima:
- Logger a `~/.broker-sync/logs/broker-sync.log` con rotación (último 7 días).
- Niveles WARN/ERROR contables.
- `make logs-tail` que hace `tail -f` al log.

**Esfuerzo**: ~1h. **Beneficio**: observabilidad básica antes de que algo se rompa silenciosamente.

### 3. Refactor del adapter TR (registry pattern)

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

### 4. Auto-rebalance suggestions

Dados los `concentration_limits` y la cartera actual, calcular qué hay que comprar/vender para volver dentro del límite (o cerca del target weight implícito).

```
SUGERENCIAS DE REBALANCEO
  Solana            actual 9,1% > límite 8,0%
    → vender ~30 € para volver a 8%

  (Para definir target weights más finos, añade `target_weights` en config.)
```

Solo se activa si tienes `concentration_limits` configurados. Con `null` global, salta.

**Esfuerzo**: ~1h. **Beneficio**: convierte alertas pasivas en sugerencias accionables.

### 5. Realized vs unrealized split en MWR all-time

El MWR all-time mezcla:
- Plusvalías realizadas de posiciones ya vendidas (NVIDIA, Apple, Tesla, etc.).
- Plusvalías latentes de posiciones actuales.

Sería útil ver el desglose:

```
MWR all-time desglosado:
  De posiciones vivas:       +18,2%
  De ventas pasadas:          +4,9%   ← te das cuenta de que NVIDIA tiró fuerte
  Total all-time:            +23,1%
```

**Esfuerzo**: ~1h (segregar flujos por "ISIN actualmente vivo" vs "no"). **Beneficio**: contexto sobre de dónde viene el alpha.

---

## Prioridad baja

Items grandes o de dudoso retorno hasta que haya validación de producto.

### 6. Mecanismo de alertas

Alertas semanales por email/Telegram cuando:
- Una posición cae >5% en una semana.
- Aportación del mes es <50% o >150% de la media.
- Concentración aumenta >10pp en una semana.

Ejecutado via cron (GitHub Actions). Salida a Telegram bot o SMTP.

**Esfuerzo**: ~2-3h. **Beneficio**: empuje al hábito de revisar sin abrir el script.

### 7. Web UI mínima

Solo cuando se valide que los insights aportan valor a alguien externo. La consola es suficiente para uso personal.

**Esfuerzo**: 1-2 días. **Beneficio**: solo si pivota a producto.

### 8. Multi-broker real (IBKR / DEGIRO)

Solo cuando haya un caso real (alguien que use IBKR + quiera unificar con TR). El diseño actual lo soporta — falta el código.

**Esfuerzo**: 2-3 días por broker. **Beneficio**: solo si hay demanda real.

### 9. Sesión TR cloud / SaaS

El bloqueador real para uso público es la sesión TR de 2.5h. Para resolverlo de verdad: backend cloud, auth propia, DB, frontend.

Eso ya es un **producto SaaS**. Solo a considerar si la idea de monetizar pasa de hipótesis a validación.

**Esfuerzo**: semanas. **Beneficio**: producto público viable. No empezar sin validación previa.

### 10. CI improvements

- Type checking con `mypy --strict` sobre `core/`.
- Coverage report en CI (apuntar a >80% en `core/`).
- Pre-commit hook con `ruff` para style.
- Test matrix Python 3.11 / 3.12 / 3.14.

**Esfuerzo**: ~1-2h. **Beneficio**: detectar regresiones más temprano.

### 11. Documentación arquitectura

`ARCHITECTURE.md` describe el patrón pero no tiene un diagrama visual. Añadir uno (ASCII o imagen) ayuda al primer vistazo.

**Esfuerzo**: ~30min. **Beneficio**: onboarding más rápido.

### 12. Tax loss harvesting suggestions

Posiciones con plusvalía latente negativa que podrías vender para realizar la pérdida y compensar ganancias del mismo año fiscal (con cuidado del wash sale rule español de 2 meses si vuelves a comprar lo mismo).

```
TAX LOSS HARVESTING (orientativo)
  Solana           plusvalía latente: −127,82 €
    → Vender ahora compensa hasta −127,82 € de ganancias.
    Si has cerrado +200 € en 2026, podrías reducir base imponible 64% × 127,82 = 81,80 €.
```

Solo orientativo, requiere asesoría fiscal real.

**Esfuerzo**: ~2h. **Beneficio**: recordatorio práctico antes de fin de año.

### 13. Proyección Monte Carlo

Simular 1.000-10.000 escenarios futuros (10 años) dada tu aportación mensual y asunciones de retorno (μ=8%, σ=16% típicas S&P). Mostrar percentiles 10/50/90.

```
PROYECCIÓN A 10 AÑOS (Monte Carlo, 10000 simulaciones, μ=8%, σ=16%)
  Mediana (p50):       ~ 220.000 €
  Pesimista (p10):     ~ 145.000 €
  Optimista (p90):     ~ 340.000 €
```

**Esfuerzo**: ~3-4h. **Beneficio**: visualización a largo plazo + factor "wow" si lo enseñas a otros. Pero las asunciones (μ, σ) **dominan el resultado**, así que no es decisión-grade — entretenimiento.

### 14. Goal tracker

Define una meta (`100.000 € para 2030`) en config y muestra:
- Tasa de progreso vs lineal (con tu aportación actual + retorno asumido, ¿llegas?).
- Cuánto tendrías que ahorrar/mes para llegar al objetivo en X años.

Útil como motivación; depende de Monte Carlo (#13) para hacerlo robusto.

**Esfuerzo**: ~2h. **Beneficio**: framing motivacional. Solo si te enganchas a metas explícitas.

---

## Cosas que dejaría como están

- ✅ Arquitectura `core/brokers/storage/reports/` — limpia, no la toques.
- ✅ Feature registry — ya hace su trabajo.
- ✅ Tests unitarios — 156 tests pasando, cobertura buena.
- ✅ Sync gastos/ingresos/inversiones — funciona, no migrar a otro layout.
- ✅ Cache TR — simple y suficiente.
- ✅ Snapshot store — abstracción correcta.

---

## Cómo decidir qué hacer siguiente

- **Para uso personal estable**: nada urgente. Items 1-5 son polish, no necesarios.
- **Si te molesta algo en la práctica**: typically #1 (snapshot manual) o #2 (logging) por orden de utilidad.
- **Si quieres seguir iterando técnica**: #3 (registry pattern) o #5 (realized vs unrealized split).
- **Si quieres validar producto**: PARA y enseña la herramienta a 2-3 personas. La siguiente prioridad surge de su feedback.
- **Si algo se rompe en producción**: #2 (telemetría) primero para entender qué se rompió.
