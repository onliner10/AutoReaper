"""Read a WAV header with the standard library (PCM, float and WAVE_FORMAT_EXTENSIBLE)."""
from __future__ import annotations

import struct
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
