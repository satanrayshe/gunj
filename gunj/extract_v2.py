"""ENF extractor v2: per-frame SNR-weighted harmonics (tuned on Dev only).

v1 (gunj/extract.py) is frozen by PROTOCOL.md section 7 and is not changed.
v2 reuses v1's spectral front end (16 s Hann windows, 1 s hop, 2**18-point
FFT at 1 kHz, a peak per harmonic refined by quadratic interpolation, and the
peak-to-floor ratio over k*50 +- 3 Hz). What v2 changes:

1. Harmonic gate. A harmonic whose median peak-to-floor ratio is below
   ``gate`` is treated as noise only. Its weight is 0 in every frame.
2. Per-frame weights. In frame t, harmonic k gets
   ``max(snr_k(t) - frame_offset, 0) ** frame_power`` (0 if gated out).
   The combined value is the weighted mean of the per-harmonic estimates
   (each divided by k). Frames where every weight is 0 are filled by linear
   interpolation from their neighbours.
3. Optional consensus. Harmonics further than ``consensus_hz`` from the
   per-frame weighted median of the harmonics get weight 0 in that frame.
4. Frame weights for the correlation. ``u(t)`` is the total frame weight,
   smoothed by a running mean of ``weight_smooth`` frames, divided by its
   ``weight_quantile`` quantile and clipped to [0, 1]. ``gunj.match_v2``
   uses ``u`` in a weighted normalised cross-correlation. With
   ``corr_weights=False`` u is None and the frozen v1 matcher is used.
5. The band half-width per harmonic, ``band_half`` (k * band_half Hz), may
   differ from v1's 0.15 Hz.

The hum flag keeps the PREP M1 rule: any harmonic's median peak-to-floor
ratio is at least 3. ``V2_FROZEN`` holds the parameters chosen on the five
Dev streams (results/v2_tuning.md). It is frozen by the git tag
``extractor-v2``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace

import numpy as np
from scipy.ndimage import median_filter, uniform_filter1d

from .extract import (ANALYSIS_SR, DEFAULT_HARMONICS, FLOOR_HALF_HZ, HOP_S, HUM_RATIO,
                      NOMINAL_HZ, WIN_S, ENFTrace, _harmonic_tracks, prepare)

METHOD = "v2-frame-snr-weighted-harmonics"


@dataclass(frozen=True)
class V2Params:
    band_half: float = 0.15          # Hz at the fundamental; k * band_half at harmonic k
    floor_half: float = FLOOR_HALF_HZ
    harmonics: tuple = DEFAULT_HARMONICS
    gate: float = 3.0                # median peak-to-floor needed to use a harmonic at all
    frame_offset: float = 1.0        # per-frame weight max(snr - offset, 0) ** power
    frame_power: float = 1.0
    consensus_hz: float | None = None
    smooth: int = 5                  # running-median points on the combined trace
    corr_weights: bool = True        # per-frame weights for the correlation
    weight_smooth: int = 5           # running-mean points on the frame weight
    weight_quantile: float = 0.9     # u = clip(w / quantile(w), 0, 1)
    hum_ratio: float = HUM_RATIO

    def to_dict(self) -> dict:
        d = asdict(self)
        d["harmonics"] = list(self.harmonics)
        return d


# Chosen on Dev only: candidate B1 in results/v2_tuning.md (J = 0.222 against
# v1's -0.012, results/v2_tuning.json). Frozen by git tag extractor-v2.
V2_FROZEN = V2Params(band_half=0.15, floor_half=FLOOR_HALF_HZ, harmonics=DEFAULT_HARMONICS,
                     gate=3.0, frame_offset=1.0, frame_power=2.0, consensus_hz=None,
                     smooth=5, corr_weights=True, weight_smooth=5, weight_quantile=0.9,
                     hum_ratio=HUM_RATIO)


def harmonic_tracks(audio: np.ndarray, sr: int, band_half: float = 0.15,
                    floor_half: float = FLOOR_HALF_HZ, harmonics=DEFAULT_HARMONICS,
                    batch: int = 32, workers: int = 1):
    """Per-harmonic frequency (Hz / k) and peak-to-floor ratio per frame.

    Returns (harmonics used, hf, hs, nfft, sr). The same front end as v1,
    with the band half-width as a parameter.
    """
    a, sr = prepare(audio, sr, ANALYSIS_SR)
    used, hf, hs, nfft = _harmonic_tracks(
        a, sr, harmonics, WIN_S, HOP_S, NOMINAL_HZ, band_half, floor_half,
        zero_pad=True, interpolate=True, batch=batch, workers=workers)
    return used, hf, hs, nfft, sr


def _weighted_median_rows(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Weighted median down axis 0 for each column (weights >= 0)."""
    order = np.argsort(values, axis=0)
    v = np.take_along_axis(values, order, axis=0)
    w = np.take_along_axis(weights, order, axis=0)
    cw = np.cumsum(w, axis=0)
    half = 0.5 * cw[-1]
    idx = np.argmax(cw >= half[None, :], axis=0)
    return v[idx, np.arange(values.shape[1])]


def combine(used, hf: np.ndarray, hs: np.ndarray, p: V2Params, sr: int = ANALYSIS_SR,
            nfft: int | None = None) -> ENFTrace:
    """Combine per-harmonic tracks into a v2 trace (see the module docstring)."""
    used = tuple(used)
    med = {k: float(np.median(hs[i])) for i, k in enumerate(used)}
    gate = np.array([1.0 if med[k] >= p.gate else 0.0 for k in used])
    fallback = False
    if gate.sum() == 0:
        # no harmonic passes the gate: use the strongest one alone (as v1 does)
        gate[int(np.argmax([med[k] for k in used]))] = 1.0
        fallback = True
    w = gate[:, None] * np.maximum(hs - p.frame_offset, 0.0) ** p.frame_power
    if p.consensus_hz is not None and len(used) > 1:
        tot0 = w.sum(axis=0)
        has = tot0 > 0
        if has.any():
            wm = _weighted_median_rows(hf[:, has], w[:, has])
            far = np.abs(hf[:, has] - wm[None, :]) > p.consensus_hz
            sub = w[:, has]
            sub[far] = 0.0
            w[:, has] = sub
    tot = w.sum(axis=0)
    ok = tot > 0
    n = hf.shape[1]
    if not ok.any():
        best = int(np.argmax(gate))
        freq_raw = hf[best].astype(np.float64).copy()
        snr = hs[best].astype(np.float64).copy()
        ok = np.ones(n, dtype=bool)
        tot = np.ones(n)
    else:
        with np.errstate(invalid="ignore", divide="ignore"):
            freq_raw = (w * hf).sum(axis=0) / tot
            snr = (w * hs).sum(axis=0) / tot
        if not ok.all():
            x = np.arange(n)
            freq_raw[~ok] = np.interp(x[~ok], x[ok], freq_raw[ok])
            snr[~ok] = 0.0
    freq = median_filter(freq_raw, size=p.smooth, mode="nearest") if p.smooth > 1 else freq_raw.copy()

    u = None
    if p.corr_weights:
        uw = tot * ok
        if p.weight_smooth > 1:
            uw = uniform_filter1d(uw.astype(np.float64), size=p.weight_smooth, mode="nearest")
        pos = uw[uw > 0]
        if pos.size:
            scale = float(np.quantile(pos, p.weight_quantile))
            u = np.clip(uw / scale, 0.0, 1.0) if scale > 0 else np.ones(n)
        else:
            u = np.ones(n)
        if not np.any(u > 0):
            u = np.ones(n)
    weights = {k: float(gate[i]) for i, k in enumerate(used)}
    return ENFTrace(freq=freq, freq_raw=freq_raw, snr=snr, harmonics=used,
                    harmonic_freq=hf, harmonic_snr=hs, harmonic_median_snr=med,
                    weights=weights, hum_present=bool(any(v >= p.hum_ratio for v in med.values())),
                    method=METHOD, sr=int(sr), win_s=WIN_S, hop_s=HOP_S, hum_ratio=p.hum_ratio,
                    extra={"nfft": nfft, "smooth_points": p.smooth, "params": p.to_dict(),
                           "frame_weight": u, "gate_fallback": fallback,
                           "frames_with_weight": int(ok.sum())})


def extract_enf_v2(audio: np.ndarray, sr: int, params: V2Params | None = None,
                   batch: int = 32, workers: int = 1, **overrides) -> ENFTrace:
    """Extract a 1-Hz ENF trace with v2 (``V2_FROZEN`` unless params are given)."""
    p = params or V2_FROZEN
    if overrides:
        p = replace(p, **overrides)
    used, hf, hs, nfft, sr2 = harmonic_tracks(audio, sr, p.band_half, p.floor_half,
                                              p.harmonics, batch=batch, workers=workers)
    return combine(used, hf, hs, p, sr=sr2, nfft=nfft)
