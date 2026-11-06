"""Build Gunj's one-second Indian grid frequency reference.

Source: Mendeley Data dataset bvwh9d6227 (Grid-India NERLDC PMU frequency,
IIT Guwahati), CC BY 4.0. Five CSV exports, freq_I.csv .. freq_V.csv.

Stages (run all by default, or one with --stage):
  parse     stream each CSV in chunks; drop the exporter's Java trace and any
            row whose timestamp does not parse; write per-file arrays
            (seconds, value, quality) to <derived>/_parsed; hash each file.
  assemble  build one contiguous one-second float32 series from the first to
            the last valid second, plus a uint8 validity mask and a uint8
            source-file array; compare overlapping files second by second;
            write grid_1s_*.npy and grid_1s_meta.json.
  report    gap map, monthly totals, frequency statistics, coverage plot,
            summary, and the IST/index sanity check near the 5 Nov probe lock.

Memory: every stage works in chunks or on memmaps and stays well under 1.5 GB.

Usage (from the repo root):
  .venv\\Scripts\\python.exe scripts\\build_reference.py ^
      --ref-dir  "..\\data\\enf-ref" --derived-dir "..\\data\\derived" ^
      --results-dir results
"""
from __future__ import annotations

import os

# numpy's OpenBLAS commits ~25 MB per thread at import (about 750 MB on this
# 32-thread laptop). Nothing here needs BLAS threads.
for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from gunj import reference as gref  # noqa: E402

FILE_ORDER = ["freq_I.csv", "freq_II.csv", "freq_III.csv", "freq_IV.csv", "freq_V.csv"]
FILE_LABEL = {name: name[len("freq_"):-len(".csv")] for name in FILE_ORDER}
TRACE_MARKER = b"Failed to produce download file"
TS_FORMAT = "%Y-%m-%d %H:%M:%S"
# A value outside this band is treated as not measured. The Indian grid's
# operating band is 49.90-50.05 Hz; 45-55 Hz only rejects non-physical values.
F_LO, F_HI = 45.0, 55.0
LONG_GAP_S = 10
DATASET = {
    "name": "Mendeley Data dataset bvwh9d6227",
    "url": "https://data.mendeley.com/datasets/bvwh9d6227",
    "description": "Grid-India NERLDC PMU one-second grid frequency (substation BALIP_PG), shared by IIT Guwahati",
    "licence": "CC BY 4.0",
    "licence_url": "https://creativecommons.org/licenses/by/4.0/",
    "citation": ("Mendeley Data, dataset bvwh9d6227: one-second grid frequency from Grid-India NERLDC PMU data "
                 "(IIT Guwahati). CC BY 4.0. https://data.mendeley.com/datasets/bvwh9d6227. "
                 "Author list and dataset version to be copied from the dataset page before publication."),
    "modifications": ("Merged five CSV exports into one one-second float32 series; removed the exporter's "
                      "Java error trace and the truncated row before it; marked empty or non-numeric "
                      "rows as missing; resolved overlapping files by the rule in overlap_rule."),
}
OVERLAP_RULE = ("Later file wins: for each second, take the value from the highest-numbered file "
                "(V > IV > III > II > I) that has a valid value there. An earlier file only fills seconds "
                "the later files lack. Within one file, the first row for a second wins. Reason: in every "
                "overlap the two files hold the same or nearly the same values with their time labels shifted by "
                "about one to five minutes, and "
                "the 15-minute block-clock check (scripts/check_time_axis.py) finds labels drifting late "
                "over the length of each file. The later file is near its own start there, so its labels "
                "have drifted less.")
TIME_LABEL_NOTE = ("Time labels are the files' own naive IST labels, not corrected. Overlapping files disagree "
                   "by whole minutes on when the same values occurred (see junctions and "
                   "results/reference_build_stats.json), and results/time_axis_check.json shows labels "
                   "drifting late within files. Absolute time can be off by several minutes in parts of "
                   "the series.")
VALID_RULE = (f"A second is valid (mask = 1) when some file has a row for it whose value parses as a "
              f"finite number in [{F_LO}, {F_HI}] Hz. Quality codes are recorded but not used.")


def peak_mb() -> float:
    """(peak private bytes, peak working set) of this process in MB. The working
    set includes file-backed memmap pages, which Windows can drop at any time."""
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return float("nan"), float("nan")
        return pmc.PeakPagefileUsage / 2**20, pmc.PeakWorkingSetSize / 2**20
    except Exception:
        try:
            import resource
            return float("nan"), resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        except Exception:
            return float("nan"), float("nan")


LOG_PATH: Path | None = None


def log(*a):
    priv, ws = peak_mb()
    line = " ".join([time.strftime("%Y-%m-%d %H:%M:%S"),
                     f"[peak private {priv:.0f} MB, peak working set {ws:.0f} MB]"] + [str(x) for x in a])
    print(line, flush=True)
    if LOG_PATH is not None:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------
class _LimitedHashingRaw(io.RawIOBase):
    """Raw reader that stops after `limit` bytes and hashes what it returns."""

    def __init__(self, fh, limit: int, hasher):
        self.fh, self.remaining, self.h = fh, limit, hasher

    def readable(self):
        return True

    def readinto(self, b):
        if self.remaining <= 0:
            return 0
        data = self.fh.read(min(len(b), self.remaining))
        k = len(data)
        b[:k] = data
        self.remaining -= k
        self.h.update(data)
        return k


def find_data_end(path: Path, tail_bytes: int = 1 << 20) -> tuple[int, bytes]:
    """Byte offset just after the last complete row before the Java trace,
    and the dropped tail. Without a trace, (file size, b"")."""
    size = path.stat().st_size
    start = max(0, size - tail_bytes)
    with open(path, "rb") as fh:
        fh.seek(start)
        tail = fh.read()
    pos = tail.find(TRACE_MARKER)
    if pos < 0:
        return size, b""
    nl = tail.rfind(b"\n", 0, pos)
    if nl < 0:
        raise RuntimeError(f"{path.name}: trace marker found but no row boundary in the last {tail_bytes} bytes")
    end = start + nl + 1
    return end, tail[nl + 1:]


def read_header(path: Path) -> list[str]:
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return next(csv.reader([fh.readline().strip("\r\n")]))


def pick_columns(header: list[str]) -> dict:
    if "STARTDATE" not in header:
        raise ValueError(f"no STARTDATE column in {header}")
    vals = [c for c in header if "1hz" in c.lower() and not c.endswith(":Quality")]
    if len(vals) != 1:
        raise ValueError(f"cannot pick the value column from {header}")
    q = vals[0] + ":Quality"
    return {
        "ts": "STARTDATE",
        "val": vals[0],
        "q": q if q in header else None,
        "ids": [c for c in ("SubstationId", "DeviceId", "MeasurementId") if c in header],
    }


def parse_file(path: Path, out_dir: Path, chunk_rows: int) -> dict:
    name = path.name
    out_dir.mkdir(parents=True, exist_ok=True)
    size = path.stat().st_size
    data_end, dropped = find_data_end(path)
    header = read_header(path)
    cols = pick_columns(header)
    usecols = [cols["ts"], cols["val"]] + ([cols["q"]] if cols["q"] else []) + cols["ids"]
    dtypes = {cols["ts"]: str, cols["val"]: str}
    if cols["q"]:
        dtypes[cols["q"]] = str
    for c in cols["ids"]:
        dtypes[c] = "category"

    paths = {k: out_dir / f"{path.stem}.{k}" for k in ("sec.i64", "val.f64", "q.i8")}
    fhs = {k: open(p, "wb") for k, p in paths.items()}
    st = {
        "file": name, "bytes": size, "data_bytes": data_end, "header": header, "columns_used": cols,
        "rows_read": 0, "rows_bad_timestamp": 0, "rows_kept": 0,
        "rows_empty_value": 0, "rows_nonnumeric_value": 0, "rows_numeric": 0,
        "quality_counts_numeric": {}, "quality_counts_empty": {},
        "ids": {c: [] for c in cols["ids"]},
        "steps": {"eq1": 0, "dup0": 0, "backward": 0, "jump": 0, "max_jump_s": 0},
        "first_ts": None, "last_ts": None, "first_numeric_ts": None, "last_numeric_ts": None,
        "value_min": None, "value_max": None,
    }
    trailing = dropped
    st["trailing_bytes_dropped"] = len(trailing)
    st["trailing_lines_dropped"] = (trailing.count(b"\n") + (0 if trailing.endswith(b"\n") or not trailing else 1))
    st["truncated_row_prefix"] = trailing.split(TRACE_MARKER)[0].decode("utf-8", "replace") if trailing else ""

    hasher = hashlib.sha256()
    prev_sec = None
    vmin, vmax = np.inf, -np.inf
    t_start = time.time()
    with open(path, "rb") as raw_fh:
        raw = _LimitedHashingRaw(raw_fh, data_end, hasher)
        buf = io.BufferedReader(raw, buffer_size=8 << 20)
        reader = pd.read_csv(buf, usecols=usecols, dtype=dtypes, chunksize=chunk_rows,
                             encoding="utf-8", skip_blank_lines=True)
        for chunk in reader:
            st["rows_read"] += len(chunk)
            ts = pd.to_datetime(chunk[cols["ts"]], format=TS_FORMAT, errors="coerce")
            ok_ts = ts.notna().to_numpy()
            st["rows_bad_timestamp"] += int((~ok_ts).sum())
            chunk = chunk.loc[ok_ts]
            sec = ts[ok_ts].to_numpy().astype("datetime64[s]").astype(np.int64)
            vraw = chunk[cols["val"]]
            empty = (vraw.isna() | (vraw.astype(str).str.strip() == "")).to_numpy()
            val = pd.to_numeric(vraw, errors="coerce").to_numpy(dtype=np.float64, copy=True)
            numeric = np.isfinite(val)
            val[~numeric] = np.nan
            st["rows_empty_value"] += int(empty.sum())
            st["rows_nonnumeric_value"] += int((~empty & ~numeric).sum())
            st["rows_numeric"] += int(numeric.sum())
            if cols["q"]:
                qn = pd.to_numeric(chunk[cols["q"]], errors="coerce")
                q = qn.fillna(-1).to_numpy().astype(np.int8)
                qstr = chunk[cols["q"]].fillna("<empty>").astype(str)
                for key, m in (("quality_counts_numeric", numeric), ("quality_counts_empty", ~numeric)):
                    for code, cnt in qstr[m].value_counts().items():
                        st[key][code] = st[key].get(code, 0) + int(cnt)
            else:
                q = np.full(len(sec), -1, np.int8)
            for c in cols["ids"]:
                for v in chunk[c].dropna().unique():
                    if str(v) not in st["ids"][c]:
                        st["ids"][c].append(str(v))
            if len(sec):
                full = sec if prev_sec is None else np.concatenate([[prev_sec], sec])
                d = np.diff(full)
                st["steps"]["eq1"] += int((d == 1).sum())
                st["steps"]["dup0"] += int((d == 0).sum())
                st["steps"]["backward"] += int((d < 0).sum())
                st["steps"]["jump"] += int((d > 1).sum())
                if d.size:
                    st["steps"]["max_jump_s"] = max(st["steps"]["max_jump_s"], int(d.max()))
                prev_sec = int(sec[-1])
                if st["first_ts"] is None:
                    st["first_ts"] = int(sec[0])
                st["last_ts"] = int(sec[-1])
                if numeric.any():
                    nz = np.flatnonzero(numeric)
                    if st["first_numeric_ts"] is None:
                        st["first_numeric_ts"] = int(sec[nz[0]])
                    st["last_numeric_ts"] = int(sec[nz[-1]])
                    vmin = min(vmin, float(np.nanmin(val)))
                    vmax = max(vmax, float(np.nanmax(val)))
            sec.tofile(fhs["sec.i64"])
            val.tofile(fhs["val.f64"])
            q.tofile(fhs["q.i8"])
            st["rows_kept"] += len(sec)
        consumed_all = raw.remaining == 0
        raw_fh.seek(data_end)
        while True:
            block = raw_fh.read(8 << 20)
            if not block:
                break
            hasher.update(block)
    for fh in fhs.values():
        fh.close()
    if not consumed_all:
        raise RuntimeError(f"{name}: parser stopped before the data end; hash would be wrong")
    st["sha256"] = hasher.hexdigest()
    st["value_min"] = None if vmin == np.inf else vmin
    st["value_max"] = None if vmax == -np.inf else vmax
    for k in ("first_ts", "last_ts", "first_numeric_ts", "last_numeric_ts"):
        st[k + "_ist"] = None if st[k] is None else str(pd.Timestamp(st[k], unit="s"))
    st["parse_seconds"] = round(time.time() - t_start, 1)
    (out_dir / f"{path.stem}.parse.json").write_text(json.dumps(st, indent=1), encoding="utf-8")
    log(f"parsed {name}: {st['rows_kept']:,} rows, {st['rows_numeric']:,} numeric, "
        f"{st['rows_bad_timestamp']} bad timestamps, dropped {st['trailing_lines_dropped']} trailing lines, "
        f"{st['parse_seconds']} s")
    return st


def stage_parse(ref_dir: Path, derived: Path, chunk_rows: int, only=None):
    out = derived / "_parsed"
    for name in FILE_ORDER:
        if only and FILE_LABEL[name] not in only:
            continue
        parse_file(ref_dir / name, out, chunk_rows)


# ---------------------------------------------------------------------------
# assemble
# ---------------------------------------------------------------------------
def load_parsed(derived: Path, name: str):
    out = derived / "_parsed"
    stem = Path(name).stem
    st = json.loads((out / f"{stem}.parse.json").read_text(encoding="utf-8"))
    n = st["rows_kept"]
    sec = np.memmap(out / f"{stem}.sec.i64", dtype=np.int64, mode="r", shape=(n,))
    val = np.memmap(out / f"{stem}.val.f64", dtype=np.float64, mode="r", shape=(n,))
    return st, sec, val


def row_valid(v: np.ndarray) -> np.ndarray:
    return np.isfinite(v) & (v >= F_LO) & (v <= F_HI)


def valid_range(sec, val, block: int):
    """First and last second with a valid value; count of valid and out-of-band rows."""
    first = last = None
    n_valid = n_band = 0
    for b0 in range(0, len(sec), block):
        v = np.asarray(val[b0:b0 + block])
        ok = row_valid(v)
        n_valid += int(ok.sum())
        n_band += int((np.isfinite(v) & ~ok).sum())
        if ok.any():
            nz = np.flatnonzero(ok)
            s = np.asarray(sec[b0:b0 + block])
            if first is None:
                first = int(s[nz].min())
            else:
                first = min(first, int(s[nz].min()))
            last = int(s[nz].max()) if last is None else max(last, int(s[nz].max()))
    return first, last, n_valid, n_band


def dense_window(sec, val, lo: int, hi: int, block: int) -> np.ndarray:
    """float64 array for seconds lo..hi (inclusive); NaN where the file has no
    valid value. The first row for a second wins."""
    out = np.full(hi - lo + 1, np.nan)
    filled = np.zeros(hi - lo + 1, bool)
    for b0 in range(0, len(sec), block):
        s = np.asarray(sec[b0:b0 + block])
        if s.size == 0 or s.max() < lo or s.min() > hi:
            continue
        v = np.asarray(val[b0:b0 + block])
        m = (s >= lo) & (s <= hi) & row_valid(v)
        idx = s[m] - lo
        vv = v[m]
        idx, first = np.unique(idx, return_index=True)
        vv = vv[first]
        new = ~filled[idx]
        out[idx[new]] = vv[new]
        filled[idx[new]] = True
    return out


def compare_overlap(a: np.ndarray, b: np.ndarray, lo: int) -> dict:
    av, bv = np.isfinite(a), np.isfinite(b)
    both = av & bv
    d = np.abs(a[both] - b[both])
    res = {
        "window_start_ist": str(pd.Timestamp(lo, unit="s")),
        "window_end_ist": str(pd.Timestamp(lo + len(a) - 1, unit="s")),
        "window_seconds": int(len(a)),
        "a_valid": int(av.sum()), "b_valid": int(bv.sum()),
        "both_valid": int(both.sum()), "a_only": int((av & ~bv).sum()), "b_only": int((bv & ~av).sum()),
        "neither": int((~av & ~bv).sum()),
    }
    if d.size:
        res.update({
            "identical": int((d == 0).sum()),
            "share_identical": float((d == 0).mean()),
            "max_abs_diff_hz": float(d.max()),
            "mean_abs_diff_hz": float(d.mean()),
            "median_abs_diff_hz": float(np.median(d)),
            "p99_abs_diff_hz": float(np.percentile(d, 99)),
            "n_abs_diff_gt_0p001": int((d > 0.001).sum()),
            "n_abs_diff_gt_0p01": int((d > 0.01).sum()),
        })
        # Does a time shift between the files fit better than zero? (checks a shared time axis)
        lags = {}
        for lag in (-3, -2, -1, 0, 1, 2, 3):
            if lag >= 0:
                x, y = a[lag:], b[:len(b) - lag]
            else:
                x, y = a[:lag], b[-lag:]
            m = np.isfinite(x) & np.isfinite(y)
            lags[str(lag)] = float(np.abs(x[m] - y[m]).mean()) if m.any() else None
        res["mean_abs_diff_by_lag_s"] = lags
        res["lag_search"] = best_lag(a, b, MAX_LAG_S)
    return res


MAX_LAG_S = 2 * 86400


def pearson_at_lag(a: np.ndarray, b: np.ndarray, k: int) -> tuple[float, int, float]:
    """Pearson r, pair count and mean abs diff between a[t + k] and b[t]."""
    r, n, mad, _ = _pairs_at_lag(a, b, k)
    return r, n, mad


def _pairs_at_lag(a: np.ndarray, b: np.ndarray, k: int):
    """Pearson r, pair count, mean abs diff and identical count for a[t + k] vs b[t]."""
    if k >= 0:
        x, y = a[k:], b[:len(b) - k]
    else:
        x, y = a[:k], b[-k:]
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 3:
        return float("nan"), int(m.sum()), float("nan"), 0
    x, y = x[m], y[m]
    return float(np.corrcoef(x, y)[0, 1]), int(m.sum()), float(np.abs(x - y).mean()), int((x == y).sum())


def best_lag(a: np.ndarray, b: np.ndarray, max_lag: int) -> dict:
    """Search lags |k| <= max_lag for the shift that best aligns b[t] with a[t + k]
    (FFT cross-correlation of the mean-removed series, missing seconds as 0,
    scaled by the overlap length), then report exact Pearson r at the best lag
    and at lag 0. A best lag of k means file b's timestamp + k = file a's
    timestamp for the same moment."""
    ma, mb = np.isfinite(a), np.isfinite(b)
    a0 = np.where(ma, a - np.nanmean(a), 0.0)
    b0 = np.where(mb, b - np.nanmean(b), 0.0)
    n = len(a)
    nfft = 1 << int(np.ceil(np.log2(2 * n)))
    c = np.fft.irfft(np.fft.rfft(a0, nfft) * np.conj(np.fft.rfft(b0, nfft)), nfft)
    del a0, b0
    cnt = np.fft.irfft(np.fft.rfft(ma.astype(float), nfft) * np.conj(np.fft.rfft(mb.astype(float), nfft)), nfft)
    L = min(max_lag, n - 1)
    lags = np.r_[np.arange(0, L + 1), np.arange(-L, 0)]
    vals = np.r_[c[:L + 1], c[nfft - L:]]
    cnts = np.r_[cnt[:L + 1], cnt[nfft - L:]]
    del c, cnt
    ok = cnts > max(0.25 * n, 3600)  # require at least a quarter of the window to overlap
    score = np.where(ok, vals / np.maximum(cnts, 1), -np.inf)
    k = int(lags[int(np.argmax(score))])
    runner = None
    for j in np.argsort(-score)[1:]:
        if abs(int(lags[j]) - k) > 600 and np.isfinite(score[j]):
            runner = int(lags[j])
            break
    r_best, n_best, mad_best, same_best = _pairs_at_lag(a, b, k)
    r0, n0, mad0 = pearson_at_lag(a, b, 0)
    out = {"max_lag_searched_s": int(L), "best_lag_s": k, "best_lag_hms": str(pd.Timedelta(seconds=k)),
           "r_at_best": r_best, "pairs_at_best": n_best, "identical_at_best": same_best,
           "mean_abs_diff_at_best_hz": mad_best,
           "r_at_0": r0, "mean_abs_diff_at_0_hz": mad0}
    if runner is not None:
        rr, nr, _ = pearson_at_lag(a, b, runner)
        out.update({"runner_up_lag_s": runner, "r_at_runner_up": rr})
    return out


def quality_residuals(derived: Path, name: str, sec, val, block: int, sample_step: int = 50) -> dict:
    """How far each value sits from the mean of its two neighbours, split by the
    file's quality code. Shows whether non-zero codes mark odd values. Code 0 is
    summarised on every `sample_step`-th row; other codes on every row."""
    stem = Path(name).stem
    q = np.memmap(derived / "_parsed" / f"{stem}.q.i8", dtype=np.int8, mode="r", shape=(len(sec),))
    res = {}
    for b0 in range(1, len(sec) - 1, block):
        b1 = min(len(sec) - 1, b0 + block)
        s = np.asarray(sec[b0 - 1:b1 + 1])
        v = np.asarray(val[b0 - 1:b1 + 1])
        qq = np.asarray(q[b0:b1])
        ok = (s[2:] - s[:-2] == 2) & np.isfinite(v[:-2]) & np.isfinite(v[1:-1]) & np.isfinite(v[2:])
        r = np.abs(v[1:-1] - 0.5 * (v[:-2] + v[2:]))
        for code in np.unique(qq[ok]):
            m = ok & (qq == code)
            if code == 0:
                m[np.arange(len(m)) % sample_step != (b0 % sample_step)] = False
            res.setdefault(int(code), []).append(r[m])
    out = {}
    for code, parts in sorted(res.items()):
        x = np.concatenate(parts)
        if x.size:
            out[str(code)] = {"n": int(x.size), "median_hz": float(np.median(x)), "p99_hz": float(np.percentile(x, 99)),
                              "max_hz": float(x.max()), "sampled_every": sample_step if code == 0 else 1}
    return out


def _open_out(tmp: dict, mode: str):
    return (np.load(tmp["f"], mmap_mode=mode), np.load(tmp["v"], mmap_mode=mode), np.load(tmp["s"], mmap_mode=mode))


def stage_assemble(ref_dir: Path, derived: Path, results: Path, block: int):
    parsed = {}
    for name in FILE_ORDER:
        st, sec, val = load_parsed(derived, name)
        first, last, n_valid, n_band = valid_range(sec, val, block)
        st["first_valid_sec"], st["last_valid_sec"] = first, last
        st["rows_valid"], st["rows_out_of_band"] = n_valid, n_band
        if st["columns_used"]["q"]:
            st["neighbour_residual_by_quality"] = quality_residuals(derived, name, sec, val, block)
        parsed[name] = st
        del sec, val
        log(f"{name}: valid {n_valid:,} rows, {pd.Timestamp(first, unit='s')} .. {pd.Timestamp(last, unit='s')}")
    t0 = min(st["first_valid_sec"] for st in parsed.values())
    t1 = max(st["last_valid_sec"] for st in parsed.values())
    n = t1 - t0 + 1
    log(f"series {pd.Timestamp(t0, unit='s')} .. {pd.Timestamp(t1, unit='s')} IST, {n:,} s")

    # compare overlapping files second by second, and search for a time shift between them
    overlaps = []
    for i in range(len(FILE_ORDER)):
        for j in range(i + 1, len(FILE_ORDER)):
            sa, sb = parsed[FILE_ORDER[i]], parsed[FILE_ORDER[j]]
            lo = max(sa["first_valid_sec"], sb["first_valid_sec"])
            hi = min(sa["last_valid_sec"], sb["last_valid_sec"])
            if lo > hi:
                continue
            _, seca, vala = load_parsed(derived, FILE_ORDER[i])
            a = dense_window(seca, vala, lo, hi, block)
            del seca, vala
            _, secb, valb = load_parsed(derived, FILE_ORDER[j])
            b = dense_window(secb, valb, lo, hi, block)
            del secb, valb
            r = compare_overlap(a, b, lo)
            r["a"], r["b"] = FILE_ORDER[i], FILE_ORDER[j]
            overlaps.append(r)
            del a, b
            log(f"overlap {FILE_ORDER[i]} x {FILE_ORDER[j]}: both valid {r['both_valid']:,}, identical share "
                f"{r.get('share_identical')}, best lag {r.get('lag_search', {}).get('best_lag_s')} s")

    derived.mkdir(parents=True, exist_ok=True)
    tmp = {k: derived / (v + ".tmp.npy") for k, v in
           (("f", gref.F32_NAME[:-4]), ("v", gref.VALID_NAME[:-4]), ("s", gref.SRC_NAME[:-4]))}
    F = np.lib.format.open_memmap(tmp["f"], mode="w+", dtype=np.float32, shape=(n,))
    V = np.lib.format.open_memmap(tmp["v"], mode="w+", dtype=np.uint8, shape=(n,))
    S = np.lib.format.open_memmap(tmp["s"], mode="w+", dtype=np.uint8, shape=(n,))
    for b0 in range(0, n, block):
        F[b0:b0 + block] = np.nan
        V[b0:b0 + block] = 0
        S[b0:b0 + block] = 0
    F.flush(); V.flush(); S.flush()
    del F, V, S

    # later file wins: write files from V back to I; a file only fills seconds still empty
    contrib = {}
    for k in range(len(FILE_ORDER), 0, -1):
        name = FILE_ORDER[k - 1]
        _, sec, val = load_parsed(derived, name)
        F, V, S = _open_out(tmp, "r+")
        c = {"written": 0, "already_filled_by_later_file": 0, "duplicate_second_in_file": 0,
             "duplicate_second_differs": 0}
        for b0 in range(0, len(sec), block):
            s = np.asarray(sec[b0:b0 + block])
            v = np.asarray(val[b0:b0 + block])
            ok = row_valid(v)
            idx = s[ok] - t0
            vv = v[ok]
            uidx, first = np.unique(idx, return_index=True)
            c["duplicate_second_in_file"] += int(len(idx) - len(uidx))
            if len(idx) != len(uidx):
                order = np.argsort(idx, kind="stable")
                si, sv = idx[order], vv[order]
                dup = np.flatnonzero(si[1:] == si[:-1]) + 1
                firstpos = np.searchsorted(si, si[dup])
                c["duplicate_second_differs"] += int((sv[dup] != sv[firstpos]).sum())
            vv = vv[first]
            src_now = np.asarray(S[uidx])
            free = src_now == 0
            c["already_filled_by_later_file"] += int(((src_now != 0) & (src_now != k)).sum())
            c["duplicate_second_in_file"] += int((src_now == k).sum())
            w = uidx[free]
            F[w] = vv[free].astype(np.float32)
            V[w] = 1
            S[w] = k
            c["written"] += int(free.sum())
        F.flush(); V.flush(); S.flush()
        del F, V, S, sec, val
        contrib[name] = c
        log(f"{name}: wrote {c['written']:,} s, {c['already_filled_by_later_file']:,} s already taken by a later file")

    for key, fname in (("f", gref.F32_NAME), ("v", gref.VALID_NAME), ("s", gref.SRC_NAME)):
        dst = derived / fname
        if dst.exists():
            dst.unlink()
        tmp[key].rename(dst)

    # where the series hands over from one file to the next, and the label offset measured there
    junctions = []
    for o in overlaps:
        if FILE_ORDER.index(o["b"]) != FILE_ORDER.index(o["a"]) + 1 or "lag_search" not in o:
            continue
        lag = o["lag_search"]["best_lag_s"]
        junctions.append({
            "from_file": o["a"], "to_file": o["b"],
            "handover_ist": str(pd.Timestamp(parsed[o["b"]]["first_valid_sec"], unit="s")),
            "label_offset_s": lag,
            "meaning": (f"{o['a']} labels the same moment {lag} s later than {o['b']}. Near the handover, "
                        f"about {abs(lag)} s of real time are absent from the series although the mask "
                        f"shows no gap."),
        })

    sources = []
    for k, name in enumerate(FILE_ORDER, start=1):
        st = parsed[name]
        sources.append({
            "file": name, "code": k, "bytes": st["bytes"], "sha256": st["sha256"],
            "rows_kept": st["rows_kept"], "rows_valid": st["rows_valid"],
            "first_valid_ist": str(pd.Timestamp(st["first_valid_sec"], unit="s")),
            "last_valid_ist": str(pd.Timestamp(st["last_valid_sec"], unit="s")),
            "seconds_used": contrib[name]["written"],
            "ids": st["ids"],
        })
    meta = {
        "t0_ist": pd.Timestamp(t0, unit="s").isoformat(),
        "t_end_ist": pd.Timestamp(t1, unit="s").isoformat(),
        "t0_unix_utc": int(gref.ist_to_unix(pd.Timestamp(t0, unit="s"))),
        "length": int(n),
        "step_seconds": 1,
        "timezone": "IST (UTC+05:30) wall-clock, stored naive; index i = t0 + i seconds",
        "units": "Hz",
        "files": {"freq": gref.F32_NAME, "valid": gref.VALID_NAME, "source": gref.SRC_NAME},
        "freq_dtype": "float32", "freq_at_invalid": "NaN",
        "valid_dtype": "uint8", "valid_meaning": "1 = measured, 0 = missing",
        "source_meaning": "1..5 = freq_I..freq_V, 0 = none",
        "valid_rule": VALID_RULE,
        "overlap_rule": OVERLAP_RULE,
        "time_labels": TIME_LABEL_NOTE,
        "junctions": junctions,
        "sources": sources,
        "sha256_note": ("sha256 computed over each whole CSV (including the trailing Java trace) by "
                        "scripts/build_reference.py. The comparison with Mendeley's published hashes "
                        "was done before this build and is not repeated here."),
        "dataset": DATASET,
        "built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "builder": "scripts/build_reference.py",
    }
    (derived / gref.META_NAME).write_text(json.dumps(meta, indent=1), encoding="utf-8")

    results.mkdir(parents=True, exist_ok=True)
    build_stats = {
        "parse": {name: dict(parsed[name]) for name in FILE_ORDER},
        "contributions": contrib,
        "overlaps": overlaps,
        "junctions": junctions,
        "series": {"t0_ist": meta["t0_ist"], "t_end_ist": meta["t_end_ist"], "length": n},
        "valid_band_hz": [F_LO, F_HI],
        "overlap_rule": OVERLAP_RULE,
    }
    (results / "reference_build_stats.json").write_text(json.dumps(build_stats, indent=1), encoding="utf-8")
    log("assemble done")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def runs_of_zero(v: np.ndarray, block: int = 8_000_000):
    """(start, end_inclusive) indices of runs of zeros in a 0/1 array.
    Works in blocks with int8 arithmetic so a memmap of 84 M seconds stays cheap."""
    n = len(v)
    starts, ends = [], []
    prev = 1
    for b0 in range(0, n, block):
        x = np.asarray(v[b0:b0 + block]).astype(np.int8)
        d = np.diff(x, prepend=np.int8(prev))
        starts.append(np.flatnonzero(d == -1) + b0)
        ends.append(np.flatnonzero(d == 1) + b0 - 1)
        prev = int(x[-1])
    if prev == 0:
        ends.append(np.array([n - 1]))
    return np.concatenate(starts), np.concatenate(ends)


def csv_rows_at(path: Path, ts_prefix: str, max_rows: int = 5000) -> list[tuple[str, str]]:
    """Independent check: binary-search the raw CSV text (sorted by time) for
    rows whose timestamp starts with ts_prefix. Returns (timestamp, value) text."""
    size = path.stat().st_size
    header = read_header(path)
    vi = header.index(pick_columns(header)["val"])
    key = ts_prefix.encode()

    def ts_at(off):
        fh.seek(off)
        if off:
            fh.readline()
        while True:
            line = fh.readline()
            if not line:
                return None, None
            t = line.lstrip(b'"')[:19]
            if t[:4].isdigit():
                return t, fh.tell() - len(line)

    out = []
    with open(path, "rb") as fh:
        lo, hi = 0, size
        while hi - lo > 4096:
            mid = (lo + hi) // 2
            t, _ = ts_at(mid)
            if t is None or t >= key:
                hi = mid
            else:
                lo = mid
        fh.seek(lo)
        if lo:
            fh.readline()
        for line in fh:
            row = next(csv.reader([line.decode("utf-8", "replace").strip("\r\n")]))
            if not row or not row[0][:4].isdigit():
                continue
            if row[0] < ts_prefix:
                continue
            if not row[0].startswith(ts_prefix):
                break
            out.append((row[0], row[vi] if vi < len(row) else ""))
            if len(out) >= max_rows:
                break
    return out


PROBE_OUT = REPO / "docs" / "probe" / "enf_margin_out.txt"
PROBE_EXCERPT_START_S, PROBE_EXCERPT_LEN_S, PROBE_FRAME_CENTRE_S = 120, 2400, 8


def read_probe_lock(path: Path = PROBE_OUT) -> dict | None:
    """The 40-min (len=2400s) lock line of the 5 Nov probe output."""
    import re
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"len=2400s: top r=([\d.]+) at (\S+ \S+) \(off vs YT ([+-]\d+)s\)", line)
        if m:
            lock = pd.Timestamp(m.group(2))
            off = int(m.group(3))
            return {"line": line, "r": float(m.group(1)), "lock_ist_file_IV_labels": str(lock),
                    "offset_vs_youtube_s": off, "youtube_start_ist": str(lock - pd.Timedelta(seconds=off))}
    return None


def sanity_check(ref, ref_dir: Path, build: dict) -> dict | None:
    """Index/time checks around the 5 Nov probe lock. No audio is used.

    In docs/probe/enf_margin.py the lock time is the inferred start of the
    recording: found = t0 + b - (start + 8), where b is the reference index
    matched by the excerpt that starts `start` s into the audio and 8 s is the
    centre of the 16 s analysis frame. So, on file IV's labels, the reference
    window the 40-min excerpt matched is [lock + 128 s, lock + 128 s + 2400 s).
    The probe used file IV alone; this series takes V there (later file wins),
    whose labels differ from IV's by the IV x V overlap offset."""
    probe = read_probe_lock()
    if probe is None:
        return None
    lock = pd.Timestamp(probe["lock_ist_file_IV_labels"])
    if not ref.contains(lock):
        return None
    out = {"probe": probe}
    li = ref.index_of(lock)
    out.update({"lock_index": li, "round_trip_ist": str(ref.time_of(li)),
                "round_trip_ok": bool(ref.time_of(li) == lock),
                "index_equals_seconds_from_t0": bool(int((lock - ref.t0).total_seconds()) == li),
                "value_at_lock_label_hz": float(ref.freq[li]), "valid_at_lock_label": int(ref.valid[li]),
                "source_at_lock_label": (FILE_ORDER[int(ref.source[li]) - 1]
                                         if ref.source is not None and ref.source[li] else None)})
    lag = next((o["lag_search"]["best_lag_s"] for o in build["overlaps"]
                if o["a"] == "freq_IV.csv" and o["b"] == "freq_V.csv" and "lag_search" in o), 0)
    out["iv_minus_v_label_offset_s"] = lag
    # the matched window on IV's labels, and the same moments on this series' labels
    w0_iv = lock + pd.Timedelta(seconds=PROBE_EXCERPT_START_S + PROBE_FRAME_CENTRE_S)
    w1_iv = w0_iv + pd.Timedelta(seconds=PROBE_EXCERPT_LEN_S)
    src_lock = out["source_at_lock_label"]
    shift = lag if src_lock == "freq_V.csv" else 0
    w0 = ref.index_of(w0_iv) - shift
    w1 = w0 + PROBE_EXCERPT_LEN_S
    out["matched_window_file_IV_labels_ist"] = [str(w0_iv), str(w1_iv - pd.Timedelta(seconds=1))]
    out["matched_window_series_labels_ist"] = [str(ref.time_of(w0)), str(ref.time_of(w1 - 1))]
    out["lock_on_series_labels_ist"] = str(lock - pd.Timedelta(seconds=shift))
    out["lock_on_series_labels_minus_youtube_start_s"] = int(probe["offset_vs_youtube_s"] - shift)
    i0d, i1d = ref.scope(lock, days=1)
    i0q, i1q = ref.scope(lock, days=90)
    for label, (a, b) in {"matched_window": (w0, w1), "plus_minus_1_day": (i0d, i1d),
                          "plus_minus_90_days": (i0q, i1q)}.items():
        out[f"valid_share_{label}"] = round(ref.valid_fraction(a, b), 6)
        out[f"seconds_{label}"] = int(b - a)
    if ref.source is not None:
        bc = np.bincount(np.asarray(ref.source[w0:w1]), minlength=6)
        out["matched_window_source_counts"] = {
            ("none" if k == 0 else FILE_ORDER[k - 1]): int(c) for k, c in enumerate(bc) if c}
    iv = build["parse"].get("freq_IV.csv")
    if iv:
        iv_t0 = pd.Timestamp(iv["first_ts"], unit="s")
        out["file_IV_first_row_ist"] = str(iv_t0)
        out["probe_index_in_file_IV"] = int((lock - iv_t0).total_seconds())
        out["series_index_of_file_IV_first_row"] = ref.index_of(iv_t0, check=False)
        out["series_index_minus_probe_index"] = int(li - (lock - iv_t0).total_seconds())
    # compare the series with raw CSV text found by binary search (not through the parser):
    # file IV rows at label t should equal the series at t - shift; file V rows at t equal the series at t
    checks = {}
    t_a = (lock - pd.Timedelta(seconds=shift)).floor("min")
    t_b = w1_iv
    for fname, sh in (("freq_IV.csv", shift), ("freq_V.csv", 0)):
        path = ref_dir / fname
        if not path.exists():
            continue
        rows = []
        for minute in pd.date_range(t_a, t_b, freq="min"):
            rows += csv_rows_at(path, minute.strftime("%Y-%m-%d %H:%M"))
        n_cmp = n_eq = 0
        maxd = 0.0
        for t, v in rows:
            if v.strip() == "":
                continue
            i = ref.index_of(t, check=False) - sh
            if not (0 <= i < len(ref)) or not ref.valid[i]:
                continue
            n_cmp += 1
            d = abs(float(np.float32(float(v))) - float(ref.freq[i]))
            maxd = max(maxd, d)
            n_eq += int(d == 0.0)
        checks[fname] = {"rows_from_ist": str(t_a), "rows_to_ist": str(t_b), "rows_found": len(rows),
                         "series_shift_s": sh, "rows_compared": n_cmp, "equal_as_float32": n_eq,
                         "max_abs_diff_hz": maxd,
                         "row_at_probe_lock_label": next((list(r) for r in rows if r[0] == str(lock)), None)}
    out["raw_csv_vs_series"] = checks
    return out


def stage_report(ref_dir: Path, derived: Path, results: Path, block: int):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    # the 15-minute block-clock check writes results/time_axis_check.json, used in the summary
    tac = results / "time_axis_check.json"
    if tac.exists():
        tac.unlink()
    try:
        spec = importlib.util.spec_from_file_location("check_time_axis", REPO / "scripts" / "check_time_axis.py")
        cta = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cta)
        cta.main(["--derived-dir", str(derived), "--results-dir", str(results)])
    except SystemExit as e:
        log(f"time-axis check skipped: {e}")

    ref = gref.load(derived, with_source=True)
    meta = ref.meta
    build = json.loads((results / "reference_build_stats.json").read_text(encoding="utf-8"))
    n = len(ref)
    V = ref.valid
    n_valid = int(np.count_nonzero(V))
    log(f"report: {n:,} s, {n_valid:,} valid")

    # gaps
    starts, ends = runs_of_zero(V)
    lens = ends - starts + 1
    long_m = lens > LONG_GAP_S
    ls, le, ll = starts[long_m], ends[long_m], lens[long_m]
    gaps = [{"start_ist": str(ref.time_of(s)), "end_ist": str(ref.time_of(e)), "length_s": int(L),
             "start_index": int(s)} for s, e, L in zip(ls, le, ll)]
    order = np.argsort(-lens, kind="stable")
    longest = [{"start_ist": str(ref.time_of(starts[i])), "end_ist": str(ref.time_of(ends[i])),
                "length_s": int(lens[i]), "length_h": round(float(lens[i]) / 3600, 2)} for i in order[:15]]

    log("report: gaps done")
    # monthly totals
    month_starts = pd.date_range(ref.t0.to_period("M").to_timestamp(), ref.t_end, freq="MS")
    months = []
    for ms in month_starts:
        me = ms + pd.offsets.MonthBegin(1)
        i0, i1 = ref.window(ms, me)
        span = i1 - i0
        if span <= 0:
            continue
        nv = int(np.count_nonzero(V[i0:i1]))
        in_long = int(np.clip(np.minimum(le + 1, i1) - np.maximum(ls, i0), 0, None).sum())
        sm = (ls >= i0) & (ls < i1)
        months.append({
            "month": ms.strftime("%Y-%m"), "seconds_in_span": int(span), "valid_s": nv,
            "missing_s": int(span - nv), "valid_pct": round(100.0 * nv / span, 3),
            "missing_in_gaps_gt10s_s": in_long, "gaps_gt10s_starting": int(sm.sum()),
            "longest_gap_starting_s": int(ll[sm].max()) if sm.any() else 0,
        })

    log("report: monthly done")
    # daily valid share (denominator: seconds of that day inside the span)
    day0 = ref.t0.normalize()
    days = pd.date_range(day0, ref.t_end.normalize(), freq="D")
    daily = []
    for d in days:
        i0, i1 = ref.window(d, d + pd.Timedelta(days=1))
        span = i1 - i0
        daily.append((d, span, int(np.count_nonzero(V[i0:i1])) if span else 0))
    day_share = np.array([nv / sp if sp else np.nan for _, sp, nv in daily])

    log("report: daily done")
    # frequency statistics (blockwise, float64 accumulators)
    fmin, fmax, s1, s2, cnt = np.inf, -np.inf, 0.0, 0.0, 0
    nbins = int(round((F_HI - F_LO) / 1e-4))
    edges = np.linspace(F_LO, F_HI, nbins + 1)
    hist = np.zeros(nbins, np.int64)
    max_step, n_step_gt = 0.0, 0
    for b0 in range(0, n, block):
        b1 = min(n, b0 + block + 1)
        f = np.asarray(ref.freq[b0:b1], dtype=np.float64)
        v = np.asarray(V[b0:b1]).astype(bool)
        core = slice(0, min(block, b1 - b0))
        fc, vc = f[core], v[core]
        x = fc[vc]
        if x.size:
            fmin, fmax = min(fmin, x.min()), max(fmax, x.max())
            y = x - 50.0
            s1 += y.sum(); s2 += (y * y).sum(); cnt += y.size
            hist += np.histogram(x, bins=nbins, range=(F_LO, F_HI))[0]
        both = v[1:] & v[:-1]
        st = np.abs(np.diff(f))[both]
        if st.size:
            max_step = max(max_step, float(st.max()))
            n_step_gt += int((st > 0.05).sum())
    mean = 50.0 + s1 / cnt
    std = float(np.sqrt(max(s2 / cnt - (s1 / cnt) ** 2, 0.0)))
    cdf = np.cumsum(hist) / hist.sum()
    pct = {str(p): round(float(edges[np.searchsorted(cdf, p / 100.0)] + 5e-5), 4)
           for p in (0.1, 1, 5, 50, 95, 99, 99.9)}

    log("report: freq stats done")
    # stuck values: runs of identical consecutive valid values
    neq = np.ones(n - 1, bool)  # False where a valid second repeats the previous valid value
    for b0 in range(0, n - 1, block):
        b1 = min(n - 1, b0 + block)
        f = np.asarray(ref.freq[b0:b1 + 1])
        v = np.asarray(V[b0:b1 + 1]).astype(bool)
        neq[b0:b1] = ~((f[1:] == f[:-1]) & v[1:] & v[:-1])
    rs, re_ = runs_of_zero(neq)
    del neq
    run_vals = re_ - rs + 2  # a run of k equal steps covers k + 1 seconds
    stuck = {
        "runs_ge_5s": int((run_vals >= 5).sum()), "runs_ge_10s": int((run_vals >= 10).sum()),
        "runs_ge_60s": int((run_vals >= 60).sum()),
        "seconds_in_runs_ge_10s": int(run_vals[run_vals >= 10].sum()),
        "longest_run_s": int(run_vals.max()) if run_vals.size else 0,
        "longest_run_start_ist": str(ref.time_of(rs[np.argmax(run_vals)])) if run_vals.size else None,
    }

    log("report: stuck values done")
    # source shares
    src_counts = {}
    for b0 in range(0, n, block):
        bc = np.bincount(np.asarray(ref.source[b0:b0 + block]), minlength=6)
        for k in range(6):
            src_counts[k] = src_counts.get(k, 0) + int(bc[k])

    log("report: sources done")
    sanity = sanity_check(ref, ref_dir, build)

    stats = {
        "span": {"t0_ist": str(ref.t0), "t_end_ist": str(ref.t_end), "seconds": n,
                 "days": round(n / 86400, 2)},
        "valid_seconds": n_valid, "valid_pct": round(100.0 * n_valid / n, 4),
        "missing_seconds": n - n_valid,
        "gaps_all": int(len(starts)), "gaps_gt10s": int(long_m.sum()),
        "missing_in_gaps_gt10s_s": int(ll.sum()),
        "gaps_1_to_10s": int((~long_m).sum()), "missing_in_gaps_le10s_s": int(lens[~long_m].sum()),
        "longest_gaps": longest,
        "freq": {"min_hz": float(fmin), "max_hz": float(fmax), "mean_hz": float(mean), "std_hz": std,
                 "percentiles_hz_from_0p0001_hist": pct, "max_abs_1s_step_hz": max_step,
                 "steps_gt_0p05hz": n_step_gt},
        "stuck_values": stuck,
        "source_seconds": {("none" if k == 0 else FILE_ORDER[k - 1]): c for k, c in src_counts.items()},
        "sanity": sanity,
        "days_with_full_coverage": int(np.sum(day_share >= 0.999999)),
        "days_with_zero_coverage": int(np.sum(day_share == 0)),
        "days_total": int(len(day_share)),
    }
    (results / "reference_stats.json").write_text(json.dumps(stats, indent=1, default=str), encoding="utf-8")
    gap_doc = {
        "description": (f"Every run of missing seconds longer than {LONG_GAP_S} s in the one-second reference. "
                        "start_ist and end_ist are the first and last missing seconds (inclusive), IST."),
        "series_t0_ist": str(ref.t0), "series_end_ist": str(ref.t_end), "series_seconds": n,
        "n_gaps_gt10s": len(gaps), "missing_in_gaps_gt10s_s": int(ll.sum()),
        "n_gaps_all": int(len(starts)), "missing_all_s": int(n - n_valid),
        "by_month": months, "gaps": gaps,
    }
    (results / "reference_gaps.json").write_text(json.dumps(gap_doc, indent=1), encoding="utf-8")

    log("report: sanity + json done")
    # coverage plot
    fig, ax = plt.subplots(figsize=(11, 3.4), dpi=150)
    fig.patch.set_facecolor("#fcfcfb")
    ax.set_facecolor("#fcfcfb")
    x = mdates.date2num(pd.DatetimeIndex([d for d, _, _ in daily]).to_pydatetime())
    ax.fill_between(np.r_[x, x[-1] + 1], 0, np.r_[day_share, day_share[-1]] * 100, step="post",
                    color="#2a78d6", alpha=0.25, linewidth=0)
    ax.step(np.r_[x, x[-1] + 1], np.r_[day_share, day_share[-1]] * 100, where="post",
            color="#2a78d6", linewidth=1.0)
    for s in meta["sources"]:
        a = mdates.date2num(pd.Timestamp(s["first_valid_ist"]).to_pydatetime())
        b = mdates.date2num(pd.Timestamp(s["last_valid_ist"]).to_pydatetime())
        lvl = 108 if s["code"] % 2 else 114
        ax.plot([a, b], [lvl, lvl], color="#52514e", linewidth=2, solid_capstyle="butt")
        ax.text((a + b) / 2, lvl + 1.5, "file " + FILE_LABEL[s["file"]], ha="center", va="bottom",
                fontsize=7, color="#52514e")
    if sanity:
        lx = mdates.date2num(pd.Timestamp(sanity["lock_on_series_labels_ist"]).to_pydatetime())
        ax.axvline(lx, color="#0b0b0b", linewidth=0.8, linestyle=(0, (2, 2)))
        ax.text(lx, 50, " 5 Nov probe lock\n 11 Jan 2023", fontsize=7, color="#0b0b0b", va="center")
    ax.set_ylim(0, 122)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_ylabel("valid seconds per day (%)", fontsize=8, color="#52514e")
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 4, 7, 10)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.tick_params(labelsize=7, colors="#52514e")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color("#c3c2b7")
    ax.grid(axis="y", color="#e6e5e0", linewidth=0.6)
    ax.set_axisbelow(True)
    ax.set_title(f"Reference coverage: share of each day (IST) with a measured one-second value. "
                 f"Overall {stats['valid_pct']:.2f}% of {n:,} s.", fontsize=9, color="#0b0b0b", loc="left")
    fig.text(0.01, 0.01, "Source: Mendeley Data bvwh9d6227 (Grid-India NERLDC PMU, IIT Guwahati), CC BY 4.0. "
             "Bars above: span of each source file.", fontsize=6.5, color="#52514e")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(results / "reference_coverage.png", facecolor=fig.get_facecolor())
    plt.close(fig)

    write_summary(results, meta, build, stats, gap_doc)
    log("report done")


def _fmt_s(s: int) -> str:
    s = int(s)
    if s >= 86400:
        return f"{s:,} s ({s / 86400:.2f} d)"
    if s >= 3600:
        return f"{s:,} s ({s / 3600:.2f} h)"
    if s >= 60:
        return f"{s:,} s ({s / 60:.1f} min)"
    return f"{s:,} s"


def write_summary(results: Path, meta: dict, build: dict, stats: dict, gap_doc: dict):
    L = []
    a = L.append
    a("# Reference summary")
    a("")
    a("Generated by `scripts/build_reference.py` (stage `report`). Every number below is copied by the script from "
      "`results/reference_stats.json`, `results/reference_build_stats.json` and `results/reference_gaps.json`.")
    a("")
    a("Source: Mendeley Data dataset bvwh9d6227, Grid-India NERLDC PMU one-second frequency shared by IIT Guwahati, "
      "CC BY 4.0. Times are IST (UTC+05:30) wall-clock.")
    a("")
    a("## Span and coverage")
    a("")
    sp = stats["span"]
    a(f"- Span: {sp['t0_ist']} to {sp['t_end_ist']} IST, {sp['seconds']:,} seconds ({sp['days']} days).")
    a(f"- Valid seconds: {stats['valid_seconds']:,} ({stats['valid_pct']}%). Missing: {stats['missing_seconds']:,}.")
    a(f"- Gaps of any length: {stats['gaps_all']:,}. Gaps longer than {LONG_GAP_S} s: {stats['gaps_gt10s']:,}, "
      f"holding {stats['missing_in_gaps_gt10s_s']:,} missing seconds. Gaps of 1 to {LONG_GAP_S} s: "
      f"{stats['gaps_1_to_10s']:,}, holding {stats['missing_in_gaps_le10s_s']:,} seconds.")
    a(f"- Days fully covered: {stats['days_with_full_coverage']} of {stats['days_total']}. "
      f"Days with no data: {stats['days_with_zero_coverage']}.")
    a("")
    a("Longest gaps (first and last missing second, IST):")
    a("")
    a("| # | start | end | length |")
    a("|---|---|---|---|")
    for i, g in enumerate(stats["longest_gaps"][:10], 1):
        a(f"| {i} | {g['start_ist']} | {g['end_ist']} | {_fmt_s(g['length_s'])} |")
    a("")
    a("Coverage by month:")
    a("")
    a("| month | seconds in span | valid % | gaps > 10 s | missing in gaps > 10 s | longest gap |")
    a("|---|---|---|---|---|---|")
    for m in gap_doc["by_month"]:
        a(f"| {m['month']} | {m['seconds_in_span']:,} | {m['valid_pct']:.2f} | {m['gaps_gt10s_starting']} | "
          f"{m['missing_in_gaps_gt10s_s']:,} | {_fmt_s(m['longest_gap_starting_s'])} |")
    a("")
    a("Plot: `results/reference_coverage.png`.")
    a("")
    a("## Frequency")
    a("")
    f = stats["freq"]
    a(f"- Range of valid values: {f['min_hz']:.6f} to {f['max_hz']:.6f} Hz.")
    a(f"- Mean {f['mean_hz']:.5f} Hz, standard deviation {f['std_hz']:.5f} Hz.")
    p = f["percentiles_hz_from_0p0001_hist"]
    a(f"- Percentiles (from a 0.0001 Hz histogram): 0.1% {p['0.1']}, 1% {p['1']}, 50% {p['50']}, "
      f"99% {p['99']}, 99.9% {p['99.9']} Hz.")
    a(f"- Largest change between two consecutive valid seconds: {f['max_abs_1s_step_hz']:.6f} Hz. "
      f"Changes above 0.05 Hz in one second: {f['steps_gt_0p05hz']:,}.")
    s = stats["stuck_values"]
    a(f"- Repeated values (same value in consecutive valid seconds): {s['runs_ge_10s']:,} runs of 10 s or more "
      f"({s['seconds_in_runs_ge_10s']:,} s), {s['runs_ge_60s']:,} runs of 60 s or more. Longest: "
      f"{s['longest_run_s']:,} s from {s['longest_run_start_ist']}. These are kept as valid.")
    a("")
    a("## Source files")
    a("")
    a("| file | first valid (IST) | last valid (IST) | rows kept | valid rows | seconds used | dropped tail lines |")
    a("|---|---|---|---|---|---|---|")
    for src in meta["sources"]:
        pst = build["parse"][src["file"]]
        a(f"| {src['file']} | {src['first_valid_ist']} | {src['last_valid_ist']} | {src['rows_kept']:,} | "
          f"{src['rows_valid']:,} | {src['seconds_used']:,} | {pst['trailing_lines_dropped']} |")
    a("")
    a("Notes from parsing:")
    a("")
    for src in meta["sources"]:
        pst = build["parse"][src["file"]]
        ids = "; ".join(f"{k}: {', '.join(v)}" for k, v in pst["ids"].items()) or "no id columns"
        qn = ", ".join(f"{k}: {v:,}" for k, v in sorted(pst["quality_counts_numeric"].items())) or "no quality column"
        qe = ", ".join(f"{k}: {v:,}" for k, v in sorted(pst["quality_counts_empty"].items())) or "none"
        a(f"- {src['file']}: {pst['rows_empty_value']:,} empty values, {pst['rows_nonnumeric_value']} non-numeric, "
          f"{pst['rows_out_of_band']} outside {F_LO}-{F_HI} Hz, {pst['rows_bad_timestamp']} bad timestamps; "
          f"duplicate seconds {pst['steps']['dup0']}, backward steps {pst['steps']['backward']}. "
          f"Quality codes with a value: {qn}; without a value: {qe}. Ids: {ids}.")
    a("")
    a("Do non-zero quality codes mark odd values? Distance of each value from the mean of its two neighbours "
      "(median / 99th percentile / max, Hz; code 0 sampled every 50th row):")
    a("")
    for src in meta["sources"]:
        qr = build["parse"][src["file"]].get("neighbour_residual_by_quality")
        if qr:
            a(f"- {src['file']}: " + "; ".join(
                f"code {k} (n={v['n']:,}): {v['median_hz']:.2g} / {v['p99_hz']:.2g} / {v['max_hz']:.2g}"
                for k, v in qr.items()))
    a("")
    a("SHA-256 (whole file, computed by this script):")
    a("")
    for src in meta["sources"]:
        a(f"- {src['file']}: `{src['sha256']}`")
    a("")
    a("## Overlaps")
    a("")
    a(f"Rule: {OVERLAP_RULE}")
    a("")
    a("Agreement at the same time label, second by second:")
    a("")
    a("| files | window (IST) | both valid | identical | share identical | max abs diff (Hz) | mean abs diff (Hz) |")
    a("|---|---|---|---|---|---|---|")
    for o in build["overlaps"]:
        if o["both_valid"]:
            a(f"| {FILE_LABEL[o['a']]} x {FILE_LABEL[o['b']]} | {o['window_start_ist']} to {o['window_end_ist']} | "
              f"{o['both_valid']:,} | {o['identical']:,} | {o['share_identical']:.6f} | {o['max_abs_diff_hz']:.6g} | "
              f"{o['mean_abs_diff_hz']:.3g} |")
        else:
            a(f"| {FILE_LABEL[o['a']]} x {FILE_LABEL[o['b']]} | {o['window_start_ist']} to {o['window_end_ist']} | "
              f"0 | - | - | - | - |")
    a("")
    a("Seconds valid in only one file of each pair:")
    a("")
    for o in build["overlaps"]:
        a(f"- {FILE_LABEL[o['a']]} x {FILE_LABEL[o['b']]}: {o['window_seconds']:,} s window; only "
          f"{FILE_LABEL[o['a']]}: {o['a_only']:,}; only {FILE_LABEL[o['b']]}: {o['b_only']:,}; neither: {o['neither']:,}.")
    a("")
    a("Seconds taken from each file after the rule: " +
      ", ".join(f"{k}: {v:,}" for k, v in stats["source_seconds"].items()) + ".")
    a("")
    a("## Time labels: overlaps disagree by minutes")
    a("")
    a("The second-by-second comparison shows that overlapping files do not agree at the same label. Searching "
      f"shifts of up to +-{MAX_LAG_S // 3600} h finds one shift per pair at which the files agree almost "
      "exactly (`lag_search` in `results/reference_build_stats.json`):")
    a("")
    a("| files | r at same label | best shift (s) | r at best shift | pairs at best shift | identical at best shift | mean abs diff at best shift (Hz) | next best r (>10 min away) |")
    a("|---|---|---|---|---|---|---|---|")
    for o in build["overlaps"]:
        ls = o.get("lag_search")
        if not ls:
            continue
        a(f"| {FILE_LABEL[o['a']]} x {FILE_LABEL[o['b']]} | {ls['r_at_0']:.4f} | {ls['best_lag_s']} | "
          f"{ls['r_at_best']:.7f} | {ls['pairs_at_best']:,} | {ls['identical_at_best']:,} | "
          f"{ls['mean_abs_diff_at_best_hz']:.2g} | {ls.get('r_at_runner_up', float('nan')):.3f} |")
    a("")
    a("A best shift of k s means the earlier file labels the same moment k s later than the later file. "
      "The series uses the files' own labels without correction. Where it hands over from one file to the "
      "next, about that many seconds of real time are missing although the mask shows no gap:")
    a("")
    for j in build.get("junctions", []):
        a(f"- {j['handover_ist']}: {j['from_file']} -> {j['to_file']}, offset {j['label_offset_s']} s.")
    a("")
    tac = results / "time_axis_check.json"
    if tac.exists():
        tc = json.loads(tac.read_text(encoding="utf-8"))
        a("Within files, `scripts/check_time_axis.py` folds |frequency change per second| on the 15-minute "
          "schedule blocks and measures, for each 7-day window, how far the block features sit from where they "
          "sit in the first window of every file (`results/time_axis_check.json`, `results/time_axis_check.png`). "
          "First and last window of each file, in seconds:")
        a("")
        for name, v in tc["per_file"].items():
            a(f"- {name}: {v['first_window_shift_s']} -> {v['last_window_shift_s']} "
              f"({v['windows']} windows, median profile r {v['median_r']:.2f}).")
        a("")
        for c in tc.get("overlap_cross_check", []):
            a(f"- Cross-check in the {FILE_LABEL[c['a']]} x {FILE_LABEL[c['b']]} overlap: block shift difference "
              f"{c['block_shift_a_minus_b_s']:.0f} s against the exact overlap offset {c['label_offset_from_overlap_s']} s.")
        a("")
        a("So labels drift late within each file, in steps, by up to a few minutes. The block check is relative: "
          "it assumes each file starts on time. The probe check below supports that for file V in January 2023. "
          "Before any result relies on absolute time to better than a few minutes, the labels need a correction "
          "(for example from this block clock); this build does not apply one.")
        a("")
    a("## Sanity check near the 5 Nov probe lock")
    a("")
    sn = stats["sanity"]
    if not sn:
        a("The probe lock time is outside this series, or the probe output is missing; check skipped.")
    else:
        pr = sn["probe"]
        a(f"- Probe line (`docs/probe/enf_margin_out.txt`): `{pr['line']}`. The probe used file IV alone. "
          f"Its lock {pr['lock_ist_file_IV_labels']} is the inferred recording start on file IV's labels; "
          f"YouTube's start converted to IST is {pr['youtube_start_ist']}.")
        a(f"- Index mapping: {pr['lock_ist_file_IV_labels']} -> index {sn['lock_index']:,} -> {sn['round_trip_ist']} "
          f"(round trip ok: {sn['round_trip_ok']}; index equals seconds since t0: "
          f"{sn['index_equals_seconds_from_t0']}). The probe's own index on file IV was "
          f"{sn['probe_index_in_file_IV']:,}; file IV's first row is series index "
          f"{sn['series_index_of_file_IV_first_row']:,}; the difference is "
          f"{sn['series_index_minus_probe_index']:,}, as it should be.")
        a(f"- At that label the series takes {sn['source_at_lock_label']} (later file wins). File IV labels "
          f"the same moments {sn['iv_minus_v_label_offset_s']} s later than file V. So the 40-min excerpt's "
          f"matched window, {sn['matched_window_file_IV_labels_ist'][0]} to "
          f"{sn['matched_window_file_IV_labels_ist'][1]} on file IV's labels, is "
          f"{sn['matched_window_series_labels_ist'][0]} to {sn['matched_window_series_labels_ist'][1]} on this "
          f"series' labels. Valid share there: {sn['valid_share_matched_window']:.4f} "
          f"(sources: {sn.get('matched_window_source_counts')}).")
        for fname, c in sn["raw_csv_vs_series"].items():
            a(f"- Raw text of {fname} ({c['rows_from_ist']} to {c['rows_to_ist']}, found by binary search in "
              f"the CSV, not through the parser), compared with the series {c['series_shift_s']} s earlier: "
              f"{c['rows_compared']:,} rows compared, {c['equal_as_float32']:,} equal as float32, max abs diff "
              f"{c['max_abs_diff_hz']:.3g} Hz. Row at the probe lock label: {c['row_at_probe_lock_label']}.")
        a(f"- On this series' labels the probe lock becomes {sn['lock_on_series_labels_ist']}, "
          f"{sn['lock_on_series_labels_minus_youtube_start_s']:+d} s from YouTube's start, instead of "
          f"{pr['offset_vs_youtube_s']:+d} s on file IV's labels. Most of the offset the probe read as start "
          f"latency is file IV's label offset from file V at that date ({sn['iv_minus_v_label_offset_s']} s).")
        a(f"- IST, not UTC: on either file's labels the lock is minutes from YouTube's start, not 19,800 s "
          f"(5 h 30 min). Valid share over +-1 day: {sn['valid_share_plus_minus_1_day']:.4f}; over +-90 days: "
          f"{sn['valid_share_plus_minus_90_days']:.4f}.")
    a("")
    a("## Files")
    a("")
    a("- Series: `grid_1s_f32.npy` (float32 Hz, NaN where missing), `grid_1s_valid_u8.npy` (1 = measured), "
      "`grid_1s_src_u8.npy` (source file 1..5), `grid_1s_meta.json`, in the derived folder outside the repo.")
    a("- Load with `gunj.reference.load()`; map times with `index_of()` / `time_of()`; scopes with `scope()`.")
    (results / "reference_summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref-dir", type=Path, required=True, help="folder with freq_I.csv .. freq_V.csv")
    ap.add_argument("--derived-dir", type=Path, required=True, help="output folder for large arrays")
    ap.add_argument("--results-dir", type=Path, default=REPO / "results")
    ap.add_argument("--stage", choices=["parse", "assemble", "report", "all"], default="all")
    ap.add_argument("--files", nargs="*", help="parse only these labels, e.g. IV V")
    ap.add_argument("--chunk-rows", type=int, default=500_000)
    ap.add_argument("--block", type=int, default=4_000_000, help="rows/seconds per block in assemble/report")
    args = ap.parse_args(argv)
    global LOG_PATH
    args.results_dir.mkdir(parents=True, exist_ok=True)
    LOG_PATH = args.results_dir / "reference_build.log"
    log(f"start stage={args.stage} ref_dir={args.ref_dir} derived_dir={args.derived_dir}")
    if args.stage in ("parse", "all"):
        stage_parse(args.ref_dir, args.derived_dir, args.chunk_rows, args.files)
    if args.stage in ("assemble", "all"):
        stage_assemble(args.ref_dir, args.derived_dir, args.results_dir, args.block)
    if args.stage in ("report", "all"):
        stage_report(args.ref_dir, args.derived_dir, args.results_dir, args.block)


if __name__ == "__main__":
    main()
