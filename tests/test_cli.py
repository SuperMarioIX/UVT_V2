# tests/test_cli.py

import json
import sys
from pathlib import Path

import main as cli_main  # your root main.py


def run_cli(tmp_path, args):
    """Run main.main() with patched sys.argv and return output path."""
    file_path = tmp_path / "log.log"
    file_path.write_text("\n".join(args["lines"]), encoding="utf-8")

    out_path = tmp_path / "out.json"

    argv = [
        "main.py",
        f"--file={file_path}",
        f"--out={out_path}",
    ]
    if args.get("frames"):
        argv.append("--frames")
    if args.get("overview"):
        argv.append("--overview")

    # monkeypatch sys.argv
    old_argv = sys.argv
    sys.argv = argv
    try:
        cli_main.main()
    finally:
        sys.argv = old_argv

    assert out_path.exists()
    return out_path


def test_cli_frames_smoke(tmp_path):
    lines = [
        "20251110T172801.486623|cocr|MTC=/home/k3/K3_ROOT/C_Test/OM_K3/src/pit_oam/components/MBtsomMtcComponentsSetup.ttcn3:114|BM_IMS_CLIENT|alive",
        "20251110T172845.642917|cost|BM_IMS_CLIENT=/home/k3/K3_ROOT/C_Test/OM_K3/src/common/components/MBmImsClient.ttcn3:26|MBmImsClient.f_waitForPoweroff",
        "20251110T172845.539768|ptqu|?|BM_IMS_CLIENT.mImiInterfacePort|message(value=protoStruct_gen.IMSignInResult:{something})",
        "20251110T172845.480195|cofi|BM_IMS_CLIENT|pass",
    ]
    out_path = run_cli(tmp_path, {"lines": lines, "frames": True})
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert "BM_IMS_CLIENT" in data
    assert len(data["BM_IMS_CLIENT"]) >= 1


def test_cli_overview_smoke(tmp_path):
    lines = [
        "20251110T172810.829616|ptqu|?|BM_SIMULATOR.mImiInterfacePort|message(value=protoStruct_gen.IMOperationExecuted:{request_id:=2001,execution_status:=EExecutionStatus_EXECUTED,error_code:=omit},sender=MBmIms.address:269554928,timestamp=2147483647.000000)",
        "20251110T172817.160372|ptqu|?|TPL.mTplPort|message(value=TpiInterface.TpiIpSetConfigChangeHandlerReceiveRequest:{},sender=JsonRpcConnector.address:139637976832130,timestamp=2147483647.000000)",
    ]
    out_path = run_cli(tmp_path, {"lines": lines, "overview": True})
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert "component_log_density" in data
    assert "messages_that_repeat_the_most" in data
