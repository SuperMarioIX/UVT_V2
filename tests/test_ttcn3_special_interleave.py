# tests/test_special_interleaved.py

from datetime import datetime
from pathlib import Path
from src.tool_logger import logger
from src.ttcn3_special_interleave import (
    parse_interleaved_ulog_line,
    build_interleaved_overview,
    render_interleaved_overview_text,
)

#DANI: this can be improved, it's quite basic test
def test_parse_interleaved_ulog_line_start_and_match():
    # Locate test input file relative to THIS test file
    test_dir = Path(__file__).parent
    input_file = test_dir / "tests_raw_inputs" / "test_ttcn3_special_interleave_input.log"

    lines = input_file.read_text(encoding="utf-8").splitlines()

    assert len(lines) >= 2

    raw1 = lines[0]
    raw2 = lines[1]
    raw3 = lines[2]

    ev1 = parse_interleaved_ulog_line(raw1, logger)
    ev2 = parse_interleaved_ulog_line(raw2, logger)
    ev1_matching = parse_interleaved_ulog_line(raw3, logger)

    assert ev1 is not None
    assert ev2 is not None

    assert ev1.index == 0
    assert ev1.status == "started"
    assert ev1.dn == "/MRBTS-1/RAT-1/RUNTIME_VIEW-[0-9]+/MRBTS_R-[0-9]+/NRBTS_R-[0-9]+"
    # payload should be pruned to meaningful fields
    assert "nrbts_r" in ev1.payload_summary
    assert "operationalState:=EOperationalState_onAir" in ev1.payload_summary

    assert ev2.index == 1
    assert ev2.status == "started"
    assert ev2.dn == "/MRBTS-1/RAT-1/RUNTIME_VIEW-[0-9]+/MRBTS_R-[0-9]+/LNBTS_R-[0-9]+"
    assert "lnbts_r" in ev2.payload_summary
    assert "operationalState:=EOperationalState_onAir" in ev2.payload_summary

    assert ev1_matching.index == 0
    assert ev1_matching.status == "matched"
    assert ev1_matching.dn == ev1.dn


def test_build_overview_started_matched_not_matched():
    # Two starts (0, 1) and one match (0) -> 1 is "not matched"
    raw_start_0 = (
        '20251110T172845.160613|ulog|BM_IMS_CLIENT=/path/...:88|'
        '"DEBUG: ""[IM::expectation::interleaved] start. expected aEvents[0]"'
        '{dn:="/path/G_M-0",x:=omit,obj:={x:=?,object:={g_m:={i:={o:=enable,a1:=on,ps:=a_done}}}},a6:=omit}'
    )
    raw_start_1 = (
        '20251110T172845.160613|ulog|BM_IMS_CLIENT=/path/...:88|'
        '"DEBUG: ""[IM::expectation::interleaved] start. expected aEvents[1]"'
        '{dn:="/path/G_M-1",x:=omit,obj:={x:=?,object:={g_m:={i:={o:=enable,a1:=on,ps:=a_done}}}},a6:=omit}'
    )
    raw_match_0 = (
        '20251110T172845.246326|ulog|BM_IMS_CLIENT=/path/...:99|'
        '"DEBUG: ""[IM::expectation::interleaved] event matched. aEvents[0]"'
        '{dn:="/path/G_M-0",x:=omit,obj:={x:=?,object:={g_m:={i:={o:=enable,a1:=on,ps:=a_done}}}},a6:=omit}'
    )

    evs = [
        parse_interleaved_ulog_line(raw_start_0, logger),
        parse_interleaved_ulog_line(raw_start_1, logger),
        parse_interleaved_ulog_line(raw_match_0, logger),
    ]
    evs = [e for e in evs if e is not None]

    summary = build_interleaved_overview(evs)

    started = summary["expected_interleaves_started"]
    matched = summary["expected_interleaves_matched"]
    not_matched = summary["expected_interleaves_not_matched"]

    assert {e["index"] for e in started} == {0, 1}
    assert {e["index"] for e in matched} == {0}
    assert {e["index"] for e in not_matched} == {1}

    # render text and do some basic sanity checks
    text = render_interleaved_overview_text(summary)
    assert "BM_IMS_CLIENT" in text
    assert "expected interleaves started" in text
    assert "expected interleaves matched" in text
    assert "expected interleaves not matched" in text
    assert "Event1:" in text  # index 1 appears as not matched
