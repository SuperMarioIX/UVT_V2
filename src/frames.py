from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
from collections import Counter

from .models import ComponentRegistry, MESSAGE_INCOMING, MESSAGE_CONSUMED, MESSAGE_OUTGOING, LIFECYCLE_TO_STATE, EventType
from .parsing import event_type_str, extract_message_name
from .tool_logger import logger
from src.basic_utilities import classify_event

# ============================================================
# Frame Helpers
# ============================================================
def _parse_frame_time_from_key(frame_key: str) -> Optional[datetime]:
    """
    Extract HH:MM:SS.microsecond from 'FrameN[HH:MM:SS.micro]' and return a datetime.
    Date is arbitrary (1900-01-01); we only care about differences.
    """
    try:
        start = frame_key.index("[") + 1
        end = frame_key.index("]", start)
        t_str = frame_key[start:end]  # e.g. "17:28:31.195975"
        return datetime.strptime(t_str, "%H:%M:%S.%f")
    except Exception:
        return None

def build_component_message_stats(registry: ComponentRegistry) -> Dict[str, Any]:
    """
    Build per-component message statistics based on PTQU/PTRX/PTSD events.

    Return structure:

    {
      "COMP1": {
          "total_in": int,
          "total_consume": int,
          "total_out": int,
          "last_out_msg": Optional[str],
          "last_out_ts": Optional[datetime],
          "messages": {
              "Iface.MsgA": {"in": x, "consume": y, "out": z},
              ...
          },
          "event_buckets": [
              (timestamp, bucket, label),  # bucket in {"in","consume","out"} or None
              ...
          ],
      },
      ...
    }
    """
    stats: Dict[str, Any] = {}

    for hist in registry.histories():
        comp_id = hist.component_id
        comp_stat = stats.setdefault(
            comp_id,
            {
                "total_in": 0,
                "total_consume": 0,
                "total_out": 0,
                "last_out_msg": None,
                "last_out_ts": None,
                "messages": {},       # msg_name -> {in, consume, out}
                "event_buckets": [],  # list[(ts, bucket, label)]
            },
        )

        for ev in hist.events:
            bucket, label = classify_event(ev)

            # Record the bucket even if label is empty (for suffix analysis)
            comp_stat["event_buckets"].append((ev.timestamp, bucket, label))

            if bucket is None or not label:
                continue

            msg_map = comp_stat["messages"].setdefault(
                label,
                {"in": 0, "consume": 0, "out": 0},
            )

            if bucket == "in":
                comp_stat["total_in"] += 1
                msg_map["in"] += 1
            elif bucket == "consume":
                comp_stat["total_consume"] += 1
                msg_map["consume"] += 1
            elif bucket == "out":
                comp_stat["total_out"] += 1
                msg_map["out"] += 1
                comp_stat["last_out_msg"] = label
                comp_stat["last_out_ts"] = ev.timestamp

    return stats

def mnemonic_to_state(mnemonic: str) -> str:
    """
    Map a single event mnemonic to a frame state.
    Message mnemonics (PTQU/PTRX/PTSD) → OPERATING.
    Lifecycle mnemonics → specific states.
    Otherwise → RUNNING.
    """
    m = mnemonic.upper()
    if m in MESSAGE_INCOMING or m in MESSAGE_CONSUMED or m in MESSAGE_OUTGOING:
        return "OPERATING"
    return LIFECYCLE_TO_STATE.get(m, "RUNNING")

def infer_states_with_dtac_lookahead(mnemonics: List[str]) -> List[str]:
    """
    Implements DTAC lookahead rule:
      - Consecutive DTAC frames use COMPONENT_ACTIVATING_DEFAULTS.
      - The next non-DTAC frame uses its own state for that frame.
    Other frames map via mnemonic_to_state.
    """
    logger.debug("infer_states_with_dtac_lookahead: mnemonics=%s", mnemonics)
    out: List[str] = []
    i = 0
    n = len(mnemonics)

    while i < n:
        m = mnemonics[i].upper()
        if m == "DTAC":
            plateau_state = LIFECYCLE_TO_STATE["DTAC"]
            out.append(plateau_state)
            i += 1
            while i < n and mnemonics[i].upper() == "DTAC":
                out.append(plateau_state)
                i += 1

            if i < n:
                out.append(mnemonic_to_state(mnemonics[i]))
                i += 1
            continue

        out.append(mnemonic_to_state(m))
        i += 1

    logger.debug("infer_states_with_dtac_lookahead: states=%s", out)
    return out


# ============================================================
# Frame Core
# ============================================================
def build_frames_summary(registry: ComponentRegistry) -> Dict[str, Dict[str, Any]]:
    """
    Build the per-component frames JSON.

    Behaviour:
      - Non-DTAC events are grouped into frames by contiguous regions where:
          * inferred state (from infer_states_with_dtac_lookahead) is constant, AND
          * altsteps_active set is constant.
        All messages in that region are aggregated into that frame.
      - DTAC plateau:
          * consecutive DTAC events are not framed individually
          * instead we emit:
              - one frame at the first DTAC timestamp (with altsteps after first DTAC)
              - one frame at the last DTAC timestamp (with final altsteps after last DTAC),
                if there is more than one DTAC in the plateau
          * no messages are attached to DTAC frames (they are pure lifecycle frames).
      - Identical messages inside a frame are compressed:
          ["X", "X", "Y"] -> ["X(2)", "Y"].
      - ico_summary.{in,consume,out} are cumulative per component.
    """
    logger.debug("build_frames_summary: components=%d", len(registry.by_id))
    result: Dict[str, Dict[str, Any]] = {}

    def compress_messages(msgs: List[str]) -> List[str]:
        if not msgs:
            return msgs
        c = Counter(msgs)
        out: List[str] = []
        for name, cnt in c.items():
            if cnt > 1:
                out.append(f"{name}({cnt})")
            else:
                out.append(name)
        return out

    for hist in registry.histories():
        events = hist.events
        snaps = hist.snapshots

        logger.debug(
            "build_frames_summary: component=%s events=%d snapshots=%d",
            hist.component_id,
            len(events),
            len(snaps),
        )

        if not events:
            result[hist.component_id] = {}
            continue

        # Precompute mnemonics and states (DTAC lookahead logic already applied here)
        mnemonics = [event_type_str(e.type) for e in events]
        states = infer_states_with_dtac_lookahead(mnemonics)

        frames: Dict[str, Any] = {}

        # Cumulative per-component counters
        in_total = 0
        consume_total = 0
        out_total = 0

        # Current open non-DTAC frame
        frame_idx = 0
        current_state: Optional[str] = None
        current_altsteps: Optional[Set[str]] = None
        current_incoming: List[str] = []
        current_consumed: List[str] = []
        current_outgoing: List[str] = []
        current_ts: Optional[datetime] = None

        # DTAC plateau tracking (indices in `events`)
        dtac_run_start: Optional[int] = None
        last_dtac_idx: Optional[int] = None

        def flush_current_frame() -> None:
            nonlocal frame_idx, current_state, current_altsteps
            nonlocal current_incoming, current_consumed, current_outgoing, current_ts

            if current_state is None or current_ts is None:
                return

            frame_idx += 1
            ts_str = current_ts.strftime("%H:%M:%S.%f")
            frame_name = f"Frame{frame_idx}[{ts_str}]"

            frames[frame_name] = {
                "State": current_state,
                #"Active_Altsteps": sorted(current_altsteps) if current_altsteps else [], intentional commented line
                "Incoming_messages": compress_messages(current_incoming),
                "Consumed_messages": compress_messages(current_consumed),
                "Outgoing_messages": compress_messages(current_outgoing),
                "ico_summary": {
                    "in": in_total,
                    "consume": consume_total,
                    "out": out_total,
                },
            }

            logger.debug(
                "build_frames_summary: component=%s flushed %s state=%s altsteps=%s "
                "in=%d consume=%d out=%d",
                hist.component_id,
                frame_name,
                current_state,
                sorted(current_altsteps) if current_altsteps else [],
                in_total,
                consume_total,
                out_total,
            )

            # Reset
            current_state = None
            current_altsteps = None
            current_incoming = []
            current_consumed = []
            current_outgoing = []
            current_ts = None

        def flush_dtac_plateau() -> None:
            """
            Emit up to two frames for the DTAC plateau:
              - first DTAC event
              - last DTAC event (if different)
            Uses:
              - state = COMPONENT_ACTIVATING_DEFAULTS
              - altsteps from snapshots after those events
              - cumulative counters as of now (DTAC does not change counters).
            """
            nonlocal frame_idx, dtac_run_start, last_dtac_idx

            if dtac_run_start is None:
                return

            first = dtac_run_start
            last = last_dtac_idx if last_dtac_idx is not None else dtac_run_start

            plateau_state = LIFECYCLE_TO_STATE["DTAC"]

            def write_plateau_frame(ev_idx: int, altsteps: Set[str]) -> None:
                nonlocal frame_idx
                ev = events[ev_idx]
                frame_idx += 1
                ts_str = ev.timestamp.strftime("%H:%M:%S.%f")
                frame_name = f"Frame{frame_idx}[{ts_str}]"
                frames[frame_name] = {
                    "State": plateau_state,
                    #"Active_Altsteps": sorted(altsteps),
                    "Incoming_messages": [],
                    "Consumed_messages": [],
                    "Outgoing_messages": [],
                    "ico_summary": {
                        "in": in_total,
                        "consume": consume_total,
                        "out": out_total,
                    },
                }
                logger.debug(
                    "build_frames_summary: component=%s DTAC frame=%s altsteps=%s",
                    hist.component_id,
                    frame_name,
                    sorted(altsteps),
                )

            # Altsteps after first and last DTAC
            first_snap_idx = first + 1 if first + 1 < len(snaps) else len(snaps) - 1
            last_snap_idx = last + 1 if last + 1 < len(snaps) else len(snaps) - 1

            first_altsteps = set(getattr(snaps[first_snap_idx], "altsteps_active", []) or [])
            last_altsteps = set(getattr(snaps[last_snap_idx], "altsteps_active", []) or [])

            # First DTAC frame
            write_plateau_frame(first, first_altsteps)

            # Last DTAC frame (only if distinct index)
            if last > first:
                write_plateau_frame(last, last_altsteps)

            # Reset plateau tracking
            dtac_run_start = None
            last_dtac_idx = None

        # Main event loop
        for i, (ev, mnem, state) in enumerate(zip(events, mnemonics, states)):
            # DTAC handling: collect plateau and do not create normal frames here
            if mnem == "DTAC":
                # Close any open non-DTAC frame before plateau
                if current_state is not None:
                    flush_current_frame()

                if dtac_run_start is None:
                    dtac_run_start = i
                    last_dtac_idx = i
                else:
                    last_dtac_idx = i
                # DTAC does not affect message counters; continue
                continue

            # Non-DTAC event
            # If we are exiting a DTAC plateau, flush it now
            if dtac_run_start is not None:
                flush_dtac_plateau()

            # Determine altsteps after this event
            snap_idx = i + 1 if i + 1 < len(snaps) else len(snaps) - 1
            snap = snaps[snap_idx]
            altsteps_set: Set[str] = set(getattr(snap, "altsteps_active", []) or [])

            # Decide whether to start a new non-DTAC frame
            if current_state is None:
                current_state = state
                current_altsteps = altsteps_set
                current_ts = ev.timestamp
            else:
                if current_state != state or current_altsteps != altsteps_set:
                    flush_current_frame()
                    current_state = state
                    current_altsteps = altsteps_set
                    current_ts = ev.timestamp

            # Accumulate messages and counters into the current frame
            msg_name = extract_message_name(ev)

            if mnem in MESSAGE_INCOMING:
                in_total += 1
                current_incoming.append(msg_name)

            if mnem in MESSAGE_CONSUMED:
                consume_total += 1
                current_consumed.append(msg_name)

            if mnem in MESSAGE_OUTGOING:
                out_total += 1
                current_outgoing.append(msg_name)

        # End of events: if we finish inside a DTAC plateau, flush it
        if dtac_run_start is not None:
            flush_dtac_plateau()

        # Flush last open non-DTAC frame
        flush_current_frame()

        logger.info(
            "build_frames_summary: component=%s generated %d frames",
            hist.component_id,
            len(frames),
        )
        result[hist.component_id] = frames

    return result

def build_frames_text_summary(
    frames_payload: Dict[str, Dict[str, Any]],
    registry: ComponentRegistry,
    test_finish_ts: Optional[datetime],
) -> str:
    """
    Build a human-readable frames summary log.

    Observations:
        - Component with the highes activity (...)
        COMPA (...), discrepancy_score: ...
            => ...
            => ...
            => ...
    """
    lines: List[str] = []

    num_components = len(frames_payload)
    lines.append(f"This test has ({num_components}) components")
    lines.append("")
    lines.append("Observations:")

    # ------------------------------------------------------------------
    # 1) Component with the highest activity (by number of frames)
    # ------------------------------------------------------------------
    max_frames = 0
    max_comp_id: Optional[str] = None
    max_duration_seconds: Optional[float] = None
    max_last_state: Optional[str] = None

    for comp_id, frames in frames_payload.items():
        if not frames:
            continue
        num_frames = len(frames)

        # frames is an OrderedDict-like (in insertion order). We use first/last keys.
        frame_items = list(frames.items())
        first_key, first_frame = frame_items[0]
        last_key, last_frame = frame_items[-1]

        # Parse timestamps from frame keys: "FrameN[HH:MM:SS.micro]"
        def _extract_time_from_key(k: str) -> Optional[datetime]:
            # We have only time-of-day, so we parse it as today's date
            if "[" not in k or "]" not in k:
                return None
            time_str = k.split("[", 1)[1].rstrip("]")
            try:
                # use arbitrary date + that time
                t = datetime.strptime(time_str, "%H:%M:%S.%f")
                # date part is irrelevant for difference; use 1900-01-01
                return t
            except Exception:
                return None

        t_first = _extract_time_from_key(first_key)
        t_last = _extract_time_from_key(last_key)

        duration_seconds = None
        if t_first and t_last:
            duration_seconds = (t_last - t_first).total_seconds()
            if duration_seconds < 0:
                duration_seconds = None

        if num_frames > max_frames:
            max_frames = num_frames
            max_comp_id = comp_id
            max_duration_seconds = duration_seconds
            max_last_state = last_frame.get("State")

    if max_comp_id is not None:
        dur_str = "unknown duration"
        if max_duration_seconds is not None:
            mins = int(max_duration_seconds // 60)
            secs = int(max_duration_seconds % 60)
            dur_str = f"{mins} minutes and {secs} seconds"
        lines.append(
            f"\t- Component with the highes activity ({max_frames} frames) is {max_comp_id}, "
            f"it runs a total of {dur_str}, and terminates with state: {max_last_state}"
        )
    else:
        lines.append("\t- No component activity detected")

    # ------------------------------------------------------------------
    # 2) Discrepancy components with new narrative
    # ------------------------------------------------------------------
    component_msg_stats = build_component_message_stats(registry)

    discrepancy_entries: List[Tuple[int, str, int, int, int]] = []

    for comp_id, frames in frames_payload.items():
        if not frames:
            continue

        frames_list = list(frames.values())
        last_frame = frames_list[-1]
        ico = last_frame.get("ico_summary", {}) or {}

        in_count = int(ico.get("in", 0))
        consume_count = int(ico.get("consume", 0))
        out_count = int(ico.get("out", 0))

        vals = [in_count, consume_count, out_count]
        if not any(vals):
            continue

        diff = max(vals) - min(vals)
        
        if diff < 10:
            continue  # uncomment to filder the low components

        discrepancy_entries.append((diff, comp_id, in_count, consume_count, out_count))

    discrepancy_entries.sort(reverse=True, key=lambda x: x[0])

    if not discrepancy_entries:
        lines.append("\t<no significant PTQU/PTRX/PTSD discrepancies detected>")
        return "\n".join(lines) + "\n"

    for score, comp_id, in_c, cons_c, out_c in discrepancy_entries:
        lines.append(
            f"\t{comp_id} (in: {in_c}, consumed: {cons_c}, out: {out_c}), discrepancy_score: {score}"
        )

        msg_stat = component_msg_stats.get(comp_id)
        if not msg_stat:
            continue

        last_out_msg = msg_stat.get("last_out_msg")
        last_out_ts: Optional[datetime] = msg_stat.get("last_out_ts")
        event_buckets: List[Tuple[datetime, Optional[str], Optional[str]]] = msg_stat.get(
            "event_buckets", []
        )

        # 2.1 last message out (time-only for readability)
        if last_out_msg and last_out_ts:
            lines.append(
                f"\t\t=> {comp_id} last message out: {last_out_msg} at {last_out_ts.time().isoformat()}"
            )

        # If we do not know test finish timestamp, we cannot build suffix analysis
        if test_finish_ts is None or not event_buckets:
            continue

        # 2.2 find suffix where component only sends (no in/consume)
        suffix_start_idx: Optional[int] = None
        has_out_in_suffix = False

        # walk from tail backward until we see in/consume;
        # suffix_start_idx is first index of the contiguous tail with only out/None
        for i in range(len(event_buckets) - 1, -1, -1):
            ts, bucket, label = event_buckets[i]
            if bucket in ("in", "consume"):
                break
            if bucket == "out":
                has_out_in_suffix = True
            if has_out_in_suffix:
                suffix_start_idx = i

        if suffix_start_idx is None:
            # no "only sending" suffix → nothing to say
            continue

        start_ts = event_buckets[suffix_start_idx][0]
        end_ts = test_finish_ts

        # Guard: if start_ts somehow ends up after test_finish_ts, skip this narrative
        if start_ts > end_ts:
            logger.debug(
                "build_frames_text_summary: skipping suffix for %s because start_ts>%s finish_ts=%s",
                comp_id,
                start_ts,
                end_ts,
            )
            continue

        start_ts_str = start_ts.time().isoformat()
        end_ts_str = end_ts.time().isoformat()

        # Last state for this component (from frames)
        frames_for_comp = frames_payload.get(comp_id, {})
        last_state = None
        if frames_for_comp:
            last_state = list(frames_for_comp.values())[-1].get("State")

        lines.append(
            f"\t\t=> {comp_id} start sending messages without receiving and consuming at "
            f"{start_ts_str}, test finished {end_ts_str} and {comp_id} state: {last_state}"
        )

        # 2.3 count last_out_msg occurrences and incoming/consumed in [start_ts, end_ts]
        msg_name_for_count = last_out_msg
        out_msg_count = 0
        in_after = 0
        cons_after = 0

        for ts, bucket, label in event_buckets:
            if ts < start_ts or ts > end_ts:
                continue
            if bucket == "out" and msg_name_for_count and label == msg_name_for_count:
                out_msg_count += 1
            elif bucket == "in":
                in_after += 1
            elif bucket == "consume":
                cons_after += 1

        lines.append(
            f"\t\t=> {comp_id} send {msg_name_for_count or '<unknown>'} "
            f"({out_msg_count}) times in the interval {start_ts_str} to {end_ts_str}, "
            f"during this period in_messages({in_after}), consumed_messages({cons_after})"
        )

    return "\n".join(lines) + "\n"