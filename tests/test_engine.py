# tests/test_engine.py

from src.engine import process_events
from src.models import ComponentRegistry


def test_process_events_builds_registry_with_lifecycle():
    """Note: COCR is now attributed to the *new* component (NOKIA_CPRI_RADIO_1)
    rather than the creator (MTC). MTC may still appear in the registry if
    it has its own events, but in this minimal scenario only CPRI and
    NOKIA_CPRI_RADIO_1 should be present."""
    lines = [
        "20251110T172801.489274|cocr|MTC=/home/k3/K3_ROOT/C_Test/OM_K3/src/pit_oam/components/MBtsomMtcComponentsSetup.ttcn3:177|NOKIA_CPRI_RADIO_1|alive",
        "20251110T172802.059122|cost|CPRI=/home/k3/K3_ROOT/C_Test/OM_K3/src/common/components/MHwApiCpri.ttcn3:306|MHwApiCpri.f_setup",
        "20251110T172802.065589|cofi|CPRI|none",
    ]

    registry, skipped = process_events(lines, strict=True)
    assert skipped == 0
    assert isinstance(registry, ComponentRegistry)

    comp_ids = sorted(registry.by_id.keys())
    # COCR now creates a history entry for the NEW component, not the creator
    assert "NOKIA_CPRI_RADIO_1" in comp_ids
    assert "CPRI" in comp_ids
    assert "MTC" not in comp_ids   # MTC has no events of its own here

    # NOKIA_CPRI_RADIO_1 received the Created lifecycle event with creator
    # info preserved as related_component
    hist_radio = registry.by_id["NOKIA_CPRI_RADIO_1"]
    last_radio = hist_radio.snapshots[-1]
    assert last_radio.lifecycle.created is True
    assert hist_radio.events[0].related_component == "MTC"
    assert hist_radio.events[0].source is not None
    assert hist_radio.events[0].source.module_path.endswith("MBtsomMtcComponentsSetup.ttcn3")

    # CPRI lifecycle stays unchanged
    hist_cpri = registry.by_id["CPRI"]
    last_cpri = hist_cpri.snapshots[-1]
    assert last_cpri.lifecycle.started is True
    assert last_cpri.verdict == "none"
