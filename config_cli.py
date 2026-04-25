"""CLI interactiva para gestionar config.yaml sin tocar código.

Usos:
    python tr_sync.py config init                   # wizard inicial
    python tr_sync.py config show                   # imprime config.yaml
    python tr_sync.py config validate               # comprueba el config
    python tr_sync.py config set KEY VALUE          # cambia un valor (dot-notation)
    python tr_sync.py config add-asset ISIN LABEL   # añade ISIN al portfolio
    python tr_sync.py config remove-asset ISIN      # quita ISIN del portfolio
    python tr_sync.py config add-ignore SECTION TXT # añade patrón a ignore_events[SECTION]
    python tr_sync.py config remove-ignore SECTION TXT
    python tr_sync.py config features               # toggles de features
"""
from __future__ import annotations

import sys
from pathlib import Path

import questionary
import yaml

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"
EXAMPLE_PATH = Path(__file__).resolve().parent / "config.example.yaml"

# ── Helpers ───────────────────────────────────────────────────────────────


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_yaml(path: Path, data: dict, header: str | None = None) -> None:
    """Escribe `data` a `path` en YAML legible. `header` opcional como comentario al inicio."""
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
    if header:
        text = f"{header.rstrip()}\n\n{text}"
    path.write_text(text, encoding="utf-8")


def _set_nested(d: dict, dotted_key: str, value):
    """Setea d['a']['b']['c'] = value para dotted_key='a.b.c'. Crea dicts intermedios."""
    parts = dotted_key.split(".")
    cur = d
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def _get_nested(d: dict, dotted_key: str, default=None):
    parts = dotted_key.split(".")
    cur = d
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def _coerce_value(s: str):
    """Convierte el string del CLI al tipo razonable: bool/int/float/str. None y null → None."""
    if s.lower() in ("null", "none", "~"):
        return None
    if s.lower() in ("true", "yes"):
        return True
    if s.lower() in ("false", "no"):
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _confirm_overwrite(path: Path) -> bool:
    if not path.exists():
        return True
    return questionary.confirm(
        f"{path.name} ya existe. ¿Sobrescribir?",
        default=False,
    ).ask() or False


# ── Wizard `init` ─────────────────────────────────────────────────────────


_HEADER = """\
# tr-sync — config personal generado por `tr-sync config init`.
# NO subir al repo público (está en .gitignore).
# Para una plantilla limpia con todos los campos documentados, mira config.example.yaml.
"""


def cmd_init(args):
    """Wizard interactivo. Pregunta paso a paso y escribe config.yaml."""
    print("\n🛠  Asistente de configuración de tr-sync\n")

    if not _confirm_overwrite(CONFIG_PATH):
        print("Abortado.")
        return 1

    cfg: dict = {}

    # ── Sheet ID ──
    sheet_id = questionary.text(
        "ID de tu Google Sheet (de la URL: docs.google.com/spreadsheets/d/<ESTE_ID>/edit):",
        validate=lambda v: True if v and not v.startswith("REEMPLAZA") and not v.startswith("REPLACE") else "Pega el ID real",
    ).ask()
    if sheet_id is None:
        return 1
    cfg["sheet_id"] = sheet_id.strip()

    # ── Features (toggles) ──
    features = questionary.checkbox(
        "Qué partes de tu Sheet vas a sincronizar (espacio para marcar/desmarcar):",
        choices=[
            questionary.Choice("Gastos", value="expenses", checked=True),
            questionary.Choice("Ingresos", value="income", checked=True),
            questionary.Choice("Inversiones (Dinero invertido YYYY)", value="investments", checked=True),
            questionary.Choice("Portfolio (Calculo ganancias)", value="portfolio", checked=True),
        ],
    ).ask()
    if features is None:
        return 1
    cfg["features"] = {k: (k in features) for k in ("expenses", "income", "investments", "portfolio")}

    # ── Pestañas ──
    cfg["sheets"] = {}
    if cfg["features"]["expenses"]:
        cfg["sheets"]["expenses"] = questionary.text(
            "Nombre de la pestaña de gastos:",
            default="Gastos",
        ).ask() or "Gastos"
        cfg["sheets"]["expenses_layout"] = questionary.select(
            "Layout de la pestaña de gastos:",
            choices=[
                questionary.Choice("monthly_columns — meses como pares de columnas (Concepto+Importe)", value="monthly_columns"),
                questionary.Choice("ledger — una fila por evento con Fecha/Concepto/Importe", value="ledger"),
            ],
            default="monthly_columns",
        ).ask() or "monthly_columns"
    if cfg["features"]["income"]:
        cfg["sheets"]["income"] = questionary.text(
            "Nombre de la pestaña de ingresos:",
            default="Ingresos",
        ).ask() or "Ingresos"
        cfg["sheets"]["income_layout"] = questionary.select(
            "Layout de la pestaña de ingresos:",
            choices=[
                questionary.Choice("monthly_columns", value="monthly_columns"),
                questionary.Choice("ledger", value="ledger"),
            ],
            default="monthly_columns",
        ).ask() or "monthly_columns"

    if cfg["features"]["investments"]:
        cfg["sheets"]["investments_year_format"] = questionary.text(
            "Patrón del nombre de la pestaña de inversiones (debe contener {year}):",
            default="Dinero invertido {year}",
            validate=lambda v: True if "{year}" in v else "Debe contener {year}",
        ).ask() or "Dinero invertido {year}"

    if cfg["features"]["portfolio"]:
        cfg["sheets"]["portfolio"] = questionary.text(
            "Nombre de la pestaña del portfolio:",
            default="Calculo ganancias",
        ).ask() or "Calculo ganancias"

    cfg["sheets"]["status"] = questionary.text(
        "Nombre de la pestaña de estado de sync (la creará el script):",
        default="Estado sync",
    ).ask() or "Estado sync"
    cfg["sheets"]["sync_state"] = questionary.text(
        "Nombre de la pestaña oculta de dedup interno:",
        default="_sync_state",
    ).ask() or "_sync_state"

    # ── Markers para layout monthly_columns ──
    if (cfg["sheets"].get("expenses_layout") == "monthly_columns"
            or cfg["sheets"].get("income_layout") == "monthly_columns"):
        print("\nMarkers que el script busca al final de cada mes para detectar el bloque resumen.")
        if cfg["sheets"].get("expenses_layout") == "monthly_columns":
            markers_e = questionary.text(
                "Markers de Gastos (separados por comas):",
                default="gastos innecesarios, gastos totales, extraordinarios",
            ).ask() or ""
            cfg.setdefault("summary_markers", {})["expenses"] = [
                m.strip() for m in markers_e.split(",") if m.strip()
            ]
        if cfg["sheets"].get("income_layout") == "monthly_columns":
            markers_i = questionary.text(
                "Markers de Ingresos (separados por comas):",
                default="ingresos totales, ingresos, totales",
            ).ask() or ""
            cfg.setdefault("summary_markers", {})["income"] = [
                m.strip() for m in markers_i.split(",") if m.strip()
            ]

    # ── Portfolio: ISIN/label/range ──
    if cfg["features"]["portfolio"]:
        print("\nVamos a definir el mapeo ISIN → fila de la pestaña Portfolio.")
        cfg["portfolio_value_range"] = questionary.text(
            "Rango A1 donde escribir los valores actuales (ej. C2:C8):",
            default="C2:C8",
            validate=lambda v: True if ":" in v else "Debe ser un rango A1 tipo C2:C8",
        ).ask() or "C2:C8"

        n = questionary.text(
            "¿Cuántos activos vas a seguir en el portfolio?",
            default="7",
            validate=lambda v: v.isdigit() and int(v) > 0 or "Número entero positivo",
        ).ask() or "7"
        n = int(n)

        cfg["portfolio_cell_map"] = []
        for i in range(n):
            isin = questionary.text(f"  Activo {i+1} — ISIN:").ask()
            if not isin:
                break
            label = questionary.text(f"  Activo {i+1} — Etiqueta corta:").ask()
            cfg["portfolio_cell_map"].append({"isin": isin.strip(), "label": (label or isin).strip()})

    # ── Asset name map ──
    if cfg["features"]["investments"]:
        print("\nMapeo de nombres de TR a tu pestaña Inversiones.")
        print("Ej: 'Core S&P 500 USD (Acc)' → 'SP 500'. Pulsa Enter en ISIN vacío para acabar.")
        amap: dict = {}
        while True:
            tr_name = questionary.text("  Nombre que usa TR (vacío para terminar):").ask()
            if not tr_name:
                break
            display = questionary.text("  Nombre que tienes en tu pestaña:").ask()
            if display:
                amap[tr_name.strip()] = display.strip()
        if amap:
            cfg["asset_name_map"] = amap

    # ── Cripto (opcional) ──
    crypto = questionary.text(
        "ISINs cripto en tu cartera (separados por comas, vacío si ninguno):",
        default="",
    ).ask() or ""
    crypto_list = [c.strip() for c in crypto.split(",") if c.strip()]
    if crypto_list:
        cfg["crypto_isins"] = crypto_list

    # ── Timezone y buffer ──
    cfg["timezone"] = questionary.text("Zona horaria:", default="Europe/Madrid").ask() or "Europe/Madrid"
    cfg["default_buffer_days"] = int(questionary.text(
        "Días de buffer al descargar eventos del mes:",
        default="7",
        validate=lambda v: v.isdigit() and int(v) >= 0 or "Entero >= 0",
    ).ask() or "7")

    # ── Confirmación ──
    print("\n📄 Resumen del config:")
    print(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
    if not questionary.confirm("¿Guardar este config en config.yaml?", default=True).ask():
        print("Abortado, no se ha guardado nada.")
        return 1

    _save_yaml(CONFIG_PATH, cfg, header=_HEADER)
    print(f"\n✅ {CONFIG_PATH.name} guardado. Lanza `make doctor` para verificar.")
    return 0


# ── show / validate ──────────────────────────────────────────────────────


def cmd_show(args):
    if not CONFIG_PATH.exists():
        print(f"❌ {CONFIG_PATH.name} no existe. Lanza `tr-sync config init` para crearlo.")
        return 1
    print(CONFIG_PATH.read_text(encoding="utf-8"))
    return 0


def cmd_validate(args):
    """Carga el config y ejecuta la validación que hace tr_sync al arrancar."""
    if not CONFIG_PATH.exists():
        print(f"❌ {CONFIG_PATH.name} no existe.")
        return 1
    try:
        # Importar tr_sync ya carga y valida el config; si llega aquí, OK.
        import importlib
        import tr_sync as _tr
        importlib.reload(_tr)
        print(f"✅ {CONFIG_PATH.name} es válido.")
        return 0
    except Exception as e:
        print(f"❌ Error en config.yaml:\n{e}")
        return 1


# ── set ──────────────────────────────────────────────────────────────────


def cmd_set(args):
    """Cambia un valor del config con dot-notation: `config set sheets.expenses Gastos`."""
    if not CONFIG_PATH.exists():
        print(f"❌ {CONFIG_PATH.name} no existe. Lanza `config init` primero.")
        return 1
    cfg = _load_yaml(CONFIG_PATH)
    value = _coerce_value(args.value)
    _set_nested(cfg, args.key, value)
    _save_yaml(CONFIG_PATH, cfg, header=_HEADER)
    print(f"✅ {args.key} = {value!r}")
    print("   Nota: los comentarios del fichero se han perdido al regenerar el YAML.")
    return 0


# ── portfolio: add-asset / remove-asset ──────────────────────────────────


def cmd_add_asset(args):
    cfg = _load_yaml(CONFIG_PATH)
    pcm = cfg.setdefault("portfolio_cell_map", [])
    if any(e.get("isin") == args.isin for e in pcm):
        print(f"⚠  {args.isin} ya está en portfolio_cell_map.")
        return 1
    pcm.append({"isin": args.isin, "label": args.label})
    _save_yaml(CONFIG_PATH, cfg, header=_HEADER)
    print(f"✅ Añadido {args.isin} ({args.label}). portfolio_cell_map ahora tiene {len(pcm)} entradas.")
    print(f"   Recuerda actualizar `portfolio_value_range` si era una columna fija.")
    return 0


def cmd_remove_asset(args):
    cfg = _load_yaml(CONFIG_PATH)
    pcm = cfg.get("portfolio_cell_map", [])
    new_pcm = [e for e in pcm if e.get("isin") != args.isin]
    if len(new_pcm) == len(pcm):
        print(f"⚠  {args.isin} no estaba en portfolio_cell_map.")
        return 1
    cfg["portfolio_cell_map"] = new_pcm
    _save_yaml(CONFIG_PATH, cfg, header=_HEADER)
    print(f"✅ Quitado {args.isin}. portfolio_cell_map ahora tiene {len(new_pcm)} entradas.")
    return 0


# ── ignore_events: add / remove ──────────────────────────────────────────


def cmd_add_ignore(args):
    if args.section not in ("income", "expenses"):
        print("❌ section debe ser 'income' o 'expenses'.")
        return 1
    cfg = _load_yaml(CONFIG_PATH)
    ignore = cfg.setdefault("ignore_events", {}).setdefault(args.section, {})
    titles = ignore.setdefault("title_contains", [])
    if args.text in titles:
        print(f"⚠  '{args.text}' ya está en ignore_events.{args.section}.title_contains.")
        return 1
    titles.append(args.text)
    _save_yaml(CONFIG_PATH, cfg, header=_HEADER)
    print(f"✅ Añadido '{args.text}' a ignore_events.{args.section}.title_contains.")
    return 0


def cmd_remove_ignore(args):
    cfg = _load_yaml(CONFIG_PATH)
    titles = (cfg.get("ignore_events") or {}).get(args.section, {}).get("title_contains", [])
    if args.text not in titles:
        print(f"⚠  '{args.text}' no estaba en ignore_events.{args.section}.title_contains.")
        return 1
    titles.remove(args.text)
    _save_yaml(CONFIG_PATH, cfg, header=_HEADER)
    print(f"✅ Quitado '{args.text}'.")
    return 0


# ── features (toggles) ───────────────────────────────────────────────────


def cmd_features(args):
    cfg = _load_yaml(CONFIG_PATH)
    current = cfg.get("features") or {"expenses": True, "income": True, "investments": True, "portfolio": True}
    selected = questionary.checkbox(
        "Qué features quieres activar:",
        choices=[
            questionary.Choice("expenses", value="expenses", checked=current.get("expenses", True)),
            questionary.Choice("income", value="income", checked=current.get("income", True)),
            questionary.Choice("investments", value="investments", checked=current.get("investments", True)),
            questionary.Choice("portfolio", value="portfolio", checked=current.get("portfolio", True)),
        ],
    ).ask()
    if selected is None:
        return 1
    cfg["features"] = {k: (k in selected) for k in ("expenses", "income", "investments", "portfolio")}
    _save_yaml(CONFIG_PATH, cfg, header=_HEADER)
    print(f"✅ Features actualizadas: {cfg['features']}")
    return 0


# ── Dispatcher ───────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    """Entrada del subcomando 'config'. argv son los argumentos tras 'config'."""
    import argparse

    parser = argparse.ArgumentParser(prog="tr-sync config")
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("init", help="Wizard interactivo (primer setup)")
    sub.add_parser("show", help="Imprime config.yaml")
    sub.add_parser("validate", help="Valida config.yaml")

    p = sub.add_parser("set", help="Cambia un valor (dot-notation: sheets.expenses Gastos)")
    p.add_argument("key")
    p.add_argument("value")

    p = sub.add_parser("add-asset", help="Añade un ISIN al portfolio_cell_map")
    p.add_argument("isin")
    p.add_argument("label")

    p = sub.add_parser("remove-asset", help="Quita un ISIN del portfolio_cell_map")
    p.add_argument("isin")

    p = sub.add_parser("add-ignore", help="Añade un patrón a ignore_events[section].title_contains")
    p.add_argument("section", choices=["income", "expenses"])
    p.add_argument("text")

    p = sub.add_parser("remove-ignore", help="Quita un patrón de ignore_events[section].title_contains")
    p.add_argument("section", choices=["income", "expenses"])
    p.add_argument("text")

    sub.add_parser("features", help="Wizard para activar/desactivar features")

    args = parser.parse_args(argv)
    handler = {
        "init": cmd_init,
        "show": cmd_show,
        "validate": cmd_validate,
        "set": cmd_set,
        "add-asset": cmd_add_asset,
        "remove-asset": cmd_remove_asset,
        "add-ignore": cmd_add_ignore,
        "remove-ignore": cmd_remove_ignore,
        "features": cmd_features,
    }[args.action]
    return handler(args) or 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
