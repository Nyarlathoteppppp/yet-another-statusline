import shutil
import subprocess
from pathlib import Path

import pytest

from yas.config import Config
from yas.info import SessionView
from yas.session import SessionInfo, Worktree


def _session(**d: object) -> SessionInfo:
    return SessionInfo.from_dict(d)


def test_worktree_path_wins_over_cwd() -> None:
    s = _session(
        cwd='/a/main',
        workspace={'current_dir': '/a/main'},
        worktree={'path': '/a/wt', 'branch': 'feat/x'},
    )
    assert s.effective_cwd == '/a/wt'
    assert s.short_pwd.endswith('/wt')


def test_current_dir_used_without_worktree() -> None:
    s = _session(cwd='/a/main', workspace={'current_dir': '/a/sub'})
    assert s.effective_cwd == '/a/sub'


def test_cwd_fallback() -> None:
    assert _session(cwd='/a/main').effective_cwd == '/a/main'


@pytest.mark.parametrize('bad', ['str', 5, None, [], {'path': 7, 'branch': []}, {'path': ''}])
def test_malformed_worktree_ignored(bad: object) -> None:
    s = _session(cwd='/a/main', worktree=bad)
    assert s.worktree == Worktree()
    assert s.effective_cwd == '/a/main'


def test_worktree_branch_fallback_when_git_lookup_fails(tmp_path: Path) -> None:
    s = _session(cwd=str(tmp_path), worktree={'path': str(tmp_path), 'branch': 'feat/x'})
    assert SessionView(s, Config()).git.branch == 'feat/x'


@pytest.mark.skipif(shutil.which('git') is None, reason='git not installed')
def test_real_linked_worktree_shows_worktree_branch(tmp_path: Path) -> None:
    main, wt = tmp_path / 'main', tmp_path / 'wt'
    def run(*args: str) -> None:
        subprocess.run(['git', '-C', str(main), *args], check=True, capture_output=True)
    subprocess.run(['git', 'init', '-b', 'main', str(main)], check=True, capture_output=True)
    run('config', 'user.email', 't@t.com')
    run('config', 'user.name', 'T')
    (main / 'f.txt').write_text('x\n')
    run('add', 'f.txt')
    run('commit', '-m', 'init')
    run('worktree', 'add', '-b', 'feat/wt', str(wt))
    s = _session(cwd=str(main), worktree={'path': str(wt), 'branch': 'wrong'})
    git = SessionView(s, Config()).git
    assert git.branch == 'feat/wt'
    assert git.commit
