#!/usr/bin/env python
"""Figures and secondary tables that PROTOCOL.md section 11 promises, plus one
EXPLORATORY breakdown.

Every number this script writes or draws is counted or computed at run time
from a file in the repo, and that file is named next to the number. Nothing
is typed in by hand. The script reads results that scripts/evaluate.py wrote;
it runs no search, changes no frozen file and uses no audio.

What it makes
1. M2 signed-error distributions (section 11, M2: "The signed error
   distribution is plotted for every cell"): one 4 x 3 grid per extractor,
   results/figures/m2_signed_error_<extractor>.png.
2. M3 reliability plot (section 11, M3): Test top-1 hit rate by confidence
   bin [0, 0.025), [0.025, 0.05), [0.05, 0.1), [0.1, 0.2), [0.2, 0.4),
   [0.4, inf), with counts, and the report-half negatives' confidences on the
   same axis (real and SYNTHETIC apart, tau marked):
   results/figures/m3_reliability_v2_60min_full.png (primary cell) and
   results/figures/m3_reliability_v2_all_cells.png.
3. Confident-verdict breakdown from the pre-registered columns hit_30s,
   hit_120s and hit_600s (section 9): results/error_breakdown.json,
   results/error_breakdown.md and
   results/figures/confident_verdict_breakdown_v2.png.
4. M4 at the measured noise level (section 11, M4): the equivalent trace
   noise of the Test streams that lock, computed as in scripts/probe_noise.py
   (sigma = s * sqrt(1 / r**2 - 1)), and the uniqueness table
   (results/uniqueness_table.csv, SYNTHETIC noise) read at that level by
   linear interpolation between the pre-set levels: results/m4_measured.json,
   results/m4_measured.md and results/figures/m4_measured_noise.png. This is
   the only step that needs the built reference series and the traces. If the
   reference is not found, the step is skipped and an existing
   results/m4_measured.json is reused for the figure and the text.
5. EXPLORATORY (post hoc, not pre-registered): what goes with confident
   verdicts that are 30 to 600 s from T*: results/exploratory_offset_verdicts.json,
   results/exploratory_offset_verdicts.md and
   results/figures/exploratory_offset_verdicts_v2.png. Counts and medians
   only; no tests, no new thresholds.
Also results/figures/README.md (a caption for every figure) and
results/figures/figures.json (the numbers each figure draws).

Readings chosen where PROTOCOL.md leaves room (each is repeated in the
output it affects)
- "Test streams that lock" (M4): the streams the frozen gate counts as locks
  (results/gate_frozen.json: extractor v2, top candidate in the full or
  +-90-day scope within 30 s of T* for at least one excerpt length, with
  confidence >= tau of that cell). Confident hits and all top-1 hits are
  given beside it.
- "Equivalent trace noise ... as in scripts/probe_noise.py" (M4): reading A
  uses the r the frozen evaluation recorded for the locking excerpt (v2's
  weighted correlation) and s = the standard deviation of the gap-filled
  reference over the matched window. Reading B uses the plain (unweighted)
  correlation of the same trace with the same window, which is what the
  unweighted matcher of the M4 simulation would see. Both are reported.
- One value per stream: the median over that stream's locking excerpts.
- The clip length "needed" (M4) is the shortest tested length whose
  interpolated rate is at least 90%, the convention of results/uniqueness.md.
- The M2 denominators keep excerpts whose search could not run (a constant
  trace); they have no top-1 candidate and form their own class.

The script stops (exit 2) if results/test_results.csv disagrees with the m2
block of results/evaluation.json, or if results/false_matches.json and
results/evaluation.json come from different scoring runs. Run it again once
scripts/evaluate.py has finished.

Files are written to <name>.tmp and then renamed. With the same inputs the
script writes the same text. PNGs are 1800 px wide (twice the README width)
on a white background. One process, and the reference is opened with
np.load(..., mmap_mode="r") through gunj.reference.

Usage (from the repo root):
  .venv\\Scripts\\python.exe scripts\\make_figures.py
  .venv\\Scripts\\python.exe scripts\\make_figures.py --ref ..\\data\\derived
  .venv\\Scripts\\python.exe scripts\\make_figures.py --skip-m4-measure
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")   # before numpy is imported

import argparse
import csv
import hashlib
import json
import math
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np                                   # noqa: E402

EXTRACTORS = ("v2", "v1", "naive")                   # scripts/eval_common.py
PRIMARY = "v2"
LENGTHS = (10, 20, 40, 60)                           # PROTOCOL section 8
SCOPES = ("full", "90d", "1d")
SCOPE_LABEL = {"full": "full span", "90d": "±90 days", "1d": "±1 day"}
SCOPE_SHORT = {"full": "full", "90d": "±90 d", "1d": "±1 d"}
SCOPE_LIMIT_S = {"90d": 90 * 86400, "1d": 86400}
GATE_SCOPES = ("full", "90d")                        # PROTOCOL section 15
HIT_S = 30                                           # PROTOCOL section 9
COARSE_S = (120, 600)
Z95 = 1.959964                                       # PROTOCOL section 13
CONF_BINS = (0.0, 0.025, 0.05, 0.1, 0.2, 0.4, math.inf)   # PROTOCOL section 11, M3
UNIQUE_TARGET = 0.9                                  # PROTOCOL section 11, M4: "90% unique locks"
FRAME0_S = 128                                       # PROTOCOL section 8: frame 0 is centred 128 s in

CLASSES = ("within_30", "from_30_to_120", "from_120_to_600", "beyond_600", "no_candidate")
CLASS_LABEL = {"within_30": "within 30 s (hit)", "from_30_to_120": "30 to 120 s",
               "from_120_to_600": "120 to 600 s", "beyond_600": "beyond 600 s",
               "no_candidate": "no top-1 candidate"}

# ------------------------------------------------------------------- style
INK = "#2b3036"            # text
INK_2 = "#5a636d"          # secondary text
RULE = "#aeb6bf"           # axis lines
GRID = "#e6e9ed"           # hairlines
AMBER = "#d98e04"          # Gunj's primary results
AMBER_2 = "#a36a00"        # darker steps of the same hue, for ordered classes
AMBER_3 = "#6e4700"
GREEN = "#1a7f37"          # confirmed hits only
GREEN_TINT = "#e7f3ea"     # the +-30 s hit band
GREY = "#6b7785"           # baselines and reference data
GREY_LIGHT = "#c9cfd6"
SHADE = "#f1f3f5"
DPI = 200
FIG_W = 9.0                # inches; 1800 px at DPI 200
CLASS_COLOUR = {"within_30": GREEN, "from_30_to_120": AMBER, "from_120_to_600": AMBER_2,
                "beyond_600": AMBER_3, "no_candidate": GREY_LIGHT}
CLASS_TEXT = {"within_30": "white", "from_30_to_120": INK, "from_120_to_600": "white",
              "beyond_600": "white", "no_candidate": INK}

STYLE = {
    "font.family": "DejaVu Sans", "font.size": 9, "text.color": INK, "axes.labelcolor": INK,
    "axes.edgecolor": RULE, "axes.linewidth": 0.6, "axes.titlesize": 8.5, "axes.labelsize": 8.5,
    "xtick.color": INK_2, "ytick.color": INK_2, "xtick.labelsize": 8, "ytick.labelsize": 8,
    "xtick.major.width": 0.6, "ytick.major.width": 0.6, "xtick.major.size": 3, "ytick.major.size": 3,
    "xtick.minor.width": 0.5, "xtick.minor.size": 2,
    "axes.spines.top": False, "axes.spines.right": False, "axes.facecolor": "white",
    "figure.facecolor": "white", "savefig.facecolor": "white", "legend.frameon": False,
    "legend.fontsize": 8, "axes.unicode_minus": True, "hatch.linewidth": 0.6,
}


class FigureError(Exception):
    """Stops the run: an input is missing or two inputs disagree."""


# ------------------------------------------------------------------ basics

def wilson(k: int, n: int, z: float = Z95):
    """Wilson 95% interval, the formula of PROTOCOL section 13; (None, None) if n == 0."""
    if n <= 0:
        return None, None
    p = k / n
    den = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def rate(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    return {"k": int(k), "n": int(n), "rate": None if n == 0 else k / n,
            "wilson95": None if lo is None else [lo, hi]}


def fmt_rate(d: dict) -> str:
    if not d or not d["n"]:
        return "n/a (N = 0)"
    lo, hi = d["wilson95"]
    return f"{d['k']}/{d['n']} ({100 * d['rate']:.1f}%, 95% CI {100 * lo:.1f} to {100 * hi:.1f}%)"


def _bool(s):
    return None if s in (None, "") else (s == "True")


def _float(s):
    return None if s in (None, "") else float(s)


def _int(s):
    return None if s in (None, "") else int(float(s))


def rel(path: Path, repo: Path = REPO) -> str:
    try:
        return Path(path).resolve().relative_to(Path(repo).resolve()).as_posix()
    except ValueError:
        return f"<outside the repo>/{Path(path).name}"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_DRIVE_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]")


def check_no_abs_paths(text: str, repo: Path = REPO) -> None:
    """Refuse to write a local absolute path (a drive letter, the repo folder or the home folder)."""
    if _DRIVE_RE.search(text):
        raise FigureError("refusing to write a local absolute path (drive letter) into an output file")
    low = text.lower()
    for base in (Path(repo).resolve(), Path.home()):
        s = str(base)
        for m in (s, s.replace("\\", "/"), s.replace("\\", "\\\\")):
            if len(m) > 3 and m.lower() in low:
                raise FigureError("refusing to write a local absolute path into an output file")


def _replace(tmp: Path, dst: Path, tries: int = 20) -> None:
    for i in range(tries):
        try:
            os.replace(tmp, dst)
            return
        except PermissionError:                      # Windows: a viewer has the file open
            if i == tries - 1:
                raise
            time.sleep(0.25)


def write_text(path: Path, text: str, repo: Path = REPO) -> None:
    check_no_abs_paths(text, repo)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    _replace(tmp, path)


def _json_default(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"cannot write {type(o).__name__} to JSON")


def write_json(path: Path, obj, repo: Path = REPO) -> None:
    write_text(path, json.dumps(obj, indent=1, ensure_ascii=False, default=_json_default,
                                allow_nan=False) + "\n", repo)


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_csv(path: Path) -> list:
    with open(path, encoding="utf-8", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def r6(v):
    return None if v is None else round(float(v), 6)


def quartiles(values) -> dict | None:
    """n, min, Q1, median, Q3, max by linear interpolation between order statistics (numpy's
    default). A value of None is undefined and ranks above every number; a quantile that
    falls on or beside it is None."""
    v = sorted(math.inf if x is None else float(x) for x in values)
    n = len(v)
    if n == 0:
        return None

    def q(p: float):
        pos = p * (n - 1)
        lo, hi = math.floor(pos), math.ceil(pos)
        if math.isinf(v[hi]):
            return None
        return v[lo] + (pos - lo) * (v[hi] - v[lo])

    return {"n": n, "n_undefined": sum(1 for x in v if math.isinf(x)), "min": q(0.0), "q1": q(0.25),
            "median": q(0.5), "q3": q(0.75), "max": q(1.0)}


def round_stats(q: dict | None, nd: int = 6) -> dict | None:
    if q is None:
        return None
    return {k: (v if k in ("n", "n_undefined") or v is None else round(float(v), nd)) for k, v in q.items()}


def median(values):
    v = [float(x) for x in values if x is not None]
    return float(np.median(v)) if v else None


_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}


def file_label(name: str) -> str:
    """'freq_III.csv' -> 'III'."""
    return name.replace("freq_", "").replace(".csv", "")


def file_order(name: str):
    return (_ROMAN.get(file_label(name), 99), name)


# ------------------------------------------------------------- Test results

def searched_rows(rows: list) -> list:
    """Rows of results/test_results.csv that hold one search (extractor x excerpt x scope)."""
    out = []
    for r in rows:
        if r.get("extractor") in EXTRACTORS and r.get("scope") in SCOPES and _int(r.get("excerpt_min")) in LENGTHS:
            out.append(r)
    return out


def cell_rows(rows: list, ext: str, m: int, scope: str) -> list:
    return [r for r in rows if r["extractor"] == ext and _int(r["excerpt_min"]) == m and r["scope"] == scope]


def error_class(row: dict) -> str:
    """Distance class of the top-1 candidate from T*, from the pre-registered columns."""
    if row.get("error_s", "") == "":
        return "no_candidate"
    if _bool(row.get("hit_30s")):
        return "within_30"
    if _bool(row.get("hit_120s")):
        return "from_30_to_120"
    if _bool(row.get("hit_600s")):
        return "from_120_to_600"
    return "beyond_600"


def column_mismatches(rows: list) -> int:
    """Rows whose hit_30s / hit_120s / hit_600s disagree with abs(error_s). Should be 0."""
    bad = 0
    for r in rows:
        e = _int(r.get("error_s"))
        if e is None:
            continue
        for col, lim in (("hit_30s", HIT_S), ("hit_120s", COARSE_S[0]), ("hit_600s", COARSE_S[1])):
            if _bool(r.get(col)) != (abs(e) <= lim):
                bad += 1
    return bad


def split_counts(rows: list) -> dict:
    c = {k: 0 for k in CLASSES}
    for r in rows:
        c[error_class(r)] += 1
    n = len(rows)
    w30 = c["within_30"]
    w120 = w30 + c["from_30_to_120"]
    w600 = w120 + c["from_120_to_600"]
    return {"n": n, **c, "within_30_share": rate(w30, n), "within_120_share": rate(w120, n),
            "within_600_share": rate(w600, n)}


def breakdown(rows: list) -> dict:
    """{ext: {length: {scope: {tau, confident: split, top1: split}}}} for every cell."""
    out = {}
    for ext in EXTRACTORS:
        for m in LENGTHS:
            for scope in SCOPES:
                cr = cell_rows(rows, ext, m, scope)
                taus = sorted({r["tau"] for r in cr if r.get("tau")})
                out.setdefault(ext, {}).setdefault(str(m), {})[scope] = {
                    "tau": float(taus[0]) if len(taus) == 1 else None,
                    "confident": split_counts([r for r in cr if r["verdict"] == "match"]),
                    "top1": split_counts(cr)}
    return out


def check_against_evaluation(bd: dict, ev: dict) -> int:
    """The counts above must equal the m2 block that scripts/evaluate.py wrote. Returns cells checked."""
    n = 0
    for ext in EXTRACTORS:
        for m in LENGTHS:
            for scope in SCOPES:
                mine = bd[ext][str(m)][scope]
                theirs = ev["m2"][ext][str(m)][scope]
                pairs = (("n", mine["top1"]["n"], theirs["n"]),
                         ("top1_hit", mine["top1"]["within_30"], theirs["top1_hit"]["k"]),
                         ("confident_hit", mine["confident"]["within_30"], theirs["confident_hit"]["k"]),
                         ("confident_wrong", mine["confident"]["n"] - mine["confident"]["within_30"],
                          theirs["confident_wrong"]["k"]))
                for name, a, b in pairs:
                    if a != b:
                        raise FigureError(f"{ext} {m} min {scope}: {name} is {a} in test_results.csv but {b} in "
                                          "evaluation.json; the two files are not from the same scoring run")
                n += 1
    return n


# -------------------------------------------------------- M2: signed error
# Display position of a signed error (s). Inside the hit band the axis is linear; outside it is
# logarithmic. Errors are whole seconds, so the band edge is drawn at 30.5 s: |e| <= 30 is inside.
BAND_HALF = HIT_S + 0.5
N_LIN_BINS = 5                    # bins across the hit band
LOG_BINS_PER_DECADE = 2
BIN_W = 1.0 / LOG_BINS_PER_DECADE
LIN_HALF = N_LIN_BINS * BIN_W / 2          # display half-width of the band, in decades


def sym_pos(e):
    """Signed error (s) -> display position. Monotone; +-BAND_HALF maps to +-LIN_HALF."""
    e = np.asarray(e, dtype=float)
    a = np.abs(e)
    lin = a / BAND_HALF * LIN_HALF
    log = LIN_HALF + np.log10(np.maximum(a, BAND_HALF) / BAND_HALF)
    return np.sign(e) * np.where(a <= BAND_HALF, lin, log)


def sym_edges(max_abs_error_s: float) -> np.ndarray:
    """Bin edges in display units: equal width, symmetric, N_LIN_BINS bins across the band."""
    need = float(sym_pos(max(float(max_abs_error_s), BAND_HALF)))
    k = max(0, math.ceil((need - LIN_HALF) / BIN_W - 1e-9))
    half = LIN_HALF + k * BIN_W
    n = int(round(2 * half / BIN_W))
    return -half + BIN_W * np.arange(n + 1)


def error_histogram(errors, edges: np.ndarray) -> dict:
    """Counts per display bin, and which bins lie inside the hit band."""
    t = sym_pos(np.asarray(errors, dtype=float)) if len(errors) else np.empty(0)
    counts, _ = np.histogram(t, bins=edges)
    centres = (edges[:-1] + edges[1:]) / 2
    in_band = np.abs(centres) < LIN_HALF
    return {"counts": counts.astype(int), "centres": centres, "in_band": in_band}


def m2_cells(rows: list, ext: str) -> dict:
    out = {}
    for m in LENGTHS:
        for scope in SCOPES:
            cr = cell_rows(rows, ext, m, scope)
            errs = [_int(r["error_s"]) for r in cr if r.get("error_s", "") != ""]
            out[(m, scope)] = {"n": len(cr), "hits": sum(1 for r in cr if _bool(r.get("hit_30s"))),
                               "no_candidate": len(cr) - len(errs), "errors": errs}
    return out


# ------------------------------------------------------ M3: reliability

def conf_bin(c: float) -> int:
    for i in range(len(CONF_BINS) - 1):
        if CONF_BINS[i] <= c < CONF_BINS[i + 1]:
            return i
    raise ValueError(f"confidence {c} is outside every bin")


def bin_label(i: int) -> str:
    lo, hi = CONF_BINS[i], CONF_BINS[i + 1]
    return f"[{lo:g}, {hi:g})" if math.isfinite(hi) else f"[{lo:g}, inf)"


def reliability(cr: list) -> dict:
    """Test top-1 hit rate by confidence bin for one cell (PROTOCOL section 11, M3)."""
    k = [0] * (len(CONF_BINS) - 1)
    n = [0] * (len(CONF_BINS) - 1)
    hits, misses, none = [], [], 0
    for r in cr:
        c = _float(r.get("confidence"))
        if c is None:
            none += 1
            continue
        i = conf_bin(c)
        n[i] += 1
        if _bool(r.get("hit_30s")):
            k[i] += 1
            hits.append(c)
        else:
            misses.append(c)
    return {"n_excerpts": len(cr), "n_without_confidence": none,
            "bins": [{"bin": bin_label(i), "lo": CONF_BINS[i],
                      "hi": None if not math.isfinite(CONF_BINS[i + 1]) else CONF_BINS[i + 1],
                      **rate(k[i], n[i])} for i in range(len(n))],
            "confidence_hits": sorted(hits), "confidence_misses": sorted(misses)}


def negatives_of(fm: dict, ext: str, m: int, scope: str) -> dict:
    """Report-half negatives of one cell from results/false_matches.json."""
    cell = fm["cells"][ext][str(m)][scope]
    out = {"tau": cell.get("tau")}
    for kind in ("real", "SYNTHETIC"):
        ts = [t for t in cell.get("trials", []) if t.get("kind") == kind]
        conf = sorted(float(t["confidence"]) for t in ts if t.get("confidence") is not None)
        out[kind] = {"n": len(ts), "n_without_confidence": len(ts) - len(conf), "confidence": conf,
                     "n_at_or_above_tau": sum(1 for t in ts if t.get("false_match")),
                     "n_at_or_above_tau_with_hum_flag": sum(1 for t in ts if t.get("false_match_hum"))}
    return out


def conf_pos(c):
    """Confidence -> display position: linear on [0, 0.025], then one unit per doubling,
    so the five finite pre-registered bins have equal width."""
    c = np.asarray(c, dtype=float)
    first = CONF_BINS[1]
    return np.where(c <= first, c / first, 1.0 + np.log2(np.maximum(c, first) / first))


def swarm(x_pt, d_pt: float) -> np.ndarray:
    """Vertical offsets (points) that keep markers of diameter d_pt from overlapping.
    Deterministic: markers are placed in order of x, each at the free slot nearest the row."""
    x = np.asarray(x_pt, dtype=float)
    y = np.zeros(x.size)
    placed = []
    for i in np.argsort(x, kind="stable"):
        k = 0
        while True:
            cands = (0.0,) if k == 0 else (k * d_pt * 0.9, -k * d_pt * 0.9)
            ok = None
            for yc in cands:
                if all((x[i] - px) ** 2 + (yc - py) ** 2 >= d_pt ** 2 - 1e-9 for px, py in placed):
                    ok = yc
                    break
            if ok is not None:
                y[i] = ok
                placed.append((x[i], ok))
                break
            k += 1
    return y


# ------------------------------------------------- M4: measured trace noise

def sigma_eff(s: float, r):
    """scripts/probe_noise.py: sigma = s * sqrt(1 / r**2 - 1). None unless 0 < r <= 1."""
    if r is None or not (0.0 < r <= 1.0):
        return None
    return float(s) * math.sqrt(1.0 / (r * r) - 1.0)


def pearson(x, y) -> float | None:
    x = np.asarray(x, dtype=float) - np.mean(x)
    y = np.asarray(y, dtype=float) - np.mean(y)
    den = math.sqrt(float((x * x).sum()) * float((y * y).sum()))
    return None if den == 0 else float((x * y).sum() / den)


def excerpt_noise(ref_freq, ref_valid, index: int, trace, weights, r_scored: float) -> dict:
    """Equivalent trace noise of one excerpt locked at reference second ``index`` (frame 0)."""
    from gunj.match import ref_segment
    x = np.asarray(trace, dtype=float)
    y, ok = ref_segment(ref_freq, ref_valid, int(index), int(index) + x.size)
    s = float(np.std(y))
    r_unw = pearson(x, y)
    out = {"n_frames": int(x.size), "window_valid_share": float(np.mean(ok)), "ref_window_std_hz": s,
           "r_scored": float(r_scored), "sigma_A_hz": sigma_eff(s, r_scored),
           "r_unweighted": r_unw, "sigma_B_hz": sigma_eff(s, r_unw)}
    if weights is not None:
        u = np.asarray(weights, dtype=float)
        W = float(u.sum())
        yc = y - float((u * y).sum()) / W
        xc = x - float((u * x).sum()) / W
        syy, sxx = float((u * yc * yc).sum()), float((u * xc * xc).sum())
        out["ref_window_weighted_std_hz"] = math.sqrt(syy / W)
        out["r_weighted_recomputed"] = None if syy <= 0 or sxx <= 0 else float((u * xc * yc).sum() / math.sqrt(sxx * syy))
        out["sigma_A_weighted_std_hz"] = sigma_eff(out["ref_window_weighted_std_hz"], r_scored)
    return out


def hit_excerpts(rows: list, scopes=GATE_SCOPES, ext: str = PRIMARY) -> dict:
    """{(video_id, length): row} for excerpts whose top-1 is within 30 s of T* in one of ``scopes``.
    The same excerpt in two scopes is kept once (the row of the first scope in which it is a hit).
    ``_locks`` and ``_matches`` list the scopes in which that hit has confidence >= tau, and a
    verdict "match". Streams harvested after the gate's decision time are left out."""
    out = {}
    for scope in scopes:
        for r in rows:
            if r["extractor"] != ext or r["scope"] != scope or not _bool(r.get("hit_30s")):
                continue
            if _bool(r.get("harvested_by_decision_time")) is False:
                continue
            key = (r["video_id"], _int(r["excerpt_min"]))
            if key not in out:
                out[key] = dict(r, _locks=[], _matches=[])
            if _bool(r.get("confidence_ge_tau")):
                out[key]["_locks"].append(scope)
            if r["verdict"] == "match":
                out[key]["_matches"].append(scope)
    return out


def search_of(rec: dict, ext: str, m: int, scope: str):
    for s in rec.get("searches", []):
        if s["ext"] == ext and int(s["length_min"]) == int(m) and s["scope"] == scope:
            return s
    return None


def measure_noise(rows: list, ref, traces_dir: Path, cache_dir: Path) -> list:
    """One record per Test excerpt (v2) whose top-1 is within 30 s of T* in the full or +-90-day scope."""
    out = []
    for (vid, m), row in sorted(hit_excerpts(rows).items()):
        rec = read_json(Path(cache_dir) / "test" / f"{vid}.json")
        s = search_of(rec, PRIMARY, m, row["scope"])
        if s is None or not s.get("candidates"):
            raise FigureError(f"{vid} {m} min {row['scope']}: no candidate in results/search_cache/test")
        top = s["candidates"][0]
        if abs(top["r"] - float(row["r1"])) > 2e-6 or top["start_ist"] != row["top1_start_ist"]:
            raise FigureError(f"{vid} {m} min: results/search_cache/test and results/test_results.csv disagree")
        with np.load(Path(traces_dir) / "test" / f"{vid}.npz", allow_pickle=False) as z:
            x = z[f"{PRIMARY}__x{m}__freq"].astype(np.float64)
            wkey = f"{PRIMARY}__x{m}__fw"
            u = z[wkey].astype(np.float64) if wkey in z.files else None
        if x.size != int(s["n_frames"]):
            raise FigureError(f"{vid} {m} min: the trace has {x.size} frames, the search used {s['n_frames']}")
        en = excerpt_noise(ref.freq, ref.valid, int(top["index"]), x, u, float(top["r"]))
        rw = en.get("r_weighted_recomputed")
        # The matcher fills reference gaps over a whole search block, this script over the matched
        # window only, so a window with missing seconds may differ a little; a full window may not.
        tol = 1e-6 if en["window_valid_share"] == 1.0 else 5e-3
        if rw is not None and abs(rw - float(top["r"])) > tol:
            raise FigureError(f"{vid} {m} min: recomputed weighted r {rw:.6f} differs from the recorded "
                              f"{top['r']:.6f}; the matched window is not aligned")
        out.append({"video_id": vid, "excerpt_min": m, "scope_used": row["scope"],
                    "lock_scopes": list(row["_locks"]), "match_scopes": list(row["_matches"]),
                    "gate_lock": bool(row["_locks"]), "confident_hit": bool(row["_matches"]),
                    "lock_index": int(top["index"]), "error_s": _int(row["error_s"]),
                    **{k: (r6(v) if isinstance(v, float) else v) for k, v in en.items()}})
    return out


def per_stream(excerpts: list, key: str) -> dict:
    """{video_id: median of ``key`` over the stream's excerpts}. An undefined value ranks above
    every number, so the median is None when it falls on or beside one."""
    by = {}
    for e in excerpts:
        by.setdefault(e["video_id"], []).append(e.get(key))
    return {vid: quartiles(v)["median"] for vid, v in sorted(by.items())}


def table_levels(table: list, kind: str) -> list:
    """Pre-set noise levels (mHz) of one noise kind. The 0 mHz control is shared by both kinds."""
    return sorted({float(r["sigma_mhz"]) for r in table
                   if r["noise_kind"] == kind or float(r["sigma_mhz"]) == 0.0})


def table_rate(table: list, kind: str, scope: str, length_min: int, sigma_mhz: float):
    for r in table:
        if (r["scope"] == scope and int(float(r["length_min"])) == int(length_min)
                and float(r["sigma_mhz"]) == float(sigma_mhz)
                and (r["noise_kind"] == kind or float(r["sigma_mhz"]) == 0.0)):
            return int(r["hits"]) / int(r["N"]), int(r["hits"]), int(r["N"])
    return None


def read_uniqueness(table: list, kind: str, scope: str, sigma_mhz: float) -> dict:
    """Hit rate at ``sigma_mhz`` for every tested length, by linear interpolation between the two
    pre-set levels that bracket it. Above the largest pre-set level nothing is read."""
    levels = table_levels(table, kind)
    lengths = sorted({int(float(r["length_min"])) for r in table if r["scope"] == scope})
    out = {"sigma_mhz": sigma_mhz, "noise_kind": kind, "scope": scope, "levels_mhz": levels,
           "rates_by_length_min": {}, "bracket_mhz": None, "shortest_length_min": None,
           "previous_tested_min": None, "status": "read"}
    if sigma_mhz is None or not levels or sigma_mhz > levels[-1] or sigma_mhz < levels[0]:
        out["status"] = "not read: the level is outside the pre-set levels"
        return out
    hi_i = next(i for i, lv in enumerate(levels) if lv >= sigma_mhz)
    lo_i = hi_i if levels[hi_i] == sigma_mhz else hi_i - 1
    lo, hi = levels[lo_i], levels[hi_i]
    out["bracket_mhz"] = [lo, hi]
    for m in lengths:
        a, b = table_rate(table, kind, scope, m, lo), table_rate(table, kind, scope, m, hi)
        if a is None or b is None:
            continue
        w = 0.0 if hi == lo else (sigma_mhz - lo) / (hi - lo)
        out["rates_by_length_min"][str(m)] = {
            "rate": a[0] + w * (b[0] - a[0]),
            "at_lower_level": {"sigma_mhz": lo, "hits": a[1], "N": a[2]},
            "at_upper_level": {"sigma_mhz": hi, "hits": b[1], "N": b[2]}}
    ok = [m for m in lengths if str(m) in out["rates_by_length_min"]
          and out["rates_by_length_min"][str(m)]["rate"] >= UNIQUE_TARGET]
    if ok:
        out["shortest_length_min"] = ok[0]
        below = [m for m in lengths if m < ok[0]]
        out["previous_tested_min"] = below[-1] if below else None
    else:
        out["status"] = "read: no tested length reaches 90%"
    return out


# ------------------------------------------------------------- matplotlib

def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def save_png(fig, path: Path) -> None:
    plt = _plt()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fig.savefig(tmp, format="png", dpi=DPI, facecolor="white", metadata={"Software": None})
    plt.close(fig)
    _replace(tmp, path)


def _patch(colour, edge=None, hatch=None):
    from matplotlib.patches import Patch
    return Patch(facecolor=colour, edgecolor=edge or colour, linewidth=0.6 if edge else 0, hatch=hatch)


def _wrap(text: str, width: int = 142) -> str:
    """Break a long figure note into lines that fit the 9-inch figure at 8 pt."""
    import textwrap
    return "\n".join(textwrap.wrap(text, width=width, break_long_words=False, break_on_hyphens=False))


# Ticks at the pre-registered 600 s coarse limit and at the two scope limits (1 day, 90 days).
# The 10-min labels sit on a second line so that neighbouring labels do not touch.
ERR_TICKS = ((-90 * 86400, "−90 d"), (-86400, "−1 d"), (-600, "\n−10 min"), (0, "0"),
             (600, "\n10 min"), (86400, "1 d"), (90 * 86400, "90 d"))
ERR_MINOR = (-10 * 86400, -3600, -COARSE_S[0], COARSE_S[0], 3600, 10 * 86400)


def fig_signed_error(cells: dict, ext: str, path: Path) -> dict:
    """4 x 3 grid of signed-error histograms (PROTOCOL section 11, M2)."""
    plt = _plt()
    primary = ext == PRIMARY
    miss_colour = AMBER if primary else GREY
    max_abs = max([abs(e) for c in cells.values() for e in c["errors"]] + [BAND_HALF])
    edges = sym_edges(max_abs)
    half = float(edges[-1])
    drawn = {}
    with plt.rc_context(STYLE):
        fig, axes = plt.subplots(len(LENGTHS), len(SCOPES), figsize=(FIG_W, 8.9), sharex=True, sharey=True)
        ymax = 1
        for i, m in enumerate(LENGTHS):
            for j, scope in enumerate(SCOPES):
                ax, c = axes[i, j], cells[(m, scope)]
                h = error_histogram(c["errors"], edges)
                ymax = max(ymax, int(h["counts"].max()) if h["counts"].size else 1)
                ax.axvspan(-LIN_HALF, LIN_HALF, color=GREEN_TINT, lw=0, zorder=0)
                if scope in SCOPE_LIMIT_S:
                    lim = float(sym_pos(SCOPE_LIMIT_S[scope]))
                    if lim < half:
                        ax.axvspan(lim, half, color=SHADE, lw=0, zorder=0)
                        ax.axvspan(-half, -lim, color=SHADE, lw=0, zorder=0)
                cols = [GREEN if b else miss_colour for b in h["in_band"]]
                ax.bar(h["centres"], h["counts"], width=BIN_W * 0.84, color=cols, linewidth=0, zorder=2)
                ax.set_title(f"{m} min, {SCOPE_LABEL[scope]}: N = {c['n']}, hits {c['hits']}", loc="left", pad=4)
                ax.set_xlim(-half, half)
                ax.set_xticks([float(sym_pos(v)) for v, _ in ERR_TICKS])
                ax.set_xticklabels([lab for _, lab in ERR_TICKS], fontsize=7.5)
                ax.set_xticks([float(sym_pos(v)) for v in ERR_MINOR], minor=True)
                ax.yaxis.grid(True, color=GRID, lw=0.6, zorder=1)
                ax.set_axisbelow(True)
                ax.tick_params(axis="y", length=0)
                drawn[f"{m}/{scope}"] = {"n": c["n"], "hits": c["hits"], "no_candidate": c["no_candidate"],
                                         "bin_counts": [int(v) for v in h["counts"]]}
        step = 5 if ymax <= 30 else 10
        top = step * math.ceil((ymax + 1) / step)
        for ax in axes.flat:
            ax.set_ylim(0, top)
            ax.set_yticks(range(0, top + 1, step))
        for ax in axes[:, 0]:
            ax.set_ylabel("Test streams per bin")
        for ax in axes[-1, :]:
            ax.set_xlabel("signed error e of the top-1 candidate")
        name = f"extractor {ext}" + (" (primary)" if primary else " (baseline)")
        fig.suptitle(f"M2: signed error of the top-1 candidate, {name}, Test split",
                     x=0.012, y=0.992, ha="left", va="top", fontsize=10.5, color=INK)
        fig.text(0.012, 0.963, "e = implied recording start of the top-1 candidate − T*. The axis is linear inside "
                 "±30 s and logarithmic outside; all bins have the same drawn width.",
                 ha="left", va="top", fontsize=8, color=INK_2)
        handles = [_patch(GREEN), _patch(miss_colour), _patch(GREEN_TINT), _patch(SHADE)]
        labels = ["top-1 within 30 s of T* (hit)", "top-1 more than 30 s from T*",
                  "hit band, |e| ≤ 30 s", "outside the search scope"]
        fig.legend(handles, labels, loc="upper left", bbox_to_anchor=(0.006, 0.944), ncol=4,
                   handlelength=1.2, handleheight=0.9, columnspacing=1.6, borderaxespad=0.0)
        per_len = {m: sorted({cells[(m, s)]["no_candidate"] for s in SCOPES}) for m in LENGTHS}
        note = "N counts every Test stream with a trace at that length."
        if any(cells[k]["no_candidate"] for k in cells):
            if all(len(v) == 1 for v in per_len.values()):
                txt = ", ".join(f"{per_len[m][0]} at {m} min" for m in LENGTHS)
            else:
                txt = ", ".join(f"{cells[(m, s)]['no_candidate']} at {m} min {SCOPE_SHORT[s]}"
                                for m in LENGTHS for s in SCOPES)
            note += (" Streams whose search could not run (constant trace) have no top-1 candidate and are not "
                     f"drawn: {txt}.")
        fig.text(0.012, 0.008, _wrap(note, 150), ha="left", va="bottom", fontsize=7.5, color=INK_2,
                 linespacing=1.3)
        fig.subplots_adjust(left=0.062, right=0.992, top=0.9, bottom=0.106, hspace=0.36, wspace=0.07)
        save_png(fig, path)
    return {"extractor": ext, "bin_edges_display": [float(v) for v in edges],
            "band_half_width_s": BAND_HALF, "display_rule": "linear for |e| <= 30.5 s, then one unit per decade",
            "cells": drawn}


def _conf_axis(ax, xmax: float, labels: bool = True, small: bool = False) -> None:
    ticks = list(range(0, int(xmax) + 1))
    vals = [0.0] + [CONF_BINS[1] * 2 ** (t - 1) for t in ticks[1:]]
    ax.set_xlim(0, xmax)
    ax.set_xticks(ticks)
    if labels:
        ax.set_xticklabels([f"{v:g}" for v in vals], fontsize=6.5 if small else 8)
    for t in range(1, len(CONF_BINS) - 1):
        ax.axvline(t, color=GRID, lw=0.6, zorder=0)


def _conf_xmax(*groups) -> float:
    vals = [float(v) for g in groups for v in g]
    top = max(vals + [CONF_BINS[-2] * 2])
    return float(max(6, math.ceil(float(conf_pos(top)) + 1e-9)))


def _draw_reliability(ax, rel_d: dict, xmax: float, label_size: float, marker: float) -> None:
    for i, b in enumerate(rel_d["bins"]):
        x0, x1 = float(i), (float(i + 1) if b["hi"] is not None else xmax)
        xc = (x0 + x1) / 2
        if not b["n"]:
            ax.text(xc, 0.03, "n = 0", ha="center", va="bottom", fontsize=label_size, color=INK_2)
            continue
        lo, hi = b["wilson95"]
        ax.plot([x0 + 0.06, x1 - 0.06], [b["rate"], b["rate"]], color=AMBER, lw=2.2, solid_capstyle="butt", zorder=3)
        ax.plot([xc, xc], [lo, hi], color=INK_2, lw=0.9, zorder=2)
        ax.plot([xc], [b["rate"]], marker="o", ms=marker, color=AMBER, markeredgecolor="white",
                markeredgewidth=1.0, zorder=4)
        up = hi + 0.03 <= 0.9
        ax.text(xc, hi + 0.025 if up else lo - 0.03, f"{b['k']}/{b['n']}", ha="center",
                va="bottom" if up else "top", fontsize=label_size, color=INK, zorder=6,
                bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.8, "pad": 0.6})


def fig_reliability(rel_d: dict, neg: dict, title: str, path: Path) -> dict:
    """Primary-cell reliability plot (PROTOCOL section 11, M3)."""
    plt = _plt()
    tau = neg.get("tau")
    rows = [("Test, top-1 within 30 s", rel_d["confidence_hits"], GREEN, True),
            ("Test, top-1 more than\n30 s from T*", rel_d["confidence_misses"], AMBER, True),
            ("real negatives,\nreport half", neg["real"]["confidence"], GREY, True),
            ("SYNTHETIC negatives,\nreport half", neg["SYNTHETIC"]["confidence"], GREY, False)]
    xmax = _conf_xmax(*[r[1] for r in rows])
    fig_h = 6.5
    with plt.rc_context(STYLE):
        fig = plt.figure(figsize=(FIG_W, fig_h))
        left, right = 0.2, 0.985
        ax = fig.add_axes([left, 0.5, right - left, 0.32])
        ax2 = fig.add_axes([left, 0.085, right - left, 0.335], sharex=ax)
        _conf_axis(ax, xmax)
        _draw_reliability(ax, rel_d, xmax, 8.5, 6.5)
        ax.set_ylim(0, 1.04)
        ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
        ax.yaxis.grid(True, color=GRID, lw=0.6, zorder=0)
        ax.set_axisbelow(True)
        ax.tick_params(axis="y", length=0)
        ax.tick_params(axis="x", labelbottom=False)
        ax.set_ylabel("Test top-1 hit rate\n(within 30 s of T*)")
        for i in range(len(CONF_BINS) - 1):          # the pre-registered bins, named under the top panel
            x1 = float(i + 1) if math.isfinite(CONF_BINS[i + 1]) else xmax
            lab = bin_label(i).replace("inf", "∞")
            ax.text((i + x1) / 2, -0.035, lab, transform=ax.get_xaxis_transform(), ha="center", va="top",
                    fontsize=7.5, color=INK_2)
        # strips
        pts_per_unit = FIG_W * (right - left) * 72 / xmax
        row_pt = fig_h * 0.335 * 72 / len(rows)
        d_pt = 4.6
        for k, (lab, vals, colour, filled) in enumerate(rows):
            yrow = len(rows) - 1 - k
            if k:
                ax2.axhline(yrow + 0.5, color=GRID, lw=0.6, zorder=0)
            x = conf_pos(np.asarray(vals, dtype=float)) if len(vals) else np.empty(0)
            off = swarm(x * pts_per_unit, d_pt) / row_pt if len(vals) else np.empty(0)
            if off.size and np.abs(off).max() > 0.44:
                off = off * 0.44 / np.abs(off).max()
            ax2.plot(x, yrow + off, ls="none", marker="o", ms=d_pt - 1.0, color=colour,
                     markerfacecolor=colour if filled else "white", markeredgecolor=colour if not filled else "white",
                     markeredgewidth=0.9 if not filled else 0.4, zorder=3, clip_on=False)
        ax2.set_ylim(-0.5, len(rows) - 0.5)
        ax2.set_yticks([len(rows) - 1 - k for k in range(len(rows))])
        ax2.set_yticklabels([f"{lab} (n = {len(vals)})" for lab, vals, _, _ in rows], fontsize=8, color=INK)
        ax2.tick_params(axis="y", length=0)
        ax2.spines["left"].set_visible(False)
        _conf_axis(ax2, xmax)
        ax2.set_xlabel("confidence = atanh(r1) − atanh(r2)  (linear below 0.025, one step per doubling above)")
        if tau is not None:
            xt = float(conf_pos(tau))
            for a in (ax, ax2):
                a.axvline(xt, color=INK, lw=1.0, ls=(0, (4, 3)), zorder=5)
            ax.text(xt, 1.02, f"τ = {tau:.4f}", transform=ax.get_xaxis_transform(), ha="center", va="bottom",
                    fontsize=8.5, color=INK)
        fig.text(0.012, 0.99, "M3 reliability: Test top-1 hit rate by confidence bin", ha="left", va="top",
                 fontsize=10.5, color=INK)
        fig.text(0.012, 0.955, _wrap(title + " Labels are hits / Test excerpts in the bin; whiskers are Wilson 95% "
                                     "intervals. Each dot in the lower panel is one excerpt. A verdict “match” "
                                     "needs confidence ≥ τ and the hum flag."),
                 ha="left", va="top", fontsize=8, color=INK_2, linespacing=1.35)
        save_png(fig, path)
    return {"x_max_display": xmax}


def fig_reliability_grid(rels: dict, negs: dict, ext: str, counts: dict, path: Path) -> None:
    """Reliability for every cell of one extractor (4 x 3)."""
    plt = _plt()
    xmax = _conf_xmax(*[rels[k]["confidence_hits"] + rels[k]["confidence_misses"] for k in rels],
                      *[negs[k][kind]["confidence"] for k in negs for kind in ("real", "SYNTHETIC")])
    with plt.rc_context(STYLE):
        fig, axes = plt.subplots(len(LENGTHS), len(SCOPES), figsize=(FIG_W, 9.2), sharex=True, sharey=True)
        for i, m in enumerate(LENGTHS):
            for j, scope in enumerate(SCOPES):
                ax, rel_d, neg = axes[i, j], rels[(m, scope)], negs[(m, scope)]
                _conf_axis(ax, xmax, small=True)
                _draw_reliability(ax, rel_d, xmax, 6.5, 4.2)
                ax.axhline(0, color=RULE, lw=0.6, zorder=1)
                for yrow, kind, filled in ((-0.13, "real", True), (-0.27, "SYNTHETIC", False)):
                    v = neg[kind]["confidence"]
                    ax.plot(conf_pos(np.asarray(v, dtype=float)) if v else [], [yrow] * len(v), ls="none", marker="o",
                            ms=3.0, color=GREY, markerfacecolor=GREY if filled else "white",
                            markeredgecolor=GREY, markeredgewidth=0.7, alpha=0.9, zorder=3, clip_on=False)
                if neg.get("tau") is not None:
                    ax.axvline(float(conf_pos(neg["tau"])), color=INK, lw=0.9, ls=(0, (4, 3)), zorder=5)
                tau_txt = "n/a" if neg.get("tau") is None else f"{neg['tau']:.3f}"
                ax.set_title(f"{m} min, {SCOPE_LABEL[scope]}: N = {rel_d['n_excerpts']}, τ = {tau_txt}",
                             loc="left", pad=4)
                ax.set_ylim(-0.36, 1.16)
                ax.set_yticks([0, 0.5, 1.0])
                ax.tick_params(axis="y", length=0)
                ax.spines["bottom"].set_visible(False)
                ax.tick_params(axis="x", length=2)
        for ax in axes[:, 0]:
            ax.set_ylabel("top-1 hit rate")
        for ax in axes[-1, :]:
            ax.set_xlabel("confidence = atanh(r1) − atanh(r2)")
        fig.suptitle(f"M3 reliability for every cell, extractor {ext}"
                     + (" (primary)" if ext == PRIMARY else " (baseline)") + ", Test split",
                     x=0.012, y=0.993, ha="left", va="top", fontsize=10.5, color=INK)
        fig.text(0.012, 0.964, "Amber: Test top-1 hit rate per pre-registered confidence bin, labelled hits / "
                 "excerpts, with Wilson 95% intervals. Dashed line: τ of the cell.",
                 ha="left", va="top", fontsize=8, color=INK_2)
        from matplotlib.lines import Line2D
        hs = [Line2D([], [], ls="none", marker="o", ms=4, color=GREY, markerfacecolor=GREY),
              Line2D([], [], ls="none", marker="o", ms=4, color=GREY, markerfacecolor="white")]
        fig.legend(hs, [f"real negatives, report half ({counts['real']} streams searched)",
                        f"SYNTHETIC negatives, report half ({counts['SYNTHETIC']} items)"],
                   loc="upper left", bbox_to_anchor=(0.006, 0.95), ncol=2, columnspacing=1.6, borderaxespad=0.0,
                   handletextpad=0.3)
        fig.subplots_adjust(left=0.07, right=0.992, top=0.89, bottom=0.055, hspace=0.34, wspace=0.07)
        save_png(fig, path)


def _stack(ax, x: float, split: dict, classes, width: float, label_min: float, side_labels: bool) -> None:
    """One stacked bar. A count is printed inside its segment when the segment is tall enough;
    with side_labels the counts of thinner segments are printed beside the bar."""
    bottom = 0
    for c in classes:
        v = split[c]
        if not v:
            continue
        ax.bar([x], [v], bottom=[bottom], width=width, color=CLASS_COLOUR[c], edgecolor="white", linewidth=1.0,
               zorder=2)
        if v >= label_min:
            ax.text(x, bottom + v / 2, str(v), ha="center", va="center", fontsize=7.5, color=CLASS_TEXT[c], zorder=3)
        elif side_labels:
            ax.text(x + width / 2 + 0.05, bottom + v / 2, str(v), ha="left", va="center", fontsize=7,
                    color=INK_2, zorder=3)
        bottom += v
    ax.text(x, bottom, f"n = {split['n']}" if side_labels else f"{split['n']}", ha="center", va="bottom",
            fontsize=7.5, color=INK, zorder=3)


def fig_breakdown(bd_ext: dict, ext: str, path: Path) -> None:
    """Stacked bars by cell: confident verdicts (top) and every top-1 candidate (bottom)."""
    plt = _plt()
    xs, labels, centres = [], [], []
    for i, m in enumerate(LENGTHS):
        for j, scope in enumerate(SCOPES):
            xs.append(i * (len(SCOPES) + 0.9) + j)
            labels.append(SCOPE_SHORT[scope])
        centres.append(i * (len(SCOPES) + 0.9) + (len(SCOPES) - 1) / 2)
    cells = [(m, s) for m in LENGTHS for s in SCOPES]
    with plt.rc_context(STYLE):
        fig, (ax, ax2) = plt.subplots(2, 1, figsize=(FIG_W, 7.6), sharex=True,
                                      gridspec_kw={"height_ratios": [1.45, 1.0]})
        for a, key, ylabel in ((ax, "confident", "excerpts with verdict “match”"),
                               (ax2, "top1", "all excerpts (top-1 candidate,\nwhatever the verdict)")):
            top = max(bd_ext[str(m)][s][key]["n"] for m, s in cells)
            step = 5 if top <= 40 else 25
            ytop = step * math.ceil((top * 1.08 + 1) / step)
            for x, (m, s) in zip(xs, cells):
                _stack(a, x, bd_ext[str(m)][s][key], CLASSES, 0.6, ytop * (0.045 if key == "confident" else 0.07),
                       side_labels=key == "confident")
            a.set_ylim(0, ytop)
            a.set_yticks(range(0, ytop + 1, step))
            a.yaxis.grid(True, color=GRID, lw=0.6, zorder=0)
            a.set_axisbelow(True)
            a.tick_params(axis="y", length=0)
            a.tick_params(axis="x", length=0)
            a.set_ylabel(ylabel)
        ax2.set_xticks(xs)
        ax2.set_xticklabels(labels)
        ax2.set_xlim(xs[0] - 0.75, xs[-1] + 0.75)
        for cx, m in zip(centres, LENGTHS):
            ax2.text(cx, -0.17, f"{m}-min excerpts", transform=ax2.get_xaxis_transform(), ha="center", va="top",
                     fontsize=8.5, color=INK)
        used = [c for c in CLASSES if any(bd_ext[str(m)][s][k][c] for m, s in cells for k in ("confident", "top1"))]
        fig.legend([_patch(CLASS_COLOUR[c]) for c in used], [CLASS_LABEL[c] for c in used], loc="upper left",
                   bbox_to_anchor=(0.006, 0.915), ncol=len(used), handlelength=1.2, handleheight=0.9,
                   columnspacing=1.6, borderaxespad=0.0)
        fig.text(0.012, 0.992, f"Distance of the top-1 candidate from T*, extractor {ext}"
                 + (" (primary)" if ext == PRIMARY else " (baseline)") + ", Test split",
                 ha="left", va="top", fontsize=10.5, color=INK)
        fig.text(0.012, 0.962, _wrap("Classes from the pre-registered columns hit_30s, hit_120s and hit_600s. Only "
                                     "“within 30 s” is a hit; the other classes are confident wrong verdicts in the "
                                     "top panel. The number above each bar is its total. Scopes are the full span, "
                                     "±90 days and ±1 day."),
                 ha="left", va="top", fontsize=8, color=INK_2, linespacing=1.35)
        fig.subplots_adjust(left=0.085, right=0.992, top=0.868, bottom=0.085, hspace=0.1)
        save_png(fig, path)


def _nice_ceiling(v: float) -> float:
    """Smallest of 1, 2, 3, 5 times a power of ten that is at least v."""
    e = 10 ** math.floor(math.log10(v))
    for k in (1, 2, 3, 5, 10):
        if k * e >= v * (1 - 1e-12):
            return float(k * e)
    return float(10 * e)


def fig_m4(m4: dict, path: Path) -> None:
    """Measured equivalent trace noise against the pre-set levels, and the uniqueness readings."""
    plt = _plt()
    series = [("A", "reading A\nr as scored (v2, weighted)", AMBER),
              ("B", "reading B\nsame traces, unweighted r", GREY)]
    levels = [lv for lv in m4["uniqueness_levels_mhz"] if lv > 0]
    fig_h = 6.3
    with plt.rc_context(STYLE):
        fig = plt.figure(figsize=(FIG_W, fig_h))
        left, right = 0.215, 0.985
        ax = fig.add_axes([left, 0.555, right - left, 0.25])
        vals = {k: [1000 * v for v in m4["per_stream_sigma_hz"][k].values() if v is not None] for k, _, _ in series}
        allv = [v for k in vals for v in vals[k]] + levels
        lo, hi = 10 ** math.floor(math.log10(min(allv))), _nice_ceiling(max(allv) * 1.1)
        ax.set_xscale("log")
        ax.set_xlim(lo, hi)
        pts_per_dec = FIG_W * (right - left) * 72 / math.log10(hi / lo)
        row_pt = fig_h * 0.25 * 72 / 2.35
        for i, lv in enumerate(levels):
            ax.axvline(lv, color=GREY_LIGHT, lw=0.8, zorder=0)
            ax.text(lv, 1.02, f"{lv:g} mHz" if i == len(levels) - 1 else f"{lv:g}", transform=ax.get_xaxis_transform(),
                    ha="center" if i < len(levels) - 1 else "left", va="bottom", fontsize=7.5, color=INK_2)
        for k, (key, lab, colour) in enumerate(series):
            yrow = 1 - k
            v = np.asarray(vals[key], dtype=float)
            if not v.size:
                continue
            off = swarm(np.log10(v) * pts_per_dec, 5.4) / row_pt
            if np.abs(off).max() > 0.34:
                off = off * 0.34 / np.abs(off).max()
            ax.plot(v, yrow + off, ls="none", marker="o", ms=4.6, color=colour, markeredgecolor="white",
                    markeredgewidth=0.5, zorder=3)
            med = m4["distribution_hz"][key]["median"]
            if med is not None:
                ax.plot([1000 * med] * 2, [yrow - 0.4, yrow + 0.4], color=INK, lw=1.4, zorder=4)
                ax.text(1000 * med, yrow - 0.43, f"median {1000 * med:.1f} mHz", ha="center", va="top",
                        fontsize=7.5, color=INK)
        ax.set_ylim(-0.75, 1.6)
        ax.set_yticks([1, 0])
        ax.set_yticklabels([f"{lab}\n(n = {len(vals[key])} streams)" for key, lab, _ in series], fontsize=8, color=INK)
        ax.tick_params(axis="y", length=0)
        ax.spines["left"].set_visible(False)
        ax.set_xlabel("equivalent trace noise per stream, σ = s · sqrt(1/r² − 1)  (mHz, logarithmic axis)")
        from matplotlib.ticker import FixedLocator, FuncFormatter, NullFormatter
        majors = [t for t in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000) if lo <= t <= hi]
        ax.xaxis.set_major_locator(FixedLocator(majors))
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
        ax.xaxis.set_minor_formatter(NullFormatter())
        left2 = 0.085
        w = (right - left2 - 2 * 0.03) / 3
        handles = []
        for j, scope in enumerate(SCOPES):
            a = fig.add_axes([left2 + j * (w + 0.03), 0.08, w, 0.285])
            lens = None
            for key, lab, colour in series:
                rd = m4["readings"][key]["median"]["hann16"][scope]
                pts = sorted((int(m), d["rate"]) for m, d in rd["rates_by_length_min"].items())
                if not pts:
                    continue
                lens = [p[0] for p in pts]
                h, = a.plot(lens, [p[1] for p in pts], color=colour, lw=2, marker="o", ms=5.5,
                            markeredgecolor="white", markeredgewidth=1.0, zorder=3, clip_on=False)
                if j == 0:
                    handles.append((h, f"at the reading {key} median ({rd['sigma_mhz']:.1f} mHz)"))
            a.axhline(UNIQUE_TARGET, color=INK, lw=0.9, ls=(0, (4, 3)), zorder=2)
            a.set_xscale("log")
            if lens:
                a.set_xticks(lens)
                a.set_xticklabels([str(v) for v in lens])
                a.set_xlim(lens[0] * 0.85, lens[-1] * 1.18)
            a.minorticks_off()
            a.set_ylim(-0.03, 1.05)
            a.set_yticks([0, 0.25, 0.5, 0.75, 0.9, 1.0])
            a.set_yticklabels(["0", "0.25", "0.5", "0.75", "0.9", "1"] if j == 0 else [])
            a.yaxis.grid(True, color=GRID, lw=0.6, zorder=0)
            a.set_axisbelow(True)
            a.tick_params(axis="y", length=0)
            a.set_title(f"{SCOPE_LABEL[scope]}", loc="left", pad=4)
            a.set_xlabel("excerpt length (min)")
            if j == 0:
                a.set_ylabel("share of unique locks")
        if handles:
            fig.legend([h for h, _ in handles], [t for _, t in handles], loc="upper left",
                       bbox_to_anchor=(left2 - 0.01, 0.445), ncol=2, columnspacing=1.6, borderaxespad=0.0,
                       title="Uniqueness table (real reference excerpts, SYNTHETIC hann16 noise), interpolated:",
                       title_fontsize=8, alignment="left")
        fig.text(0.012, 0.99, "M4 at the measured noise level: Test streams that lock (extractor v2)", ha="left",
                 va="top", fontsize=10.5, color=INK)
        fig.text(0.012, 0.955, _wrap("Top: equivalent trace noise, one dot per stream (the median over its locking "
                                     "excerpts). Vertical lines are the pre-set SYNTHETIC noise levels of the M4 "
                                     "simulation. Bottom: the share of unique locks read from the uniqueness table at "
                                     "each median by linear interpolation between those levels. The dashed line is "
                                     "the 90% target."),
                 ha="left", va="top", fontsize=8, color=INK_2, linespacing=1.35)
        save_png(fig, path)


def fig_exploratory(ex: dict, path: Path) -> None:
    """EXPLORATORY (post hoc): five small panels, hits against 30 to 600 s offsets."""
    plt = _plt()
    groups = (("within_30", "within 30 s (hit)", GREEN), ("offset_30_600", "30 to 600 s from T*", AMBER))
    prim = ex["primary_cell"]
    last = LENGTHS[-1]

    def bars(ax, cats, counts, title, xlabel):
        y = np.arange(len(cats))[::-1]
        hh = 0.34
        top = max([1] + [counts[g][c] for g, _, _ in groups for c in cats])
        for gi, (g, _, colour) in enumerate(groups):
            vals = [counts[g][c] for c in cats]
            yy = y + (hh / 2 + 0.02) * (1 if gi == 0 else -1)
            ax.barh(yy, vals, height=hh, color=colour, linewidth=0, zorder=2)
            for yv, v in zip(yy, vals):
                ax.text(v + top * 0.03, yv, str(v), ha="left", va="center", fontsize=7.5, color=INK)
        ax.set_yticks(y)
        ax.set_yticklabels([c.replace(" ", "\n") if len(c) > 12 else c for c in cats], fontsize=8, color=INK,
                           linespacing=0.95)
        ax.set_xlim(0, top * 1.22)
        ax.set_ylim(-0.6, len(cats) - 0.4)
        ax.xaxis.set_major_locator(plt.MaxNLocator(4, integer=True))
        ax.xaxis.grid(True, color=GRID, lw=0.6, zorder=0)
        ax.set_axisbelow(True)
        ax.tick_params(axis="y", length=0)
        ax.set_title(title, loc="left", pad=4)
        ax.set_xlabel(xlabel)

    fig_h = 6.9
    with plt.rc_context(STYLE):
        fig = plt.figure(figsize=(FIG_W, fig_h))
        top_y, top_h, bot_y, bot_h = 0.55, 0.28, 0.075, 0.31
        ax = fig.add_axes([0.14, top_y, 0.5, top_h])
        row_pt = fig_h * top_h * 72 / 2.25
        dmax = max([1.0] + [abs(v) for g, _, _ in groups for v in prim[g]["abs_D_s_values"]])
        xr = 50 * math.ceil(dmax * 1.04 / 50)
        pts_per_s = FIG_W * 0.5 * 72 / (xr * 1.02)
        for gi, (g, lab, colour) in enumerate(groups):
            yrow = 1 - gi
            v = np.asarray(prim[g]["abs_D_s_values"], dtype=float)
            if v.size:
                off = swarm(v * pts_per_s, 6.2) / row_pt
                if np.abs(off).max() > 0.34:
                    off = off * 0.34 / np.abs(off).max()
                ax.plot(v, yrow + off, ls="none", marker="o", ms=5.4, color=colour, markeredgecolor="white",
                        markeredgewidth=0.6, zorder=3, clip_on=False)
                med = prim[g]["abs_D_s"]["median"]
                ax.plot([med, med], [yrow - 0.4, yrow + 0.4], color=INK, lw=1.4, zorder=4)
                ax.text(med, yrow - 0.43, f"median {med:g} s", ha="center", va="top", fontsize=7.5, color=INK)
        ax.set_ylim(-0.7, 1.55)
        ax.set_xlim(-xr * 0.02, xr)
        ax.set_yticks([1, 0])
        ax.set_yticklabels([f"within 30 s\n(n = {prim['within_30']['n_verdicts']})",
                            f"30 to 600 s\n(n = {prim['offset_30_600']['n_verdicts']})"], fontsize=8, color=INK)
        ax.tick_params(axis="y", length=0)
        ax.spines["left"].set_visible(False)
        ax.xaxis.grid(True, color=GRID, lw=0.6, zorder=0)
        ax.set_axisbelow(True)
        ax.set_xlabel("|D|, the label-drift correction of the stream (s)")
        ax.set_title(f"(a) |D| per stream, {last}-min excerpts", loc="left", pad=4)
        unit = f"verdicts at {last} min"
        bars(fig.add_axes([0.8, top_y, 0.185, top_h]), ["label-stable", "not label-stable"],
             {g: {"label-stable": prim[g]["label_stable"]["True"], "not label-stable": prim[g]["label_stable"]["False"]}
              for g, _, _ in groups}, f"(b) label stability, {last} min", unit)
        files = ex["categories"]["ref_src_file"]
        bars(fig.add_axes([0.085, bot_y, 0.22, bot_h]), [f"file {file_label(f)}" for f in files],
             {g: {f"file {file_label(f)}": prim[g]["ref_src_file"][f] for f in files}
              for g, _, _ in groups}, f"(c) reference file, {last} min", unit)
        bars(fig.add_axes([0.41, bot_y, 0.22, bot_h]), [f"{m} min" for m in LENGTHS],
             {g: {f"{m} min": ex["by_length"][str(m)][g]["n_verdicts"] for m in LENGTHS} for g, _, _ in groups},
             "(d) excerpt length", "verdicts at each length")
        venues = ex["categories"]["venue_type"]
        bars(fig.add_axes([0.765, bot_y, 0.22, bot_h]), venues, {g: {v: prim[g]["venue_type"][v] for v in venues}
                                                                 for g, _, _ in groups},
             f"(e) venue type, {last} min", unit)
        fig.legend([_patch(c) for _, _, c in groups], [lab for _, lab, _ in groups], loc="upper left",
                   bbox_to_anchor=(0.006, 0.9), ncol=2, handlelength=1.2, handleheight=0.9, columnspacing=1.6,
                   borderaxespad=0.0)
        fig.text(0.012, 0.992, "EXPLORATORY (post hoc, not pre-registered): confident verdicts within 30 s of T* "
                 "and 30 to 600 s from it", ha="left", va="top", fontsize=10.5, color=INK)
        fig.text(0.012, 0.96, _wrap(f"Extractor {ex['extractor']}, full span, excerpts with verdict “match”. Every "
                                    f"number is a count of verdicts. Panels (a), (b), (c) and (e) use the {last}-min "
                                    "excerpts, one verdict per stream; panel (d) shows all four excerpt lengths. "
                                    "Description only: no test and no new threshold."),
                 ha="left", va="top", fontsize=8, color=INK_2, linespacing=1.35)
        save_png(fig, path)


# ------------------------------------------------------------ exploratory

def _group_of(row: dict) -> str:
    c = error_class(row)
    if c == "within_30":
        return "within_30"
    if c in ("from_30_to_120", "from_120_to_600"):
        return "offset_30_600"
    return "beyond_600"


def describe_group(rows: list, src: dict, cats: dict, stream_level: bool = False) -> dict:
    """Counts and medians of one group. With stream_level=True every row is one stream, and the
    fields that belong to a single verdict (error, confidence) are left out."""
    absd = [abs(float(r["label_drift_s"])) for r in rows if r.get("label_drift_s", "") != ""]
    out = {"n_streams": len({r["video_id"] for r in rows}),
           "abs_D_s": round_stats(quartiles(absd), 3), "abs_D_s_values": sorted(absd),
           "label_stable": {k: sum(1 for r in rows if r.get("label_stable") == k) for k in ("True", "False")},
           "ref_src_file": {f: sum(1 for r in rows if src.get(r["video_id"]) == f) for f in cats["ref_src_file"]},
           "venue_type": {v: sum(1 for r in rows if r.get("venue_type") == v) for v in cats["venue_type"]}}
    if not stream_level:
        abse = [abs(_int(r["error_s"])) for r in rows if r.get("error_s", "") != ""]
        conf = [_float(r["confidence"]) for r in rows if r.get("confidence", "") != ""]
        out = {"n_verdicts": len(rows), **out, "abs_error_s_median": median(abse),
               "confidence_median": r6(median(conf))}
    return out


def exploratory(rows: list, splits: list, ext: str = PRIMARY, scope: str = "full") -> dict:
    """EXPLORATORY (post hoc). Confident verdicts of one extractor in one scope, grouped by distance from T*."""
    src = {r["video_id"]: r.get("ref_src_file", "") for r in splits}
    conf = [r for r in rows if r["extractor"] == ext and r["scope"] == scope and r["verdict"] == "match"]
    cats = {"ref_src_file": sorted({src.get(r["video_id"], "") for r in rows if r["extractor"] == ext},
                                   key=file_order),
            "venue_type": sorted({r["venue_type"] for r in rows if r["extractor"] == ext})}
    groups = ("within_30", "offset_30_600", "beyond_600")

    def by_group(sel):
        return {g: describe_group([r for r in sel if _group_of(r) == g], src, cats) for g in groups}

    by_len = {str(m): by_group([r for r in conf if _int(r["excerpt_min"]) == m]) for m in LENGTHS}
    off_ids = sorted({r["video_id"] for r in conf if _group_of(r) == "offset_30_600"})
    streams = []
    for vid in off_ids:
        mine = {(_int(r["excerpt_min"])): r for r in rows
                if r["extractor"] == ext and r["scope"] == scope and r["video_id"] == vid}
        any_row = next(iter(mine.values()))
        streams.append({"video_id": vid, "venue_type": any_row["venue_type"], "ref_src_file": src.get(vid, ""),
                        "label_drift_s": _float(any_row["label_drift_s"]),
                        "label_stable": any_row["label_stable"] == "True",
                        "by_length": {str(m): (None if m not in mine else {
                            "error_s": _int(mine[m]["error_s"]), "verdict": mine[m]["verdict"],
                            "class": error_class(mine[m])}) for m in LENGTHS}})
    with_hit_elsewhere = sum(1 for s in streams if any(v and v["class"] == "within_30"
                                                       for v in s["by_length"].values()))
    all_ids = {g: sorted({r["video_id"] for r in conf if _group_of(r) == g}) for g in groups}
    only_hits = sorted(set(all_ids["within_30"]) - set(all_ids["offset_30_600"]) - set(all_ids["beyond_600"]))
    first = {}
    for r in conf:
        first.setdefault(r["video_id"], r)
    stream_level = {
        "at_least_one_offset_30_600": describe_group([first[v] for v in off_ids], src, cats, stream_level=True),
        "every_confident_verdict_within_30": describe_group([first[v] for v in only_hits], src, cats,
                                                            stream_level=True)}
    return {"label": "EXPLORATORY (post hoc, not pre-registered)", "extractor": ext, "scope": scope,
            "unit": "one confident verdict = one Test stream at one excerpt length (verdict match)",
            "groups": {"within_30": "top-1 within 30 s of T* (hit_30s)",
                       "offset_30_600": "more than 30 s and up to 600 s from T* (hit_600s and not hit_30s)",
                       "beyond_600": "more than 600 s from T* (not hit_600s)"},
            "categories": cats, "primary_cell": by_len[str(LENGTHS[-1])], "by_length": by_len,
            "all_lengths_pooled": by_group(conf),
            "distinct_streams": stream_level,
            "offset_streams": streams,
            "offset_streams_with_a_hit_at_another_length": with_hit_elsewhere}


# --------------------------------------------------------------- markdown

def _share_cells(s: dict) -> str:
    return " | ".join(fmt_rate(s[k]) for k in ("within_30_share", "within_120_share", "within_600_share"))


def breakdown_md(bd: dict, meta: dict) -> str:
    L = ["# Confident verdicts and top-1 candidates by distance from T*", ""]
    L.append(f"Written by `scripts/make_figures.py` from `{meta['csv']}` (scoring run of {meta['written_ist']}, "
             f"`{meta['evaluation']}`). Every number is counted by the script from the pre-registered columns "
             "`verdict`, `hit_30s`, `hit_120s` and `hit_600s` (PROTOCOL.md section 9). The hit rule stays as "
             "pre-registered: a hit is a top-1 candidate within 30 s of T*. The 120 s and 600 s classes are the "
             "pre-registered coarse hits, reported for the label problem and never substituted for the 30 s rule.")
    L.append("")
    L.append("- Rows are Test streams with a trace at that excerpt length (the M2 denominators). A verdict "
             "\"match\" needs the hum flag and confidence at or above tau of the cell.")
    L.append("- The distance classes do not overlap: within 30 s; more than 30 s and up to 120 s; more than 120 s "
             "and up to 600 s; beyond 600 s.")
    L.append("- The three shares are cumulative (within 30 s, within 120 s, within 600 s) and carry Wilson 95% "
             "intervals (PROTOCOL.md section 13).")
    L.append(f"- Check: for all {meta['cells_checked']} cells the within-30 s counts equal `top1_hit`, "
             f"`confident_hit` and `confident_wrong` in `{meta['evaluation']}` (`m2`), and "
             f"{meta['column_mismatches']} rows have hit columns that disagree with `error_s`.")
    L.append(f"- Figure: `{meta['figure']}` (extractor {PRIMARY}).")
    L.append("")
    p = bd[PRIMARY][str(LENGTHS[-1])]["full"]
    c, t = p["confident"], p["top1"]
    L.append(f"## Primary cell ({PRIMARY}, {LENGTHS[-1]}-min excerpts, full span)")
    L.append("")
    L.append("| excerpts | n | within 30 s | 30 to 120 s | 120 to 600 s | beyond 600 s | no top-1 candidate |")
    L.append("|---|---|---|---|---|---|---|")
    L.append(f"| verdict \"match\" | {c['n']} | {c['within_30']} | {c['from_30_to_120']} | {c['from_120_to_600']} | "
             f"{c['beyond_600']} | {c['no_candidate']} |")
    L.append(f"| all, whatever the verdict | {t['n']} | {t['within_30']} | {t['from_30_to_120']} | "
             f"{t['from_120_to_600']} | {t['beyond_600']} | {t['no_candidate']} |")
    L.append("")
    L.append(f"- Verdict \"match\": within 30 s {fmt_rate(c['within_30_share'])}; within 120 s "
             f"{fmt_rate(c['within_120_share'])}; within 600 s {fmt_rate(c['within_600_share'])}.")
    L.append(f"- All excerpts: within 30 s {fmt_rate(t['within_30_share'])}; within 120 s "
             f"{fmt_rate(t['within_120_share'])}; within 600 s {fmt_rate(t['within_600_share'])}.")
    L.append("")
    for ext in EXTRACTORS:
        name = f"Extractor {ext}" + (" (primary)" if ext == PRIMARY else " (baseline)")
        for key, head in (("confident", "excerpts with verdict \"match\""),
                          ("top1", "top-1 candidate of every excerpt, whatever the verdict")):
            L.append(f"## {name}: {head}")
            L.append("")
            extra = " no top-1 candidate |" if key == "top1" else ""
            L.append("| excerpt | scope | n | within 30 s | 30 to 120 s | 120 to 600 s | beyond 600 s |" + extra
                     + " within 30 s, share | within 120 s, share | within 600 s, share |")
            L.append("|---|---|---|---|---|---|---|" + ("---|" if key == "top1" else "") + "---|---|---|")
            for m in LENGTHS:
                for s in SCOPES:
                    d = bd[ext][str(m)][s][key]
                    nc = f" {d['no_candidate']} |" if key == "top1" else ""
                    L.append(f"| {m} min | {SCOPE_LABEL[s]} | {d['n']} | {d['within_30']} | {d['from_30_to_120']} | "
                             f"{d['from_120_to_600']} | {d['beyond_600']} |{nc} {_share_cells(d)} |")
            L.append("")
    L.append("\"No top-1 candidate\" counts excerpts whose search could not run (a constant trace). They stay in "
             "the denominator, as in M2.")
    return "\n".join(L) + "\n"


def _mhz(v, nd=1):
    return "n/a" if v is None else f"{1000 * v:.{nd}f}"


def _reading_cell(rd: dict) -> str:
    if rd["shortest_length_min"] is None:
        return rd["status"]
    r = rd["rates_by_length_min"][str(rd["shortest_length_min"])]["rate"]
    prev = rd["previous_tested_min"]
    return (f"{rd['shortest_length_min']} min ({100 * r:.1f}%"
            + (f"; {prev} min is below {100 * UNIQUE_TARGET:.0f}%" if prev is not None else "; shortest tested")
            + ")")


def m4_md(m4: dict) -> str:
    L = ["# M4 at the measured noise level", ""]
    L.append(f"Written by `scripts/make_figures.py`. Inputs: `{m4['inputs']['test_results']}`, "
             f"`{m4['inputs']['gate_frozen']}`, `results/search_cache/test/`, `traces/test/`, the built reference "
             f"series, and `{m4['inputs']['uniqueness_table']}` (real reference excerpts with SYNTHETIC noise; no "
             "audio). PROTOCOL.md section 11, M4: \"The 'measured SNR' is the equivalent trace noise of the Test "
             "streams that lock, computed as in `scripts/probe_noise.py`. The curve is read at that noise level by "
             "interpolating between the pre-set levels.\" The simulation and its noise levels were set before any "
             "Test audio existed; the Test results only set the level at which the table is read.")
    L.append("")
    L.append("## What was measured")
    L.append("")
    L.append(f"- **Streams that lock:** the {m4['streams_that_lock']['n']} Test streams that the frozen gate counts "
             f"as locks (`{m4['inputs']['gate_frozen']}`): extractor {PRIMARY}, top-1 candidate in the full span or "
             "±90 days within 30 s of T* for at least one excerpt length, with confidence at or above tau of that "
             "cell (PROTOCOL.md section 15). The same list recomputed from "
             f"`{m4['inputs']['test_results']}`: "
             + ("identical." if m4["streams_that_lock"]["matches_recomputed_list"] else "DIFFERENT, see the JSON."))
    L.append(f"- **Excerpts:** every excerpt length at which such a stream locks ({m4['streams_that_lock']['n_excerpts']} "
             "excerpts; the same excerpt in both scopes is counted once).")
    L.append("- **Noise per excerpt, as in `scripts/probe_noise.py`:** sigma = s * sqrt(1/r^2 - 1), where s is the "
             "standard deviation of the gap-filled reference over the window the excerpt matched and r is the "
             "correlation at the lock.")
    L.append("- **One value per stream:** the median over its locking excerpts.")
    L.append("- **Reading A (r as scored):** r is the r1 that the frozen evaluation recorded. For v2 this is the "
             "weighted correlation of `gunj/match_v2.py`, which gives little weight to frames with weak hum.")
    L.append("- **Reading B (unweighted r):** r is the plain correlation of the same trace with the same window, "
             "every frame weighted equally. The M4 simulation adds noise of one size to every second and uses the "
             "unweighted matcher, so this is the noise that simulation's matcher would see in these traces.")
    L.append("")
    L.append("## Measured noise (mHz)")
    L.append("")
    L.append("| reading | streams | min | Q1 | median | Q3 | max |")
    L.append("|---|---|---|---|---|---|---|")
    for key, lab in (("A", "A: r as scored (v2, weighted)"), ("B", "B: same traces, unweighted r")):
        d = m4["distribution_hz"][key]
        L.append(f"| {lab} | {d['n']} | {_mhz(d['min'])} | {_mhz(d['q1'])} | {_mhz(d['median'])} | {_mhz(d['q3'])} | "
                 f"{_mhz(d['max'])} |")
    L.append("")
    aw = m4["distribution_hz"].get("A_weighted_std")
    if aw:
        L.append(f"- Reading A with s also weighted by the frame weights: median {_mhz(aw['median'])} mHz "
                 f"(Q1 {_mhz(aw['q1'])}, Q3 {_mhz(aw['q3'])}).")
    pr = m4.get("probe")
    if pr:
        L.append(f"- For comparison, the 5 Nov feasibility probe (one stream, an earlier extractor): median "
                 f"{_mhz(pr['sigma_eff_all_median_hz'])} mHz over {pr['n_all_points']} excerpts "
                 f"(`{m4['inputs']['probe_noise']}`).")
    bl = m4["by_length_hz"]
    L.append("- Median over locking excerpts by excerpt length, reading A / reading B: "
             + "; ".join(f"{m} min {_mhz(bl[str(m)]['A']['median'])} / {_mhz(bl[str(m)]['B']['median'])} mHz "
                         f"(n = {bl[str(m)]['A']['n']})" for m in LENGTHS if bl.get(str(m)) and bl[str(m)]["A"])
             + ". The cells have different tau, so different streams lock at each length.")
    L.append("")
    L.append("## Clip length needed for 90% unique locks at the measured level")
    L.append("")
    lv_txt = ", ".join(f"{v:g}" for v in m4["uniqueness_levels_mhz"])
    len_txt = ", ".join(str(v) for v in m4["uniqueness_lengths_min"])
    L.append(f"The hit rates of `{m4['inputs']['uniqueness_table']}` are interpolated linearly between the two "
             f"pre-set SYNTHETIC noise levels that bracket the measured median (levels in mHz: {lv_txt}; the 0 mHz "
             "control is shared by both noise kinds). The entry is the shortest tested length whose interpolated "
             f"rate is at least {100 * UNIQUE_TARGET:.0f}%. Only these lengths were tested (min): {len_txt}. The "
             "true length therefore lies between the previous tested length and the one shown. `white` noise is "
             "independent each second; `hann16` is smoothed with a 16 s Hann window like real extractor error and "
             "is the closer stand-in (`scripts/uniqueness.py`).")
    L.append("")
    L.append("| reading | level (mHz) | SYNTHETIC noise | full span | ±90 days | ±1 day |")
    L.append("|---|---|---|---|---|---|")
    for key in ("A", "B"):
        rd = m4["readings"][key]["median"]
        for kind in ("hann16", "white"):
            L.append(f"| {key}, median | {_mhz(m4['distribution_hz'][key]['median'])} | {kind} | "
                     + " | ".join(_reading_cell(rd[kind][s]) for s in SCOPES) + " |")
    L.append("")
    L.append("Interpolated share of unique locks by excerpt length, `hann16` noise:")
    L.append("")
    lens = m4["uniqueness_lengths_min"]
    L.append("| reading | scope | " + " | ".join(f"{m} min" for m in lens) + " |")
    L.append("|---|---|" + "---|" * len(lens))
    for key in ("A", "B"):
        for s in SCOPES:
            rd = m4["readings"][key]["median"]["hann16"][s]
            cells = [("n/a" if str(m) not in rd["rates_by_length_min"]
                      else f"{100 * rd['rates_by_length_min'][str(m)]['rate']:.1f}%") for m in lens]
            L.append(f"| {key}, median | {SCOPE_LABEL[s]} | " + " | ".join(cells) + " |")
    L.append("")
    L.append("The same reading at the quartiles of each distribution (`hann16`, shortest tested length reaching 90%):")
    L.append("")
    L.append("| reading | level (mHz) | full span | ±90 days | ±1 day |")
    L.append("|---|---|---|---|---|")
    for key in ("A", "B"):
        for q in ("q1", "q3"):
            rd = m4["readings"][key][q]["hann16"]
            L.append(f"| {key}, {q.upper()} | {_mhz(m4['distribution_hz'][key][q])} | "
                     + " | ".join(_reading_cell(rd[s]) for s in SCOPES) + " |")
    L.append("")
    L.append("## Other readings of \"streams that lock\"")
    L.append("")
    L.append("| streams | n | reading A median (mHz) | reading B median (mHz) |")
    L.append("|---|---|---|---|")
    for k, lab in (("gate_locks", "gate locks (used above)"),
                   ("confident_hits", "confident hits: verdict \"match\" and within 30 s, full span or ±90 days"),
                   ("all_top1_hits", "every stream with a top-1 within 30 s, full span or ±90 days, whatever tau"),
                   ("primary_cell_locks", f"locks in the primary cell only ({LENGTHS[-1]} min, full span)")):
        a = m4["alternatives_hz"][k]
        L.append(f"| {lab} | {a['A']['n'] if a['A'] else 0} | {_mhz(a['A']['median']) if a['A'] else 'n/a'} | "
                 f"{_mhz(a['B']['median']) if a['B'] else 'n/a'} |")
    L.append("")
    L.append("## Ambiguities and limits")
    L.append("")
    ma, mb = m4["distribution_hz"]["A"]["median"], m4["distribution_hz"]["B"]["median"]
    ratio = "" if not ma or not mb else f" At the median, reading B is {mb / ma:.1f} times reading A."
    L.append("- PROTOCOL.md does not say which correlation to use for an extractor with a weighted matcher. "
             "`scripts/probe_noise.py` took the r that its matcher printed, which corresponds to reading A. "
             "Reading B is given because the simulation it is compared with is unweighted." + ratio
             + " The clip length read from the table depends on the reading, so both are reported.")
    L.append("- Neither reading fits the simulation exactly. Reading A can flatter v2: a weighted correlation rests "
             "on fewer effective frames than the excerpt has, and the simulation does not model that. Reading B can "
             "understate v2: it counts noisy frames in full, and v2's matcher gives them little weight.")
    L.append("- \"Streams that lock\" could also mean confident hits or every top-1 hit; the table above gives "
             "their medians.")
    L.append("- The level is measured only on streams that lock, so it describes recordings with usable hum. "
             "It says nothing about the Test streams that do not lock.")
    L.append("- sigma is an equivalent white noise. It lumps together extraction noise, smoothing and any error "
             "in the alignment, and real trace error is not the same size in every second.")
    L.append("- The uniqueness table uses SYNTHETIC noise on real reference excerpts. The interpolation is linear "
             "in the noise level between two tested levels; the simulation was not re-run at the measured level.")
    if m4.get("undefined_B"):
        L.append(f"- Reading B is undefined for {m4['undefined_B']} excerpt(s) whose unweighted r is not positive; "
                 "they count as infinite noise in the per-stream median.")
    L.append(f"- Figure: `{m4['figure']}`.")
    return "\n".join(L) + "\n"


def _num(v) -> str:
    """A count or a median for a table: whole numbers in full, halves with one decimal."""
    if v is None:
        return "n/a"
    v = float(v)
    return f"{int(v):,}" if v == int(v) else f"{v:,.1f}"


def _d_range(d: dict | None) -> str:
    return "n/a" if not d else f"{_num(d['median'])} ({_num(d['min'])} to {_num(d['max'])})"


def _grp_row(lab: str, g: dict) -> str:
    return (f"| {lab} | {g['n_verdicts']} | {g['n_streams']} | {_d_range(g['abs_D_s'])} | "
            f"{_num(g['abs_error_s_median'])} | {g['label_stable']['True']} / {g['label_stable']['False']} |")


def exploratory_md(ex: dict, meta: dict) -> str:
    G = (("within_30", "within 30 s (hit)"), ("offset_30_600", "30 to 600 s from T*"),
         ("beyond_600", "beyond 600 s"))
    L = ["# EXPLORATORY (post hoc): confident verdicts 30 to 600 s from T*", ""]
    L.append("**This analysis was not pre-registered.** It was written after the Test results were known, to "
             "describe the confident verdicts that are more than 30 s but at most 600 s from T*. It is description "
             "only: counts and medians, no significance test, no new threshold, and no change to any result. A "
             "verdict more than 30 s from T* stays a confident wrong verdict.")
    L.append("")
    L.append(f"Written by `scripts/make_figures.py` from `{meta['csv']}` (scoring run of {meta['written_ist']}) and "
             f"`{meta['splits']}` (`ref_src_file`). Extractor {ex['extractor']}, full span, excerpts with verdict "
             "\"match\". One verdict is one Test stream at one excerpt length, so a stream can appear at up to "
             f"{len(LENGTHS)} lengths. Figure: `{meta['figure']}`.")
    L.append("")
    L.append(f"## Primary cell ({ex['extractor']}, {LENGTHS[-1]}-min excerpts, full span), one verdict per stream")
    L.append("")
    L.append("| group | verdicts | streams | median abs(D) in s (range) | median abs(e) in s | label-stable / not |")
    L.append("|---|---|---|---|---|---|")
    for g, lab in G:
        L.append(_grp_row(lab, ex["primary_cell"][g]))
    L.append("")
    for field, head, fmt in (("ref_src_file", "Reference source file", file_label),
                             ("venue_type", "Venue type", str)):
        cats = ex["categories"][field]
        L.append(f"{head}, {LENGTHS[-1]}-min excerpts (verdicts):")
        L.append("")
        L.append("| group | " + " | ".join(fmt(c) for c in cats) + " |")
        L.append("|---|" + "---|" * len(cats))
        for g, lab in G:
            L.append(f"| {lab} | " + " | ".join(str(ex["primary_cell"][g][field][c]) for c in cats) + " |")
        L.append("")
    L.append("## By excerpt length (full span)")
    L.append("")
    L.append("| excerpt | group | verdicts | streams | median abs(D) in s (range) | median abs(e) in s | label-stable / not |")
    L.append("|---|---|---|---|---|---|---|")
    for m in LENGTHS:
        for g, lab in G:
            L.append(f"| {m} min " + _grp_row(lab, ex["by_length"][str(m)][g]))
    L.append("")
    L.append("tau differs by cell (PROTOCOL.md section 10), so the number of confident verdicts at each length "
             "reflects the threshold of that cell as well as the excerpt.")
    L.append("")
    L.append("## Distinct streams (full span, any excerpt length)")
    L.append("")
    L.append("| streams | n | median abs(D) in s (range) | label-stable / not |")
    L.append("|---|---|---|---|")
    for k, lab in (("at_least_one_offset_30_600", "at least one confident verdict 30 to 600 s from T*"),
                   ("every_confident_verdict_within_30", "every confident verdict within 30 s")):
        g = ex["distinct_streams"][k]
        L.append(f"| {lab} | {g['n_streams']} | {_d_range(g['abs_D_s'])} | "
                 f"{g['label_stable']['True']} / {g['label_stable']['False']} |")
    L.append("")
    L.append("## The streams with a confident verdict 30 to 600 s from T*")
    L.append("")
    L.append("Signed error e of the top-1 candidate in seconds at each excerpt length (full span). `match` marks a "
             "confident verdict; a value without it had no verdict at that length; `none` means the stream has no "
             "excerpt of that length.")
    L.append("")
    L.append("| stream | venue | file | D (s) | label-stable | " + " | ".join(f"{m} min" for m in LENGTHS) + " |")
    L.append("|---|---|---|---|---|" + "---|" * len(LENGTHS))
    for s in ex["offset_streams"]:
        cells = []
        for m in LENGTHS:
            v = s["by_length"][str(m)]
            if v is None:
                cells.append("none")
            elif v["error_s"] is None:
                cells.append("no candidate")
            else:
                cells.append(f"{v['error_s']:+d}" + (" match" if v["verdict"] == "match" else ""))
        L.append(f"| {s['video_id']} | {s['venue_type']} | {file_label(s['ref_src_file'])} | "
                 f"{s['label_drift_s']:g} | {'yes' if s['label_stable'] else 'no'} | " + " | ".join(cells) + " |")
    L.append("")
    L.append(f"{ex['offset_streams_with_a_hit_at_another_length']} of these {len(ex['offset_streams'])} streams have a "
             "top-1 candidate within 30 s of T* at another excerpt length.")
    L.append("")
    L.append("## Limits")
    L.append("")
    L.append("- The groups are small, and verdicts of one stream at several lengths are not independent.")
    L.append("- T* rests on YouTube's release time, one latency L and the drift estimate D. An offset of tens of "
             "seconds can come from any of them or from the lock, and this table cannot tell which.")
    L.append("- These numbers describe the pre-registered results. They do not support any claim about a rule that "
             "was not pre-registered.")
    return "\n".join(L) + "\n"


def figures_readme(fj: dict) -> str:
    L = ["# Figures", ""]
    L.append(f"Written by `scripts/make_figures.py` from the files named below (scoring run of {fj['written_ist']}, "
             f"`{fj['inputs']['evaluation']['path']}`). Every number drawn is in `{fj['self']}`. PNGs are "
             f"{int(FIG_W * DPI)} px wide on a white background. No audio or video is used or shown.")
    L.append("")
    L.append("Colours: amber is Gunj's primary extractor (v2); green marks top-1 candidates within 30 s of T* "
             "(hits) and nothing else; grey marks baselines and reference data. In the breakdown figure, darker "
             "steps of amber mark larger distances from T*. T* = release time + L + D (PROTOCOL.md section 6).")
    L.append("")
    for f in fj["figures"]:
        L.append(f"## `{f['file']}`")
        L.append("")
        L.append(f["caption"])
        L.append("")
        L.append(f"Source: {f['source']}.")
        L.append("")
    r = fj["m3_primary_cell"]
    L.append(f"## M3 reliability table, primary cell ({PRIMARY}, {LENGTHS[-1]} min, full span)")
    L.append("")
    L.append("| confidence bin | Test excerpts | top-1 within 30 s | hit rate (Wilson 95%) |")
    L.append("|---|---|---|---|")
    for b in r["test"]["bins"]:
        L.append(f"| {b['bin']} | {b['n']} | {b['k']} | {fmt_rate(b)} |")
    L.append("")
    L.append(f"Test excerpts without a confidence (the search could not run): {r['test']['n_without_confidence']} of "
             f"{r['test']['n_excerpts']}. tau of the cell: {r['negatives']['tau']:.4f}.")
    L.append("")
    L.append("| report-half negatives in this cell | n | largest confidence | at or above tau | at or above tau with the hum flag |")
    L.append("|---|---|---|---|---|")
    for kind, lab in (("real", "real foreign 50 Hz streams"), ("SYNTHETIC", "SYNTHETIC noise and hum")):
        d = r["negatives"][kind]
        mx = "n/a" if not d["confidence"] else f"{max(d['confidence']):.4f}"
        L.append(f"| {lab} | {d['n']} | {mx} | {d['n_at_or_above_tau']} | {d['n_at_or_above_tau_with_hum_flag']} |")
    L.append("")
    L.append(f"Report-half negatives searched by `scripts/evaluate.py`: {fj['m3_counts']['real']} real and "
             f"{fj['m3_counts']['SYNTHETIC']} SYNTHETIC"
             + (f"; {fj['m3_counts']['harvested_not_yet_searched']} harvested negatives were still being searched "
                "when this was written, so the plot is not final" if fj["m3_counts"].get("harvested_not_yet_searched")
                else "") + f" (`{fj['inputs']['evaluation']['path']}`, `m3_counts`). A negative has a row in a cell "
             "only if its stream is long enough for that excerpt length.")
    L.append("")
    L.append(f"## M3 reliability table, every cell of {PRIMARY}")
    L.append("")
    L.append("Each bin shows hits / Test excerpts. The last two columns give the report-half negatives of the cell "
             "as n (number at or above tau). The false-match rates with their intervals are in "
             "`results/false_matches.md`, written by `scripts/evaluate.py`.")
    L.append("")
    nb = len(CONF_BINS) - 1
    L.append("| excerpt | scope | tau | " + " | ".join(bin_label(i) for i in range(nb))
             + " | no confidence | real negatives | SYNTHETIC negatives |")
    L.append("|---|---|---|" + "---|" * nb + "---|---|---|")
    for m in LENGTHS:
        for s in SCOPES:
            c = fj["m3_all_cells"][f"{m}/{s}"]
            t, n = c["test"], c["negatives"]
            tau = "n/a" if n.get("tau") is None else f"{n['tau']:.4f}"
            L.append(f"| {m} min | {SCOPE_LABEL[s]} | {tau} | " + " | ".join(f"{b['k']}/{b['n']}" for b in t["bins"])
                     + f" | {t['n_without_confidence']} | {n['real']['n']} ({n['real']['n_at_or_above_tau']}) | "
                     f"{n['SYNTHETIC']['n']} ({n['SYNTHETIC']['n_at_or_above_tau']}) |")
    return "\n".join(L) + "\n"


# -------------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Figures and secondary tables of PROTOCOL section 11")
    ap.add_argument("--repo", default=str(REPO), help="repo root (default: the folder above scripts/)")
    ap.add_argument("--ref", default=None, help="folder with the built reference arrays "
                                                "(default: gunj.reference's default, ..\\data\\derived)")
    ap.add_argument("--skip-m4-measure", action="store_true",
                    help="do not load the reference; reuse results/m4_measured.json if it exists")
    return ap


def _inputs(repo: Path, names: dict) -> dict:
    return {k: {"path": rel(p, repo), "sha256": sha256_file(p)} for k, p in names.items() if Path(p).exists()}


def run_m4(rows: list, repo: Path, ref_dir, skip: bool, log=print):
    """Measure the noise (needs the reference) or reuse results/m4_measured.json. Returns (dict or None, note)."""
    res = repo / "results"
    out_path = res / "m4_measured.json"
    ref = None
    why = "--skip-m4-measure was given"
    if not skip:
        try:
            from gunj import reference as gref
            ref = gref.load(ref_dir, mmap=True)          # np.load(..., mmap_mode="r")
        except (OSError, ValueError, KeyError) as e:
            why = "the built reference series could not be loaded"
            log(f"M4: {why} ({type(e).__name__}); not measured in this run")
    if ref is None:
        if out_path.exists():
            return read_json(out_path), "reused"
        return None, f"not computed: {why} and results/m4_measured.json does not exist"
    table = read_csv(res / "uniqueness_table.csv")
    frozen = read_json(res / "gate_frozen.json")
    locked = sorted(frozen["gate"]["by_extractor"][PRIMARY]["locked_streams"])
    excerpts = measure_noise(rows, ref, repo / "traces", res / "search_cache")
    recomputed = sorted({e["video_id"] for e in excerpts if e["gate_lock"]})
    lock_ex = [e for e in excerpts if e["gate_lock"] and e["video_id"] in set(locked)]
    sets = {"gate_locks": lock_ex,
            "confident_hits": [e for e in excerpts if e["confident_hit"]],
            "all_top1_hits": excerpts,
            "primary_cell_locks": [e for e in excerpts if e["excerpt_min"] == LENGTHS[-1]
                                   and "full" in e["lock_scopes"]]}
    keys = {"A": "sigma_A_hz", "B": "sigma_B_hz", "A_weighted_std": "sigma_A_weighted_std_hz"}
    two = (("A", "sigma_A_hz"), ("B", "sigma_B_hz"))
    ps = {k: per_stream(lock_ex, col) for k, col in keys.items()}
    dist = {k: quartiles(ps[k].values()) for k in keys}
    readings = {}
    for key in ("A", "B"):
        readings[key] = {}
        for q in ("median", "q1", "q3"):
            lvl = None if dist[key] is None or dist[key][q] is None else 1000 * dist[key][q]
            readings[key][q] = {kind: {s: read_uniqueness(table, kind, s, lvl) for s in SCOPES}
                                for kind in ("hann16", "white")}
    probe_path = res / "probe_noise.json"
    probe = read_json(probe_path)["summary"] if probe_path.exists() else None
    m4 = {
        "what": "Equivalent trace noise of the Test streams that lock (PROTOCOL.md section 11, M4), computed as in "
                "scripts/probe_noise.py, and the uniqueness table (SYNTHETIC noise on real reference excerpts) read "
                "at that level by linear interpolation between the pre-set levels.",
        "script": "scripts/make_figures.py",
        "model": "sigma = s * sqrt(1/r^2 - 1); s = std of the gap-filled reference over the excerpt's matched "
                 "window [lock_index, lock_index + n_frames)",
        "reading_A": "r = r1 recorded by the frozen evaluation (results/search_cache/test; v2 weighted correlation)",
        "reading_B": "r = unweighted Pearson correlation of the same v2 trace with the same reference window",
        "per_stream_rule": "median over the stream's locking excerpts (one excerpt per length; a lock in the full "
                           "or +-90-day scope)",
        "inputs": {"test_results": "results/test_results.csv", "gate_frozen": "results/gate_frozen.json",
                   "uniqueness_table": "results/uniqueness_table.csv", "probe_noise": "results/probe_noise.json",
                   "search_cache": "results/search_cache/test", "traces": "traces/test",
                   "reference": {"t0_ist": str(ref.t0), "length": int(len(ref))},
                   "sha256": {k: v["sha256"] for k, v in _inputs(repo, {
                       "results/test_results.csv": res / "test_results.csv",
                       "results/gate_frozen.json": res / "gate_frozen.json",
                       "results/uniqueness_table.csv": res / "uniqueness_table.csv"}).items()}},
        "streams_that_lock": {"definition": "results/gate_frozen.json, gate.by_extractor.v2.locked_streams "
                                            "(PROTOCOL.md section 15, conditions 1 and 2)",
                              "n": len(locked), "video_ids": locked,
                              "matches_recomputed_list": recomputed == locked,
                              "n_excerpts": len(lock_ex)},
        "per_stream_sigma_hz": {k: {vid: r6(v) for vid, v in ps[k].items()} for k in keys},
        "distribution_hz": {k: round_stats(d) for k, d in dist.items()},
        "by_length_hz": {str(m): {k: round_stats(quartiles([e.get(col) for e in lock_ex if e["excerpt_min"] == m]))
                                  for k, col in two} for m in LENGTHS},
        "alternatives_hz": {name: {k: round_stats(quartiles(per_stream(ex, col).values())) for k, col in two}
                            for name, ex in sets.items()},
        "undefined_B": sum(1 for e in lock_ex if e.get("sigma_B_hz") is None),
        "uniqueness_levels_mhz": table_levels(table, "hann16"),
        "uniqueness_lengths_min": sorted({int(float(r["length_min"])) for r in table}),
        "readings": readings,
        "probe": probe,
        "excerpts": excerpts,
        "figure": "results/figures/m4_measured_noise.png",
    }
    return m4, "measured"


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(args.repo).resolve()
    res, figs = repo / "results", repo / "results" / "figures"
    try:
        for need in ("test_results.csv", "evaluation.json", "false_matches.json"):
            if not (res / need).exists():
                raise FigureError(f"results/{need} is missing; run scripts/evaluate.py first")
        ev = read_json(res / "evaluation.json")
        if ev.get("mode") != "real":
            raise FigureError("results/evaluation.json is not from a real (Test) run")
        all_rows = read_csv(res / "test_results.csv")
        rows = searched_rows(all_rows)
        fm = read_json(res / "false_matches.json")
        if fm.get("written_ist") != ev.get("written_ist"):
            raise FigureError("results/false_matches.json and results/evaluation.json are not from the same "
                              "scoring run (a run may be writing them now); run this script again")
        splits = read_csv(repo / "data" / "splits.csv")
        meta = {"csv": "results/test_results.csv", "evaluation": "results/evaluation.json",
                "splits": "data/splits.csv", "written_ist": ev.get("written_ist")}
        figures = []

        # 3. breakdown (first: it also checks the CSV against evaluation.json)
        bd = breakdown(rows)
        meta["cells_checked"] = check_against_evaluation(bd, ev)
        meta["column_mismatches"] = column_mismatches(rows)
        if meta["column_mismatches"]:
            raise FigureError(f"{meta['column_mismatches']} rows of results/test_results.csv have hit columns "
                              "that disagree with error_s")
        meta["figure"] = "results/figures/confident_verdict_breakdown_v2.png"
        fig_breakdown(bd[PRIMARY], PRIMARY, figs / "confident_verdict_breakdown_v2.png")
        write_json(res / "error_breakdown.json", {
            "what": "Excerpts with verdict match, and all excerpts, split by the distance of the top-1 candidate "
                    "from T*, from the pre-registered columns hit_30s, hit_120s and hit_600s (PROTOCOL.md section 9).",
            "script": "scripts/make_figures.py", "scoring_run_written_ist": ev.get("written_ist"),
            "inputs": _inputs(repo, {"test_results": res / "test_results.csv", "evaluation": res / "evaluation.json"}),
            "classes": {"within_30": "abs(e) <= 30 s (hit)", "from_30_to_120": "30 s < abs(e) <= 120 s",
                        "from_120_to_600": "120 s < abs(e) <= 600 s", "beyond_600": "abs(e) > 600 s",
                        "no_candidate": "the search could not run; no top-1 candidate"},
            "shares": "cumulative: within 30 s, within 120 s, within 600 s; Wilson 95% (z = 1.959964)",
            "checks": {"cells_equal_to_evaluation_m2": meta["cells_checked"],
                       "rows_with_inconsistent_hit_columns": meta["column_mismatches"]},
            "figure": meta["figure"], "cells": bd}, repo)
        write_text(res / "error_breakdown.md", breakdown_md(bd, meta), repo)
        p = bd[PRIMARY][str(LENGTHS[-1])]["full"]["confident"]
        figures.append({"file": "confident_verdict_breakdown_v2.png",
                        "caption": f"Top: excerpts with verdict \"match\" for every cell of extractor {PRIMARY}, split "
                                   "by the distance of the top-1 candidate from T*. Bottom: the same split for every "
                                   "excerpt, whatever the verdict. Only \"within 30 s\" is a hit. In the primary cell "
                                   f"({LENGTHS[-1]} min, full span) {p['n']} excerpts have a verdict \"match\": "
                                   f"{p['within_30']} within 30 s, {p['from_30_to_120']} from 30 to 120 s, "
                                   f"{p['from_120_to_600']} from 120 to 600 s and {p['beyond_600']} beyond 600 s.",
                        "source": "`results/test_results.csv` (columns `verdict`, `hit_30s`, `hit_120s`, `hit_600s`); "
                                  "table in `results/error_breakdown.md`"})

        # 1. M2 signed error
        m2_data = {}
        for ext in EXTRACTORS:
            cells = m2_cells(rows, ext)
            m2_data[ext] = fig_signed_error(cells, ext, figs / f"m2_signed_error_{ext}.png")
            c = cells[(LENGTHS[-1], "full")]
            figures.append({"file": f"m2_signed_error_{ext}.png",
                            "caption": f"M2, extractor {ext}" + (" (primary)" if ext == PRIMARY else " (baseline)")
                                       + ": signed error e of the top-1 candidate for every excerpt length (rows) and "
                                       "search scope (columns). The axis is linear inside the ±30 s hit band and "
                                       "logarithmic outside it, and every bin has the same drawn width. Green bars "
                                       "are hits. Each panel title gives N (Test streams with a trace at that "
                                       f"length) and the number of hits; {LENGTHS[-1]} min, full span: N = {c['n']}, "
                                       f"{c['hits']} hits.",
                            "source": "`results/test_results.csv` (columns `error_s`, `hit_30s`)"})

        # 2. M3 reliability
        counts = fm.get("counts") or ev.get("m3_counts") or {}
        rels = {(m, s): reliability(cell_rows(rows, PRIMARY, m, s)) for m in LENGTHS for s in SCOPES}
        negs = {(m, s): negatives_of(fm, PRIMARY, m, s) for m in LENGTHS for s in SCOPES}
        pk = (LENGTHS[-1], "full")
        sub = (f"Extractor {PRIMARY} (primary), {LENGTHS[-1]}-min excerpts, full span. N = {rels[pk]['n_excerpts']} "
               f"Test excerpts ({rels[pk]['n_without_confidence']} without a confidence, not drawn). Report-half "
               f"negatives in this cell: {negs[pk]['real']['n']} real ({negs[pk]['real']['n_at_or_above_tau']} at or "
               f"above τ) and {negs[pk]['SYNTHETIC']['n']} SYNTHETIC ({negs[pk]['SYNTHETIC']['n_at_or_above_tau']} at "
               "or above τ).")
        fig_reliability(rels[pk], negs[pk], sub, figs / "m3_reliability_v2_60min_full.png")
        fig_reliability_grid(rels, negs, PRIMARY, {"real": counts.get("real", 0), "SYNTHETIC": counts.get("SYNTHETIC", 0)},
                             figs / "m3_reliability_v2_all_cells.png")
        pending = counts.get("harvested_not_yet_searched") or 0
        figures.append({"file": "m3_reliability_v2_60min_full.png",
                        "caption": "M3 reliability plot for the primary cell, as PROTOCOL.md section 11 defines it: "
                                   "Test top-1 hit rate by confidence bin, with counts (hits / excerpts) and Wilson "
                                   "95% intervals, and below it every excerpt's confidence on the same axis: Test "
                                   "hits, Test misses, the real report-half negatives (filled grey) and the "
                                   "SYNTHETIC report-half negatives (open grey). The dashed line is tau of the cell. "
                                   f"Negatives drawn: {negs[pk]['real']['n']} real and {negs[pk]['SYNTHETIC']['n']} "
                                   "SYNTHETIC."
                                   + (f" {pending} harvested negatives were still being searched when this was drawn."
                                      if pending else ""),
                        "source": "`results/test_results.csv` (`confidence`, `hit_30s`) and "
                                  "`results/false_matches.json` (report-half negatives, `tau`)"})
        figures.append({"file": "m3_reliability_v2_all_cells.png",
                        "caption": f"The same reliability plot for all {len(LENGTHS) * len(SCOPES)} cells of extractor "
                                   f"{PRIMARY} ({len(LENGTHS)} excerpt lengths by {len(SCOPES)} scopes). The two "
                                   "rows of dots under each panel are the real (filled) and SYNTHETIC (open) "
                                   "report-half negatives of that cell.",
                        "source": "`results/test_results.csv` and `results/false_matches.json`"})

        # 5. EXPLORATORY
        ex = exploratory(rows, splits)
        ex_meta = dict(meta, figure="results/figures/exploratory_offset_verdicts_v2.png")
        fig_exploratory(ex, figs / "exploratory_offset_verdicts_v2.png")
        write_json(res / "exploratory_offset_verdicts.json", {
            "what": "EXPLORATORY (post hoc, not pre-registered). Description of confident verdicts that are 30 to "
                    "600 s from T*, beside those within 30 s. Counts and medians only.",
            "script": "scripts/make_figures.py", "scoring_run_written_ist": ev.get("written_ist"),
            "inputs": _inputs(repo, {"test_results": res / "test_results.csv", "splits": repo / "data" / "splits.csv"}),
            "figure": ex_meta["figure"], **ex}, repo)
        write_text(res / "exploratory_offset_verdicts.md", exploratory_md(ex, ex_meta), repo)
        figures.append({"file": "exploratory_offset_verdicts_v2.png",
                        "caption": "EXPLORATORY (post hoc, not pre-registered). Confident verdicts of extractor "
                                   f"{PRIMARY} in the full span: those within 30 s of T* (green) beside those 30 to "
                                   "600 s from it (amber), by the stream's drift correction abs(D), label stability, "
                                   "reference source file, excerpt length and venue type. Counts and medians only.",
                        "source": "`results/test_results.csv` and `data/splits.csv` (`ref_src_file`); tables in "
                                  "`results/exploratory_offset_verdicts.md`"})

        # 4. M4 at the measured noise level
        m4, note = run_m4(rows, repo, args.ref, args.skip_m4_measure)
        if m4 is not None:
            if note == "measured":
                write_json(res / "m4_measured.json", m4, repo)
            write_text(res / "m4_measured.md", m4_md(m4), repo)
            fig_m4(m4, figs / "m4_measured_noise.png")
            a, b = m4["distribution_hz"]["A"], m4["distribution_hz"]["B"]
            figures.append({"file": "m4_measured_noise.png",
                            "caption": "M4 at the measured noise level. Top: equivalent trace noise of the "
                                       f"{m4['streams_that_lock']['n']} Test streams that lock, one dot per stream, "
                                       f"under reading A (r as scored, median {_mhz(a['median'])} mHz) and reading B "
                                       f"(unweighted r, median {_mhz(b['median'])} mHz), against the pre-set levels "
                                       "of the M4 simulation. Bottom: the uniqueness table (real reference excerpts "
                                       "with SYNTHETIC hann16 noise) read at each median by linear interpolation.",
                            "source": "`results/m4_measured.json`, from `results/test_results.csv`, "
                                      "`results/gate_frozen.json`, `results/search_cache/test`, `traces/test`, the "
                                      "built reference and `results/uniqueness_table.csv`"})
        order = ["m2_signed_error_v2.png", "m3_reliability_v2_60min_full.png", "confident_verdict_breakdown_v2.png",
                 "m4_measured_noise.png", "m3_reliability_v2_all_cells.png", "m2_signed_error_v1.png",
                 "m2_signed_error_naive.png", "exploratory_offset_verdicts_v2.png"]
        figures.sort(key=lambda f: order.index(f["file"]) if f["file"] in order else len(order))
        fj = {"what": "The numbers drawn in results/figures/*.png", "script": "scripts/make_figures.py",
              "self": "results/figures/figures.json", "written_ist": ev.get("written_ist"),
              "inputs": _inputs(repo, {"test_results": res / "test_results.csv", "evaluation": res / "evaluation.json",
                                       "false_matches": res / "false_matches.json",
                                       "splits": repo / "data" / "splits.csv"}),
              "m3_counts": counts, "m4_status": note, "figures": figures,
              "m2_signed_error": m2_data,
              "m3_primary_cell": {"test": rels[pk], "negatives": negs[pk]},
              "m3_all_cells": {f"{m}/{s}": {"test": {k: v for k, v in rels[(m, s)].items()
                                                     if not k.startswith("confidence_")},
                                            "negatives": negs[(m, s)]} for m in LENGTHS for s in SCOPES}}
        write_json(figs / "figures.json", fj, repo)
        write_text(figs / "README.md", figures_readme(fj), repo)
    except FigureError as e:
        print(f"make_figures: {e}", file=sys.stderr)
        return 2
    pc = bd[PRIMARY][str(LENGTHS[-1])]["full"]["confident"]
    print(f"primary cell: {pc['n']} verdicts match; {pc['within_30']} within 30 s, {pc['from_30_to_120']} from 30 to "
          f"120 s, {pc['from_120_to_600']} from 120 to 600 s, {pc['beyond_600']} beyond 600 s")
    print(f"report-half negatives searched: {counts.get('real')} real, {counts.get('SYNTHETIC')} SYNTHETIC"
          + (f" ({pending} still being searched)" if pending else ""))
    if m4 is not None:
        print(f"M4 ({note}): reading A median {_mhz(m4['distribution_hz']['A']['median'])} mHz, reading B median "
              f"{_mhz(m4['distribution_hz']['B']['median'])} mHz over {m4['streams_that_lock']['n']} streams")
    else:
        print(f"M4: {note}")
    print(f"wrote {len(figures)} figures to {rel(figs, repo)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
