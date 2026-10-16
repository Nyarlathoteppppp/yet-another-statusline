"""Incremental (fold) count_transcript must equal a full parse of the same file."""

import json
from pathlib import Path

import pytest

from yas.info.parsecache import TranscriptCache
from yas.info.subagents import _parse_iso_to_epoch
from yas.info.toolcounts import count_transcript

TS_EARLY = '2026-01-01T00:00:00Z'
TS_LATE  = '2026-01-01T12:00:00Z'
CATN     = '1\tline one\n2\tline two\n3\tline three'


def _assistant(mid: str, blocks: list[dict], ts: str = TS_LATE, sidechain: bool = False) -> str:
    return json.dumps({
        'timestamp':   ts,
        'isSidechain': sidechain,
        'message':     {'id': mid, 'content': blocks},
    })


def _use(name: str, tid: str, **inp: object) -> dict:
    return {'type': 'tool_use', 'name': name, 'id': tid, 'input': inp}


def _result(tid: str, content: str, ts: str = TS_LATE) -> str:
    return json.dumps({
        'timestamp': ts,
        'message':   {'content': [{'type': 'tool_result', 'tool_use_id': tid, 'content': content}]},
    })


def _transcript() -> bytes:
    """Lines exercising cross-line pairing, repeated ids and every filter."""
    lines = [
        _assistant('m1', [_use('Read', 'r1')]),
        _result('r1', CATN),
        _assistant('m2', [_use('Bash', 'b1', command='cat foo.txt')]),
        _assistant('m3', [_use('Edit', 'e1', old_string='a\nb\n', new_string='a\n')]),
        _assistant('m4', [_use('Read', 'r2')], sidechain=True),
        _result('b1', 'x\ny\nz\n'),
        _result('r2', CATN),
        # Streamed message: same id, growing content, spans later chunks.
        _assistant('m5', [_use('Write', 'w1', content='a\nb\n')]),
        _assistant('m5', [_use('Write', 'w1', content='a\nb\nc\nd\n'), _use('Grep', 'g1')]),
        _assistant('m6', [_use('Read', 'r3')], ts=TS_EARLY),
        _result('r3', CATN, ts=TS_EARLY),
        _assistant('m7', [_use('DesignSync', 'd1', method='get_file')]),
        _result('d1', json.dumps({'method': 'get_file', 'content': 'p\nq\nr'})),
        _assistant('m5', [_use('Write', 'w1', content='a\n'), _use('Bash', 'b2', command='sed -n 1,5p f')]),
        _result('b2', 'one\ntwo\n'),
        _result('b2', 'dup\ndup\ndup\n'),
        _assistant('m8', [_use('mcp__srv__thing', 't1')]),
    ]
    return ('\n'.join(lines) + '\n').encode()


def _cuts(data: bytes) -> list[int]:
    """Cut points: mid-line, on a boundary, and in a later mid-line."""
    first = data.index(b'\n') + 1
    return [first // 2, first, len(data) // 3, len(data) // 2 + 7, len(data) - 5, len(data)]


@pytest.mark.parametrize('epoch_ts', [None, TS_LATE])
@pytest.mark.parametrize('skip_sidechain', [True, False])
def test_incremental_equals_full_parse(tmp_path: Path, epoch_ts: str | None, skip_sidechain: bool) -> None:
    import os

    clear_epoch = _parse_iso_to_epoch(epoch_ts) if epoch_ts else None
    data = _transcript()
    path = tmp_path / 't.jsonl'
    cache = TranscriptCache('s')

    path.write_bytes(b'')
    for cut in _cuts(data):
        path.write_bytes(data[:cut])
        st = os.stat(path)
        result = count_transcript(str(path), clear_epoch, skip_sidechain=skip_sidechain, cache=cache, st=st)
        expected = count_transcript(str(path), clear_epoch, skip_sidechain=skip_sidechain)
        assert result == expected, cut

    assert result.counts  # the fixture is not vacuous


def test_incremental_values(tmp_path: Path) -> None:
    import os

    data = _transcript()
    path = tmp_path / 't.jsonl'
    cache = TranscriptCache('s')
    # Stop after the m5 first line so tool_results and re-streamed m5 arrive later.
    cut = data.index(b'"id": "m6"')
    path.write_bytes(data[:cut])
    count_transcript(str(path), None, skip_sidechain=True, cache=cache, st=os.stat(path))
    path.write_bytes(data)
    result = count_transcript(str(path), None, skip_sidechain=True, cache=cache, st=os.stat(path))

    assert result.counts == {
        'Read': 2, 'Bash': 2, 'Edit': 1, 'Write': 1, 'DesignSync': 1, 'thing': 1,
    }
    # r1: 2 newlines, b1: 3, r3: 2, d1: 3 (json get_file), b2: 2
    assert result.lines_read == 2 + 3 + 2 + 3 + 2
    assert result.lines_changed == 2 + 1 + 0


def test_result_in_later_chunk_than_tool_use(tmp_path: Path) -> None:
    import os

    path = tmp_path / 't.jsonl'
    cache = TranscriptCache('s')
    path.write_bytes((_assistant('m1', [_use('Read', 'r1')]) + '\n').encode())
    first = count_transcript(str(path), None, skip_sidechain=True, cache=cache, st=os.stat(path))
    assert first.lines_read == 0
    with path.open('ab') as fh:
        fh.write((_result('r1', CATN) + '\n').encode())
    second = count_transcript(str(path), None, skip_sidechain=True, cache=cache, st=os.stat(path))
    assert second.lines_read == 2


def test_truncation_falls_back_to_full(tmp_path: Path) -> None:
    import os

    data = _transcript()
    path = tmp_path / 't.jsonl'
    cache = TranscriptCache('s')
    path.write_bytes(data)
    count_transcript(str(path), None, skip_sidechain=False, cache=cache, st=os.stat(path))

    short = data[: data.index(b'"id": "m4"')]
    short = short[: short.rindex(b'\n') + 1]
    path.write_bytes(short)
    result = count_transcript(str(path), None, skip_sidechain=False, cache=cache, st=os.stat(path))
    assert result == count_transcript(str(path), None, skip_sidechain=False)


def test_replacement_falls_back_to_full(tmp_path: Path) -> None:
    import os

    path = tmp_path / 't.jsonl'
    cache = TranscriptCache('s')
    old = '\n'.join(_assistant(f'a{i}', [_use('Bash', f'x{i}')]) for i in range(30)) + '\n'
    path.write_text(old)
    count_transcript(str(path), None, skip_sidechain=False, cache=cache, st=os.stat(path))

    # Same-or-larger file with different content before the old offset.
    new = '\n'.join(_assistant(f'b{i}', [_use('Grep', f'y{i}')]) for i in range(40)) + '\n'
    path.write_text(new)
    result = count_transcript(str(path), None, skip_sidechain=False, cache=cache, st=os.stat(path))
    assert result.counts == {'Grep': 40}
    assert result == count_transcript(str(path), None, skip_sidechain=False)


def test_state_survives_cache_save_and_load(tmp_path: Path, tmp_home: Path) -> None:
    import os

    data = _transcript()
    path = tmp_path / 't.jsonl'
    cut = data.index(b'"id": "m7"')
    cut = data.rindex(b'\n', 0, cut) + 1
    path.write_bytes(data[:cut])
    cache = TranscriptCache('persist')
    count_transcript(str(path), None, skip_sidechain=False, cache=cache, st=os.stat(path))
    cache.save()

    loaded = TranscriptCache.load('persist')
    path.write_bytes(data)
    result = count_transcript(str(path), None, skip_sidechain=False, cache=loaded, st=os.stat(path))
    assert result == count_transcript(str(path), None, skip_sidechain=False)
