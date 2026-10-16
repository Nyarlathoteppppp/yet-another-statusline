#!/usr/bin/env bash
# Usage: gif.sh <out.gif> <scene.excalidraw>...
#   env: BG=#121212 PAD=60 SCALE=2 DELAY=250 RESIZE=50% RENDERER=docker|python
#
# Renders each scene to PNG, pads every frame to the largest size (top-left
# aligned, so the statusline stays in place while the frame grows), and joins
# them into a looping GIF. Pass scenes in display order (smallest first).
#
# RENDERER=docker (default when the image exists) uses the excalidraw-diagram
# skill's `excalidraw-render` image, which drives real Excalidraw: Excalifont,
# pressure-smoothed freedraw, exact text placement. Build it once from the
# skill's REAL directory (the skill dir holds symlinks Docker cannot follow):
#   cd "$(dirname "$(readlink -f ~/.claude/skills/excalidraw-diagram/Dockerfile)")"
#   docker build -t excalidraw-render .
# RENDERER=python falls back to render.py (approximate: plain polylines).
#
# Renderer notes (docker):
# - Frames are stripped first: a frame adds its title and a large border.
# - Render WITHOUT --dark. These scenes already come out on a #121212 canvas
#   with light strokes; --dark inverts them to a light-grey canvas.
# - The export has a 1-3 px light strip on the right and bottom edges; it is
#   chopped off before padding.
# - GIF frames are written whole (no -layers optimize): frame differencing
#   corrupts labels and glyphs between frames of different content.
set -euo pipefail
out=$(realpath -m "$1"); shift
here=$(cd "$(dirname "$0")" && pwd)
bg=${BG:-#121212}
pad=${PAD:-60}
scale=${SCALE:-2}
renderer=${RENDERER:-}
if [ -z "$renderer" ]; then
  if docker image inspect excalidraw-render >/dev/null 2>&1; then renderer=docker; else renderer=python; fi
fi
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
frames=()
for scene in "$@"; do
  name=$(basename "${scene%.excalidraw}")
  png="$tmp/$name.png"
  if [ "$renderer" = docker ]; then
    python3 -I - "$scene" "$tmp/$name.excalidraw" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
d['elements'] = [{**e, 'frameId': None} for e in d['elements'] if e['type'] != 'frame']
json.dump(d, open(sys.argv[2], 'w'))
EOF
    docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$tmp:/data" excalidraw-render \
      "$name.excalidraw" --output "$name-raw.png" --scale "$scale" >/dev/null
    magick "$tmp/$name-raw.png" -gravity southeast -chop 3x3 +gravity \
      -bordercolor "$bg" -border "$pad" "$png"
  else
    python3 "$here/render.py" "$scene" "$png" --bg "$bg" --pad "$pad" --scale "$scale"
  fi
  echo "rendered $name ($renderer): $(magick identify -format '%wx%h' "$png")"
  frames+=("$png")
done
dims=$(magick identify -format '%w %h\n' "${frames[@]}" | awk '{if($1>w)w=$1;if($2>h)h=$2}END{print w"x"h}')
magick -delay "${DELAY:-250}" -loop 0 "${frames[@]}" \
  -background "$bg" -gravity northwest -extent "$dims" -resize "${RESIZE:-50%}" \
  -set dispose background +repage "$out"
magick identify "$out" | awk '{print $1,$3}'
