# whisper2 viewer

Self-contained interactive HTML viewer for the JSON outputs produced by
`whisper2/main.py`. No build step, no install, no server required to use.

## Quick start

After running `python main.py <yourlog>.log --frames`, you'll have an output
directory like `output_<yourlog>/` containing:

- `<yourlog>_diagnostics.json` — verdict, flows, log warnings (always)
- `<yourlog>_frames.json`      — per-component state frames (when `--frames` is used)

Open the viewer:

### Option 1 — open directly (file://)
```
xdg-open viewer/index.html      # Linux
open viewer/index.html          # macOS
start viewer/index.html         # Windows
```
Then drag-and-drop the `_diagnostics.json` (and optionally `_frames.json`)
files onto the dropzone.

### Option 2 — local HTTP server (recommended; allows `?file=` auto-load)
```
cd Backend/whisper2/viewer
python -m http.server 8765
```
Then open in browser:

- **Drop UI**: <http://localhost:8765/>
- **Auto-load**: <http://localhost:8765/?file=PATH/diagnostics.json&frames=PATH/frames.json>

You can also just place the JSON files next to `index.html` and hit:

- <http://localhost:8765/?file=pit_oam_K3_diagnostics.json&frames=pit_oam_K3_frames.json>

### Option 3 — one-shot launcher
```
python serve.py /path/to/output_dir   # starts server + opens browser
```

## What you get

| Tab           | Shows                                                                                  |
|---------------|----------------------------------------------------------------------------------------|
| **Overview**  | KPI cards (verdict, flows, issues, components, warnings, duration) + top issues/missing |
| **Issues**    | All verdict failures / regressions / missing-tcfi, click → drawer with details          |
| **Flows**     | All declared TC/Startup flows with pass/fail icons, validating component, location      |
| **Components**| Grid of component cards with sparklines + state machine (Mermaid) on click             |
| **Warnings**  | pllg WRN/ERR signals collapsed by template, count, first/last seen                     |

## Keyboard shortcuts

- `1`..`5` — switch tabs
- `/`      — focus search
- `Esc`    — close drawer / clear search
- `t`      — toggle dark/light theme
- `l`      — load files

## Dependencies

- Modern browser (Chrome 90+, Firefox 90+, Safari 15+, Edge 90+)
- Internet for fonts (Inter, JetBrains Mono via Google Fonts) and Mermaid via CDN
- Or: download fonts and Mermaid offline if your env is air-gapped (TODO)

## Files

| File          | Purpose                                  | Size |
|---------------|------------------------------------------|------|
| `index.html`  | Markup + topbar / sidebar / views layout | ~11 KB |
| `styles.css`  | Design tokens, components, animations    | ~30 KB |
| `app.js`      | State management, rendering, mermaid     | ~43 KB |
| `serve.py`    | Convenience launcher (optional)          | ~1 KB |

Total: ~85 KB of code. Runs entirely client-side.
