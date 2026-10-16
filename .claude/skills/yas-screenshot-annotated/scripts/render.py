'''Render an Excalidraw scene (image, text, freedraw) to a PNG with Pillow.

Stored ink is white (#ffffff). It is drawn as-is on a dark --bg (default: the scene canvas, else #121212)
and as #1e1e1e on a light --bg (for example --bg white), so both stay readable.
'''
from __future__ import annotations

import argparse
import base64
import io
import json
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

FONT_PATH    = Path(__file__).resolve().parent.parent / 'assets' / 'fonts' / 'Excalifont-Regular.ttf'
LIGHT_BG_INK = '#1e1e1e'
DEFAULT_BG    = '#121212'
STORED_WHITE = {'#ffffff', '#fff'}
SKIP_TYPES   = {'frame', 'magicframe'}


def live_elements(scene: dict) -> list[dict]:
    return [e for e in scene['elements'] if not e.get('isDeleted') and e['type'] not in SKIP_TYPES]


def element_bounds(e: dict) -> tuple[float, float, float, float]:
    'Freedraw bounds come from the points; the stored width/height can overstate them.'
    if e['type'] == 'freedraw':
        xs = [e['x'] + p[0] for p in e['points']]
        ys = [e['y'] + p[1] for p in e['points']]
        return min(xs), min(ys), max(xs), max(ys)
    return e['x'], e['y'], e['x'] + e['width'], e['y'] + e['height']


def text_bounds(e: dict, font: ImageFont.FreeTypeFont) -> tuple[float, float, float, float]:
    'Bounds from the real glyph widths, so the canvas never clips a label.'
    lines = e['text'].split('\n')
    width = max(font.getlength(line) for line in lines)
    return e['x'], e['y'], e['x'] + max(width, e['width']), e['y'] + e['height']


def stroke_colour(e: dict, bg: str) -> str:
    colour = e.get('strokeColor', '#ffffff')
    light_bg = sum(ImageColor.getrgb(bg)) >= 3 * 128
    if light_bg and colour.lower() in STORED_WHITE:
        return LIGHT_BG_INK
    return colour


def draw_text(draw: ImageDraw.ImageDraw, e: dict, ox: float, oy: float, scale: float, colour: str) -> None:
    size = e['fontSize'] * scale
    font = ImageFont.truetype(str(FONT_PATH), round(size))
    line_h = size * e.get('lineHeight', 1.25)
    ascent, descent = font.getmetrics()
    offset = (line_h - (ascent + descent)) / 2 + ascent
    for i, line in enumerate(e['text'].split('\n')):
        x = (e['x'] - ox) * scale
        y = (e['y'] - oy) * scale + i * line_h + offset
        draw.text((x, y), line, font=font, fill=colour, anchor='ls')


def draw_freedraw(draw: ImageDraw.ImageDraw, e: dict, ox: float, oy: float, scale: float, colour: str) -> None:
    pts   = [((e['x'] + px - ox) * scale, (e['y'] + py - oy) * scale) for px, py in e['points']]
    width = max(1, round(e.get('strokeWidth', 1) * scale))
    draw.line(pts, fill=colour, width=width, joint='curve')
    r = width / 2
    for x, y in (pts[0], pts[-1]):
        draw.ellipse((x - r, y - r, x + r, y + r), fill=colour)


def paste_image(canvas: Image.Image, scene: dict, e: dict, ox: float, oy: float, scale: float) -> None:
    data = scene['files'][e['fileId']]['dataURL'].split(',', 1)[1]
    img  = Image.open(io.BytesIO(base64.b64decode(data))).convert('RGBA')
    size = (round(e['width'] * scale), round(e['height'] * scale))
    img  = img.resize(size, Image.LANCZOS)
    canvas.alpha_composite(img, (round((e['x'] - ox) * scale), round((e['y'] - oy) * scale)))


def render(scene: dict, bg: str, pad: float, scale: float) -> Image.Image:
    els  = live_elements(scene)
    texts = {id(e): ImageFont.truetype(str(FONT_PATH), round(e['fontSize'])) for e in els if e['type'] == 'text'}
    boxes = [text_bounds(e, texts[id(e)]) if e['type'] == 'text' else element_bounds(e) for e in els]
    ox = min(b[0] for b in boxes) - pad
    oy = min(b[1] for b in boxes) - pad
    w  = max(b[2] for b in boxes) + pad - ox
    h  = max(b[3] for b in boxes) + pad - oy
    canvas = Image.new('RGBA', (round(w * scale), round(h * scale)), ImageColor.getrgb(bg) + (255,))
    for e in (e for e in els if e['type'] == 'image'):
        paste_image(canvas, scene, e, ox, oy, scale)
    draw = ImageDraw.Draw(canvas)
    for e in els:
        colour = stroke_colour(e, bg)
        if e['type'] == 'text':
            draw_text(draw, e, ox, oy, scale, colour)
        elif e['type'] == 'freedraw':
            draw_freedraw(draw, e, ox, oy, scale, colour)
    return canvas.convert('RGB')


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('scene')
    ap.add_argument('out')
    ap.add_argument('--bg', default=None, help='canvas colour (default: scene appState.viewBackgroundColor, else #121212)')
    ap.add_argument('--pad', type=float, default=40)
    ap.add_argument('--scale', type=float, default=2.0)
    args = ap.parse_args()
    scene = json.loads(Path(args.scene).read_text())
    bg    = args.bg or scene.get('appState', {}).get('viewBackgroundColor') or DEFAULT_BG
    img   = render(scene, bg, args.pad, args.scale)
    img.save(args.out)
    print(f'{args.out} {img.width}x{img.height}')


if __name__ == '__main__':
    main()
