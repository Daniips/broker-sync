# Mejoras pendientes

Lista priorizada de cosas a mejorar en el proyecto, escritas para que dentro de 3 meses (o cuando lleguen colaboradores) no haga falta repensarlas desde cero.

> Las primeras tres (INSIGHTS.md, cache TR, adapter tests) **ya están hechas**. Lo que sigue está pendiente.

---

## Prioridad alta

### 1. `tr_sync.py` sigue en 2271 líneas

El elefante restante es **`sync_renta`** (~500 líneas, FIFO + dividendos + intereses + bonos + Modelo 720, todo Spain-specific). Sacarlo a `reports/renta_es.py` deja `tr_sync.py` ~1700 líneas y formaliza que Renta es un report **plug-in** específico de España. Cuando llegue otro régimen fiscal (UK ISA / DE Steuerbericht / PT IRS) cuadra el patrón.

**Esfuerzo**: ~1-2h. **Beneficio**: separación de concerns; ningún cambio de comportamiento; abre la puerta a otros report types.

### 2. Per-asset concentration limits

Tu Excel ya tiene una columna `Límite` por activo (Solana 8%, etc.). El config debería aceptar:

```yaml
concentration_limits:
  XF000SOL0012: 0.08    # SOL: límite 8%
  IE00B5BMR087: 0.50    # SP500: límite 50%
```

Y el bloque de concentración debería avisar **cuando un activo concreto sobrepasa SU límite**, no solo el threshold global. El threshold global queda como fallback.

**Esfuerzo**: ~30min (config + condicional en `sync_insights`). **Beneficio**: matchea cómo piensas tú la concentración.

### 3. Sanity check del MWR all-time

El MWR all-time actual (+23,08% income / +19,66% deposit) **huele alto** para una cartera 60% S&P en 18 meses. Posibles causas legítimas: comprar en bajadas + dividendos + ventas con beneficio. Posibles causas técnicas: bug sutil en `mwr()` que las pruebas sintéticas no atrapan.

**Acción**: copiar los flujos de SP500 (BUYs + dividendos + valor actual) a una hoja nueva con XIRR formula nativa de Sheets/Excel. Comparar contra el +23%. Si discrepa significativamente, debug. Si cuadra, documentarlo.

**Esfuerzo**: ~30min de Excel. **Beneficio**: confianza real en el número.

---

## Prioridad media

### 4. Snapshot manual override

Cuando un snapshot reconstruido es claramente erróneo (Solana sin precio, fecha rara) o quieres añadir uno manual de antes del backfill (ej. una cifra que recuerdas de un screenshot viejo), no hay manera. Añadir:

```bash
python tr_sync.py --snapshot-set 2024-08-22 --positions 100 --cash 5000
python tr_sync.py --snapshot-delete 2025-04-26  # borra entradas con ese ts exacto
```

Escribe directo a `_snapshots`. Útil para correcciones puntuales.

**Esfuerzo**: ~30min. **Beneficio**: control fino sobre el histórico.

### 5. Soporte de cripto en backfill

Solana no se cubre porque `aggregateHistoryLight` con LSX/BTLX/BSF no devuelve datos. Investigar:
- Si TR tiene un endpoint distinto para cripto (`cryptoOhlcHistory`, `cryptoPriceHistory`, etc.).
- Si pytr expone algún hook para crypto que no estoy usando.
- Fallback: pull de Coingecko vía API gratuita (Solana → SOL/EUR daily prices). Limitación: requiere mapear ISIN ↔ Coingecko ID en config.

**Esfuerzo**: ~1-2h investigando + integración. **Beneficio**: backfill cubre 100% de la cartera. Crítico si llega un usuario crypto-pesado.

### 6. Telemetría / logging persistente

Tú detectas bugs porque ejecutas en consola y lees el output. Si lo usa otra persona y algo falla silenciosamente (parser que no extrae shares, exchange equivocado en backfill, snapshot con precio raro, rate limit no manejado), no te enteras.

Propuesta mínima:
- Logger a `~/.broker-sync/logs/broker-sync.log` con rotación (último 7 días).
- Niveles WARN/ERROR contables.
- `make logs-tail` que hace `tail -f` al log.

**Esfuerzo**: ~1h. **Beneficio**: observabilidad básica antes de que algo se rompa silenciosamente.

### 7. Refactor leve del adapter TR (registry pattern)

`raw_event_to_tx` tiene un `if/elif` largo por cada `eventType`. Cuando añadas más event types o cuando un futuro broker (IBKR) requiera código similar, se vuelve frágil. Refactor a un dict de handlers:

```python
EVENT_HANDLERS = {
    "TRADING_TRADE_EXECUTED": _handle_trade,
    "TRADING_SAVINGSPLAN_EXECUTED": _handle_trade,
    "SAVEBACK_AGGREGATE": _handle_saveback,
    ...
}

def raw_event_to_tx(raw, *, tz, gift_overrides=None):
    handler = EVENT_HANDLERS.get(raw.get("eventType"))
    if not handler:
        return None
    return handler(raw, tz=tz, gift_overrides=gift_overrides)
```

**Esfuerzo**: ~1h. **Beneficio**: más fácil añadir event types nuevos; tests por handler en vez de por función gigante.

---

## Prioridad baja

### 8. Mecanismo de alertas

Mencionado en el menú original pero pospuesto. Idea: alertas semanales por email/Telegram cuando:
- Una posición cae >5% en una semana.
- Aportación del mes es <50% de la media (te has quedado corto).
- Aportación del mes es >150% de la media (gasto inusual o lump sum).
- Concentración aumenta >10pp en una semana.

Ejecutado via cron (GitHub Actions, mismo workflow que `sync.yml`). Salida a Telegram bot o SMTP.

**Esfuerzo**: ~2-3h. **Beneficio**: empuje al hábito de revisar sin abrir el script.

### 9. Web UI mínima

Solo cuando se valide que los insights aportan valor a alguien externo. La consola es suficiente para uso personal. Una web mínima sería: render del output de `make insights` en HTML estático generado por GitHub Pages, con auth básica.

**Esfuerzo**: 1-2 días. **Beneficio**: solo justificable si pivota a producto.

### 10. Multi-broker real (IBKR / DEGIRO)

Solo cuando haya un caso real (alguien que use IBKR + quiera unificar con TR). El diseño actual lo soporta — falta el código del broker. Pero es una inversión grande para un beneficio especulativo.

**Esfuerzo**: 2-3 días por broker. **Beneficio**: solo si hay demanda real.

### 11. Sesión TR cloud / SaaS

El bloqueador real para uso público es la sesión TR de 2.5h. Cualquiera que abra el script tendrá que (a) instalar pytr, (b) login con SMS, (c) repetir cada 2-3h. Para resolverlo de verdad necesitarías:
- Un backend cloud que mantenga la sesión TR del usuario.
- Auth propia (cuentas en tu servicio).
- DB para persistir snapshots, txs, config por usuario.
- Frontend / API para consumir.

Eso ya es un **producto SaaS de verdad**. Solo a considerar si la idea de monetizar pasa de hipótesis a validación.

**Esfuerzo**: semanas. **Beneficio**: producto público viable. No empezar sin validación previa.

### 12. Mejoras de CI

- Type checking con `mypy --strict` sobre `core/`.
- Coverage report en CI (apuntar a >80% en `core/`).
- Pre-commit hook con `ruff` para style.
- Test matrix Python 3.11 / 3.12 / 3.14.

**Esfuerzo**: ~1-2h. **Beneficio**: detectar regresiones más temprano.

### 13. Documentación arquitectura

`ARCHITECTURE.md` describe el patrón pero no tiene un diagrama visual. Añadir uno (ASCII o imagen) ayuda al primer vistazo.

**Esfuerzo**: ~30min. **Beneficio**: onboarding más rápido.

---

## Cosas que dejaría como están

- ✅ Arquitectura `core/brokers/storage/` — limpia, no la toques.
- ✅ Feature registry — ya hace su trabajo.
- ✅ Tests unitarios de `core/` — cobertura buena (140 tests).
- ✅ Sync gastos/ingresos/inversiones — funciona, no migrar a otro layout.
- ✅ Cache TR — ya hecho, simple y suficiente.
- ✅ Snapshot store — ya hecho, abstracción correcta.

---

## Cómo decidir qué hacer siguiente

Si **vas a usar la herramienta tú durante un tiempo** sin más cambios estructurales: dejá los items 1-7 para cuando te lo pidan los datos.

Si **quieres seguir iterando técnica**: ataca #1 (saca renta a su módulo) — es el cleanup más obvio y libera espacio mental.

Si **quieres validar producto**: NO hagas más mejoras técnicas, enseña la herramienta a 2-3 personas que usen TR y observa su fricción real. La siguiente prioridad surge sola.

Si **algo se rompe en producción** (TR cambia algo, parser falla): #6 (telemetría) primero para entender qué se rompió, luego el fix.
