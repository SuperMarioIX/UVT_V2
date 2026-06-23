# tests/test_overview.py

from src.engine import process_events
from src.overview import build_overview_profiling
from src.models import ComponentRegistry


def test_overview_component_log_density_and_repeated_messages():
    lines = [
        # two queued messages, one consumed
        "20251110T172810.820721|ptqu|?|LOM.mLomPort|message(value=SackInterface.SAaSysMbPublReplyMsg:{msgParams:={agent:=61455,operation:=1,channel:=0,zone:=15,sicad:=269554178,msgId:=13812,size:=0,reserved:=0},status:=0},sender=MLom.address:269551107,timestamp=2147483647.000000)",
        "20251110T172810.827067|ptqu|?|LOM.mLomPort|message(value=SackInterface.SAaSysMbPublReplyMsg:{msgParams:={agent:=61455,operation:=1,channel:=0,zone:=15,sicad:=269554178,msgId:=13812,size:=0,reserved:=0},status:=0},sender=MLom.address:269551107,timestamp=2147483647.000000)",
        "20251110T172810.829717|ptrx|LOM=/home/k3/K3_ROOT/C_Test/OM_K3/src/common/components/MLom.ttcn3:184|LOM.mLomPort|value=SackInterface.SAaSysMbPublReplyMsg:?|ready(match=message(value=SackInterface.SAaSysMbPublReplyMsg:{msgParams:={agent:=61455,operation:=1,channel:=0,zone:=15,sicad:=269554178,msgId:=13812,size:=0,reserved:=0},status:=0},sender=MLom.address:269551107,timestamp=11.277963))+consume",
    ]
    registry, skipped = process_events(lines, strict=True)
    assert skipped == 0

    profiling = build_overview_profiling(registry)
    assert "component_log_density" in profiling
    assert "messages_that_repeat_the_most" in profiling
    assert "ports_with_messages_blocked_on_queue" in profiling

    # Check repeated messages
    msgs = profiling["messages_that_repeat_the_most"]
    names = {m["message"] for m in msgs}
    assert "SackInterface.SAaSysMbPublReplyMsg" in names

    # Check blocked queue: one message should remain queued
    blocked = profiling["ports_with_messages_blocked_on_queue"]
    # exact key: "LOM.mLomPort"
    assert "LOM.mLomPort" in blocked
    blocked_msgs = blocked["LOM.mLomPort"]
    # Something like "SackInterface.SAaSysMbPublReplyMsg" or "SackInterface.SAaSysMbPublReplyMsg(1)"
    assert any(m.startswith("SackInterface.SAaSysMbPublReplyMsg") for m in blocked_msgs)
