# Mejoras pendientes

Lista priorizada de cosas a mejorar en el proyecto, escritas para que dentro de 3 meses (o cuando lleguen colaboradores) no haga falta repensarlas desde cero.

---

## ✅ Hecho recientemente

- **INSIGHTS.md** — doc explicando bloque a bloque el output de `make insights`.
- **Cache TR** — `core/cache.py` con TTL=5min, evita re-fetch entre comandos. `--refresh` para invalidar.
- **Tests del adapter TR** — `test_adapter.py` con 24 tests cubriendo cada eventType (parser mockeado).
- **Per-asset concentration limits** — campo `concentration_limits: {ISIN: float}` en config. Soporta `concentration_threshold: null` para apagar el global y solo alertar lo configurado.
- **Solana en backfill** — exchange `BHS` capturado vía compactPortfolio. Fallback `instrument_details(isin)` para descubrir exchanges no hardcoded.
- **Determinismo en backfill** — timestamps normalizados a `T12:00:00`. Re-runs no duplican filas.
- **Currency exposure** — campo `asset_currencies: {ISIN: divisa}` + bloque nuevo en `make insights` agrupando patrimonio (cash + posiciones) por divisa.
- **`make mwr-flows`** — vuelca los flujos de caja del MWR en TSV para sanity check con XIRR nativo de Sheets/Excel. Soporta `LOCALE=es` para coma decimal.
- **MWR sanity check humano** — verificado vs `=TIR.NO.PER` de Sheets, diferencia 0,02pp (rounding). MWR all-time es real.
- **Benchmark vs MWR** — campo `benchmark_isin` en config. `make insights` compara tu MWR (modo income) contra el rendimiento anualizado del benchmark en all-time / YTD / 12m, con Δ en pp y marca `✓` si bates al índice.

---

## Prioridad alta

### 1. ~~Sacar sync_renta a reports/renta_es.py~~ ✅ HECHO

`reports/renta_es.py` (~720 líneas) contiene toda la lógica del informe IRPF español. `tr_sync.py` baja a ~1925 líneas. Shims de compat en tr_sync.py (`_collect_*`, `_build_lots_and_sales`, `_retentions_by_country`) para que tests existentes no rompan. Cuando llegue otro régimen fiscal (UK ISA / DE Steuerbericht / PT IRS) se añade un módulo hermano sin tocar tr_sync.

### 2. Sanity check del MWR all-time (parcialmente automatizado)

Ya hay herramienta para verificarlo: `make mwr-flows` vuelca los flujos en TSV. Pega en Sheets/Excel y aplica `=XIRR(B:B, A:A)`. Pendiente la verificación humana.

### 3. ~~Solana en backfill~~ ✅ HECHO

Resuelto: TR expone Solana en exchange `BHS` (Bitstamp Handelssystem) y compactPortfolio incluye el field `exchangeIds`. Caso futuro de fallback (ISIN cripto sin exchange en compactPortfolio): `fetch_instrument_exchanges()` consulta `instrument_details(isin)` para descubrir.

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

### 5. ~~Benchmark comparison vs índice de referencia~~ ✅ HECHO

Implementado: campo `benchmark_isin` en config + bloque "RENTABILIDAD VS BENCHMARK" en `make insights`. Compara tu MWR contra rendimiento anualizado del benchmark en all-time / YTD / 12m con Δ en pp. Ver `CONFIG.md` y `INSIGHTS.md`.

### 6. ~~Currency exposure breakdown~~ ✅ HECHO

Implementado: `asset_currencies` en config + bloque "EXPOSICIÓN POR DIVISA" en `make insights`. Ver `CONFIG.md` y `INSIGHTS.md`.

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

### 9. ~~Performance attribution por posición~~ ✅ HECHO

Implementado: `core.metrics.per_position_attribution()` + bloque "ATRIBUCIÓN DE RENDIMIENTO POR POSICIÓN" en `make insights`. MWR individual por ISIN ponderado por peso en cartera. Ver `INSIGHTS.md`.

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
