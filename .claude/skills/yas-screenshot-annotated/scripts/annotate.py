#!/usr/bin/env python3
'''Build an Excalidraw scene: a YAS statusline PNG in a frame, with hand-style labels and pointers.

Stdlib only. The scene follows the style of the user's own annotated screenshots:
Excalifont text (fontFamily 5, size 16), thin white (#ffffff) freedraw pointer lines, one #bbb frame.

  annotate.py --txt demo/openspec.txt --dump-grid
  annotate.py --png demo/openspec.png --txt demo/openspec.txt \
      --spec assets/views/openspec.json --out openspec.excalidraw

Cell geometry (an estimate, the PNG pipeline reports none): the PNG is trimmed to its ink and
padded by `--pad` px. The first and last text rows are box-drawing rows whose ink is a centred
line, so column c is centred at x = pad + c * (W - 2 pad) / (cols - 1), and row r is centred at
y = pad + r * (H - 2 pad) / (rows - 1). cols is the longest stripped line, rows the non-blank
line count. All of this is in native PNG pixels and is scaled to the displayed image size.
'''
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import random
import re
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

RED    = '\033[31m'
YELLOW = '\033[33m'
RESET  = '\033[0m'

ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')

INK           = '#ffffff'
CANVAS_BG     = '#121212'
FRAME_STROKE  = '#bbb'
FONT_FAMILY   = 5
BASE_FONT     = 16
LINE_HEIGHT   = 1.25
CHAR_W        = 0.62     # width estimate per character, in font sizes (Excalifont renders wide; over-estimating is harmless)
DISPLAY_WIDTH = 1440     # image width in the user's own scenes
FRAME_PAD     = 70
GAP_ABOVE     = 40
GAP_BELOW     = 40
GAP_LEFT      = 65
GAP_RIGHT     = 60
STAGGER       = 20
MIN_TOP_STEP  = 70
POINTER_START = 10
POINTER_DEPTH = 15       # minimum depth inside the image edge (row 0 / border targets, 'anchor' mode)
EDGE_INSET    = 28       # 'edge' mode: depth of the pointer end inside the image edge (measured from the user's edit)
EDGE_ROW      = 0.45     # fraction of a row height from a row's centre to the edge where a pointer ends
B62           = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
SIDES         = ('above', 'below', 'left', 'right')
END_MODES     = ('edge', 'anchor')

Point = tuple[float, float]


@dataclass
class Grid:
    rows:   list[str]
    cols:   int
    width:  int
    height: int
    pad:    int

    @property
    def cell_w(self) -> float:
        return (self.width - 2 * self.pad) / max(self.cols - 1, 1)

    @property
    def cell_h(self) -> float:
        return (self.height - 2 * self.pad) / max(len(self.rows) - 1, 1)

    def centre(self, col: float, row: float) -> Point:
        'Native pixel centre of a (col, row) cell'
        return self.pad + col * self.cell_w, self.pad + row * self.cell_h


@dataclass
class Label:
    text:   str
    side:   str
    target: Point                 # display px, centre of the targeted cell
    fs:     float
    x:      float = 0.0
    y:      float = 0.0
    start:  Point = (0.0, 0.0)
    end:    Point = (0.0, 0.0)
    ranges: list[float] = field(default_factory=list)
    end_at: str = 'edge'          # 'edge': pointer stops at the block edge; 'anchor': at the targeted glyph

    @property
    def lines(self) -> list[str]:
        return self.text.split('\n')

    @property
    def w(self) -> float:
        return CHAR_W * self.fs * max(len(s) for s in self.lines)

    @property
    def h(self) -> float:
        return len(self.lines) * self.fs * LINE_HEIGHT

    def bbox(self) -> tuple[float, float, float, float]:
        return self.x, self.y, self.x + self.w, self.y + self.h


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub('', text)


def load_rows(txt: Path) -> list[str]:
    'Stripped lines without the blank lines before and after the box (the PNG trims them)'
    rows = [s.rstrip() for s in strip_ansi(txt.read_text()).split('\n')]
    while rows and not rows[0]:
        rows.pop(0)
    while rows and not rows[-1]:
        rows.pop()
    return rows


def dump_grid(rows: list[str]) -> None:
    cols = max(len(r) for r in rows)
    print(f'rows={len(rows)} cols={cols}')
    print('     ' + ''.join(f'{c // 10 % 10 if c % 10 == 0 else " "}' for c in range(cols)))
    print('     ' + ''.join(str(c % 10) for c in range(cols)))
    for i, row in enumerate(rows):
        print(f'{i:>3}  {row}')


def png_size(png: Path) -> tuple[int, int]:
    head = png.read_bytes()[:24]
    if head[:8] != b'\x89PNG\r\n\x1a\n':
        raise SystemExit(f'{png}: not a PNG')
    return struct.unpack('>II', head[16:24])


def warn(msg: str) -> None:
    print(f'{YELLOW}warning: {msg}{RESET}', file=sys.stderr)


def resolve(label: dict, grid: Grid) -> tuple[float, float] | None:
    'Return the (col, row) targeted by a label spec, or None when the anchor does not match'
    if 'at' in label:
        col, row = label['at']
        return float(col), float(row)
    anchor = label.get('anchor')
    if not anchor:
        return None
    side = label.get('side', 'above')
    wanted_row = anchor.get('row')
    hits = [
        (m, i)
        for i, line in enumerate(grid.rows)
        if wanted_row is None or wanted_row == i
        for m in re.finditer(anchor['regex'], line)
    ]
    n = anchor.get('occurrence', 1)
    if not 1 <= n <= len(hits):
        return None
    m, row = hits[n - 1]
    if side == 'left':
        col = float(m.start())
    elif side == 'right':
        col = float(m.end() - 1)
    else:
        col = (m.start() + m.end() - 1) / 2
    dcol, drow = anchor.get('offset', [0, 0])
    return col + dcol, row + drow


def overlaps_x(a: Label, b: Label, margin: float = 14.0) -> bool:
    return a.x < b.x + b.w + margin and b.x < a.x + a.w + margin


def place_row(
    labels: list[Label], img: tuple[float, float, float, float], cell_h: float, above: bool, text_edge: float,
) -> None:
    '''Place above/below labels centred on their target x, staggered on as many baselines as needed.

    text_edge is the y of the block's first text row top (above) or last text row bottom (below);
    pointers in 'edge' mode end there instead of at the targeted row.
    '''
    img_x, img_y, img_r, img_b = img
    labels.sort(key=lambda lb: lb.target[0])
    step = max((lb.h for lb in labels), default=0) + STAGGER
    placed: list[tuple[int, Label]] = []
    for lb in labels:
        lb.x = lb.target[0] - lb.w / 2
        tx = lb.target[0]
        k = 0
        while True:
            clash = any(
                (j == k and overlaps_x(lb, o))
                or (j < k and o.x - 6 < tx < o.x + o.w + 6)
                or (j > k and lb.x - 6 < o.target[0] < lb.x + lb.w + 6)
                for j, o in placed
            )
            if not clash:
                break
            k += 1
        offset = (GAP_ABOVE if above else GAP_BELOW) + k * step
        lb.y = img_y - offset - lb.h if above else img_b + offset
        placed.append((k, lb))
        sign = 1 if above else -1
        start_y = lb.y + lb.h + POINTER_START if above else lb.y - POINTER_START
        lb.start = (tx, start_y)
        ty = lb.target[1]
        if lb.end_at == 'anchor':
            end_y = max(img_y + POINTER_DEPTH, ty - EDGE_ROW * cell_h) if above else min(img_b - POINTER_DEPTH, ty + EDGE_ROW * cell_h)
        else:
            end_y = max(img_y + POINTER_DEPTH, min(text_edge, ty - EDGE_ROW * cell_h)) if above else min(img_b - POINTER_DEPTH, max(text_edge, ty + EDGE_ROW * cell_h))
        lb.end = (tx, end_y)
        lb.ranges = [sign]


def place_column(labels: list[Label], img: tuple[float, float, float, float], left: bool) -> None:
    'Place left/right labels in a column beside the image, tops at least MIN_TOP_STEP apart'
    img_x, img_y, img_r, img_b = img
    labels.sort(key=lambda lb: lb.target[1])
    prev_top = -math.inf
    prev_h = 0.0
    for lb in labels:
        top = max(lb.target[1] - lb.h / 2, prev_top + max(MIN_TOP_STEP, prev_h + 12))
        lb.y = top
        lb.x = img_x - GAP_LEFT - lb.w if left else img_r + GAP_RIGHT
        prev_top, prev_h = top, lb.h
        cy = lb.y + lb.h / 2
        tx, ty = lb.target
        edge = lb.end_at == 'edge'
        if left:
            lb.start = (lb.x + lb.w + POINTER_START, cy)
            lb.end = (max(img_x + POINTER_DEPTH, min(tx, img_x + (EDGE_INSET if edge else 60))), ty)
        else:
            lb.start = (lb.x - POINTER_START, cy)
            lb.end = (min(img_r - POINTER_DEPTH, max(tx, img_r - (EDGE_INSET if edge else 60))), ty)


def pointer_points(rng: random.Random, start: Point, end: Point) -> list[Point]:
    'Hand-drawn style polyline relative to start: ease-in spacing, slight bow, tiny wobble'
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length, dx / length
    n = max(20, min(40, int(length / 3)))
    bow = rng.choice((-1, 1)) * 0.05 * length
    pts: list[Point] = [(0.0, 0.0)]
    for i in range(1, n):
        u = i / (n - 1)
        t = u ** 1.4
        wobble = rng.uniform(-0.8, 0.8) * math.sin(math.pi * u)
        off = bow * math.sin(math.pi * t) + wobble
        pts.append((dx * t + nx * off, dy * t + ny * off))
    pts[-1] = (dx, dy)
    return pts


def index_keys(n: int) -> list[str]:
    'Fractional index strings in increasing order: a0..az, then b00..bzz'
    keys = [f'a{B62[i]}' for i in range(min(n, 62))]
    keys += [f'b{B62[(i - 62) // 62]}{B62[(i - 62) % 62]}' for i in range(62, n)]
    return keys


class Scene:
    'Collects elements with the fields the Excalidraw+ endpoint requires'

    def __init__(self, name: str, seed: int = 1) -> None:
        self.name = name
        self.rng = random.Random(seed)
        self.now = int(time.time() * 1000)
        self.elements: list[dict] = []
        self.used_ids: set[str] = set()
        self.frame_id = self.new_id('frame', 'frame')

    def new_id(self, role: str, key: str) -> str:
        'Deterministic id from the scene name, element role and label text, so a re-run overwrites'
        base = f'{self.name}|{role}|{key}'
        n = 0
        while True:
            eid = hashlib.sha1(f'{base}|{n}'.encode()).hexdigest()[:21]
            if eid not in self.used_ids:
                self.used_ids.add(eid)
                return eid
            n += 1

    def add(self, kind: str, x: float, y: float, w: float, h: float, key: str = '', **extra: object) -> dict:
        el = {
            'id':              self.frame_id if kind == 'frame' else self.new_id(kind, key),
            'type':            kind,
            'x':               x,
            'y':               y,
            'width':           w,
            'height':          h,
            'angle':           0,
            'strokeColor':     INK,
            'backgroundColor': '#ffffff',
            'fillStyle':       'solid',
            'strokeWidth':     1,
            'strokeStyle':     'solid',
            'roughness':       0,
            'opacity':         100,
            'groupIds':        [],
            'frameId':         self.frame_id,
            'index':           '',
            'roundness':       None,
            'seed':            self.rng.randrange(1, 2**31),
            'version':         self.now // 1000,  # above any earlier upload, so the merge keeps this element
            'versionNonce':    self.rng.randrange(1, 2**31),
            'isDeleted':       False,
            'boundElements':   [],
            'updated':         self.now,
            'link':            None,
            'locked':          False,
        }
        el.update(extra)
        self.elements.append(el)
        return el

    def finish(self) -> list[dict]:
        for el, key in zip(self.elements, index_keys(len(self.elements))):
            el['index'] = key
        return self.elements


def build(args: argparse.Namespace) -> int:
    spec = json.loads(Path(args.spec).read_text())
    rows = load_rows(Path(args.txt))
    width, height = png_size(Path(args.png))
    grid = Grid(rows=rows, cols=max(len(r) for r in rows), width=width, height=height, pad=args.pad)
    fs = BASE_FONT * args.scale
    disp_w = DISPLAY_WIDTH * args.scale
    k = disp_w / width
    disp_h = height * k
    img = (0.0, 0.0, disp_w, disp_h)

    by_side: dict[str, list[Label]] = {s: [] for s in SIDES}
    for item in spec['labels']:
        side = item.get('side', 'above')
        text = item['text']
        pos = resolve(item, grid)
        if pos is None or side not in SIDES:
            warn(f'skipped label {text!r}: anchor {item.get("anchor") or item.get("at")} did not match (side={side})')
            continue
        px, py = grid.centre(*pos)
        end_at = item.get('end', 'edge')
        if end_at not in END_MODES:
            warn(f'label {text!r}: unknown end {end_at!r}, using edge')
            end_at = 'edge'
        by_side[side].append(Label(text=text, side=side, target=(px * k, py * k), fs=fs, end_at=end_at))

    cell_h = grid.cell_h * k
    top_edge = grid.centre(0, 1)[1] * k - EDGE_ROW * cell_h                   # top of the first text row
    bottom_edge = grid.centre(0, len(rows) - 2)[1] * k + EDGE_ROW * cell_h    # bottom of the last text row
    place_row(by_side['above'], img, cell_h, above=True, text_edge=top_edge)
    place_row(by_side['below'], img, cell_h, above=False, text_edge=bottom_edge)
    place_column(by_side['left'], img, left=True)
    place_column(by_side['right'], img, left=False)
    labels = [lb for s in SIDES for lb in by_side[s]]
    check_overlaps(labels)

    xs = [0.0, disp_w]
    ys = [0.0, disp_h]
    for lb in labels:
        x0, y0, x1, y1 = lb.bbox()
        xs += [x0, x1, lb.start[0], lb.end[0]]
        ys += [y0, y1, lb.start[1], lb.end[1]]
    ox = FRAME_PAD - min(xs)
    oy = FRAME_PAD - min(ys)
    frame_w = max(xs) - min(xs) + 2 * FRAME_PAD
    frame_h = max(ys) - min(ys) + 2 * FRAME_PAD

    png_bytes = Path(args.png).read_bytes()
    file_id = hashlib.sha1(png_bytes).hexdigest()
    scene = Scene(args.name, seed=int(hashlib.sha1(args.name.encode()).hexdigest()[:8], 16))
    frame = scene.add(
        'frame', 0, 0, frame_w, frame_h,
        strokeColor=FRAME_STROKE, backgroundColor='transparent', strokeWidth=2,
        frameId=None, name=args.name,
    )
    scene.add(
        'image', ox, oy, disp_w, disp_h,
        strokeColor='transparent', status='saved', fileId=file_id, scale=[1, 1], crop=None,
    )
    for lb in labels:
        sx, sy = lb.start[0] + ox, lb.start[1] + oy
        pts = pointer_points(scene.rng, lb.start, lb.end)
        pw = max(p[0] for p in pts) - min(p[0] for p in pts)
        ph = max(p[1] for p in pts) - min(p[1] for p in pts)
        scene.add(
            'freedraw', sx, sy, pw, ph,
            key=lb.text,
            points=[[round(p[0], 2), round(p[1], 2)] for p in pts],
            pressures=[], simulatePressure=True, lastCommittedPoint=None,
        )
        scene.add(
            'text', lb.x + ox, lb.y + oy, lb.w, lb.h,
            key=lb.text,
            text=lb.text, originalText=lb.text, fontSize=lb.fs, fontFamily=FONT_FAMILY,
            textAlign='left', verticalAlign='top', containerId=None,
            autoResize=True, lineHeight=LINE_HEIGHT,
        )
    elements = scene.finish()
    # The frame is first in the array (as in Excalidraw exports); children follow it.
    elements.insert(0, elements.pop(0))
    data_url = 'data:image/png;base64,' + base64.b64encode(png_bytes).decode()
    out = {
        'type':     'excalidraw',
        'version':  2,
        'source':   'https://app.excalidraw.com',
        'elements': elements,
        'appState': {'viewBackgroundColor': CANVAS_BG},
        'files':    {file_id: {'mimeType': 'image/png', 'id': file_id, 'dataURL': data_url, 'created': scene.now}},
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out))
    print(
        f'{args.out}: {len(labels)} labels, image {width}x{height}px native, shown {disp_w:.0f}x{disp_h:.0f}, '
        f'cols={grid.cols} rows={len(rows)} cell_w={grid.cell_w:.2f} cell_h={grid.cell_h:.2f} (native px)'
    )
    return 0


def check_overlaps(labels: list[Label]) -> None:
    for i, a in enumerate(labels):
        ax0, ay0, ax1, ay1 = a.bbox()
        for b in labels[i + 1:]:
            bx0, by0, bx1, by1 = b.bbox()
            if ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1:
                warn(f'labels overlap: {a.text!r} and {b.text!r}')


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--png')
    p.add_argument('--txt', required=True)
    p.add_argument('--spec')
    p.add_argument('--out')
    p.add_argument('--name')
    p.add_argument('--scale', type=float, default=1.0)
    p.add_argument('--pad',   type=int,   default=24, help='YAS_DEMO_PAD used for the PNG')
    p.add_argument('--dump-grid', action='store_true', help='print stripped text with a column ruler')
    args = p.parse_args(argv)
    if not args.dump_grid and not (args.png and args.spec and args.out):
        p.error('--png, --spec and --out are required unless --dump-grid')
    if not args.dump_grid:
        args.name = args.name or json.loads(Path(args.spec).read_text()).get('frame', 'yas')
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dump_grid:
        dump_grid(load_rows(Path(args.txt)))
        return 0
    return build(args)


if __name__ == '__main__':
    sys.exit(main())
