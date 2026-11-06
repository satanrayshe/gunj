"""Load an audio file as mono float32 at the analysis rate (1 kHz).

Formats that libsndfile reads (WAV, FLAC, OGG, ...) are read with soundfile
in blocks, mixed to mono and resampled block by block, so a long 48 kHz file
never sits in memory at full rate. Anything else (m4a, webm, mp4, opus, ...)
is decoded by ffmpeg, which must be on PATH, straight to mono at the target
rate.

Block resampling: each block is resampled with scipy's polyphase filter
together with enough neighbouring samples on both sides that the result
matches resampling the whole file in one go (checked in the tests).
"""
from __future__ import annotations

import shutil
import subprocess
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from .extract import ANALYSIS_SR


def load_audio(path, target_sr: int = ANALYSIS_SR, block_s: float = 60.0,
               prefer_ffmpeg: bool = False):
    """Return (mono float32 samples, sample rate).

    The rate is ``target_sr`` unless the file's own rate is lower, in which
    case the file is returned at its own rate.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    if not prefer_ffmpeg:
        try:
            info = sf.info(str(path))
        except Exception:
            info = None
        if info is not None:
            return _load_soundfile(path, info.samplerate, target_sr, block_s)
    return _load_ffmpeg(path, target_sr)


def _context_blocks(up: int, down: int) -> int:
    # resample_poly's default filter has half-length 10 * max(up, down) taps at
    # the upsampled rate, i.e. 10 * max(up, down) / up input samples.
    half_in = 10 * max(up, down) / up
    return int(np.ceil((half_in + down) / down))


def _load_soundfile(path: Path, sr_in: int, target_sr: int, block_s: float):
    with sf.SoundFile(str(path)) as f:
        n = f.frames
        if sr_in <= target_sr:
            data = f.read(dtype="float32", always_2d=True)
            return data.mean(axis=1).astype(np.float32), int(sr_in)
        g = gcd(int(target_sr), int(sr_in))
        up, down = int(target_sr) // g, int(sr_in) // g
        p = _context_blocks(up, down)
        ctx = p * down
        m = max(1, int(round(block_s * sr_in / down)))
        step_in = m * down
        n_out = int(np.ceil(n * up / down))
        out = np.empty(n_out, dtype=np.float32)
        w = 0
        for s in range(0, n, step_in):
            a, b = s - ctx, s + step_in + ctx
            ra, rb = max(a, 0), min(b, n)
            f.seek(ra)
            chunk = f.read(rb - ra, dtype="float32", always_2d=True).mean(axis=1)
            seg = np.zeros(b - a, dtype=np.float64)
            seg[ra - a:ra - a + chunk.shape[0]] = chunk
            y = resample_poly(seg, up, down)
            y = y[p * up:p * up + m * up]
            take = min(y.shape[0], n_out - w)
            out[w:w + take] = y[:take]
            w += take
        return out[:w], int(target_sr)


def _load_ffmpeg(path: Path, target_sr: int):
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise RuntimeError(f"cannot read {path.name} with soundfile and ffmpeg is not on PATH")
    cmd = [exe, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path),
           "-vn", "-ac", "1", "-ar", str(int(target_sr)), "-f", "f32le",
           "-acodec", "pcm_f32le", "pipe:1"]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed on {path.name}: {proc.stderr.decode(errors='replace')[-500:]}")
    return np.frombuffer(proc.stdout, dtype="<f4").astype(np.float32), int(target_sr)
