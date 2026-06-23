"""
Log warnings detector. Scans ``pllg`` lines and exposes the WARNING/ERROR
ones as structured signals.

The k3 runtime emits plugin / connector log messages as:

    <ts>|pllg|<source>|<hex_code>|<level>|<module>|<message...>

Field-level details:

  - ``<source>`` is ``COMPONENT=<file>:<line>`` for component-attached
    plugins, or ``?`` for internal events with no specific owner.
  - ``<level>`` is the runtime log level (``INF``, ``INFO``, ``WRN``,
    ``ERR``, ``DBG``, ...). Some lines use a non-standard field shift
    (e.g. ``cond``); those are skipped silently.
  - ``<module>`` is the plugin name (``RestConnector #0``, ``Binary-Codec2``,
    ``SicSharedConnector``, ``JsonRPC-Connector``, ...).

By default we consider a "real" warning to be ``level ∈ {WRN, ERR, ERROR,
FATAL, CRITICAL, EMERG}`` (case-insensitive). ``INF/INFO/DBG/TRACE`` are
informational and dropped here.

Because these lines tend to spam (e.g. "Use of deprecated contextName
parameter" repeats N times for every connector instance), the report
collapses identical (component, level, module, message) tuples into a
single entry with a count, and exposes a few first/last timestamps for
correlation in the UI.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .parsing import parse_timestamp
from .tool_logger import logger


# ============================================================
# Constants
# ============================================================
INTERESTING_LEVELS: Set[str] = {
    "WRN", "WARN", "WARNING",
    "ERR", "ERROR",
    "FATAL", "CRITICAL", "EMERG",
}

# Default whitelist: well-known harmless WRN messages that flood the log
# with no diagnostic value. Patterns are matched as substrings against the
# 'message' field. Each entry can be turned off via configuration later.
DEFAULT_WHITELIST: Tuple[str, ...] = (
    "Use of deprecated contextName parameter",     # RestConnector startup
    "default int size 4",                           # Binary-Codec2 noise
    "no entry for address",                         # SicSharedConnector cleanup
)


# ============================================================
# Data model
# ============================================================
@dataclass
class LogWarning:
    """A unique (component, level, module, message) bucket with hit stats."""
    component: Optional[str]
    level: str
    module: Optional[str]
    message: str
    count: int = 0
    first_ts: Optional[str] = None
    last_ts: Optional[str] = None
    sample_source: Optional[str] = None     # "<file>:<line>" of first sighting

    def to_jsonable(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LogWarningsReport:
    total_seen: int                            # total candidate lines
    total_kept: int                            # after whitelist filtering
    by_level: Dict[str, int]                   # level -> count
    items: List[LogWarning] = field(default_factory=list)
    whitelist_hits: Dict[str, int] = field(default_factory=dict)

    def to_jsonable(self) -> Dict[str, Any]:
        return {
            "summary": {
                "total_seen": self.total_seen,
                "total_kept": self.total_kept,
                "suppressed": sum(self.whitelist_hits.values()),
                "by_level": self.by_level,
            },
            "items": [w.to_jsonable() for w in self.items],
            "whitelist_hits": self.whitelist_hits,
        }

    def to_text(self) -> str:
        lines: List[str] = []
        lines.append(
            f"Log warnings: {self.total_kept} unique buckets "
            f"({self.total_seen} raw lines, "
            f"{sum(self.whitelist_hits.values())} suppressed by whitelist)"
        )
        if not self.items:
            return "\n".join(lines) + "\n  <none>\n"
        lines.append("")
        for w in self.items:
            comp = w.component or "?"
            mod = w.module or "?"
            src = f" @ {w.sample_source}" if w.sample_source else ""
            lines.append(
                f"  [{w.level}] x{w.count}  {comp} ({mod}){src}\n"
                f"    {w.message}\n"
                f"    first={w.first_ts}  last={w.last_ts}"
            )
        return "\n".join(lines) + "\n"


# ============================================================
# Helpers
# ============================================================
def _split_component_source(source_field: str) -> Tuple[Optional[str], Optional[str]]:
    src = source_field.strip()
    if not src or src == "?":
        return None, None
    if "=" not in src:
        return src, None
    name, loc = src.split("=", 1)
    loc_compact = loc.split("/")[-1] if "/" in loc else loc
    return name.strip(), loc_compact.strip()


def _is_whitelisted(message: str, whitelist: Iterable[str]) -> Optional[str]:
    """Return the matched whitelist pattern, or None."""
    for pat in whitelist:
        if pat in message:
            return pat
    return None


# ============================================================
# Public API
# ============================================================
def scan_log_warnings(
    raw_lines: Iterable[str],
    whitelist: Optional[Iterable[str]] = None,
    max_buckets: int = 500,
) -> LogWarningsReport:
    """Single-pass scan over raw log lines.

    Args:
        raw_lines: raw log lines (will be iterated once).
        whitelist: substring patterns to suppress; defaults to
            :data:`DEFAULT_WHITELIST`.
        max_buckets: hard cap on number of distinct buckets returned (sorted
            by count descending). Prevents pathological logs from blowing up
            the JSON.
    """
    if whitelist is None:
        whitelist = DEFAULT_WHITELIST
    whitelist = tuple(whitelist)

    # We use an OrderedDict keyed by (comp, level, module, message). Insertion
    # order = first-sighting order; we'll sort on the way out.
    buckets: "OrderedDict[Tuple[Optional[str], str, Optional[str], str], LogWarning]" = (
        OrderedDict()
    )
    by_level: Dict[str, int] = {}
    whitelist_hits: Dict[str, int] = {}
    total_seen = 0

    for raw in raw_lines:
        line = raw.rstrip("\n")
        if "|pllg|" not in line:
            continue

        # Cheap level pre-filter: avoid splitting INF/DBG lines.
        # We can be strict here because k3 always pads field 5 with the level.
        if not any(f"|{lv}|" in line for lv in INTERESTING_LEVELS):
            continue

        parts = line.split("|")
        # Expected layout: ts|pllg|src|hex|level|module|message...
        if len(parts) < 6:
            continue

        level = parts[4].strip().upper()
        if level not in INTERESTING_LEVELS:
            continue

        ts_raw = parts[0].strip()
        try:
            ts = parse_timestamp(ts_raw)
        except Exception:
            continue
        ts_iso = ts.isoformat()

        comp, loc = _split_component_source(parts[2])
        module = parts[5].strip() or None
        # message may contain '|' (the trailing fields), rejoin them as-is
        message = "|".join(parts[6:]).strip() if len(parts) >= 7 else ""

        total_seen += 1

        wl_match = _is_whitelisted(message, whitelist)
        if wl_match is not None:
            whitelist_hits[wl_match] = whitelist_hits.get(wl_match, 0) + 1
            continue

        by_level[level] = by_level.get(level, 0) + 1

        key = (comp, level, module, message)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = LogWarning(
                component=comp,
                level=level,
                module=module,
                message=message,
                count=1,
                first_ts=ts_iso,
                last_ts=ts_iso,
                sample_source=loc,
            )
            buckets[key] = bucket
        else:
            bucket.count += 1
            bucket.last_ts = ts_iso

    items = sorted(buckets.values(), key=lambda w: (-w.count, w.level, w.component or ""))
    if max_buckets and len(items) > max_buckets:
        logger.info(
            "scan_log_warnings: capped output at %d/%d buckets",
            max_buckets, len(items),
        )
        items = items[:max_buckets]

    logger.info(
        "scan_log_warnings: total_seen=%d kept=%d suppressed=%d levels=%s",
        total_seen,
        sum(b.count for b in buckets.values()),
        sum(whitelist_hits.values()),
        by_level,
    )

    return LogWarningsReport(
        total_seen=total_seen,
        total_kept=sum(b.count for b in buckets.values()),
        by_level=by_level,
        items=items,
        whitelist_hits=whitelist_hits,
    )
