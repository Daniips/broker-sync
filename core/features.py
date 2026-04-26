"""
Registry of product features and their broker-capability requirements.

Registro de features del producto y las capabilities de broker que necesita
cada una.

Usage / Uso:
  - Cada feature declara qué `capabilities` necesita del broker activo.
  - Cada broker (`brokers/<x>/__init__.py`) exporta un set CAPABILITIES.
  - El config del usuario (`config.yaml > features`) puede activar/desactivar
    cualquier feature individualmente.
  - `is_feature_enabled(name, broker_caps, config)` combina ambas señales.

Por qué importa: cuando llegue un 2º broker que no soporte X (p.ej. saveback
no aplica a IBKR), las features que dependen de X se desactivan
automáticamente en vez de petar al ejecutarse. El usuario ve en `make features`
qué está disponible y qué no, y por qué.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class Feature:
    name: str
    description: str
    requires: tuple[str, ...] = field(default_factory=tuple)
    default_enabled: bool = True


# Registry of all product features. Add new ones here as you build them.
# Registro de todas las features. Cuando añadas una nueva, decláralas aquí.
FEATURE_REGISTRY: dict[str, Feature] = {
    # ── Sync diario a la Sheet ──────────────────────────────────────────
    "expenses": Feature(
        name="expenses",
        description="Sincroniza gastos del broker (CARD_TRANSACTION, BIZUM out) a Sheet",
        requires=("expense_tracking",),
    ),
    "income": Feature(
        name="income",
        description="Sincroniza ingresos del broker (BIZUM in, transferencias) a Sheet",
        requires=("expense_tracking",),
    ),
    "investments": Feature(
        name="investments",
        description="Recalcula 'Dinero invertido YYYY' con BUYs del mes actual",
        requires=("fetch_transactions",),
    ),
    "portfolio": Feature(
        name="portfolio",
        description="Snapshot de valor por activo a la pestaña 'Calculo ganancias'",
        requires=("fetch_snapshot",),
    ),

    # ── Informes / análisis ─────────────────────────────────────────────
    "renta": Feature(
        name="renta",
        description="Informe IRPF español: FIFO + dividendos + intereses + bonos + 720",
        requires=("fetch_transactions", "tax_renta_es"),
    ),
    "insights": Feature(
        name="insights",
        description="Patrimonio, rentabilidad TR-style + propio, MWR, aportaciones",
        requires=("fetch_transactions", "fetch_snapshot"),
    ),
    "concentration": Feature(
        name="concentration",
        description="Distribución de cartera por posición + alerta de concentración",
        requires=("fetch_snapshot",),
    ),

    # ── Snapshots históricos ────────────────────────────────────────────
    "snapshot_persist": Feature(
        name="snapshot_persist",
        description="Guarda snapshot agregado y por posición en pestañas ocultas",
        requires=("fetch_snapshot",),
    ),
    "backfill_snapshots": Feature(
        name="backfill_snapshots",
        description="Reconstruye snapshots históricos con precios pasados",
        requires=("fetch_snapshot", "fetch_transactions", "fetch_price_history"),
    ),

    # ── Métricas que dependen de features broker-específicas ────────────
    "saveback_metrics": Feature(
        name="saveback_metrics",
        description="Plusvalía descontando saveback (cuando el broker tiene saveback)",
        requires=("fetch_snapshot", "fetch_transactions", "saveback"),
    ),
}


def is_feature_supported(feature_name: str, broker_capabilities: Iterable[str]) -> bool:
    """¿El broker actual soporta TODAS las capabilities que la feature necesita?"""
    f = FEATURE_REGISTRY.get(feature_name)
    if f is None:
        return False
    caps = set(broker_capabilities)
    return all(req in caps for req in f.requires)


def is_feature_enabled(
    feature_name: str,
    broker_capabilities: Iterable[str],
    config_features: dict | None = None,
) -> bool:
    """¿Está activa la feature? Combina toggle de usuario (config) y soporte del broker.

    - Si la feature no existe en el registro → False.
    - Si el broker no la soporta → False (independientemente del config).
    - Si el config la marca como `false` → False.
    - En otro caso → True.
    """
    f = FEATURE_REGISTRY.get(feature_name)
    if f is None:
        return False
    if not is_feature_supported(feature_name, broker_capabilities):
        return False
    cfg = config_features or {}
    return bool(cfg.get(feature_name, f.default_enabled))


def feature_status(
    broker_capabilities: Iterable[str],
    config_features: dict | None = None,
) -> list[dict]:
    """Devuelve estado de cada feature (para tablas/diagnóstico).

    Cada entrada: {name, description, supported, enabled_in_config, effective}.
    """
    cfg = config_features or {}
    caps = set(broker_capabilities)
    out = []
    for name, f in FEATURE_REGISTRY.items():
        supported = is_feature_supported(name, caps)
        enabled = bool(cfg.get(name, f.default_enabled))
        out.append({
            "name": name,
            "description": f.description,
            "requires": f.requires,
            "supported": supported,
            "enabled_in_config": enabled,
            "effective": supported and enabled,
        })
    return out
