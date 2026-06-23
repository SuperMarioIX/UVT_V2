"""Unit tests for src.log_warnings."""

from src.log_warnings import scan_log_warnings, DEFAULT_WHITELIST


# ----------------------------------------------------------------
# Helpers — synthetic lines mimicking real k3 pllg format
# ----------------------------------------------------------------
def _pllg(level: str, message: str, component: str = "NETACT",
          loc: str = "/abs/path/MNetact.ttcn3:100",
          module: str = "RestConnector #0",
          ts: str = "20260510T182250.129664",
          hexcode: str = "00c") -> str:
    return f"{ts}|pllg|{component}={loc}|{hexcode}|{level}|{module}|{message}"


# ----------------------------------------------------------------
# Tests
# ----------------------------------------------------------------
def test_inf_lines_are_dropped():
    lines = [
        _pllg("INF", "Some info noise"),
        _pllg("INFO", "Verbose"),
        _pllg("DBG", "Debug stuff"),
    ]
    rep = scan_log_warnings(lines, whitelist=())
    assert rep.total_seen == 0
    assert rep.items == []


def test_wrn_line_kept():
    lines = [_pllg("WRN", "Mismatch detected at runtime")]
    rep = scan_log_warnings(lines, whitelist=())
    assert rep.total_seen == 1
    assert len(rep.items) == 1
    w = rep.items[0]
    assert w.level == "WRN"
    assert w.component == "NETACT"
    assert w.module == "RestConnector #0"
    assert w.message == "Mismatch detected at runtime"
    assert w.count == 1
    assert w.sample_source == "MNetact.ttcn3:100"


def test_err_and_fatal_kept_too():
    lines = [
        _pllg("ERR", "Something broke"),
        _pllg("ERROR", "Oops"),
        _pllg("FATAL", "Goodbye world"),
    ]
    rep = scan_log_warnings(lines, whitelist=())
    assert rep.total_seen == 3
    levels = {w.level for w in rep.items}
    assert levels == {"ERR", "ERROR", "FATAL"}


def test_duplicates_collapse_to_count_with_first_last():
    lines = [
        _pllg("WRN", "Same warning",
              ts="20260510T182250.000000"),
        _pllg("WRN", "Same warning",
              ts="20260510T182300.000000"),
        _pllg("WRN", "Same warning",
              ts="20260510T182310.000000"),
    ]
    rep = scan_log_warnings(lines, whitelist=())
    assert len(rep.items) == 1
    w = rep.items[0]
    assert w.count == 3
    assert w.first_ts == "2026-05-10T18:22:50"
    assert w.last_ts == "2026-05-10T18:23:10"


def test_whitelist_suppresses_noise():
    lines = [
        _pllg("WRN", "Use of deprecated contextName parameter"),
        _pllg("WRN", "Use of deprecated contextName parameter"),
        _pllg("WRN", "Real new warning"),
    ]
    rep = scan_log_warnings(lines)  # default whitelist
    assert rep.total_seen == 3
    assert rep.total_kept == 1
    assert len(rep.items) == 1
    assert rep.items[0].message == "Real new warning"
    assert rep.whitelist_hits.get("Use of deprecated contextName parameter") == 2


def test_default_whitelist_known_patterns_match():
    """Real-log spam patterns from pit_oam_K3.log must be suppressed."""
    spam_lines = [
        _pllg("WRN", "ID_7003 in encoding-helper.cpp:28|default int size 4",
              component="HWCTRL", module="Binary-Codec2"),
        _pllg("WRN", "ID_5096 in routes-semaphore-set.hpp:68|no entry for address.",
              component="?", module="SicSharedConnector"),
    ]
    rep = scan_log_warnings(spam_lines)
    assert rep.total_seen == 2
    assert rep.total_kept == 0  # both suppressed
    assert len(rep.items) == 0


def test_unknown_component_marked_as_none():
    line = "20260510T182418.389916|pllg|?|028|WRN|SicSharedConnector|cleanup msg"
    rep = scan_log_warnings([line], whitelist=())
    assert len(rep.items) == 1
    assert rep.items[0].component is None
    assert rep.items[0].sample_source is None


def test_max_buckets_caps_output():
    lines = [
        _pllg("WRN", f"unique-{i}", component=f"C{i}")
        for i in range(50)
    ]
    rep = scan_log_warnings(lines, whitelist=(), max_buckets=10)
    assert rep.total_seen == 50
    assert len(rep.items) == 10  # capped


def test_items_sorted_by_count_descending():
    lines = (
        [_pllg("WRN", "rare")]
        + [_pllg("WRN", "common") for _ in range(5)]
        + [_pllg("WRN", "medium") for _ in range(2)]
    )
    rep = scan_log_warnings(lines, whitelist=())
    assert [w.message for w in rep.items] == ["common", "medium", "rare"]
    assert rep.items[0].count == 5


def test_jsonable_shape():
    lines = [_pllg("WRN", "x")]
    rep = scan_log_warnings(lines, whitelist=())
    j = rep.to_jsonable()
    assert j["summary"]["total_seen"] == 1
    assert j["summary"]["total_kept"] == 1
    assert j["summary"]["by_level"] == {"WRN": 1}
    assert len(j["items"]) == 1
    assert j["items"][0]["level"] == "WRN"


def test_messages_with_pipes_are_preserved():
    """Messages can contain '|' characters; we must keep them."""
    line = "20260510T182250.000000|pllg|X=/p:1|001|WRN|Mod|some|text|with|pipes"
    rep = scan_log_warnings([line], whitelist=())
    assert len(rep.items) == 1
    assert rep.items[0].message == "some|text|with|pipes"
