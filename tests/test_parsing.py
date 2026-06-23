# tests/test_parsing.py

from datetime import datetime

from src.parsing import parse_log_line, extract_message_name
from src.models import (
    LIFECYCLE_TO_STATE,
    MESSAGE_INCOMING, MESSAGE_CONSUMED, MESSAGE_OUTGOING,
)

def test_parse_cocr_attributes_event_to_new_component():
    """COCR is logged from the creator's call site (parts[2] = MTC), but the
    *Created* lifecycle event must be attached to the NEW component
    (parts[3] = APIGW). The creator is preserved in related_component, and
    the source location (where in MTC the create happened) is kept on the
    event so the FSM can show 'Created at <file>:<line> by <creator>'."""
    line = "20251110T172801.488796|cocr|MTC=/home/k3/K3_ROOT/C_Test/OM_K3/src/pit_oam/components/MBtsomMtcComponentsSetup.ttcn3:125|APIGW|alive"
    ev = parse_log_line(line)
    assert ev is not None
    assert ev.type == "COCR"
    assert ev.component_id == "APIGW"           # NEW component, not the creator
    assert ev.related_component == "MTC"        # creator preserved
    assert ev.port_name is None                 # lifecycle must not carry ports
    assert ev.source is not None
    assert ev.source.module_path.endswith("MBtsomMtcComponentsSetup.ttcn3")
    assert ev.source.line_number == 125


def test_parse_cocr_engine_creates_mtc():
    """At startup k3r emits 'cocr|k3r|MTC|once'. The MTC component must
    receive its Created event; related_component=k3r marks the engine."""
    line = "20251110T172247.933696|cocr|k3r|MTC|once"
    ev = parse_log_line(line)
    assert ev is not None
    assert ev.type == "COCR"
    assert ev.component_id == "MTC"
    assert ev.related_component == "K3R"
    assert ev.source is None                    # k3r has no source location


def test_parse_cost_with_start_fn():
    line = "20251110T172801.910576|cost|OPT=/home/k3/K3_ROOT/C_Test/OM_K3/src/common/components/MHwApiOpt.ttcn3:375|MHwApiOpt.f_setup"
    ev = parse_log_line(line)
    assert ev is not None
    assert ev.type == "COST"
    assert ev.component_id == "OPT"
    assert ev.start_fn is not None
    assert ev.start_fn.qualified_name == "MHwApiOpt.f_setup"
    assert ev.port_name is None


def test_parse_dtac_with_altstep():
    line = "20251110T172810.742456|dtac|OPT=/home/k3/K3_ROOT/C_Test/OM_K3/src/common/components/MSimulatedComponent.ttcn3:24|MSimulatedComponent.a_stopOnPoweroff()"
    ev = parse_log_line(line)
    assert ev is not None
    assert ev.type == "DTAC"
    assert ev.component_id == "OPT"
    assert ev.activated_fn is not None
    assert ev.activated_fn.qualified_name.startswith("MSimulatedComponent.a_stopOnPoweroff")
    assert ev.port_name is None


def test_parse_ptqu_has_port_and_message_name():
    line = (
        "20251110T172812.662474|ptqu|?|BBCUTIL.mBbcUtilityPort|"
        "message(value=MBbcUtility.SBbcUtilSubscribtionFileSystemMemoryInfoReq:{blockDeviceName:='72616D0000000000000000000000000000000000'O,fileSystemName:='72616D31000000000000000000000000000000000000000000000000000000000'O,state:=EBbcUtilState_Enabled},sender=MBbcUtility.address:269557873,timestamp=2147483647.000000)"
    )
    ev = parse_log_line(line)
    assert ev is not None
    assert ev.type == "PTQU"
    assert ev.component_id == "BBCUTIL"
    assert ev.port_name == "mBbcUtilityPort"
    msg_name = extract_message_name(ev)
    assert msg_name == "MBbcUtility.SBbcUtilSubscribtionFileSystemMemoryInfoReq"


def test_parse_ptrx_consumes_same_message():
    line = (
        "20251110T172811.417820|ptrx|ADET=/home/k3/K3_ROOT/C_Test/OM_K3/src/common/components/MHwApiAutodetection.ttcn3:842|"
        "ADET.mHwApiAutodetectionPort|value=SackInterface.SApi4AutodetReq:?|ready(match=message(value=SackInterface.SApi4AutodetReq:{transactionId:=45141,operation:=EAdetOperation_Start,provideDomainInd:=1,domainIndClientAddress:=0,domainIndScope:=EAdetDomainIndScope_Local,domainIndVersion:=EAdetApiVersion_3,provideUnitInd:=1,unitIndClientAddress:=0,unitIndVersion:=EAdetApiVersion_5,provideCpuInd:=1,cpuIndClientAddress:=0,cpuIndVersion:=EAdetApiVersion_3},sender=MHwApiAutodetection.address:269557691,timestamp=11.874923))+consume"
    )
    ev = parse_log_line(line)
    assert ev is not None
    assert ev.type == "PTRX"
    assert ev.component_id == "ADET"
    assert ev.port_name == "mHwApiAutodetectionPort"
    assert extract_message_name(ev) == "SackInterface.SApi4AutodetReq"
