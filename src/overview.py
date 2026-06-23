from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple, Iterable
from datetime import datetime, timedelta
from .models import (
    ComponentRegistry, QueueConsumeStats, LogEvent, ComponentStateHistory,
    MESSAGE_INCOMING, MESSAGE_CONSUMED, MESSAGE_OUTGOING,
    TOP_COMPONENTS_FOR_DENSITY, TOP_MESSAGES_FOR_REPETITION,
    LOW_ACTIVITY_MAX_FRAMES, LOW_ACTIVITY_MAX_TOTAL_MSGS
)
from .parsing import extract_message_name
from .tool_logger import logger
from src.ttcn3_special_interleave import (
    parse_interleaved_ulog_line, InterleavedEvent,
    build_interleaved_overview,
    render_interleaved_overview_text,
    is_empty_event
)
# type alias for clarity
PortKey = Tuple[str, str]  # (component_id, port_name)


# ============================================================
# Main overview builders for .log and .json formats
# ============================================================
def build_llm_overview(
    registry,                # currently unused, but keep for future
    lines: List[str],
    logger,
) -> Tuple[Dict[str, Any], str]:
    from collections import defaultdict
    """
    Build two artifacts:
      - JSON-like structure with all interleaved events (for machine use),
      - Human-readable overview text (for overview.log and LLM prompts).

    All events returned by parse_interleaved_ulog_line(...) are considered,
    and their payload_summary is used as-is (no recomputation).
    """

    events: List[InterleavedEvent] = []
    total_ulog_special = 0

    # 1) Collect all BM_IMS_CLIENT interleaved events
    for raw in lines:
        if "|ulog|" in raw and "BM_IMS_CLIENT" in raw:
            total_ulog_special += 1

        ev = parse_interleaved_ulog_line(raw, logger)
        if ev is not None:
            events.append(ev)
            logger.debug(
                "OVERVIEW_COLLECTED: idx=%s status=%s dn=%s payload=%s",
                ev.index,
                ev.status,
                ev.dn,
                ev.payload_summary,
            )

    logger.info("Total '|ulog|BM_IMS_CLIENT' lines in raw: %d", total_ulog_special)
    logger.info("Total InterleavedEvent objects parsed: %d", len(events))

    if not events:
        # No interleaved expectations at all
        text = "BM_IMS_CLIENT\n\tno interleaved expectations found in log\n"
        json_obj = {
            "component": "BM_IMS_CLIENT",
            "events_total": 0,
            "by_index": {},
        }
        return json_obj, text

    # 2) Group events by index (aEvents[index])
    by_index: Dict[int, List[InterleavedEvent]] = defaultdict(list)
    for ev in events:
        by_index[ev.index].append(ev)

    # 3) Classify indices by status
    started_indices = set(
        ev.index for ev in events if ev.status.lower() == "started"
    )
    matched_indices = set(
        ev.index for ev in events if ev.status.lower() == "matched"
    )
    unknown_indices = set(
        ev.index for ev in events if ev.status.lower() not in ("started", "matched")
    )
    not_matched_indices = started_indices - matched_indices

    logger.info(
        "Indices started=%s matched=%s not_matched=%s unknown=%s",
        sorted(started_indices),
        sorted(matched_indices),
        sorted(not_matched_indices),
        sorted(unknown_indices),
    )

    # 4) Build JSON structure (for machine use later)
    json_by_index: Dict[int, Dict[str, Any]] = {}

    for idx, ev_list in by_index.items():
        json_by_index[idx] = {
            "events": [
                {
                    "ts": ev.ts.isoformat(),
                    "status": ev.status,
                    "dn": ev.dn,
                    "payload_summary": ev.payload_summary,
                }
                for ev in sorted(ev_list, key=lambda e: e.ts)
            ]
        }

    json_obj = {
        "component": "BM_IMS_CLIENT",
        "events_total": len(events),
        "indices": sorted(by_index.keys()),
        "by_index": json_by_index,
    }

    # 5) Build human-readable overview text (for .overview.log)
    lines_out: List[str] = []
    lines_out.append("BM_IMS_CLIENT")

    # --- started ---
    lines_out.append("\tEXPECTED EVENTS STARTED")

    # Filter out minimal / empty events (no record, no payload)
    started_events = [
        e for e in events
        if e.status.lower() == "started" and not is_empty_event(e)
    ]

    if not started_events:
        lines_out.append("\t\t<none>")
    else:
        from collections import defaultdict
        started_by_index: Dict[int, List[InterleavedEvent]] = defaultdict(list)

        for ev in started_events:
            started_by_index[ev.index].append(ev)

        for idx in sorted(started_by_index.keys()):
            ev_list = sorted(started_by_index[idx], key=lambda e: e.ts)

            for seq, ev in enumerate(ev_list):
                suffix = f"-{seq}" if len(ev_list) > 1 else ""
                lines_out.append(f"\t\tEvent{idx}{suffix}:")
                lines_out.append(f"\t\t\tExpected object: {ev.dn}")
                lines_out.append(f"\t\t\tExpected payload: {ev.payload_summary}")

                logger.debug(
                    "OVERVIEW_PRINT_STARTED idx=%s seq=%s dn=%s payload=%s",
                    idx,
                    seq,
                    ev.dn,
                    ev.payload_summary,
                )

    # Optional: log dropped started events for dev visibility
    dropped_started = [
        e for e in events
        if e.status.lower() == "started" and is_empty_event(e)
    ]
    for ev in dropped_started:
        logger.debug(
            "OVERVIEW_DROP_STARTED idx=%s ts=%s reason=empty_interleave line=%r",
            ev.index,
            ev.ts,
            ev.raw_line,
        )

    # --- matched ---
    lines_out.append("\tEXPECTED EVENTS MATCHED")
    matched_events = [
        e for e in events
        if e.status.lower() == "matched" and not is_empty_event(e)
    ]

    if not matched_events:
        lines_out.append("\t\t<none>")
    else:
        from collections import defaultdict
        matched_by_index: Dict[int, List[InterleavedEvent]] = defaultdict(list)

        for ev in matched_events:
            matched_by_index[ev.index].append(ev)

        for idx in sorted(matched_by_index.keys()):
            ev_list = sorted(matched_by_index[idx], key=lambda e: e.ts)
            for seq, ev in enumerate(ev_list):
                suffix = f"-{seq}" if len(ev_list) > 1 else ""
                lines_out.append(f"\t\tEvent{idx}{suffix}:")
                lines_out.append(f"\t\t\tMatched object: {ev.dn}")
                lines_out.append(f"\t\t\tMatched payload: {ev.payload_summary}")

                logger.debug(
                    "OVERVIEW_PRINT_MATCHED idx=%s seq=%s dn=%s payload=%s",
                    idx,
                    seq,
                    ev.dn,
                    ev.payload_summary,
                )

    dropped_matched = [
        e for e in events
        if e.status.lower() == "matched" and is_empty_event(e)
    ]
    for ev in dropped_matched:
        logger.debug(
            "OVERVIEW_DROP_MATCHED idx=%s ts=%s reason=empty_interleave line=%r",
            ev.index,
            ev.ts,
            ev.raw_line,
        )

    # --- not matched (started but never matched) ---
    lines_out.append("\tEXPECTED EVENTS NOT MATCHED")

    any_not_matched = False

    any_not_matched = False
    unmatched_starts: List[InterleavedEvent] = []

    for idx in sorted(by_index.keys()):
        started_events = sorted(
            [
                e for e in by_index[idx]
                if e.status.lower() == "started" and not is_empty_event(e)
            ],
            key=lambda e: e.ts,
        )
        matched_events = sorted(
            [
                e for e in by_index[idx]
                if e.status.lower() == "matched" and not is_empty_event(e)
            ],
            key=lambda e: e.ts,
        )

        if not started_events:
            continue

        covered = min(len(started_events), len(matched_events))
        unmatched = list(enumerate(started_events[covered:], start=covered))

        logger.debug(
            "NOT_MATCH idx=%s started=%d matched=%d unmatched=%d",
            idx, len(started_events), len(matched_events), len(unmatched),
        )

        for seq, ev in unmatched:
            any_not_matched = True
            unmatched_starts.append(ev)
            suffix = f"-{seq}" if len(started_events) > 1 else ""
            lines_out.append(f"\t\tEvent{idx}{suffix}:")
            lines_out.append(f"\t\t\tNotMatched object: {ev.dn}")
            lines_out.append(f"\t\t\tNotMatched payload: {ev.payload_summary}")
            logger.debug(
                "OVERVIEW_PRINT_NOT_MATCHED idx=%s seq=%s dn=%s payload=%s",
                idx, seq, ev.dn, ev.payload_summary,
            )

    if not any_not_matched:
        lines_out.append("\t\t<none>")

    # --- PTQU/PTRX conclusions around unmatched starts ---
    conclusion_lines = build_conclusions_from_ptqu_ptrx(registry, unmatched_starts)
    if conclusion_lines:
        lines_out.append("")  # separate sections
        lines_out.extend(conclusion_lines)

    # --- unknown status (if any) ---
    if unknown_indices:
        lines_out.append("\tother interleaves (unknown status)")
        for idx in sorted(unknown_indices):
            for ev in sorted(by_index[idx], key=lambda e: e.ts):
                if ev.status.lower() in ("started", "matched"):
                    continue
                lines_out.append(
                    f"\t\tEvent{idx} ({ev.status}): dn={ev.dn}, payload={ev.payload_summary}"
                )

    text_out = "\n".join(lines_out) + "\n"

    logger.info("LLM overview built with %d lines of text", len(lines_out))

    return json_obj, text_out

def is_low_activity_component(frames_for_component: Dict[str, Any]) -> bool:
    """
    Decide if a component should be considered low-activity.

    Rules (OR together):

      0) Completely silent:
         - last frame's (in + consume + out) == 0

      1) Threshold rule:
         - number of frames ≤ LOW_ACTIVITY_MAX_FRAMES, AND
         - last frame's (in + consume + out) ≤ LOW_ACTIVITY_MAX_TOTAL_MSGS

      2) No-out rule (updated):
         - last frame has out == 0, AND
         - EITHER
              (in ≥ 1 AND consume ≥ 1)
           OR (in == 0 AND consume == 0)

    Note: a previous "single frame ⇒ low activity" rule was removed because
    it incorrectly hid components that have a single dense frame with many
    messages (typical for MTC in short tests). Rule 0 already catches the
    silent single-frame case; rule 1 catches the low-volume case.

    This is evaluated on the last frame since ico_summary is cumulative.
    """
    if not frames_for_component:
        # No frames at all → low activity
        return True

    frames_list = list(frames_for_component.values())
    num_frames = len(frames_list)

    # frames_for_component is a dict; order in JSON is insertion order,
    # so values() preserves frame order – last one is the last frame.
    last_frame = frames_list[-1]
    ico = last_frame.get("ico_summary", {}) or {}

    in_count = int(ico.get("in", 0))
    consume_count = int(ico.get("consume", 0))
    out_count = int(ico.get("out", 0))
    total_msgs = in_count + consume_count + out_count

    # 0) Completely silent component in terms of messages
    if total_msgs == 0:
        return True

    # 1) Generic low-volume threshold
    low_by_threshold = (
        num_frames <= LOW_ACTIVITY_MAX_FRAMES
        and total_msgs <= LOW_ACTIVITY_MAX_TOTAL_MSGS
    )

    # 2) Components that never send anything out
    low_by_no_out = (
        out_count == 0
        and (
            (in_count >= 1 and consume_count >= 1)   # receives+consumes, but no out
            or (in_count == 0 and consume_count == 0)  # fully silent case (extra safety)
        )
    )

    return low_by_threshold or low_by_no_out

def is_low_activity_history(history: ComponentStateHistory) -> bool:
    """
    Low-activity detection for ComponentStateHistory, to be used in --overview.

    It mirrors is_low_activity_component(...) but works on events instead of
    precomputed frames.
    """
    events = history.events
    if not events:
        return True

    # Approximate "frames" with number of events for this component
    num_frames = len(events)

    # Compute cumulative (in, consume, out) from message mnemonics
    in_count = sum(1 for ev in events if ev.type in MESSAGE_INCOMING)
    consume_count = sum(1 for ev in events if ev.type in MESSAGE_CONSUMED)
    out_count = sum(1 for ev in events if ev.type in MESSAGE_OUTGOING)
    total_msgs = in_count + consume_count + out_count

    low_by_threshold = (
        num_frames <= LOW_ACTIVITY_MAX_FRAMES
        and total_msgs <= LOW_ACTIVITY_MAX_TOTAL_MSGS
    )

    low_by_no_out = (
        out_count == 0
        and (
            (in_count >= 1 and consume_count >= 1)
            or (in_count == 0 and consume_count == 0)
        )
    )

    return low_by_threshold or low_by_no_out


def build_overview_profiling(registry: ComponentRegistry) -> Dict[str, Any]:
    """
    High-level profiling overview.

    Produces:
      {
        "component_log_density": [...],
        "messages_that_repeat_the_most": [...],
        "ports_with_messages_blocked_on_queue": {
          "COMP_A.compAPort": ["Iface.MsgX(3)", "Iface.MsgY"],
          ...
        }
      }
    """
    # ----------------------------------------
    # 1) Component log density
    # ----------------------------------------
    component_msg_counts: Dict[str, int] = {}

    for hist in registry.histories():
        msg_count = 0
        for ev in hist.events:
            t = ev.type
            if t in MESSAGE_INCOMING or t in MESSAGE_CONSUMED or t in MESSAGE_OUTGOING:
                msg_count += 1
        component_msg_counts[hist.component_id] = msg_count

    total_msgs_all_components = sum(component_msg_counts.values()) or 1

    ranked_components = sorted(
        component_msg_counts.items(),
        key=lambda kv: kv[1],
        reverse=True,
    )

    component_log_density: List[Dict[str, Any]] = []
    for comp, cnt in ranked_components[:TOP_COMPONENTS_FOR_DENSITY]:
        pct = round((cnt / total_msgs_all_components) * 100.0, 2)
        component_log_density.append(
            {
                "component": comp,
                "density_percent": pct,
                "message_volume": cnt,
            }
        )

    # ----------------------------------------
    # 2) Messages that repeat the most
    # ----------------------------------------
    message_counter: Counter[str] = Counter()

    for hist in registry.histories():
        for ev in hist.events:
            t = ev.type
            if t in MESSAGE_INCOMING or t in MESSAGE_CONSUMED or t in MESSAGE_OUTGOING:
                name = extract_message_name(ev)
                message_counter[name] += 1

    most_common_messages = message_counter.most_common(TOP_MESSAGES_FOR_REPETITION)

    messages_that_repeat_the_most: List[Dict[str, Any]] = [
        {"message": msg, "count": cnt}
        for msg, cnt in most_common_messages
    ]

    # ----------------------------------------
    # 3) Ports with messages blocked on queue
    #    (PTQU seen but not PTRX)
    # ----------------------------------------
    # key: "COMP.port"  value: Counter[message_name] of outstanding (queued - consumed)
    blocked_by_port: Dict[str, Counter[str]] = defaultdict(Counter)

    for hist in registry.histories():
        for ev in hist.events:
            t = ev.type
            if ev.port_name is None:
                continue

            msg_name = extract_message_name(ev)
            port_key = f"{hist.component_id}.{ev.port_name}"

            if t in MESSAGE_INCOMING:   # PTQU
                blocked_by_port[port_key][msg_name] += 1

            elif t in MESSAGE_CONSUMED:  # PTRX
                cnt = blocked_by_port[port_key][msg_name]
                if cnt > 0:
                    blocked_by_port[port_key][msg_name] = cnt - 1
                    if blocked_by_port[port_key][msg_name] == 0:
                        del blocked_by_port[port_key][msg_name]

    # Convert to JSONable structure, compressing counts as Name(N)
    ports_with_messages_blocked_on_queue: Dict[str, Any] = {}

    for port_key, counter in blocked_by_port.items():
        if not counter:
            continue
        msgs: List[str] = []
        for msg, cnt in counter.items():
            if cnt > 1:
                msgs.append(f"{msg}({cnt})")
            else:
                msgs.append(msg)
        ports_with_messages_blocked_on_queue[port_key] = msgs

    ptqu_ptrx = build_ptqu_ptrx_comparison(registry)

    profiling = {
        "component_log_density": component_log_density,
        "messages_that_repeat_the_most": messages_that_repeat_the_most,
        "ports_with_messages_blocked_on_queue": ports_with_messages_blocked_on_queue,
        "ptqu_ptrx_comparison": ptqu_ptrx["items"],
    }

    logger.info(
        "build_overview_profiling: components=%d total_msgs=%d "
        "distinct_messages=%d ports_blocked=%d",
        len(component_msg_counts),
        total_msgs_all_components,
        len(message_counter),
        len(ports_with_messages_blocked_on_queue),
    )

    return profiling

def build_ptqu_ptrx_comparison(registry: ComponentRegistry) -> Dict[str, Any]:
    """
    Compare PTQU (incoming / queue) and PTRX (consumed) messages per
    (component, port, message_name).

    Returns a JSONable structure summarizing match/mismatch situations.

    Shape:

    {
      "items": [
        {
          "component": "PTC_X",
          "port": "compPort",
          "message": "IfaceX.MsgA",
          "ptqu_count": 3,
          "ptrx_count": 2,
          "status": "partially_consumed",
          "last_queued_at": "...",
          "last_consumed_at": "..."
        },
        ...
      ]
    }
    """
    stats: Dict[Tuple[str, str, str], QueueConsumeStats] = defaultdict(QueueConsumeStats)

    # Collect counts and timestamps
    for hist in registry.histories():
        comp_id = hist.component_id
        for ev in hist.events:
            t = ev.type
            if not (t in MESSAGE_INCOMING or t in MESSAGE_CONSUMED):
                continue

            msg_name = extract_message_name(ev)
            port_name = ev.port_name or "<no_port>"

            key = (comp_id, port_name, msg_name)
            st = stats[key]

            if t in MESSAGE_INCOMING:
                st.ptqu_count += 1
                st.ptqu_timestamps.append(ev.timestamp)
            elif t in MESSAGE_CONSUMED:
                st.ptrx_count += 1
                st.ptrx_timestamps.append(ev.timestamp)

    # Classify each key into a high-level status
    items = []
    for (comp_id, port_name, msg_name), st in stats.items():
        ptqu = st.ptqu_count
        ptrx = st.ptrx_count

        if ptqu == 0 and ptrx == 0:
            # Should not really happen, but skip if it does
            continue

        if ptqu == ptrx and ptqu > 0:
            status = "fully_matched"
        elif ptrx == 0 and ptqu > 0:
            status = "never_consumed"           # appears only in PTQU
        elif ptqu > ptrx:
            status = "partially_consumed"       # some queued, fewer consumed
        elif ptrx > ptqu:
            status = "over_consumed"            # more consumes than queues (unexpected)
        else:
            status = "unknown"

        items.append(
            {
                "component": comp_id,
                "port": None if port_name == "<no_port>" else port_name,
                "message": msg_name,
                "ptqu_count": ptqu,
                "ptrx_count": ptrx,
                "status": status,
                "last_queued_at": (
                    st.last_queued_at.isoformat() if st.last_queued_at else None
                ),
                "last_consumed_at": (
                    st.last_consumed_at.isoformat() if st.last_consumed_at else None
                ),
            }
        )

    logger.info("build_ptqu_ptrx_comparison: %d (component,port,message) entries", len(items))

    return {"items": items}

def _build_ptqu_ptrx_index(all_events: List[LogEvent]) -> Dict[PortKey, Dict[str, Dict[str, List[datetime]]]]:
    """
    Build an index:
      index[(comp, port)][message_name] = {
         "ptqu": [ts1, ts2, ...],
         "ptrx": [ts1, ts2, ...],
      }
    """
    index: Dict[PortKey, Dict[str, Dict[str, List[datetime]]]] = defaultdict(
        lambda: defaultdict(lambda: {"ptqu": [], "ptrx": []})
    )

    for ev in all_events:
        mnem = ev.type.upper()
        if mnem not in ("PTQU", "PTRX"):
            continue

        comp = ev.component_id
        port = getattr(ev, "port_name", None)
        if not port:
            # If you want to include “portless” events, you can fall back to some default string.
            continue

        msg_name = ev.message_name or "<unknown_message>"
        bucket = "ptqu" if mnem == "PTQU" else "ptrx"

        index[(comp, port)][msg_name][bucket].append(ev.timestamp)

    # Keep timestamps sorted per bucket
    for (_, port_dict) in index.items():
        for (_, buckets) in port_dict.items():
            buckets["ptqu"].sort()
            buckets["ptrx"].sort()

    return index

def _classify_msg_in_window(
    ptqu_times: List[datetime],
    ptrx_times: List[datetime],
    window_start: datetime,
    window_end: datetime,
) -> Tuple[str, Dict[str, datetime]]:
    """
    Classify how a message behaves relative to a time window.

    Returns:
      (classification, extra_info)

      classification ∈ {
        "FULL_CONSUME",
        "FULL_CONSUME_DIFF_TIME_FRAME",
        "NOT_FULLY_CONSUMED",
        "ONLY_OUTSIDE_WINDOW",
      }

      extra_info contains:
        - "first_in_window"
        - "last_in_window"
        - "last_consumed_any"
        - "last_consumed_in_window"
        - "last_consumed_outside"
      (only some will be present depending on case)
    """
    in_window = [t for t in ptqu_times if window_start <= t <= window_end]
    if not in_window:
        return "ONLY_OUTSIDE_WINDOW", {}

    consumed_in_window = [t for t in ptrx_times if window_start <= t <= window_end]
    consumed_outside = [t for t in ptrx_times if t < window_start or t > window_end]

    total_in = len(in_window)
    total_consumed = len(ptrx_times)

    info: Dict[str, datetime] = {
        "first_in_window": in_window[0],
        "last_in_window": in_window[-1],
    }
    if ptrx_times:
        info["last_consumed_any"] = ptrx_times[-1]
    if consumed_in_window:
        info["last_consumed_in_window"] = consumed_in_window[-1]
    if consumed_outside:
        info["last_consumed_outside"] = consumed_outside[-1]

    # Case: nothing consumed at all, or fewer consumed than queued
    if total_consumed < total_in:
        return "NOT_FULLY_CONSUMED", info

    # All occurrences consumed, and all inside window
    if total_consumed == total_in and not consumed_outside:
        return "FULL_CONSUME", info

    # All occurrences consumed, but some consumption happened outside the window
    if total_consumed == total_in and consumed_outside:
        return "FULL_CONSUME_DIFF_TIME_FRAME", info

    # Fallback classification (very unlikely with the above logic)
    return "NOT_FULLY_CONSUMED", info

def build_conclusions_from_ptqu_ptrx(
    registry: ComponentRegistry,
    unmatched_starts: List[InterleavedEvent],
) -> List[str]:
    """
    Build Conclusion#1 / Conclusion#2 sections based on PTQU/PTRX behaviour
    around each unmatched expectation start event.

    - Conclusion#1: look 1 minute BEFORE anchor.ts
    - Conclusion#2: look 1 minute AFTER anchor.ts
    """
    # Flatten all LogEvents from registry
    all_events: List[LogEvent] = []
    for hist in registry.histories():
        all_events.extend(hist.events)

    pt_index = _build_ptqu_ptrx_index(all_events)

    lines: List[str] = []
    if not unmatched_starts:
        return lines

    for concl_idx, anchor in enumerate(sorted(unmatched_starts, key=lambda e: e.ts), start=1):
        anchor_ts = anchor.ts
        idx = anchor.index

        # Two windows: -1 minute and +1 minute
        windows = [
            ("Conclusion#%d" % (2 * concl_idx - 1), anchor_ts - timedelta(minutes=1), anchor_ts),
            ("Conclusion#%d" % (2 * concl_idx),     anchor_ts,                        anchor_ts + timedelta(minutes=1)),
        ]

        for title, win_start, win_end in windows:
            lines.append(f"{title}:")
            lines.append(
                f"\tEvent{idx} started as expectation at: {anchor_ts.isoformat()} "
                f"(time window {win_start.isoformat()} .. {win_end.isoformat()})"
            )
            lines.append(f"\tStart: {win_start.isoformat()}")
            # Analyze each (component, port)
            for (comp, port), msg_map in sorted(pt_index.items(), key=lambda k: (k[0][0], k[0][1])):
                header_written = False
                for msg_name, buckets in msg_map.items():
                    ptqu_times = buckets["ptqu"]
                    ptrx_times = buckets["ptrx"]

                    cls, info = _classify_msg_in_window(ptqu_times, ptrx_times, win_start, win_end)
                    if cls in ("ONLY_OUTSIDE_WINDOW",):
                        continue  # nothing to say about this message for this window

                    if not header_written:
                        lines.append(f"\t{comp}")
                        lines.append(f"\t\t{port}:")
                        header_written = True

                    count_in = len([t for t in ptqu_times if win_start <= t <= win_end])
                    count_consumed_in = len([t for t in ptrx_times if win_start <= t <= win_end])

                    if cls == "FULL_CONSUME":
                        lines.append(
                            f"\t\t\t-{msg_name} appears ({count_in}) times in queue, "
                            f"and it was consumed ({count_in}) times => FULL_CONSUME"
                        )
                    elif cls == "FULL_CONSUME_DIFF_TIME_FRAME":
                        lines.append(
                            f"\t\t\t-{msg_name} appears ({count_in}) times in queue, "
                            f"and it was consumed ({count_in}) times but in a different time frame "
                            f"than the one I am looking into => FULL_CONSUME_DIFF_TIME_FRAME"
                        )
                        first_in = info.get("first_in_window")
                        last_out = info.get("last_consumed_outside")
                        if first_in and last_out:
                            lines.append(
                                f"\t\t\t\t--{msg_name} first appears in this interval "
                                f"{win_start.time()}-{win_end.time()} but last time was consumed at "
                                f"{last_out.time()}"
                            )
                    elif cls == "NOT_FULLY_CONSUMED":
                        total_in = len(ptqu_times)
                        total_consumed = len(ptrx_times)


                        # NEW naming
                        new_cls_name = "CONSUME_OK_IN_FRAME_NOT_FULL_GLOBALLY"

                        lines.append(
                            f"\t\t\t-{msg_name} appears ({total_in}) times in queue globally, and it was "
                            f"consumed ({total_consumed}) times => {new_cls_name}"
                        )

                        # Optional: also mention what happened in THIS window, so LLM has context
                        if count_in > 0 or count_consumed_in > 0:
                            lines.append(
                                f"\t\t\t\t(in this window: queued={count_in}, consumed={count_consumed_in})"
                            )
                # if header_written is False for this (comp, port), nothing printed

            lines.append(f"\tEnd:   {win_end.isoformat()}")
            lines.append("")  # blank between conclusions

    return lines
