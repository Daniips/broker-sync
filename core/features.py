"""
Registry of product features and their broker-capability requirements.

Usage:
  - Each feature declares which `capabilities` it needs from the active broker.
  - Each broker (`brokers/<x>/__init__.py`) exports a CAPABILITIES set.
  - The user config (`config.yaml > features`) can enable/disable any
    feature individually.
  - `is_feature_enabled(name, broker_caps, config)` combines both signals.

Why it matters: when a 2nd broker arrives that doesn't support X (e.g.
saveback doesn't apply to IBKR), features depending on X get disabled
automatically instead of crashing at runtime. The user sees in
`make features` what is available and what isn't, and why.
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
FEATURE_REGISTRY: dict[str, Feature] = {
    # ── Daily sync to the Sheet ─────────────────────────────────────────
    "expenses": Feature(
        name="expenses",
        description="Sync broker expenses (CARD_TRANSACTION, BIZUM out) to the Sheet",
        requires=("expense_tracking",),
    ),
    "income": Feature(
        name="income",
        description="Sync broker income (BIZUM in, transfers) to the Sheet",
        requires=("expense_tracking",),
    ),
    "investments": Feature(
        name="investments",
        description="Recompute 'Dinero invertido YYYY' with the current month's BUYs",
        requires=("fetch_transactions",),
    ),
    "portfolio": Feature(
        name="portfolio",
        description="Per-asset value snapshot to the 'Calculo ganancias' tab",
        requires=("fetch_snapshot",),
    ),

    # ── Reports / analysis ──────────────────────────────────────────────
    "renta": Feature(
        name="renta",
        description="Spanish IRPF report: FIFO + dividends + interest + bonds + 720",
        requires=("fetch_transactions", "tax_renta_es"),
    ),
    "insights": Feature(
        name="insights",
        description="Net worth, TR-style + own return, MWR, contributions",
        requires=("fetch_transactions", "fetch_snapshot"),
    ),
    "concentration": Feature(
        name="concentration",
        description="Per-position distribution + concentration alert",
        requires=("fetch_snapshot",),
    ),

    # ── Historical snapshots ────────────────────────────────────────────
    "snapshot_persist": Feature(
        name="snapshot_persist",
        description="Persist aggregate and per-position snapshots in hidden tabs",
        requires=("fetch_snapshot",),
    ),
    "backfill_snapshots": Feature(
        name="backfill_snapshots",
        description="Reconstruct historical snapshots with past prices",
        requires=("fetch_snapshot", "fetch_transactions", "fetch_price_history"),
    ),

    # ── Metrics depending on broker-specific features ───────────────────
    "saveback_metrics": Feature(
        name="saveback_metrics",
        description="P&L net of saveback (when the broker has saveback)",
        requires=("fetch_snapshot", "fetch_transactions", "saveback"),
    ),
}


def is_feature_supported(feature_name: str, broker_capabilities: Iterable[str]) -> bool:
    """Does the current broker support ALL capabilities required by the feature?"""
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
    """Is the feature active? Combines the user's toggle (config) and broker support.

    - If the feature is not in the registry → False.
    - If the broker does not support it → False (regardless of config).
    - If config marks it as `false` → False.
    - Otherwise → True.
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
    """Return the status of each feature (for tables/diagnostics).

    Each entry: {name, description, supported, enabled_in_config, effective}.
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
