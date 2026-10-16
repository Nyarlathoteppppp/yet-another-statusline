"""Incremental (fold_file) results must equal a full parse of the same file."""
import json
from datetime import datetime, timezone

import pytest

from yas.info.parsecache import TranscriptCache
from yas.info.skills import LoadedSkills
from yas.info.tasks import TaskList
from yas.info.transcript import TranscriptUsage


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def _assistant(mid: str, out: int, ts: float, cache_read: int = 0, h1: int = 0) -> str:
    return json.dumps({
        'type': 'assistant', 'timestamp': _iso(ts),
        'message': {'id': mid, 'usage': {
            'input_tokens': 1, 'output_tokens': out,
            'cache_read_input_tokens': cache_read,
            'cache_creation': {'ephemeral_1h_input_tokens': h1},
        }},
    }) + '\n'


def _tool(name: str, inp: dict, ts: float) -> str:
    return json.dumps({
        'type': 'assistant', 'timestamp': _iso(ts),
        'message': {'content': [{'type': 'tool_use', 'name': name, 'input': inp}]},
    }) + '\n'


def _create(subject: str, ts: float) -> str:
    return _tool('TaskCreate', {'subject': subject, 'activeForm': subject + 'ing'}, ts)


def _update(tid: int, status: str, ts: float) -> str:
    return _tool('TaskUpdate', {'taskId': str(tid), 'status': status}, ts)


USAGE_LINES = [
    _assistant('a', 5, 1000),
    _assistant('b', 1, 1010, cache_read=10),
    _assistant('b', 9, 1020, cache_read=20, h1=3),
    'not json "usage" "assistant"\n',
    _assistant('c', 4, 1030),
    _assistant('a', 7, 1040),
]
SKILL_LINES = [
    _tool('Skill', {'skill': 'alpha'}, 1),
    _tool('Read', {'file_path': '/x/skills/beta/SKILL.md'}, 2),
    _tool('Skill', {'skill': 'alpha'}, 3),
    _tool('Skill', {'skill': 'gamma'}, 4),
]
TASK_LINES = [
    _create('one', 1000), _create('two', 1001),
    _update(1, 'in_progress', 1002), _update(1, 'completed', 1003),
    _update(2, 'completed', 1004),
    _create('fresh', 1005),
    _update(1, 'in_progress', 1006), _create('second', 1007),
]
CASES = {
    'usage':  (USAGE_LINES, lambda p, c: TranscriptUsage.from_transcript(p, cache=c)),
    'skills': (SKILL_LINES, lambda p, c: LoadedSkills.from_transcript(p, cache=c)),
    'tasks':  (TASK_LINES,  lambda p, c: TaskList.from_session(p, cache=c)),
}


def _chunks(data: bytes, cuts: list[int]) -> list[bytes]:
    bounds = [0, *cuts, len(data)]
    return [data[a:b] for a, b in zip(bounds, bounds[1:])]


@pytest.mark.parametrize('name', sorted(CASES))
@pytest.mark.parametrize('splits', [3, 7, 11])
def test_incremental_equals_full(tmp_path, tmp_home, name, splits):
    lines, read = CASES[name]
    data = ''.join(lines).encode()
    cuts = sorted({len(data) * i // splits + i for i in range(1, splits)})  # mostly mid-line
    cache = TranscriptCache('sess')
    path = tmp_path / 'm.jsonl'
    path.write_bytes(b'')

    result = None
    for part in _chunks(data, cuts):
        with path.open('ab') as fh:
            fh.write(part)
        result = read(str(path), cache)

    assert result == read(str(path), None)


@pytest.mark.parametrize('name', sorted(CASES))
def test_truncation_falls_back_to_full_parse(tmp_path, tmp_home, name):
    lines, read = CASES[name]
    path = tmp_path / 'm.jsonl'
    cache = TranscriptCache('sess')
    path.write_text(''.join(lines))
    read(str(path), cache)
    path.write_text(''.join(lines[:2]))

    result = read(str(path), cache)

    assert result == read(str(path), None)


@pytest.mark.parametrize('name', sorted(CASES))
def test_replacement_falls_back_to_full_parse(tmp_path, tmp_home, name):
    lines, read = CASES[name]
    path = tmp_path / 'm.jsonl'
    cache = TranscriptCache('sess')
    path.write_text(''.join(lines))
    read(str(path), cache)
    path.unlink()
    path.write_text(''.join(reversed(lines)) + ''.join(lines))

    result = read(str(path), cache)

    assert result == read(str(path), None)


def test_task_generation_reset_across_chunk_boundary(tmp_path, tmp_home):
    path = tmp_path / 'm.jsonl'
    cache = TranscriptCache('sess')
    path.write_text(_create('one', 1000) + _update(1, 'completed', 1001))
    assert TaskList.from_session(str(path), cache=cache).total == 1

    with path.open('a') as fh:
        fh.write(_create('fresh', 1002))
    result = TaskList.from_session(str(path), cache=cache)

    assert [(t.id, t.subject, t.status) for t in result.tasks] == [(1, 'fresh', 'pending')]


def test_usage_streamed_id_spans_chunks(tmp_path, tmp_home):
    path = tmp_path / 'm.jsonl'
    cache = TranscriptCache('sess')
    path.write_text(_assistant('m', 2, 1000))
    TranscriptUsage.from_transcript(str(path), cache=cache)
    with path.open('a') as fh:
        fh.write(_assistant('m', 8, 1001))

    result = TranscriptUsage.from_transcript(str(path), cache=cache)

    assert (result.output_tokens, result.input_tokens) == (8, 1)


def test_partial_last_line_waits(tmp_path, tmp_home):
    path = tmp_path / 'm.jsonl'
    cache = TranscriptCache('sess')
    line = _assistant('m', 3, 1000)
    path.write_text(line[:-10])

    before = TranscriptUsage.from_transcript(str(path), cache=cache)
    with path.open('a') as fh:
        fh.write(line[-10:])
    after = TranscriptUsage.from_transcript(str(path), cache=cache)

    assert (before.output_tokens, after.output_tokens) == (0, 3)


def test_state_survives_save_and_load(tmp_path, tmp_home):
    path = tmp_path / 'm.jsonl'
    path.write_text(''.join(TASK_LINES[:4]))
    cache = TranscriptCache('sess')
    TaskList.from_session(str(path), cache=cache)
    cache.save()
    with path.open('a') as fh:
        fh.write(''.join(TASK_LINES[4:]))

    result = TaskList.from_session(str(path), cache=TranscriptCache.load('sess'))

    assert result == TaskList.from_session(str(path))
