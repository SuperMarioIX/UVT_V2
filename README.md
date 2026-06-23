# whisper2

`whisper2` is a local TTCN-3 runtime log analyzer. It reads a flat, interleaved
k3 execution log, reconstructs the behavior of each component, detects common
failure signals, and exports structured JSON files that can be inspected in a
static browser viewer.

The project is designed for offline debugging: no backend service, database, or
testcase instrumentation is required.

## What It Does

- Parses TTCN-3/k3 log lines such as `cocr`, `cost`, `dtac`, `dtde`, `codo`,
  `cofi`, `ptqu`, `ptrx`, and `ptsd`.
- Reconstructs per-component lifecycle histories from the global interleaved log.
- Builds frame-based timelines with incoming, consumed, and outgoing messages.
- Detects verdict issues from `tcfi`, `cofi`, and `setv` lines.
- Validates declared `TC flow` and `Startup flow` messages.
- Groups relevant `pllg` warning/error messages.
- Produces JSON and text output files for diagnostics, overview, summaries, and
  viewer visualization.

## Project Structure

```text
whisper2/
  main.py                    # CLI entry point and pipeline orchestration
  src/
    parsing.py               # Log line and timestamp parsing
    engine.py                # Per-component runtime reconstruction
    frames.py                # Frame/timeline generation
    verdict_detector.py      # Global and component verdict analysis
    flow_validator.py        # TC/Startup flow validation
    log_warnings.py          # Runtime warning aggregation
    overview.py              # High-level profiling and queue analysis
    models.py                # Internal data model
    basic_utilities.py       # Shared helper functions
    tool_logger.py           # Tool logging utilities
  tests/                     # Pytest test suite
  viewer/
    index.html               # Static UI
    app.js                   # Viewer state and rendering logic
    styles.css               # Viewer styling
    serve.py                 # Optional local viewer launcher
```

## Requirements

- Python 3.10+
- `pytest` only if you want to run the test suite
- A modern browser for the viewer

No Python package installation is required for normal analyzer usage.

## Quick Start

Run the analyzer on a TTCN-3/k3 log file:

```bash
python main.py path/to/test.log --frames
```

This creates an output folder next to the input log:

```text
output_test/
  test_diagnostics.json
  test_diagnostics.log
  test_frames.json
  test_frames_summary.log
```

Open the viewer:

```bash
python viewer/serve.py path/to/output_test
```

Or open `viewer/index.html` directly in a browser and drag-and-drop the generated
`*_diagnostics.json` and `*_frames.json` files.

## CLI Usage

```bash
python main.py <log_file> [options]
```

Common options:

```text
--frames              Generate per-component frames JSON and text summary
--overview            Generate high-level overview/profiling output
--summary             Generate a full registry summary JSON
--diagnostics-only    Run only verdict, flow, and warning diagnostics
--lowActivity         Hide low-activity components from frames/overview output
--strict              Stop on the first parsing or processing error
--out PATH            Write the selected JSON output to a specific path
--output_dir PATH     Use a custom output directory
--output_base NAME    Use a custom output base name
--no-diagnostics      Skip diagnostics JSON/log generation
--indent N            JSON indentation level, default is 2
```

### Examples

Generate diagnostics and component frames:

```bash
python main.py pit_oam_K3.log --frames
```

Run a fast pass/fail diagnostic check:

```bash
python main.py pit_oam_K3.log --diagnostics-only
```

Generate an LLM-friendly overview:

```bash
python main.py pit_oam_K3.log --overview
```

Generate frames while hiding low-activity components:

```bash
python main.py pit_oam_K3.log --frames --lowActivity
```

## Output Files

### Diagnostics

`*_diagnostics.json` answers: did the testcase pass, and what issues were found?

It contains:

- global verdict and testcase name;
- component verdicts;
- verdict transitions and issues;
- flow validation results;
- grouped runtime warnings.

Example:

```json
{
  "verdict": {
    "test_name": "Example.test",
    "global_verdict": "pass",
    "passed": true,
    "summary": {
      "total_issues": 0,
      "by_severity": {},
      "by_kind": {}
    }
  },
  "flows": {
    "summary": {
      "declared": 3,
      "validated": 3,
      "missing": 0,
      "all_validated": true
    }
  }
}
```

### Frames

`*_frames.json` answers: what happened to each component over time?

Each component is represented as a sequence of frames:

```json
{
  "MTC": {
    "Frame1[18:22:47.933696]": {
      "State": "COMPONENT_CREATED",
      "Incoming_messages": [],
      "Consumed_messages": [],
      "Outgoing_messages": [],
      "ico_summary": {
        "in": 0,
        "consume": 0,
        "out": 0
      }
    },
    "Frame8[18:22:51.220811]": {
      "State": "OPERATING",
      "Incoming_messages": [],
      "Consumed_messages": [],
      "Outgoing_messages": ["extProcNew.ExtProcRequest"],
      "ico_summary": {
        "in": 0,
        "consume": 0,
        "out": 1
      }
    }
  }
}
```

## Viewer

The viewer is a static web app located in `viewer/`. It consumes generated JSON
files and displays:

- overview KPI cards;
- verdict and issue lists;
- validated and missing flows;
- component cards and state diagrams;
- recent component activity;
- grouped runtime warnings.

Launch with:

```bash
python viewer/serve.py output_test
```

Or manually:

```bash
cd viewer
python -m http.server 8765
```

Then open:

```text
http://localhost:8765/
```

## Low-Activity Filtering

When `--lowActivity` is used with `--frames` or `--overview`, the tool removes
components that are unlikely to be useful during debugging.

Current rules are OR-ed together:

- the component has no message activity;
- the component has at most `LOW_ACTIVITY_MAX_FRAMES` frames and at most
  `LOW_ACTIVITY_MAX_TOTAL_MSGS` total messages;
- the component receives/consumes messages but never sends anything out.

The thresholds are defined in `src/models.py`.

## Running Tests

Install pytest if needed:

```bash
pip install pytest
```

Run the test suite:

```bash
pytest
```

The tests cover parsing, runtime reconstruction, frame generation, flow
validation, verdict detection, warning scanning, overview generation, and the CLI.

## Architecture Summary

The analyzer uses a small pipeline:

```text
raw .log file
  -> preprocessing and parsing
  -> component registry reconstruction
  -> diagnostics detectors
  -> frames / overview / summary generation
  -> JSON + text artifacts
  -> static viewer
```

This keeps the analyzer and viewer decoupled. The Python side understands the
raw TTCN-3 log format, while the browser side only renders stable JSON outputs.