from typing import Iterable, List, Tuple
from .models import ComponentRegistry, ComponentStateHistory, ComponentStateSnapshot, LifecycleState, LogEvent, EventType
from .parsing import parse_log_line
from .tool_logger import logger

def apply_event(history: ComponentStateHistory, ev: LogEvent) -> ComponentStateSnapshot:
    curr = history.latest
    if curr is None:
        curr = ComponentStateSnapshot(timestamp=ev.timestamp, lifecycle=LifecycleState())
        logger.debug(
            "apply_event: initializing snapshot for component=%s at ts=%s",
            history.component_id,
            ev.timestamp.isoformat(),
        )

    next_snap = curr.clone()
    next_snap.timestamp = ev.timestamp

    logger.debug(
        "apply_event: before update component=%s type=%s created=%s started=%s altsteps=%s",
        history.component_id,
        ev.type,
        curr.lifecycle.created,
        curr.lifecycle.started,
        list(curr.altsteps_active),
    )

    if ev.type == EventType.COCR:
        next_snap.lifecycle.created = True

    elif ev.type == EventType.COST:
        next_snap.lifecycle.started = True
        if ev.start_fn:
            next_snap.lifecycle.start_origin = ev.start_fn

    elif ev.type == EventType.DTAC:
        if ev.activated_fn:
            next_snap.altsteps_active.add(ev.activated_fn.qualified_name)

    elif ev.type == EventType.DTDE:
        for f in ev.deactivated_fns:
            next_snap.altsteps_active.discard(f.qualified_name)

    elif ev.type == EventType.CODO:
        if ev.expectation is not None:
            next_snap.expectation.status = ev.expectation
        if ev.related_component:
            next_snap.expectation.related_component = ev.related_component

    elif ev.type == EventType.COFI:
        if ev.verdict is not None:
            next_snap.verdict = ev.verdict

    logger.debug(
        "apply_event: after update component=%s type=%s created=%s started=%s altsteps=%s verdict=%s",
        history.component_id,
        ev.type,
        next_snap.lifecycle.created,
        next_snap.lifecycle.started,
        list(next_snap.altsteps_active),
        next_snap.verdict,
    )
    return next_snap

def _parse_events_from_lines(
    lines: Iterable[str],
    strict: bool,
) -> Tuple[List[LogEvent], List[str]]:
    logger.debug("_parse_events_from_lines: strict=%s", strict)
    events: List[LogEvent] = []
    errors: List[str] = []

    for idx, line in enumerate(lines, start=1):
        raw = line.strip()
        if not raw:
            continue

        try:
            ev = parse_log_line(raw)
        except Exception as exc:
            msg = f"Line {idx}: {exc} | content={raw}"
            if strict:
                logger.error("Parsing failed (strict) at line %d: %s", idx, msg)
                raise ValueError(f"Parsing failed. {msg}")
            logger.warning("Parsing error at line %d: %s", idx, msg)
            errors.append(msg)
            continue

        if ev is None:
            # Treat as silently skipped (e.g. malformed or non-event line)
            logger.debug("Skipping None event at line %d: %s", idx, raw)
            continue

        logger.debug(
            "_parse_events_from_lines: parsed event line=%d ts=%s type=%s component=%s",
            idx,
            ev.timestamp.isoformat(),
            ev.type,
            ev.component_id,
        )
        events.append(ev)

    logger.info("_parse_events_from_lines: total events=%d, errors=%d", len(events), len(errors))
    return events, errors

def _build_registry_from_events(
    events: List[LogEvent],
    strict: bool,
    errors: List[str],
) -> ComponentRegistry:
    logger.debug("_build_registry_from_events: events=%d strict=%s", len(events), strict)
    events.sort(key=lambda e: e.timestamp)

    registry = ComponentRegistry()

    for idx, ev in enumerate(events):
        logger.debug(
            "_build_registry_from_events: applying event %d/%d ts=%s type=%s component=%s",
            idx + 1,
            len(events),
            ev.timestamp.isoformat(),
            ev.type,
            ev.component_id,
        )
        try:
            hist = registry.ensure(ev.component_id, ev.timestamp)
            next_snapshot = apply_event(hist, ev)
            hist.append(ev, next_snapshot)
        except Exception as exc:
            msg = (
                f"Apply error: component={ev.component_id}, "
                f"ts={ev.timestamp}, type={ev.type} -> {exc}"
            )
            if strict:
                logger.error("Apply failed (strict) for event %d: %s", idx + 1, msg)
                raise ValueError(msg)
            logger.warning("Apply error for event %d: %s", idx + 1, msg)
            errors.append(msg)
            continue

    logger.info("_build_registry_from_events: built %d component histories", len(registry.by_id))
    return registry

def process_events(lines: Iterable[str], strict: bool = False) -> Tuple[ComponentRegistry, int]:
    logger.debug("process_events: strict=%s", strict)
    events, errors = _parse_events_from_lines(lines, strict=strict)
    registry = _build_registry_from_events(events, strict=strict, errors=errors)

    if errors:
        logger.warning("process_events: total malformed/failed lines/events=%d", len(errors))
        print(
            f"Skipped {len(errors)} malformed or failed lines/events. "
            "Use --strict to fail on first error."
        )

    logger.debug("process_events: completed with components=%d", len(registry.by_id))
    return registry, len(errors)