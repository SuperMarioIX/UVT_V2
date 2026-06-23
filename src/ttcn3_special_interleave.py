# special_interleaved.py

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import re

from src.ttcn3_record_parser import parse_ttcn_record, prune_placeholders


def dbg(msg: str):
    if INTERLEAVED_DEBUG:
        print(f"[INTERLEAVED DEBUG] {msg}")


# ============================================================
# Timestamp parser (same semantics as in parsing.py)
# ============================================================

def parse_timestamp(value: str) -> datetime:
    """
    Parse ISO-like timestamps or numeric epoch-like floats in logs.
    Supports:
      - '2024-05-01T17:27:59.604357'
      - '2024-05-01 17:27:59.604357'
      - '2024-05-01T17:27:59'
      - '2147483647.000000' (epoch seconds)
    """
    value = str(value).strip()
    # Try ISO
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        pass
    # Try plain datetime
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y%m%dT%H%M%S.%f",
        "%Y%m%dT%H%M%S",
    ):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    # Try float seconds epoch
    try:
        seconds = float(value)
        return datetime.fromtimestamp(int(seconds))
    except Exception:
        pass
    raise ValueError(f"Unsupported timestamp format: {value}")


# ============================================================
# Model
# ============================================================
@dataclass
class InterleavedEvent:
    ts: datetime
    index: int              # aEvents[index]
    status: str             # "started" or "matched"
    dn: str                 # extracted object path (dn/dnt/...)
    payload_summary: str    # concise human-readable summary
    raw_line: str

# ============================================================
# Regexes and constants
# ============================================================
INTERLEAVED_HEADER_RE = re.compile(
    r'\[IM::expectation::interleaved\]\s*(.*?)\s*aEvents\[(\d+)\]',
    re.IGNORECASE,
)

SINGLE_HEADER_RE = re.compile(
    r'\[IM::expectation::single\]\s*(start|event matched)',
    re.IGNORECASE,
)

# key:=value where value is "string" or bare token (identifier/number/etc.)
KV_RE = re.compile(
    r'([a-zA-Z0-9_]+)\s*:=\s*("([^"]*)"|[a-zA-Z0-9_.\-]+)'
)

DN_RE = re.compile(r'dn\w*\s*:=\s*"([^"]+)"')

INTERLEAVED_DEBUG = True   # set to False when done debugging
PLACEHOLDER_TOKENS = {"?", "omit", "*"}

# ============================================================
# Helpers
# ============================================================
def extract_dn(rec: dict, record_text: str) -> str:
    dn = None

    if isinstance(rec, dict):
        # Prefer exact dn
        if "dn" in rec and isinstance(rec["dn"], str):
            dn = rec["dn"]
        else:
            # Any key starting with "dn"
            for k, v in rec.items():
                if k.startswith("dn") and isinstance(v, str):
                    dn = v
                    break

    if not dn and record_text:
        m = DN_RE.search(record_text)
        if m:
            dn = m.group(1)

    return dn or "<unknown>"

def _is_placeholder_scalar(v: Any) -> bool:
    return isinstance(v, str) and v in PLACEHOLDER_TOKENS

def _flatten_fields(
    node: Any,
    prefix: str = "",
    depth: int = 0,
    max_depth: int = 3,
    max_fields: int = 12,
    out: Optional[List[str]] = None,
) -> List[str]:
    """
    Recursively flatten a nested TTCN record into "path:=value" strings.

    - prefix: dot-separated path (e.g. "obj.object.g_m.s")
    - max_depth: stop descending after this many nested levels
    - max_fields: stop after collecting this many fields
    """
    if out is None:
        out = []

    if len(out) >= max_fields:
        return out
    if depth > max_depth:
        return out

    # dict → recurse into keys
    if isinstance(node, dict):
        for k, v in node.items():
            if len(out) >= max_fields:
                break
            path = f"{prefix}.{k}" if prefix else k

            if isinstance(v, (dict, list)):
                _flatten_fields(v, path, depth + 1, max_depth, max_fields, out)
            else:
                if _is_placeholder_scalar(v):
                    continue
                if isinstance(v, str):
                    out.append(f"{path}:={v}")
                else:
                    out.append(f"{path}:={v!r}")
        return out

    # list → treat items with indices
    if isinstance(node, list):
        for idx, v in enumerate(node):
            if len(out) >= max_fields:
                break
            path = f"{prefix}[{idx}]" if prefix else f"[{idx}]"
            if isinstance(v, (dict, list)):
                _flatten_fields(v, path, depth + 1, max_depth, max_fields, out)
            else:
                if _is_placeholder_scalar(v):
                    continue
                if isinstance(v, str):
                    out.append(f"{path}:={v}")
                else:
                    out.append(f"{path}:={v!r}")
        return out

    # Scalars at the root (rare here)
    if not _is_placeholder_scalar(node):
        if isinstance(node, str):
            out.append(f"{prefix}:={node}" if prefix else node)
        else:
            out.append(f"{prefix}:={node!r}" if prefix else repr(node))
    return out

def fallback_kv_summary(record_text: str, max_pairs: int = 15) -> str:
    """
    Very robust summariser for TTCN-ish record text:
      - Ignore full syntax.
      - Extract key:=value pairs by regex.
      - Skip placeholders (?, omit, *).
      - Return up to max_pairs as 'k:=v, k2:=v2, ...'.
    """
    pairs: List[str] = []

    for m in KV_RE.finditer(record_text):
        key = m.group(1)
        # If quoted "val", group(3) is without quotes; otherwise group(2)
        val = m.group(3) if m.group(3) is not None else m.group(2)

        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1]

        if _is_placeholder_scalar(val):
            continue

        pairs.append(f"{key}:={val}")
        if len(pairs) >= max_pairs:
            break

    if not pairs:
        return "<no_payload>"

    return ", ".join(pairs)

def is_empty_event(ev: InterleavedEvent) -> bool:
    return ev.dn == "<unknown>" and ev.payload_summary == "<no_payload>"

# ============================================================
# Core parsing logic
# ============================================================
def _extract_record_text(text: str) -> Optional[str]:
    """
    Extract TTCN-3 record text from a string that starts at or before '{'.
    Assumes that the first '{' in 'text' belongs to the record.
    """
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]

    return text[start:]  # best-effort fallback

def _build_payload_summary(rec: dict) -> str:
    """
    Generic, key-agnostic payload summary.

    Strategy:
      1) Prefer rec["obj"]["object"] if it exists (typical TTCN logs).
      2) Else use rec["obj"] if it exists.
      3) Else use the top-level dict itself.
      4) Flatten to up to max_fields path:=value entries.
    """
    if not isinstance(rec, dict):
        return "<no_payload>"

    candidates: List[dict] = []

    obj = rec.get("obj")
    if isinstance(obj, dict):
        obj_object = obj.get("object")
        if isinstance(obj_object, dict):
            candidates.append(obj_object)
        candidates.append(obj)

    candidates.append(rec)

    root = None
    for c in candidates:
        # pick the first dict that has at least one non-placeholder scalar somewhere
        flat = _flatten_fields(c, max_fields=1)
        if flat:
            root = c
            break

    if root is None:
        return "<no_payload>"

    # Now actually flatten with reasonable limits
    flat_fields = _flatten_fields(root, max_fields=10, max_depth=4)
    if not flat_fields:
        return "<no_payload>"

    # Compact single-line summary, suitable for LLM context
    joined = ", ".join(flat_fields)
    return joined

def parse_interleaved_ulog_line(raw_line: str, logger) -> Optional[InterleavedEvent]:
    # logger.debug(f"CHECKING raw line: {raw_line.rstrip()}")

    line = raw_line.strip()
    if not line:
        logger.debug(" -> REJECT: empty line")
        return None

    low = line.lower()
    if "|ulog|" not in low:
        return None

    if "bm_ims_client" not in low:
        return None

    parts = line.split("|")
    if len(parts) < 3:
        return None

    ts_str = parts[0].strip()
    try:
        ts = parse_timestamp(ts_str)
    except Exception as exc:
        logger.debug(f" -> REJECT: timestamp error: {exc}")
        return None

    # 1) Try interleaved header: [IM::expectation::interleaved] ... aEvents[idx]
    header_match = INTERLEAVED_HEADER_RE.search(line)

    if header_match:
        kind_str, idx_str = header_match.groups()
        logger.debug(f"INTERLEAVED HEADER kind='{kind_str}' idx='{idx_str}'")

        try:
            index = int(idx_str)
        except ValueError:
            logger.debug(" -> REJECT: invalid index in interleaved header")
            return None

        kind_low = kind_str.lower()
        if "start" in kind_low:
            status = "started"
        elif "match" in kind_low:
            status = "matched"
        else:
            status = "unknown"

        header_end = header_match.end()

    else:
        # 2) Try single header: [IM::expectation::single] start / event matched
        single_match = SINGLE_HEADER_RE.search(line)
        if not single_match:
            # Not an interleaved nor a single expectation line
            return None

        kind_str = single_match.group(1)  # "start" or "event matched"
        logger.debug(f"SINGLE HEADER kind='{kind_str}'")

        kind_low = kind_str.lower()
        if "start" in kind_low:
            status = "started"
        elif "match" in kind_low:
            status = "matched"
        else:
            status = "unknown"

        # For single expectations we don't have an index from aEvents[],
        # so use a synthetic index (0). We can later group only by ts/status if needed.
        index = 0
        header_end = single_match.end()

    logger.debug(f"status={status}")

    # From here on, the rest of the function can stay exactly as you had it,
    # including record extraction and TTCN parsing / fallback:

    brace_pos = line.find("{", header_end)
    logger.debug(f"header_end={header_end} brace_pos={brace_pos}")

    # RULE: matched events MUST have a record
    if status == "matched" and brace_pos == -1:
        logger.debug(" -> REJECT matched event with no record")
        return None

    # RULE: started events may have no record
    if brace_pos == -1:
        logger.debug(" -> No record found; returning minimal event")
        return InterleavedEvent(
            ts=ts,
            index=index,
            status=status,
            dn="<unknown>",
            payload_summary="<no_payload>",
            raw_line=raw_line,
        )

    record_sub = line[brace_pos:]
    logger.debug(f"record_sub: {record_sub[:200]}")

    record_text = _extract_record_text(record_sub)
    logger.debug(f"record_text={record_text}")

    if not record_text:
        logger.debug(" -> Could not extract record_text; using minimal event")
        return InterleavedEvent(
            ts=ts,
            index=index,
            status=status,
            dn="<unknown>",
            payload_summary="<no_payload>",
            raw_line=raw_line,
        )

    try:
        parsed = parse_ttcn_record(record_text)
        logger.debug(f"parsed={parsed}")
        pruned = prune_placeholders(parsed)
        logger.debug(f"pruned={pruned}")
        source_for_payload = pruned if isinstance(pruned, dict) and pruned else parsed

        if pruned is None:
            pruned = {}

        dn = extract_dn(pruned, record_text)
        payload_summary = _build_payload_summary(source_for_payload)
        logger.debug(f"payload_summary={payload_summary}")

        return InterleavedEvent(
            ts=ts,
            index=index,
            status=status,
            dn=dn,
            payload_summary=payload_summary,
            raw_line=raw_line,
        )

    except Exception as exc:
        logger.debug(f" -> PARSE ERROR: {exc}")

        dn = extract_dn({}, record_text)           # from raw text only
        payload_summary = fallback_kv_summary(record_text)

        logger.debug(
            " -> PARSE ERROR FALLBACK: dn=%s payload=%s",
            dn,
            payload_summary,
        )

        return InterleavedEvent(
            ts=ts,
            index=index,
            status=status,
            dn=dn,
            payload_summary=payload_summary,
            raw_line=raw_line,
        )

# ============================================================
# Overview building
# ============================================================
def build_interleaved_overview(events: List[InterleavedEvent]) -> Dict[str, Any]:
    """
    Group events by index:
      - started
      - matched
      - not_matched (started but no matched)
    """
    from collections import defaultdict

    by_index: Dict[int, Dict[str, List[InterleavedEvent]]] = defaultdict(
        lambda: {"started": [], "matched": []}
    )

    for ev in events:
        by_index[ev.index][ev.status].append(ev)

    started_block: List[Dict[str, Any]] = []
    matched_block: List[Dict[str, Any]] = []
    not_matched_block: List[Dict[str, Any]] = []

    for idx in sorted(by_index.keys()):
        group = by_index[idx]
        starts = group["started"]
        matches = group["matched"]

        start_ev = starts[0] if starts else None
        match_ev = matches[0] if matches else None

        if start_ev:
            started_block.append(
                {
                    "index": idx,
                    "object": start_ev.dn,
                    "payload": start_ev.payload_summary,
                    "ts": start_ev.ts.isoformat(),
                }
            )

        if match_ev:
            matched_block.append(
                {
                    "index": idx,
                    "object": match_ev.dn,
                    "payload": match_ev.payload_summary,
                    "ts": match_ev.ts.isoformat(),
                }
            )

        if start_ev and not match_ev:
            not_matched_block.append(
                {
                    "index": idx,
                    "object": start_ev.dn,
                    "payload": start_ev.payload_summary,
                    "ts": start_ev.ts.isoformat(),
                }
            )

    return {
        "component": "BM_IMS_CLIENT",
        "expected_interleaves_started": started_block,
        "expected_interleaves_matched": matched_block,
        "expected_interleaves_not_matched": not_matched_block,
    }

def render_interleaved_overview_text(summary: Dict[str, Any]) -> str:
    """
    Render BM_IMS_CLIENT overview in the textual style you sketched.
    """
    lines: List[str] = []

    lines.append(summary["component"])
    lines.append("\texpected interleaves started")
    for ev in summary["expected_interleaves_started"]:
        lines.append(f"\t\tEvent{ev['index']}:")
        lines.append(f"\t\t\tExpected object: \"{ev['object']}\"")
        lines.append(f"\t\t\tExpected payload: {ev['payload']}")

    lines.append("\texpected interleaves matched")
    for ev in summary["expected_interleaves_matched"]:
        lines.append(f"\t\tEvent{ev['index']}:")
        lines.append(f"\t\t\tMatched object: \"{ev['object']}\"")
        lines.append(f"\t\t\tMatched payload: {ev['payload']}")

    lines.append("\texpected interleaves not matched")
    for ev in summary["expected_interleaves_not_matched"]:
        lines.append(f"\t\tEvent{ev['index']}:")
        lines.append(f"\t\t\tNotMatched object: \"{ev['object']}\"")
        lines.append(f"\t\t\tNotMatched payload: {ev['payload']}")

    return "\n".join(lines) + "\n"