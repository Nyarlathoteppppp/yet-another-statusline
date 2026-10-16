#!/usr/bin/env bash
# Render a demo scenario, build the annotated Excalidraw scene for one view preset,
# and upload it to the Excalidraw+ "yas!" collection.
#
#   shoot-annotated.sh <view> [scene-name]
#
#   view        basename of assets/views/<view>.json (default, subagents, openspec, ...)
#   scene-name  name of the uploaded scene; default "yas <version> - <view> view (annotated)"
#
# Env:
#   NO_UPLOAD=1      build the .excalidraw file only
#   COLLECTION=...   Excalidraw+ collection name or id (default: yas!)
#   SCENE_ID=...     scene id precedence (first match wins):
#                      1. env SCENE_ID, if set
#                      2. "scene_id" in assets/views/<view>.json, if present
#                      3. otherwise create a new scene in COLLECTION
#                    A resolved id replaces that existing scene in place (keeps its links
#                    valid); COLLECTION and scene-name are then ignored.
#                    SCENE_ID=new skips the preset id and forces a new scene.
#   SCALE=1.0        passed to annotate.py --scale
#   YAS_DEMO_*       passed through to ops/ansi_png.py (font, size, pad, dpi, colours)
#   EXCALIDRAW_API_KEY  required for the upload; never written to disk
#
# Output: .scratch/<branch-shelf>/annotated/<view>.excalidraw (plus the txt/png it used).
set -euo pipefail

[ $# -ge 1 ] || { echo "usage: shoot-annotated.sh <view> [scene-name]" >&2; exit 2; }

view=$1
here=$(cd "$(dirname "$0")" && pwd)
yas_root=$(git rev-parse --show-toplevel)
spec="$here/../assets/views/$view.json"
[ -f "$spec" ] || { echo "no such view: $spec" >&2; exit 2; }

scenario=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["scenario"])' "$spec")
version=$(sed -n 's/^version = "\(.*\)"/\1/p' "$yas_root/pyproject.toml" | head -1)
name=${2:-"yas $version - $view view (annotated)"}
shelf=$(git -C "$yas_root" branch --show-current | tr / -)
out_dir="$yas_root/.scratch/$shelf/annotated"
mkdir -p "$out_dir"

# 1. Render the scenario PNG (also rewrites demo/*.txt).
( cd "$yas_root" && DEMO_ONLY="$scenario" make demo/img >/dev/null )
case "$scenario" in
  subagent-tree-*) src="$yas_root/demo/subagents/$scenario" ;;
  *)               src="$yas_root/demo/$scenario" ;;
esac
# Copy straight away: another `make demo/img` could rewrite demo/ between steps,
# and the txt and png must come from the same render.
cp "$src.png" "$out_dir/$view.png"
cp "$src.txt" "$out_dir/$view.txt"

# 2. Build the scene.
python3 "$here/annotate.py" \
  --png   "$out_dir/$view.png" \
  --txt   "$out_dir/$view.txt" \
  --spec  "$spec" \
  --out   "$out_dir/$view.excalidraw" \
  --pad   "${YAS_DEMO_PAD:-24}" \
  --scale "${SCALE:-1.0}"

# Resolve the scene id (see the SCENE_ID header above); "new" means create a scene.
scene_id=${SCENE_ID:-$(jq -r '.scene_id // empty' "$spec")}
[ "$scene_id" != new ] || scene_id=
[ -z "${NO_UPLOAD:-}" ] || {
  echo "scene id: ${scene_id:-new}" >&2
  echo "NO_UPLOAD set: $out_dir/$view.excalidraw"; exit 0
}

# 3. Upload (or replace in place) and print the scene URL.
# With a scene id, upload_scene.py tombstones live elements missing from the new build and
# checks the live element count against the local count (exit 1 on mismatch).
rc=0
if [ -n "$scene_id" ]; then
  log=$(python3 "$here/upload_scene.py" "$out_dir/$view.excalidraw" --scene-id "$scene_id") || rc=$?
else
  log=$(python3 "$here/upload_scene.py" "$out_dir/$view.excalidraw" \
    --collection "${COLLECTION:-yas!}" --name "$name") || rc=$?
fi
echo "$log"
[ "$rc" -eq 0 ] || { echo "upload check failed (exit $rc)" >&2; exit "$rc"; }
id=$(printf '%s\n' "$log" | sed -n 's/^scene id: *//p' | head -1)
[ -n "$id" ] && echo "scene url: https://app.excalidraw.com/s/1BFmCncTPVB/$id"
