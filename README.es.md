# tr-sync

> 🇬🇧 [English version](README.md) · 🇪🇸 estás leyendo el español.

Sincroniza eventos de **Trade Republic** (gastos con tarjeta, ingresos, savings plan, dividendos, intereses, etc.) con un **Google Sheet** personal de finanzas. Incluye además un informe **IRPF (Renta)** automatizado: FIFO de ganancias/pérdidas patrimoniales, dividendos, intereses, rendimientos de bonos, retenciones extranjeras por país, posición cripto y saldo total para Modelo 720/721.

> Desarrollado para uso personal. Hecho público bajo MIT para que cualquiera pueda adaptarlo a su Sheet — los datos sensibles (Sheet ID, ISINs, mapeos) viven en `config.yaml`, que está en `.gitignore`.

---

## Tabla de contenidos

- [Qué hace](#qué-hace)
- [Requisitos](#requisitos)
- [Instalación rápida](#instalación-rápida)
- [Configuración](#configuración)
- [Uso](#uso)
- [Comandos `make` disponibles](#comandos-make-disponibles)
- [Ignorar eventos](#ignorar-eventos)
- [Informe IRPF (`make renta`)](#informe-irpf-make-renta)
- [Automatización con GitHub Actions](#automatización-con-github-actions-opcional)
- [Inspección de eventos brutos (debug)](#inspección-de-eventos-brutos-debug)
- [Estructura del repo](#estructura-del-repo)
- [FAQ y troubleshooting](#faq-y-troubleshooting)
- [Licencia y disclaimer](#licencia-y-disclaimer)

---

## Qué hace

- **Sync mensual de gastos / ingresos / inversiones** a tu Google Sheet, organizando los eventos del último mes en pestañas tipo "Gastos", "Ingresos" y "Dinero invertido <año>". Detecta el bloque resumen al final de cada mes y **inserta nuevas filas justo encima** sin pisarlo.
- **Snapshot de portfolio** que escribe el valor actual de cada activo en un rango configurable de la pestaña "Calculo ganancias".
- **Informe IRPF completo** del año pasado (o el que indiques): FIFO automático para ganancias/pérdidas patrimoniales, dividendos con retenciones por país (deducción doble imposición), intereses, rendimiento neto de bonos extranjeros, posición cripto (informativo Modelo 721) y saldo total TR (orientativo Modelo 720). Lo escribe en una pestaña "Renta YYYY".
- **Filtros configurables** para ignorar eventos que ya gestionas a mano (p.ej. la nómina que recibes en otra cuenta y luego transfieres a TR — para que no se duplique).
- **Deduplicación automática** de eventos ya sincronizados (mediante una pestaña oculta `_sync_state`).
- **Modo `--dry-run`** para verificar antes de escribir.

---

## Requisitos

- **Python 3.11+**
- Cuenta de Trade Republic
- Un proyecto de Google Cloud Console con la **Google Sheets API** activada y unas credenciales OAuth descargadas.
- (Opcional) Cuenta de GitHub si quieres automatizar la sync con Actions.

---

## Instalación rápida

```bash
git clone https://github.com/<tu_usuario>/tr-sync.git
cd tr-sync
make setup
make config-init     # asistente interactivo: te pregunta paso a paso (sheet_id, layout, ISINs...)
make login           # primera vez: SMS de verificación
make init-sheet      # crea las pestañas en tu Google Sheet
make doctor          # verifica que todo el setup está listo
make verify          # dry-run de portfolio para confirmar OAuth
```

> Si prefieres editar el YAML a mano: `cp config.example.yaml config.yaml` y rellénalo siguiendo [CONFIG.md](CONFIG.md).

Si todo ha ido bien, en `make verify` verás los netValue de tus activos pero nada se ha escrito en la Sheet aún.

---

## Configuración

Hay dos ficheros relevantes:

- **`config.yaml`** — tu config personal (gitignored). Es el único fichero que **debes editar**. La referencia completa de cada campo está en [CONFIG.md](CONFIG.md).
- **`Makefile.local`** — opcional, gitignored. Sirve para fijar variables específicas de tu entorno (p.ej. `REPO := tu_usuario/tu_repo` para los targets de GitHub Actions).

### Estructura del Sheet

El script asume una estructura concreta de pestañas y columnas. Lee [SHEET_TEMPLATE.md](SHEET_TEMPLATE.md) **antes** de lanzar `make sync` por primera vez. Si tu Sheet tiene otra organización, ajusta `config.yaml` (nombres de pestañas, markers, rangos) o adapta el código.

### Google Sheets OAuth (primera vez)

1. Ve a [Google Cloud Console](https://console.cloud.google.com/), crea un proyecto y activa la **Google Sheets API**.
2. Crea credenciales **OAuth client ID** de tipo **Desktop app**.
3. Descarga el JSON y guárdalo como `~/.config/gspread/credentials.json`.
4. La primera vez que el script abra la Sheet, gspread abrirá un navegador; autoriza con la cuenta Google donde está tu Sheet. El token se guarda en `~/.config/gspread/authorized_user.json`.

### Trade Republic — login

```bash
make login
```

Pide número de teléfono, PIN y SMS. La sesión queda en `~/.pytr/`. Mientras la cookie no caduque (~2 días si haces login normal, hasta 1 mes si usas `--store_credentials`) no necesitarás repetir el SMS.

---

## Uso

```bash
make sync         # sync mensual gastos / ingresos / inversiones
make portfolio    # snapshot del portfolio
make verify       # portfolio dry-run (no escribe en la Sheet)
make renta        # informe IRPF del año pasado
make renta YEAR=2024   # informe IRPF de un año concreto
make inspect      # debug de eventos brutos
make test         # tests unitarios
```

Si la cookie de TR caduca, repite `make login`.

---

## Comandos `make` disponibles

### Setup

| Comando | Qué hace |
|---|---|
| `make setup` | Crea `.venv`, instala dependencias |
| `make login` | Login en TR (SMS si toca) |
| `make config-init` | Asistente interactivo para crear `config.yaml` (recomendado para usuarios nuevos) |
| `make init-sheet` | Crea en tu Google Sheet las pestañas que falten (idempotente) |
| `make doctor` | Health check: verifica que todo el setup está listo antes de sincronizar |

### Uso diario

| Comando | Qué hace |
|---|---|
| `make sync` | Sincroniza gastos/ingresos/inversiones del último mes |
| `make portfolio` | Snapshot del portfolio (valor actual por activo) |
| `make verify` | Portfolio dry-run local |
| `make renta` | Informe IRPF del año pasado |
| `make renta YEAR=N` | Informe IRPF de un año concreto |
| `make inspect` | Inspeccionar eventos brutos de TR (debug) |
| `make test` | Tests unitarios (sin red, deterministas) |

### GitHub Actions (opcional)

Estos targets requieren `gh` CLI autenticado y `REPO=usuario/repo` (lo más cómodo: define `REPO := usuario/repo` en `Makefile.local`).

| Comando | Qué hace |
|---|---|
| `make all` | login → sync → portfolio → upload-secret → clear-cache |
| `make refresh-cookie` | login → verify → upload-secret → clear-cache |
| `make upload-secret` | Empaqueta `~/.pytr/` y sube como secret `PYTR_KEYS_B64` |
| `make clear-cache` | Borra caches `pytr-session-*` de Actions |

---

## CLI de configuración (sin tocar YAML)

Hay un subcomando `config` que permite gestionar `config.yaml` por completo desde la terminal, sin abrir el fichero a mano.

### Comandos disponibles

| Comando | Qué hace |
|---|---|
| `make config-init` | Wizard interactivo paso a paso (primer setup) |
| `make config-show` | Imprime el `config.yaml` actual |
| `make config-validate` | Valida el config y reporta errores con mensajes claros |
| `make config-features` | Wizard de checkboxes para activar/desactivar features |
| `python tr_sync.py config set KEY VALUE` | Cambia un valor concreto. Acepta dot-notation: `set sheets.expenses Gastos` |
| `python tr_sync.py config add-asset ISIN LABEL` | Añade entrada a `portfolio_cell_map` |
| `python tr_sync.py config remove-asset ISIN` | Quita una entrada |
| `python tr_sync.py config add-ignore SECTION TEXT` | Añade patrón a `ignore_events.{income,expenses}.title_contains` |
| `python tr_sync.py config remove-ignore SECTION TEXT` | Quita un patrón |

### Notas

- El wizard `config init` te pregunta sheet_id, features, layouts, pestañas, portfolio (ISINs+labels), `asset_name_map`, cripto, timezone, etc. Tarda <2 minutos.
- Los comandos `set` / `add-*` regeneran el YAML al guardar y **pierden los comentarios** del fichero. Si quieres preservarlos, edita a mano.
- Todos los subcomandos `config` arrancan **sin requerir un `config.yaml` previo** ni `pytr`/`gspread` cargados — ideales para el primer setup.

---

## Ignorar eventos

¿Tienes ingresos o gastos que **ya gestionas a mano** en la Sheet y no quieres que el sync los duplique? P.ej. la nómina que cobras en otra cuenta y luego mueves a TR, o reembolsos personales entre tus cuentas.

En `config.yaml`:

```yaml
ignore_events:
  income:
    title_contains:
      - "tu nombre apellido"   # autotransferencias entrantes
      - "imagin"                # ingresos de imagin
    subtitle_contains: []
  expenses:
    title_contains: []
    subtitle_contains: []
```

Match **case-insensitive** y por **substring**, tanto sobre `title` como sobre `subtitle` del evento. Si un evento entrante matchea, se descarta del sync y se loggea con detalle:

```
[Ingresos] 1 evento(s) ignorado(s) por config.yaml → ignore_events:
   - 2026-04-30   1850.00 €  'Tu Nombre Apellido'
```

Si no estás seguro de qué string usar, lánzalo:
```bash
.venv/bin/python inspect_events.py --eventtype BANK_TRANSACTION_INCOMING
```

Te dumpeará todos los ingresos brutos con su `title`/`subtitle` y eliges el patrón que quieras filtrar.

---

## Informe IRPF (`make renta`)

Lee la guía detallada en **[RENTA.md](RENTA.md)**. Resumen rápido:

- 9 secciones: G/P patrimoniales (FIFO), dividendos, intereses, bonos, resumen por casilla, retenciones por país, saveback, posición cripto, saldo total Modelo 720.
- Soporta acciones, ETFs, regalos (`ETF-Geschenk`), lotería (`Verlosung`), bonos extranjeros con cupón + amortización.
- Volcado simultáneo a consola y a una pestaña `Renta YYYY` de la Sheet.
- Importante: **siempre verifica los números** contra el PDF "Jährlicher Steuerbericht YYYY" oficial de TR antes de presentar la declaración.

```bash
make renta            # año actual − 1
make renta YEAR=2024  # otro año
```

---

## Automatización con GitHub Actions (opcional)

El repo trae `.github/workflows/sync.yml` que puede sincronizar a diario sin tu intervención. Para usarlo:

1. Sube este repo a GitHub (puede ser privado).
2. En **Settings → Secrets**, añade dos secrets:
   - **`PYTR_KEYS_B64`**: contenido de `~/.pytr/` empaquetado y en base64. Se sube/refresca con `make upload-secret`.
   - **`GSPREAD_AUTH_B64`**: contenido de `~/.config/gspread/` empaquetado y en base64. Se sube manualmente (una vez):
     ```bash
     tar -czf /tmp/gspread.tgz -C $HOME .config/gspread/
     gh secret set GSPREAD_AUTH_B64 --repo $REPO --body "$(base64 -i /tmp/gspread.tgz)"
     ```
3. Define `REPO := tu_usuario/tu_repo` en `Makefile.local`.
4. Lanza el workflow desde la pestaña Actions o programa un cron en `sync.yml`.

> Cuando la cookie de TR caduque, el workflow fallará y abrirá un issue en tu repo. Bastará con que ejecutes `make refresh-cookie` localmente para subir una nueva.

---

## Inspección de eventos brutos (debug)

`inspect_events.py` ayuda a explorar la API de TR cuando algo raro pasa o cuando quieres entender qué te envía:

```bash
.venv/bin/python inspect_events.py                              # resumen + ventas año pasado
.venv/bin/python inspect_events.py --year 2024                  # otro año
.venv/bin/python inspect_events.py --raw                        # JSON de la 1ª venta del año
.venv/bin/python inspect_events.py --isin US0378331005          # eventos de un ISIN concreto
.venv/bin/python inspect_events.py --eventtype INTEREST_PAYOUT  # JSONs de un eventType
.venv/bin/python inspect_events.py --title "Feb. 2025"          # buscar por título
```

---

## Estructura del repo

```
tr_sync.py             — script principal (sync + portfolio + renta)
inspect_events.py      — utilidad de inspección de eventos brutos
test_tr_sync.py        — tests unitarios (sin red, deterministas)
config.example.yaml    — plantilla de configuración (commiteable)
config.yaml            — TU config personal (gitignored)
Makefile               — atajos de make
Makefile.local         — variables locales (gitignored, opcional)
README.md              — este fichero
CONFIG.md              — referencia de cada campo de config.yaml
SHEET_TEMPLATE.md      — estructura esperada del Google Sheet
RENTA.md               — guía detallada del informe IRPF
LICENSE                — MIT
.github/workflows/     — workflow opcional para GitHub Actions
```

---

## FAQ y troubleshooting

**P: La cookie de TR ha caducado.**
R: `make login` (te pedirá SMS) y, si usas Actions, `make upload-secret` después.

**P: El sync no encuentra una pestaña.**
R: Comprueba que el nombre exacto en `config.yaml > sheets` coincide con el de tu pestaña en el Sheet (mayúsculas/minúsculas, acentos, espacios).

**P: La nómina se sigue colando aunque tengo `ignore_events`.**
R: El patrón debe matchear el `title` o `subtitle` real de TR, no el alias que tú le pones. Lanza `inspect_events.py --eventtype BANK_TRANSACTION_INCOMING` para ver el title exacto y ajusta.

**P: `make renta` dice "X shares sin casar — falta histórico de compras".**
R: El parser no encuentra las compras anteriores de ese ISIN. Causas habituales: regalo/lotería sin metadatos parseables (rellena `gift_cost_overrides` en `config.yaml` con el dato del Jährlicher Steuerbericht) o un evento de compra antigua con estructura no soportada (revisa con `inspect_events.py --isin <ISIN>`).

**P: ¿Por qué el script muestra subtitles en alemán?**
R: La API de TR responde siempre en alemán al script (no respeta el idioma de la app). Hay un diccionario `SUBTITLE_ES` en `tr_sync.py` que traduce los más habituales para mostrarlos en castellano. Si aparece alguno sin traducir, añádelo al diccionario.

**P: ¿Es seguro publicar mi `config.yaml`?**
R: No. Está en `.gitignore` por una razón. Contiene tu Sheet ID (que sin acceso a la cuenta de Google está bloqueado, pero mejor no exponerlo) y la lista de ISINs/activos que tienes — información no crítica pero personal.

**P: ¿Y el Sheet ID en el git history viejo?**
R: Si te incomoda, reescribe historia con `git filter-repo --replace-text`. El ID por sí solo no da acceso a nada — Google requiere que la cuenta del visitante tenga permisos.

---

## Licencia y disclaimer

MIT — ver [LICENSE](LICENSE).

> Este software se proporciona tal cual, sin garantías. Las cifras del informe IRPF son **orientativas**: siempre verifícalas contra el Jährlicher Steuerbericht oficial de TR antes de presentar tu declaración. El autor no se hace responsable de errores fiscales derivados del uso de esta herramienta.
