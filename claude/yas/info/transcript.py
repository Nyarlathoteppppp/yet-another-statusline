"""Transcript usage reader — parses token usage from a JSONL conversation file."""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from yas.constants import CACHE_TTL_1H_SECONDS, CACHE_TTL_SECONDS
from yas.info.parsecache import fold_file

if TYPE_CHECKING:
    from yas.info.parsecache import TranscriptCache


def _usage_init() -> dict[str, Any]:
    return {'ids': {}, 'ts': '', 'h1': False}


def _usage_fold(state: dict[str, Any], lines: list[bytes]) -> dict[str, Any]:
    """Fold assistant usage lines in; the last record per message id wins.

    Streaming re-writes the same id as it appends content blocks and the
    counters GROW across those writes, so the final one carries the real
    totals. The cache anchor is the last line (any id) that touched the cache.
    """
    ids = state['ids']
    for raw in lines:
        if b'"usage"' not in raw or b'"assistant"' not in raw:
            continue
        try:
            d = json.loads(raw.decode('utf-8', 'ignore'))
        except (ValueError, TypeError):
            continue
        if not isinstance(d, dict):
            continue
        msg = d.get('message') or {}
        mid = msg.get('id')
        if not mid:
            continue
        u = msg.get('usage') or {}
        ids[mid] = [
            u.get('input_tokens', 0) or 0,
            u.get('cache_creation_input_tokens', 0) or 0,
            u.get('cache_read_input_tokens', 0) or 0,
            u.get('output_tokens', 0) or 0,
        ]
        if ids[mid][2] > 0 or ids[mid][1] > 0:
            state['ts'] = d.get('timestamp', '') or ''
            state['h1'] = bool((u.get('cache_creation') or {}).get('ephemeral_1h_input_tokens', 0))
    return state


class TranscriptUsage:
    __slots__ = (
        'input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens',
        'output_tokens', 'cache_anchor_epoch', 'cache_ttl',
    )

    def __init__(
        self,
        input_tokens:                int   = 0,
        cache_creation_input_tokens: int   = 0,
        cache_read_input_tokens:     int   = 0,
        output_tokens:               int   = 0,
        cache_anchor_epoch:          float = 0.0,
        cache_ttl:                   int   = 0,
    ) -> None:
        self.input_tokens                = input_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens
        self.cache_read_input_tokens     = cache_read_input_tokens
        self.output_tokens               = output_tokens
        self.cache_anchor_epoch          = cache_anchor_epoch
        self.cache_ttl                   = cache_ttl

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TranscriptUsage):
            return NotImplemented
        return (self.input_tokens, self.cache_creation_input_tokens, self.cache_read_input_tokens,
                self.output_tokens, self.cache_anchor_epoch, self.cache_ttl) == \
               (other.input_tokens, other.cache_creation_input_tokens, other.cache_read_input_tokens,
                other.output_tokens, other.cache_anchor_epoch, other.cache_ttl)

    __hash__ = None  # type: ignore[assignment]

    def __add__(self, other: 'TranscriptUsage') -> TranscriptUsage:
        """Sum the four token counters; keep the LATEST cache anchor.

        `cache_anchor_epoch` is a "when was the prompt cache last touched"
        signal, not an accumulator -- the most recent write across main +
        subagent transcripts is the one that determines the live TTL
        countdown, so we take whichever side has the larger epoch (its
        paired `cache_ttl` travels with it), not a sum.
        """
        if not isinstance(other, TranscriptUsage):
            return NotImplemented
        newer = self if self.cache_anchor_epoch >= other.cache_anchor_epoch else other
        return TranscriptUsage(
            input_tokens                = self.input_tokens + other.input_tokens,
            cache_creation_input_tokens = self.cache_creation_input_tokens + other.cache_creation_input_tokens,
            cache_read_input_tokens     = self.cache_read_input_tokens + other.cache_read_input_tokens,
            output_tokens               = self.output_tokens + other.output_tokens,
            cache_anchor_epoch          = newer.cache_anchor_epoch,
            cache_ttl                   = newer.cache_ttl,
        )

    def __repr__(self) -> str:
        return (f'TranscriptUsage(input_tokens={self.input_tokens}, '
                f'cache_creation_input_tokens={self.cache_creation_input_tokens}, '
                f'cache_read_input_tokens={self.cache_read_input_tokens}, '
                f'output_tokens={self.output_tokens}, '
                f'cache_anchor_epoch={self.cache_anchor_epoch}, cache_ttl={self.cache_ttl})')

    @classmethod
    def from_transcript(
        cls,
        transcript_path: str,
        *,
        cache: 'TranscriptCache | None' = None,
    ) -> TranscriptUsage:
        if not transcript_path:
            return cls()
        try:
            st = os.stat(transcript_path)
        except OSError:
            return cls()
        if not stat.S_ISREG(st.st_mode):
            return cls()
        state = fold_file(cache, transcript_path, st, 'usage', '', _usage_init, _usage_fold)
        return cls._from_state(state)

    @classmethod
    def _from_state(cls, state: dict[str, Any]) -> TranscriptUsage:
        vals = state['ids'].values()
        anchor_ts = state['ts']
        cache_anchor_epoch = 0.0
        if anchor_ts:
            try:
                ts = anchor_ts
                if ts.endswith('Z'):
                    ts = ts[:-1] + '+00:00'
                cache_anchor_epoch = datetime.fromisoformat(ts).timestamp()
            except (ValueError, TypeError):
                cache_anchor_epoch = 0.0
        cache_ttl = (
            CACHE_TTL_1H_SECONDS if state['h1']
            else (CACHE_TTL_SECONDS if anchor_ts else 0)
        )
        return cls(
            input_tokens                = sum(v[0] for v in vals),
            cache_creation_input_tokens = sum(v[1] for v in vals),
            cache_read_input_tokens     = sum(v[2] for v in vals),
            output_tokens               = sum(v[3] for v in vals),
            cache_anchor_epoch          = cache_anchor_epoch,
            cache_ttl                   = cache_ttl,
        )

    @classmethod
    def from_session(
        cls,
        transcript_path: str,
        *,
        cache: 'TranscriptCache | None' = None,
        main_usage: 'TranscriptUsage | None' = None,
    ) -> TranscriptUsage:
        """Main transcript usage PLUS every subagent transcript for this session.

        Subagent usage is persisted in a sibling directory next to the main
        transcript (`<session>.jsonl` -> `<session>/subagents/agent-*.jsonl`),
        not in the main transcript itself -- a coordinator-heavy session can
        burn several times more tokens in subagents than on the main thread,
        so `from_transcript` alone drastically undercounts true burn. Mirrors
        the discovery rule `RunningSubagents.from_session` already uses
        (yas.info.subagents) rather than re-deriving it.

        Applies NO sidechain filter to the subagent files -- every record in
        them is `isSidechain: true`, so filtering would zero their entire
        contribution (see the warning in yas.info.toolcounts). `message.id`
        values are disjoint across files, so summing each file's own
        last-write-wins result is safe without cross-file dedup.

        `main_usage`, when supplied, is used instead of re-parsing
        `transcript_path` -- callers (e.g. `SessionView.rate_limit_usage`)
        that already hold a cached `transcript_usage` for the same file pass
        it through so a render doesn't parse the (often large) main
        transcript twice.

        Returns main-only when `transcript_path` is empty or the
        `subagents/` sibling directory doesn't exist -- never crashes, never
        assumes the on-disk layout.
        """
        total = main_usage if main_usage is not None else cls.from_transcript(transcript_path)
        if not transcript_path:
            return total
        subdir = Path(transcript_path).with_suffix('') / 'subagents'
        if not subdir.is_dir():
            return total
        for jsonl in sorted(subdir.glob('agent-*.jsonl')):
            total += cls._from_transcript_cached(str(jsonl), cache)
        return total

    @classmethod
    def _from_transcript_cached(cls, path: str, cache: 'TranscriptCache | None') -> TranscriptUsage:
        """`from_transcript` for a subagent file, resumed from the fold cache."""
        return cls.from_transcript(path, cache=cache)

    @property
    def billed_in(self) -> int:
        return self.input_tokens + self.cache_creation_input_tokens

    @property
    def cache_read(self) -> int:
        return self.cache_read_input_tokens

    @property
    def out(self) -> int:
        return self.output_tokens
