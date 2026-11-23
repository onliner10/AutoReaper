import asyncio
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile

from autoreaper import bridge as bridge_module, server
from autoreaper.wav import wav_info, wav_peak_dbfs

# The skill's measuring script, run by uv on its own, imported here as a module.
_spec = importlib.util.spec_from_file_location(
    'analyze', Path(__file__).parents[1] / 'skills' / 'reaper' / 'scripts' / 'analyze.py')
audio = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audio)

SR = 48000


def test_capture_range_bars_seconds_and_rejections():
    assert server.capture_range(117, 133, None, None) == ('bars', 117, 133)
    assert server.capture_range(None, None, 1, 2.5) == ('seconds', 1.0, 2.5)
    for bad in ((None, None, None, None), (3, 3, None, None), (0, 2, None, None), (1.0, 2, None, None),
                (1, None, None, None), (1, 600, None, None), (1, 2, 0, None), (None, None, 2, 1),
                (None, None, True, 2)):
        with pytest.raises(ValueError):
            server.capture_range(*bad)


def test_track_guids():
    guid = '{0A1B2C3D-0000-1111-2222-333344445555}'
    assert server.coerce_track_guids(json.dumps([guid])) == [guid]
    assert server.coerce_track_guids(guid) == [guid] and server.coerce_track_guids(None) == []
    assert server.coerce_track_guids(['Kick', guid]) == ['Kick', guid]
    with pytest.raises(ValueError):
        server.coerce_track_guids(['master'])
    with pytest.raises(ValueError):
        server.coerce_track_guids([1])


def test_summarize_grid_maps_bars_to_starts():
    bar = 60 / 132 * 4
    steady = [{'bar': 117 + i, 'start_seconds': 210.909090909 + i * bar, 'end_seconds': 210.909090909 + (i + 1) * bar,
               'tempo': 132.0, 'numerator': 4, 'denominator': 4} for i in range(3)]
    # A steady grid is one bar length (the sidecar keeps every bar).
    one = {'bar_seconds': 1.818182, 'first_bar': 117, 'first_bar_start_seconds': 210.9091, 'tempo_bpm': 132.0,
           'meter': '4/4'}
    assert server.summarize_grid(steady) == one and server.summarize_grid(steady[:1]) == one
    changing = steady[:2] + [{**steady[2], 'tempo': 120.0}]
    changing[2]['start_seconds'] += .1
    assert server.summarize_grid(changing) == {
        'bars': {'117': 210.9091, '118': 212.7273, '119': 214.6455}, 'tempo_bpm': [120.0, 132.0], 'meter': '4/4'}


def test_output_moves_large_results_to_a_file(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTOREAPER_HOME', str(tmp_path))
    assert json.loads(server.output({'ok': True, 'track': 'Bęben'}, 't')) == {'ok': True, 'track': 'Bęben'}
    summary = json.loads(server.output({'ok': True, 'big': 'x' * (server.MAX_INLINE_CHARS + 1)}, 'inspect'))
    assert summary['ok'] is True and 'big' not in summary and summary['keys'] == ['ok', 'big']
    assert json.loads(open(summary['result_file'], encoding='utf-8').read())['big'].startswith('xxx')


def test_prune_captures_by_age_size_and_stale_workspaces(tmp_path, monkeypatch):
    import os
    now = 1_000_000_000.0
    day = 24 * 60 * 60

    def capture(capture_id, age, size=10, extra=()):
        paths = [tmp_path / f'capture-{capture_id}.wav', tmp_path / f'capture-{capture_id}.json',
                 *(tmp_path / f'capture-{capture_id}-{name}.png' for name in extra)]
        for path in paths:
            path.write_bytes(b'x' * size)
            os.utime(path, (now - age, now - age))
        return paths

    old = capture('a' * 12, 3 * day, extra=['full'])
    fresh = capture('b' * 12, 60)
    # An old WAV whose spectrogram was just made counts as recent.
    analysed = capture('c' * 12, 3 * day)
    os.utime(analysed[1], (now - 120, now - 120))
    stale = tmp_path / 'render-111111111111'
    stale.mkdir()
    (stale / 'mix.wav').write_bytes(b'x')
    os.utime(stale, (now - day, now - day))
    running = tmp_path / 'render-222222222222'
    running.mkdir()
    os.utime(running, (now - 10, now - 10))
    unrelated = tmp_path / 'notes.txt'
    unrelated.write_text('keep')

    server.prune_captures(tmp_path, now=now)
    assert not any(p.exists() for p in old) and all(p.exists() for p in fresh + analysed)
    assert not stale.exists() and running.is_dir() and unrelated.exists()

    # Over the size cap recent captures are spared...
    monkeypatch.setattr(server, 'MAX_CAPTURE_BYTES', 50)
    newest = capture('d' * 12, 10, size=15)
    server.prune_captures(tmp_path, now=now)
    assert all(p.exists() for p in newest + fresh + analysed)
    # ...and once they are older than that, the oldest go first.
    monkeypatch.setattr(server, 'PROTECTED_CAPTURE_SECONDS', 0)
    server.prune_captures(tmp_path, now=now)
    assert all(p.exists() for p in newest + fresh) and not any(p.exists() for p in analysed)


def test_prune_scratch_covers_temp_legacy_and_results(tmp_path, monkeypatch):
    import os
    home, captures = tmp_path / 'home', tmp_path / 'temp-captures'
    monkeypatch.setenv('AUTOREAPER_HOME', str(home))
    monkeypatch.setenv('AUTOREAPER_CAPTURES', str(captures))
    assert server.captures_directory() == captures
    old = time.time() - 3 * 24 * 3600
    files = [captures / 'capture-aaaaaaaaaaaa.wav', home / 'captures' / 'capture-bbbbbbbbbbbb.wav',
             home / 'results' / 'inspect-1.json']
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x')
        os.utime(path, (old, old))
    server.prune_scratch()
    assert not any(p.exists() for p in files)


def test_captures_default_to_the_os_temp_folder(monkeypatch):
    import tempfile
    monkeypatch.delenv('AUTOREAPER_CAPTURES', raising=False)
    if not server.sys.platform.startswith('linux'):
        assert server.captures_directory() == Path(tempfile.gettempdir()) / 'autoreaper' / 'captures'


def test_main_prunes_at_startup(monkeypatch):
    pruned = threading.Event()
    monkeypatch.setattr(server, 'prune_scratch', pruned.set)
    monkeypatch.setattr(server.mcp, 'run', lambda: None)
    server.main()
    assert pruned.wait(5)


def test_prune_results_by_age(tmp_path):
    import os
    old, new = tmp_path / 'inspect-1.json', tmp_path / 'inspect-2.json'
    for path, age in ((old, 3 * 24 * 3600), (new, 60)):
        path.write_text('{}')
        os.utime(path, (1e9 - age, 1e9 - age))
    server.prune_results(tmp_path, now=1e9)
    assert not old.exists() and new.exists()


def test_server_lists_its_tools_with_read_only_hints():
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    assert set(tools) == {'reaper_status', 'install_bridge', 'inspect_project', 'inspect_signal_flow',
                          'search_installed_fx', 'reaper_eval', 'read_receipt', 'reaper_eval_write',
                          'capture', 'add_fx', 'fx_parameters', 'set_fx_parameters', 'edit_fx', 'sidechain_send',
                          'add_faust_fx', 'read_faust_fx', 'edit_faust_fx', 'install_faust_plugin', 'compare_variants'}
    read_only = {name for name, tool in tools.items() if tool.annotations and tool.annotations.readOnlyHint}
    assert read_only == {'reaper_status', 'inspect_project', 'inspect_signal_flow', 'search_installed_fx',
                         'read_receipt', 'fx_parameters', 'read_faust_fx'}
    # reaper_eval is full ReaScript (it reports changes rather than preventing them), so it is not read-only.
    assert tools['reaper_eval'].annotations.destructiveHint is False
    assert tools['reaper_eval_write'].annotations.destructiveHint is True


def test_install_bridge_copies_the_script(tmp_path):
    path = bridge_module.install_bridge_script(tmp_path)
    assert path == tmp_path / 'Scripts' / 'AutoReaper Bridge.lua'
    assert 'AUTOREAPER_HOME' in path.read_text(encoding='utf-8')
    with pytest.raises(FileNotFoundError):
        bridge_module.install_bridge_script(tmp_path / 'missing')


def fake_reaper(directory, answer, stop):
    """Plays the Lua side: heartbeat, claim request.lua, write the receipt."""
    while not stop.is_set():
        (directory / 'heartbeat.json').write_text(json.dumps(
            {'session': 'S', 'timestamp': time.time(), 'project_id': 'P', 'protocol': 2}), encoding='utf-8')
        request = directory / 'request.lua'
        if request.exists():
            text = request.read_text(encoding='utf-8')
            request.unlink()
            request_id = text.split('id="', 1)[1].split('"', 1)[0]
            request_id = ''.join(chr(int(n)) for n in request_id.split('\\')[1:])
            (directory / f'{request_id}.json').write_text(json.dumps(answer(text)), encoding='utf-8')
        time.sleep(.01)


def test_bridge_round_trip_and_missing_bridge(tmp_path):
    client = bridge_module.ReaperBridge(tmp_path)
    with pytest.raises(bridge_module.BridgeError, match='not running'):
        client.status()
    stop = threading.Event()
    thread = threading.Thread(target=fake_reaper, daemon=True, args=(
        tmp_path, lambda text: {'ok': True, 'outcome': 'read_only', 'changed': False, 'result': {'n': 1}}, stop))
    thread.start()
    try:
        time.sleep(.05)
        receipt = asyncio.run(client.evaluate('return 1', mutate=False))
        assert receipt['result'] == {'n': 1}
        with pytest.raises(bridge_module.BridgeError, match='project_id'):
            asyncio.run(client.evaluate('x()', mutate=True))
    finally:
        stop.set()
        thread.join()
    assert not list(tmp_path.glob('*.json'))[1:]  # only the heartbeat is left


def write_capture(path, samples, start, bars):
    """A WAV plus the sidecar capture writes: bar grid from {bar: start_seconds}."""
    soundfile.write(str(path), samples, SR, subtype='FLOAT')
    grid = audio.normalize_bar_grid(bars, len(samples) / SR, start)
    path.with_suffix('.json').write_text(json.dumps({'wav_start_seconds': start, 'bar_grid': grid}))


def test_wav_info_reads_float_wav_headers(tmp_path):
    x = sine(1000, 1.5)
    soundfile.write(str(tmp_path / 'f.wav'), np.stack([x, x], 1), SR, subtype='FLOAT')
    info = wav_info(tmp_path / 'f.wav')
    assert info['sample_rate'] == SR and info['channels'] == 2 and info['sample_format'] == 'FLOAT'
    assert info['duration_seconds'] == pytest.approx(1.5)


def test_analyze_prints_levels_table_difference_and_spectrogram(tmp_path, capsys):
    x = sine(1000, 1.0, 0.5)
    after, before = tmp_path / 'after.wav', tmp_path / 'before.wav'
    write_capture(after, np.stack([x, x], 1), 10.0, {'5': 10.0, '6': 10.5})
    write_capture(before, np.stack([x, x], 1) * 0.5, 10.0, {'5': 10.0, '6': 10.5})
    assert audio.main([str(after), '--compare', str(before), '--spectrogram', '--panels', 'side']) == 0
    lines = capsys.readouterr().out.splitlines()
    summary = json.loads(lines[0])
    assert summary['bar_grid_source'] == 'sidecar' and summary['compared_bars'] == 2
    assert summary['levels']['peak_dbfs'] == pytest.approx(-6.02, abs=0.01)
    assert summary['level_difference_db']['rms_dbfs'] == pytest.approx(6.02, abs=0.01)
    assert lines[3].startswith('5 ') and lines[4].startswith('6 ')
    diff = [line for line in lines if line.startswith('5 ')][1]
    assert float(diff.split()[4]) == pytest.approx(6.0, abs=0.15)  # mid band 500-2000 Hz: twice the amplitude
    assert Path(summary['spectrogram']['png']).exists()


def test_analyze_without_bar_grid_compares_whole_files(tmp_path, capsys):
    x = sine(100, 1.0, 0.5)
    soundfile.write(str(tmp_path / 'a.wav'), np.stack([x, x], 1), SR, subtype='FLOAT')
    soundfile.write(str(tmp_path / 'b.wav'), np.stack([x, x], 1), SR, subtype='FLOAT')
    assert audio.main([str(tmp_path / 'a.wav'), '--compare', str(tmp_path / 'b.wav')]) == 0
    out = capsys.readouterr().out
    assert '# no bar grid' in out and any(line.startswith('all ') for line in out.splitlines())


def test_normalize_bar_grid_from_map_and_list():
    rows = audio.normalize_bar_grid({'2': 11.0, '1': 10.0}, duration_seconds=3.0, wav_start_seconds=10.0)
    assert rows == [{'bar': 1, 'start_seconds': 10.0, 'end_seconds': 11.0},
                    {'bar': 2, 'start_seconds': 11.0, 'end_seconds': 13.0}]
    assert audio.normalize_bar_grid(None) == []
    with pytest.raises(ValueError):
        audio.normalize_bar_grid([{'bar': 1, 'start_seconds': 2, 'end_seconds': 1}])


def sine(freq, seconds, amplitude=1.0):
    t = np.arange(int(SR * seconds)) / SR
    return amplitude * np.sin(2 * np.pi * freq * t)


def test_segment_row_puts_a_sine_in_its_band():
    x = sine(100, 1.0, 0.5)
    row = audio.segment_row(np.stack([x, x], 1), SR)
    assert row['low'] == pytest.approx(-9.0, abs=0.1)  # 0.5 amplitude sine: RMS -9.03 dBFS
    assert row['total'] == pytest.approx(-9.0, abs=0.1)
    assert row['sub'] < -60 and row['lowmid'] < -60
    assert row['centroid_hz'] == 100
    assert row['side_mid_db'] == audio.FLOOR_DB  # mono


def test_segment_row_side_mid_for_one_sided_signal():
    x = sine(1000, 0.5)
    row = audio.segment_row(np.stack([x, np.zeros_like(x)], 1), SR)
    assert row['side_mid_db'] == pytest.approx(0.0, abs=0.01)
    assert row['mid'] == pytest.approx(-6.0, abs=0.1)  # mean of L/R power: one channel at -3


def test_bar_table_slices_bars_and_clips_to_the_recording():
    quiet, loud = sine(3000, 1.0, 0.01), sine(3000, 1.0, 1.0)
    samples = np.concatenate([quiet, loud])
    grid = [{'bar': 9, 'start_seconds': 4.0, 'end_seconds': 5.0},
            {'bar': 10, 'start_seconds': 5.0, 'end_seconds': 7.0}]
    rows = audio.bar_table(samples, SR, grid, wav_start_seconds=4.0)
    assert [r['bar'] for r in rows] == [9, 10]
    assert rows[0]['highmid'] == pytest.approx(-43.0, abs=0.1)
    assert rows[1]['highmid'] == pytest.approx(-3.0, abs=0.1)
    assert audio.bar_segments(grid, 4.0, 2.0, SR, 2 * SR) == [(9, 0, SR), (10, SR, 2 * SR)]


def test_format_table_is_one_line_per_bar():
    bands = ('sub', 'low', 'lowmid', 'mid', 'highmid', 'high', 'air', 'total')
    rows = [{'bar': 117, **{k: -20.0 for k in bands}, 'centroid_hz': 1500, 'side_mid_db': -8.0}] * 3
    lines = audio.format_table(rows).splitlines()
    assert len(lines) == 2 + 3 and lines[2].startswith('117') and lines[2].rstrip().endswith('-8.0')


def test_frequency_axis_and_bar_marks():
    assert audio.frequency_to_y(20000, 20, 20000, 300) == 0
    assert audio.frequency_to_y(20, 20, 20000, 300) == pytest.approx(300)
    assert audio.frequency_to_y(632.46, 20, 20000, 300) == pytest.approx(150, abs=0.1)
    grid = [{'bar': b, 'start_seconds': 10 + 2 * i, 'end_seconds': 12 + 2 * i}
            for i, b in enumerate(range(116, 120))]
    assert audio.time_marks(grid, 10.0, 8.0) == [
        (0.0, '116', False), (2.0, '117', True), (4.0, '118', False), (6.0, '119', False), (8.0, '120', False)]


def test_spectrogram_shows_a_sine_at_its_row_and_level(tmp_path):
    x = sine(SR / 4096 * 85, 2.0)  # bin-centred, no Hann scalloping loss
    freqs, power = audio.stft_power(x, SR, 10, 4096)
    assert 10 * np.log10(power.max()) == pytest.approx(0.0, abs=0.5)  # full-scale sine = 0 dB
    rows = audio.resample_log(freqs, power, 20, 20000, 300)
    peak_row = int(np.argmax(rows[:, 5]))
    assert peak_row == pytest.approx(audio.frequency_to_y(996.1, 20, 20000, 300), abs=2)
    receipt = audio.render_spectrogram(np.stack([x, x], 1), SR, tmp_path / 's.png', title='t',
                                          panels=('full', 'side'))
    assert (tmp_path / 's.png').exists() and receipt['db_scale']['top_dbfs'] == 0
    assert receipt['x_axis'].startswith('seconds')


def test_analyze_steps_show_what_plays_on_the_beat(tmp_path, capsys):
    # Three 2-second bars; an 80 Hz burst on each beat (first sixteenth of four), silence otherwise.
    t = np.arange(6 * SR) / SR
    on_beat = (t % 0.5) < 0.125
    x = np.where(on_beat, 0.5 * np.sin(2 * np.pi * 80 * t), 0.0)
    after, before = tmp_path / 'after.wav', tmp_path / 'before.wav'
    write_capture(after, np.stack([x, x], 1), 0.0, {'1': 0.0, '2': 2.0, '3': 4.0})
    write_capture(before, np.stack([x, x], 1) * 0.5, 0.0, {'1': 0.0, '2': 2.0, '3': 4.0})
    rows = audio.step_table(*audio.load(str(after))[:4], 16)
    assert [row['bar'] for row in rows[:5]] == ['1.1', '1.2', '1.3', '1.4', '2.1']
    assert rows[0]['low'] > rows[1]['low'] + 40 and rows[4]['low'] == pytest.approx(rows[0]['low'], abs=0.1)
    assert audio.main([str(after), '--compare', str(before), '--steps', '16', '--bars', '2-3', '--no-table']) == 0
    lines = capsys.readouterr().out.splitlines()
    assert json.loads(lines[0])['compared_bars'] == 2
    assert lines[1].startswith('# per-step') and 'average of bars 2-3' in lines[1]
    step_diff = next(line for line in lines if line.startswith('# difference per step'))
    first = lines[lines.index(step_diff) + 2]
    assert first.startswith('1.1 ') and float(first.split()[2]) == pytest.approx(6.0, abs=0.1)  # low band


_docs_spec = importlib.util.spec_from_file_location(
    'faust_docs', Path(__file__).parents[1] / 'skills' / 'reaper' / 'scripts' / 'faust_docs.py')
faust_docs = importlib.util.module_from_spec(_docs_spec)
_docs_spec.loader.exec_module(faust_docs)


def test_faust_docs_reads_the_cache_without_network(tmp_path, monkeypatch, capsys):
    assert sorted(['2.72.13', '2.70.3', '2.8.0'], key=faust_docs.version_key) == ['2.8.0', '2.70.3', '2.72.13']
    folder = tmp_path / 'faust-docs' / '2.70.3'
    folder.mkdir(parents=True)
    for name in ('SOURCE.md', 'syntax.md', 'midi.md', 'errors.md'):
        (folder / name).write_text('x', encoding='utf-8')
    monkeypatch.setenv('AUTOREAPER_HOME', str(tmp_path))
    monkeypatch.setattr(faust_docs.subprocess, 'run', lambda *a, **k: pytest.fail('no git or faust when cached'))
    monkeypatch.setattr('sys.argv', ['faust_docs.py', '--version', '2.70.3 (LLVM 17.0.6)'])
    faust_docs.main()
    assert capsys.readouterr().out.strip() == str(folder)
    monkeypatch.setattr('sys.argv', ['faust_docs.py', '--version', 'latest'])
    with pytest.raises(SystemExit):
        faust_docs.main()


def test_wav_peak_dbfs_reads_float_captures(tmp_path):
    x = sine(1000, 1.0, 0.25)
    soundfile.write(str(tmp_path / 'f.wav'), np.stack([x, -x * 2], 1), SR, subtype='FLOAT')
    assert wav_peak_dbfs(tmp_path / 'f.wav') == pytest.approx(-6.02, abs=0.01)
    soundfile.write(str(tmp_path / 'p.wav'), np.stack([x, x], 1), SR, subtype='PCM_24')
    assert wav_peak_dbfs(tmp_path / 'p.wav') is None


def test_loudness_of_a_sine_matches_bs1770(tmp_path, capsys):
    # A 997 Hz sine at -20 dBFS peak in both channels reads -20 LUFS (K-weighting is ~0 dB there).
    x = sine(997, 40.0, 0.1)
    path = tmp_path / 'tone.wav'
    write_capture(path, np.stack([x, x], 1), 0.0, {str(b): (b - 1) * 2.0 for b in range(1, 21)})
    assert audio.main([str(path), '--loudness']) == 0
    lines = capsys.readouterr().out.splitlines()
    loud = json.loads(lines[0])['loudness']
    for key in ('integrated_lufs', 'max_short_term_lufs', 'loudest_30s_lufs', 'true_peak_dbtp'):
        assert loud[key] == pytest.approx(-20.0, abs=0.1), key
    assert lines[2].endswith('LUFS') and float(lines[3].split()[-1]) == pytest.approx(-20.0, abs=0.15)


def test_loudness_gate_ignores_silence(tmp_path, capsys):
    x = np.concatenate([sine(997, 10.0, 0.1), np.zeros(SR * 30)])
    path = tmp_path / 'gap.wav'
    soundfile.write(str(path), np.stack([x, x], 1), SR, subtype='FLOAT')
    assert audio.main([str(path), '--loudness', '--no-table']) == 0
    loud = json.loads(capsys.readouterr().out.splitlines()[0])['loudness']
    assert loud['integrated_lufs'] == pytest.approx(-20.0, abs=0.1)


def test_table_compares_labelled_files_with_the_baseline_and_its_noise_floor(tmp_path, capsys):
    t = np.arange(SR * 4) / SR
    tone = 0.1 * np.sin(2 * np.pi * 1000 * t)
    files = {'base': tone, 'again': tone * 1.0001, 'louder': tone * 2, 'bassier': tone + 0.1 * np.sin(2 * np.pi * 100 * t)}
    for name, x in files.items():
        soundfile.write(str(tmp_path / f'{name}.wav'), np.stack([x, x], 1), SR, subtype='FLOAT')
    args = ['table', *(f'{name}={tmp_path / name}.wav' for name in files), '--baseline', 'base', '--noise', 'again']
    assert audio.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    diff = {row['label']: row for row in result['vs_baseline']}
    assert set(diff) == {'louder', 'bassier'} and [f['label'] for f in result['files']] == list(files)
    assert diff['louder']['rms_dbfs'] == pytest.approx(6.02, abs=.05)
    assert diff['louder']['integrated_lufs'] == pytest.approx(6.02, abs=.1)
    assert diff['bassier']['bands']['low'] > 40 and abs(diff['bassier']['bands']['mid']) < .2
    assert abs(result['noise_floor']['rms_dbfs']) < .01


def test_table_rejects_bad_labels(tmp_path):
    with pytest.raises(SystemExit):
        audio.main(['table', 'nolabel.wav', '--baseline', 'x'])
    with pytest.raises(SystemExit):
        audio.main(['table', f'a={tmp_path}/a.wav', '--baseline', 'b'])


class FakeSeries:
    """compare_variants against fakes: records the order of renders and applies."""

    def __init__(self, monkeypatch, fail_apply=None, fail_restore=False, analysis=({'ok': True, 'files': []}, None)):
        self.log = []
        self.fail_apply, self.fail_restore = fail_apply, fail_restore
        self.silent, self.project = False, 'p'

        async def guids(refs):
            return ['{T}'] if refs else []

        async def read_query(code, label):
            return {'ok': True, 'originals': [{'track': '{T}', 'fx': '{F}', 'param': 1, 'value': .5, 'name': 'Gain',
                                               'shown': '+0.0 dB', 'track_name': 'Bass', 'fx_name': 'Comp'}]}

        async def render_variant(label, first, last, guids_, project_id=None, variant=None, workspace=None,
                                 originals=None):
            self.log.append(('render', label))
            await server.same_project(project_id)
            if variant is not None and label == self.fail_apply:
                raise server.BridgeError('boom')
            warnings = ['baseline peaks at -90.0 dBFS'] if label == 'baseline' and self.silent else []
            applied = {'rows': [{'name': 'Gain', 'after': '-18.0 dB'}], 'failed': 0, 'unrestored': []} if variant else None
            return f'C:/x/{label}.wav', {'targets': [{'track': 'Bass'}], 'warnings': warnings, 'peak_dbfs': -3.0,
                                         'applied': applied, 'preroll_seconds': min(1.0, first)}

        async def apply_variant(workspace, originals, variant, *, label=None, project_id=None):
            name = variant.label if variant is not None else None
            self.log.append(('keep' if label else 'apply', name))
            if name is None and self.fail_restore:
                raise server.BridgeError('The REAPER bridge is stopped or busy')
            if name and name == self.fail_apply and not label:
                raise server.BridgeError('boom')
            return {'ok': True, 'rows': [{'name': 'Gain', 'after': '-18.0 dB'}] if name else [], 'failed': 0,
                    'unrestored': [], 'undo': label}

        async def run_analysis(files, baseline, noise):
            self.log.append(('analyze', [label for label, _ in files], noise))
            return analysis

        for name, fake in (('track_guids_for', guids), ('read_query', read_query), ('render_variant', render_variant),
                           ('apply_variant', apply_variant), ('run_analysis', run_analysis)):
            monkeypatch.setattr(server, name, fake)
        monkeypatch.setattr(server, 'prune_scratch', lambda: None)
        monkeypatch.setattr(server.bridge, 'status', lambda: {'project_id': self.project})

    def run(self, **kwargs):
        variants = [server.Variant(label=label, set=[server.VariantChange(track='Bass', fx=0, param='Gain',
                                                                          value='-18 dB')])
                    for label in kwargs.pop('labels', ('a', 'b'))]
        return json.loads(asyncio.run(server.compare_variants(variants, start_seconds=kwargs.pop('start', 2), end_seconds=6,
                                                              track_guids=['Bass'], **kwargs)))


def test_variants_render_baseline_each_variant_baseline_again_then_measure(monkeypatch):
    series = FakeSeries(monkeypatch)
    result = series.run()
    # Each variant is applied, rendered and undone in one request; the restore after the series is a safety net.
    assert series.log == [('render', 'baseline'), ('render', 'a'), ('render', 'b'),
                          ('apply', None), ('render', 'baseline_end'),
                          ('analyze', ['baseline', 'a', 'b', 'baseline_end'], 'baseline_end')]
    assert result['ok'] and result['restored'] == {'ok': True} and 'analysis' in result
    assert [f['label'] for f in result['files']] == ['baseline', 'a', 'b', 'baseline_end']


def test_a_failure_mid_series_still_restores_and_returns_what_was_rendered(monkeypatch):
    series = FakeSeries(monkeypatch, fail_apply='b')
    result = series.run()
    assert ('apply', None) in series.log and ('render', 'baseline_end') not in series.log
    assert not result['ok'] and 'boom' in result['error'] and result['restored']['ok']
    assert [f['label'] for f in result['files']] == ['baseline', 'a']


def test_a_failed_restore_says_what_the_settings_were(monkeypatch):
    series = FakeSeries(monkeypatch, fail_restore=True)
    result = series.run(keep='a')
    assert not result['ok'] and not result['restored']['ok'] and 'stopped or busy' in result['restored']['error']
    assert result['restored']['originals'] == [{'track_name': 'Bass', 'fx_name': 'Comp', 'name': 'Gain',
                                                'shown': '+0.0 dB', 'enabled': None}]
    assert ('keep', 'a') not in series.log and ('render', 'baseline_end') not in series.log


def test_keep_applies_the_variant_as_one_undo_step_after_the_series(monkeypatch):
    series = FakeSeries(monkeypatch)
    result = series.run(keep='b')
    assert series.log.index(('keep', 'b')) > series.log.index(('render', 'baseline_end'))
    assert result['ok'] and result['kept'] == {'label': 'b', 'ok': True, 'undo': 'Keep variant b'}


def test_variants_without_analysis_still_return_the_wavs(monkeypatch):
    series = FakeSeries(monkeypatch, analysis=(None, 'uv is not on PATH'))
    result = series.run()
    assert result['ok'] and result['analysis_error'] == 'uv is not on PATH' and len(result['files']) == 4


def test_variant_arguments_are_checked_before_anything_runs(monkeypatch):
    series = FakeSeries(monkeypatch)
    assert 'unique' in series.run(labels=('a', 'a'))['error']
    assert 'reserved' in series.run(labels=('baseline',))['error']
    assert 'keep names no variant' in series.run(keep='z')['error']
    long = json.loads(asyncio.run(server.compare_variants(
        [server.Variant(label='a', fx=[server.VariantSwitch(track='Bass', fx=0, enabled=False)])],
        start_seconds=0, end_seconds=60)))
    assert 'exceed' in long['error'] and series.log == []


def test_analysis_never_hangs_or_raises(monkeypatch, tmp_path):
    files = [('baseline', 'a.wav')]
    script = tmp_path / 'fake.py'

    def command(code):
        script.write_text(code, encoding='utf-8')
        monkeypatch.setattr(server, 'analysis_command', lambda *_: ([sys.executable, str(script)], None))

    command('import time; time.sleep(30)')
    monkeypatch.setattr(server, 'ANALYSIS_TIMEOUT_SECONDS', 1)
    assert 'was stopped' in asyncio.run(server.run_analysis(files, 'baseline', None))[1]
    command('import sys; sys.stderr.write("Traceback" + chr(10) + "ValueError: bad wav" + chr(10)); sys.exit(3)')
    assert 'exit 3' in asyncio.run(server.run_analysis(files, 'baseline', None))[1]
    command('print("not json")')
    assert 'did not print JSON' in asyncio.run(server.run_analysis(files, 'baseline', None))[1]
    command('import json; print(json.dumps({"ok": True, "files": []}))')
    assert asyncio.run(server.run_analysis(files, 'baseline', None)) == ({'ok': True, 'files': []}, None)
    monkeypatch.setattr(server, 'analysis_command', lambda *_: (['C:/no/such/uv.exe'], None))
    assert 'could not start' in asyncio.run(server.run_analysis(files, 'baseline', None))[1]


def test_analysis_command_needs_uv_and_the_script(monkeypatch, tmp_path):
    monkeypatch.setattr(server.shutil, 'which', lambda _: None)
    assert 'uv is not on PATH' in server.analysis_command([], 'baseline', None)[1]
    monkeypatch.setattr(server.shutil, 'which', lambda _: 'uv')
    monkeypatch.setattr(server, 'ANALYZE_SCRIPT', tmp_path / 'missing.py')
    assert 'not found' in server.analysis_command([], 'baseline', None)[1]


def test_render_warnings_reach_the_result(monkeypatch):
    series = FakeSeries(monkeypatch)
    series.silent = True
    assert series.run()['warnings'] == ['baseline peaks at -90.0 dBFS']
    series.silent = False
    assert 'pre-roll' in series.run(start=0.25)['warnings'][0]


def test_a_project_switch_stops_the_series(monkeypatch):
    series = FakeSeries(monkeypatch)
    original = server.same_project

    async def switch_after_first_variant(project_id):
        if ('render', 'a') in series.log:
            series.project = 'other'
        return await original(project_id)
    monkeypatch.setattr(server, 'same_project', switch_after_first_variant)
    result = series.run()
    assert not result['ok'] and 'Another project became active' in result['error']
    # Stopped at the render that found another project; the original settings are still put back.
    assert ('render', 'b') not in series.log and series.log[-1] == ('apply', None)
    assert [f['label'] for f in result['files']] == ['baseline']


def test_analysis_errors_of_any_kind_come_back_as_text(monkeypatch):
    async def broken(*_):
        raise RuntimeError('event loop closed')
    monkeypatch.setattr(server, 'analysis_process', broken)
    assert asyncio.run(server.run_analysis([], 'baseline', None)) == (None, 'analysis failed: RuntimeError: event loop closed')


def test_wav_drop_head_cuts_the_pre_roll_exactly(tmp_path):
    from autoreaper.wav import wav_drop_head
    x = np.arange(SR * 3, dtype=np.float32) / (SR * 3)
    path = tmp_path / 'r.wav'
    soundfile.write(str(path), np.stack([x, -x], 1), SR, subtype='FLOAT')
    info = wav_drop_head(path, SR)
    y, rate = soundfile.read(str(path), dtype='float32', always_2d=True)
    assert rate == SR and info['frames'] == SR * 2 and len(y) == SR * 2
    assert y[0, 0] == x[SR] and y[-1, 1] == -x[-1]
    assert wav_drop_head(path, 0)['frames'] == SR * 2
