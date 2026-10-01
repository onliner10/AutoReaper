"""Measurements and spectrogram images for captures.

Pure numpy + Pillow (+ soundfile for reading), no REAPER, so tests run
without it. Levels are dBFS of the original float
samples; a full-scale sine reads 0 dB on the spectrogram and -3 dB RMS in the
table.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

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


def segment_row(samples, sample_rate):
    """Band energies, total RMS, centroid and side/mid for one stretch of audio."""
    stereo = as_stereo(samples)
    freqs, left = power_spectrum(stereo[:, 0], sample_rate)
    _, right = power_spectrum(stereo[:, 1], sample_rate)
    power = (left + right) / 2  # mean power over the two channels
    row = {}
    for name, low, high in BANDS:
        row[name] = round(db(float(power[(freqs >= low) & (freqs < high)].sum())), 1)
    row['total'] = round(db(float(np.mean(stereo * stereo))), 1)
    audible = (freqs >= 20) & (freqs <= 20000)
    weight = float(power[audible].sum())
    row['centroid_hz'] = round(float((freqs[audible] * power[audible]).sum() / weight)) if weight > 0 else 0
    mid, side = mid_side(stereo)
    mid_power, side_power = float(np.mean(mid * mid)), float(np.mean(side * side))
    if side_power <= 0:
        row['side_mid_db'] = FLOOR_DB
    elif mid_power <= 0:
        row['side_mid_db'] = -FLOOR_DB
    else:
        row['side_mid_db'] = round(max(FLOOR_DB, 10 * math.log10(side_power / mid_power)), 1)
    return row


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


TABLE_COLUMNS = ('sub', 'low', 'lowmid', 'mid', 'highmid', 'high', 'air', 'total', 'centroid_hz', 'side_mid_db')


def format_table(rows):
    """One line per bar; the header names the units."""
    header = ('bar   sub20-60 low60-150 lm150-500 mid.5-2k hm2-5k hi5-10k air10-20k | totalRMS centroid S/M')
    lines = ['# per-bar energy dBFS (mean of L/R power; total = RMS, full-scale sine = -3); '
             'centroid Hz over 20-20k; S/M = side/mid power dB', header]
    for row in rows:
        bands = ' '.join(f"{row[name]:>{width}.1f}" for name, width in zip(
            ('sub', 'low', 'lowmid', 'mid', 'highmid', 'high', 'air'), (8, 9, 9, 8, 6, 7, 9)))
        lines.append(f"{row['bar']:<4} {bands} | {row['total']:>8.1f} {row['centroid_hz']:>8d} "
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
    from PIL import ImageFont
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
    from PIL import Image, ImageDraw
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
