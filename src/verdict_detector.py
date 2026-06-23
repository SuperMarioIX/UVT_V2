"""
Verdict failure detector. Looks at three independent signals:

1. ``tcfi|k3r|<test_name>|<global_verdict>`` — the test's final verdict.
2. ``cofi|<component>|<verdict>``           — per-component finish verdict.
3. ``setv|<component>=<src>|<old>|<new>``   — verdict transitions during run.

Anything that ends up with ``fail``/``inconc``/``error`` is reported as an
issue. ``none`` is *not* a failure (it just means the verdict was never set,
which is normal for passive simulator components).

The output is intentionally additive: it never removes events from the
existing per-component history, it only computes a summary on top of the raw
log (and the registry that ``engine.process_events`` already built).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .parsing import parse_timestamp
from .tool_logger import logger


# ============================================================
# Constants / classification
# ============================================================
PASS_VERDICTS = {"pass", "none"}
FAIL_VERDICTS = {"fail", "inconc", "error"}

SEVERITY_BY_KIND = {
    "global_verdict_fail":     "CRITICAL",
    "component_verdict_fail":  "CRITICAL",
    "verdict_regression":      "CRITICAL",
    "verdict_transition_bad":  "HIGH",   # setv ... -> inconc/error (non-fail bad)
}


# ============================================================
# Data model
# ============================================================
@dataclass
class VerdictTransition:
    ts: str
    component: str
    from_verdict: str
    to_verdict: str
    source: Optional[str] = None
    raw_line: Optional[str] = None


@dataclass
class Issue:
    severity: str
    kind: str
    ts: str
    message: str
    component: Optional[str] = None
    source: Optional[str] = None
    from_verdict: Optional[str] = None
    to_verdict: Optional[str] = None
    test_name: Optional[str] = None


@dataclass
class VerdictReport:
    test_name: Optional[str]
    global_verdict: Optional[str]
    tcfi_at: Optional[str]
    component_verdicts: Dict[str, str]            # last-known verdict per component
    transitions: List[VerdictTransition] = field(default_factory=list)
    issues: List[Issue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.global_verdict == "pass" and not self.issues

    def to_jsonable(self) -> Dict[str, Any]:
        return {
            "test_name": self.test_name,
            "global_verdict": self.global_verdict,
            "tcfi_at": self.tcfi_at,
            "passed": self.passed,
            "summary": {
                "total_issues": len(self.issues),
                "by_severity": _count_by(self.issues, lambda i: i.severity),
                "by_kind": _count_by(self.issues, lambda i: i.kind),
            },
            "component_verdicts": self.component_verdicts,
            "transitions": [asdict(t) for t in self.transitions],
            "issues": [asdict(i) for i in self.issues],
        }

    def to_text(self) -> str:
        lines: List[str] = []
        lines.append(f"Test: {self.test_name or '<unknown>'}")
        lines.append(f"Global verdict: {self.global_verdict or '<missing>'}")
        lines.append(f"Issues: {len(self.issues)}")
        lines.append("")

        if not self.issues:
            lines.append("  <no verdict-related issues detected>")
            return "\n".join(lines) + "\n"

        for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            bucket = [i for i in self.issues if i.severity == sev]
            if not bucket:
                continue
            lines.append(f"[{sev}] ({len(bucket)})")
            for i in bucket:
                comp = f" {i.component}" if i.component else ""
                src = f" @ {i.source}" if i.source else ""
                lines.append(f"  {i.ts}{comp}{src}")
                lines.append(f"    {i.message}")
            lines.append("")

        return "\n".join(lines) + "\n"


def _count_by(items: Iterable[Any], key) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for it in items:
        k = key(it)
        out[k] = out.get(k, 0) + 1
    return out


# ============================================================
# Helpers
# ============================================================
def _split_component_source(source_field: str) -> Tuple[Optional[str], Optional[str]]:
    src = source_field.strip()
    if not src:
        return None, None
    if "=" not in src:
        return src, None
    name, loc = src.split("=", 1)
    loc_compact = loc.split("/")[-1] if "/" in loc else loc
    return name.strip(), loc_compact.strip()


def _normalize_verdict(v: Optional[str]) -> str:
    return (v or "").strip().lower()


def _is_fail(v: str) -> bool:
    return v in FAIL_VERDICTS


# ============================================================
# Single-pass scan over raw lines
# ============================================================
def _scan_raw(raw_lines: Iterable[str]) -> Tuple[
    Optional[str], Optional[str], Optional[str], List[VerdictTransition]
]:
    """Return (test_name, global_verdict, tcfi_at_iso, transitions_from_setv)."""
    test_name: Optional[str] = None
    global_verdict: Optional[str] = None
    tcfi_at_iso: Optional[str] = None
    transitions: List[VerdictTransition] = []

    for raw in raw_lines:
        line = raw.rstrip("\n")
        if not line:
            continue
        if "|tcfi|" not in line and "|setv|" not in line:
            continue

        parts = line.split("|")
        if len(parts) < 5:
            continue

        ts_raw = parts[0].strip()
        mnem = parts[1].strip().lower()

        try:
            ts = parse_timestamp(ts_raw)
        except Exception:
            continue

        if mnem == "tcfi":
            # tcfi|k3r|<TestName>|<verdict>
            if test_name is None:
                test_name = parts[3].strip() or None
            global_verdict = _normalize_verdict(parts[4]) or None
            tcfi_at_iso = ts.isoformat()

        elif mnem == "setv":
            # setv|<COMP>=<src>|<old>|<new>
            comp, loc = _split_component_source(parts[2])
            old_v = _normalize_verdict(parts[3])
            new_v = _normalize_verdict(parts[4])
            if not comp:
                continue
            transitions.append(
                VerdictTransition(
                    ts=ts.isoformat(),
                    component=comp,
                    from_verdict=old_v,
                    to_verdict=new_v,
                    source=loc,
                    raw_line=raw,
                )
            )

    return test_name, global_verdict, tcfi_at_iso, transitions


# ============================================================
# Public API
# ============================================================
def detect_verdict_issues(
    raw_lines: Iterable[str],
    component_verdicts: Optional[Dict[str, str]] = None,
) -> VerdictReport:
    """
    Build the verdict report.

    Args:
        raw_lines: raw log lines (will be iterated once).
        component_verdicts: optional map ``component -> last_verdict`` derived
            from the registry (the engine already extracts ``cofi`` verdicts).
            If ``None``, this function will *also* scan ``cofi`` lines from
            ``raw_lines`` to build that map itself.
    """
    # Materialize once if it's a generator
    if not isinstance(raw_lines, list):
        raw_lines = list(raw_lines)

    # 1. Scan tcfi + setv
    test_name, global_verdict, tcfi_at_iso, transitions = _scan_raw(raw_lines)

    # 2. Component verdicts: prefer caller-provided (from registry), else scan cofi
    if component_verdicts is None:
        component_verdicts = _scan_cofi(raw_lines)

    # ============================================================
    # 3. Build issues
    # ============================================================
    issues: List[Issue] = []

    # 3.1 Global verdict
    if global_verdict is None:
        # No tcfi at all -> probably crashed before finish; still an issue
        issues.append(
            Issue(
                severity="CRITICAL",
                kind="missing_tcfi",
                ts="",
                message="Testcase did not emit a final 'tcfi' verdict line "
                        "(possible crash or aborted run).",
                test_name=test_name,
            )
        )
    elif global_verdict not in PASS_VERDICTS:
        issues.append(
            Issue(
                severity="CRITICAL",
                kind="global_verdict_fail",
                ts=tcfi_at_iso or "",
                message=f"Testcase finished with verdict '{global_verdict}'.",
                test_name=test_name,
                to_verdict=global_verdict,
            )
        )

    # 3.2 Per-component verdict
    for comp, verdict in sorted(component_verdicts.items()):
        v = _normalize_verdict(verdict)
        if v in PASS_VERDICTS:
            continue
        issues.append(
            Issue(
                severity=SEVERITY_BY_KIND["component_verdict_fail"],
                kind="component_verdict_fail",
                ts="",
                component=comp,
                message=f"Component {comp} finished with verdict '{v}'.",
                to_verdict=v,
                test_name=test_name,
            )
        )

    # 3.3 setv regressions / bad transitions
    for t in transitions:
        old_v = _normalize_verdict(t.from_verdict)
        new_v = _normalize_verdict(t.to_verdict)
        if new_v in PASS_VERDICTS:
            continue  # going to pass/none is fine
        if old_v == new_v:
            continue  # no actual regression (e.g. fail -> fail)

        if _is_fail(new_v) and old_v in PASS_VERDICTS:
            issues.append(
                Issue(
                    severity=SEVERITY_BY_KIND["verdict_regression"],
                    kind="verdict_regression",
                    ts=t.ts,
                    component=t.component,
                    source=t.source,
                    from_verdict=old_v or None,
                    to_verdict=new_v,
                    message=f"Verdict regressed from '{old_v or 'none'}' to '{new_v}'.",
                    test_name=test_name,
                )
            )
        elif _is_fail(new_v):
            # Was already in a non-pass state, this is a transition between bad states
            issues.append(
                Issue(
                    severity=SEVERITY_BY_KIND["verdict_transition_bad"],
                    kind="verdict_transition_bad",
                    ts=t.ts,
                    component=t.component,
                    source=t.source,
                    from_verdict=old_v or None,
                    to_verdict=new_v,
                    message=f"Verdict transition '{old_v}' -> '{new_v}' (both non-pass).",
                    test_name=test_name,
                )
            )

    # Stable order
    issues.sort(key=lambda i: (i.ts or "", i.kind, i.component or ""))

    logger.info(
        "detect_verdict_issues: test=%s global=%s issues=%d transitions=%d "
        "component_verdicts=%d",
        test_name,
        global_verdict,
        len(issues),
        len(transitions),
        len(component_verdicts),
    )

    return VerdictReport(
        test_name=test_name,
        global_verdict=global_verdict,
        tcfi_at=tcfi_at_iso,
        component_verdicts=component_verdicts,
        transitions=transitions,
        issues=issues,
    )


def _scan_cofi(raw_lines: Iterable[str]) -> Dict[str, str]:
    """Extract last-known verdict per component from raw cofi lines.

    Used when the caller doesn't pass a registry-derived map.
    """
    out: Dict[str, str] = {}
    for raw in raw_lines:
        line = raw.rstrip("\n")
        if "|cofi|" not in line:
            continue
        parts = line.split("|")
        if len(parts) < 4:
            continue
        comp = parts[2].split("=")[0].strip()
        verdict = _normalize_verdict(parts[3])
        if comp:
            out[comp] = verdict  # last-write-wins
    return out


def component_verdicts_from_registry(registry) -> Dict[str, str]:
    """Build component -> last verdict from a `ComponentRegistry`."""
    out: Dict[str, str] = {}
    for hist in registry.histories():
        last = hist.snapshots[-1] if hist.snapshots else None
        if last is None:
            continue
        v = last.verdict
        if v is None:
            continue
        out[hist.component_id] = _normalize_verdict(v)
    return out
