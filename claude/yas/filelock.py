"""Cross-process safe read-modify-write for the small shared runtime logs.

Many sessions render concurrently and share token-rate.log / render.log. A
writer takes an exclusive flock on a sidecar `<file>.lock` for the whole
read-modify-write, and publishes via tempfile + os.replace so lock-free readers
never see a partial file. Where fcntl is missing (Windows) the lock is skipped
but the atomic replace is kept.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    'Hold an exclusive lock on `<path>.lock`; best-effort, never raises OSError.'
    fd = -1
    if fcntl is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(f'{path}.lock', os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError:
            if fd >= 0:
                os.close(fd)
            fd = -1
    try:
        yield
    finally:
        if fd >= 0:
            os.close(fd)  # closing releases the flock


def atomic_write_text(path: Path, text: str) -> None:
    'Write `text` to a temp file in the same dir, then os.replace onto `path`. Raises OSError.'
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
