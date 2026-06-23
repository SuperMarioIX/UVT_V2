"""
Flow validator: detects ``TC flow N: <msg>`` declarations at testcase setup
and matches them against later ``TC flow: <msg>`` validations emitted by the
running test components.

The k3 runtime emits each declared flow once at testcase startup as:
    <ts>|ulog|k3r|"DEBUG: ""TC flow 1: BTSOM initialized"

Then, while the test executes, every f_log/log("TC flow: ...") prints again as:
    <ts>|ulog|MTC=...:215|"INFO: ""TC flow: BTSOM initialized"

Per project convention (Q3, Q5):
  - presence-only matching (no strict ordering)
  - collapse declarations by message body; a declared row is "validated"
    iff the count of matching un-numbered occurrences is >= the count of
    declarations sharing the same body.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .parsing import parse_timestamp
from .tool_logger import logger


# ============================================================
# Patterns
# ============================================================

# Field-level: "DEBUG: ""<message>" or "INFO: ""<message>"
ULOG_PAYLOAD_RE = re.compile(
    r'^"(?:DEBUG|INFO|WARN|WARNING|ERROR|TRACE):\s*""(.*?)"\s*$'
)

# Declared (numbered): "TC flow 9: Minor ALARM..." or "Startup flow 1: ..."
DECLARED_FLOW_RE = re.compile(
    r'^(TC flow|Startup flow)\s+(\d+):\s*(.+?)\s*$'
)

# Validated (un-numbered): "TC flow: BTSOM initialized" or "Startup flow: ..."
VALIDATED_FLOW_RE = re.compile(
    r'^(TC flow|Startup flow):\s*(.+?)\s*$'
)


# ============================================================
# Domain model
# ============================================================
@dataclass
class FlowDeclaration:
    index: int
    kind: str           # "TC flow" or "Startup flow"
    message: str
    declared_at: datetime
    declared_at_iso: str
    raw_line: str


@dataclass
class FlowValidation:
    kind: str
    message: str
    validated_at: datetime
    validated_at_iso: str
    component: Optional[str]
    source: Optional[str]   # "<file>:<line>"
    raw_line: str


@dataclass
class FlowResult:
    index: int
    kind: str
    message: str
    declared_at: str
    validated: bool
    validation_count: int
    expected_count: int                           # number of declarations sharing this body
    first_validated_at: Optional[str] = None
    validating_component: Optional[str] = None
    validating_location: Optional[str] = None
    all_validation_ts: List[str] = field(default_factory=list)


@dataclass
class FlowReport:
    test_name: Optional[str]
    config: Optional[str]
    declared_total: int
    validated_total: int          # declared rows that came back validated
    missing_total: int
    declarations: List[FlowDeclaration]
    validations: List[FlowValidation]
    results: List[FlowResult]

    def to_jsonable(self) -> Dict[str, Any]:
        return {
            "test_name": self.test_name,
            "config": self.config,
            "summary": {
                "declared": self.declared_total,
                "validated": self.validated_total,
                "missing": self.missing_total,
                "all_validated": self.missing_total == 0,
            },
            "results": [asdict(r) for r in self.results],
            "validations": [
                {
                    "kind": v.kind,
                    "message": v.message,
                    "validated_at": v.validated_at_iso,
                    "component": v.component,
                    "source": v.source,
                }
                for v in self.validations
            ],
        }

    def to_text(self) -> str:
        lines: List[str] = []
        if self.test_name:
            lines.append(f"Test: {self.test_name}")
        if self.config:
            lines.append(f"Config: {self.config}")

        status = "OK" if self.missing_total == 0 else f"FAIL ({self.missing_total} missing)"
        lines.append(
            f"Flow validation: {self.validated_total}/{self.declared_total} validated [{status}]"
        )
        lines.append("")

        for r in self.results:
            mark = "OK " if r.validated else "MISS"
            v_at = r.first_validated_at or "<never>"
            lines.append(
                f"  [{mark}] #{r.index:>2} ({r.kind}) {r.message}"
                f" -> {v_at}  ({r.validating_component or '?'})"
            )

        return "\n".join(lines) + "\n"


# ============================================================
# Helpers
# ============================================================
def _extract_inner_message(payload_field: str) -> Optional[str]:
    """Given field like '"DEBUG: ""TC flow 1: BTSOM initialized"', return the
    inner message 'TC flow 1: BTSOM initialized'."""
    m = ULOG_PAYLOAD_RE.match(payload_field)
    if not m:
        return None
    return m.group(1)


def _split_component_source(source_field: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse e.g. 'MTC=/path/file.ttcn3:215' -> ('MTC', 'file.ttcn3:215').
    Returns ('k3r', None) for 'k3r' (no '=')."""
    src = source_field.strip()
    if not src:
        return None, None
    if "=" not in src:
        return src, None
    name, loc = src.split("=", 1)
    # Compact location to last path segment for display
    loc_compact = loc.split("/")[-1] if "/" in loc else loc
    return name.strip(), loc_compact.strip()


def _try_extract_test_name(line: str) -> Optional[str]:
    """tcst|k3r|<TestName>|<timeout>"""
    parts = line.rstrip("\n").split("|")
    if len(parts) >= 4 and parts[1].strip().lower() == "tcst":
        return parts[3].strip() or None
    return None


def _try_extract_config_from_setup(line: str) -> Optional[str]:
    """fxen|k3r=...:15|CommandFunctions.blockingCommandIO(command=
       "setupTestcaseFlow.py /var/fpwork/.../<CONFIG>/ccs_fs/...",..."""
    if "|fxen|" not in line or "setupTestcaseFlow.py" not in line:
        return None
    m = re.search(r"setupTestcaseFlow\.py\s+/[^\"']*?/([A-Z][A-Za-z0-9_]+)/ccs_fs/", line)
    if m:
        return m.group(1)
    return None


# ============================================================
# Public API
# ============================================================
def validate_flows(raw_lines: Iterable[str]) -> FlowReport:
    """Single-pass scan over raw log lines."""
    declarations: List[FlowDeclaration] = []
    validations: List[FlowValidation] = []
    test_name: Optional[str] = None
    config: Optional[str] = None

    for raw in raw_lines:
        line = raw.rstrip("\n")
        if not line:
            continue

        # Cheap guards before regex
        if test_name is None:
            tn = _try_extract_test_name(line)
            if tn:
                test_name = tn

        if config is None:
            cfg = _try_extract_config_from_setup(line)
            if cfg:
                config = cfg

        if "|ulog|" not in line:
            continue
        if "TC flow" not in line and "Startup flow" not in line:
            continue

        parts = line.split("|", 3)  # ts, mnem, source, payload(rest)
        if len(parts) < 4:
            continue

        ts_raw = parts[0].strip()
        source_raw = parts[2].strip()
        payload_raw = parts[3]  # may contain more '|' if message body has them; keep as-is

        inner = _extract_inner_message(payload_raw)
        if inner is None:
            continue

        try:
            ts = parse_timestamp(ts_raw)
        except Exception as exc:
            logger.debug("validate_flows: bad timestamp %r: %s", ts_raw, exc)
            continue

        # Try declared first (numbered) — more specific
        m_decl = DECLARED_FLOW_RE.match(inner)
        if m_decl:
            kind, idx_str, message = m_decl.group(1), m_decl.group(2), m_decl.group(3).strip()
            try:
                idx = int(idx_str)
            except ValueError:
                continue
            declarations.append(
                FlowDeclaration(
                    index=idx,
                    kind=kind,
                    message=message,
                    declared_at=ts,
                    declared_at_iso=ts.isoformat(),
                    raw_line=raw,
                )
            )
            continue

        # Otherwise, try validated (un-numbered)
        m_val = VALIDATED_FLOW_RE.match(inner)
        if m_val:
            kind, message = m_val.group(1), m_val.group(2).strip()
            comp, loc = _split_component_source(source_raw)
            validations.append(
                FlowValidation(
                    kind=kind,
                    message=message,
                    validated_at=ts,
                    validated_at_iso=ts.isoformat(),
                    component=comp,
                    source=loc,
                    raw_line=raw,
                )
            )

    # ============================================================
    # Match declarations to validations  (greedy temporal assignment)
    #
    # Previous algorithm used a count-collapse rule: a declaration was only
    # marked validated if hits >= total declarations sharing the same body.
    # That produced misleading results: when "Bts on air" is declared at #4
    # AND #19 (twice in the test), but the test crashed after iteration 1,
    # both rows were marked missing — even though #4 *was* observed.
    #
    # New rule: assign hits to declarations greedily in temporal order.
    # Each declaration claims the earliest still-unused validation of the
    # same body that occurred *after* its declaration timestamp. This
    # correctly marks #4 as validated and #19 as missing.
    # ============================================================
    decl_count_by_body: Dict[Tuple[str, str], int] = defaultdict(int)
    for d in declarations:
        decl_count_by_body[(d.kind, d.message)] += 1

    # Group validations by body, kept sorted by timestamp so we can pop
    # them in chronological order during greedy assignment.
    pool_by_body: Dict[Tuple[str, str], List[FlowValidation]] = defaultdict(list)
    for v in validations:
        pool_by_body[(v.kind, v.message)].append(v)
    for k in pool_by_body:
        pool_by_body[k].sort(key=lambda v: v.validated_at)

    # Walk declarations in their declared (== index) order. We do NOT sort
    # by declared_at because at testcase startup all declarations share the
    # same timestamp; their natural order is the index field.
    decl_in_order = sorted(declarations, key=lambda d: d.index)

    # Assignment table: declaration -> matched validation (or None)
    assigned: Dict[int, Optional[FlowValidation]] = {}

    # Track per-body cursor (next unused validation), so we don't repeatedly
    # rescan the list.
    next_idx_by_body: Dict[Tuple[str, str], int] = defaultdict(int)

    for d in decl_in_order:
        key = (d.kind, d.message)
        pool = pool_by_body.get(key, [])
        cursor = next_idx_by_body[key]
        match: Optional[FlowValidation] = None
        # Advance cursor until we find a validation that came AFTER this
        # declaration's timestamp. (Validations that fall before any
        # declaration of the same body are noise — we still skip them.)
        while cursor < len(pool):
            candidate = pool[cursor]
            cursor += 1
            if candidate.validated_at > d.declared_at:
                match = candidate
                break
        next_idx_by_body[key] = cursor
        assigned[d.index] = match

    results: List[FlowResult] = []
    validated_total = 0

    for d in declarations:
        key = (d.kind, d.message)
        expected = decl_count_by_body[key]
        all_hits_for_body = pool_by_body.get(key, [])
        my_match = assigned.get(d.index)
        is_validated = my_match is not None

        result = FlowResult(
            index=d.index,
            kind=d.kind,
            message=d.message,
            declared_at=d.declared_at_iso,
            validated=is_validated,
            validation_count=len(all_hits_for_body),
            expected_count=expected,
            first_validated_at=my_match.validated_at_iso if my_match else None,
            validating_component=my_match.component if my_match else None,
            validating_location=my_match.source if my_match else None,
            # Note: this lists ALL hits of this body across the run (not just
            # the one assigned to this row) — the UI uses this to show the
            # full sequence of when this message was observed.
            all_validation_ts=[v.validated_at_iso for v in all_hits_for_body],
        )
        results.append(result)
        if is_validated:
            validated_total += 1

    # Sort by index for stable display
    results.sort(key=lambda r: r.index)

    missing_total = len(declarations) - validated_total

    logger.info(
        "validate_flows: declared=%d validations=%d validated_rows=%d missing=%d "
        "test=%s config=%s",
        len(declarations),
        len(validations),
        validated_total,
        missing_total,
        test_name,
        config,
    )

    return FlowReport(
        test_name=test_name,
        config=config,
        declared_total=len(declarations),
        validated_total=validated_total,
        missing_total=missing_total,
        declarations=declarations,
        validations=validations,
        results=results,
    )
