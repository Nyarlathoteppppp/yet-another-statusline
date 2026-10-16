# Local rendering and GIF

`gif.sh` renders with the `excalidraw-render` Docker image when it exists (real Excalidraw; see the
prerequisites in `SKILL.md`), else with `render.py`. Force one with `RENDERER=docker|python`.
`render.py` needs Pillow; `gif.sh` also needs `magick`.

- `scripts/render.py <scene.excalidraw> <out.png> [--bg #121212] [--pad 40] [--scale 2]` draws a scene
  with Pillow: embedded image, Excalifont text (`assets/fonts/`, licence in the README beside it) at each
  element's x,y with lineHeight 1.25, and freedraw polylines. The canvas is the tight bounding box of all
  non-frame elements plus `--pad` on each side. Stored ink is white (`#ffffff`): it is drawn as-is on a
  dark `--bg` (default: the scene's `appState.viewBackgroundColor`, else `#121212`) and as `#1e1e1e` on a light `--bg` such as `--bg white`.
- `scripts/gif.sh <out.gif> <scene.excalidraw>...` renders each scene, pads all frames to the largest size
  (top-left aligned, so the statusline stays fixed), 250 cs per frame, loops forever.
  Order scenes smallest to largest. Env overrides: `BG`, `PAD`, `SCALE`, `DELAY`, `RESIZE`, `RENDERER`.
  Frames are stored whole: `-layers optimize` corrupts labels between frames.
- `render.py` differences from a true Excalidraw render: freedraw is drawn as a plain polyline (no pressure
  variation), and glyph baseline placement is estimated from font metrics.
