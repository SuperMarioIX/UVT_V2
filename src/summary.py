from collections import Counter
from typing import Any, Dict, List, Set
from .models import ComponentRegistry, EventType, ComponentStateSnapshot, LogEvent
from .tool_logger import logger

def event_to_dict(ev: LogEvent) -> Dict[str, Any]:
    return {
        "timestamp": ev.timestamp.isoformat(),
        "type": ev.type,
        "component_id": ev.component_id,
        "source": (
            {
                "module_path": ev.source.module_path,
                "line_number": ev.source.line_number,
            }
            if ev.source
            else None
        ),
        "start_fn": (ev.start_fn.qualified_name if ev.start_fn else None),
        "activated_fn": (ev.activated_fn.qualified_name if ev.activated_fn else None),
        "deactivated_fns": [f.qualified_name for f in ev.deactivated_fns] if ev.deactivated_fns else [],
        "related_component": ev.related_component,
        "expectation": ev.expectation,
        "verdict": ev.verdict,
        "raw_line": ev.raw_line,
        "message_name": ev.message_name,
        "port_name": ev.port_name, 
    }

def snapshot_to_dict(s: ComponentStateSnapshot) -> Dict[str, Any]:
    return {
        "timestamp": s.timestamp.isoformat(),
        "lifecycle": {
            "created": s.lifecycle.created,
            "started": s.lifecycle.started,
            "start_origin": (
                s.lifecycle.start_origin.qualified_name if s.lifecycle.start_origin else None
            ),
        },
        "altsteps_active": sorted(s.altsteps_active),
        "expectation": {
            "status": s.expectation.status,
            "related_component": s.expectation.related_component,
        },
        "verdict": s.verdict,
    }

def registry_to_jsonable(registry: ComponentRegistry) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for hist in registry.histories():
        logger.debug(
            "registry_to_jsonable: component=%s events=%d snapshots=%d",
            hist.component_id,
            len(hist.events),
            len(hist.snapshots),
        )
        out[hist.component_id] = {
            "events": [event_to_dict(e) for e in hist.events],
            "snapshots": [snapshot_to_dict(s) for s in hist.snapshots],
        }
    return out

def summarize_registry(registry: ComponentRegistry, parse_errors_count: int = 0) -> Dict[str, Any]:
    logger.debug(
        "summarize_registry: components=%d skipped_lines=%d",
        len(registry.by_id),
        parse_errors_count,
    )
    all_events: List[LogEvent] = []
    for hist in registry.histories():
        all_events.extend(hist.events)

    if all_events:
        global_start = min(e.timestamp for e in all_events)
        global_end = max(e.timestamp for e in all_events)
    else:
        global_start = None
        global_end = None

    global_summary = {
        "components": len(registry.by_id),
        "events_total": len(all_events),
        "skipped_lines": parse_errors_count,
        "time_range": {
            "start": (global_start.isoformat() if global_start else None),
            "end": (global_end.isoformat() if global_end else None),
            "duration_seconds": (
                (global_end - global_start).total_seconds() if global_start and global_end else None
            ),
        },
    }

    per_component: Dict[str, Any] = {}
    for hist in registry.histories():
        events = hist.events
        snaps = hist.snapshots

        logger.debug(
            "summarize_registry: component=%s events=%d snapshots=%d",
            hist.component_id,
            len(events),
            len(snaps),
        )

        if not events or not snaps:
            per_component[hist.component_id] = {
                "events": 0,
                "created": False,
                "started": False,
                "verdict": None,
            }
            continue

        counts = Counter(e.type for e in events)
        first_ts = events[0].timestamp
        last_ts = events[-1].timestamp
        duration_sec = (last_ts - first_ts).total_seconds() if last_ts and first_ts else None
        rate_per_min = (len(events) / (duration_sec / 60.0)) if duration_sec and duration_sec > 0 else None

        last_snap = snaps[-1]
        created = last_snap.lifecycle.created
        started = last_snap.lifecycle.started
        start_origin = (
            last_snap.lifecycle.start_origin.qualified_name
            if last_snap.lifecycle.start_origin
            else None
        )
        verdict = last_snap.verdict
        exp_status = last_snap.expectation.status
        exp_related = last_snap.expectation.related_component

        seen_altsteps: Set[str] = set()
        for s in snaps:
            seen_altsteps.update(s.altsteps_active)
        altsteps_distinct = len(seen_altsteps)
        altsteps_active_last = len(last_snap.altsteps_active)

        per_component[hist.component_id] = {
            "events": len(events),
            "counts": {
                "COCR": counts.get(EventType.COCR, 0),
                "COST": counts.get(EventType.COST, 0),
                "DTAC": counts.get(EventType.DTAC, 0),
                "DTDE": counts.get(EventType.DTDE, 0),
                "CODO": counts.get(EventType.CODO, 0),
                "COFI": counts.get(EventType.COFI, 0),
            },
            "time_range": {
                "first": first_ts.isoformat(),
                "last": last_ts.isoformat(),
                "duration_seconds": duration_sec,
                "events_per_min": rate_per_min,
            },
            "lifecycle": {
                "created": created,
                "started": started,
                "start_origin": start_origin,
            },
            "verdict": verdict,
            "expectation": {
                "status": exp_status,
                "related_component": exp_related,
            },
            "altsteps": {
                "distinct_seen": altsteps_distinct,
                "active_last": altsteps_active_last,
            },
        }

    return {
        "global": global_summary,
        "components": per_component,
    }