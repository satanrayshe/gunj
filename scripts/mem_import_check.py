"""Memory committed by importing numpy and scipy, with and without
OPENBLAS_NUM_THREADS=1. Windows only. Writes a small JSON.

Usage: .venv\\Scripts\\python.exe scripts\\mem_import_check.py --out results\\mem_import_check.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

CHILD = r'''
import ctypes, json, os
from ctypes import wintypes
class PMC(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("a", ctypes.c_size_t), ("b", ctypes.c_size_t), ("c", ctypes.c_size_t),
                ("d", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]
k32 = ctypes.WinDLL("kernel32"); psapi = ctypes.WinDLL("psapi")
k32.GetCurrentProcess.restype = wintypes.HANDLE
psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
def m():
    c = PMC(); c.cb = ctypes.sizeof(PMC)
    psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return {"working_set_mb": round(c.WorkingSetSize / 2**20, 1),
            "private_commit_mb": round(c.PagefileUsage / 2**20, 1)}
out = {"start": m()}
import numpy
out["after_numpy"] = m()
import scipy.fft, scipy.signal, scipy.ndimage
out["after_scipy"] = m()
print(json.dumps(out))
'''


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    res = {"cpu_count": os.cpu_count()}
    for label, val in (("default", None), ("OPENBLAS_NUM_THREADS=1", "1")):
        env = dict(os.environ)
        env.pop("OPENBLAS_NUM_THREADS", None)
        if val:
            env["OPENBLAS_NUM_THREADS"] = val
        p = subprocess.run([sys.executable, "-c", CHILD], env=env, capture_output=True,
                           text=True, check=True)
        res[label] = json.loads(p.stdout)
    tmp = Path(args.out + ".tmp")
    tmp.write_text(json.dumps(res, indent=1), encoding="utf-8")
    tmp.replace(args.out)          # never leave a half-written result
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
