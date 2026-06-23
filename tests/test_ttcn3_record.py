# tests/test_ttcn_record.py

from src.ttcn3_record_parser import parse_ttcn_record, prune_placeholders


def test_simple_record_two_fields():
    text = '{a:=1,b:="x"}'
    parsed = parse_ttcn_record(text)
    assert isinstance(parsed, dict)
    assert parsed == {"a": 1, "b": "x"}


def test_nested_record():
    text = '{outer:={inner:={x:=42,y:="ok"}}}'
    parsed = parse_ttcn_record(text)
    assert parsed["outer"]["inner"]["x"] == 42
    assert parsed["outer"]["inner"]["y"] == "ok"


def test_list_record():
    text = '{1,2,3}'
    parsed = parse_ttcn_record(text)
    assert isinstance(parsed, list)
    assert parsed == [1, 2, 3]


def test_placeholders_not_pruned_in_parser_only_in_prune():
    text = '{a:=?,b:=omit,c:=*}'
    parsed = parse_ttcn_record(text)
    assert parsed["a"] == "?"
    assert parsed["b"] == "omit"
    assert parsed["c"] == "*"

    pruned = prune_placeholders(parsed)
    assert pruned is None  # everything was placeholder → empty dict → None


def test_prune_mixed_fields():
    text = '{a:=?,b:=1,c:="x",d:=omit,e:={x:=?,y:=2}}'
    parsed = parse_ttcn_record(text)
    pruned = prune_placeholders(parsed)
    # 'a' and 'd' should be gone; e.x is placeholder so removed, e.y kept
    assert pruned == {
        "b": 1,
        "c": "x",
        "e": {"y": 2},
    }


def test_complex_sample_similar_to_special_comp_a():
    text = (
        '{dn:="/A-1/B-1/C-1/D_M-1/E_M-1/F_M-1/G_M-0",'
        'x:=omit,'
        'obj:={x:=?,object:={g_m:={i:={o:=enable,a:=?,a1:=on,b:=?,r:=?,a2:=?,ps:=a_done,rs:=*,pbl:=?},'
        'ros:=?,dc:=?,bcs:=?,cc:=?,c1s:=?,s:=?,r2:=?,rs2:={state:=?,bsp:=?,ms:=?,r4:=?},'
        'sbs:=*,is:=?,eco:=?,ics:=?,ac:=*,sc:=*,f:=?}}},'
        'a6:=omit}'
    )

    parsed = parse_ttcn_record(text)
    # Basic sanity
    assert parsed["dn"] == "/A-1/B-1/C-1/D_M-1/E_M-1/F_M-1/G_M-0"
    assert "obj" in parsed

    pruned = prune_placeholders(parsed)
    # 'x' and 'a6' should be removed
    assert "x" not in pruned
    assert "a6" not in pruned

    # Check that we still have a g_m.i subtree and only meaningful fields
    g_m = (
        pruned
        .get("obj", {})
        .get("object", {})
        .get("g_m", {})
    )
    assert "i" in g_m
    i = g_m["i"]
    # Only these three should remain
    assert i["o"] == "enable"
    assert i["a1"] == "on"
    assert i["ps"] == "a_done"
    # There should not be any placeholder fields left
    assert "a" not in i
    assert "b" not in i
    assert "r" not in i
    assert "a2" not in i
    assert "rs" not in i
    assert "pbl" not in i


def test_boolean_and_numbers():
    text = '{flag:=true,count:=10,ratio:=1.5}'
    parsed = parse_ttcn_record(text)
    assert parsed["flag"] is True
    assert parsed["count"] == 10
    # depending on conversion, ratio may be float
    assert parsed["ratio"] == 1.5
