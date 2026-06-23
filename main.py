#!/usr/bin/env python3
import json
import logging
import argparse
from pathlib import Path
from typing import Optional
from src.tool_logger import logger, SizeAwareFileHandler
from src.engine import process_events
from src.frames import build_frames_summary, build_frames_text_summary
from src.summary import summarize_registry, registry_to_jsonable
from src.overview import build_overview_profiling, is_low_activity_component, build_llm_overview, is_low_activity_history
from src.basic_utilities import preprocess_lines
from src.parsing import find_test_finish_timestamp
from src.flow_validator import validate_flows, FlowReport
from src.verdict_detector import (
    detect_verdict_issues,
    component_verdicts_from_registry,
    VerdictReport,
)
from src.log_warnings import scan_log_warnings, LogWarningsReport

def setup_interleaved_debug_logger(path: Path) -> logging.Logger:
    log = logging.getLogger("interleaved")
    log.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
    )

    for h in log.handlers:
        if isinstance(h, SizeAwareFileHandler) and h.baseFilename == str(path):
            return log

    fh = SizeAwareFileHandler(
        str(path),
        mode="w",
        encoding="utf-8",
        soft_limit_mb=50,
    )
    fh.setFormatter(formatter)
    log.addHandler(fh)

    return log


def _write_diagnostics(
    output_dir: Path,
    base_name: str,
    flow_report: FlowReport,
    verdict_report: VerdictReport,
    log_warnings_report: LogWarningsReport,
    args,
) -> None:
    """Write <base>_diagnostics.json and <base>_diagnostics.log."""
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / f"{base_name}_diagnostics.json"
    log_path = output_dir / f"{base_name}_diagnostics.log"

    payload = {
        "verdict": verdict_report.to_jsonable(),
        "flows": flow_report.to_jsonable(),
        "log_warnings": log_warnings_report.to_jsonable(),
    }
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=args.indent)
    logger.info("Saved diagnostics JSON to: %s", json_path)

    text = "=== VERDICT ===\n" + verdict_report.to_text()
    text += "\n=== FLOWS ===\n" + flow_report.to_text()
    text += "\n=== LOG WARNINGS ===\n" + log_warnings_report.to_text()
    with log_path.open("w", encoding="utf-8") as f:
        f.write(text)
    logger.info("Saved diagnostics log to: %s", log_path)


def _print_console_summary(
    flow_report: FlowReport,
    verdict_report: VerdictReport,
    log_warnings_report: Optional[LogWarningsReport] = None,
) -> None:
    """One-line-ish PASS/FAIL summary printed at the end of every run.

    Note: we print to stdout (not the logger) so that this is always visible
    regardless of logger configuration.
    """
    g = verdict_report.global_verdict or "?"
    n_issues = len(verdict_report.issues)
    n_flows_ok = flow_report.validated_total
    n_flows_total = flow_report.declared_total
    n_missing = flow_report.missing_total

    overall_ok = (
        g == "pass"
        and n_issues == 0
        and n_missing == 0
        and n_flows_total > 0
    )
    mark = "PASS" if overall_ok else "FAIL"

    print()
    print(f"=== {mark} ===")
    if verdict_report.test_name:
        print(f"  test:    {verdict_report.test_name}")
    print(f"  verdict: {g}")
    print(f"  flows:   {n_flows_ok}/{n_flows_total} validated"
          + (f"  ({n_missing} missing)" if n_missing > 0 else ""))
    print(f"  issues:  {n_issues}")

    if log_warnings_report is not None:
        suppressed = sum(log_warnings_report.whitelist_hits.values())
        print(
            f"  pllg:    {log_warnings_report.total_kept} kept "
            f"({suppressed} suppressed) — {len(log_warnings_report.items)} unique"
        )

    if n_missing > 0:
        print("  missing flows:")
        for r in flow_report.results:
            if not r.validated:
                print(f"    - #{r.index} ({r.kind}): {r.message}")

    if verdict_report.issues:
        print("  top issues:")
        for i in verdict_report.issues[:5]:
            comp = f" [{i.component}]" if i.component else ""
            print(f"    - {i.severity}{comp}: {i.message}")
        if n_issues > 5:
            print(f"    ... +{n_issues - 5} more (see diagnostics.log)")

    print()


def main() -> None:
    ap = argparse.ArgumentParser(description="TTCN-3 log analyzer: timelines, summaries, frames")
    ap.add_argument("file", help="Path to input log file")  # ✅ POSITIONAL argument (not --file)
    ap.add_argument("--out", help="Path to output JSON file")
    ap.add_argument("--output_dir", help="Output directory (used by API, overrides default)")
    ap.add_argument("--output_base", help="Base name for output files (used by API)")
    ap.add_argument("--indent", type=int, default=2)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument(
        "--lowActivity",
        action="store_true",
        help="When used with --frames or --overview, drop low-activity components from output",
    )
    ap.add_argument(
        "--overview",
        action="store_true",
        help="Produce high-level overview to be used by LLM",
    )
    ap.add_argument(
        "--frames",
        action="store_true",
        help="Emit per-component frames split summary",
    )
    ap.add_argument(
        "--diagnostics-only",
        action="store_true",
        help="Run only the flow validator + verdict detector (skip frames/registry build). "
             "Use this for a fast pass/fail check.",
    )
    ap.add_argument(
        "--no-diagnostics",
        action="store_true",
        help="Skip writing the *_diagnostics.json/.log files (they are written by default).",
    )
    args = ap.parse_args()

    # Configure debug.log (file-only, always on)
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        fh = SizeAwareFileHandler(
            "debug.log",
            mode="w",
            encoding="utf-8",
            soft_limit_mb=500,
        )
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
        )
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    logger.info(
        "Program start: file=%s out=%s output_dir=%s output_base=%s indent=%d strict=%s summary=%s frames=%s lowActivity=%s overview=%s",
        args.file,
        args.out,
        args.output_dir,
        args.output_base,
        args.indent,
        args.strict,
        args.summary,
        args.frames,
        args.lowActivity,
        args.overview,
    )

    in_path = Path(args.file)
    logger.debug("Opening input file: %s", in_path)

    with in_path.open("r", encoding="utf-8") as f:
        raw_lines = f.readlines()
    logger.debug("Read %d raw lines from %s", len(raw_lines), in_path)

    # Base name and output directory (used when --out is not provided)
    base_name = in_path.stem  # e.g. "fail" for "fail.log"

    # Use API-provided output_dir if given, otherwise use default
    if args.output_dir:
        output_dir = Path(args.output_dir)
        # If output_base is also provided, use it as the base name
        if args.output_base:
            base_name = args.output_base
    else:
        output_dir = in_path.parent / f"output_{base_name}"

    # ============================================================
    # DIAGNOSTICS PHASE  (always: flow validator + verdict detector
    # + log warnings scan). Cheap, single-pass over raw_lines; produces
    # an answer to "did the test pass and which flows did/didn't validate".
    # ============================================================
    flow_report = validate_flows(raw_lines)
    log_warnings_report = scan_log_warnings(raw_lines)

    # diagnostics-only mode: skip the heavy registry build
    if args.diagnostics_only:
        verdict_report = detect_verdict_issues(raw_lines, component_verdicts=None)
        _write_diagnostics(
            output_dir, base_name, flow_report, verdict_report, log_warnings_report, args,
        )
        _print_console_summary(flow_report, verdict_report, log_warnings_report)
        logger.info("Program finished in --diagnostics-only mode")
        return

    test_finish_ts = find_test_finish_timestamp(raw_lines)
    lines = preprocess_lines(raw_lines)
    logger.debug("After preprocess_lines: kept %d lines", len(lines))

    registry, skipped = process_events(lines, strict=args.strict)
    logger.info(
        "process_events finished: components=%d skipped=%d",
        len(registry.by_id),
        skipped,
    )

    verdict_report = detect_verdict_issues(
        raw_lines,
        component_verdicts=component_verdicts_from_registry(registry),
    )

    if not args.no_diagnostics:
        _write_diagnostics(
            output_dir, base_name, flow_report, verdict_report, log_warnings_report, args,
        )

    # FRAMES MODE
    if args.frames:
        frames_payload = build_frames_summary(registry)

        if args.lowActivity:
            before = len(frames_payload)
            filtered = {
                comp_id: frames
                for comp_id, frames in frames_payload.items()
                if not is_low_activity_component(frames)
            }
            dropped = before - len(filtered)
            logger.info(
                "Low-activity filter applied for frames: before=%d after=%d dropped=%d",
                before,
                len(filtered),
                dropped,
            )
            frames_payload = filtered

        # JSON output path
        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            output_dir.mkdir(parents=True, exist_ok=True)
            out_path = output_dir / f"{base_name}_frames.json"

        logger.debug("Writing frames summary to: %s", out_path)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(frames_payload, f, indent=args.indent)
        logger.info("Saved frames summary to: %s", out_path)

        # Text summary log path
        if args.out:
            frames_log_path = out_path.with_name(out_path.stem + "_summary.log")
        else:
            frames_log_path = output_dir / f"{base_name}_frames_summary.log"

        frames_text = build_frames_text_summary(
            frames_payload,
            registry,
            test_finish_ts,
        )
        with frames_log_path.open("w", encoding="utf-8") as f:
            f.write(frames_text)

        logger.info("Saved frames text summary to: %s", frames_log_path)
        logger.info("Program finished with frames output")
        _print_console_summary(flow_report, verdict_report, log_warnings_report)
        return

    # SUMMARY MODE
    if args.summary:
        summary = summarize_registry(registry, parse_errors_count=skipped)

        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            output_dir.mkdir(parents=True, exist_ok=True)
            out_path = output_dir / f"{base_name}_summary.json"

        logger.debug("Writing summary to: %s", out_path)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(summary, f, indent=args.indent)
        logger.info("Saved summary JSON to: %s", out_path)
        logger.info("Program finished with summary output")

    # OVERVIEW MODE
    if args.overview:
        # Optional low-activity filter on registry for overview
        if args.lowActivity:
            before = len(registry.by_id)
            registry.by_id = {
                comp_id: hist
                for comp_id, hist in registry.by_id.items()
                if not is_low_activity_history(hist)
            }
            after = len(registry.by_id)
            logger.info(
                "Low-activity filter applied for overview: before=%d after=%d dropped=%d",
                before,
                after,
                before - after,
            )

        # Create debug log for BM_IMS_CLIENT / IM expectation parsing
        interleaved_log_path = in_path.with_suffix(".interleaved_debug.log")
        interleaved_debug_logger = setup_interleaved_debug_logger(interleaved_log_path)

        profiling = build_overview_profiling(registry)
        llm_json, llm_text = build_llm_overview(registry, lines, interleaved_debug_logger)

        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_log_path = out_path.with_suffix(".log")
        else:
            output_dir.mkdir(parents=True, exist_ok=True)
            out_path = output_dir / f"{base_name}_overview.json"
            out_log_path = output_dir / f"{base_name}_overview.log"

        with out_path.open("w", encoding="utf-8") as f:
            json.dump(profiling, f, indent=args.indent)

        with out_log_path.open("w", encoding="utf-8") as f:
            f.write(llm_text)

        logger.info("Saved overview log to: %s", out_log_path)
        logger.info("Saved profiling overview to: %s", out_path)
        logger.info("Saved BM_IMS_CLIENT debug log to: %s", interleaved_log_path)

        logger.info("Program finished with overview profiling output")
        _print_console_summary(flow_report, verdict_report, log_warnings_report)
        return

    # DEFAULT: FULL REGISTRY DUMP
    payload = registry_to_jsonable(registry)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / f"{base_name}.json"

    logger.debug("Writing full registry to: %s", out_path)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=args.indent)
    logger.info("Saved JSON to: %s", out_path)
    logger.info("Program finished with full JSON output")
    _print_console_summary(flow_report, verdict_report, log_warnings_report)

if __name__ == "__main__":
    main()