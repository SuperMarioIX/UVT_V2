"""Unit tests for src.flow_validator."""

from src.flow_validator import validate_flows


# ----------------------------------------------------------------
# Helpers — synthetic lines mimicking real k3 ulog format
# ----------------------------------------------------------------
def _decl(idx: int, kind: str, msg: str, ts: str = "20260510T182247.916486") -> str:
    """A numbered declared flow (emitted by k3r at testcase startup)."""
    return f'{ts}|ulog|k3r|"DEBUG: ""{kind} {idx}: {msg}"'


def _val(msg: str, ts: str, comp: str = "MTC", level: str = "DEBUG",
         loc: str = "/path/to/file.ttcn3:215", kind: str = "TC flow") -> str:
    """An un-numbered validation emitted later by some component."""
    return f'{ts}|ulog|{comp}={loc}|"{level}: ""{kind}: {msg}"'


# ----------------------------------------------------------------
# Tests
# ----------------------------------------------------------------
def test_all_flows_validated_simple():
    lines = [
        _decl(1, "TC flow", "BTSOM initialized"),
        _decl(2, "TC flow", "Bts on air"),
        _val("BTSOM initialized", "20260510T182251.000000"),
        _val("Bts on air", "20260510T182300.000000"),
    ]
    rep = validate_flows(lines)
    assert rep.declared_total == 2
    assert rep.validated_total == 2
    assert rep.missing_total == 0
    assert all(r.validated for r in rep.results)


def test_missing_flow_is_flagged():
    lines = [
        _decl(1, "TC flow", "BTSOM initialized"),
        _decl(2, "TC flow", "Bts on air"),
        # only the first one ever validates
        _val("BTSOM initialized", "20260510T182251.000000"),
    ]
    rep = validate_flows(lines)
    assert rep.declared_total == 2
    assert rep.validated_total == 1
    assert rep.missing_total == 1
    missing = [r for r in rep.results if not r.validated]
    assert len(missing) == 1
    assert missing[0].message == "Bts on air"


def test_duplicate_message_requires_count():
    """Two declarations with the same body must have >= 2 validations to pass."""
    lines = [
        _decl(13, "TC flow", "All Cells on air"),
        _decl(21, "TC flow", "All Cells on air"),
        _val("All Cells on air", "20260510T182300.000000"),
        _val("All Cells on air", "20260510T182410.000000"),
    ]
    rep = validate_flows(lines)
    assert rep.declared_total == 2
    assert rep.validated_total == 2  # both rows validated by count rule


def test_duplicate_message_one_hit_validates_first_only():
    """Greedy temporal assignment: with 2 declarations of the same body
    and only 1 validation, the FIRST declaration claims the hit (green),
    the SECOND remains missing (red). This is the typical pattern when a
    test crashes mid-iteration: iteration 1 produced the validation,
    iteration 2 never reached it."""
    lines = [
        _decl(13, "TC flow", "All Cells on air"),
        _decl(21, "TC flow", "All Cells on air"),
        _val("All Cells on air", "20260510T182300.000000"),  # only ONE hit
    ]
    rep = validate_flows(lines)
    assert rep.declared_total == 2
    assert rep.validated_total == 1   # first one got the hit
    assert rep.missing_total == 1     # second one didn't

    by_idx = {r.index: r for r in rep.results}
    assert by_idx[13].validated is True
    assert by_idx[13].first_validated_at is not None
    assert by_idx[21].validated is False
    assert by_idx[21].first_validated_at is None


def test_duplicate_message_two_hits_validate_both():
    """Both declarations get a hit each."""
    lines = [
        _decl(13, "TC flow", "All Cells on air"),
        _decl(21, "TC flow", "All Cells on air"),
        _val("All Cells on air", "20260510T182300.000000"),
        _val("All Cells on air", "20260510T182410.000000"),
    ]
    rep = validate_flows(lines)
    assert rep.validated_total == 2
    by_idx = {r.index: r for r in rep.results}
    # Greedy: earliest declaration gets earliest hit
    assert by_idx[13].first_validated_at == "2026-05-10T18:23:00"
    assert by_idx[21].first_validated_at == "2026-05-10T18:24:10"


def test_duplicate_message_three_hits_two_decls():
    """If hits exceed declarations, only the first N hits (in order) are
    assigned; the rest remain in all_validation_ts but are not "claimed"
    by any specific row."""
    lines = [
        _decl(9,  "TC flow", "Minor ALARM 7115 for fault 1911 has been created"),
        _decl(15, "TC flow", "Minor ALARM 7115 for fault 1911 has been created"),
        _val("Minor ALARM 7115 for fault 1911 has been created", "20260510T182300.000000"),
        _val("Minor ALARM 7115 for fault 1911 has been created", "20260510T182315.000000"),
        _val("Minor ALARM 7115 for fault 1911 has been created", "20260510T182410.000000"),
        _val("Minor ALARM 7115 for fault 1911 has been created", "20260510T182425.000000"),
    ]
    rep = validate_flows(lines)
    assert rep.validated_total == 2
    by_idx = {r.index: r for r in rep.results}
    # validation_count reports total hits for the body, not just claimed
    assert by_idx[9].validation_count == 4
    assert by_idx[15].validation_count == 4
    # but each row only gets ITS OWN matched validation timestamp
    assert by_idx[9].first_validated_at == "2026-05-10T18:23:00"
    assert by_idx[15].first_validated_at == "2026-05-10T18:23:15"


def test_startup_flow_is_recognized():
    lines = [
        _decl(1, "Startup flow", "NRBTS OperationalState changed to 'onAir'"),
        _val("NRBTS OperationalState changed to 'onAir'",
             "20260510T182300.000000",
             kind="Startup flow"),
    ]
    rep = validate_flows(lines)
    assert rep.declared_total == 1
    assert rep.validated_total == 1
    assert rep.results[0].kind == "Startup flow"


def test_ignores_unrelated_tc_flow_messages():
    """'TC flow: blockingCommandIO rc 0' is debug noise, not a validation."""
    lines = [
        _decl(1, "TC flow", "BTSOM initialized"),
        # noise line that starts with 'TC flow:' but doesn't match any declared body
        _val("blockingCommandIO rc 0", "20260510T182251.000000"),
        # actual validation
        _val("BTSOM initialized", "20260510T182252.000000"),
    ]
    rep = validate_flows(lines)
    assert rep.declared_total == 1
    assert rep.validated_total == 1
    # the noise validation should be present in the validations list, but
    # not affect our declared rows
    assert any(v.message == "blockingCommandIO rc 0" for v in rep.validations)


def test_extracts_test_name_from_tcst():
    lines = [
        "20260510T182247.921254|tcst|k3r|SBTS_N_Fault1911ForAffectedPipeAndRfRecovery.test|340.0",
        _decl(1, "TC flow", "BTSOM initialized"),
        _val("BTSOM initialized", "20260510T182251.000000"),
    ]
    rep = validate_flows(lines)
    assert rep.test_name == "SBTS_N_Fault1911ForAffectedPipeAndRfRecovery.test"


def test_extracts_config_from_setup_command():
    lines = [
        '20260510T182247.843538|fxen|k3r=/some/path/TestcaseFlow.ttcn3:15|'
        'CommandFunctions.blockingCommandIO(command='
        '"setupTestcaseFlow.py /var/fpwork/foo/bar/SBTS_N_ASIK_ABIL_AEQV/ccs_fs/0x1011/rom/config/SCFC_1.xml",'
        'input={"x"},output=-)',
        _decl(1, "TC flow", "BTSOM initialized"),
        _val("BTSOM initialized", "20260510T182251.000000"),
    ]
    rep = validate_flows(lines)
    assert rep.config == "SBTS_N_ASIK_ABIL_AEQV"


def test_jsonable_shape():
    lines = [
        _decl(1, "TC flow", "BTSOM initialized"),
        _val("BTSOM initialized", "20260510T182251.000000",
             comp="MTC", loc="/abs/path/MBtsomMtcSetup.ttcn3:215"),
    ]
    rep = validate_flows(lines)
    j = rep.to_jsonable()
    assert j["summary"]["all_validated"] is True
    assert j["summary"]["declared"] == 1
    r = j["results"][0]
    assert r["validating_component"] == "MTC"
    assert r["validating_location"] == "MBtsomMtcSetup.ttcn3:215"
    assert r["expected_count"] == 1
    assert r["validation_count"] == 1
