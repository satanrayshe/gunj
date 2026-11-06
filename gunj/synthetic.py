"""SYNTHETIC test signals. Nothing in this module is real grid or audio data.

Used by the tests and by experiments that must be labelled synthetic:
- ``synthetic_enf_walk``: a SYNTHETIC mean-reverting random walk around
  50 Hz at one value per second, standing in for a grid-frequency log.
- ``synthetic_hum``: SYNTHETIC audio whose mains hum follows a given 1-Hz
  frequency trace, with harmonics and white noise.
- ``cut_audio``: a SYNTHETIC edit that removes a stretch of samples.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import lfilter

SYNTHETIC = True  # marker for callers that record provenance


def synthetic_enf_walk(n: int, seed: int = 0, f0: float = 50.0,
                       step_hz: float = 0.002, revert: float = 0.001,
                       dtype=np.float64, out: np.ndarray | None = None,
                       chunk: int = 1_000_000) -> np.ndarray:
    """SYNTHETIC 1-Hz frequency series: an Ornstein-Uhlenbeck random walk.

    Each second the deviation from ``f0`` moves by a Gaussian step of
    ``step_hz`` and is pulled back toward zero by a fraction ``revert``.
    The long-run standard deviation is about step_hz / sqrt(2 * revert).
    Generated in chunks, so ``out`` may be a large np.memmap.
    """
    rng = np.random.default_rng(seed)
    if out is None:
        out = np.empty(n, dtype=dtype)
    dev = 0.0
    keep = 1.0 - revert
    for start in range(0, n, chunk):
        steps = rng.normal(0.0, step_hz, size=min(chunk, n - start))
        # AR(1) recursion dev[t] = keep * dev[t-1] + step[t], done in chunks.
        seg, _ = lfilter([1.0], [1.0, -keep], steps, zi=np.array([keep * dev]))
        out[start:start + seg.shape[0]] = f0 + seg
        dev = seg[-1]
    return out


DEFAULT_AMPS = {1: 0.02, 2: 0.06, 3: 0.015, 4: 0.0, 5: 0.0, 6: 0.0}


def synthetic_hum(freq_1hz: np.ndarray, sr: int = 1000, amps: dict | None = None,
                  noise_std: float = 1.0, seed: int = 0,
                  phases: dict | None = None) -> np.ndarray:
    """SYNTHETIC audio carrying mains hum that follows ``freq_1hz``.

    ``freq_1hz[s]`` is the instantaneous fundamental frequency at audio time
    s seconds; between whole seconds it is linearly interpolated. The output
    has ``(len(freq_1hz) - 1) * sr`` samples. Harmonic k has amplitude
    ``amps[k]`` (relative to white noise of std ``noise_std``).
    """
    amps = DEFAULT_AMPS if amps is None else amps
    rng = np.random.default_rng(seed)
    n_sec = len(freq_1hz) - 1
    if n_sec < 1:
        raise ValueError("need at least two seconds of frequency values")
    n = n_sec * sr
    t = np.arange(n) / sr
    inst = np.interp(t, np.arange(len(freq_1hz)), np.asarray(freq_1hz, dtype=np.float64))
    phase = 2.0 * np.pi * np.cumsum(inst) / sr
    out = rng.normal(0.0, noise_std, size=n)
    for k, a in amps.items():
        if a == 0:
            continue
        ph = (phases or {}).get(k, rng.uniform(0, 2 * np.pi))
        out += a * np.cos(k * phase + ph)
    return out.astype(np.float32)


def hann_truth(freq_1hz: np.ndarray, sr: int, n_frames: int, win_s: float = 16.0,
               hop_s: float = 1.0) -> np.ndarray:
    """Hann-weighted mean of the SYNTHETIC instantaneous frequency per frame.

    This is what a 16 s Hann-window estimator can at best recover, so it is
    the fair target for the extraction error test.
    """
    n_win = int(round(win_s * sr))
    hop = int(round(hop_s * sr))
    w = np.hanning(n_win)
    w = w / w.sum()
    out = np.empty(n_frames)
    grid = np.arange(len(freq_1hz))
    for i in range(n_frames):
        t = (i * hop + np.arange(n_win)) / sr
        out[i] = np.dot(w, np.interp(t, grid, freq_1hz))
    return out


def cut_audio(audio: np.ndarray, sr: int, at_s: float, length_s: float) -> np.ndarray:
    """SYNTHETIC edit: remove ``length_s`` seconds starting at ``at_s``."""
    a = int(round(at_s * sr))
    b = int(round((at_s + length_s) * sr))
    return np.concatenate([audio[:a], audio[b:]])
