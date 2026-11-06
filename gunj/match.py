"""Match an ENF trace against a long 1-second reference.

Search
------
``ncc_search`` computes the normalised cross-correlation r between the
trace and every same-length window of the reference, with FFTs done block
by block (overlap-save). One block holds about 2 million reference seconds,
so an 83-million-second reference is searched in about 40 blocks and the
whole reference never has to be in memory at once.

- r[p] compares trace frame 0 with reference second p. Since frame 0 is
  centred 8 s into the audio, the recording starts at reference second
  p - 8 (see ENFTrace.offset_s).
- Windows with less than 90% valid reference seconds are excluded. Invalid
  seconds are non-finite values, values outside 45-55 Hz, or seconds marked
  False in an optional validity mask. Inside a block they are filled by
  linear interpolation before correlating.
- Windows whose reference standard deviation is under 0.003 Hz are excluded
  (the probe's floor against flat, filled stretches).
- An optional scope [start, stop) limits the candidate positions p.

Candidates and confidence
-------------------------
A candidate is a position whose r is the largest within +-60 s of itself
(a local peak). The top-k candidates are returned, sorted by r. The runner-up
is the best candidate at least 60 s from the top one; this is "the best
wrong candidate" if the top one is right. PREP M3 asks for a score built on
the margin, not on raw r, so:

    margin     = r1 - r2
    confidence = atanh(r1) - atanh(r2)   (margin in Fisher-z units)

The decision threshold on ``confidence`` is not set here. It is read by the
CLI from config.json and is a placeholder until it is calibrated on the
negative set.

Edit localisation
-----------------
``local_offsets`` slides short windows along a locked trace and finds each
window's best offset near the lock (useful for plots). ``find_edits`` splits
the trace into pieces that each fit the reference at one constant offset and
reports where the offset jumps (a cut or an insertion). Short windows alone
proved too noisy on SYNTHETIC medium-strength hum, so the pieces are fitted
as long segments. Basic version only; its defaults (r >= 0.6, residual drop
>= 0.15, jump >= 5 s) were chosen on SYNTHETIC cuts and are not calibrated
on real clips.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import scipy.fft as sfft
from scipy.ndimage import maximum_filter1d

NOMINAL_HZ = 50.0
VALID_RANGE_HZ = (45.0, 55.0)
MIN_VALID = 0.9
MIN_REF_STD_HZ = 0.003
RUNNER_UP_SEP_S = 60
DEFAULT_NFFT = 1 << 21          # about 2.1M positions per block, ~200 MB peak
_R_CLIP = 1.0 - 1e-6


# --------------------------------------------------------------------------
# Reference access
# --------------------------------------------------------------------------

def ref_segment(values, valid, a: int, b: int):
    """Reference seconds [a, b) as float64 Hz minus 50, gaps filled.

    Returns (y, ok): ``ok`` marks valid seconds; invalid ones in ``y`` are
    linear interpolations between the nearest valid seconds in the segment
    (or 0 if the segment has none).
    """
    seg = np.asarray(values[a:b], dtype=np.float64)
    with np.errstate(invalid="ignore"):
        ok = np.isfinite(seg) & (seg >= VALID_RANGE_HZ[0]) & (seg <= VALID_RANGE_HZ[1])
    if valid is not None:
        ok &= np.asarray(valid[a:b]).astype(bool)
    y = seg - NOMINAL_HZ
    if not ok.all():
        good = np.flatnonzero(ok)
        if good.size == 0:
            y[:] = 0.0
        else:
            bad = np.flatnonzero(~ok)
            y[bad] = np.interp(bad, good, y[good])
    return y, ok


def _prep_query(trace) -> np.ndarray:
    x = np.asarray(trace, dtype=np.float64)
    if x.ndim != 1 or x.size < 2:
        raise ValueError("trace must be a 1-D array of at least 2 values")
    if not np.all(np.isfinite(x)):
        raise ValueError("trace contains non-finite values")
    x = x - x.mean()
    nrm = np.linalg.norm(x)
    if nrm == 0:
        raise ValueError("trace is constant; nothing to correlate")
    return x / nrm


def _sliding_sums(v: np.ndarray, n: int, npos: int) -> np.ndarray:
    cs = np.concatenate(([0.0], np.cumsum(v, dtype=np.float64)))
    return cs[n:n + npos] - cs[:npos]


# --------------------------------------------------------------------------
# Global search
# --------------------------------------------------------------------------

@dataclass
class Candidate:
    index: int      # reference second aligned with trace frame 0
    r: float


@dataclass
class SearchResult:
    candidates: list            # top-k Candidates, r descending, >= sep apart
    runner_up: Candidate | None  # best candidate >= sep from the top one
    margin: float | None         # r1 - r2
    confidence: float | None     # atanh(r1) - atanh(r2)
    n_query: int
    scope: tuple                 # candidate positions [start, stop)
    n_positions: int             # positions with an admissible window
    sep: int
    strip: np.ndarray | None = None   # max r per strip_bin positions (optional)
    strip_bin: int | None = None
    params: dict = field(default_factory=dict)

    @property
    def top(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None


def fisher_margin(r1: float, r2: float) -> float:
    """Margin between two correlations in Fisher-z units."""
    a = float(np.arctanh(np.clip(r1, -_R_CLIP, _R_CLIP)))
    b = float(np.arctanh(np.clip(r2, -_R_CLIP, _R_CLIP)))
    return a - b


def _select(idx: np.ndarray, r: np.ndarray, k: int, sep: int) -> list:
    order = np.lexsort((idx, -r))   # r descending, then index ascending
    chosen: list = []
    for o in order:
        i = int(idx[o])
        if all(abs(i - c.index) >= sep for c in chosen):
            chosen.append(Candidate(i, float(r[o])))
            if len(chosen) >= k:
                break
    return chosen


def ncc_search(trace, ref_values, ref_valid=None, start: int = 0,
               stop: int | None = None, k: int = 5, sep: int = RUNNER_UP_SEP_S,
               min_valid: float = MIN_VALID, min_ref_std: float = MIN_REF_STD_HZ,
               nfft: int = DEFAULT_NFFT, keep_per_block: int | None = None,
               strip_bin: int | None = None, r_out=None,
               workers: int = 1) -> SearchResult:
    """Blocked FFT normalised cross-correlation search of ``trace``.

    ``ref_values`` may be a NumPy array or a read-only np.memmap of float32
    Hz values; ``ref_valid`` an optional same-length mask. Positions p are
    searched in [start, stop) with p + len(trace) <= len(ref_values).
    If ``r_out`` is given (length stop - start), r is written into it, with
    -inf where a window is excluded.
    """
    x = _prep_query(trace)
    n = x.size
    n_ref = int(len(ref_values))
    p0 = max(0, int(start))
    p1 = min(n_ref if stop is None else int(stop), n_ref - n + 1)
    keep = keep_per_block or max(64, 4 * (k + 2))
    params = dict(k=k, sep=sep, min_valid=min_valid, min_ref_std=min_ref_std)
    if p1 <= p0:
        return SearchResult([], None, None, None, n, (p0, max(p0, p1)), 0, sep,
                            params=params)

    nfft = max(int(nfft), 1 << int(np.ceil(np.log2(4 * (n + 2 * sep)))))
    core = nfft - (n - 1) - 2 * sep
    xspec_cache: dict = {}
    std_floor2 = (min_ref_std ** 2) * n
    n_strip = None
    strip = None
    if strip_bin:
        n_strip = int(np.ceil((p1 - p0) / strip_bin))
        strip = np.full(n_strip, -np.inf, dtype=np.float32)

    cand_idx, cand_r = [], []
    n_ok = 0
    for c0 in range(p0, p1, core):
        c1 = min(c0 + core, p1)
        e0, e1 = max(p0, c0 - sep), min(p1, c1 + sep)
        npos = e1 - e0
        m = npos + n - 1
        y, ok = ref_segment(ref_values, ref_valid, e0, e0 + m)
        fl = sfft.next_fast_len(m, real=True)
        if fl not in xspec_cache:
            xspec_cache.clear()
            xspec_cache[fl] = np.conj(sfft.rfft(x, fl, workers=workers))
        corr = sfft.irfft(sfft.rfft(y, fl, workers=workers) * xspec_cache[fl], fl,
                          workers=workers)[:npos]
        s1 = _sliding_sums(y, n, npos)
        s2 = _sliding_sums(y * y, n, npos)
        var = s2 - s1 * s1 / n                    # sum of squared deviations
        frac = _sliding_sums(ok.astype(np.float64), n, npos) / n
        bad = (frac < min_valid) | (var < std_floor2)
        r = corr / np.sqrt(np.maximum(var, 1e-300))
        r[bad] = -np.inf
        del corr, s1, s2, var, frac, y, ok

        lo, hi = c0 - e0, c1 - e0               # core slice inside r
        rc = r[lo:hi]
        n_ok += int(np.isfinite(rc).sum())
        if r_out is not None:
            r_out[c0 - p0:c1 - p0] = rc
        if strip is not None:
            rel = np.arange(c0 - p0, c1 - p0)
            bins = rel // strip_bin
            starts = np.flatnonzero(np.r_[True, bins[1:] != bins[:-1]])
            vals = np.maximum.reduceat(rc, starts).astype(np.float32)
            np.maximum.at(strip, bins[starts], vals)

        # local peaks: r equal to the max over +-sep (positions outside the
        # searched range count as -inf)
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
    if strip is not None:
        strip[~np.isfinite(strip)] = np.nan
    return SearchResult(chosen[:k], runner, margin, conf, n, (p0, p1), n_ok, sep,
                        strip=strip, strip_bin=strip_bin, params=params)


# --------------------------------------------------------------------------
# Local re-alignment and edit localisation (basic)
# --------------------------------------------------------------------------

def local_offsets(trace, ref_values, ref_valid, lock_index: int, win: int = 120,
                  step: int = 10, max_shift: int = 300, min_valid: float = MIN_VALID,
                  min_ref_std: float = MIN_REF_STD_HZ) -> dict:
    """Best local offset of each short window of a locked trace.

    Window w covers trace frames [w, w + win). With no edits it should align
    with reference second lock_index + w. The returned offset is how many
    seconds later (positive) or earlier it actually fits best, searched over
    +-max_shift. r is the window's correlation at that offset (NaN if no
    admissible position).
    """
    t = np.asarray(trace, dtype=np.float64)
    n_ref = int(len(ref_values))
    starts = np.arange(0, max(t.size - win + 1, 0), step)
    offs = np.full(starts.size, np.nan)
    rs = np.full(starts.size, np.nan)
    for i, w in enumerate(starts):
        xw = t[w:w + win] - t[w:w + win].mean()
        nx = np.linalg.norm(xw)
        if nx == 0:
            continue
        xw /= nx
        base = int(lock_index) + int(w)
        a = max(0, base - max_shift)
        b = min(n_ref - win, base + max_shift)
        if b < a:
            continue
        y, ok = ref_segment(ref_values, ref_valid, a, b + win)
        yw = np.lib.stride_tricks.sliding_window_view(y, win)
        yc = yw - yw.mean(axis=1, keepdims=True)
        den = np.sqrt((yc * yc).sum(axis=1))
        frac = np.lib.stride_tricks.sliding_window_view(ok, win).mean(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            r = (yc @ xw) / den
        r[(frac < min_valid) | (den * den < (min_ref_std ** 2) * win) | ~np.isfinite(r)] = -np.inf
        j = int(np.argmax(r))
        if np.isfinite(r[j]):
            offs[i] = a + j - base
            rs[i] = r[j]
    return {"window_start": starts, "offset": offs, "r": rs, "win": win, "step": step}


def _ref_values_at(ref_values, ref_valid, idx):
    """Gap-filled reference values (Hz) at integer indices idx."""
    n_ref = int(len(ref_values))
    idx = np.clip(np.asarray(idx, dtype=np.int64), 0, n_ref - 1)
    y, _ = ref_segment(ref_values, ref_valid, int(idx.min()), int(idx.max()) + 1)
    return y[idx - idx.min()] + NOMINAL_HZ


def _fit(t, ref_values, ref_valid, lock, a, b, centre, radius):
    """Best offset of trace frames [a, b) within centre +- radius.

    Returns (offset, r, residual) or None. The residual is the sum of
    squared errors after the best linear fit, SS_x * (1 - r**2).
    """
    if b - a < 2:
        return None
    try:
        res = ncc_search(t[a:b], ref_values, ref_valid,
                         start=lock + a + int(centre) - radius,
                         stop=lock + a + int(centre) + radius + 1, k=1, sep=1)
    except ValueError:
        return None
    if not res.candidates:
        return None
    x = t[a:b]
    ssx = float(((x - x.mean()) ** 2).sum())
    r = res.top.r
    return res.top.index - (lock + a), r, ssx * (1.0 - r * r)


def _refine_split(t, ref_values, ref_valid, lock, a, s, b, off_l, off_r, reach):
    """Frame where the trace switches from off_l to off_r, searched in s +- reach.

    Each side's constant bias is taken from its whole segment ([a, s) and
    [s, b)); the switch frame minimises the summed squared error.
    """
    j_l, j_r = np.arange(a, s), np.arange(s, b)
    bias_l = np.median(t[a:s] - _ref_values_at(ref_values, ref_valid, lock + j_l + int(off_l)))
    bias_r = np.median(t[s:b] - _ref_values_at(ref_values, ref_valid, lock + j_r + int(off_r)))
    lo, hi = max(a + 1, s - reach), min(b - 1, s + reach)
    j = np.arange(lo, hi)
    el = (t[lo:hi] - _ref_values_at(ref_values, ref_valid, lock + j + int(off_l)) - bias_l) ** 2
    er = (t[lo:hi] - _ref_values_at(ref_values, ref_valid, lock + j + int(off_r)) - bias_r) ** 2
    cl = np.concatenate(([0.0], np.cumsum(el)))
    cr = np.concatenate(([0.0], np.cumsum(er)))
    cost = cl + (cr[-1] - cr)          # cost[i]: frames < lo + i use off_l
    return lo + int(np.argmin(cost))


def _anchor(t, ref_values, ref_valid, lock, radius, max_shift):
    """Choose the anchor for edit search.

    A whole-trace lock on an edited clip can land between the two sides, or
    (for long cuts) somewhere else entirely. Each half of the trace is
    locked on its own within +-radius of the given lock. Each candidate
    anchor (given, first-half, second-half) is scored by the mean of the
    best r of the two halves within +-max_shift of it; the best-scoring
    anchor is used (ties keep the given lock).
    """
    h = t.size // 2
    out = {"given": int(lock), "used": int(lock)}
    cands = {"given": int(lock)}
    try:
        a = ncc_search(t[:h], ref_values, ref_valid, start=lock - radius,
                       stop=lock + radius + 1, k=1).top
        b = ncc_search(t[h:], ref_values, ref_valid, start=lock + h - radius,
                       stop=lock + h + radius + 1, k=1).top
    except ValueError:
        return out
    if a is not None:
        cands["first_half"] = int(a.index)
    if b is not None:
        cands["second_half"] = int(b.index - h)
    out.update({k: v for k, v in cands.items() if k != "given"})
    scores = {}
    for name, anc in cands.items():
        fa = _fit(t, ref_values, ref_valid, anc, 0, h, 0, max_shift)
        fb = _fit(t, ref_values, ref_valid, anc, h, t.size, 0, max_shift)
        if fa is not None and fb is not None:
            scores[name] = 0.5 * (fa[1] + fb[1])
    if scores:
        best = max(scores, key=lambda k: (scores[k], k == "given"))
        out["used"] = cands[best]
        out["chosen"] = best
        out["scores"] = {k: round(float(v), 4) for k, v in scores.items()}
    return out


def _segment(t, ref_values, ref_valid, lock, a, b, *, max_shift, min_seg, step,
             min_jump, r_min, min_gain, depth, out):
    """Binary segmentation of frames [a, b) into constant-offset pieces."""
    if b - a < 2 * min_seg or depth <= 0:
        return
    whole = _fit(t, ref_values, ref_valid, lock, a, b, 0, max_shift)
    if whole is None:
        return
    best = None
    for s in range(a + min_seg, b - min_seg + 1, step):
        left = _fit(t, ref_values, ref_valid, lock, a, s, 0, max_shift)
        right = _fit(t, ref_values, ref_valid, lock, s, b, 0, max_shift)
        if left is None or right is None:
            continue
        cost = left[2] + right[2]
        if best is None or cost < best[0]:
            best = (cost, s, left, right)
    if best is None:
        return
    cost, s, left, right = best
    gain = 1.0 - cost / whole[2] if whole[2] > 0 else 0.0
    jump = right[0] - left[0]
    if abs(jump) < min_jump or min(left[1], right[1]) < r_min or gain < min_gain:
        return
    s = _refine_split(t, ref_values, ref_valid, lock, a, s, b, left[0], right[0], step + 16)
    left = _fit(t, ref_values, ref_valid, lock, a, s, left[0], 3) or left
    right = _fit(t, ref_values, ref_valid, lock, s, b, right[0], 3) or right
    out.append({"frame": int(s), "jump_s": float(right[0] - left[0]),
                "offset_before_s": float(left[0]), "offset_after_s": float(right[0]),
                "r_before": round(float(left[1]), 4), "r_after": round(float(right[1]), 4),
                "residual_drop": round(float(gain), 4)})
    kw = dict(max_shift=max_shift, min_seg=min_seg, step=step, min_jump=min_jump,
              r_min=r_min, min_gain=min_gain, depth=depth - 1, out=out)
    _segment(t, ref_values, ref_valid, lock, a, s, **kw)
    _segment(t, ref_values, ref_valid, lock, s, b, **kw)


def find_edits(trace, ref_values, ref_valid, lock_index: int, max_shift: int = 300,
               min_seg: int = 180, step: int = 10, min_jump: int = 5,
               r_min: float = 0.6, min_gain: float = 0.15, max_edits: int = 3,
               relock_radius: int | None = 86_400, win: int = 120,
               offset_s: float = 8.0, hop_s: float = 1.0) -> dict:
    """Find cuts and insertions in a locked trace (basic edit localisation).

    1. Optionally re-anchor: lock each half of the trace on its own within
       +-relock_radius of ``lock_index`` (see ``_anchor``).
    2. Binary segmentation: for every split point (every ``step`` frames,
       at least ``min_seg`` frames from either end), find the best offset of
       each side within +-max_shift s of the anchor and keep the split with
       the smallest total residual. Accept it if the two offsets differ by
       at least ``min_jump`` s, both sides correlate at r >= r_min, and the
       split removes at least ``min_gain`` of the one-offset residual. The
       split frame is refined frame by frame, then each side is searched
       again (``max_edits`` levels deep at most).
    3. For display, ``local_offsets`` also slides ``win``-second windows
       along the trace and reports each window's best offset.

    A positive ``jump_s`` means that much audio is missing (a cut); a
    negative one means material was inserted or repeated. An edit closer
    than ``min_seg`` s to either end of the clip cannot be found.
    """
    t = np.asarray(trace, dtype=np.float64)
    lock = int(lock_index)
    anchor = {"given": lock, "used": lock}
    if relock_radius:
        anchor = _anchor(t, ref_values, ref_valid, lock, int(relock_radius), max_shift)
        lock = anchor["used"]
    found: list = []
    _segment(t, ref_values, ref_valid, lock, 0, t.size, max_shift=max_shift,
             min_seg=min_seg, step=step, min_jump=min_jump, r_min=r_min,
             min_gain=min_gain, depth=max_edits, out=found)
    found.sort(key=lambda e: e["frame"])
    edits = [{
        "trace_frame": e["frame"],
        "clip_time_s": float(offset_s + e["frame"] * hop_s),
        "jump_s": e["jump_s"],
        "kind": "cut" if e["jump_s"] > 0 else "insertion",
        **{k: e[k] for k in ("offset_before_s", "offset_after_s", "r_before", "r_after",
                             "residual_drop")},
    } for e in found]
    return {"edits": edits, "anchor": anchor,
            "windows": local_offsets(t, ref_values, ref_valid, lock, win=win, step=step,
                                     max_shift=max_shift),
            "params": dict(max_shift=max_shift, min_seg=min_seg, step=step,
                           min_jump=min_jump, r_min=r_min, min_gain=min_gain,
                           max_edits=max_edits, relock_radius=relock_radius)}
