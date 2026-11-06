"""Time and memory of one ncc_search over the real reference.

This is a wiring and performance check, not an accuracy result. The query is
a 20-minute stretch of the reference itself plus SYNTHETIC Gaussian noise
(0.002 Hz), so the right answer is known. Scopes: full, +-90 days, +-1 day.

Usage (from the repo root):
  .venv\\Scripts\\python.exe scripts\\bench_search.py ^
      --ref-dir "..\\data\\derived" --out results\\bench_search.json
"""
from __future__ import annotations

import gunj  # noqa: F401  (first, so its OPENBLAS_NUM_THREADS default applies)
import argparse
import ctypes
import json
import sys
import time
import tracemalloc
from pathlib import Path

import numpy as np

from gunj import reference as gref
from gunj.match import DEFAULT_NFFT, ncc_search


def peak_memory_mb() -> dict:
    if sys.platform != "win32":
        import resource
        return {"peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}

    class PMC(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    from ctypes import wintypes
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
            "peak_private_mb": round(c.PeakPagefileUsage / 2 ** 20, 1)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--length", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)

    ref = gref.load(args.ref_dir, mmap=True)
    rng = np.random.default_rng(args.seed)
    n = args.length
    i = None
    for cand in range(int(rng.integers(0, len(ref) - n)), len(ref) - n, 3600):
        if ref.valid[cand:cand + n].all():
            i = cand
            break
    if i is None:
        raise SystemExit("no fully valid stretch found")
    q = np.asarray(ref.freq[i:i + n], dtype=np.float64) + rng.normal(0, 0.002, n)

    tracemalloc.start()
    runs = []
    for label, (a, b) in (("full", ref.scope_full()),
                          ("+-90d", ref.scope(ref.time_of(i), days=90)),
                          ("+-1d", ref.scope(ref.time_of(i), days=1))):
        tracemalloc.reset_peak()
        t = time.time()
        res = ncc_search(q, ref.freq, ref.valid, start=a, stop=b)
        runs.append({
            "scope": label, "positions_in_scope": b - a,
            "positions_with_valid_window": res.n_positions,
            "seconds": round(time.time() - t, 2),
            "tracemalloc_peak_mb": round(tracemalloc.get_traced_memory()[1] / 2 ** 20, 1),
            "top1_index": res.top.index, "top1_correct": res.top.index == i,
            "top1_r": round(res.top.r, 4), "runner_up_r": round(res.runner_up.r, 4),
            "margin": round(res.margin, 4), "confidence": round(res.confidence, 4),
        })
        print(runs[-1], flush=True)
    out = {
        "what": ("Self-match timing/memory check on the real reference. Query = reference "
                 "stretch plus SYNTHETIC noise; not an accuracy result."),
        "reference_seconds": len(ref), "query_length_s": n, "query_start_index": i,
        "query_start_ist": str(ref.time_of(i)), "synthetic_noise_hz": 0.002,
        "nfft": DEFAULT_NFFT, "runs": runs, "process_memory": peak_memory_mb(),
    }
    tmp = Path(args.out + ".tmp")
    tmp.write_text(json.dumps(out, indent=1), encoding="utf-8")
    tmp.replace(args.out)          # never leave a half-written result
    print(json.dumps(out["process_memory"]))


if __name__ == "__main__":
    main()
