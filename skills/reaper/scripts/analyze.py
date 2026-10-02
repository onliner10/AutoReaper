# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy>=1.26", "soundfile>=0.12", "Pillow>=10"]
# ///
"""Measure a WAV from AutoReaper's capture: levels, per-bar band table, before/after differences
and an optional spectrogram image.

    uv run --script analyze.py WAV [--compare BEFORE_WAV] [--spectrogram] [options]

Prints one JSON line (levels, bar grid source, files written), then text tables:
- per-bar band energy (sub 20-60 Hz ... air 10-20 kHz, dBFS), total RMS, spectral centroid, side/mid;
- with --compare: the same table for WAV minus BEFORE_WAV, bar by bar (positive = louder in WAV);
- with --steps N: the same per position in the bar (N parts, e.g. 16 sixteenths), averaged over the bars;
- with --bars A-B: only those bars.
The bar grid comes from the capture's .json sidecar (or --bar-grid). Levels are dBFS of the
original float samples: a full-scale sine reads -3 dB RMS in the table and 0 dB on the spectrogram.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

BANDS = (('sub', 20, 60), ('low', 60, 150), ('lowmid', 150, 500), ('mid', 500, 2000),
         ('highmid', 2000, 5000), ('high', 5000, 10000), ('air', 10000, 20000))
FLOOR_DB = -120.0
FREQ_TICKS = (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000)
LOW_TICKS = (20, 30, 40, 50, 60, 80, 100, 150, 200, 300, 500)
# Inferno-like control points, dark (quiet) to bright (loud).
_CMAP_POINTS = ((0.0, (0, 0, 4)), (0.13, (31, 12, 72)), (0.25, (85, 15, 109)), (0.38, (136, 34, 106)),
                (0.5, (186, 54, 85)), (0.63, (227, 89, 51)), (0.75, (249, 140, 10)),
                (0.88, (249, 201, 50)), (1.0, (252, 255, 164)))


def db(power):
    """10*log10 of a mean-square value, floored for silence."""
    return max(FLOOR_DB, 10 * math.log10(power)) if power > 0 else FLOOR_DB


def as_stereo(samples):
    samples = np.asarray(samples, dtype=np.float64)
    if samples.ndim == 1:
        samples = samples[:, None]
    if samples.shape[1] == 1:
        samples = np.repeat(samples, 2, axis=1)
    return samples[:, :2]


def mid_side(samples):
    stereo = as_stereo(samples)
    return (stereo[:, 0] + stereo[:, 1]) / 2, (stereo[:, 0] - stereo[:, 1]) / 2


def levels(samples):
    """Sample peak and RMS per channel, dBFS (RMS of a full-scale sine is -3 dB)."""
    stereo = as_stereo(samples)
    rows = []
    for channel in range(2):
        x = stereo[:, channel]
        peak = float(np.max(np.abs(x))) if len(x) else 0.0
        rows.append({'peak_dbfs': round(20 * math.log10(peak), 2) if peak > 0 else FLOOR_DB,
                     'rms_dbfs': round(db(float(np.mean(x * x))) if len(x) else FLOOR_DB, 2)})
    total_peak = max(r['peak_dbfs'] for r in rows)
    total_rms = db(float(np.mean(stereo * stereo))) if len(stereo) else FLOOR_DB
    return {'peak_dbfs': total_peak, 'rms_dbfs': round(total_rms, 2), 'left': rows[0], 'right': rows[1]}


def power_spectrum(x, sample_rate):
    """One-sided spectrum whose bins sum to the mean square of x (Parseval)."""
    n = len(x)
    spectrum = np.fft.rfft(x)
    power = (np.abs(spectrum) ** 2) / (n * n)
    if n % 2 == 0:
        power[1:-1] *= 2
    else:
        power[1:] *= 2
    return np.fft.rfftfreq(n, 1 / sample_rate), power


def segment_powers(samples, sample_rate):
    """Linear mean-square values of one stretch of audio: per band, total, centroid weights, mid and side."""
    stereo = as_stereo(samples)
    freqs, left = power_spectrum(stereo[:, 0], sample_rate)
    _, right = power_spectrum(stereo[:, 1], sample_rate)
    power = (left + right) / 2  # mean power over the two channels
    powers = {name: float(power[(freqs >= low) & (freqs < high)].sum()) for name, low, high in BANDS}
    audible = (freqs >= 20) & (freqs <= 20000)
    mid, side = mid_side(stereo)
    powers.update(total=float(np.mean(stereo * stereo)), weight=float(power[audible].sum()),
                  weighted_hz=float((freqs[audible] * power[audible]).sum()),
                  mid_channel=float(np.mean(mid * mid)), side_channel=float(np.mean(side * side)))
    return powers


def powers_row(powers):
    """Band energies (dB), total RMS, centroid and side/mid from segment_powers values."""
    row = {name: round(db(powers[name]), 1) for name, _, _ in BANDS}
    row['total'] = round(db(powers['total']), 1)
    row['centroid_hz'] = round(powers['weighted_hz'] / powers['weight']) if powers['weight'] > 0 else 0
    mid_power, side_power = powers['mid_channel'], powers['side_channel']
    if side_power <= 0:
        row['side_mid_db'] = FLOOR_DB
    elif mid_power <= 0:
        row['side_mid_db'] = -FLOOR_DB
    else:
        row['side_mid_db'] = round(max(FLOOR_DB, 10 * math.log10(side_power / mid_power)), 1)
    return row


def segment_row(samples, sample_rate):
    """Band energies, total RMS, centroid and side/mid for one stretch of audio."""
    return powers_row(segment_powers(samples, sample_rate))


def bar_segments(bar_grid, wav_start_seconds, duration_seconds, sample_rate, total_samples):
    """(bar, first_sample, end_sample) per bar, clipped to the recording."""
    rows = []
    for bar in bar_grid:
        start = max(0.0, bar['start_seconds'] - wav_start_seconds)
        end = min(duration_seconds, bar['end_seconds'] - wav_start_seconds)
        if end <= start:
            continue
        first = int(round(start * sample_rate))
        last = min(total_samples, int(round(end * sample_rate)))
        if last > first:
            rows.append((bar['bar'], first, last))
    return rows


def bar_table(samples, sample_rate, bar_grid, wav_start_seconds):
    samples = as_stereo(samples)
    duration = len(samples) / sample_rate
    rows = []
    for bar, first, last in bar_segments(bar_grid, wav_start_seconds, duration, sample_rate, len(samples)):
        rows.append({'bar': bar, **segment_row(samples[first:last], sample_rate)})
    return rows


def step_label(step, steps):
    """1-based step in a bar as beat.subdivision when steps divide into 4 beats ("2.3"), else the number."""
    if steps % 4 == 0 and steps > 4:
        per_beat = steps // 4
        return f'{(step - 1) // per_beat + 1}.{(step - 1) % per_beat + 1}'
    return str(step)


def step_table(samples, sample_rate, bar_grid, wav_start_seconds, steps):
    """Per position in the bar (each bar cut into `steps` equal parts): energy averaged over the bars.

    Shows rhythmic detail a per-bar table hides: what plays on the beat versus off it, how a
    ducker or a hat pattern shapes each sixteenth. Only bars wholly inside the recording count,
    so every step averages the same number of slices.
    """
    samples = as_stereo(samples)
    duration = len(samples) / sample_rate
    whole = [bar for bar in bar_grid if bar['start_seconds'] - wav_start_seconds >= -1e-6
             and bar['end_seconds'] - wav_start_seconds <= duration + 1e-6]
    if not whole:
        return []
    sums = [None] * steps
    for bar in whole:
        edges = np.linspace(bar['start_seconds'] - wav_start_seconds, bar['end_seconds'] - wav_start_seconds, steps + 1)
        for step in range(steps):
            first = max(0, int(round(edges[step] * sample_rate)))
            last = min(len(samples), int(round(edges[step + 1] * sample_rate)))
            if last - first < 16:
                raise ValueError('--steps is too fine for this tempo and sample rate')
            powers = segment_powers(samples[first:last], sample_rate)
            sums[step] = powers if sums[step] is None else {k: sums[step][k] + v for k, v in powers.items()}
    return [{'bar': step_label(step + 1, steps), **powers_row({k: v / len(whole) for k, v in sums[step].items()})}
            for step in range(steps)]


def select_bars(grid, bars):
    """The grid rows within an inclusive "A-B" (or single "A") bar range."""
    if not bars:
        return grid
    first, _, last = bars.partition('-')
    first, last = int(first), int(last or first)
    return [row for row in grid if first <= row['bar'] <= last]


TABLE_COLUMNS = ('sub', 'low', 'lowmid', 'mid', 'highmid', 'high', 'air', 'total', 'centroid_hz', 'side_mid_db')


def format_table(rows, unit='bar', note=''):
    """One line per bar (or step); the header names the units."""
    header = f'{unit:<5} sub20-60 low60-150 lm150-500 mid.5-2k hm2-5k hi5-10k air10-20k | totalRMS centroid S/M'
    lines = [f'# per-{unit} energy dBFS (mean of L/R power; total = RMS, full-scale sine = -3); '
             f'centroid Hz over 20-20k; S/M = side/mid power dB{note}', header]
    for row in rows:
        bands = ' '.join(f"{row[name]:>{width}.1f}" for name, width in zip(
            ('sub', 'low', 'lowmid', 'mid', 'highmid', 'high', 'air'), (8, 9, 9, 8, 6, 7, 9)))
        lines.append(f"{str(row['bar']):<5} {bands} | {row['total']:>8.1f} {row['centroid_hz']:>8d} "
                     f"{row['side_mid_db']:>5.1f}")
    return '\n'.join(lines)


def normalize_bar_grid(grid, duration_seconds=None, wav_start_seconds=0.0):
    """Bar grid as [{bar, start_seconds, end_seconds}] from that list or a {bar: start_seconds} map.

    A map's last bar ends at the end of the recording (needs duration_seconds).
    """
    if not grid:
        return []
    if isinstance(grid, dict):
        starts = sorted((int(bar), float(start)) for bar, start in grid.items())
        rows = []
        for i, (bar, start) in enumerate(starts):
            if i + 1 < len(starts):
                end = starts[i + 1][1]
            elif duration_seconds is not None:
                end = wav_start_seconds + duration_seconds
            else:
                raise ValueError('A {bar: start_seconds} grid needs the recording length for its last bar.')
            rows.append({'bar': bar, 'start_seconds': start, 'end_seconds': end})
        grid = rows
    if not isinstance(grid, list):
        raise ValueError('bar_grid must be a list of {bar, start_seconds, end_seconds} or a {bar: start_seconds} map.')
    rows = []
    for row in grid:
        if not isinstance(row, dict) or not all(k in row for k in ('bar', 'start_seconds', 'end_seconds')):
            raise ValueError('Each bar_grid row needs bar, start_seconds and end_seconds.')
        rows.append({'bar': int(row['bar']), 'start_seconds': float(row['start_seconds']),
                     'end_seconds': float(row['end_seconds'])})
    if any(b['end_seconds'] <= b['start_seconds'] for b in rows) or             any(b['start_seconds'] < a['start_seconds'] for a, b in zip(rows, rows[1:])):
        raise ValueError('bar_grid rows must be increasing in time with positive length.')
    return rows


# ---------------------------------------------------------------- spectrogram

def colormap(values):
    """values in 0..1 -> uint8 RGB."""
    positions = np.array([p for p, _ in _CMAP_POINTS])
    colours = np.array([c for _, c in _CMAP_POINTS], dtype=np.float64)
    values = np.clip(values, 0, 1)
    return np.stack([np.interp(values, positions, colours[:, i]) for i in range(3)], axis=-1).astype(np.uint8)


def log_frequency_rows(low_hz, high_hz, height):
    """Row edges and centres, top row = high_hz."""
    edges = np.geomspace(high_hz, low_hz, height + 1)
    return edges, np.sqrt(edges[:-1] * edges[1:])


def frequency_to_y(freq, low_hz, high_hz, height):
    """Pixel row (0 = top) of a frequency on a log axis."""
    return (math.log(high_hz) - math.log(freq)) / (math.log(high_hz) - math.log(low_hz)) * height


def stft_power(x, sample_rate, columns, n_fft):
    """Hann STFT power (amplitude squared; full-scale sine peaks at 1 = 0 dB) at `columns` evenly spaced frame centres."""
    window = np.hanning(n_fft).astype(np.float32)
    scale = 2.0 / window.sum()
    # Reflected edges: zero padding would draw a broadband click at both ends.
    x = np.asarray(x, dtype=np.float32)
    pad = min(n_fft // 2, max(0, len(x) - 1))
    padded = np.pad(x, (pad, pad), mode='reflect') if pad else x
    padded = np.pad(padded, (n_fft // 2 - pad, n_fft // 2 - pad))
    centres = ((np.arange(columns) + 0.5) / columns * len(x)).astype(np.int64)
    out = np.empty((n_fft // 2 + 1, columns), dtype=np.float32)
    chunk = max(1, int(4e7 // n_fft))
    for begin in range(0, columns, chunk):
        idx = centres[begin:begin + chunk, None] + np.arange(n_fft)[None, :]
        frames = padded[idx] * window
        magnitude = np.abs(np.fft.rfft(frames, axis=1)) * scale
        out[:, begin:begin + chunk] = magnitude.T ** 2
    return np.fft.rfftfreq(n_fft, 1 / sample_rate), out  # power (amplitude^2)


def resample_log(freqs, power, low_hz, high_hz, height):
    """Average power into log-spaced rows (top = high); interpolate rows narrower than a bin."""
    edges, centres = log_frequency_rows(low_hz, high_hz, height)
    cumulative = np.vstack([np.zeros((1, power.shape[1])), np.cumsum(power, axis=0)])
    bin_width = freqs[1] - freqs[0]
    rows = np.empty((height, power.shape[1]), dtype=np.float64)
    for r in range(height):
        hi, lo = edges[r], edges[r + 1]
        a, b = int(np.ceil(lo / bin_width)), int(np.floor(hi / bin_width)) + 1
        b = min(b, power.shape[0])
        if b - a >= 1:
            rows[r] = (cumulative[b] - cumulative[a]) / (b - a)
        else:
            position = min(centres[r] / bin_width, power.shape[0] - 1.001)
            i = int(position)
            frac = position - i
            rows[r] = power[i] * (1 - frac) + power[i + 1] * frac
    return 10 * np.log10(np.maximum(rows, 1e-20))


def time_marks(bar_grid, wav_start_seconds, duration_seconds):
    """(seconds from WAV start, label, heavy) for vertical lines; bars when known, else seconds."""
    marks = []
    if bar_grid:
        for bar in bar_grid:
            t = bar['start_seconds'] - wav_start_seconds
            if -1e-6 <= t <= duration_seconds + 1e-6:
                marks.append((max(0.0, t), str(bar['bar']), bar['bar'] % 4 == 1))
        last = bar_grid[-1]
        end = last['end_seconds'] - wav_start_seconds
        if abs(end - duration_seconds) < 1e-3 and (not marks or marks[-1][0] < end - 1e-6):
            marks.append((duration_seconds, str(last['bar'] + 1), (last['bar'] + 1) % 4 == 1))
        return marks
    step = next(s for s in (1, 2, 5, 10, 15, 30, 60, 120, 300) if duration_seconds / s <= 40)
    t = 0.0
    while t <= duration_seconds + 1e-9:
        marks.append((t, f'{t:g}s', round(t) % (step * 5) == 0))
        t += step
    return marks


def _font(size, bold=False):
    for name in (('arialbd.ttf' if bold else 'arial.ttf'), 'DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _tick_label(freq):
    return f'{freq // 1000:g}k' if freq >= 1000 and freq % 1000 == 0 else (f'{freq / 1000:g}k' if freq >= 1000 else str(freq))


def render_spectrogram(samples, sample_rate, path, *, title, bar_grid=None, wav_start_seconds=0.0,
                       panels=('full', 'lowband'), dynamic_range_db=80.0, top_dbfs=None, width=1600):
    """Write a PNG with one row per panel; returns a receipt with the dB scale used."""
    stereo = as_stereo(samples)
    mid, side = mid_side(stereo)
    duration = len(stereo) / sample_rate
    spec = {'full': ('Mid (L+R)/2, 20 Hz-20 kHz', mid, 20.0, 20000.0, 4096, 470, FREQ_TICKS),
            'lowband': ('Mid (L+R)/2, low band 20-500 Hz', mid, 20.0, 500.0, 16384, 260, LOW_TICKS),
            'side': ('Side (L-R)/2, 20 Hz-20 kHz, same dB scale', side, 20.0, 20000.0, 4096, 300, FREQ_TICKS)}
    unknown = [p for p in panels if p not in spec]
    if unknown or not panels:
        raise ValueError(f'panels must be from {list(spec)}; got {list(panels)}')
    left, right, top, gap, bottom = 70, 130, 46, 34, 40
    plot_w = width - left - right
    images = []
    for name in panels:
        label, signal, low_hz, high_hz, n_fft, height, ticks = spec[name]
        freqs, power = stft_power(signal, sample_rate, plot_w, n_fft)
        images.append((name, label, low_hz, high_hz, height, ticks, resample_log(freqs, power, low_hz, high_hz, height)))
    # One dB scale for every panel, from the loudest panel content.
    if top_dbfs is None:
        ceiling = max(float(np.percentile(m, 99.9)) for *_, m in images)
        top_db = math.ceil(ceiling / 5) * 5
    else:
        top_db = float(top_dbfs)
    bottom_db = top_db - dynamic_range_db
    total_h = top + sum(img[4] for img in images) + gap * (len(images) - 1) + bottom
    canvas = Image.new('RGB', (width, total_h), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    font, small, bold, small_bold = _font(14), _font(12), _font(17, True), _font(12, True)
    draw.text((left, 12), title, fill=(0, 0, 0), font=bold)
    marks = time_marks(bar_grid, wav_start_seconds, duration)
    label_every = 1 if plot_w / max(1, len(marks)) >= 22 else 4
    y = top
    for name, label, low_hz, high_hz, height, ticks, matrix in images:
        rgb = colormap((matrix - bottom_db) / dynamic_range_db)
        canvas.paste(Image.fromarray(rgb, 'RGB'), (left, y))
        overlay = Image.new('RGBA', (plot_w, height), (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        for t, text, heavy in marks:
            x = min(plot_w - 1, int(round(t / duration * plot_w)))
            odraw.line([(x, 0), (x, height)], fill=(255, 255, 255, 200 if heavy else 90), width=3 if heavy else 1)
        for freq in ticks:
            if low_hz <= freq <= high_hz:
                fy = int(round(frequency_to_y(freq, low_hz, high_hz, height)))
                odraw.line([(0, fy), (plot_w, fy)], fill=(255, 255, 255, 40), width=1)
        canvas.paste(overlay, (left, y), overlay)
        draw.rectangle([left - 1, y - 1, left + plot_w, y + height], outline=(0, 0, 0))
        draw.text((left + 6, y + 4), label, fill=(255, 255, 255), font=font)
        for freq in ticks:
            if low_hz <= freq <= high_hz:
                fy = y + int(round(frequency_to_y(freq, low_hz, high_hz, height)))
                draw.line([(left - 6, fy), (left - 1, fy)], fill=(0, 0, 0))
                text = _tick_label(freq)
                tw = draw.textlength(text, font=small)
                draw.text((left - 9 - tw, fy - 7), text, fill=(0, 0, 0), font=small)
        for t, text, heavy in marks:
            if label_every > 1 and not heavy:
                continue
            x = left + min(plot_w - 1, int(round(t / duration * plot_w)))
            draw.line([(x, y + height), (x, y + height + (6 if heavy else 3))], fill=(0, 0, 0))
            tw = draw.textlength(text, font=small)
            draw.text((x - tw / 2, y + height + 7), text, fill=(0, 0, 0), font=small_bold if heavy else small)
        y += height + gap
    axis = 'bar number (heavy line every 4 bars: 1, 5, 9, ...)' if bar_grid else 'seconds from start of recording'
    draw.text((left + plot_w / 2 - draw.textlength(axis, font=font) / 2, total_h - 18), axis, fill=(0, 0, 0), font=font)
    draw.text((8, top), 'Hz', fill=(0, 0, 0), font=font)
    # Colour bar.
    bar_x, bar_top, bar_h = left + plot_w + 22, top, min(400, total_h - top - bottom)
    gradient = colormap(np.linspace(1, 0, bar_h))[:, None, :].repeat(18, axis=1)
    canvas.paste(Image.fromarray(gradient, 'RGB'), (bar_x, bar_top))
    draw.rectangle([bar_x - 1, bar_top - 1, bar_x + 18, bar_top + bar_h], outline=(0, 0, 0))
    for level in range(int(top_db), int(bottom_db) - 1, -10):
        ly = bar_top + int(round((top_db - level) / dynamic_range_db * (bar_h - 1)))
        draw.line([(bar_x + 18, ly), (bar_x + 23, ly)], fill=(0, 0, 0))
        draw.text((bar_x + 26, ly - 7), f'{level}', fill=(0, 0, 0), font=small)
    draw.text((bar_x - 4, bar_top + bar_h + 6), 'dBFS', fill=(0, 0, 0), font=font)
    draw.text((bar_x - 4, bar_top + bar_h + 24), '(sine 0 dB)', fill=(0, 0, 0), font=small)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path, optimize=True)
    return {'png': str(path), 'size': [width, total_h], 'panels': list(panels),
            'db_scale': {'top_dbfs': top_db, 'top_source': 'fixed' if top_dbfs is not None else 'auto (99.9th percentile)', 'bottom_dbfs': bottom_db,
                         'reference': 'Hann-windowed STFT magnitude, full-scale sine = 0 dBFS; mid = (L+R)/2'},
            'fft_sizes': {name: spec[name][4] for name in panels},
            'x_axis': axis}


# ---------------------------------------------------------------- command line

DIFF_COLUMNS = ('sub', 'low', 'lowmid', 'mid', 'highmid', 'high', 'air', 'total', 'side_mid_db')


def load(wav, bar_grid=None, wav_start_seconds=None):
    """(samples, sample_rate, grid, wav_start_seconds, sidecar) of a WAV and its capture sidecar."""
    import soundfile
    path = Path(wav)
    samples, sample_rate = soundfile.read(str(path), dtype='float64', always_2d=True)
    sidecar_path = path.with_suffix('.json')
    sidecar = json.loads(sidecar_path.read_text(encoding='utf-8')) if sidecar_path.exists() else {}
    start = float(wav_start_seconds if wav_start_seconds is not None else sidecar.get('wav_start_seconds', 0.0))
    grid = normalize_bar_grid(bar_grid if bar_grid is not None else sidecar.get('bar_grid'),
                              len(samples) / sample_rate, start)
    return samples, sample_rate, grid, start, sidecar


def difference_rows(after, before):
    """after - before per band, for the bars both tables contain (matched by bar number)."""
    earlier = {row['bar']: row for row in before}
    rows = []
    for row in after:
        if row['bar'] in earlier:
            old = earlier[row['bar']]
            rows.append({'bar': row['bar'], **{k: round(row[k] - old[k], 1) for k in DIFF_COLUMNS},
                         'centroid_hz': row['centroid_hz'] - old['centroid_hz']})
    return rows


def format_difference_table(rows, unit='bar'):
    header = f'{unit:<5} sub20-60 low60-150 lm150-500 mid.5-2k hm2-5k hi5-10k air10-20k | totalRMS centroid S/M'
    lines = [f'# difference per {unit} in dB, WAV minus --compare (positive = more energy in WAV); centroid in Hz',
             header]
    for row in rows:
        bands = ' '.join(f"{row[name]:>+{width}.1f}" for name, width in zip(
            ('sub', 'low', 'lowmid', 'mid', 'highmid', 'high', 'air'), (8, 9, 9, 8, 6, 7, 9)))
        lines.append(f"{str(row['bar']):<5} {bands} | {row['total']:>+8.1f} {row['centroid_hz']:>+8d} "
                     f"{row['side_mid_db']:>+5.1f}")
    return '\n'.join(lines)


def spectrogram_title(wav, grid, duration, sidecar):
    span = f'bars {grid[0]["bar"]}-{grid[-1]["bar"]} ({len(grid)} bars)' if grid else f'{duration:.2f} s'
    rng = sidecar.get('range') or {}
    when = f", project {rng['start_seconds']:.2f}-{rng['end_seconds']:.2f} s" if 'start_seconds' in rng else ''
    isolated = ', '.join(t.get('track', '') for t in sidecar.get('isolated_tracks') or [])
    return f'{Path(wav).stem}: {span}{when}' + (f' | solo: {isolated}' if isolated else '')


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('wav')
    parser.add_argument('--compare', metavar='BEFORE_WAV', help='print WAV minus this WAV, bar by bar')
    parser.add_argument('--spectrogram', action='store_true', help='also write a PNG next to the WAV')
    parser.add_argument('--panels', default='full,lowband', help='comma list of full, lowband, side')
    parser.add_argument('--top-dbfs', type=float, help='fixed top of the colour scale, so two images compare')
    parser.add_argument('--dynamic-range-db', type=float, default=80.0)
    parser.add_argument('--bar-grid', help='JSON {bar: start_seconds} when the WAV has no sidecar')
    parser.add_argument('--wav-start', type=float, help='project time of the first sample, with --bar-grid')
    parser.add_argument('--no-table', action='store_true', help='skip the per-bar table')
    parser.add_argument('--steps', type=int, help='also a table per position in the bar (16 = sixteenths), '
                                                  'averaged over the bars; with --compare, its difference too')
    parser.add_argument('--bars', help='only these bars, inclusive: "57-64" or "57"')
    args = parser.parse_args(argv)
    if args.steps is not None and not 2 <= args.steps <= 64:
        parser.error('--steps must be in 2..64')
    if args.bars and not all(part.isdigit() for part in args.bars.split('-', 1)):
        parser.error('--bars takes "A-B" or "A" (bar numbers)')
    args.panels = tuple(p.strip() for p in args.panels.split(',') if p.strip())
    if not args.panels or any(p not in ('full', 'lowband', 'side') for p in args.panels) \
            or len(set(args.panels)) != len(args.panels):
        parser.error('--panels takes a comma list of full, lowband, side')
    if not 20 <= args.dynamic_range_db <= 160:
        parser.error('--dynamic-range-db must be in 20..160')
    if args.top_dbfs is not None and not -120 <= args.top_dbfs <= 30:
        parser.error('--top-dbfs must be in -120..30')
    args.bar_grid = json.loads(args.bar_grid) if args.bar_grid else None
    return args


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding='utf-8')
    samples, sample_rate, grid, start, sidecar = load(args.wav, args.bar_grid, args.wav_start)
    grid = select_bars(grid, args.bars)
    duration = len(samples) / sample_rate
    summary = {'ok': True, 'wav': str(Path(args.wav)), 'duration_seconds': round(duration, 6),
               'sample_rate': sample_rate, 'levels': levels(samples),
               **{k: sidecar[k] for k in ('range', 'scope', 'isolated_tracks', 'tempo_bpm', 'meter') if k in sidecar},
               'bar_grid_source': 'arguments' if args.bar_grid is not None else
               ('sidecar' if sidecar.get('bar_grid') else 'none')}
    tables = []
    rows = bar_table(samples, sample_rate, grid, start) if grid else []
    if not args.no_table:
        tables.append(format_table(rows) if grid else '# no bar grid, so no per-bar table')
    steps = step_table(samples, sample_rate, grid, start, args.steps) if args.steps and grid else []
    if args.steps:
        span = f'; average of bars {grid[0]["bar"]}-{grid[-1]["bar"]}' if grid else ''
        tables.append(format_table(steps, 'step', span) if steps else '# no whole bars, so no per-step table')
    if args.compare:
        before, before_rate, before_grid, before_start, _ = load(args.compare)
        before_grid = select_bars(before_grid, args.bars)
        summary['compare'] = {'wav': str(Path(args.compare)), 'levels': levels(before)}
        summary['level_difference_db'] = {k: round(summary['levels'][k] - summary['compare']['levels'][k], 2)
                                          for k in ('peak_dbfs', 'rms_dbfs')}
        if grid and before_grid:
            diff = difference_rows(rows, bar_table(before, before_rate, before_grid, before_start))
            summary['compared_bars'] = len(diff)
        else:
            # Without a bar grid on both sides, compare the whole recordings.
            diff = difference_rows([{'bar': 'all', **segment_row(samples, sample_rate)}],
                                   [{'bar': 'all', **segment_row(before, before_rate)}])
        tables.append(format_difference_table(diff))
        if steps and before_grid:
            before_steps = step_table(before, before_rate, before_grid, before_start, args.steps)
            tables.append(format_difference_table(difference_rows(steps, before_steps), 'step'))
    if args.spectrogram:
        png = Path(args.wav).with_name(Path(args.wav).stem + '-' + '-'.join(args.panels) + '.png')
        summary['spectrogram'] = render_spectrogram(
            samples, sample_rate, png, title=spectrogram_title(args.wav, grid, duration, sidecar), bar_grid=grid,
            wav_start_seconds=start, panels=args.panels, dynamic_range_db=args.dynamic_range_db,
            top_dbfs=args.top_dbfs)
    print(json.dumps(summary, ensure_ascii=False))
    for table in tables:
        print(table)
    return 0


if __name__ == '__main__':
    sys.exit(main())
