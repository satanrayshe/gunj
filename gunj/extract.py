"""ENF (electric network frequency) extraction from a mono audio signal.

Method v1, ported from the 5 Nov feasibility probe (docs/probe/enf_hp.py):

- 16 s Hann window, 1 s hop. Frame i covers audio [i, i + 16) s, so its
  value belongs to the window centre, i + 8 s into the audio.
- Zero-padded FFT. At 1 kHz the FFT length is 2**18, a bin spacing of
  about 0.0038 Hz, the same as the probe.
- For each harmonic k = 1..6, the peak is searched within k*50 +- 0.15*k Hz
  and refined by quadratic interpolation on the log magnitude.
- Per-frame peak-to-floor ratio ("SNR"): the peak magnitude divided by the
  median magnitude over k*50 +- 3 Hz.
- Harmonics are combined with weight max(median SNR - 2, 0) (the probe's
  weights). If every weight is zero, the harmonic with the highest median
  SNR is used alone.
- The combined trace is smoothed with a 5-point running median, as in the
  probe.
- hum_present is True when any harmonic's median peak-to-floor ratio is at
  least 3 (PREP M1).

A naive baseline (``extract_enf_naive``) takes the strongest single FFT bin
near 50 Hz with no zero padding, no interpolation, no harmonics and no
smoothing (PREP baselines).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import gcd

import numpy as np
import scipy.fft as sfft
from scipy.ndimage import median_filter
from scipy.signal import resample_poly

NOMINAL_HZ = 50.0
DEFAULT_HARMONICS = (1, 2, 3, 4, 5, 6)
BAND_HALF_HZ = 0.15        # search band per harmonic: k*50 +- k*0.15 Hz
FLOOR_HALF_HZ = 3.0        # floor: median magnitude over k*50 +- 3 Hz
HUM_RATIO = 3.0            # PREP M1: hum present if any harmonic median >= 3
WEIGHT_OFFSET = 2.0        # v1 weight = max(median SNR - 2, 0)
WIN_S = 16.0
HOP_S = 1.0
ANALYSIS_SR = 1000         # audio above this rate is resampled down to it
TARGET_BIN_HZ = 1000.0 / 2 ** 18   # probe bin spacing (2**18-point FFT at 1 kHz)
SMOOTH_POINTS = 5


@dataclass
class ENFTrace:
    """A 1-Hz ENF trace plus the hum statistics behind it.

    ``freq[i]`` is the estimated mains frequency (fundamental equivalent, Hz)
    for the analysis window centred ``offset_s + i * hop_s`` seconds into the
    audio.
    """

    freq: np.ndarray                 # combined trace after median smoothing, Hz
    freq_raw: np.ndarray             # combined trace before smoothing, Hz
    snr: np.ndarray                  # per-second combined peak-to-floor ratio
    harmonics: tuple                 # harmonic numbers actually analysed
    harmonic_freq: np.ndarray        # (n_harmonics, n_frames), Hz divided by k
    harmonic_snr: np.ndarray         # (n_harmonics, n_frames), peak-to-floor
    harmonic_median_snr: dict        # k -> median peak-to-floor ratio
    weights: dict                    # k -> combination weight
    hum_present: bool
    method: str
    sr: int                          # analysis sample rate
    win_s: float = WIN_S
    hop_s: float = HOP_S
    hum_ratio: float = HUM_RATIO
    extra: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return int(self.freq.shape[0])

    @property
    def offset_s(self) -> float:
        """Seconds from the start of the audio to the centre of frame 0."""
        return self.win_s / 2.0

    def times(self) -> np.ndarray:
        """Window-centre time of each frame, in seconds from audio start."""
        return self.offset_s + self.hop_s * np.arange(len(self))

    def summary(self) -> dict:
        """Hum statistics as plain JSON-ready values."""
        med = {f"{int(k * NOMINAL_HZ)} Hz": round(float(v), 3)
               for k, v in self.harmonic_median_snr.items()}
        wts = {f"{int(k * NOMINAL_HZ)} Hz": round(float(v), 3)
               for k, v in self.weights.items()}
        return {
            "present": bool(self.hum_present),
            "rule": f"any harmonic median peak-to-floor >= {self.hum_ratio:g} (PREP M1)",
            "median_peak_to_floor": med,
            "weights": wts,
            "median_combined_snr": round(float(np.median(self.snr)), 3) if len(self) else None,
            "n_seconds": len(self),
            "method": self.method,
        }


def to_mono(audio: np.ndarray) -> np.ndarray:
    """Average channels of a (n,) or (n, channels) array into float64 mono."""
    a = np.asarray(audio)
    if a.ndim == 2:
        a = a.mean(axis=1)
    elif a.ndim != 1:
        raise ValueError("audio must be 1-D or (samples, channels)")
    return a.astype(np.float64, copy=False)


def prepare(audio: np.ndarray, sr: int, target_sr: int = ANALYSIS_SR):
    """Mono, mean-removed float32 audio at min(sr, target_sr)."""
    a = to_mono(audio)
    sr = int(sr)
    if sr > target_sr:
        g = gcd(int(target_sr), sr)
        a = resample_poly(a, int(target_sr) // g, sr // g)
        sr = int(target_sr)
    a = a - a.mean()
    return a.astype(np.float32), sr


def _nfft_for(sr: int, n_win: int, zero_pad: bool) -> int:
    if not zero_pad:
        return n_win
    need = max(n_win, int(np.ceil(sr / TARGET_BIN_HZ - 1e-6)))
    return 1 << int(np.ceil(np.log2(need)))


def _frames(a: np.ndarray, n_win: int, hop: int) -> np.ndarray:
    if a.shape[0] < n_win:
        raise ValueError(f"audio is shorter than one {n_win}-sample analysis window")
    return np.lib.stride_tricks.sliding_window_view(a, n_win)[::hop]


def _quad_interp(band: np.ndarray, j: np.ndarray) -> np.ndarray:
    """Quadratic interpolation on log magnitude around argmax j (rows of band)."""
    n_rows, width = band.shape
    d = np.zeros(n_rows, dtype=np.float64)
    ok = (j > 0) & (j < width - 1)
    if ok.any():
        rows = np.nonzero(ok)[0]
        jj = j[ok]
        tiny = 1e-30
        y0 = np.log(np.maximum(band[rows, jj - 1].astype(np.float64), tiny))
        y1 = np.log(np.maximum(band[rows, jj].astype(np.float64), tiny))
        y2 = np.log(np.maximum(band[rows, jj + 1].astype(np.float64), tiny))
        den = y0 - 2.0 * y1 + y2
        with np.errstate(divide="ignore", invalid="ignore"):
            dd = np.where(den != 0, 0.5 * (y0 - y2) / den, 0.0)
        d[rows] = np.clip(dd, -0.5, 0.5)
    return d


def _harmonic_tracks(a, sr, harmonics, win_s, hop_s, nominal, band_half,
                     floor_half, zero_pad, interpolate, batch, workers):
    n_win = int(round(win_s * sr))
    hop = int(round(hop_s * sr))
    nfft = _nfft_for(sr, n_win, zero_pad)
    df = sr / nfft
    frq = np.arange(nfft // 2 + 1) * df
    nyq = sr / 2.0

    used = [k for k in harmonics if k * nominal + floor_half < nyq]
    if not used:
        raise ValueError("no harmonic lies below the Nyquist frequency")
    bands = []
    for k in used:
        lo = int(np.searchsorted(frq, k * (nominal - band_half)))
        hi = int(np.searchsorted(frq, k * (nominal + band_half)))
        flo = int(np.searchsorted(frq, k * nominal - floor_half))
        fhi = int(np.searchsorted(frq, k * nominal + floor_half))
        bands.append((k, lo, max(hi, lo + 1), flo, max(fhi, flo + 1)))

    frames = _frames(a, n_win, hop)
    n_frames = frames.shape[0]
    win = np.hanning(n_win).astype(np.float32)
    hf = np.empty((len(used), n_frames), dtype=np.float64)
    hs = np.empty((len(used), n_frames), dtype=np.float64)

    for b0 in range(0, n_frames, batch):
        b1 = min(b0 + batch, n_frames)
        spec = sfft.rfft(frames[b0:b1] * win, n=nfft, axis=1, workers=workers)
        for h, (k, lo, hi, flo, fhi) in enumerate(bands):
            band = np.abs(spec[:, lo:hi])
            j = np.argmax(band, axis=1)
            peak = band[np.arange(b1 - b0), j].astype(np.float64)
            d = _quad_interp(band, j) if interpolate else 0.0
            hf[h, b0:b1] = (frq[lo + j] + d * df) / k
            floor = np.median(np.abs(spec[:, flo:fhi]), axis=1).astype(np.float64)
            hs[h, b0:b1] = peak / np.maximum(floor, 1e-30)
        del spec
    return tuple(used), hf, hs, nfft


def extract_enf(audio: np.ndarray, sr: int, harmonics=DEFAULT_HARMONICS,
                win_s: float = WIN_S, hop_s: float = HOP_S,
                nominal: float = NOMINAL_HZ, band_half: float = BAND_HALF_HZ,
                floor_half: float = FLOOR_HALF_HZ, hum_ratio: float = HUM_RATIO,
                weight_offset: float = WEIGHT_OFFSET, smooth: int = SMOOTH_POINTS,
                target_sr: int = ANALYSIS_SR, batch: int = 32,
                workers: int = 1) -> ENFTrace:
    """Extract a 1-Hz ENF trace with the v1 SNR-weighted harmonic method."""
    a, sr = prepare(audio, sr, target_sr)
    used, hf, hs, nfft = _harmonic_tracks(
        a, sr, harmonics, win_s, hop_s, nominal, band_half, floor_half,
        zero_pad=True, interpolate=True, batch=batch, workers=workers)

    med = {k: float(np.median(hs[i])) for i, k in enumerate(used)}
    weights = {k: max(med[k] - weight_offset, 0.0) for k in used}
    if sum(weights.values()) <= 0:
        best = max(used, key=lambda k: med[k])
        weights = {k: (1.0 if k == best else 0.0) for k in used}
    w = np.array([weights[k] for k in used])[:, None]
    freq_raw = (w * hf).sum(axis=0) / w.sum()
    snr = (w * hs).sum(axis=0) / w.sum()
    freq = median_filter(freq_raw, size=smooth, mode="nearest") if smooth > 1 else freq_raw.copy()
    hum = any(v >= hum_ratio for v in med.values())
    return ENFTrace(freq=freq, freq_raw=freq_raw, snr=snr, harmonics=used,
                    harmonic_freq=hf, harmonic_snr=hs, harmonic_median_snr=med,
                    weights=weights, hum_present=bool(hum),
                    method="v1-snr-weighted-harmonics", sr=sr, win_s=win_s,
                    hop_s=hop_s, hum_ratio=hum_ratio,
                    extra={"nfft": nfft, "smooth_points": smooth})


def extract_enf_naive(audio: np.ndarray, sr: int, win_s: float = WIN_S,
                      hop_s: float = HOP_S, nominal: float = NOMINAL_HZ,
                      band_half: float = BAND_HALF_HZ,
                      floor_half: float = FLOOR_HALF_HZ,
                      hum_ratio: float = HUM_RATIO, smooth: int = 1,
                      target_sr: int = ANALYSIS_SR, batch: int = 64,
                      workers: int = 1) -> ENFTrace:
    """Naive baseline: strongest single FFT bin within 50 +- 0.15 Hz.

    Same 16 s Hann frames and 1 s hop as v1, but no zero padding (bin
    spacing 1/16 Hz), no interpolation, fundamental only, no smoothing.
    """
    a, sr = prepare(audio, sr, target_sr)
    used, hf, hs, nfft = _harmonic_tracks(
        a, sr, (1,), win_s, hop_s, nominal, band_half, floor_half,
        zero_pad=False, interpolate=False, batch=batch, workers=workers)
    med = {1: float(np.median(hs[0]))}
    freq_raw = hf[0].copy()
    freq = median_filter(freq_raw, size=smooth, mode="nearest") if smooth > 1 else freq_raw.copy()
    return ENFTrace(freq=freq, freq_raw=freq_raw, snr=hs[0].copy(), harmonics=(1,),
                    harmonic_freq=hf, harmonic_snr=hs, harmonic_median_snr=med,
                    weights={1: 1.0}, hum_present=bool(med[1] >= hum_ratio),
                    method="naive-single-bin-50hz", sr=sr, win_s=win_s,
                    hop_s=hop_s, hum_ratio=hum_ratio,
                    extra={"nfft": nfft, "smooth_points": smooth})
