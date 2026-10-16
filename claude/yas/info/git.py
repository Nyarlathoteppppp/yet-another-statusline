from __future__ import annotations
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from yas.constants import GIT_STATUS_CACHE_TTL, _sanitize, runtime_dir


class GitInfo:
    __slots__ = ('branch', 'commit', 'modified', 'untracked', 'deleted', 'renamed')

    def __init__(
        self,
        branch:    str = '',
        commit:    str = '',
        modified:  int = 0,
        untracked: int = 0,
        deleted:   int = 0,
        renamed:   int = 0,
    ) -> None:
        self.branch    = branch
        self.commit    = commit
        self.modified  = modified
        self.untracked = untracked
        self.deleted   = deleted
        self.renamed   = renamed

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GitInfo):
            return NotImplemented
        return (self.branch, self.commit, self.modified, self.untracked, self.deleted, self.renamed) == \
               (other.branch, other.commit, other.modified, other.untracked, other.deleted, other.renamed)

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return (f'GitInfo(branch={self.branch!r}, commit={self.commit!r}, modified={self.modified}, '
                f'untracked={self.untracked}, deleted={self.deleted}, renamed={self.renamed})')

    @classmethod
    def from_cwd(cls, cwd: str, status: bool = True) -> GitInfo:
        repo, gitdir   = cls._find_repo(cwd)
        branch, commit = cls._read_head(gitdir)
        modified = untracked = deleted = renamed = 0
        if branch and status:
            modified, untracked, deleted, renamed = cls._cached_dirty(repo, gitdir)
        return cls(
            branch    = branch,
            commit    = commit,
            modified  = modified,
            untracked = untracked,
            deleted   = deleted,
            renamed   = renamed,
        )

    @staticmethod
    def _find_repo(cwd: str) -> tuple[str, str]:
        curr = Path(cwd) if cwd else None
        while curr:
            if (curr / '.git').exists():
                return str(curr), str(curr / '.git')
            if curr == curr.parent:
                break
            curr = curr.parent
        return '', ''

    @staticmethod
    def _read_head(gitdir: str) -> tuple[str, str]:
        if not gitdir:
            return '', ''
        head_path = Path(gitdir) / 'HEAD'
        if not head_path.is_file():
            return '', ''
        try:
            head = head_path.read_text().strip()
        except OSError:
            return '', ''
        branch = ''
        if head.startswith('ref:'):
            target = head[4:].strip()
            if target.startswith('refs/heads/'):
                branch = target[len('refs/heads/'):]
            else:
                branch = target.rsplit('/', 1)[-1]
        elif head:
            branch = f'd:{head[:7]}'
        # .git/HEAD is repo-supplied (attacker-controlled for a cloned repo);
        # strip control chars so a crafted branch name can't inject escapes.
        branch = _sanitize(branch)
        commit = ''
        if branch and not branch.startswith('d:'):
            ref = Path(gitdir) / 'refs' / 'heads' / branch
            if ref.is_file():
                try:
                    commit = ref.read_text().strip()[:9]
                except OSError:
                    pass
        if not commit:
            orig = Path(gitdir) / 'ORIG_HEAD'
            if orig.is_file():
                try:
                    commit = orig.read_text().strip()[:9]
                except OSError:
                    pass
        return branch, commit

    @staticmethod
    def _real_gitdir(gitdir: str) -> Path:
        'Resolve a `.git` file (worktree `gitdir:` pointer) to the real git dir.'
        path = Path(gitdir)
        if path.is_dir():
            return path
        try:
            text = path.read_text().strip()
        except OSError:
            return path
        if not text.startswith('gitdir:'):
            return path
        target = Path(text[len('gitdir:'):].strip())
        return target if target.is_absolute() else path.parent / target

    @classmethod
    def _signals(cls, gitdir: str) -> list[object]:
        'Cheap invalidation signals: index mtime, HEAD text, HEAD ref file mtime.'
        real = cls._real_gitdir(gitdir)
        index_mtime = _mtime_ns(real / 'index')
        try:
            head = (real / 'HEAD').read_text().strip()
        except OSError:
            head = ''
        ref_mtime = 0
        if head.startswith('ref:'):
            ref_mtime = _mtime_ns(real / head[4:].strip())
        return [index_mtime, head, ref_mtime]

    @classmethod
    def _cached_dirty(cls, repo: str, gitdir: str) -> tuple[int, int, int, int]:
        path    = runtime_dir() / f'git-status.{hashlib.sha1(repo.encode()).hexdigest()[:16]}.json'
        signals = cls._signals(gitdir)
        now     = time.time()
        try:
            data   = json.loads(path.read_text())
            counts = data['counts']
            if now - data['ts'] <= GIT_STATUS_CACHE_TTL and data['signals'] == signals:
                modified, untracked, deleted, renamed = (int(n) for n in counts)
                return modified, untracked, deleted, renamed
        except (OSError, ValueError, KeyError, TypeError):
            pass
        result = cls._dirty(repo)
        cls._store(path, {'ts': now, 'signals': signals, 'counts': list(result)})
        return result

    @staticmethod
    def _store(path: Path, data: dict[str, object]) -> None:
        tmp = ''
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.git-status.')
            with os.fdopen(fd, 'w') as fh:
                json.dump(data, fh)
            os.replace(tmp, path)
        except OSError:
            if tmp:
                Path(tmp).unlink(missing_ok=True)

    @staticmethod
    def _dirty(repo: str) -> tuple[int, int, int, int]:
        modified = untracked = deleted = renamed = 0
        if not repo:
            return modified, untracked, deleted, renamed
        try:
            import subprocess
            r = subprocess.run(
                # --no-optional-locks: skip the index refresh write, so a
                # SIGKILL on timeout can't leave a stray .git/index.lock.
                ['git', '--no-optional-locks', '-C', repo, 'status',
                 '--porcelain=v1', '-z', '--untracked-files=normal'],
                capture_output=True, text=True, timeout=2,
            )
        except Exception:
            return modified, untracked, deleted, renamed
        entries = [e for e in r.stdout.split('\0') if e]
        i = 0
        while i < len(entries):
            entry = entries[i]
            if len(entry) < 2:
                i += 1
                continue
            x, y = entry[0], entry[1]
            if x == 'R' or y == 'R':
                renamed += 1
                i += 2  # rename consumes a second NUL-separated original-name field
                continue
            if x == '?' and y == '?':
                untracked += 1
            elif x == 'A' or y == 'A':
                untracked += 1
            elif x == 'D' or y == 'D':
                deleted += 1
            elif x == 'M' or y == 'M':
                modified += 1
            i += 1
        return modified, untracked, deleted, renamed


def _mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0
