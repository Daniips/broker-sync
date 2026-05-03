# IRPF report (`make renta`)

Generates a complete report of the fiscal year with everything you need for the Spanish **Declaración de la Renta** (Modelo 100), plus orientative data for **Modelos 720/721**.

```bash
make renta              # current year − 1 (typical: in April 2026 you get 2025)
make renta YEAR=2024    # a specific year
```

The report is dumped to:
- **Console**: human-readable formatted output.
- **Google Sheet**: `Renta YYYY` tab (created/overwritten).

---

## ⚠️ Important disclaimer

The figures in the report are **orientative**. **Always verify them** against the official **"Jährlicher Steuerbericht YYYY"** PDF that TR sends you each year in April (you find it in your timeline). That document is what TR reports to Hacienda and is what prevails for tax purposes.

The author is not responsible for errors in your tax return derived from using this tool.

---

## Report structure

The report has **9 sections** generated in order:

### 1. Ganancias / pérdidas patrimoniales (FIFO)

For each **sale** made in the year:

- Matches the sale with its prior purchases applying **FIFO** (First In, First Out) **per ISIN** — the mandatory rule in Spain for fungible securities.
- Computes:
  - **Valor de transmisión** = net received (includes sales fees deducted).
  - **Valor de adquisición** = total cost of the matched shares (includes purchase fees).
  - **Ganancia / pérdida** = transmisión − adquisición.

Supports:
- ✅ Individual stocks (Apple, Tesla, Deutsche Telekom, etc.)
- ✅ ETFs (S&P 500, MSCI EM IMI, Digitalisation, etc.)
- ✅ **TR gifts** (`ETF-Geschenk`, `Verlosung`/lottery) — the tax cost is the market value at receipt.
- ✅ **Bonds** sold before maturity.

If a sale doesn't find enough purchase history, the report shows a `HISTÓRICO INCOMPLETO` warning. Read the FAQ below.

**Where to declare them**:
- **ETFs**: specific box for "fondos cotizados" (Hacienda **usually pre-fills** with TR data).
- **Individual stocks**: "transmisión de acciones admitidas a negociación" (Hacienda **does NOT** usually pre-fill — you add them yourself).

---

### 2. Dividendos (casilla 0029)

For each `SSP_CORPORATE_ACTION_CASH` with subtitle `Bardividende`, `Aktienprämiendividende`, or `Kapitalertrag`:

- **Bruto** (Bruttoertrag): amount before withholdings.
- **Retención extranjera** (Steuer): tax withheld at source (US typical 15%, Germany 25%+5.5%, etc.).
- **Neto**: what you actually received.

**Where to declare them**: casilla **0029** ("Dividendos y demás rendimientos por la participación en fondos propios de entidades").

> **Heads-up**: TR is a German broker. Hacienda pre-fills 0029 with what TR reports automatically, but **often falls short** (TR doesn't always report dividends from foreign stocks). Always compare the script's total with the figure on your borrador.

---

### 3. Intereses (casilla 0027)

Sum of `INTEREST_PAYOUT` / `INTEREST_PAYOUT_CREATED` events for the year. This includes the monthly interest on TR cash (the famous "Zinsen" at 2.5–3.5% APR).

**Where to declare them**: casilla **0027** ("Intereses de cuentas, depósitos y de activos financieros en general").

---

### 4. Rendimientos de bonos / otros activos financieros (casilla 0031)

For each **bond** with coupons and/or maturity in the year, groups by ISIN and computes the **real net yield**:

```
net_yield = coupons_received + maturity_amount − purchase_cost
```

**Where to declare them**: casilla **0031** ("Rendimientos procedentes de la transmisión, amortización o reembolso de otros activos financieros").

> **Note**: casilla **0030** is exclusively for **Letras del Tesoro español**. Foreign bonds (with prefix XS, DE, etc.) go to 0031.

---

### 5. Resumen por casilla

Compact table to cross-check against your Hacienda borrador:

```
Casilla 0027 (Intereses)            : 176.12 €
Casilla 0029 (Dividendos neto)      :   2.66 €
  · retención extranjera (ded.DII)  :   0.30 €
Casilla 0031 (bonos/otros act.fin.) :  33.59 €
Ganancias/pérdidas patrimoniales    : -23.29 €  (7 ventas)
```

**How to use it**: open your Renta borrador and compare box by box. Those that differ are the ones you'll need to correct/add manually.

---

### 6. Retención extranjera por país

Groups dividends by **country of origin** (first 2 characters of the ISIN: US, DE, FR, GB, etc.) and sums the withholding from each:

```
US: 5 dividendos, bruto=12.34€, retención=1.85€, neto=10.49€
DE: 1 dividendo, bruto=0.93€, retención=0.00€, neto=0.93€
```

**Where to declare**: the **deducción por doble imposición internacional** (casillas 588-589 of Modelo 100). Lets you recover part of the withholding you already paid abroad, up to the limit of Spanish IRPF on those dividends.

---

### 7. Saveback received

Sum of `SAVEBACK_AGGREGATE` for the year (the cents TR returns to you in shares when you pay with the card).

**Tax treatment**: debatable. TR **does NOT report it** to Hacienda. Some advisors declare it as rendimiento del capital mobiliario in kind (casilla 0029); others treat it as a "commercial discount" not subject to tax. The script lists it informationally for you to decide.

---

### 8. Posición cripto (current snapshot)

List of your crypto according to `crypto_isins` in `config.yaml`, with their current value in €.

**Modelo 721**: if the balance of crypto at a foreign provider (TR is German) **exceeds 50,000 €** at 31/12, there's a reporting obligation. The script's current snapshot is **NOT** at 31/12 but as of today, but it gives you an idea of whether you're approaching the threshold.

---

### 9. Saldo total TR — orientative Modelo 720

Sum of all positions in TR + cash in account (in €):

```
Posiciones (instrumentos):  3456.78 €
Cash EUR             :       234.56 €
TOTAL HOY (2026-04-25):     3691.34 €
```

**Modelo 720**: informative declaration of assets and rights abroad. Required if the total **exceeds 50,000 €** at 31/12 (or average balance of the last quarter). TR's Spanish IBAN does **NOT** exempt you — what counts is where the assets are custodied (Frankfurt, Germany).

> **Note**: the script's snapshot is from **today**, not 31/12. For the official figure use the Jährlicher Steuerbericht.

---

## Special cases

### ETF gifts (`ETF-Geschenk`)

TR sometimes gifts you fractions of ETFs. The script detects them as `GIFTING_RECIPIENT_ACTIVITY` and adds them as purchase lots with cost = market value at receipt (taken from the event's own JSON).

If a gift is mis-parsed and "unmatched shares" appear, add the data manually to `gift_cost_overrides` in `config.yaml`:

```yaml
gift_cost_overrides:
  LU1681048804:
    shares: 0.222311
    cost_eur: 25.00
```

The exact value is in the Jährlicher Steuerbericht PDF.

### TR lottery (`Verlosung`)

TR raffles free shares (typically Tesla, Apple…). The script detects them as `GIFTING_LOTTERY_PRIZE_ACTIVITY` and treats them like gifts: tax cost = market value at receipt.

### Foreign bonds with coupon

The script groups by ISIN the events `Kauforder` (purchase), `Zinszahlung` (coupon), and `Endgültige Fälligkeit` (maturity) and computes the real net yield:

```
ISIN XS0213101073  'Feb. 2025'
  2024-10-17  Orden de compra      -2000.99 €
  2025-02-24  Pago de cupón         +106.07 €
  2025-02-24  Vencimiento final    +1928.51 €
  → rendim. neto:    +33.59 €  (+1.68% sobre inversión)
```

### Stocks of companies that change ISIN (corporate actions)

When a company does a swap/wechsel/spinoff (events `SSP_CORPORATE_ACTION_ACTIVITY`), the ISIN can change. The script **doesn't handle** this automatically — it would show as "unmatched shares" on the subsequent sale.

Workaround: add a manual `gift_cost_overrides` with the original cost.

---

## FAQ

**Q: My script's figure doesn't match the Jährlicher Steuerbericht PDF.**
A: Look at whether the difference is in:
- Fees (TR sometimes charges them to acquisition cost and other times to transmission value, depending on country).
- Withholdings (some reported to Hacienda and others not).
- Currency differences in USD bonds.
- Events in `gift_cost_overrides` with manual data that differs from the PDF.

Always trust the PDF for filing the return. The script is a review tool.

**Q: What do I do if Hacienda pre-fills fewer dividends than TR has paid me?**
A: Common with foreign stock dividends. Add the difference yourself to casilla 0029 with the script's total. If you were withheld at source, also add the deducción por doble imposición (casillas 588-589).

**Q: I have several purchases of the same ISIN before selling. How do I declare the sale?**
A: FIFO already sums them for you. The form usually asks for "fecha de adquisición más antigua" → put the date of the first lot consumed and the aggregated total cost of all lots that were matched.

**Q: What if I sold only part of a position?**
A: The script's FIFO consumes the oldest shares to cover what you sold; the rest remain available for future sales. The reported cost is only that of the sold shares.

**Q: The sale says "X shares unmatched — buy history missing".**
A: The script didn't find purchases of that ISIN. Possible causes:
1. **Purchases prior to your timeline range**: if you've moved the portfolio from another broker, data is missing. Add overrides in `gift_cost_overrides` or document by hand on the return.
2. **Gift with unparseable data**: add the ISIN to `gift_cost_overrides` with shares and cost from the Steuerbericht.
3. **Corporate action**: the ISIN changed in the past. Same workaround.

**Q: Why does saveback have "controversial" treatment?**
A: Because the AEAT hasn't ruled clearly. There are two positions:
- **It's a commercial discount** (like a card cashback) → not declared.
- **It's rendimiento del capital mobiliario in kind** → declared in 0029.
Common practice is not to declare it, but keep records in case you're asked.

**Q: Does the script work for joint or non-resident returns?**
A: The script assumes individual taxation with Spanish tax residence (IRPF). For other cases, the concepts are the same but the boxes/forms change — adapt to your situation.

---

## Data the report does NOT process

Things the script **does not cover** and you'll have to add/check by hand:

- ❌ Accounts/investments outside TR (another broker, bank, crypto on exchange...)
- ❌ Offsetting capital losses from prior years (you can carry them forward up to 4 years; the script doesn't remember cross-years).
- ❌ Pension plan contributions, rentals, work income, etc.
- ❌ In-kind income (Saveback is only listed informationally).
- ❌ Crypto buys/sells made with TR (not yet supported; the script only gives a snapshot of current position).

---

## What the script DOES do well

- ✅ FIFO per ISIN, robust even with dozens of purchases and partial sales.
- ✅ Fees included in the calculation (already in the net `amount.value` returned by TR).
- ✅ Detects when an event is a gift, lottery, or bond and applies the correct tax logic.
- ✅ Automatic dedup: if the same trade appears in `TRADING_TRADE_EXECUTED` and `TRADE_INVOICE` (TR sometimes emits both), it only counts once.
- ✅ Support for the old TR JSON formats (`TRADE_INVOICE` with separate Transaktion section) and new (`TRADING_TRADE_EXECUTED` with prefix in displayValue).
- ✅ Automatic persistence to the `Renta YYYY` tab (you can consult it whenever you want without re-running).
