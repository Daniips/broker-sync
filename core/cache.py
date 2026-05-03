"""
Disk cache for `(PortfolioSnapshot, list[Transaction])` to avoid re-fetching
TR data twice in a row.

Typical use: running `make portfolio && make insights` no longer triggers
two logins and two downloads — the second run reuses the cache if it was
created less than TTL ago.

# Tradeoffs

- Pickle: simple, supports dataclasses + ZoneInfo + tuples. Not portable
  across Python versions if the internal pickles change, but since the cache
  is refreshed every few minutes it's not an issue.
- Short TTL (5 min default): enough to chain commands in the same terminal
  session without hitting the limit where TR may have emitted new events.
  Do not use as a long-lived cache.
- Best-effort: if the cache is corrupt or fails to load, returns None and
  triggers a refetch. Never breaks the main flow.
"""
from __future__ import annotations

import logging
import pickle
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from core.types import PortfolioSnapshot, Transaction


DEFAULT_TTL = timedelta(minutes=5)
_CACHE_VERSION = 2   # bump if the shape of Transaction/Position/Snapshot/cache changes

log = logging.getLogger("tr_sync")


def load_cached_session(
    cache_path: Path,
    *,
    ttl: timedelta = DEFAULT_TTL,
) -> Optional[tuple[PortfolioSnapshot, list[Transaction], dict[str, list[dict]]]]:
    """Return (snapshot, txs, benchmarks) if there is a fresh cache, or None.

    `benchmarks` is `{ISIN: price_history_list}` for the benchmark ISINs
    cached with the session. Empty `{}` if none was ever requested.

    `ttl`: maximum cache age before considering it stale.
    Any load error (file missing, corrupt pickle, obsolete version, etc.)
    → silently None. The caller falls back to a normal fetch.
    """
    if not cache_path.exists():
        return None
    try:
        with open(cache_path, "rb") as f:
            data = pickle.load(f)
    except Exception as e:
        log.debug(f"cache: could not load ({e}); refetch")
        return None
    if not isinstance(data, dict):
        return None
    if data.get("version") != _CACHE_VERSION:
        log.debug(f"cache: obsolete version ({data.get('version')} ≠ {_CACHE_VERSION}); refetch")
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
    benchmarks = data.get("benchmarks") or {}
    if not isinstance(snapshot, PortfolioSnapshot) or not isinstance(txs, list):
        return None
    if not isinstance(benchmarks, dict):
        benchmarks = {}
    log.info(f"   ⚡ using TR cache (age: {age.total_seconds():.0f}s, TTL: {ttl.total_seconds():.0f}s)")
    return snapshot, txs, benchmarks


def save_cached_session(
    cache_path: Path,
    snapshot: PortfolioSnapshot,
    txs: list[Transaction],
    *,
    benchmarks: Optional[dict[str, list[dict]]] = None,
) -> None:
    """Save (snapshot, txs, benchmarks) to disk. Best-effort: never breaks."""
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "wb") as f:
            pickle.dump({
                "version": _CACHE_VERSION,
                "cached_at": datetime.now(),
                "snapshot": snapshot,
                "txs": txs,
                "benchmarks": benchmarks or {},
            }, f)
    except Exception as e:
        log.debug(f"cache: could not save ({e})")


def invalidate_cache(cache_path: Path) -> None:
    """Delete the cache (for `--refresh` or after destructive changes)."""
    try:
        cache_path.unlink(missing_ok=True)
    except Exception:
        pass
