---
name: yas-screenshot-annotated
description: Build an annotated YAS statusline screenshot (callouts on the default, subagents or openspec view) as an Excalidraw+ scene. Use when the user asks for an annotated or labelled statusline image, or a screenshot for the readme, landing page or a release.
---

# YAS annotated screenshots

Render one demo scenario to a PNG, then build an Excalidraw scene in the same
style as the hand-made annotated screenshots: the PNG inside one `#bbb` frame,
short lowercase labels (Excalifont, size 16), and thin pointer lines drawn as
`freedraw` strokes. The scene is uploaded to the Excalidraw+ collection
`yas!` (id `995VzGCwaVc`) where the user reviews and hand-tunes it.

The ink (labels and pointers) is white. Scenes carry a `#121212` canvas background, so a normal
export with dark mode off comes out dark. Excalidraw dark mode inverts colours, so it shows white
ink as near-black.

## Prerequisites

- `magick` (ImageMagick with Pango) and a Mono Nerd Font (see `ops/ansi_png.py`).
- `EXCALIDRAW_API_KEY` in the environment. Never write it to disk.
- Python 3.12. `annotate.py`, `combine.py` and `upload_scene.py` use the
  standard library only.
- For PNG/GIF output, the `excalidraw-render` Docker image from the
  excalidraw-diagram skill. It drives real Excalidraw, so Excalifont and the
  pressure-smoothed pointer strokes match the editor. Build it once from the
  skill's real directory, because the installed skill dir holds symlinks that
  Docker cannot follow:
  `cd "$(dirname "$(readlink -f ~/.claude/skills/excalidraw-diagram/Dockerfile)")" && docker build -t excalidraw-render .`
  `scripts/gif.sh` uses it when the image exists. Without it, set
  `RENDERER=python` to fall back to `scripts/render.py` (needs Pillow; draws
  plain polylines, so it only approximates the editor).
  Renderer quirks, handled in `gif.sh`: strip frames first, render without
  `--dark`, chop a 3 px light strip off the right and bottom edges, and never
  use `-layers optimize` on the GIF.

## Views

Each view has a view preset at `assets/views/<view>.json`.

| View | Scenario | Shows |
|---|---|---|
| `default` | `sonnet-thinking` | bare statusline: every top-level section |
| `subagents` | `subagents` | flat subagent list: duration, name, model, task description, tokens, current activity |
| `openspec` | `openspec` | spec progress rows under the main sections |

## Steps

Run from the repo root. The script is `.claude/skills/yas-screenshot-annotated/scripts/shoot-annotated.sh <view> [scene name]`.
The header of that script explains the env knobs, including `SCENE_ID`.

1. **Build** with `NO_UPLOAD=1`:

   ```bash
   NO_UPLOAD=1 .claude/skills/yas-screenshot-annotated/scripts/shoot-annotated.sh default
   ```

2. **Check the warnings.** `annotate.py` prints skipped-label and overlap warnings on stderr.
   Done when there are none, or each remaining one is noted for the report.

3. **Upload.** Run the same command without `NO_UPLOAD=1`. By default it replaces the
   scene in place. Done when the script exits 0 and prints `element count: local N, live M`
   with N == M.

4. **Report** the scene URL, the scene id, skipped labels and open doubts.

## All views in one scene

`scripts/shoot-all.sh <scene-name> <view>...` builds each view with `NO_UPLOAD=1`, stacks them
with `scripts/combine.py` (frames at x = 0, 150 px gap, in the order given), writes
`.scratch/<branch>/annotated/all.excalidraw`, and uploads it with the same live-count check.
The stored scene id is in `assets/all.json`, and the rules are the same as for single views (see the script header).

```bash
.claude/skills/yas-screenshot-annotated/scripts/shoot-all.sh 'yas 0.9.6 — all views (annotated)' default subagents openspec
```

`combine.py <out> <in>...` can also be run alone on built `<view>.excalidraw` files. It
re-assigns `index` values, merges `files`, and fails if two element ids collide.
Warning: this re-renders each view, so any hand edit made in Excalidraw+ is overwritten.

## Where to read next

- Read `LABELS.md` when you add or reword a label, or add a view.
- Read `RENDER.md` when you need a local PNG or GIF of a scene.
- Read the `scripts/upload_scene.py` docstring for upload, tombstone and live-count mechanics.
