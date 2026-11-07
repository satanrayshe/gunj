"""Weighted normalised cross-correlation for extractor v2 traces.

gunj/match.py is frozen by PROTOCOL.md section 7 and is not changed. This
module adds the per-frame weights that PROTOCOL allows v2 to use in the
correlation. Everything else is the frozen matcher's: the same blocks, the
same admissibility rules (at least 90% valid reference seconds and a
reference standard deviation of at least 0.003 Hz, both unweighted), the
same local-peak candidates, the same runner-up at least 60 s away, and the
same confidence, atanh(r1) - atanh(r2).

With weights u (u >= 0, W = sum u) the correlation at position p is

    xw = sum(u x) / W,   a = u (x - xw)
    cov(p) = sum_i a_i y[p+i]
    vy(p)  = sum_i u_i y[p+i]**2 - (sum_i u_i y[p+i])**2 / W
    r(p)   = cov(p) / sqrt(sum(u (x - xw)**2) * vy(p))

With u = 1 everywhere this is the frozen matcher's r.
"""
from __future__ import annotations

import numpy as np
import scipy.fft as sfft
from scipy.ndimage import maximum_filter1d

from .match import (DEFAULT_NFFT, MIN_REF_STD_HZ, MIN_VALID, RUNNER_UP_SEP_S, SearchResult,
                    _select, _sliding_sums, fisher_margin, ncc_search, ref_segment)


def _prep_weighted(trace, weights):
    x = np.asarray(trace, dtype=np.float64)
    u = np.asarray(weights, dtype=np.float64)
    if x.ndim != 1 or x.size < 2 or u.shape != x.shape:
        raise ValueError("trace and weights must be 1-D arrays of the same length (>= 2)")
    if not (np.all(np.isfinite(x)) and np.all(np.isfinite(u))):
        raise ValueError("trace or weights contain non-finite values")
    if np.any(u < 0):
        raise ValueError("weights must be >= 0")
    W = float(u.sum())
    if W <= 0:
        raise ValueError("all weights are zero")
    xc = x - float((u * x).sum()) / W
    sxx = float((u * xc * xc).sum())
    if sxx <= 0:
        raise ValueError("weighted trace is constant; nothing to correlate")
    return u * xc, u, W, sxx


def wncc_search(trace, weights, ref_values, ref_valid=None, start: int = 0,
                stop: int | None = None, k: int = 5, sep: int = RUNNER_UP_SEP_S,
                min_valid: float = MIN_VALID, min_ref_std: float = MIN_REF_STD_HZ,
                nfft: int = DEFAULT_NFFT, keep_per_block: int | None = None,
                r_out=None, workers: int = 1) -> SearchResult:
    """Blocked FFT weighted NCC search; same interface and output as ncc_search.

    If ``r_out`` is given (length stop - start), r is written into it, with
    -inf where a window is excluded.
    """
    a, u, W, sxx = _prep_weighted(trace, weights)
    n = a.size
    n_ref = int(len(ref_values))
    p0 = max(0, int(start))
    p1 = min(n_ref if stop is None else int(stop), n_ref - n + 1)
    keep = keep_per_block or max(64, 4 * (k + 2))
    params = dict(k=k, sep=sep, min_valid=min_valid, min_ref_std=min_ref_std, weighted=True)
    if p1 <= p0:
        return SearchResult([], None, None, None, n, (p0, max(p0, p1)), 0, sep, params=params)

    nfft = max(int(nfft), 1 << int(np.ceil(np.log2(4 * (n + 2 * sep)))))
    core = nfft - (n - 1) - 2 * sep
    cache: dict = {}
    std_floor2 = (min_ref_std ** 2) * n
    cand_idx, cand_r = [], []
    n_ok = 0
    for c0 in range(p0, p1, core):
        c1 = min(c0 + core, p1)
        e0, e1 = max(p0, c0 - sep), min(p1, c1 + sep)
        npos = e1 - e0
        m = npos + n - 1
        y, ok = ref_segment(ref_values, ref_valid, e0, e0 + m)
        fl = sfft.next_fast_len(m, real=True)
        if fl not in cache:
            cache.clear()
            cache[fl] = (np.conj(sfft.rfft(a, fl, workers=workers)),
                         np.conj(sfft.rfft(u, fl, workers=workers)))
        A, U = cache[fl]
        Y = sfft.rfft(y, fl, workers=workers)
        Y2 = sfft.rfft(y * y, fl, workers=workers)
        cov = sfft.irfft(Y * A, fl, workers=workers)[:npos]
        sy = sfft.irfft(Y * U, fl, workers=workers)[:npos]
        syy = sfft.irfft(Y2 * U, fl, workers=workers)[:npos]
        del Y, Y2
        vy = syy - sy * sy / W
        # admissibility, unweighted, exactly as in the frozen matcher
        s1 = _sliding_sums(y, n, npos)
        s2 = _sliding_sums(y * y, n, npos)
        var = s2 - s1 * s1 / n
        frac = _sliding_sums(ok.astype(np.float64), n, npos) / n
        bad = (frac < min_valid) | (var < std_floor2) | (vy <= 0)
        r = cov / np.sqrt(np.maximum(vy, 1e-300) * sxx)
        r[bad] = -np.inf
        del cov, sy, syy, vy, s1, s2, var, frac, y, ok

        lo, hi = c0 - e0, c1 - e0
        rc = r[lo:hi]
        n_ok += int(np.isfinite(rc).sum())
        if r_out is not None:
            r_out[c0 - p0:c1 - p0] = rc
        padded = np.full((c1 - c0) + 2 * sep, -np.inf)
        off = e0 - (c0 - sep)
        padded[off:off + npos] = r
        mx = maximum_filter1d(padded, size=2 * sep + 1, mode="constant", cval=-np.inf)
        core_pad = padded[sep:sep + (c1 - c0)]
        is_peak = (core_pad == mx[sep:sep + (c1 - c0)]) & np.isfinite(core_pad)
        pk = np.flatnonzero(is_peak)
        if pk.size > keep:
            pk = pk[np.argpartition(-core_pad[pk], keep - 1)[:keep]]
        cand_idx.append(pk + c0)
        cand_r.append(core_pad[pk])
        del r, padded, mx

    idx = np.concatenate(cand_idx) if cand_idx else np.empty(0, dtype=np.int64)
    rr = np.concatenate(cand_r) if cand_r else np.empty(0)
    chosen = _select(idx, rr, max(k, 2), sep)
    runner = chosen[1] if len(chosen) > 1 else None
    margin = conf = None
    if runner is not None:
        margin = chosen[0].r - runner.r
        conf = fisher_margin(chosen[0].r, runner.r)
    return SearchResult(chosen[:k], runner, margin, conf, n, (p0, p1), n_ok, sep, params=params)


def search(freq, frame_weight, ref_values, ref_valid=None, start: int = 0,
           stop: int | None = None, k: int = 5, **kw) -> SearchResult:
    """Weighted search if frame weights are given, else the frozen ncc_search."""
    if frame_weight is None:
        return ncc_search(freq, ref_values, ref_valid, start=start, stop=stop, k=k, **kw)
    return wncc_search(freq, frame_weight, ref_values, ref_valid, start=start, stop=stop, k=k, **kw)
