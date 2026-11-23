"""Read a WAV header with the standard library (PCM, float and WAVE_FORMAT_EXTENSIBLE)."""
from __future__ import annotations

import array
import math
import struct
import sys
from pathlib import Path


def wav_info(path: Path) -> dict:
    """Sample rate, channels, bits, format tag and duration of a RIFF/WAVE (or RF64) file."""
    with open(path, 'rb') as f:
        riff, _, wave = struct.unpack('<4sI4s', f.read(12))
        if riff not in (b'RIFF', b'RF64') or wave != b'WAVE':
            raise ValueError(f'{path} is not a WAV file')
        fmt = None
        data_size = None
        large_data_size = None
        while True:
            header = f.read(8)
            if len(header) < 8:
                break
            chunk, size = struct.unpack('<4sI', header)
            if chunk == b'ds64':
                body = f.read(size)
                large_data_size = struct.unpack('<Q', body[8:16])[0]
            elif chunk == b'fmt ':
                body = f.read(size)
                tag, channels, sample_rate, _, block_align, bits = struct.unpack('<HHIIHH', body[:16])
                if tag == 0xFFFE and len(body) >= 26:
                    tag = struct.unpack('<H', body[24:26])[0]
                fmt = {'format_tag': tag, 'channels': channels, 'sample_rate': sample_rate,
                       'block_align': block_align, 'bits': bits}
            elif chunk == b'data':
                data_size = large_data_size if size == 0xFFFFFFFF and large_data_size is not None else size
                break
            else:
                f.seek(size + (size & 1), 1)
            if chunk in (b'ds64', b'fmt ') and size & 1:
                f.seek(1, 1)
    if fmt is None or data_size is None or not fmt['block_align'] or not fmt['sample_rate']:
        raise ValueError(f'{path} has no readable fmt/data chunk')
    frames = data_size // fmt['block_align']
    return {**fmt, 'frames': frames, 'duration_seconds': frames / fmt['sample_rate'],
            'sample_format': 'FLOAT' if fmt['format_tag'] == 3 else 'PCM' if fmt['format_tag'] == 1 else str(fmt['format_tag'])}


def wav_peak_dbfs(path: Path, chunk_frames: int = 1 << 16):
    """Sample peak in dBFS of a 32-bit float WAV (AutoReaper's captures), None for other formats."""
    info = wav_info(path)
    if info['sample_format'] != 'FLOAT' or info['bits'] != 32:
        return None
    with open(path, 'rb') as f:
        f.seek(12)
        while True:
            chunk, size = struct.unpack('<4sI', f.read(8))
            if chunk == b'data':
                break
            f.seek(size + (size & 1), 1)
        remaining, peak = info['frames'] * info['block_align'], 0.0
        while remaining > 0:
            data = f.read(min(remaining, chunk_frames * info['block_align']))
            if not data:
                break
            remaining -= len(data)
            values = array.array('f')
            values.frombytes(data[:len(data) - len(data) % 4])
            if sys.byteorder != 'little':
                values.byteswap()
            if values:
                peak = max(peak, max(values), -min(values))
    return 20 * math.log10(peak) if peak > 0 else -math.inf


def wav_drop_head(path: Path, frames: int) -> dict:
    """Cut the first frames off a RIFF WAV in place (a render's pre-roll); returns the new wav_info."""
    info = wav_info(path)
    frames = max(0, min(int(frames), info['frames']))
    if not frames:
        return info
    with open(path, 'rb') as f:
        riff, _, _ = struct.unpack('<4sI4s', f.read(12))
        if riff != b'RIFF':
            raise ValueError(f'{path}: only RIFF WAV files can be trimmed')
        fmt_chunk = None
        while True:
            chunk, size = struct.unpack('<4sI', f.read(8))
            if chunk == b'fmt ':
                fmt_chunk = f.read(size + (size & 1))[:size]
            elif chunk == b'data':
                break
            else:
                f.seek(size + (size & 1), 1)
        f.seek(frames * info['block_align'], 1)
        data = f.read((info['frames'] - frames) * info['block_align'])
    if fmt_chunk is None:
        raise ValueError(f'{path} has no fmt chunk')
    body = b'WAVE' + struct.pack('<4sI', b'fmt ', len(fmt_chunk)) + fmt_chunk + (b'\0' if len(fmt_chunk) & 1 else b'')
    body += struct.pack('<4sI', b'data', len(data)) + data + (b'\0' if len(data) & 1 else b'')
    temporary = path.with_suffix('.trim')
    temporary.write_bytes(b'RIFF' + struct.pack('<I', len(body)) + body)
    temporary.replace(path)
    return wav_info(path)
