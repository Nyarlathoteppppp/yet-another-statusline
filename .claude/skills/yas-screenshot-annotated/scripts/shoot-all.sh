#!/usr/bin/env bash
# Build several annotated views, stack them in ONE Excalidraw scene, and upload it.
#
#   shoot-all.sh <scene-name> <view> [view ...]
#
# Each view is built with NO_UPLOAD=1 (shoot-annotated.sh), then combine.py stacks the
# frames vertically (x = 0, 150 px gap) into .scratch/<branch-shelf>/annotated/all.excalidraw.
#
# Env:
#   NO_UPLOAD=1      stop after combining
#   COLLECTION=...   Excalidraw+ collection name or id (default: yas!)
#   SCENE_ID=...     scene id precedence (first match wins):
#                      1. env SCENE_ID, if set
#                      2. "scene_id" in assets/all.json, if present
#                      3. otherwise create a new scene in COLLECTION
#                    A resolved id replaces that existing scene in place (keeps its links
#                    valid); COLLECTION and scene-name are then ignored.
#                    SCENE_ID=new skips the stored id and forces a new scene.
#   Other env (SCALE, YAS_DEMO_*) passes through to shoot-annotated.sh.
set -euo pipefail

[ $# -ge 2 ] || { echo "usage: shoot-all.sh <scene-name> <view> [view ...]" >&2; exit 2; }

name=$1; shift
here=$(cd "$(dirname "$0")" && pwd)
yas_root=$(git rev-parse --show-toplevel)
shelf=$(git -C "$yas_root" branch --show-current | tr / -)
out_dir="$yas_root/.scratch/$shelf/annotated"

files=()
for view in "$@"; do
  # SCENE_ID is for the combined scene, not the per-view builds.
  env -u SCENE_ID NO_UPLOAD=1 "$here/shoot-annotated.sh" "$view"
  files+=("$out_dir/$view.excalidraw")
done

python3 "$here/combine.py" "$out_dir/all.excalidraw" "${files[@]}"

# Resolve the scene id (see the SCENE_ID header above); "new" means create a scene.
scene_id=${SCENE_ID:-$(jq -r '.scene_id // empty' "$here/../assets/all.json" 2>/dev/null || true)}
[ "$scene_id" != new ] || scene_id=
[ -z "${NO_UPLOAD:-}" ] || {
  echo "scene id: ${scene_id:-new}" >&2
  echo "NO_UPLOAD set: $out_dir/all.excalidraw"; exit 0
}

rc=0
if [ -n "$scene_id" ]; then
  log=$(python3 "$here/upload_scene.py" "$out_dir/all.excalidraw" --scene-id "$scene_id") || rc=$?
else
  log=$(python3 "$here/upload_scene.py" "$out_dir/all.excalidraw" \
    --collection "${COLLECTION:-yas!}" --name "$name") || rc=$?
fi
echo "$log"
[ "$rc" -eq 0 ] || { echo "upload check failed (exit $rc)" >&2; exit "$rc"; }
id=$(printf '%s\n' "$log" | sed -n 's/^scene id: *//p' | head -1)
[ -n "$id" ] && echo "scene url: https://app.excalidraw.com/s/1BFmCncTPVB/$id"
