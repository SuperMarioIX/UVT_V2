from datetime import datetime
from typing import Optional, Tuple, Any, List, Iterable
import re

from .models import (
    SourceLocation, StartFunction, AltstepFunction, LogEvent,
    INTERESTING_MNEMONICS, MESSAGE_INCOMING, MESSAGE_CONSUMED, MESSAGE_OUTGOING, LIFECYCLE_TO_STATE
)
from .tool_logger import logger

MESSAGE_NAME_RE = re.compile(
    r"message\s*\(\s*value\s*=\s*([A-Za-z0-9_.]+)",
    re.IGNORECASE | re.DOTALL,
)


def find_test_finish_timestamp(raw_lines: Iterable[str]) -> Optional[datetime]:
    """
    Scan raw log lines and determine the testcase finish timestamp.

    Rules:
      - If any line has mnemonic 'TCFI', return the timestamp of the LAST such line.
      - Otherwise, return the timestamp of the LAST parsable line in the file.
      - If nothing can be parsed, return None.
    """
    last_ts: Optional[datetime] = None
    last_tcfi_ts: Optional[datetime] = None

    for raw in raw_lines:
        line = raw.strip()
        if not line:
            continue

        parts = line.split("|")
        if len(parts) < 2:
            continue

        ts_str = parts[0].strip()
        try:
            ts = parse_timestamp(ts_str)
        except Exception:
            continue

        last_ts = ts

        mnemonic = normalize_type(parts[1].strip())
        if mnemonic == "TCFI":
            last_tcfi_ts = ts

    return last_tcfi_ts or last_ts




def normalize_type(t: Optional[str]) -> Optional[str]:
    return t.upper() if isinstance(t, str) else t

def normalize_component_id(raw_id: str) -> str:
    return raw_id.strip().upper()

def parse_source_location(token: Optional[str]) -> Optional[SourceLocation]:
    if not token:
        return None
    if ":" in token:
        path_str, line_str = token.rsplit(":", 1)
        try:
            return SourceLocation(module_path=path_str, line_number=int(line_str))
        except ValueError:
            return SourceLocation(module_path=path_str, line_number=None)
    return SourceLocation(module_path=token, line_number=None)

def parse_timestamp(value: str) -> datetime:
    """
    Parse ISO-like timestamps or numeric epoch-like floats in logs.
    Supports:
      - '2024-05-01T17:27:59.604357'
      - '2024-05-01 17:27:59.604357'
      - '2024-05-01T17:27:59'
      - '2147483647.000000' (epoch seconds) -> treated as epoch UTC
    """
    value = str(value).strip()
    logger.debug("parse_timestamp: value=%s", value)

    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
        logger.debug("parse_timestamp: parsed ISO=%s", ts.isoformat())
        return ts
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y%m%dT%H%M%S.%f",
        "%Y%m%dT%H%M%S",
    ):
        try:
            ts = datetime.strptime(value, fmt)
            logger.debug("parse_timestamp: parsed fmt=%s -> %s", fmt, ts.isoformat())
            return ts
        except ValueError:
            continue

    try:
        seconds = float(value)
        ts = datetime.fromtimestamp(int(seconds))
        logger.debug("parse_timestamp: parsed epoch=%s", ts.isoformat())
        return ts
    except Exception:
        pass

    logger.error("parse_timestamp: unsupported format: %s", value)
    raise ValueError(f"Unsupported timestamp format: {value}")

def split_component_and_port(token: str) -> Tuple[str, Optional[str]]:
    """
    Extract a TTCN-3 component id (and optionally port) from a token like:
      - "MY_COMPONENT.MYPORT1"
      - "MY_COMPONENT"
      - "MY_COMPONENT=/path/file.ttcn3:42"
      - "MY_COMPONENT.MYPORT1=/path/file.ttcn3:42"

    Returns (COMPONENT_ID, port_name_or_None).
    """
    token = token.strip()
    if not token:
        return "UNKNOWN", None

    # Strip trailing "=<source>" if present
    if "=" in token:
        token, _ = token.split("=", 1)

    port_name: Optional[str] = None
    if "." in token:
        comp, port = token.split(".", 1)
        port_name = port
    else:
        comp = token

    return normalize_component_id(comp), port_name

def event_type_str(ev_type: Any) -> str:
    """
    Convert an event type (enum or string) to uppercase string mnemonic.
    """
    try:
        name = ev_type.name  # type: ignore[attr-defined]
    except Exception:
        name = str(ev_type)
    return name.upper()

def extract_message_name(ev: LogEvent) -> str:
    if ev.message_name:
        logger.debug(
            "extract_message_name: using ev.message_name=%s for component=%s type=%s ts=%s",
            ev.message_name,
            ev.component_id,
            ev.type,
            ev.timestamp.isoformat(),
        )
        return ev.message_name

    if isinstance(ev.raw_line, str) and ev.raw_line:
        m = MESSAGE_NAME_RE.search(ev.raw_line)
        if m:
            name = m.group(1)
            logger.debug(
                "extract_message_name: regex matched name=%s for component=%s type=%s ts=%s",
                name,
                ev.component_id,
                ev.type,
                ev.timestamp.isoformat(),
            )
            return name

    logger.warning(
        "extract_message_name: fallback to type for component=%s type=%s ts=%s raw_line=%r",
        ev.component_id,
        ev.type,
        ev.timestamp.isoformat(),
        ev.raw_line,
    )
    return event_type_str(ev.type)

def parse_log_line(line: str) -> Optional[LogEvent]:
    """
    Parse a single pipe-delimited TTCN-3 log line.

    Only lines whose mnemonic is in INTERESTING_MNEMONICS are turned into LogEvent.
    Everything else returns None (and is skipped by process_events).
    """
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None

    parts = raw.split("|")
    if len(parts) < 2:
        logger.debug("parse_log_line: too few parts, skipping line: %s", raw)
        return None

    ts_raw = parts[0].strip()
    et_raw = parts[1].strip()
    et = normalize_type(et_raw or "") or ""

    if et not in INTERESTING_MNEMONICS:
        logger.debug("parse_log_line: mnemonic %s not in INTERESTING_MNEMONICS, skipping", et)
        return None

    ts = parse_timestamp(ts_raw)
    logger.debug("parse_log_line: ts=%s, et=%s, raw=%s", ts.isoformat(), et, raw)

    component_id = "UNKNOWN"
    source: Optional[SourceLocation] = None
    start_fn: Optional[StartFunction] = None
    activated_fn: Optional[AltstepFunction] = None
    deactivated_fns: Tuple[AltstepFunction, ...] = tuple()
    related_component: Optional[str] = None
    expectation: Optional[str] = None
    verdict: Optional[str] = None
    port_name: Optional[str] = None

    def parse_component_and_source(token: str) -> Tuple[str, Optional[SourceLocation]]:
        token = token.strip()
        if not token:
            return "UNKNOWN", None
        if "=" in token:
            cid, loc = token.split("=", 1)
            return normalize_component_id(cid), parse_source_location(loc.strip())
        return normalize_component_id(token), None

    if et in LIFECYCLE_TO_STATE:
        if len(parts) >= 3:
            component_id, source = parse_component_and_source(parts[2])

        if et == "COST":
            if len(parts) >= 4 and parts[3].strip():
                start_fn = StartFunction(parts[3].strip())

        elif et == "DTAC":
            if len(parts) >= 4 and parts[3].strip():
                activated_fn = AltstepFunction(parts[3].strip())

        elif et == "DTDE":
            fns: List[AltstepFunction] = []
            for tok in parts[3:]:
                tok = tok.strip()
                if tok:
                    fns.append(AltstepFunction(tok))
            deactivated_fns = tuple(fns)

        elif et == "CODO":
            if len(parts) >= 4 and parts[3].strip():
                related_component = parts[3].strip()
            if len(parts) >= 5 and parts[4].strip():
                expectation = parts[4].strip()

        elif et == "COFI":
            if len(parts) >= 4 and parts[3].strip():
                verdict = parts[3].strip()

        elif et == "COCR":
            # Re-attribute: COCR is logged from the creator's call site
            # (parts[2]), but it represents the *creation* of the new component
            # in parts[3]. The new component is the rightful owner of the
            # 'Created' lifecycle event.
            #
            # Format: cocr|<creator>(=<src>)?|<new_component>|<lifetime>
            #
            # We swap so that:
            #   component_id          = new component   (parts[3])
            #   related_component     = creator's name  (parts[2] head)
            #   source                = creator's src   (parts[2] tail)
            #
            # On the engine side this means new_snap.lifecycle.created=True
            # is correctly set on the new component (e.g. NOKIA_CPRI_RADIO_1)
            # at the actual creation timestamp.
            if len(parts) >= 4 and parts[3].strip():
                creator_name = component_id      # captured above from parts[2]
                creator_source = source           # may be None for "k3r"
                new_component_id = normalize_component_id(parts[3].strip())

                component_id = new_component_id
                related_component = creator_name
                source = creator_source

    else:
        # Non-lifecycle interesting events (e.g. PTQU/PTRX/PTSD)
        # Component is usually "COMP.PORT" or "COMP".
        raw_comp = ""
        if len(parts) >= 4 and parts[3].strip():
            raw_comp = parts[3].strip()
        elif len(parts) >= 3 and parts[2].strip():
            raw_comp = parts[2].strip()

        component_id, port_name = split_component_and_port(raw_comp)
        # If you ever want per-port info later, you can store port_name somewhere in LogEvent
        # (e.g. related_component, or add a new field).
        logger.debug(
            "parse_log_line: message event mapped to component_id=%s port=%s raw_comp=%s",
            component_id,
            port_name,
            raw_comp,
        )

        # Message name extraction for PTQU/PTRX/PTSD, etc.
    message_name: Optional[str] = None

    if et in MESSAGE_INCOMING or et in MESSAGE_CONSUMED or et in MESSAGE_OUTGOING:
        # PTSD pattern:
        #   ts|ptsd|COMP=/path:line|COMP.port|SYSTEM.port[...]|Iface.MessageName|{payload}
        if len(parts) >= 6:
            last = parts[-1].strip()
            # Heuristic: if last field looks like TTCN payload, previous field is the message type
            if last.startswith("{"):
                candidate = parts[-2].strip()
                if candidate:
                    message_name = candidate
                    logger.debug(
                        "parse_log_line: PTSD/PT* message_name from penultimate field: %s "
                        "(component=%s type=%s)",
                        message_name,
                        component_id,
                        et,
                    )

    # Fallback: regex on raw line (for other styles or if above did not match)
    if message_name is None:
        msg_match = MESSAGE_NAME_RE.search(raw)
        if msg_match:
            message_name = msg_match.group(1)
            logger.debug(
                "parse_log_line: message_name from regex: %s (component=%s type=%s)",
                message_name,
                component_id,
                et,
            )

    ev = LogEvent(
        timestamp=ts,
        type=et,
        component_id=component_id,
        source=source,
        start_fn=start_fn,
        activated_fn=activated_fn,
        deactivated_fns=deactivated_fns,
        related_component=related_component,
        expectation=expectation,
        verdict=verdict,
        raw_line=raw,
        message_name=message_name,
        port_name=port_name,
    )
    logger.debug(
        "parse_log_line: created LogEvent ts=%s type=%s component=%s message_name=%s",
        ev.timestamp.isoformat(),
        ev.type,
        ev.component_id,
        ev.message_name,
    )
    return ev