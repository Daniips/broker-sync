"""
Disk cache for `(PortfolioSnapshot, list[Transaction])` to avoid re-fetching
TR data twice in a row.

Cache en disco para `(PortfolioSnapshot, list[Transaction])` y evitar bajar
de TR los mismos datos dos veces seguidas.

Uso típico: hacer `make portfolio && make insights` ya no genera dos logins
y dos descargas — la segunda ejecución reutiliza el cache si fue hace <TTL.

# Tradeoffs

- Pickle: simple, soporta dataclasses + ZoneInfo + tuples. No portable entre
  versiones de Python si cambian los pickles internos, pero como el cache se
  refresca cada minutos no es problema.
- TTL corto (5 min default): suficiente para encadenar comandos en la misma
  sesión de terminal sin llegar al límite donde TR podría haber emitido nuevos
  eventos. No usar como cache de larga duración.
- Best-effort: si el cache está corrupto o falla cargar, devuelve None y se
  refetch. Nunca rompe el flujo principal.
"""
from __future__ import annotations

import logging
import pickle
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from core.types import PortfolioSnapshot, Transaction


DEFAULT_TTL = timedelta(minutes=5)
_CACHE_VERSION = 1   # bump si cambia el shape de Transaction/Position/Snapshot

log = logging.getLogger("tr_sync")


def load_cached_session(
    cache_path: Path,
    *,
    ttl: timedelta = DEFAULT_TTL,
) -> Optional[tuple[PortfolioSnapshot, list[Transaction]]]:
    """Devuelve (snapshot, txs) si hay cache fresco, o None.

    `ttl`: edad máxima del cache antes de considerarlo stale.
    Cualquier error al cargar (fichero no existe, pickle corrupto, versión
    obsoleta, etc.) → None silencioso. El caller hace fetch normal.
    """
    if not cache_path.exists():
        return None
    try:
        with open(cache_path, "rb") as f:
            data = pickle.load(f)
    except Exception as e:
        log.debug(f"cache: no se pudo cargar ({e}); refetch")
        return None
    if not isinstance(data, dict):
        return None
    if data.get("version") != _CACHE_VERSION:
        log.debug(f"cache: versión obsoleta ({data.get('version')} ≠ {_CACHE_VERSION}); refetch")
        return None
    cached_at = data.get("cached_at")
    if not isinstance(cached_at, datetime):
        return None
    age = datetime.now() - cached_at
    if age > ttl:
        log.debug(f"cache: stale ({age.total_seconds():.0f}s > {ttl.total_seconds():.0f}s); refetch")
        return None
    snapshot = data.get("snapshot")
    txs = data.get("txs")
    if not isinstance(snapshot, PortfolioSnapshot) or not isinstance(txs, list):
        return None
    log.info(f"   ⚡ usando cache TR (edad: {age.total_seconds():.0f}s, TTL: {ttl.total_seconds():.0f}s)")
    return snapshot, txs


def save_cached_session(
    cache_path: Path,
    snapshot: PortfolioSnapshot,
    txs: list[Transaction],
) -> None:
    """Guarda (snapshot, txs) al disco. Best-effort: si falla, no rompe."""
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "wb") as f:
            pickle.dump({
                "version": _CACHE_VERSION,
                "cached_at": datetime.now(),
                "snapshot": snapshot,
                "txs": txs,
            }, f)
    except Exception as e:
        log.debug(f"cache: no se pudo guardar ({e})")


def invalidate_cache(cache_path: Path) -> None:
    """Borra el cache (para `--refresh` o tras cambios destructivos)."""
    try:
        cache_path.unlink(missing_ok=True)
    except Exception:
        pass
