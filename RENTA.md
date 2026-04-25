# Informe IRPF (`make renta`)

Genera un informe completo del año fiscal con todo lo que necesitas para la **Declaración de la Renta** española (Modelo 100), más datos orientativos para los **Modelos 720/721**.

```bash
make renta              # año actual − 1 (típico: en abril 2026 te saca 2025)
make renta YEAR=2024    # un año concreto
```

El informe se vuelca a:
- **Consola**: salida formateada legible.
- **Google Sheet**: pestaña `Renta YYYY` (la crea/sobrescribe).

---

## ⚠️ Disclaimer importante

Las cifras del informe son **orientativas**. **Siempre verifícalas** contra el PDF oficial **"Jährlicher Steuerbericht YYYY"** que TR te envía cada año en abril (lo encuentras en tu timeline). Ese documento es el que TR reporta a Hacienda y es el que prevalece a efectos fiscales.

El autor no se hace responsable de errores en tu declaración derivados del uso de esta herramienta.

---

## Estructura del informe

El informe tiene **9 secciones** que se generan en orden:

### 1. Ganancias / pérdidas patrimoniales (FIFO)

Por cada **venta** realizada en el año:

- Casa la venta con sus compras anteriores aplicando **FIFO** (First In, First Out) **por ISIN** — la regla obligatoria en España para valores homogéneos.
- Calcula:
  - **Valor de transmisión** = neto recibido (incluye comisiones de venta descontadas).
  - **Valor de adquisición** = coste total de las shares matched (incluye comisiones de compra).
  - **Ganancia / pérdida** = transmisión − adquisición.

Soporta:
- ✅ Acciones individuales (Apple, Tesla, Deutsche Telekom, etc.)
- ✅ ETFs (S&P 500, MSCI EM IMI, Digitalisation, etc.)
- ✅ **Regalos de TR** (`ETF-Geschenk`, `Verlosung`/lotería) — el coste fiscal es el valor de mercado al recibirlos.
- ✅ **Bonos** vendidos antes de vencimiento.

Si una venta no encuentra suficiente histórico de compras, el informe muestra un aviso `HISTÓRICO INCOMPLETO`. Lee el FAQ más abajo.

**Dónde declararlas**:
- **ETFs**: casilla específica de "fondos cotizados" (Hacienda **suele preliquidarlos** con datos de TR).
- **Acciones individuales**: "transmisión de acciones admitidas a negociación" (Hacienda **NO** suele preliquidarlas — las añades tú).

---

### 2. Dividendos (casilla 0029)

Por cada `SSP_CORPORATE_ACTION_CASH` con subtitle `Bardividende`, `Aktienprämiendividende` o `Kapitalertrag`:

- **Bruto** (Bruttoertrag): importe antes de retenciones.
- **Retención extranjera** (Steuer): impuesto retenido en origen (USA típico 15%, Alemania 25%+5,5%, etc.).
- **Neto**: lo que efectivamente has cobrado.

**Dónde declararlos**: casilla **0029** ("Dividendos y demás rendimientos por la participación en fondos propios de entidades").

> **Ojo**: TR es un broker alemán. Hacienda prerellena la 0029 con lo que TR le reporta automáticamente, pero **muchas veces se queda corto** (TR no siempre reporta dividendos de acciones extranjeras). Compara siempre el total del script con la cifra de tu borrador.

---

### 3. Intereses (casilla 0027)

Suma de eventos `INTEREST_PAYOUT` / `INTEREST_PAYOUT_CREATED` del año. Esto incluye los intereses mensuales del cash de TR (los famosos "Zinsen" al 2,5–3,5% TAE).

**Dónde declararlos**: casilla **0027** ("Intereses de cuentas, depósitos y de activos financieros en general").

---

### 4. Rendimientos de bonos / otros activos financieros (casilla 0031)

Para cada **bono** con cupón y/o amortización en el año, agrupa por ISIN y calcula el **rendimiento neto real**:

```
rendimiento_neto = cupones_cobrados + importe_amortización − coste_de_compra
```

**Dónde declararlos**: casilla **0031** ("Rendimientos procedentes de la transmisión, amortización o reembolso de otros activos financieros").

> **Nota**: la casilla **0030** es exclusiva para **Letras del Tesoro español**. Bonos extranjeros (con prefijo XS, DE, etc.) van a 0031.

---

### 5. Resumen por casilla

Tabla compacta para contrastar contra tu borrador de Hacienda:

```
Casilla 0027 (Intereses)            : 176.12 €
Casilla 0029 (Dividendos neto)      :   2.66 €
  · retención extranjera (ded.DII)  :   0.30 €
Casilla 0031 (bonos/otros act.fin.) :  33.59 €
Ganancias/pérdidas patrimoniales    : -23.29 €  (7 ventas)
```

**Cómo usarlo**: abre tu borrador de la Renta y compara casilla por casilla. Las que difieran son las que tendrás que corregir/añadir manualmente.

---

### 6. Retenciones extranjeras por país

Agrupa los dividendos por **país de origen** (primeros 2 caracteres del ISIN: US, DE, FR, GB, etc.) y suma la retención de cada uno:

```
US: 5 dividendos, bruto=12.34€, retención=1.85€, neto=10.49€
DE: 1 dividendo, bruto=0.93€, retención=0.00€, neto=0.93€
```

**Dónde declarar**: la **deducción por doble imposición internacional** (casillas 588-589 del Modelo 100). Te permite recuperar parte de la retención que ya pagaste en el extranjero, hasta el límite del IRPF español sobre esos dividendos.

---

### 7. Saveback recibido

Suma de `SAVEBACK_AGGREGATE` del año (los céntimos que TR te devuelve en acciones cuando pagas con la tarjeta).

**Tratamiento fiscal**: discutible. TR **NO lo reporta** a Hacienda. Algunos asesores lo declaran como rendimiento del capital mobiliario en especie (casilla 0029); otros lo tratan como "descuento comercial" no sujeto. El script lo lista informativamente para que decidas tú.

---

### 8. Posición cripto (snapshot actual)

Listado de tus criptos según `crypto_isins` en `config.yaml`, con su valor actual en €.

**Modelo 721**: si el saldo de criptos en proveedor extranjero (TR es alemán) **supera 50.000 €** a 31/12, hay obligación informativa. El snapshot actual del script **NO** es a 31/12 sino a hoy, pero te da una idea de si te acercas al umbral.

---

### 9. Saldo TR — orientativo Modelo 720

Suma de todas las posiciones en TR + cash en cuenta (en €):

```
Posiciones (instrumentos):  3456.78 €
Cash EUR             :       234.56 €
TOTAL HOY (2026-04-25):     3691.34 €
```

**Modelo 720**: declaración informativa de bienes y derechos en el extranjero. Obligación si el total **supera 50.000 €** a 31/12 (o saldo medio del último trimestre). El IBAN español de TR **NO** te exime — lo que cuenta es dónde se custodian los activos (Frankfurt, Alemania).

> **Nota**: el snapshot del script es de **hoy**, no a 31/12. Para el dato oficial usa el Jährlicher Steuerbericht.

---

## Casos especiales

### Regalos de ETF (`ETF-Geschenk`)

TR a veces te regala fracciones de ETF. El script los detecta como `GIFTING_RECIPIENT_ACTIVITY` y los añade como lotes de compra con coste = valor de mercado al recibirlos (sacado del JSON del propio evento).

Si un regalo viene mal parseado y aparece "shares sin casar", añade el dato manualmente en `gift_cost_overrides` de `config.yaml`:

```yaml
gift_cost_overrides:
  LU1681048804:
    shares: 0.222311
    cost_eur: 25.00
```

El valor exacto está en el PDF Jährlicher Steuerbericht.

### Lotería de TR (`Verlosung`)

TR sortea acciones gratis (típicamente Tesla, Apple…). El script los detecta como `GIFTING_LOTTERY_PRIZE_ACTIVITY` y los trata igual que los regalos: coste fiscal = valor de mercado al recibirlos.

### Bonos extranjeros con cupón

El script agrupa por ISIN los eventos `Kauforder` (compra), `Zinszahlung` (cupón) y `Endgültige Fälligkeit` (amortización al vencimiento) y calcula el rendimiento neto real:

```
ISIN XS0213101073  'Feb. 2025'
  2024-10-17  Orden de compra      -2000.99 €
  2025-02-24  Pago de cupón         +106.07 €
  2025-02-24  Vencimiento final    +1928.51 €
  → rendim. neto:    +33.59 €  (+1.68% sobre inversión)
```

### Acciones de empresas que cambian de ISIN (corporate actions)

Cuando una empresa hace un swap/wechsel/spinoff (eventos `SSP_CORPORATE_ACTION_ACTIVITY`), el ISIN puede cambiar. El script **no maneja** esto automáticamente — quedaría como "shares sin casar" en la venta posterior.

Workaround: añade un `gift_cost_overrides` manual con el coste original.

---

## FAQ

**P: La cifra de mi script no coincide con el PDF Jährlicher Steuerbericht.**
R: Mira si la diferencia está en:
- Comisiones (TR a veces las imputa al coste de adquisición y otras al valor de transmisión, según país).
- Retenciones (algunas reportadas a Hacienda y otras no).
- Diferencias de cambio en bonos en USD.
- Eventos en `gift_cost_overrides` con datos manuales que difieren del PDF.

Confía siempre en el PDF para presentar la declaración. El script es una herramienta de revisión.

**P: ¿Qué hago si Hacienda preliquida menos dividendos de los que me ha pagado TR?**
R: Es habitual con dividendos de acciones extranjeras. Añade tú la diferencia en la casilla 0029 con el total del script. Si te retuvieron impuestos en origen, suma también la deducción por doble imposición (casillas 588-589).

**P: Tengo varias compras del mismo ISIN antes de vender. ¿Cómo declaro la venta?**
R: El FIFO ya las suma por ti. En el formulario suelen pedir "fecha de adquisición más antigua" → pon la del primer lote consumido y el coste total agregado de todos los lotes que se han casado.

**P: ¿Y si vendí solo una parte de una posición?**
R: El FIFO del script consume las shares más antiguas hasta cubrir las que vendiste; las restantes quedan disponibles para futuras ventas. El coste reportado es solo el de las shares vendidas.

**P: La venta dice "X shares sin casar — falta histórico de compras".**
R: El script no encontró las compras de ese ISIN. Posibles causas:
1. **Compras anteriores a tu rango de timeline**: si has movido la cartera de otro broker, faltan datos. Añade overrides en `gift_cost_overrides` o documenta a mano en la declaración.
2. **Regalo con datos no parseables**: añade el ISIN a `gift_cost_overrides` con shares y coste del Steuerbericht.
3. **Corporate action**: el ISIN cambió en el pasado. Mismo workaround.

**P: ¿Por qué el saveback tiene tratamiento "controvertido"?**
R: Porque la AEAT no se ha pronunciado claramente. Hay dos posturas:
- **Es un descuento comercial** (como un cashback de tarjeta) → no se declara.
- **Es un rendimiento del capital mobiliario en especie** → se declara en 0029.
La práctica habitual es no declararlo, pero conserva los registros por si te preguntan.

**P: ¿El script vale para declaraciones conjuntas o de no residentes?**
R: El script asume tributación individual con residencia fiscal en España (IRPF). Para otros casos, los conceptos son los mismos pero las casillas/modelos cambian — adapta a tu situación.

---

## Datos que NO procesa el informe

Cosas que el script **no contempla** y tendrás que añadir/comprobar a mano:

- ❌ Cuentas/inversiones fuera de TR (otro broker, banco, cripto en exchange...)
- ❌ Compensación de pérdidas patrimoniales de años anteriores (puedes arrastrarlas hasta 4 años; el script no recuerda años cruzados).
- ❌ Aportaciones a planes de pensiones, alquileres, rendimientos del trabajo, etc.
- ❌ Rentas en especie (Saveback solo lo lista informativamente).
- ❌ Compras/ventas de cripto realizadas con TR (no soportadas todavía; el script solo da snapshot de posición actual).

---

## Lo que el script SÍ hace bien

- ✅ FIFO por ISIN, robusto incluso con docenas de compras y ventas parciales.
- ✅ Comisiones incluidas en el cálculo (ya están en el `amount.value` neto que devuelve TR).
- ✅ Detecta cuándo un evento es regalo, lotería o bono y aplica la lógica fiscal correcta.
- ✅ Dedup automático: si el mismo trade aparece en `TRADING_TRADE_EXECUTED` y en `TRADE_INVOICE` (TR a veces emite ambos), solo cuenta una vez.
- ✅ Soporte de los formatos del JSON de TR antiguo (`TRADE_INVOICE` con sección Transaktion separada) y nuevo (`TRADING_TRADE_EXECUTED` con prefijo en displayValue).
- ✅ Persistencia automática en la pestaña `Renta YYYY` (puedes consultarla cuando quieras sin re-ejecutar).
