"""Unit tests for src.verdict_detector."""

from src.verdict_detector import detect_verdict_issues


# ----------------------------------------------------------------
# Helpers — synthetic lines mimicking real k3 log format
# ----------------------------------------------------------------
def _tcfi(verdict: str, name: str = "Foo.test", ts: str = "20260510T182418.435065") -> str:
    return f"{ts}|tcfi|k3r|{name}|{verdict}"


def _setv(comp: str, old: str, new: str, ts: str = "20260510T182300.000000",
          loc: str = "/path/file.ttcn3:42") -> str:
    return f"{ts}|setv|{comp}={loc}|{old}|{new}"


def _cofi(comp: str, verdict: str, ts: str = "20260510T182418.000000") -> str:
    return f"{ts}|cofi|{comp}|{verdict}"


# ----------------------------------------------------------------
# Tests
# ----------------------------------------------------------------
def test_pass_log_has_no_issues():
    lines = [
        _setv("MTC", "none", "pass"),
        _setv("NOKIA_CPRI_RADIO_1", "none", "pass"),
        _setv("NOKIA_CPRI_RADIO_1", "pass", "pass"),
        _cofi("MTC", "pass"),
        _cofi("NOKIA_CPRI_RADIO_1", "pass"),
        _tcfi("pass"),
    ]
    rep = detect_verdict_issues(lines, component_verdicts={"MTC": "pass", "NOKIA_CPRI_RADIO_1": "pass"})
    assert rep.global_verdict == "pass"
    assert rep.passed
    assert rep.issues == []
    # transitions are still recorded for the UI timeline
    assert len(rep.transitions) == 3


def test_global_fail_is_critical_issue():
    lines = [_tcfi("fail")]
    rep = detect_verdict_issues(lines, component_verdicts={})
    assert rep.global_verdict == "fail"
    assert not rep.passed
    crit = [i for i in rep.issues if i.kind == "global_verdict_fail"]
    assert len(crit) == 1
    assert crit[0].severity == "CRITICAL"
    assert crit[0].to_verdict == "fail"


def test_component_fail_is_reported():
    lines = [_tcfi("pass")]
    rep = detect_verdict_issues(
        lines,
        component_verdicts={"NOKIA_CPRI_RADIO_1": "fail", "MTC": "pass"},
    )
    comp_fails = [i for i in rep.issues if i.kind == "component_verdict_fail"]
    assert len(comp_fails) == 1
    assert comp_fails[0].component == "NOKIA_CPRI_RADIO_1"
    assert comp_fails[0].severity == "CRITICAL"


def test_setv_regression_is_critical():
    lines = [
        _setv("BM_IMS_CLIENT", "pass", "fail",
              ts="20260510T182300.000000",
              loc="/abs/path/StartupTestsCommon.ttcn3:428"),
        _tcfi("fail"),
    ]
    rep = detect_verdict_issues(lines, component_verdicts={"BM_IMS_CLIENT": "fail"})
    regressions = [i for i in rep.issues if i.kind == "verdict_regression"]
    assert len(regressions) == 1
    r = regressions[0]
    assert r.component == "BM_IMS_CLIENT"
    assert r.from_verdict == "pass"
    assert r.to_verdict == "fail"
    assert r.source == "StartupTestsCommon.ttcn3:428"
    assert r.severity == "CRITICAL"


def test_setv_none_to_pass_is_not_an_issue():
    """`setverdict(pass)` from a never-set state must NOT trigger anything."""
    lines = [
        _setv("MTC", "none", "pass"),
        _setv("NOKIA_CPRI_RADIO_1", "none", "pass"),
        _tcfi("pass"),
    ]
    rep = detect_verdict_issues(lines, component_verdicts={})
    assert rep.issues == []


def test_missing_tcfi_is_critical():
    """A log without a `tcfi` line means the test never finished."""
    lines = [
        _setv("MTC", "none", "pass"),
        # no tcfi at all
    ]
    rep = detect_verdict_issues(lines, component_verdicts={})
    assert rep.global_verdict is None
    missing = [i for i in rep.issues if i.kind == "missing_tcfi"]
    assert len(missing) == 1
    assert missing[0].severity == "CRITICAL"


def test_inconc_global_is_failure():
    lines = [_tcfi("inconc")]
    rep = detect_verdict_issues(lines, component_verdicts={})
    assert not rep.passed
    assert rep.global_verdict == "inconc"
    fails = [i for i in rep.issues if i.kind == "global_verdict_fail"]
    assert len(fails) == 1


def test_pass_to_pass_not_reported():
    """Reaffirming pass is a no-op."""
    lines = [
        _setv("X", "pass", "pass"),
        _tcfi("pass"),
    ]
    rep = detect_verdict_issues(lines, component_verdicts={})
    assert rep.issues == []


def test_fallback_cofi_scan_when_registry_not_provided():
    """When no registry-derived map is given, must scan cofi lines from raw."""
    lines = [
        _cofi("MTC", "pass"),
        _cofi("BM_IMS_CLIENT", "fail"),
        _tcfi("fail"),
    ]
    rep = detect_verdict_issues(lines, component_verdicts=None)
    comp_fails = [i for i in rep.issues if i.kind == "component_verdict_fail"]
    assert len(comp_fails) == 1
    assert comp_fails[0].component == "BM_IMS_CLIENT"


def test_jsonable_shape():
    lines = [
        _setv("X", "pass", "fail"),
        _tcfi("fail"),
    ]
    rep = detect_verdict_issues(lines, component_verdicts={"X": "fail"})
    j = rep.to_jsonable()
    assert j["passed"] is False
    assert j["global_verdict"] == "fail"
    assert j["summary"]["total_issues"] >= 2  # global + regression + component
    assert "by_severity" in j["summary"]
    assert "by_kind" in j["summary"]
