"""Loaded skills reader — parses skill names from a Claude transcript."""

from __future__ import annotations

import os
import re
import stat
from typing import TYPE_CHECKING

from yas.constants import _sanitize
from yas.info.parsecache import fold_file

if TYPE_CHECKING:
    from yas.info.parsecache import TranscriptCache

_SKILL_PAT      = re.compile(r'"name"\s*:\s*"Skill"[^}]*?"skill"\s*:\s*"([^"]+)"')
_READ_PAT       = re.compile(r'"name"\s*:\s*"Read"[^}]*?"file_path"\s*:\s*"([^"]+)"')
_SKILL_PATH_PAT = re.compile(r'/skills/([^/"]+)/SKILL\.md$')


def _skills_fold(names: list[str], lines: list[bytes]) -> list[str]:
    """Append skill names in first-occurrence order (pure append-only union)."""
    seen = set(names)
    for raw in lines:
        has_skill = b'"Skill"' in raw
        has_read  = b'"Read"' in raw and b'SKILL.md' in raw
        if not (has_skill or has_read):
            continue
        ln = raw.decode('utf-8', 'ignore')
        found: list[str] = []
        if has_skill:
            found.extend(m.group(1) for m in _SKILL_PAT.finditer(ln))
        if has_read:
            for m in _READ_PAT.finditer(ln):
                sm = _SKILL_PATH_PAT.search(m.group(1))
                if sm:
                    found.append(sm.group(1))
        for raw_name in found:
            name = _sanitize(raw_name)
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


class LoadedSkills:
    __slots__ = ('names',)

    def __init__(self, names: list[str] | None = None) -> None:
        self.names = names if names is not None else []

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LoadedSkills):
            return NotImplemented
        return self.names == other.names

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f'LoadedSkills(names={self.names!r})'

    @classmethod
    def from_transcript(
        cls,
        transcript_path: str,
        *,
        cache: 'TranscriptCache | None' = None,
    ) -> LoadedSkills:
        if not transcript_path:
            return cls()
        try:
            st = os.stat(transcript_path)
        except OSError:
            return cls()
        if not stat.S_ISREG(st.st_mode):
            return cls()
        names = fold_file(cache, transcript_path, st, 'skills', '', list, _skills_fold)
        return cls(names=list(names))
