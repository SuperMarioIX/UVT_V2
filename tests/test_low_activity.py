# tests/test_low_activity.py

from src.overview import is_low_activity_component


def test_low_activity_by_threshold():
    # 3 frames, total messages 2 => low activity
    frames = {
        "Frame1[...]" : {"ico_summary": {"in": 1, "consume": 1, "out": 0}},
        "Frame2[...]" : {"ico_summary": {"in": 1, "consume": 1, "out": 0}},
        "Frame3[...]" : {"ico_summary": {"in": 1, "consume": 1, "out": 0}},
    }
    assert is_low_activity_component(frames) is True


def test_low_activity_no_out_but_in_and_consume():
    frames = {
        "Frame1[...]" : {"ico_summary": {"in": 5, "consume": 5, "out": 0}},
    }
    assert is_low_activity_component(frames) is True


def test_low_activity_no_traffic_at_all():
    frames = {
        "Frame1[...]" : {"ico_summary": {"in": 0, "consume": 0, "out": 0}},
    }
    assert is_low_activity_component(frames) is True


def test_single_frame_with_heavy_traffic_is_NOT_low_activity():
    """Regression: previously a single frame triggered low_by_single_frame
    even with hundreds of messages, hiding important components like MTC
    in short tests. The single-frame OR rule has been removed."""
    frames = {
        "Frame1[...]" : {"ico_summary": {"in": 50, "consume": 50, "out": 50}},
    }
    assert is_low_activity_component(frames) is False


def test_single_frame_only_outgoing_is_NOT_low_activity():
    """A single frame with sends but no receives should not be hidden."""
    frames = {
        "Frame1[...]" : {"ico_summary": {"in": 0, "consume": 0, "out": 30}},
    }
    assert is_low_activity_component(frames) is False
