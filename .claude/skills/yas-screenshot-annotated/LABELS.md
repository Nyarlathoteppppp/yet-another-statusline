# Labels and view presets

Read this to add or reword a label, or to add a view. A view preset is the file
`assets/views/<view>.json`.

## How positions are found

`annotate.py --dump-grid --txt demo/<scenario>.txt` prints the ANSI-stripped text
with a column ruler and 0-based row numbers. Pick anchors from it. The cell-geometry
formula is in the `annotate.py` docstring.

- An anchor is a **regex on the stripped text**, not a fixed (col,row). View presets
  then survive layout changes between releases: a moved pill still matches.
  `occurrence` (1-based) selects among all matches in reading order; `row` restricts
  to one row; `offset: [dcol, drow]` nudges the target.
- `"at": [col, row]` replaces `anchor` when no stable text exists.
- An anchor that does not match is a warning and the label is skipped, never a crash.
- Targeted cell: centre of the match for above/below labels, first character for
  left, last character for right.

## View preset format

```json
{"scenario": "openspec", "frame": "openspec view",
 "labels": [{"text": "session ID", "side": "above",
             "anchor": {"regex": "[0-9a-f]{8}-[0-9a-f]{4}", "occurrence": 1, "offset": [10, 0]}}]}
```

`side` is `above`, `below`, `left` or `right`. Use `\n` for multi-line labels.
Optional `"end": "edge"` (default) or `"anchor"`, see the pointer end rule below.
Keep label texts unique per view, because element ids are derived from them.
The preset also holds a `scene_id` (see the `shoot-annotated.sh` header).

## Adding a label

1. Dump the grid, choose a regex that is unique or add `occurrence`/`row`.
2. Append an object to the view preset. Use the wording style of the existing view
   presets in `assets/views/`.
3. Re-run with `NO_UPLOAD=1` and read the warnings.

For a new view, copy a view preset, set `scenario` (any name in `ops/demo.py` `SCENARIOS`;
`subagent-tree-*` render to `demo/subagents/`) and rewrite the labels.

## Placement rules

- Above/below: centred on the target x, a fixed gap from the image, staggered onto
  further baselines when x-ranges clash, so no two labels overlap and pointers do
  not cross a neighbouring label. A crowded row uses as many baselines as needed.
- Left/right: one column beside the image, tops spaced apart, sorted by target y.
  This matches the hand-made scenes.
- Use above/below only when nothing sits between the edge and the target; otherwise use left/right.
- In views where the tokens row is inside the image (subagents, openspec), use one combined
  left label instead of three below-labels, because left pointers can only reach the image edge.
- Pointers go label to image, with ease-in spacing and a slight bow so they look hand-drawn.
- No arrowheads, no rectangles, roughness 0, stroke `#ffffff` (white) width 1.
- Constant values (`EDGE_INSET`, `POINTER_DEPTH`, `CHAR_W`, gaps) are in `annotate.py`.

**Pointer end rule (default `"end": "edge"`).** A pointer stops at the edge of the block it
describes, never inside the contents. It does not run to the anchor's row.

- above: ends at the top edge of the first text row, slightly inside the image for row-0 targets such as the session ID.
- below: ends at the bottom edge of the last text row, slightly inside the image.
- left/right: ends a fixed inset inside the image edge (never past the anchor's x). The inset was measured from the user's hand edit.
- Interior targets (table columns) therefore point at the top or bottom of the table block.
- Per-label override `"end": "anchor"` ends at the targeted glyph instead.
  Use it only when a label must touch one specific glyph.

## What each label means (from `claude/yas/`)

### What the top-row and tokens-row numbers are

- `+13:27`: session elapsed time (`SessionView.elapsed`, from `cost.total_duration_ms`).
- 5h pill `(-2:00) 30.0% -30.0%`: time until the 5h window resets (`resets_at - now`),
  percent of the 5h limit used (`used_percentage`), burn rate.
- 7d pill `20.0% -78.8%`: percent of the 7d limit used, burn rate.
- Burn rate (`burndown_delta`): `used% - ideal%`, where ideal is the share of the window
  elapsed. Negative = under the even-use pace; positive = over it. Shown as a signed percent.
- Tokens row `↓ 41.5K/8.2M (422.4K/215.4M) ⬆ 4.8K/1.5M`: each pair is `this session / today`
  (today = all sessions, from the token log). First pair: input tokens, which is
  `input_tokens + cache_creation_input_tokens` (`billed_in`). Parenthesised pair: cache read
  tokens. Last pair: output tokens. The cell after it (`0 0`) is lines of code read / changed.

### Subagent row columns

- subagent name: `subagent_type_label(sub)` (renderer.py, `type_text = subagent_type_label(sub)`).
- task description: `sub.description` (renderer.py `desc_text = sub.description or ''`, italic).
- current activity: `subagent_activity(sub.last_activity)` (renderer.py `def subagent_activity`):
  `Tool[arg]`, `(thinking)`, or the latest reply text.

Label meaning that is still a guess: "token limit" (the end of the context bar).
