#!/usr/bin/env python
"""Export compact JSON for the results explorer in site/.

Reads only files that other scripts wrote (results/, traces/, data/,
gunj/config.json, PROTOCOL.md, scripts/evaluate.py for the decision time) and
the one-second reference (memory-mapped, never loaded whole). Writes:

  site/data/overview.json                 reference ribbon, label drift, uniqueness
                                          (SYNTHETIC noise simulation), latency L,
                                          tau, Calibration and Dev summaries, the
                                          hero lock, and the Test results
  site/data/clips.json                    one summary row per clip
  site/data/clips/<split>/<video_id>.json one page per clip

Test results in overview.json, each copied from the file named beside it:
  test          M2, M3 and the gate from results/evaluation.json and
                results/gate_frozen.json; M1 counted from results/test_results.csv
  breakdown     confident verdicts by distance from T* (results/error_breakdown.json),
                recounted here from results/test_results.csv before it is copied
  m5            edit detection on SYNTHETIC cuts (results/m5.json)
  m4_measured   the uniqueness table read at the measured noise level
                (results/m4_measured.json)
  figures       the numbers of the M2 signed-error plot and the M3 reliability
                plot (results/figures/figures.json), which the site draws itself
  libhum        "not run", with the sentence of PROTOCOL.md section 19 that says why

The four later files record the SHA-256 of the scoring outputs they were
computed from. If one of them no longer matches results/test_results.csv,
results/evaluation.json, results/false_matches.json or results/gate_frozen.json,
the export stops with exit status 2 and names the script to re-run. A block
whose file does not exist is written as null, and the site says "not computed".

Every file is written to <name>.tmp and then renamed.

Searches run here (all with the frozen matcher, gunj/match_v2.wncc_search,
single thread, small FFT blocks):
- For every Dev and Calibration clip, the 60-min v2 excerpt in the +-1-day
  scope, to list the top 5 candidates. The result is checked against the
  number the pipeline recorded (results/calibration.json, results/v2_tuning.json).
- For the hero clip only, the same excerpt over the full 32 months
  (--no-full-search skips it).
No Test search is run here; Test numbers come from results/test_results.csv.

Usage (from the repo root):
  .venv\\Scripts\\python.exe scripts\\export_site_data.py
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import argparse                                   # noqa: E402
import csv                                        # noqa: E402
import datetime as dt                             # noqa: E402
import hashlib                                    # noqa: E402
import json                                       # noqa: E402
import math                                       # noqa: E402
import re                                         # noqa: E402
import sys                                        # noqa: E402
import time                                       # noqa: E402
import warnings                                   # noqa: E402
from pathlib import Path                          # noqa: E402

import numpy as np                                # noqa: E402

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from gunj import reference as gref                # noqa: E402
from gunj.extract_v2 import V2_FROZEN, combine    # noqa: E402
from gunj.match_v2 import wncc_search             # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
FRAME0_S = 128            # trace frame 0 sits 128 s into the recording (PROTOCOL section 8)
EXCERPT_START_S = 120
HERO_LENGTH_MIN = 60
HIT_S = 30
SEARCH_NFFT = 1 << 18     # small FFT blocks keep memory low; r does not depend on the block size
BIN_S = 3600              # ribbon bin
HERO_STEP_S = 2
CLIP_STEP_S = 3
HERO_CONTEXT_S = 1800
CLIP_CONTEXT_S = 600
SITE_BUDGET_BYTES = 8 * 1024 * 1024
ABS_PATH_RE = re.compile(r"([A-Za-z]:[\\/]+(Users|Documents|Program)|Users[\\/]+Shrey|AppData)", re.I)
SCOPE_LABEL = {"full": "full 32 months", "90d": "±90 days", "1d": "±1 day"}
SPLIT_LABEL = {"dev": "Dev", "calibration": "Calibration", "test": "Test", "practice": "PRACTICE"}
REPO_URL = "https://github.com/satanrayshe/gunj"
PRIMARY = "v2"            # PROTOCOL section 7
PRIMARY_LENGTH_MIN = 60   # PROTOCOL section 13: the primary endpoint is 60 min in the full scope
LENGTHS = (10, 20, 40, 60)
SCOPES = ("full", "90d", "1d")
EXTRACTORS = ("v2", "v1", "naive")
Z95 = 1.959964            # PROTOCOL section 13
CLASS_KEYS = ("within_30", "from_30_to_120", "from_120_to_600", "beyond_600", "no_candidate")

SOURCES: dict = {}        # repo-relative path -> sha256, for every input file read


class StaleInput(Exception):
    """A later result file was computed from another version of the scoring outputs."""


# --------------------------------------------------------------------- helpers

def now_ist() -> str:
    return dt.datetime.now(IST).isoformat(timespec="seconds")


def rel(p: Path) -> str:
    p = Path(p).resolve()
    try:
        return p.relative_to(REPO).as_posix()
    except ValueError:
        try:
            return "../" + p.relative_to(REPO.parent).as_posix()
        except ValueError:
            return p.name


def note_source(p: Path) -> None:
    p = Path(p)
    if p.exists() and p.is_file() and p.stat().st_size < 64 * 1024 * 1024:
        SOURCES[rel(p)] = hashlib.sha256(p.read_bytes()).hexdigest()


def read_json(p: Path):
    note_source(p)
    return json.loads(Path(p).read_text(encoding="utf-8"))


def read_csv(p: Path) -> list:
    note_source(p)
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_json_atomic(p: Path, obj) -> int:
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    m = ABS_PATH_RE.search(text)
    if m:
        raise ValueError(f"{p.name}: refusing to write an absolute local path ({m.group(0)!r})")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)
    return len(text.encode("utf-8"))


def fnum(v, nd=None):
    """Float or None from a CSV/JSON value; NaN and '' become None."""
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return round(x, nd) if nd is not None else x


def fint(v):
    x = fnum(v)
    return None if x is None else int(round(x))


def fbool(v):
    if isinstance(v, bool):
        return v
    if v in (None, ""):
        return None
    return str(v).strip().lower() == "true"


def enc_hz(a, scale=1e4, base=50.0) -> list:
    """Hz values as integers of 1/scale Hz relative to base (0.1 mHz by default); NaN -> None."""
    a = np.asarray(a, dtype=np.float64)
    out = np.round((a - base) * scale)
    return [None if not math.isfinite(v) else int(v) for v in out.tolist()]


def decimate(x, w, step: int):
    """Weighted mean of each block of `step` frames (plain mean where all weights are 0)."""
    x = np.asarray(x, dtype=np.float64)
    w = np.ones_like(x) if w is None else np.asarray(w, dtype=np.float64)
    n = x.size // step * step
    if n == 0:
        return x, w
    xb, wb = x[:n].reshape(-1, step), w[:n].reshape(-1, step)
    sw = wb.sum(1)
    with np.errstate(invalid="ignore", divide="ignore"):
        xm = np.where(sw > 0, (xb * wb).sum(1) / np.where(sw > 0, sw, 1), xb.mean(1))
    return xm, wb.mean(1)


def ref_window(ref, a: int, b: int, step: int) -> dict:
    """Reference values for [a, b) (clipped), block means of `step` seconds; missing -> None."""
    a0, b0 = max(0, int(a)), min(len(ref), int(b))
    seg = np.asarray(ref.freq[a0:b0], dtype=np.float64)
    n = seg.size // step * step
    seg = seg[:n].reshape(-1, step)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        m = np.nanmean(seg, axis=1)
    return {"start_index": a0, "start_ist": ist(ref, a0), "step_s": step, "values": enc_hz(m)}


def ist(ref, i: int) -> str:
    return str(ref.time_of(int(i)))


def index_of_ist(ref, s: str) -> int:
    return ref.index_of(s, check=False)


def hum_labels(med: dict) -> dict:
    """{1: ratio, ...} or {'50 Hz': ratio} -> {'50 Hz': ratio}."""
    out = {}
    for k, v in (med or {}).items():
        key = f"{50 * int(k)} Hz" if str(k).isdigit() else str(k)
        out[key] = None if v is None else round(float(v), 3)
    return out


def rate(d) -> dict | None:
    if not isinstance(d, dict):
        return None
    return {"k": d.get("k"), "n": d.get("n"), "rate": d.get("rate"), "wilson95": d.get("wilson95")}


def wilson(k: int, n: int, z: float = Z95):
    """Wilson 95% interval, the formula of PROTOCOL section 13 (the same as scripts/eval_common.py)."""
    if n <= 0:
        return None
    p = k / n
    den = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [max(0.0, centre - half), min(1.0, centre + half)]


def rate_kn(k: int, n: int) -> dict:
    """A rate counted here, in the shape scripts/evaluate.py writes."""
    return {"k": int(k), "n": int(n), "rate": None if n <= 0 else k / n, "wilson95": wilson(k, n)}


def sha256_of(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def need_same(what: str, recorded, path: Path, rerun: str) -> None:
    """Stop if `what` was computed from another version of the file at `path`."""
    if not recorded:
        raise StaleInput(f"{what} does not record the SHA-256 of {rel(path)}; re-run {rerun}")
    if not Path(path).exists() or sha256_of(path) != recorded:
        raise StaleInput(f"{what} was computed from another version of {rel(path)}; re-run {rerun}")


def dur_text(s: float) -> str:
    """A length of time in words, with the rule scripts/fill_results.py uses for the README:
    under 2 min in seconds, under 2 h in minutes, under 2 days in hours, otherwise in days."""
    s = abs(float(s))
    if s < 120:
        return f"{int(s)} s" if s == int(s) else f"{s:g} s"
    if s < 2 * 3600:
        return f"{round(s / 60)} minutes"
    if s < 2 * 86400:
        return f"{s / 3600:.1f} hours"
    return f"{s / 86400:.1f} days"


def utc_to_ist(s: str | None) -> str | None:
    """'2026-11-08T00:00:42+00:00' -> '2026-11-08T05:30:42+05:30'; None if it cannot be read."""
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(IST).isoformat(timespec="seconds")


def git_head() -> str | None:
    """Commit hash of HEAD, read from .git files (no git command)."""
    g = REPO / ".git"
    try:
        head = (g / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head[:12]
        ref = head.split(" ", 1)[1].strip()
        f = g / ref
        if f.exists():
            return f.read_text(encoding="utf-8").strip()[:12]
        packed = g / "packed-refs"
        if packed.exists():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.endswith(" " + ref):
                    return line.split(" ", 1)[0][:12]
    except OSError:
        return None
    return None


def peak_memory_mb() -> dict:
    """Peak working set and peak private memory of this process (Windows only)."""
    if os.name != "nt":
        return {}
    import ctypes
    from ctypes import wintypes

    class PMC(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    try:
        k32, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        c = PMC()
        c.cb = ctypes.sizeof(PMC)
        if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return {}
        return {"peak_working_set_mb": round(c.PeakWorkingSetSize / 2 ** 20),
                "peak_private_mb": round(c.PeakPagefileUsage / 2 ** 20)}
    except OSError:
        return {}


def decision_time() -> str | None:
    p = REPO / "scripts" / "evaluate.py"
    m = re.search(r'^DECISION_TIME\s*=\s*"([^"]+)"', p.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


# ------------------------------------------------------------------ searches

def scope_positions(ref, release_ts: int, scope: str) -> tuple:
    """Search positions [p0, p1) for a scope; same rule as scripts/calibrate_latency.py."""
    if scope == "full":
        i0, i1 = ref.scope_full()
    else:
        i0, i1 = ref.scope(gref.unix_to_ist(int(release_ts)), days={"90d": 90, "1d": 1}[scope])
    return i0 + FRAME0_S, min(i1 + FRAME0_S, len(ref))


def run_search(ref, freq, fw, release_ts: int, scope: str) -> dict:
    t = time.time()
    p0, p1 = scope_positions(ref, release_ts, scope)
    res = wncc_search(np.asarray(freq, dtype=np.float64), np.asarray(fw, dtype=np.float64),
                      ref.freq, ref.valid, start=p0, stop=p1, k=5, nfft=SEARCH_NFFT)
    cands = [{"index": int(c.index), "start_index": int(c.index) - FRAME0_S,
              "start_ist": ist(ref, int(c.index) - FRAME0_S), "r": round(float(c.r), 6)}
             for c in res.candidates]
    ru = res.runner_up
    return {"scope": scope, "positions_searched": int(res.n_positions), "candidates": cands,
            "runner_up": None if ru is None else {"start_index": int(ru.index) - FRAME0_S,
                                                  "start_ist": ist(ref, int(ru.index) - FRAME0_S),
                                                  "r": round(float(ru.r), 6)},
            "confidence": None if res.confidence is None else round(float(res.confidence), 6),
            "_conf": None if res.confidence is None else float(res.confidence),
            "seconds": round(time.time() - t, 2)}


def verdict_of(hum_flag, conf, tau) -> tuple:
    if conf is None:
        return "no verdict", "no runner-up candidate"
    if tau is None:
        return "no verdict", "tau not set for this cell"
    if not hum_flag:
        return "no verdict", "no hum flag on this trace (no harmonic with median peak-to-floor of 3 or more)"
    if conf < tau:
        return "no verdict", "confidence below tau for this cell"
    return "match", "hum flag set and confidence at or above tau"


# ------------------------------------------------------------------- overview

def build_ribbon(ref) -> dict:
    """Hourly min and max of the reference (mHz from 50 Hz), clock-hour bins, gaps as null."""
    t = time.time()
    n = len(ref)
    t0 = ref.t0.to_pydatetime()
    off = (t0.minute * 60 + t0.second) % BIN_S
    first = t0 - dt.timedelta(seconds=off)
    nb = -(-(n + off) // BIN_S)
    mn = np.full(nb, np.nan)
    mx = np.full(nb, np.nan)
    cnt = np.zeros(nb, dtype=np.int64)
    chunk = 240
    for b0 in range(0, nb, chunk):
        b1 = min(nb, b0 + chunk)
        i0, i1 = b0 * BIN_S - off, b1 * BIN_S - off
        seg = np.full((b1 - b0) * BIN_S, np.nan, dtype=np.float32)
        a, z = max(i0, 0), min(i1, n)
        seg[a - i0:z - i0] = ref.freq[a:z]
        m = seg.reshape(b1 - b0, BIN_S)
        cnt[b0:b1] = np.isfinite(m).sum(axis=1)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            mn[b0:b1] = np.nanmin(m, axis=1)
            mx[b0:b1] = np.nanmax(m, axis=1)
        del seg, m
    gap = cnt < BIN_S // 2
    mn[gap] = np.nan
    mx[gap] = np.nan
    return {"first_bin_start_ist": first.strftime("%Y-%m-%d %H:%M:%S"),
            "first_bin_index": -off, "bin_s": BIN_S, "n_bins": int(nb),
            "units": "mHz relative to 50 Hz (integer); null = under half of the hour valid",
            "min": enc_hz(mn, scale=1e3), "max": enc_hz(mx, scale=1e3),
            "n_gap_bins": int(gap.sum()), "seconds": round(time.time() - t, 1),
            "source": "../data/derived/grid_1s_f32.npy (memory-mapped)"}


def build_reference_block(ref, derived: Path) -> dict:
    st = read_json(REPO / "results" / "reference_stats.json")
    gaps = read_json(REPO / "results" / "reference_gaps.json")
    meta_p = derived / gref.META_NAME
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    files = []
    for s in meta.get("sources", []):
        files.append({"file": s["file"], "code": s["code"], "first_valid_ist": s.get("first_valid_ist"),
                      "last_valid_ist": s.get("last_valid_ist"), "seconds_used": s.get("seconds_used"),
                      "sha256": s.get("sha256")})
    junctions = [{"from": j["from_file"], "to": j["to_file"], "handover_ist": j["handover_ist"],
                  "handover_index": index_of_ist(ref, j["handover_ist"]), "label_offset_s": j["label_offset_s"]}
                 for j in meta.get("junctions", [])]
    big = [{"start_index": g["start_index"], "length_s": g["length_s"], "start_ist": g["start_ist"],
            "end_ist": g["end_ist"]} for g in gaps.get("gaps", []) if g["length_s"] >= 600]
    return {
        "dataset": "Mendeley Data bvwh9d6227: Grid-India NERLDC PMU one-second frequency, shared by IIT Guwahati, CC BY 4.0",
        "t0_ist": st["span"]["t0_ist"], "t_end_ist": st["span"]["t_end_ist"],
        "seconds": st["span"]["seconds"], "days": st["span"]["days"],
        "valid_seconds": st["valid_seconds"], "valid_pct": st["valid_pct"], "missing_seconds": st["missing_seconds"],
        "gaps_gt10s": st["gaps_gt10s"], "gaps_all": st["gaps_all"],
        "days_full": st["days_with_full_coverage"], "days_total": st["days_total"],
        "freq_min_hz": round(st["freq"]["min_hz"], 4), "freq_max_hz": round(st["freq"]["max_hz"], 4),
        "freq_std_hz": round(st["freq"]["std_hz"], 5),
        "files": files, "junctions": junctions, "gaps_ge_10min": big,
        "sources": ["results/reference_stats.json", "results/reference_gaps.json", "../data/derived/grid_1s_meta.json"],
    }


def build_drift() -> dict:
    d = read_json(REPO / "results" / "time_axis_check.json")
    ss = read_json(REPO / "results" / "splits_summary.json")
    per_file = {k: v for k, v in d["per_file"].items()}
    worst = max(per_file.items(), key=lambda kv: max(abs(kv[1]["max_shift_s"]), abs(kv[1]["min_shift_s"])))
    test = ss.get("splits", {}).get("test", {})
    return {
        "method": d["method"],
        "per_file": per_file,
        "windows": [{"file": w["file"], "mid": w["window_mid_ist"], "shift_s": w["shift_s"], "r": w["r"]}
                    for w in d["per_window"]],
        "overlaps": [{"a": o["a"], "b": o["b"], "label_offset_s": o["label_offset_from_overlap_s"]}
                     for o in d["overlap_cross_check"]],
        "largest_shift": {"file": worst[0], "max_shift_s": worst[1]["max_shift_s"],
                          "min_shift_s": worst[1]["min_shift_s"]},
        "test_abs_D_gt_30s": test.get("abs_label_drift_gt_30s"), "test_n": test.get("n"),
        "test_abs_D": test.get("abs_label_drift_s"),
        "stable_rule": "label-stable if the drift window's profile correlation r is at least 0.5 (PROTOCOL section 6)",
        "sources": ["results/time_axis_check.json", "results/splits_summary.json"],
    }


def build_uniqueness() -> dict:
    rows = read_csv(REPO / "results" / "uniqueness_table.csv")
    run = read_json(REPO / "results" / "uniqueness_run.json")
    probe = read_json(REPO / "results" / "probe_noise.json")
    out = []
    for r in rows:
        out.append({"scope": r["scope"], "length_min": fint(r["length_min"]), "noise": r["noise_kind"],
                    "sigma_mhz": fnum(r["sigma_mhz"]), "N": fint(r["N"]), "hits": fint(r["hits"]),
                    "hit_rate": fnum(r["hit_rate"]), "lo": fnum(r["wilson95_lo"]), "hi": fnum(r["wilson95_hi"]),
                    "random_guess": fnum(r["random_guess_hit_rate"])})
    labels = sorted({r["noise_label"] for r in rows})
    s = probe.get("summary", {})
    return {
        "label": "SYNTHETIC noise simulation on the reference only. No audio.",
        "noise_labels": labels, "seed": run.get("seed"), "hit_tol_s": run.get("hit_tol_s"),
        "rows": out,
        "probe_sigma_mhz": {"median": round(1000 * s["sigma_eff_all_median_hz"], 1),
                            "min": round(1000 * s["sigma_eff_all_min_hz"], 1),
                            "max": round(1000 * s["sigma_eff_all_max_hz"], 1), "n": s["n_all_points"]}
        if s else None,
        "sources": ["results/uniqueness_table.csv", "results/uniqueness_run.json", "results/probe_noise.json"],
    }


def build_tau() -> dict:
    d = read_json(REPO / "results" / "threshold.json")
    cells = {}
    for ext, by_len in d["cells"].items():
        for m, by_scope in by_len.items():
            for sc, c in by_scope.items():
                cells.setdefault(ext, {}).setdefault(m, {})[sc] = {
                    "tau": c.get("tau"), "n": c.get("n"), "n_real": c.get("n_real"),
                    "n_synthetic": c.get("n_synthetic"), "status": c.get("status")}
    return {"written_ist": d["written_ist"], "rule": d["rule"], "primary_extractor": d["primary_extractor"],
            "n_negatives": d.get("n_negatives"), "excluded": [e.get("video_id") for e in d.get("excluded", [])],
            "cells": cells, "sources": ["results/threshold.json"]}


def tau_of(tau_block: dict, ext: str, m: int, scope: str):
    try:
        return tau_block["cells"][ext][str(m)][scope]["tau"]
    except (KeyError, TypeError):
        return None


def build_latency(cal: dict) -> dict:
    lat = read_json(REPO / "results" / "latency.json")
    rows = []
    for s in cal["streams"]:
        lk = (s.get("locks") or {}).get("v2")
        if not lk:
            continue
        rows.append({"video_id": s["video_id"], "o_s": lk.get("o_s"), "confidence": lk.get("confidence"),
                     "counted": lk.get("confidence") is not None and lk["confidence"] >= lat["tau"]
                     and abs(lk.get("o_s", 10 ** 9)) <= cal["max_abs_o_s"]})
    by_ext = {}
    for ext, v in cal["latency"].items():
        by_ext[ext] = {"n_streams": v["n_streams"], "n_counted": v["counted"]["n"], "status": v["status"],
                       "L_s": v["L_s"], "median_s": v["counted"].get("median_s"),
                       "range_s": v["counted"].get("range_s"), "tau": v["tau"]}
    return {"L_s": lat["L_s"], "status": lat["status"], "written_ist": lat["written_ist"],
            "counted": lat["counted"], "tau_60_1d": lat["tau"], "streams": rows, "by_extractor": by_ext,
            "rule": "counted: confidence at or above tau (60 min, ±1 day) and |o| at most 600 s; L = median of counted o",
            "sources": ["results/latency.json", "results/calibration.json"]}


def build_test_block(tdir: Path) -> dict | None:
    ev_p = tdir / "evaluation.json"
    gate_p = tdir / "gate.md"
    if not ev_p.exists() and not gate_p.exists():
        return None
    out = {"sources": []}
    if ev_p.exists():
        d = read_json(ev_p)
        out["sources"].append(rel(ev_p))
        m2 = {}
        for ext, by_len in (d.get("m2") or {}).items():
            for m, by_scope in by_len.items():
                for sc, c in by_scope.items():
                    m2.setdefault(ext, {}).setdefault(m, {})[sc] = {
                        "n": c.get("n"), "tau": c.get("tau"),
                        **{k: rate(c.get(k)) for k in ("top1_hit", "top1_hit_D0", "top1_hit_hum_present",
                                                       "confident_hit", "confident_wrong", "no_verdict")}}
        m3 = {}
        for ext, by_len in (d.get("m3") or {}).items():
            for m, by_scope in by_len.items():
                for sc, c in by_scope.items():
                    m3.setdefault(ext, {}).setdefault(m, {})[sc] = {
                        g: {k: rate((c.get(g) or {}).get(k)) for k in ("false_match", "false_match_with_hum_flag")}
                        for g in ("real", "SYNTHETIC", "together")}
        g = d.get("gate") or {}
        out.update({
            "written_ist": d.get("written_ist"), "mode": d.get("mode"), "split_label": d.get("split_label"),
            "after_decision_time": d.get("after_decision_time"), "decision_time": d.get("decision_time"),
            "latency_line": d.get("latency_line"), "n_selected": d.get("n_selected"),
            "n_with_trace": d.get("n_with_trace"),
            "flow": [{"step": s["step"], "n": s["n"]} for s in (d.get("flow") or {}).get("steps", [])],
            "gate": {"N_processed": g.get("N_processed"), "gate_met": g.get("gate_met"),
                     "condition_1_2_met": g.get("condition_1_2_met"), "condition_3_met": g.get("condition_3_met"),
                     "tau_L_committed_in_time": g.get("tau_L_committed_in_time"),
                     "by_extractor": {e: {k: v for k, v in b.items() if k != "locked_streams"}
                                      | {"locked_streams": b.get("locked_streams", [])}
                                      for e, b in (g.get("by_extractor") or {}).items()}},
            "gate_freeze": d.get("gate_freeze"), "m2": m2, "m3": m3, "m3_counts": d.get("m3_counts"),
        })
    frozen_p = tdir / "gate_frozen.json"
    out["gate_frozen"] = frozen_p.exists()
    if frozen_p.exists():
        fz = read_json(frozen_p)
        out["sources"].append(rel(frozen_p))
        out["gate_frozen_ist"] = fz.get("frozen_ist")
        out["gate_frozen_met"] = (fz.get("gate") or {}).get("gate_met")
    if gate_p.exists():
        text = gate_p.read_text(encoding="utf-8")
        note_source(gate_p)
        out["sources"].append(rel(gate_p))
        m = re.search(r"^## Verdict:\s*(.+)$", text, re.M)
        out["gate_md_verdict"] = m.group(1).strip() if m else None
        out["gate_md_interim"] = "INTERIM" in text
        out["gate_md_practice"] = "PRACTICE" in text
    return out


# ---------------------------------------------------------------------- clips

def splits_rows() -> dict:
    return {r["video_id"]: r for r in read_csv(REPO / "data" / "splits.csv")}


def base_meta(s: dict) -> dict:
    return {"video_id": s["video_id"], "channel": s.get("channel"), "venue_type": s.get("venue_type"),
            "licence": s.get("licence"), "licence_cc": fbool(s.get("license_cc")),
            "official_status": s.get("official_status"), "release_time_ist": s.get("release_time_ist"),
            "release_timestamp": fint(s.get("release_timestamp")), "D_s": fnum(s.get("label_drift_s")),
            "label_stable": fbool(s.get("label_stable")), "drift_window_r": fnum(s.get("drift_window_r")),
            "ref_src_file": s.get("ref_src_file"),
            "excerpt_lengths_min": [int(x) for x in (s.get("excerpt_lengths_min") or "").split(";") if x]}


def truth_index(ref, release_ts: int, L: int, D: float) -> int:
    t = gref.unix_to_ist(int(release_ts)) + dt.timedelta(seconds=float(L) + float(D))
    return ref.index_of(t, check=False)


def trace_block(freq, fw, step: int, source: str, extractor: str, length_min: int) -> dict:
    x, w = decimate(freq, fw, step)
    return {"extractor": extractor, "length_min": length_min, "n_frames": int(np.asarray(freq).size),
            "audio_s": [EXCERPT_START_S, EXCERPT_START_S + 60 * length_min], "frame0_recording_s": FRAME0_S,
            "step_s": step, "base_hz": 50.0, "scale": 1e4, "freq": enc_hz(x),
            "weight": [int(round(100 * float(v))) for v in np.clip(w, 0, 1)], "source": source}


def window_for(ref, start_index: int, n_frames: int, step: int, ctx: int) -> dict:
    p = int(start_index) + FRAME0_S
    a = p - ctx
    w = ref_window(ref, a, p + n_frames + ctx, step)
    w["trace_offset_s"] = p - w["start_index"]           # seconds from window start to trace frame 0
    w["lock_position_index"] = p
    return w


def add_errors(cands: list, tstar: int) -> list:
    for c in cands:
        c["e_s"] = int(c["start_index"] - tstar)
    return cands


def calibration_clips(ref, cal: dict, lat: dict, tau: dict, splits: dict, log) -> list:
    clips = []
    L = lat["L_s"]
    for s in cal["streams"]:
        vid = s["video_id"]
        meta = base_meta(splits.get(vid, {"video_id": vid}))
        meta.update(split="calibration", split_note="Calibration split: used only to estimate the latency L "
                    "(PROTOCOL section 6). Its errors are not an independent test.")
        if s.get("harvest_status") != "done":
            meta.update(harvest={"status": s.get("harvest_status"), "reason": s.get("harvest_reason")},
                        search=None, verdict="not harvested", verdict_reason=s.get("harvest_reason"))
            clips.append(meta)
            continue
        tp = REPO / "traces" / "calibration" / f"{vid}.npz"
        with np.load(tp) as z:
            freq = z["v2__x60__freq"].astype(np.float64)
            fw = z["v2__x60__fw"].astype(np.float64)
            tmeta = json.loads(str(z["meta_json"]))
        note_source(tp)
        lk = s["locks"]["v2"]
        sr = run_search(ref, freq, fw, meta["release_timestamp"], "1d")
        raw = sr.pop("_conf", None)
        top = sr["candidates"][0] if sr["candidates"] else None
        check = {"against": "results/calibration.json (locks.v2)",
                 "lock_index_equal": top is not None and top["index"] == lk["lock_index"],
                 "confidence_equal_5dp": raw is not None and lk.get("confidence") is not None
                 and round(raw, 5) == round(lk["confidence"], 5)}
        if not (check["lock_index_equal"] and check["confidence_equal_5dp"]):
            log(f"WARNING calibration {vid}: recomputed search differs from results/calibration.json")
        tstar = truth_index(ref, meta["release_timestamp"], L, meta["D_s"] or 0.0)
        add_errors(sr["candidates"], tstar)
        hum60 = s["hum"].get("v2__x60", {})
        hum_flag = bool(lk.get("trace_hum_flag"))
        t = tau_of(tau, "v2", 60, "1d")
        verdict, why = verdict_of(hum_flag, sr["confidence"], t)
        e = None if top is None else top["e_s"]
        man = tmeta.get("manifest_row", {})
        meta.update(
            harvest={"status": "done", "seconds": s.get("seconds")},
            hum={"m1_present": fbool(s.get("hum_present_v1_full")),
                 "m1_max_ratio": fnum(man.get("hum_max_median_ratio_v1")),
                 "m1_ratios": hum_labels(s["hum"].get("v1__full", {}).get("median_peak_to_floor")),
                 "trace_flag": hum_flag, "trace_ratios": hum_labels(hum60.get("median_peak_to_floor")),
                 "rule": "hum flag: any harmonic's median peak-to-floor ratio is 3 or more"},
            trace=trace_block(freq, fw, CLIP_STEP_S, rel(tp) + " (v2__x60)", "v2", 60),
            search={**sr, "scope_label": SCOPE_LABEL["1d"], "length_min": 60, "extractor": "v2", "tau": t,
                    "conf_ge_tau": sr["confidence"] is not None and t is not None and sr["confidence"] >= t,
                    "verdict": verdict, "verdict_reason": why, "check": check,
                    "source": "recomputed by scripts/export_site_data.py with gunj.match_v2.wncc_search; "
                              "checked against results/calibration.json"},
            searches=[{"extractor": ext, "length_min": 60, "scope": "1d", "r1": v.get("r1"), "r2": v.get("r2"),
                       "confidence": v.get("confidence"), "tau": tau_of(tau, ext, 60, "1d"),
                       "lock_start_ist": v.get("lock_start_ist"), "o_s": v.get("o_s"),
                       "e_s": None if v.get("o_s") is None else v["o_s"] - L,
                       "trace_hum_flag": v.get("trace_hum_flag"), "source": "results/calibration.json"}
                      for ext, v in s["locks"].items() if v],
            truth={"T_star_ist": ist(ref, tstar), "T_star_index": tstar, "L_s": L, "D_s": meta["D_s"],
                   "e_s": e, "hit_30s": e is not None and abs(e) <= HIT_S,
                   "rule": "T* = release time in IST + L + D (PROTOCOL section 6); e = top candidate start - T*",
                   "in_sample_note": "L was estimated from the Calibration split, so e here is not an independent test."},
            verdict=verdict, verdict_reason=why,
            ref_window=None if top is None else window_for(ref, top["start_index"], freq.size, CLIP_STEP_S, CLIP_CONTEXT_S),
        )
        clips.append(meta)
    return clips


def dev_clips(ref, tun: dict, lat: dict, tau: dict, splits: dict, log) -> list:
    clips = []
    L = lat["L_s"]
    chosen = next(c for c in tun["candidates"] if c["id"] == tun["chosen"])
    cells = {(c["video_id"], c["length_min"]): c for c in chosen["cells"]}
    v1cells = {(c["video_id"], c["length_min"]): c for c in tun["baselines"]["v1"]["cells"]}
    diag = {d["video_id"]: d for d in tun.get("full_scope_60_diagnostic", [])}
    cache = REPO / "media" / "keep" / "tune_cache"
    for d in tun["dev_streams"]:
        vid = d["video_id"]
        meta = base_meta(splits.get(vid, {"video_id": vid}))
        meta.update(split="dev", split_note="Dev split: used to tune extractor v2 (results/v2_tuning.md). "
                    "Its results are in-sample and never pooled with Test.")
        tp = REPO / "traces" / "dev" / f"{vid}.npz"
        with np.load(tp) as z:
            tmeta = json.loads(str(z["meta_json"]))
            v1x = z["v1__x60__freq"].astype(np.float64) if "v1__x60__freq" in z.files else None
        note_source(tp)
        man = tmeta.get("manifest_row", {})
        v1full = (tmeta.get("traces", {}).get("v1__full", {}) or {}).get("hum", {})
        cf = cache / f"{vid}_x60_b{V2_FROZEN.band_half:.2f}.npz"
        tr_src, ext_used = None, "v2"
        if cf.exists():
            with np.load(cf) as z:
                used = tuple(int(k) for k in z["used"])
                tr = combine(used, z["hf"], z["hs"], V2_FROZEN, sr=1000, nfft=int(z["nfft"]))
            freq = tr.freq.astype(np.float64)
            fw = np.asarray(tr.extra.get("frame_weight"), dtype=np.float64)
            hum_flag, hum_r = bool(tr.hum_present), hum_labels(tr.harmonic_median_snr)
            tr_src = ("v2 recombined from the cached Dev harmonic tracks with the frozen V2_FROZEN parameters "
                      "(the same path scripts/tune_v2.py used; no audio read)")
        elif v1x is not None:
            log(f"NOTE dev {vid}: no v2 harmonic cache, showing the v1 trace")
            freq, fw, ext_used = v1x, np.ones_like(v1x), "v1"
            h = (tmeta.get("traces", {}).get("v1__x60", {}) or {}).get("hum", {})
            hum_flag, hum_r = bool(h.get("present")), hum_labels(h.get("median_peak_to_floor"))
            tr_src = rel(tp) + " (v1__x60; the v2 trace for Dev was not saved)"
        else:
            continue
        sr = run_search(ref, freq, fw, d["release_timestamp"], "1d")
        top = sr["candidates"][0] if sr["candidates"] else None
        c60 = cells.get((vid, 60), {})
        e_ref = d["D"]
        E = truth_index(ref, d["release_timestamp"], 0, e_ref)
        check = {"against": "results/v2_tuning.json (chosen candidate, 60 min cell)",
                 "confidence_equal_5dp": ext_used == "v2" and sr["_conf"] is not None
                 and c60.get("confidence") is not None and round(sr["_conf"], 5) == round(c60["confidence"], 5),
                 "top_offset_equal": top is not None and c60.get("top_offset_s") is not None
                 and top["start_index"] - E == c60["top_offset_s"]}
        if ext_used == "v2" and not (check["confidence_equal_5dp"] and check["top_offset_equal"]):
            log(f"WARNING dev {vid}: recomputed search differs from results/v2_tuning.json")
        sr.pop("_conf", None)
        tstar = truth_index(ref, d["release_timestamp"], L, d["D"])
        add_errors(sr["candidates"], tstar)
        t = tau_of(tau, ext_used, 60, "1d")
        verdict, why = verdict_of(hum_flag, sr["confidence"], t)
        e = None if top is None else top["e_s"]
        searches = []
        for m in (10, 20, 40, 60):
            for ext, src in (("v2", cells), ("v1", v1cells)):
                c = src.get((vid, m))
                if not c:
                    continue
                searches.append({"extractor": ext, "length_min": m, "scope": "1d", "r1": c.get("top_r"),
                                 "confidence": c.get("confidence"), "tau": tau_of(tau, ext, m, "1d"),
                                 "e_s": None if c.get("top_offset_s") is None else c["top_offset_s"] - L,
                                 "trace_hum_flag": c.get("hum_present"), "source": "results/v2_tuning.json"})
        dg = diag.get(vid)
        if dg:
            for ext in ("v2", "v1"):
                x = dg.get(ext)
                if x:
                    searches.append({"extractor": ext, "length_min": 60, "scope": "full", "r1": x.get("top_r"),
                                     "confidence": x.get("confidence"), "tau": tau_of(tau, ext, 60, "full"),
                                     "lock_start_ist": x.get("top_start_ist"),
                                     "e_s": None if x.get("top_offset_s") is None else x["top_offset_s"] - L,
                                     "source": "results/v2_tuning.json (full_scope_60_diagnostic; top candidate only)"})
        meta.update(
            harvest={"status": man.get("status"), "seconds": fnum(man.get("seconds"))},
            hum={"m1_present": fbool(man.get("hum_present_v1")), "m1_max_ratio": fnum(man.get("hum_max_median_ratio_v1")),
                 "m1_ratios": hum_labels(v1full.get("median_peak_to_floor")), "trace_flag": hum_flag,
                 "trace_ratios": hum_r, "rule": "hum flag: any harmonic's median peak-to-floor ratio is 3 or more"},
            trace=trace_block(freq, fw, CLIP_STEP_S, tr_src, ext_used, 60),
            search={**sr, "scope_label": SCOPE_LABEL["1d"], "length_min": 60, "extractor": ext_used, "tau": t,
                    "conf_ge_tau": sr["confidence"] is not None and t is not None and sr["confidence"] >= t,
                    "verdict": verdict, "verdict_reason": why, "check": check,
                    "source": "recomputed by scripts/export_site_data.py with gunj.match_v2.wncc_search; "
                              "checked against results/v2_tuning.json"},
            searches=searches,
            truth={"T_star_ist": ist(ref, tstar), "T_star_index": tstar, "L_s": L, "D_s": d["D"], "e_s": e,
                   "hit_30s": e is not None and abs(e) <= HIT_S,
                   "rule": "T* = release time in IST + L + D (PROTOCOL section 6); e = top candidate start - T*",
                   "in_sample_note": "Dev streams tuned v2, so these results are in-sample."},
            verdict=verdict, verdict_reason=why,
            ref_window=None if top is None else window_for(ref, top["start_index"], freq.size, CLIP_STEP_S, CLIP_CONTEXT_S),
        )
        clips.append(meta)
    return clips


def test_clips(ref, tdir: Path, splits: dict, log) -> tuple:
    p = tdir / "test_results.csv"
    if not p.exists():
        return [], None
    rows = read_csv(p)
    mode = rows[0].get("mode") if rows else None
    key_split = "test" if mode == "real" else "practice"
    by_vid: dict = {}
    for r in rows:
        by_vid.setdefault(r["video_id"], []).append(r)
    clips = []
    for vid, rs in by_vid.items():
        r0 = rs[0]
        meta = base_meta(splits.get(vid, {"video_id": vid}))
        src_split = r0.get("split") or "test"
        meta.update(split=key_split, source_split=src_split,
                    split_note=("Test split: searched blind after tau and L were committed (PROTOCOL section 14). "
                                + ("Gate frozen: these are the final v1 numbers."
                                   if (Path(__file__).resolve().parents[1] / "results" / "gate_frozen.json").exists()
                                   else "Numbers are interim until the gate is frozen.") if key_split == "test" else
                                "PRACTICE run of scripts/evaluate.py on Calibration traces. Not a Test result."))
        cells = [r for r in rs if r.get("extractor") and r.get("scope")]
        meta["harvest"] = {"status": r0.get("harvest_status"), "reason": r0.get("harvest_reason") or None,
                           "by_decision_time": fbool(r0.get("harvested_by_decision_time"))}
        meta["hum"] = {"m1_present": fbool(r0.get("hum_present_v1_full")),
                       "m1_max_ratio": fnum(r0.get("hum_max_median_ratio_v1_full")), "m1_ratios": None,
                       "rule": "hum flag: any harmonic's median peak-to-floor ratio is 3 or more"}
        meta["searches"] = [{
            "extractor": c["extractor"], "length_min": fint(c["excerpt_min"]), "scope": c["scope"],
            "r1": fnum(c["r1"], 6), "r2": fnum(c["r2"], 6), "confidence": fnum(c["confidence"], 6),
            "tau": fnum(c["tau"], 6), "verdict": c["verdict"], "verdict_reason": c["verdict_reason"],
            "lock_start_ist": c["top1_start_ist"] or None, "e_s": fint(c["error_s"]),
            "e_D0_s": fint(c["error_D0_s"]), "outcome": c["outcome"], "trace_hum_flag": fbool(c["trace_hum_present"]),
            "search_error": c.get("search_error") or None, "source": rel(p)} for c in cells]
        v2 = [c for c in cells if c["extractor"] == "v2" and c["top1_start_ist"]]
        if not v2:
            meta.update(search=None, verdict="no verdict" if cells else (r0.get("harvest_status") or "not searched"),
                        verdict_reason=(r0.get("harvest_reason") or "no v2 search result") if not cells
                        else "no v2 search result")
            clips.append(meta)
            continue
        mlen = max(fint(c["excerpt_min"]) for c in v2)
        prim = next((c for c in v2 if fint(c["excerpt_min"]) == mlen and c["scope"] == "full"), v2[0])
        mlen = fint(prim["excerpt_min"])
        starts = [s for s in (prim["top5_start_ist"] or "").split(";") if s]
        rs5 = [fnum(x) for x in (prim["top5_r"] or "").split(";") if x]
        es5 = [fint(x) for x in (prim["top5_error_s"] or "").split(";") if x]
        cands = []
        for i, st in enumerate(starts):
            si = index_of_ist(ref, st)
            cands.append({"index": si + FRAME0_S, "start_index": si, "start_ist": st,
                          "r": rs5[i] if i < len(rs5) else None, "e_s": es5[i] if i < len(es5) else None})
        tstar = index_of_ist(ref, prim["true_start_ist"]) if prim.get("true_start_ist") else None
        tp = REPO / "traces" / src_split / f"{vid}.npz"
        trace = ref_w = None
        if tp.exists():
            with np.load(tp) as z:
                k = f"v2__x{mlen}"
                if f"{k}__freq" in z.files:
                    freq = z[f"{k}__freq"].astype(np.float64)
                    fw = z[f"{k}__fw"].astype(np.float64) if f"{k}__fw" in z.files else np.ones_like(freq)
                    trace = trace_block(freq, fw, CLIP_STEP_S, rel(tp) + f" ({k})", "v2", mlen)
                    if cands:
                        ref_w = window_for(ref, cands[0]["start_index"], freq.size, CLIP_STEP_S, CLIP_CONTEXT_S)
            note_source(tp)
        e = fint(prim["error_s"])
        meta.update(
            trace=trace, ref_window=ref_w,
            hum={**meta["hum"], "trace_flag": fbool(prim["trace_hum_present"]),
                 "trace_ratios": json.loads(prim["trace_hum_median_peak_to_floor"] or "{}")},
            search={"scope": prim["scope"], "scope_label": SCOPE_LABEL.get(prim["scope"], prim["scope"]),
                    "length_min": mlen, "extractor": "v2", "positions_searched": fint(prim["positions_searched"]),
                    "candidates": cands, "runner_up": None if not prim["runner_up_start_ist"] else
                    {"start_ist": prim["runner_up_start_ist"], "r": fnum(prim["r2"], 6)},
                    "confidence": fnum(prim["confidence"], 6), "tau": fnum(prim["tau"], 6),
                    "conf_ge_tau": fbool(prim["confidence_ge_tau"]), "verdict": prim["verdict"],
                    "verdict_reason": prim["verdict_reason"], "outcome": prim["outcome"], "source": rel(p)},
            truth={"T_star_ist": prim.get("true_start_ist") or None, "T_star_index": tstar,
                   "L_s": fint(prim["L_s"]), "D_s": fnum(prim["label_drift_s"]), "e_s": e,
                   "e_D0_s": fint(prim["error_D0_s"]), "hit_30s": fbool(prim["hit_30s"]),
                   "rule": "T* = release time in IST + L + D (PROTOCOL section 6); e = top candidate start - T*"},
            verdict=prim["verdict"], verdict_reason=prim["verdict_reason"],
        )
        clips.append(meta)
    return clips, mode


def summary_row(c: dict) -> dict:
    s = c.get("search") or {}
    t = c.get("truth") or {}
    h = c.get("hum") or {}
    return {"video_id": c["video_id"], "split": c["split"], "channel": c.get("channel"),
            "venue_type": c.get("venue_type"), "release_time_ist": c.get("release_time_ist"),
            "D_s": c.get("D_s"), "label_stable": c.get("label_stable"), "licence_cc": c.get("licence_cc"),
            "harvest": (c.get("harvest") or {}).get("status"), "hum_m1": h.get("m1_present"),
            "hum_trace": h.get("trace_flag"), "verdict": c.get("verdict"), "verdict_reason": c.get("verdict_reason"),
            "scope": s.get("scope"), "length_min": s.get("length_min"), "confidence": s.get("confidence"),
            "tau": s.get("tau"), "e_s": t.get("e_s"), "hit_30s": t.get("hit_30s"),
            "lock_start_ist": (s.get("candidates") or [{}])[0].get("start_ist") if s.get("candidates") else None,
            "page": c.get("search") is not None or c.get("harvest") is not None}


def pick_hero(clips: list, want: str | None):
    if want:
        sp, _, vid = want.partition(":")
        for c in clips:
            if c["split"] == sp and c["video_id"] == vid:
                return c, f"chosen with --hero {want}"
        raise SystemExit(f"--hero {want}: no such clip")
    for sp in ("calibration", "dev"):
        ok = [c for c in clips if c["split"] == sp and c.get("verdict") == "match" and (c.get("truth") or {}).get("hit_30s")]
        if ok:
            best = max(ok, key=lambda c: c["search"]["confidence"])
            return best, (f"highest confidence among the {len(ok)} {SPLIT_LABEL[sp]} streams whose 60-min v2 excerpt "
                          f"has a match verdict (hum flag set, confidence at or above tau) within 30 s of T* in the "
                          f"±1-day scope")
    return None, "no Calibration or Dev stream has a match verdict"


def build_hero(ref, c: dict, why: str, tau: dict, lat: dict, full: bool, log) -> dict:
    split, vid = c["split"], c["video_id"]
    m = int(c["search"]["length_min"])
    if split == "dev":
        cf = REPO / "media" / "keep" / "tune_cache" / f"{vid}_x60_b{V2_FROZEN.band_half:.2f}.npz"
        with np.load(cf) as z:
            tr = combine(tuple(int(k) for k in z["used"]), z["hf"], z["hs"], V2_FROZEN, sr=1000, nfft=int(z["nfft"]))
        freq, fw = tr.freq.astype(np.float64), np.asarray(tr.extra["frame_weight"], dtype=np.float64)
        src = c["trace"]["source"]
    else:                                   # calibration, test, practice: the harvested trace file
        tp = REPO / "traces" / c.get("source_split", split) / f"{vid}.npz"
        k = f"v2__x{m}"
        with np.load(tp) as z:
            freq = z[f"{k}__freq"].astype(np.float64)
            fw = z[f"{k}__fw"].astype(np.float64) if f"{k}__fw" in z.files else np.ones_like(freq)
        src = rel(tp) + f" ({k})"
    tstar = c["truth"]["T_star_index"]
    base = c["search"]["scope"]
    searches = {base: c["search"]}
    full_by_exporter = False
    if full and base != "full":
        log(f"hero {split}/{vid}: full-scope search (single thread) ...")
        sf = run_search(ref, freq, fw, c["release_timestamp"], "full")
        sf.pop("_conf", None)
        if tstar is not None:
            add_errors(sf["candidates"], tstar)
        t = tau_of(tau, "v2", m, "full")
        v, why_v = verdict_of(c["hum"]["trace_flag"], sf["confidence"], t)
        sf.update(scope_label=SCOPE_LABEL["full"], length_min=m, extractor="v2", tau=t,
                  conf_ge_tau=sf["confidence"] is not None and t is not None and sf["confidence"] >= t,
                  verdict=v, verdict_reason=why_v,
                  source="computed by scripts/export_site_data.py: gunj.match_v2.wncc_search on " + src +
                         " over the whole reference. This display search is not part of any pre-registered metric.")
        searches["full"] = sf
        full_by_exporter = True
        log(f"  full scope: top {sf['candidates'][0]['start_ist'] if sf['candidates'] else None}, "
            f"conf {sf['confidence']}, {sf['seconds']} s")
    show = base
    one = searches[base]
    if full_by_exporter and searches["full"]["candidates"] and one["candidates"]             and searches["full"]["candidates"][0]["index"] == one["candidates"][0]["index"]:
        show = "full"
    top = searches[show]["candidates"][0]
    rel_idx = ref.index_of(gref.unix_to_ist(int(c["release_timestamp"])), check=False)
    return {
        "video_id": vid, "split": split, "split_label": SPLIT_LABEL[split], "why": why,
        "split_note": c["split_note"], "channel": c.get("channel"), "venue_type": c.get("venue_type"),
        "licence": c.get("licence"), "licence_cc": c.get("licence_cc"),
        "release_time_ist": c["release_time_ist"], "release_index": rel_idx,
        "public_start_ist": ist(ref, rel_idx), "D_s": c["D_s"], "L_s": c["truth"].get("L_s", lat["L_s"]),
        "hum": c["hum"], "length_min": m, "base_scope": base, "shown_scope": show,
        "full_by_exporter": full_by_exporter and show == "full", "searches": searches, "truth": c["truth"],
        "trace": trace_block(freq, fw, HERO_STEP_S, src, "v2", m),
        "ref_window": window_for(ref, top["start_index"], freq.size, HERO_STEP_S, HERO_CONTEXT_S),
        "scope_window": {sc: list(scope_positions(ref, c["release_timestamp"], sc)) for sc in ("1d", "90d", "full")},
    }


def cal_summary(cal: dict, lat: dict, tau: dict) -> dict:
    L = lat["L_s"]
    out = {"n_selected": cal["n_calibration_streams"], "n_harvested": cal["n_harvested"],
           "m1_hum": sum(1 for s in cal["streams"] if s.get("hum_present_v1_full")),
           "scope": "±1 day, 60 min", "by_extractor": {}}
    for ext in ("v2", "v1", "naive"):
        locks = [s["locks"][ext] for s in cal["streams"] if (s.get("locks") or {}).get(ext)]
        t = tau_of(tau, ext, 60, "1d")
        out["by_extractor"][ext] = {
            "n": len(locks), "tau": t,
            "within_600s": sum(1 for x in locks if x.get("o_s") is not None and abs(x["o_s"]) <= 600),
            "within_30s_of_Tstar": sum(1 for x in locks if x.get("o_s") is not None and abs(x["o_s"] - L) <= HIT_S),
            "conf_ge_tau": sum(1 for x in locks if x.get("confidence") is not None and t is not None and x["confidence"] >= t),
            "match_and_hit": sum(1 for x in locks if x.get("confidence") is not None and t is not None
                                 and x["confidence"] >= t and x.get("trace_hum_flag")
                                 and x.get("o_s") is not None and abs(x["o_s"] - L) <= HIT_S),
            "match_and_wrong": sum(1 for x in locks if x.get("confidence") is not None and t is not None
                                   and x["confidence"] >= t and x.get("trace_hum_flag")
                                   and x.get("o_s") is not None and abs(x["o_s"] - L) > HIT_S)}
    out["note"] = "Calibration streams set L, so the within-30-s counts are in-sample."
    out["sources"] = ["results/calibration.json", "results/latency.json", "results/threshold.json"]
    return out


def dev_summary(tun: dict) -> dict:
    ch = next(c for c in tun["candidates"] if c["id"] == tun["chosen"])
    return {"chosen": tun["chosen"], "J_v2": ch["J"], "J_v1": tun["baselines"]["v1"]["J"],
            "J_naive": tun["baselines"]["naive"]["J"],
            "cells_top_in_true_v2": ch["top_in_true"], "cells_top_in_true_v1": tun["baselines"]["v1"]["top_in_true"],
            "cells_top_in_true_naive": tun["baselines"]["naive"]["top_in_true"], "n_cells": ch["n_cells"],
            "true_region_s": tun["objective"]["true_region_s"],
            "objective": tun["objective"]["J"], "sources": ["results/v2_tuning.json"]}


# ----------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(REPO / "site" / "data"), help="output folder (default site/data)")
    ap.add_argument("--test-dir", default=str(REPO / "results"),
                    help="folder with test_results.csv, evaluation.json, gate.md (default results)")
    ap.add_argument("--derived-dir", default=None, help="reference folder (default $GUNJ_DERIVED or ../data/derived)")
    ap.add_argument("--hero", default=None, help="force the hero clip, as split:video_id")
    ap.add_argument("--no-full-search", action="store_true", help="skip the hero's full-scope search")
    ap.add_argument("--repo-url", default=None, help="public repository URL for links (optional)")
    args = ap.parse_args(argv)
    t_start = time.time()
    out = Path(args.out)
    tdir = Path(args.test_dir)

    def log(msg):
        print(f"[{dt.datetime.now(IST).strftime('%H:%M:%S')}] {msg}", flush=True)

    derived = Path(args.derived_dir) if args.derived_dir else gref.default_derived_dir()
    ref = gref.load(derived, mmap=True)                    # np.load(..., mmap_mode="r")
    note_source(derived / gref.META_NAME)
    log(f"reference: {len(ref):,} s from {ref.t0} (memory-mapped)")

    splits = splits_rows()
    cal = read_json(REPO / "results" / "calibration.json")
    tun = read_json(REPO / "results" / "v2_tuning.json")
    tau = build_tau()
    lat = build_latency(cal)
    read_json(REPO / "gunj" / "config.json")

    log("ribbon ...")
    ribbon = build_ribbon(ref)
    log(f"  {ribbon['n_bins']} hourly bins in {ribbon['seconds']} s")

    log("calibration clips ...")
    cclips = calibration_clips(ref, cal, lat, tau, splits, log)
    log("dev clips ...")
    dclips = dev_clips(ref, tun, lat, tau, splits, log)
    log("test clips ...")
    tclips, tmode = test_clips(ref, tdir, splits, log)
    log(f"  {len(tclips)} test clips" + (f" (mode {tmode})" if tmode else " (results/test_results.csv not found)"))
    clips = dclips + cclips + tclips

    hero_clip, why = pick_hero(clips, args.hero)
    hero = None
    if hero_clip is not None:
        hero = build_hero(ref, hero_clip, why, tau, lat, not args.no_full_search, log)

    ss = read_json(REPO / "results" / "splits_summary.json")
    test_block = build_test_block(tdir)
    dtime = decision_time()
    overview = {
        "written_ist": now_ist(), "exporter": "scripts/export_site_data.py", "git_head": git_head(),
        "repo_url": args.repo_url,
        "decision_time": dtime,
        "reference": build_reference_block(ref, derived), "ribbon": ribbon,
        "drift": build_drift(), "uniqueness": build_uniqueness(), "latency": lat, "tau": tau,
        "calibration": cal_summary(cal, lat, tau), "dev": dev_summary(tun),
        "random_guess": ss.get("analytic_random_guess"),
        "splits": {k: {"n": v.get("n")} for k, v in ss.get("splits", {}).items()},
        "hero": hero, "test": test_block,
        "clip_counts": {sp: sum(1 for c in clips if c["split"] == sp) for sp in ("dev", "calibration", "test", "practice")},
    }

    sizes = {}
    clip_dir = out / "clips"
    keep = set()
    for c in clips:
        p = clip_dir / c["split"] / f"{c['video_id']}.json"
        sizes[p.relative_to(out).as_posix()] = write_json_atomic(p, c)
        keep.add(p.resolve())
    if clip_dir.exists():                                  # drop pages of clips no longer exported
        for p in clip_dir.rglob("*.json"):
            if p.resolve() not in keep:
                p.unlink()
    idx = {"written_ist": overview["written_ist"], "clips": [summary_row(c) for c in clips]}
    sizes["clips.json"] = write_json_atomic(out / "clips.json", idx)
    overview["sources"] = dict(sorted(SOURCES.items()))
    sizes["overview.json"] = write_json_atomic(out / "overview.json", overview)
    total = sum(sizes.values())
    log(f"wrote {len(sizes)} files, {total / 1e6:.2f} MB in {rel(out) if out.resolve().is_relative_to(REPO) else out.name}; overview {sizes['overview.json'] / 1e3:.0f} kB; "
        f"{time.time() - t_start:.0f} s")
    mem = peak_memory_mb()
    if mem:
        log(f"memory: peak private {mem['peak_private_mb']} MB, peak working set {mem['peak_working_set_mb']} MB "
            "(the working set includes shared, memory-mapped reference pages)")
    if total > SITE_BUDGET_BYTES:
        log(f"WARNING: site data is {total / 1e6:.2f} MB, over the {SITE_BUDGET_BYTES / 1e6:.1f} MB budget")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
