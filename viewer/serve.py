#!/usr/bin/env python3
"""Convenience launcher for the whisper2 viewer.

Usage:
    python serve.py                         # serve viewer/ on http://localhost:8765
    python serve.py path/to/output_dir/     # also auto-load the diagnostics + frames files
    python serve.py path/to/diag.json       # auto-load a specific diagnostics file
    python serve.py /full/path file.log     # also try to detect output_<basename>/ next to the log

By default opens the default browser to the right URL.
"""

from __future__ import annotations

import argparse
import http.server
import os
import socketserver
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote


def find_output_files(target: Path) -> tuple[Path | None, Path | None]:
    """Return (diagnostics_json, frames_json) given a target.

    target can be:
      - a directory (output_<base>/) — scan for *_diagnostics.json and *_frames.json
      - a *_diagnostics.json file directly
      - a *.log file — look for sibling output_<basename>/
    """
    if not target.exists():
        return None, None

    if target.is_file():
        name = target.name.lower()
        if name.endswith("_diagnostics.json"):
            base = target.parent
            stem = target.name[:-len("_diagnostics.json")]
            frames = base / f"{stem}_frames.json"
            return target, frames if frames.exists() else None
        if name.endswith(".log"):
            base = target.parent
            stem = target.stem
            out = base / f"output_{stem}"
            if out.is_dir():
                return find_output_files(out)
            return None, None
        # Maybe a frames.json
        if name.endswith("_frames.json"):
            stem = target.name[:-len("_frames.json")]
            diag = target.parent / f"{stem}_diagnostics.json"
            return (diag if diag.exists() else None), target
        return None, None

    if target.is_dir():
        diag = next(iter(target.glob("*_diagnostics.json")), None)
        frames = next(iter(target.glob("*_frames.json")), None)
        return diag, frames

    return None, None


def main() -> int:
    ap = argparse.ArgumentParser(description="Launch the whisper2 viewer locally.")
    ap.add_argument("target", nargs="?", help="Output dir, diagnostics.json, frames.json, or .log")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-open", action="store_true", help="Don't open a browser tab")
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    os.chdir(here)

    # Determine auto-load files
    diag = frames = None
    if args.target:
        diag, frames = find_output_files(Path(args.target).resolve())
        if diag is None:
            print(f"[warn] No diagnostics.json found at/near {args.target}", file=sys.stderr)

    # Copy / symlink files into viewer dir if outside
    auto_args = ""
    if diag and diag.exists():
        target_diag = here / diag.name
        if diag.parent.resolve() != here:
            try:
                target_diag.write_bytes(diag.read_bytes())
                print(f"[copy] {diag} -> {target_diag.name}")
            except OSError as exc:
                print(f"[warn] could not copy {diag}: {exc}", file=sys.stderr)
        auto_args = f"?file={quote(target_diag.name)}"
    if frames and frames.exists():
        target_frames = here / frames.name
        if frames.parent.resolve() != here:
            try:
                target_frames.write_bytes(frames.read_bytes())
                print(f"[copy] {frames} -> {target_frames.name}")
            except OSError as exc:
                print(f"[warn] could not copy {frames}: {exc}", file=sys.stderr)
        auto_args += ("&" if auto_args else "?") + f"frames={quote(target_frames.name)}"

    url = f"http://{args.host}:{args.port}/{auto_args}"

    # Start server
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer((args.host, args.port), handler) as httpd:
        print(f"[serve] whisper2 viewer at {url}")
        print("[serve] Ctrl-C to stop")

        if not args.no_open:
            threading.Timer(0.5, lambda: webbrowser.open_new_tab(url)).start()

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[serve] stopped")
            return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
