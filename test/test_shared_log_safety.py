import multiprocessing
import re
import time

from yas.constants import render_log, token_rate_log
from yas.tokens import RenderTiming, TokenRate

N_PROCS = 6
M_WRITES = 25

TOKEN_ROW_RE  = re.compile(r'^\d+\.\d{3} \S+ \d+ \d+$')
RENDER_ROW_RE = re.compile(r'^\d+\.\d{3} \S+ \d+\.\d$')


def _token_worker(claude_dir, idx):
    from yas import constants
    constants.CLAUDE_DIR = claude_dir
    for i in range(M_WRITES):
        TokenRate.update(f'sess-{idx}', i * 10, i * 5)


def _render_worker(claude_dir, idx):
    from yas import constants
    constants.CLAUDE_DIR = claude_dir
    for i in range(M_WRITES):
        RenderTiming.write(f'sess-{idx}', float(i))


def _run_procs(target, claude_dir):
    ctx = multiprocessing.get_context('fork')
    procs = [ctx.Process(target=target, args=(claude_dir, i)) for i in range(N_PROCS)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
    return [p.exitcode for p in procs]


class TestConcurrentTokenRateWrites:
    def test_no_rows_lost(self, tmp_home):
        from yas import constants

        exitcodes = _run_procs(_token_worker, constants.CLAUDE_DIR)
        lines = token_rate_log().read_text().splitlines()

        result = (exitcodes, len(lines), sorted({ln.split()[1] for ln in lines}))
        expected = ([0] * N_PROCS, N_PROCS * M_WRITES, [f'sess-{i}' for i in range(N_PROCS)])
        assert result == expected

    def test_row_format_unchanged(self, tmp_home):
        TokenRate.update('abc', 100, 50)

        lines = token_rate_log().read_text().splitlines()

        result = [bool(TOKEN_ROW_RE.match(ln)) for ln in lines]
        expected = [True]
        assert result == expected
        assert token_rate_log().read_text().endswith('\n')

    def test_no_temp_files_left_behind(self, tmp_home):
        TokenRate.update('abc', 100, 50)

        result = sorted(p.name for p in token_rate_log().parent.iterdir() if p.name.endswith('.tmp'))

        expected = []
        assert result == expected


class TestConcurrentRenderTimingWrites:
    def test_no_sessions_lost(self, tmp_home):
        from yas import constants

        exitcodes = _run_procs(_render_worker, constants.CLAUDE_DIR)
        lines = render_log().read_text().splitlines()

        result = (exitcodes, sorted(ln.split()[1] for ln in lines), [ln.split()[2] for ln in lines])
        expected = ([0] * N_PROCS, [f'sess-{i}' for i in range(N_PROCS)], [f'{M_WRITES - 1}.0'] * N_PROCS)
        assert result == expected

    def test_row_format_unchanged(self, tmp_home):
        RenderTiming.write('abc', 12.34)

        lines = render_log().read_text().splitlines()

        result = [bool(RENDER_ROW_RE.match(ln)) for ln in lines]
        expected = [True]
        assert result == expected


class TestOldFormatFilesStillParse:
    def test_token_rate_readers(self, tmp_home):
        now = time.time()
        log = token_rate_log()
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            f'{now - 4:.3f} s1 100 10\n'
            f'{now - 2:.3f} s1 200 50\n'
            f'{now - 1:.3f} other 5 5\n'
        )

        result = (
            TokenRate.history('s1', 4, 8.0),
            TokenRate.recently_active('s1', 10.0),
            TokenRate.update('s1', 300, 90),
        )

        assert sum(result[0]) == 140
        assert result[1] == (True, True)
        assert result[2] == (300 + 90) - (100 + 10)

    def test_update_preserves_foreign_rows(self, tmp_home):
        now = time.time()
        log = token_rate_log()
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(f'{now - 1:.3f} other 5 5\n{now - 999:.3f} stale 1 1\n')

        TokenRate.update('s1', 1, 1)

        result = [ln.split()[1] for ln in log.read_text().splitlines()]
        expected = ['other', 's1']
        assert result == expected

    def test_render_log_reader(self, tmp_home):
        now = time.time()
        log = render_log()
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(f'{now - 3:.3f} s1 42.5\n{now - 2:.3f} s2 7.0\n')

        result = RenderTiming.read('s1')

        expected = 42.5
        assert result == expected

    def test_render_write_keeps_other_sessions(self, tmp_home):
        now = time.time()
        log = render_log()
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(f'{now - 3:.3f} s1 42.5\n{now - 2:.3f} s2 7.0\n')

        RenderTiming.write('s1', 9.0)

        result = (RenderTiming.read('s1'), RenderTiming.read('s2'))
        expected = (9.0, 7.0)
        assert result == expected
