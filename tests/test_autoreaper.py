import asyncio
import importlib.util
import json
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile

from autoreaper import bridge as bridge_module, server
from autoreaper.wav import wav_info

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
    with pytest.raises(ValueError):
        server.coerce_track_guids(['Kick'])


def test_summarize_grid_maps_bars_to_starts():
    grid = [{'bar': 117, 'start_seconds': 210.909090909, 'end_seconds': 212.72, 'tempo': 132.0,
             'numerator': 4, 'denominator': 4},
            {'bar': 118, 'start_seconds': 212.72727, 'end_seconds': 214.5, 'tempo': 132.0,
             'numerator': 4, 'denominator': 4}]
    assert server.summarize_grid(grid) == {'bars': {'117': 210.9091, '118': 212.7273}, 'tempo_bpm': 132.0,
                                           'meter': '4/4'}


def test_output_moves_large_results_to_a_file(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTOREAPER_HOME', str(tmp_path))
    assert json.loads(server.output({'ok': True, 'track': 'Bęben'}, 't')) == {'ok': True, 'track': 'Bęben'}
    summary = json.loads(server.output({'ok': True, 'big': 'x' * (server.MAX_INLINE_CHARS + 1)}, 'inspect'))
    assert summary['ok'] is True and 'big' not in summary and summary['keys'] == ['ok', 'big']
    assert json.loads(open(summary['result_file'], encoding='utf-8').read())['big'].startswith('xxx')


def test_server_lists_its_tools_with_read_only_hints():
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    assert set(tools) == {'reaper_status', 'install_bridge', 'inspect_project', 'inspect_signal_flow',
                          'search_installed_fx', 'reaper_eval', 'read_receipt', 'reaper_eval_write',
                          'capture'}
    read_only = {name for name, tool in tools.items() if tool.annotations and tool.annotations.readOnlyHint}
    assert read_only == {'reaper_status', 'inspect_project', 'inspect_signal_flow', 'search_installed_fx',
                         'reaper_eval', 'read_receipt'}
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
