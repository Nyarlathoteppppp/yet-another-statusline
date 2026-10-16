import json
import os
import subprocess
from pathlib import Path

import pytest

import yas.constants as constants
from yas.info import git as git_mod
from yas.info.git import GitInfo


class _Run:
    def __init__(self, stdout=' M a.py\0?? b.py\0'):
        self.calls  = 0
        self.stdout = stdout

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return subprocess.CompletedProcess(args, 0, stdout=self.stdout, stderr='')


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    (root / '.git' / 'refs' / 'heads').mkdir(parents=True)
    (root / '.git' / 'HEAD').write_text('ref: refs/heads/main\n')
    (root / '.git' / 'refs' / 'heads' / 'main').write_text('abc123abc123\n')
    (root / '.git' / 'index').write_bytes(b'x')
    monkeypatch.setattr(constants, 'CLAUDE_DIR', tmp_path / '.claude')
    return root


@pytest.fixture
def run(monkeypatch):
    fake = _Run()
    monkeypatch.setattr(subprocess, 'run', fake)
    return fake


def _cache_file() -> Path:
    files = list(constants.runtime_dir().glob('git-status.*.json'))
    assert len(files) == 1
    return files[0]


class TestGitStatusCache:
    def test_hit_reuses_result_without_subprocess(self, repo, run):
        first  = GitInfo.from_cwd(str(repo))
        second = GitInfo.from_cwd(str(repo))

        assert run.calls == 1
        assert second == first
        assert (second.modified, second.untracked) == (1, 1)

    def test_ttl_expiry_reruns(self, repo, run, monkeypatch):
        GitInfo.from_cwd(str(repo))
        monkeypatch.setattr(git_mod, 'GIT_STATUS_CACHE_TTL', -1.0)
        GitInfo.from_cwd(str(repo))

        assert run.calls == 2

    def test_index_mtime_change_invalidates(self, repo, run):
        GitInfo.from_cwd(str(repo))
        index = repo / '.git' / 'index'
        os.utime(index, ns=(1, index.stat().st_mtime_ns + 1_000_000))
        GitInfo.from_cwd(str(repo))

        assert run.calls == 2

    def test_head_change_invalidates(self, repo, run):
        GitInfo.from_cwd(str(repo))
        (repo / '.git' / 'HEAD').write_text('ref: refs/heads/other\n')
        GitInfo.from_cwd(str(repo))

        assert run.calls == 2

    @pytest.mark.parametrize('content', ['', 'not json', '{"ts": "x"}', '[]'])
    def test_corrupt_cache_is_a_miss(self, repo, run, content):
        GitInfo.from_cwd(str(repo))
        _cache_file().write_text(content)
        result = GitInfo.from_cwd(str(repo))

        assert run.calls == 2
        assert (result.modified, result.untracked) == (1, 1)

    def test_cache_file_holds_counts_and_signals(self, repo, run):
        GitInfo.from_cwd(str(repo))
        data = json.loads(_cache_file().read_text())

        assert data['counts'] == [1, 1, 0, 0]
        assert data['signals'][1] == 'ref: refs/heads/main'

    def test_worktree_gitdir_pointer_is_resolved(self, repo, run, tmp_path):
        real = tmp_path / 'real-gitdir'
        real.mkdir()
        (real / 'HEAD').write_text('ref: refs/heads/main\n')
        wt = tmp_path / 'wt'
        wt.mkdir()
        (wt / '.git').write_text(f'gitdir: {real}\n')
        GitInfo._cached_dirty(str(wt), str(wt / '.git'))
        GitInfo._cached_dirty(str(wt), str(wt / '.git'))

        assert run.calls == 1


class TestShowGitStatusOff:
    def test_off_runs_no_subprocess_and_no_counts(self, repo, run):
        result = GitInfo.from_cwd(str(repo), status=False)

        assert run.calls == 0
        assert result.branch == 'main'
        assert (result.modified, result.untracked, result.deleted, result.renamed) == (0, 0, 0, 0)
        assert not list(constants.runtime_dir().glob('git-status.*.json'))
