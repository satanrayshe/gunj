"""M4: reference-only uniqueness curve (PREP section 6).

Question: how long must an excerpt of India's grid frequency be before a
search of the reference finds it again, uniquely, at each search scope?

No audio is used. Each trial cuts an excerpt out of the real reference
series, adds SYNTHETIC Gaussian noise to the 1-Hz trace, and searches the
reference for it with the same normalised cross-correlation as gunj.match.
Everything about the noise is synthetic; the excerpts are real reference data.

Design
------
- Excerpt starts: for each length, drawn uniformly from all start seconds
  whose window has >= 90% valid seconds (rejection sampling, fixed seed).
  Missing seconds inside an excerpt are filled by linear interpolation, as
  gunj.match.ref_segment does for the reference.
- Lengths: 5, 10, 20, 40, 60 min.
- Noise (SYNTHETIC): one standard normal vector per (length, excerpt) is
  shared by every noise condition (common random numbers), so the levels
  differ only in scale.
    white  : sigma * z
    hann16 : z smoothed by the probe extractor's 16 s Hann window, rescaled
             to unit variance, times sigma. Closer to real extractor error,
             which is correlated over about 16 s.
  Levels are chosen to bracket the probe's equivalent noise
  (results/probe_noise.json, scripts/probe_noise.py).
- Scopes: full span; +-90 days around the truth; +-1 day around the truth
  (clipped at the ends of the series).
- Nested excerpts: excerpt j is the same in every scope, so the scopes are
  compared on the same queries. The cheap scopes get more excerpts.
- Hit: top-1 start within 30 s of the truth. Margin: r1 - r2, where r2 is the
  best local peak at least 60 s from the top one (gunj.match's definition).

Search
------
``batched_search`` is gunj.match.ncc_search for many same-length queries at
once: each reference block is read, FFT'd and its window statistics computed
once, then every query is correlated against it (threads). Same block layout,
same exclusions (90% valid, 0.003 Hz std floor), same local-peak candidates.
One shortcut: a block's peak search is skipped for a query when the block's
best r is below that query's current 5th candidate; this cannot change the
top two candidates (see _maybe_skip). ``selfcheck`` compares it against
ncc_search on real excerpts and must match exactly.

Usage (from the repo root)
--------------------------
  .venv\\Scripts\\python.exe scripts\\uniqueness.py selfcheck --ref-dir ..\\data\\derived --out-dir results
  .venv\\Scripts\\python.exe scripts\\uniqueness.py bench     --ref-dir ..\\data\\derived
  .venv\\Scripts\\python.exe scripts\\uniqueness.py run       --ref-dir ..\\data\\derived --out-dir results
  .venv\\Scripts\\python.exe scripts\\uniqueness.py report    --out-dir results

``run`` is resumable: finished groups in uniqueness_trials.csv are skipped.
"""
from __future__ import annotations

import gunj  # noqa: F401  (first, so its OPENBLAS_NUM_THREADS default applies)
import argparse
import ctypes
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import scipy.fft as sfft
from scipy.ndimage import maximum_filter1d

from gunj import reference as gref
from gunj.match import (DEFAULT_NFFT, MIN_REF_STD_HZ, MIN_VALID, NOMINAL_HZ, RUNNER_UP_SEP_S,
                        _prep_query, _select, _sliding_sums, fisher_margin, ncc_search,
                        ref_segment)

SEED = 20261107
LENGTHS_S = (300, 600, 1200, 2400, 3600)
HIT_TOL_S = 30
SCOPES = ("1d", "90d", "full")
SCOPE_DAYS = {"1d": 1, "90d": 90}
HANN_S = 16
# (kind, sigma Hz). 0 is the noise-free control (shared by both kinds).
CONDITIONS = (("white", 0.0), ("white", 0.010), ("white", 0.020), ("white", 0.040),
              ("white", 0.060), ("hann16", 0.010), ("hann16", 0.020), ("hann16", 0.040),
              ("hann16", 0.060))
K_SELECT = 5
UNIQ_NFFT = 1 << 16   # small blocks stay in cache; ~4x faster than 2**21 here
TRIAL_COLS = ["scope", "length_s", "noise_kind", "sigma_hz", "excerpt_j", "truth_index",
              "truth_ist", "valid_share", "ref_window_std_hz", "top_index", "top_error_s", "hit",
              "r1", "runner_up_index", "r2", "margin", "confidence", "r_at_truth",
              "r_max_within_30s", "n_positions", "scope_start", "scope_stop", "noise_label"]
NOISE_LABEL = "SYNTHETIC noise added to real reference excerpt"


# --------------------------------------------------------------------------
# memory
# --------------------------------------------------------------------------

def peak_memory_mb() -> dict:
    """Peak working set and peak private bytes of this process (Windows)."""
    if sys.platform != "win32":
        import resource
        return {"peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}
    from ctypes import wintypes

    class PMC(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    k32 = ctypes.WinDLL("kernel32")
    psapi = ctypes.WinDLL("psapi")
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    c = PMC()
    c.cb = ctypes.sizeof(PMC)
    if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
        return {"error": "GetProcessMemoryInfo failed"}
    return {"peak_working_set_mb": round(c.PeakWorkingSetSize / 2 ** 20, 1),
            "peak_private_mb": round(c.PeakPagefileUsage / 2 ** 20, 1),
            "private_now_mb": round(c.PagefileUsage / 2 ** 20, 1)}


# --------------------------------------------------------------------------
# batched search (same maths as gunj.match.ncc_search)
# --------------------------------------------------------------------------

class _QState:
    __slots__ = ("x", "truth", "cidx", "cr", "thr", "r_truth", "r_truth30", "skipped")

    def __init__(self, x, truth):
        self.x = x
        self.truth = truth
        self.cidx: list = []
        self.cr: list = []
        self.thr = -np.inf
        self.r_truth = -np.inf
        self.r_truth30 = -np.inf
        self.skipped = 0


def _maybe_skip(rc_max: float, thr: float) -> bool:
    """Skip a block's peak search when its best r is below the query's current
    5th selected candidate. Why this is safe for the top two: the 5 current
    picks are >= sep apart and all beat every value in the block. In the final
    greedy selection each pick is either kept or removed by a higher candidate
    within sep of it, and one candidate can remove at most 2 picks. So at
    least 3 final picks beat the block, and the block cannot reach the top 2."""
    return rc_max < thr


def _ref_block(ref_values, ref_valid, n, p0, p1, c0, core, sep, min_valid, std_floor2):
    """Reference side of one block: FFT and window statistics (as ncc_search)."""
    c1 = min(c0 + core, p1)
    e0, e1 = max(p0, c0 - sep), min(p1, c1 + sep)
    npos = e1 - e0
    m = npos + n - 1
    y, ok = ref_segment(ref_values, ref_valid, e0, e0 + m)
    fl = sfft.next_fast_len(m, real=True)
    Y = sfft.rfft(y, fl)
    s1 = _sliding_sums(y, n, npos)
    s2 = _sliding_sums(y * y, n, npos)
    var = s2 - s1 * s1 / n
    frac = _sliding_sums(ok.astype(np.float64), n, npos) / n
    bad = (frac < min_valid) | (var < std_floor2)
    den = np.sqrt(np.maximum(var, 1e-300))
    lo, hi = c0 - e0, c1 - e0
    return dict(c0=c0, c1=c1, e0=e0, e1=e1, npos=npos, fl=fl, Y=Y, den=den, bad=bad, lo=lo, hi=hi,
                off=e0 - (c0 - sep), n_ok=int((~bad[lo:hi]).sum()))


def _query_block(st, blk, spec, sep, keep, k):
    """Query side of one block for one query: r, truth bookkeeping, local peaks."""
    c0, c1, e0, e1, npos, lo, hi, off = (blk[x] for x in ("c0", "c1", "e0", "e1", "npos", "lo", "hi", "off"))
    corr = sfft.irfft(blk["Y"] * spec, blk["fl"])[:npos]
    r = corr / blk["den"]
    del corr
    r[blk["bad"]] = -np.inf
    t = st.truth
    if c0 <= t < c1:
        st.r_truth = float(r[t - e0])
    a, b = max(t - HIT_TOL_S, e0), min(t + HIT_TOL_S + 1, e1)
    if a < b:
        st.r_truth30 = max(st.r_truth30, float(r[a - e0:b - e0].max()))
    rc = r[lo:hi]
    if rc.size == 0:
        return
    if _maybe_skip(float(rc.max()), st.thr):
        st.skipped += 1
        return
    padded = np.full((c1 - c0) + 2 * sep, -np.inf)
    padded[off:off + npos] = r
    del r
    mx = maximum_filter1d(padded, size=2 * sep + 1, mode="constant", cval=-np.inf)
    core_pad = padded[sep:sep + (c1 - c0)]
    is_peak = (core_pad == mx[sep:sep + (c1 - c0)]) & np.isfinite(core_pad)
    pk = np.flatnonzero(is_peak)
    if pk.size > keep:
        pk = pk[np.argpartition(-core_pad[pk], keep - 1)[:keep]]
    st.cidx.append(pk + c0)
    st.cr.append(core_pad[pk].copy())
    chosen = _select(np.concatenate(st.cidx), np.concatenate(st.cr), k, sep)
    if len(chosen) >= k:
        st.thr = chosen[k - 1].r


def batched_search(queries, truths, ref_values, ref_valid, start, stop, *, k=K_SELECT,
                   sep=RUNNER_UP_SEP_S, min_valid=MIN_VALID, min_ref_std=MIN_REF_STD_HZ,
                   nfft=UNIQ_NFFT, group=32, threads=4, pool=None):
    """Search several same-length queries over reference positions [start, stop).

    Same maths and block layout as gunj.match.ncc_search called with the same
    ``nfft``. Blocks are handled ``group`` at a time: the reference side of
    the group's blocks is computed in parallel, then each query (in parallel)
    runs through the group's blocks in order.

    Returns one dict per query: top index and r, runner-up index and r, margin,
    confidence, r at the truth position and the best r within +-30 s of it,
    and the number of admissible positions.
    """
    states = [_QState(_prep_query(q), int(t)) for q, t in zip(queries, truths)]
    n = states[0].x.size
    if any(s.x.size != n for s in states):
        raise ValueError("queries must share one length")
    n_ref = int(len(ref_values))
    p0 = max(0, int(start))
    p1 = min(n_ref if stop is None else int(stop), n_ref - n + 1)
    if p1 <= p0:
        raise ValueError("empty scope")
    nfft = max(int(nfft), 1 << int(np.ceil(np.log2(4 * (n + 2 * sep)))))
    core = nfft - (n - 1) - 2 * sep
    keep = max(64, 4 * (k + 2))
    std_floor2 = (min_ref_std ** 2) * n
    xspec: dict = {}
    n_ok = 0
    own_pool = pool is None
    if own_pool:
        pool = ThreadPoolExecutor(max_workers=threads)
    try:
        c0s = list(range(p0, p1, core))
        for g0 in range(0, len(c0s), group):
            blks = list(pool.map(lambda c0: _ref_block(ref_values, ref_valid, n, p0, p1, c0, core, sep,
                                                       min_valid, std_floor2), c0s[g0:g0 + group]))
            need = {b["fl"] for b in blks}
            for fl in [f for f in xspec if f not in need]:
                del xspec[fl]
            for fl in need - set(xspec):
                xspec[fl] = list(pool.map(lambda st, fl=fl: np.conj(sfft.rfft(st.x, fl)), states))
            n_ok += sum(b["n_ok"] for b in blks)

            def work(qi, blks=blks):
                st = states[qi]
                for blk in blks:
                    _query_block(st, blk, xspec[blk["fl"]][qi], sep, keep, k)

            list(pool.map(work, range(len(states))))
            del blks
    finally:
        if own_pool:
            pool.shutdown()

    out = []
    for st in states:
        idx = np.concatenate(st.cidx) if st.cidx else np.empty(0, dtype=np.int64)
        rr = np.concatenate(st.cr) if st.cr else np.empty(0)
        chosen = _select(idx, rr, max(k, 2), sep)
        top = chosen[0] if chosen else None
        run = chosen[1] if len(chosen) > 1 else None
        out.append(dict(
            top_index=None if top is None else top.index,
            r1=None if top is None else top.r,
            runner_up_index=None if run is None else run.index,
            r2=None if run is None else run.r,
            margin=None if run is None else top.r - run.r,
            confidence=None if run is None else fisher_margin(top.r, run.r),
            r_at_truth=st.r_truth if np.isfinite(st.r_truth) else None,
            r_max_within_30s=st.r_truth30 if np.isfinite(st.r_truth30) else None,
            n_positions=n_ok, scope_start=p0, scope_stop=p1, blocks_skipped=st.skipped))
    return out


# --------------------------------------------------------------------------
# excerpts and noise
# --------------------------------------------------------------------------

class ValidCounter:
    """Valid share of a window, read straight from the (memory-mapped) mask."""

    def __init__(self, valid):
        self.valid = valid

    def frac(self, a, n):
        return int(np.count_nonzero(self.valid[a:a + n])) / n


def excerpt_starts(counter: ValidCounter, n_ref: int, length: int, count: int, seed: int):
    """First ``count`` excerpt starts for one length (deterministic, nested:
    asking for more excerpts never changes the earlier ones)."""
    rng = np.random.default_rng([seed, length])
    starts = []
    tries = 0
    while len(starts) < count:
        a = int(rng.integers(0, n_ref - length + 1))
        tries += 1
        if counter.frac(a, length) >= MIN_VALID:
            starts.append(a)
    return starts, tries


def base_noise(seed: int, length: int, j: int) -> np.ndarray:
    """Standard normal vector shared by every noise condition of one excerpt."""
    return np.random.default_rng([seed, length, j, 1]).standard_normal(length + HANN_S - 1)


def make_noise(z: np.ndarray, length: int, kind: str, sigma: float) -> np.ndarray:
    if sigma == 0:
        return np.zeros(length)
    if kind == "white":
        return sigma * z[:length]
    if kind == "hann16":
        h = np.hanning(HANN_S)
        h /= np.sqrt((h * h).sum())          # unit gain in variance for white input
        return sigma * np.convolve(z, h, mode="valid")[:length]
    raise ValueError(kind)


def excerpt_values(ref, a: int, length: int):
    y, ok = ref_segment(ref.freq, ref.valid, a, a + length)
    return y + NOMINAL_HZ, float(ok.mean())


def scope_bounds(ref, scope: str, truth: int):
    if scope == "full":
        return ref.scope_full()
    return ref.scope(ref.time_of(truth), days=SCOPE_DAYS[scope])


def conditions_list():
    return [dict(kind=k, sigma=s) for k, s in CONDITIONS]


# --------------------------------------------------------------------------
# trial bookkeeping
# --------------------------------------------------------------------------

def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, (bool, np.bool_)):
        return "1" if v else "0"
    if isinstance(v, float):
        return repr(round(v, 9))
    return str(v)


def load_done(trials_path: Path, n_cond: int):
    """Finished (scope, length, j) groups. Drops incomplete groups from the file."""
    import pandas as pd
    if not trials_path.exists():
        return set()
    df = pd.read_csv(trials_path)
    if df.empty:
        return set()
    sizes = df.groupby(["scope", "length_s", "excerpt_j"]).size()
    full = sizes[sizes == n_cond].index
    done = set((s, int(L), int(j)) for s, L, j in full)
    if len(full) != len(sizes) or len(df) != len(full) * n_cond:
        keep = df.set_index(["scope", "length_s", "excerpt_j"]).index.isin(full)
        df = df[keep].drop_duplicates(["scope", "length_s", "excerpt_j", "noise_kind", "sigma_hz"])
        tmp = trials_path.with_suffix(".csv.tmp")
        df.to_csv(tmp, index=False)
        os.replace(tmp, trials_path)
    return done


def append_rows(trials_path: Path, rows):
    new = not trials_path.exists()
    lines = []
    if new:
        lines.append(",".join(TRIAL_COLS))
    for r in rows:
        lines.append(",".join(_fmt(r[c]) for c in TRIAL_COLS))
    with open(trials_path, "a", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines) + "\n")
        f.flush()
        os.fsync(f.fileno())


def make_rows(ref, scope, length, j, truth, vshare, ystd, conds, results):
    rows = []
    for c, res in zip(conds, results):
        top = res["top_index"]
        err = None if top is None else int(top) - int(truth)
        rows.append(dict(
            scope=scope, length_s=length, noise_kind=c["kind"], sigma_hz=c["sigma"], excerpt_j=j,
            truth_index=truth, truth_ist=str(ref.time_of(truth)), valid_share=round(vshare, 6),
            ref_window_std_hz=round(ystd, 6), top_index=top, top_error_s=err,
            hit=(err is not None and abs(err) <= HIT_TOL_S), r1=res["r1"],
            runner_up_index=res["runner_up_index"], r2=res["r2"], margin=res["margin"],
            confidence=res["confidence"], r_at_truth=res["r_at_truth"],
            r_max_within_30s=res["r_max_within_30s"], n_positions=res["n_positions"],
            scope_start=res["scope_start"], scope_stop=res["scope_stop"], noise_label=NOISE_LABEL))
    return rows


def build_queries(ref, a, length, j, seed, conds):
    x0, vshare = excerpt_values(ref, a, length)
    z = base_noise(seed, length, j)
    qs = [x0 + make_noise(z, length, c["kind"], c["sigma"]) for c in conds]
    return qs, vshare, float(np.std(x0))


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_selfcheck(args):
    """batched_search must equal ncc_search (top and runner-up) on real excerpts."""
    ref = gref.load(args.ref_dir, mmap=True)
    counter = ValidCounter(ref.valid)
    conds = [dict(kind="white", sigma=0.02), dict(kind="hann16", sigma=0.04),
             dict(kind="white", sigma=0.06)]
    checks = []
    plan = [(300, "1d"), (300, "90d"), (1200, "1d"), (1200, "90d"), (3600, "90d"), (600, "full")]
    t_all = time.time()
    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        for length, scope in plan:
            starts, _ = excerpt_starts(counter, len(ref), length, 2, args.seed + 999)
            for j, a in enumerate(starts):
                qs, _, _ = build_queries(ref, a, length, j, args.seed + 999, conds)
                p0, p1 = scope_bounds(ref, scope, a)
                t = time.time()
                got = batched_search(qs, [a] * len(qs), ref.freq, ref.valid, p0, p1, pool=pool)
                tb = time.time() - t
                t = time.time()
                for c, q, g in zip(conds, qs, got):
                    want = ncc_search(q, ref.freq, ref.valid, start=p0, stop=p1, k=K_SELECT, nfft=UNIQ_NFFT)
                    dflt = ncc_search(q, ref.freq, ref.valid, start=p0, stop=p1, k=K_SELECT)
                    same = (want.top.index == g["top_index"] and want.runner_up.index == g["runner_up_index"]
                            and want.top.r == g["r1"] and want.runner_up.r == g["r2"]
                            and want.n_positions == g["n_positions"])
                    same_dflt_idx = (dflt.top.index == g["top_index"]
                                     and dflt.runner_up.index == g["runner_up_index"]
                                     and dflt.n_positions == g["n_positions"])
                    checks.append(dict(length_s=length, scope=scope, excerpt_start=a, noise=c,
                                       ncc_top=want.top.index, batched_top=g["top_index"],
                                       ncc_r1=want.top.r, batched_r1=g["r1"],
                                       ncc_runner=want.runner_up.index, batched_runner=g["runner_up_index"],
                                       ncc_r2=want.runner_up.r, batched_r2=g["r2"],
                                       r1_abs_diff=abs(want.top.r - g["r1"]),
                                       r2_abs_diff=abs(want.runner_up.r - g["r2"]),
                                       n_positions_equal=want.n_positions == g["n_positions"],
                                       blocks_skipped=g["blocks_skipped"], identical=bool(same),
                                       default_nfft_same_indices=bool(same_dflt_idx),
                                       default_nfft_r1_abs_diff=abs(dflt.top.r - g["r1"]),
                                       default_nfft_r2_abs_diff=abs(dflt.runner_up.r - g["r2"])))
                tn = time.time() - t
                print(f"selfcheck L={length} scope={scope} j={j}: batched {tb:.2f}s (3 queries), "
                      f"ncc_search {tn:.2f}s; all equal: {all(c['identical'] for c in checks[-3:])}",
                      flush=True)
    out = dict(what="batched_search vs gunj.match.ncc_search on real reference excerpts with SYNTHETIC noise. "
                    "'identical' compares with ncc_search at the same block size (nfft); 'default_nfft' "
                    "compares with ncc_search at its default block size, where FFT rounding differs.",
               nfft=UNIQ_NFFT, ncc_default_nfft=DEFAULT_NFFT,
               n_checks=len(checks), n_identical=sum(c["identical"] for c in checks),
               max_r1_abs_diff=max(c["r1_abs_diff"] for c in checks),
               max_r2_abs_diff=max(c["r2_abs_diff"] for c in checks),
               n_default_nfft_same_indices=sum(c["default_nfft_same_indices"] for c in checks),
               max_default_nfft_r_abs_diff=max(max(c["default_nfft_r1_abs_diff"], c["default_nfft_r2_abs_diff"])
                                               for c in checks),
               seconds=round(time.time() - t_all, 1), memory=peak_memory_mb(), checks=checks)
    p = Path(args.out_dir) / "uniqueness_selfcheck.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, p)
    print(json.dumps({k: v for k, v in out.items() if k != "checks"}, indent=1))
    if out["n_identical"] != out["n_checks"] or out["n_default_nfft_same_indices"] != out["n_checks"]:
        raise SystemExit("selfcheck FAILED")


def cmd_bench(args):
    ref = gref.load(args.ref_dir, mmap=True)
    counter = ValidCounter(ref.valid)
    conds = conditions_list()
    res = {}
    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        for scope, length, nq in (("1d", 1200, len(conds)), ("90d", 1200, len(conds)),
                                  ("full", 1200, len(conds) * args.bench_full_excerpts)):
            starts, _ = excerpt_starts(counter, len(ref), length, max(1, nq // len(conds)), args.seed + 555)
            qs, ts = [], []
            for j, a in enumerate(starts):
                q, _, _ = build_queries(ref, a, length, j, args.seed + 555, conds)
                qs += q
                ts += [a] * len(q)
            p0, p1 = scope_bounds(ref, scope, starts[0])
            t = time.time()
            batched_search(qs, ts, ref.freq, ref.valid, p0, p1, pool=pool)
            dt = time.time() - t
            res[scope] = dict(n_queries=len(qs), seconds=round(dt, 2), per_query_s=round(dt / len(qs), 3))
            print(scope, res[scope], peak_memory_mb(), flush=True)
    print(json.dumps(res, indent=1))


def cmd_run(args):
    ref = gref.load(args.ref_dir, mmap=True)
    out_dir = Path(args.out_dir)
    trials_path = out_dir / "uniqueness_trials.csv"
    conds = conditions_list()
    done = load_done(trials_path, len(conds))
    counter = ValidCounter(ref.valid)
    n_plan = {"1d": args.n_1d, "90d": args.n_90d, "full": args.n_full}
    budgets = {"1d": args.budget_1d_min * 60, "90d": args.budget_90d_min * 60,
               "full": args.budget_full_min * 60}
    pools = {}
    tries = {}
    for L in LENGTHS_S:
        pools[L], tries[L] = excerpt_starts(counter, len(ref), L, max(n_plan.values()), args.seed)
    del counter
    run_meta_path = out_dir / "uniqueness_run.json"
    meta = json.loads(run_meta_path.read_text(encoding="utf-8")) if run_meta_path.exists() else {}
    meta.update(dict(seed=args.seed, lengths_s=list(LENGTHS_S), conditions=conds, hit_tol_s=HIT_TOL_S,
                     n_planned=n_plan, budgets_min={k: v / 60 for k, v in budgets.items()},
                     threads=args.threads, sampling_tries=tries,
                     reference=dict(t0_ist=str(ref.t0), length=len(ref))))
    meta.setdefault("phases", {})
    log = lambda s: print(time.strftime("%H:%M:%S"), s, flush=True)  # noqa: E731

    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        # ---- +-1 d and +-90 d: one excerpt (all noise conditions) per search ----
        for scope in ("1d", "90d"):
            if scope not in args.phases:
                continue
            t_phase = time.time()
            n_new = 0
            stop_reason = "planned N reached"
            for j in range(n_plan[scope]):
                if time.time() - t_phase > budgets[scope]:
                    stop_reason = f"time budget reached at excerpt {j}"
                    break
                for L in LENGTHS_S:
                    if (scope, L, j) in done:
                        continue
                    a = pools[L][j]
                    qs, vshare, ystd = build_queries(ref, a, L, j, args.seed, conds)
                    p0, p1 = scope_bounds(ref, scope, a)
                    res = batched_search(qs, [a] * len(qs), ref.freq, ref.valid, p0, p1, pool=pool)
                    append_rows(trials_path, make_rows(ref, scope, L, j, a, vshare, ystd, conds, res))
                    done.add((scope, L, j))
                    n_new += 1
                if j % 25 == 0:
                    log(f"{scope}: excerpt {j} done, {time.time() - t_phase:.0f}s, {peak_memory_mb()}")
            meta["phases"][scope] = dict(seconds_this_run=round(time.time() - t_phase, 1),
                                         groups_added=n_new, stop_reason=stop_reason,
                                         memory=peak_memory_mb())
            _write_json(run_meta_path, meta)
            log(f"{scope} finished: {meta['phases'][scope]}")

        # ---- full span: rounds of excerpts, each length one pass per round ----
        if "full" in args.phases:
            t_phase = time.time()
            n_new = 0
            stop_reason = "planned N reached"
            B = args.batch_full
            for r0 in range(0, n_plan["full"], B):
                if time.time() - t_phase > budgets["full"]:
                    stop_reason = f"time budget reached before round starting at excerpt {r0}"
                    break
                for L in LENGTHS_S:
                    js = [j for j in range(r0, min(r0 + B, n_plan["full"])) if ("full", L, j) not in done]
                    if not js:
                        continue
                    t = time.time()
                    qs, ts, info = [], [], []
                    for j in js:
                        a = pools[L][j]
                        q, vshare, ystd = build_queries(ref, a, L, j, args.seed, conds)
                        qs += q
                        ts += [a] * len(q)
                        info.append((j, a, vshare, ystd))
                    res = batched_search(qs, ts, ref.freq, ref.valid, 0, len(ref), pool=pool)
                    rows = []
                    for gi, (j, a, vshare, ystd) in enumerate(info):
                        rows += make_rows(ref, "full", L, j, a, vshare, ystd, conds,
                                          res[gi * len(conds):(gi + 1) * len(conds)])
                    append_rows(trials_path, rows)
                    for j, *_ in info:
                        done.add(("full", L, j))
                    n_new += len(js)
                    log(f"full: L={L} excerpts {js[0]}..{js[-1]} ({len(qs)} queries) "
                        f"{time.time() - t:.0f}s, phase {time.time() - t_phase:.0f}s, {peak_memory_mb()}")
                meta["phases"]["full"] = dict(seconds_this_run=round(time.time() - t_phase, 1),
                                              groups_added=n_new, stop_reason="running",
                                              memory=peak_memory_mb())
                _write_json(run_meta_path, meta)
            meta["phases"]["full"] = dict(seconds_this_run=round(time.time() - t_phase, 1),
                                          groups_added=n_new, stop_reason=stop_reason,
                                          memory=peak_memory_mb())
            _write_json(run_meta_path, meta)
            log(f"full finished: {meta['phases']['full']}")
    meta["memory_end"] = peak_memory_mb()
    _write_json(run_meta_path, meta)


def _write_json(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def wilson(h: int, n: int, z: float = 1.959963984540054):
    if n == 0:
        return (float("nan"), float("nan"))
    p = h / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    w = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    lo = 0.0 if h == 0 else max(0.0, c - w)
    hi = 1.0 if h == n else min(1.0, c + w)
    return (lo, hi)


def nominal_candidates(scope: str, length: int, n_ref: int) -> int:
    if scope == "full":
        return n_ref - length + 1
    return 2 * SCOPE_DAYS[scope] * 86400 + 1


def cmd_report(args):
    import pandas as pd
    out_dir = Path(args.out_dir)
    df = pd.read_csv(out_dir / "uniqueness_trials.csv")
    meta = json.loads((out_dir / "uniqueness_run.json").read_text(encoding="utf-8"))
    probe = json.loads((out_dir / "probe_noise.json").read_text(encoding="utf-8"))
    selfcheck = json.loads((out_dir / "uniqueness_selfcheck.json").read_text(encoding="utf-8"))
    n_ref = int(meta["reference"]["length"])
    window = 2 * HIT_TOL_S + 1

    rows = []
    for (scope, L, kind, sig), g in df.groupby(["scope", "length_s", "noise_kind", "sigma_hz"]):
        n = len(g)
        h = int(g["hit"].sum())
        lo, hi = wilson(h, n)
        ncand = nominal_candidates(scope, int(L), n_ref)
        rows.append(dict(
            scope=scope, length_s=int(L), length_min=int(L) // 60, noise_kind=kind,
            sigma_mhz=round(sig * 1000, 3), N=n, hits=h, hit_rate=round(h / n, 4),
            wilson95_lo=round(lo, 4), wilson95_hi=round(hi, 4),
            median_margin=round(float(g["margin"].median()), 5),
            median_margin_hits=(round(float(g.loc[g["hit"] == 1, "margin"].median()), 5) if h else None),
            median_r1=round(float(g["r1"].median()), 4),
            median_r_max_within_30s=round(float(g["r_max_within_30s"].median()), 4),
            median_abs_error_misses_s=(round(float(g.loc[g["hit"] == 0, "top_error_s"].abs().median()), 0)
                                       if h < n else None),
            nominal_candidates=ncand,
            mean_admissible_positions=round(float(g["n_positions"].mean()), 0),
            random_guess_hit_rate=float(f"{window / ncand:.4g}"),
            noise_label=NOISE_LABEL))
    order = {s: i for i, s in enumerate(("full", "90d", "1d"))}
    kind_order = {"white": 0, "hann16": 1}
    tab = pd.DataFrame(rows)
    tab = tab.sort_values(by=["scope", "noise_kind", "sigma_mhz", "length_s"],
                          key=lambda s: s.map(order) if s.name == "scope" else
                          (s.map(kind_order) if s.name == "noise_kind" else s)).reset_index(drop=True)
    tmp = out_dir / "uniqueness_table.csv.tmp"
    tab.to_csv(tmp, index=False)
    os.replace(tmp, out_dir / "uniqueness_table.csv")

    # consistency: on shared excerpts a full-span hit must also hit in 90d and 1d
    key = ["length_s", "noise_kind", "sigma_hz", "excerpt_j"]
    piv = df.pivot_table(index=key, columns="scope", values="hit", aggfunc="first")
    viol = {}
    for wide, narrow in (("full", "90d"), ("90d", "1d"), ("full", "1d")):
        if wide in piv and narrow in piv:
            both = piv[[wide, narrow]].dropna()
            viol[f"{wide}_hit_but_{narrow}_miss"] = int(((both[wide] == 1) & (both[narrow] == 0)).sum())
            viol[f"pairs_{wide}_{narrow}"] = int(len(both))

    # 90% lengths
    need = []
    for (scope, kind, sig), g in tab.groupby(["scope", "noise_kind", "sigma_mhz"]):
        g = g.sort_values("length_s")
        pt = g[g["hit_rate"] >= 0.9]
        lb = g[g["wilson95_lo"] >= 0.9]
        first_pt = int(pt["length_min"].iloc[0]) if len(pt) else None
        prev = None
        if first_pt is not None:
            below = g[(g["length_min"] < first_pt)]
            prev = int(below["length_min"].iloc[-1]) if len(below) else None
        need.append(dict(scope=scope, noise_kind=kind, sigma_mhz=sig,
                         first_len_min_point=first_pt, previous_len_min=prev,
                         first_len_min_lower_bound=int(lb["length_min"].iloc[0]) if len(lb) else None,
                         rates=" / ".join(f"{int(m)}m {h}/{n}" for m, h, n in
                                          zip(g["length_min"], g["hits"], g["N"]))))
    need = pd.DataFrame(need)
    need["o"] = need["scope"].map(order)
    need["k"] = need["noise_kind"].map(kind_order)
    need = need.sort_values(["o", "k", "sigma_mhz"]).drop(columns=["o", "k"])

    _plot(tab, out_dir / "uniqueness_curve.png", probe)
    bs = out_dir / "reference_build_stats.json"
    junctions = json.loads(bs.read_text(encoding="utf-8")).get("junctions") if bs.exists() else None
    _write_md(out_dir / "uniqueness.md", tab, need, viol, meta, probe, selfcheck, df, n_ref, junctions)
    print(tab.to_string())
    print(need.to_string())
    print(viol)


def _plot(tab, path, probe):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {"full": "#2a78d6", "90d": "#eb6834", "1d": "#1baf7a"}
    markers = {"full": "o", "90d": "s", "1d": "^"}
    styles = {"full": "-", "90d": "--", "1d": ":"}
    names = {"full": "full span (972 d)", "90d": "±90 days", "1d": "±1 day"}
    conds = [("white", 0.0), ("white", 10.0), ("white", 20.0), ("white", 40.0), ("white", 60.0),
             (None, None), ("hann16", 10.0), ("hann16", 20.0), ("hann16", 40.0), ("hann16", 60.0)]
    fig, axes = plt.subplots(2, 5, figsize=(17, 7.6), sharex=True, sharey=True)
    lens = sorted(tab["length_min"].unique())
    dodge = {"full": 0.94, "90d": 1.0, "1d": 1.06}
    handles = {}
    for ax, (kind, sig) in zip(axes.flat, conds):
        ax.set_facecolor("#fcfcfb")
        if kind is None:
            ax.axis("off")
            pr = probe["summary"]
            ax.text(0.02, 0.95,
                    "Noise is SYNTHETIC Gaussian,\nadded to real reference excerpts.\n\n"
                    "white: independent each second.\nhann16: smoothed by the probe's\n"
                    "16 s Hann window (closer to\nreal extractor error).\n\n"
                    f"Probe-implied equivalent noise\n(results/probe_noise.json):\n"
                    f"median {pr['sigma_eff_all_median_hz'] * 1000:.1f} mHz, range "
                    f"{pr['sigma_eff_all_min_hz'] * 1000:.1f}-{pr['sigma_eff_all_max_hz'] * 1000:.1f} mHz.\n\n"
                    "Bars: Wilson 95% intervals.\nHit: top-1 within 30 s of truth.",
                    transform=ax.transAxes, va="top", ha="left", fontsize=9, color="#52514e")
            continue
        for scope in ("full", "90d", "1d"):
            g = tab[(tab["scope"] == scope) & (tab["sigma_mhz"] == sig) &
                    ((tab["noise_kind"] == kind) | (tab["sigma_mhz"] == 0))]
            g = g.drop_duplicates("length_min").sort_values("length_min")
            if g.empty:
                continue
            # small horizontal dodge (log axis) so the three scopes' intervals do not overlap
            x = g["length_min"].to_numpy(dtype=float) * dodge[scope]
            yv = g["hit_rate"].to_numpy()
            err = np.vstack([yv - g["wilson95_lo"].to_numpy(), g["wilson95_hi"].to_numpy() - yv])
            nmin, nmax = int(g["N"].min()), int(g["N"].max())
            ns = f"N={nmin}" if nmin == nmax else f"N={nmin}-{nmax}"
            ax.errorbar(x, yv, yerr=err, color=colors[scope], marker=markers[scope], ms=6,
                        ls=styles[scope], lw=2, capsize=2.5, elinewidth=1.2,
                        markeredgecolor="#fcfcfb", markeredgewidth=1.2,
                        label=f"{names[scope]} ({ns} per point)")
            handles[scope] = ax.get_legend_handles_labels()
        ax.axhline(0.9, color="#52514e", lw=1, ls=(0, (4, 3)))
        ax.set_xscale("log")
        ax.set_xticks(lens)
        ax.set_xticklabels([str(v) for v in lens])
        ax.minorticks_off()
        ax.set_ylim(-0.03, 1.03)
        ax.grid(True, color="#e6e5e1", lw=0.6)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        title = "no noise" if sig == 0 else f"{kind}, σ = {sig:g} mHz"
        ax.set_title(title, fontsize=10.5, color="#0b0b0b")
    axes[0, 0].text(lens[0] * 0.9, 0.915, "90%", fontsize=8, color="#52514e")
    axes[0, 0].set_ylabel("top-1 hit rate (±30 s)")
    axes[1, 1].tick_params(labelleft=True)
    axes[1, 1].set_ylabel("top-1 hit rate (±30 s)")
    if handles:
        hl = list(handles.values())[-1]
        fig.legend(hl[0], hl[1], loc="upper center", ncol=3, frameon=False, fontsize=9.5,
                   bbox_to_anchor=(0.5, 0.955))
    for ax in axes[1, 1:]:
        ax.set_xlabel("excerpt length (min)")
    fig.suptitle("M4 uniqueness: how often the true start ranks first, by excerpt length and search scope "
                 "(real reference, SYNTHETIC noise)", fontsize=12, color="#0b0b0b")
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    tmp = path.with_suffix(".tmp.png")
    fig.savefig(tmp, dpi=130, facecolor="#fcfcfb")
    plt.close(fig)
    os.replace(tmp, path)


def _write_md(path, tab, need, viol, meta, probe, selfcheck, df, n_ref, junctions=None):
    pr = probe["summary"]
    ph = meta.get("phases", {})
    L = []
    w = L.append
    w("# M4: uniqueness curve (reference only, SYNTHETIC noise)")
    w("")
    w("Generated by `scripts/uniqueness.py report` from `results/uniqueness_trials.csv`, "
      "`results/uniqueness_run.json`, `results/probe_noise.json` and `results/uniqueness_selfcheck.json`. "
      "Every number below is copied from those files by the script.")
    w("")
    w("## What was done")
    w("")
    w(f"- Excerpts are cut from the real one-second reference (Grid-India NERLDC PMU via Mendeley "
      f"bvwh9d6227, {n_ref:,} s from {meta['reference']['t0_ist']} IST). No audio is used.")
    w(f"- Excerpt starts are drawn uniformly from all windows with at least 90% valid seconds, "
      f"with seed {meta['seed']}. Lengths: {', '.join(str(x // 60) for x in meta['lengths_s'])} min. "
      "Missing seconds inside an excerpt are linearly interpolated.")
    w("- SYNTHETIC Gaussian noise is added to the 1-Hz trace. Two kinds:")
    w("  - `white`: independent each second.")
    w("  - `hann16`: the same normal draws smoothed by the probe extractor's 16 s Hann window and "
      "rescaled to the same standard deviation. Real extractor error is correlated over about 16 s, "
      "so this kind is the closer stand-in. For the same sigma it is harder for the matcher.")
    w("  - One normal vector per excerpt is shared by all noise conditions, so the levels differ only in scale.")
    w(f"- Noise levels: 0 (control), 10, 20, 40 and 60 mHz. They bracket the probe's equivalent noise "
      f"(`results/probe_noise.json`, from `scripts/probe_noise.py`): median {pr['sigma_eff_all_median_hz'] * 1000:.1f} mHz, "
      f"range {pr['sigma_eff_all_min_hz'] * 1000:.1f} to {pr['sigma_eff_all_max_hz'] * 1000:.1f} mHz over "
      f"{pr['n_all_points']} probe excerpts (median {pr['sigma_eff_raw_median_hz'] * 1000:.1f} mHz over the "
      f"{pr['n_raw_reference_points']} raw-reference points).")
    w("  - How: the probe printed r at the true lock for several excerpts of one stream. If the trace is the grid "
      "frequency plus independent noise, r = s / sqrt(s^2 + sigma^2), where s is the reference's standard deviation "
      "over the matched window. So sigma = s * sqrt(1/r^2 - 1). s was measured on the series at each excerpt's "
      f"matched window (file IV labels shifted by the {probe['alignment']['file_iv_minus_series_label_offset_s']} s "
      "IV-to-V label offset from `results/reference_build_stats.json`).")
    w("  - This sigma lumps together extractor noise, its smoothing and alignment error. It is one stream only.")
    raw = {(o["excerpt_length_s"], o["excerpt_start_audio_s"]): o for o in probe["rows"] if o["reference"] == "raw"}
    pairs = [(o, raw[(o["excerpt_length_s"], o["excerpt_start_audio_s"])]) for o in probe["rows"]
             if o["reference"] == "smoothed" and (o["excerpt_length_s"], o["excerpt_start_audio_s"]) in raw]
    if pairs:
        w("  - Smoothing the reference like the extractor barely changed the probe's r: "
          + "; ".join(f"{o['excerpt_length_s'] // 60} min from second {o['excerpt_start_audio_s']}: "
                      f"{r['r_printed']:g} raw vs {o['r_printed']:g} smoothed" for o, r in pairs)
          + ". So most of sigma is extraction error, not the extractor's smoothing of the true wander.")
    w("- Scopes: the full span; +-90 days around the truth; +-1 day around the truth (clipped at the series ends).")
    w("- Search: the same normalised cross-correlation as `gunj.match.ncc_search` (90% valid windows, 0.003 Hz "
      "std floor, local peaks 60 s apart), batched so each reference block is transformed once for many queries. "
      f"Block size {selfcheck['nfft']:,} positions. Self-check (`results/uniqueness_selfcheck.json`): against "
      f"`ncc_search` at the same block size, {selfcheck['n_identical']} of {selfcheck['n_checks']} searches gave the "
      f"same top and runner-up with identical r (max difference {selfcheck['max_r1_abs_diff']:.1e}); against "
      f"`ncc_search` at its default block size ({selfcheck['ncc_default_nfft']:,}), "
      f"{selfcheck['n_default_nfft_same_indices']} of {selfcheck['n_checks']} gave the same top and runner-up "
      f"(max r difference {selfcheck['max_default_nfft_r_abs_diff']:.1e}, FFT rounding).")
    w(f"- Hit: top-1 start within {meta['hit_tol_s']} s of the truth. Margin: r1 - r2, with r2 the best local "
      "peak at least 60 s from the top one. Wilson 95% intervals.")
    w("- Nesting: excerpt j is the same excerpt (and the same noise) in every scope. The cheap scopes get more "
      "excerpts than the full span.")
    w("")
    w("Run size (from `results/uniqueness_run.json`):")
    w("")
    for s in ("1d", "90d", "full"):
        sub = df[df["scope"] == s]
        if sub.empty:
            continue
        ns = sub.groupby("length_s")["excerpt_j"].nunique()
        p = ph.get(s, {})
        w(f"- {s}: planned {meta['n_planned'][s]} excerpts per length; done "
          f"{', '.join(f'{int(k) // 60} min: {int(v)}' for k, v in ns.items())}. "
          f"Stop reason: {p.get('stop_reason', 'n/a')}. Compute this run: {p.get('seconds_this_run', 'n/a')} s.")
    mem = meta.get("memory_end", {})
    w(f"- Peak memory of the run process: {mem.get('peak_private_mb', 'n/a')} MB private, "
      f"{mem.get('peak_working_set_mb', 'n/a')} MB working set (includes memory-mapped reference pages).")
    w("")
    w("## Summary at the probe-like noise level")
    w("")
    w("Shortest tested length with a hit rate of at least 90% (point estimate), at 40 mHz SYNTHETIC noise, "
      "the tested level nearest the probe's median equivalent noise:")
    w("")
    for _, r in need[need["sigma_mhz"] == 40.0].iterrows():
        pt = (f"{int(r['first_len_min_point'])} min" if pd_notna(r["first_len_min_point"])
              else "not reached at 60 min")
        w(f"- {r['scope']}, {r['noise_kind']}: {pt} (hits per length: {r['rates']}).")
    w("")
    w("## Length needed for 90% unique locks")
    w("")
    w("'Point' is the shortest tested length whose hit rate is at least 90%. 'Lower bound' is the shortest whose "
      "Wilson 95% lower bound is at least 90%. Only 5, 10, 20, 40 and 60 min were tested, so the true length lies "
      "between the previous tested length and the one shown.")
    w("")
    w("| scope | noise | sigma (mHz) | point | previous tested | lower bound | hits per length |")
    w("|---|---|---|---|---|---|---|")
    for _, r in need.iterrows():
        pt = f"{r['first_len_min_point']} min" if pd_notna(r["first_len_min_point"]) else "not reached at 60 min"
        pv = f"{int(r['previous_len_min'])} min" if pd_notna(r["previous_len_min"]) else "-"
        lb = f"{int(r['first_len_min_lower_bound'])} min" if pd_notna(r["first_len_min_lower_bound"]) else "not reached at 60 min"
        kind = "none" if r["sigma_mhz"] == 0 else r["noise_kind"]
        w(f"| {r['scope']} | {kind} | {r['sigma_mhz']:g} | {pt} | {pv} | {lb} | {r['rates']} |")
    w("")
    w("## Random-guess baseline")
    w("")
    w(f"A guess lands within {meta['hit_tol_s']} s of the truth if it falls in a {2 * meta['hit_tol_s'] + 1} s "
      "window. Its hit rate is that window over the number of candidate start seconds.")
    w("")
    w("| scope | candidate start seconds (nominal) | random-guess hit rate | mean admissible positions in trials |")
    w("|---|---|---|---|")
    for s in ("full", "90d", "1d"):
        g = tab[tab["scope"] == s]
        if g.empty:
            continue
        for _, r in g.drop_duplicates("length_s").iterrows():
            if s != "full" and r["length_s"] != g["length_s"].min():
                continue
            lab = f"{s}, {r['length_min']} min" if s == "full" else s
            w(f"| {lab} | {int(r['nominal_candidates']):,} | {r['random_guess_hit_rate']:.3g} | "
              f"{int(r['mean_admissible_positions']):,} |")
    w("")
    w("For the full span the count is the series length minus the excerpt length plus one, so it changes "
      "only slightly with length. For +-90 d and +-1 d it is 2 x 90 x 86,400 + 1 and 2 x 86,400 + 1 (fewer "
      "near the series ends). 'Admissible' positions are those whose window passes the 90% valid and "
      "0.003 Hz checks.")
    w("")
    w("## Consistency check")
    w("")
    w("On a shared excerpt, a hit in a wider scope must also be a hit in a narrower one (the true peak is inside both). "
      + "; ".join(f"{k}: {v}" for k, v in viol.items()) + ".")
    w("")
    w("## Tables")
    w("")
    w("Full rows (N, hits, Wilson interval, median margin, median r) are in `results/uniqueness_table.csv`; "
      "per-trial rows in `results/uniqueness_trials.csv`. sigma is the SYNTHETIC noise level.")
    w("")
    lens = sorted(tab["length_min"].unique())
    conds_sorted = (tab[["noise_kind", "sigma_mhz"]].drop_duplicates()
                    .assign(k=lambda d: d["noise_kind"].map({"white": 0, "hann16": 1}))
                    .sort_values(["k", "sigma_mhz"]))
    for title, fmt in (("Hit rate, with Wilson 95% interval", lambda r: f"{r['hit_rate']:.3f} "
                        f"({r['wilson95_lo']:.3f}-{r['wilson95_hi']:.3f})"),
                       ("Median margin r1 - r2 (all trials)", lambda r: f"{r['median_margin']:.4f}")):
        w(f"### {title}")
        w("")
        w("| scope | N per cell | noise | sigma (mHz) | " + " | ".join(f"{m} min" for m in lens) + " |")
        w("|---|---|---|---|" + "---|" * len(lens))
        for scope in ("full", "90d", "1d"):
            for _, c in conds_sorted.iterrows():
                g = tab[(tab["scope"] == scope) & (tab["noise_kind"] == c["noise_kind"]) &
                        (tab["sigma_mhz"] == c["sigma_mhz"])].set_index("length_min")
                if g.empty:
                    continue
                nmin, nmax = int(g["N"].min()), int(g["N"].max())
                ns = str(nmin) if nmin == nmax else f"{nmin}-{nmax}"
                kind = "none" if c["sigma_mhz"] == 0 else c["noise_kind"]
                cells = [fmt(g.loc[m]) if m in g.index else "" for m in lens]
                w(f"| {scope} | {ns} | {kind} | {c['sigma_mhz']:g} | " + " | ".join(cells) + " |")
        w("")
    # ---- probe comparison ----
    po = probe.get("probe_outcomes", [])
    if po:
        w("## Check against the probe (one real stream)")
        w("")
        w("The 5 Nov probe ran the real 100 Hz trace of one stream through the same kind of search "
          "(`docs/probe/*_out.txt`, parsed into `results/probe_noise.json`). Its blind search covered "
          + probe.get("probe_scope_note", "") + " A probe 'hit' is rank 1 (file IV) or a lock within 30 s "
          "(+-1 day). These are a handful of excerpts from one clip, so they are a sanity check, not a measurement.")
        w("")
        w("| length (min) | probe, file IV only | M4 90d hann16 40 mHz | M4 full hann16 40 mHz | probe, +-1 day | "
          "M4 1d hann16 40 mHz | M4 1d white 40 mHz |")
        w("|---|---|---|---|---|---|---|")

        def cell(scope, kind, sig, length):
            g = tab[(tab["scope"] == scope) & (tab["noise_kind"] == kind) & (tab["sigma_mhz"] == sig) &
                    (tab["length_s"] == length)]
            return f"{int(g['hits'].iloc[0])}/{int(g['N'].iloc[0])}" if len(g) else "-"
        plen = sorted({o["length_s"] for o in po} | set(int(x) for x in tab["length_s"].unique()))
        for ln in plen:
            iv = [o for o in po if o["scope"] == "file IV only" and o["length_s"] == ln]
            cl = [o for o in po if o["scope"] == "+-1 day" and o["length_s"] == ln]
            ivs = f"{sum(o['hits'] for o in iv)}/{sum(o['n'] for o in iv)}" if iv else "-"
            cls = f"{sum(o['hits'] for o in cl)}/{sum(o['n'] for o in cl)}" if cl else "-"
            w(f"| {ln / 60:g} | {ivs} | {cell('90d', 'hann16', 40.0, ln)} | {cell('full', 'hann16', 40.0, ln)} | "
              f"{cls} | {cell('1d', 'hann16', 40.0, ln)} | {cell('1d', 'white', 40.0, ln)} |")
        w("")
    w("## Limits")
    w("")
    w("- The noise is synthetic and Gaussian. Real extraction error also has outliers, wrong-peak jumps and "
      "slow drifts, and it changes along a clip. The probe-derived level comes from one stream.")
    w("- The excerpt is the reference itself, so the true alignment is exact. Real clips also carry the "
      "time-label drift found in `results/time_axis_check.json` (up to a few minutes). That drift does not "
      "change uniqueness, but it does change whether a lock lands within 30 s of a metadata time.")
    if junctions:
        offs = [int(j["label_offset_s"]) for j in junctions]
        w(f"- At the {len(offs)} file handovers {min(offs)} to {max(offs)} s of real time are missing without a gap "
          "in the mask (`results/reference_build_stats.json`, junctions). Excerpts that span a handover are kept; "
          "they still match themselves.")
    w("- Missing seconds inside an excerpt (up to 10%) are interpolated, which makes those seconds easier to match "
      "than real audio would.")
    w("")
    path_tmp = path.with_suffix(".md.tmp")
    path_tmp.write_text("\n".join(L), encoding="utf-8")
    os.replace(path_tmp, path)


def pd_notna(v):
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("selfcheck", "bench", "run", "report"):
        p = sub.add_parser(name)
        p.add_argument("--out-dir", default="results")
        p.add_argument("--seed", type=int, default=SEED)
        p.add_argument("--threads", type=int, default=4)
        if name != "report":
            p.add_argument("--ref-dir", required=True)
        if name == "bench":
            p.add_argument("--bench-full-excerpts", type=int, default=2)
        if name == "run":
            p.add_argument("--n-1d", type=int, default=600)
            p.add_argument("--n-90d", type=int, default=200)
            p.add_argument("--n-full", type=int, default=100)
            p.add_argument("--batch-full", type=int, default=2)
            p.add_argument("--budget-1d-min", type=float, default=20)
            p.add_argument("--budget-90d-min", type=float, default=45)
            p.add_argument("--budget-full-min", type=float, default=85)
            p.add_argument("--phases", nargs="+", default=list(SCOPES), choices=SCOPES)
    args = ap.parse_args(argv)
    {"selfcheck": cmd_selfcheck, "bench": cmd_bench, "run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
