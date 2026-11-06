"""Check each reference file's time labels against the grid's own 15-minute clock.

Indian grid schedules change at 15-minute block boundaries (00:00, 00:15, ...
IST), and frequency tends to move faster right after a boundary. If a file's
time labels are right, the average |change per second| folded on (time mod 900 s)
peaks at the same phase every day. If labels drift or are offset, the peak
phase moves.

Reads the per-file arrays written by build_reference.py --stage parse
(<derived>/_parsed). Writes results/time_axis_check.json and .png.
"""
from __future__ import annotations

import os

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
FILES = ["freq_I.csv", "freq_II.csv", "freq_III.csv", "freq_IV.csv", "freq_V.csv"]
P = 900  # seconds per scheduling block


def load_parsed(derived: Path, name: str):
    stem = Path(name).stem
    st = json.loads((derived / "_parsed" / f"{stem}.parse.json").read_text(encoding="utf-8"))
    n = st["rows_kept"]
    sec = np.memmap(derived / "_parsed" / f"{stem}.sec.i64", dtype=np.int64, mode="r", shape=(n,))
    val = np.memmap(derived / "_parsed" / f"{stem}.val.f64", dtype=np.float64, mode="r", shape=(n,))
    return st, sec, val


def daily_folds(sec, val):
    """Per IST day: sums for mean |f[t]-f[t-1]| folded on t mod 900 (t = label of the later
    second). Rows must be sorted by time; missing rows count as missing seconds."""
    s0, s1 = int(sec[0]), int(sec[-1])
    first_day, last_day = s0 // 86400, s1 // 86400
    out = {}
    for d in range(first_day, last_day + 1):
        lo, hi = d * 86400 - 1, (d + 1) * 86400          # include the last second of the day before
        i0, i1 = np.searchsorted(sec, [lo, hi])
        if i1 - i0 < 3600:
            continue
        s = np.asarray(sec[i0:i1])
        if np.any(np.diff(s) <= 0):
            raise ValueError("rows are not sorted by time")
        v = np.full(hi - lo, np.nan)
        v[s - lo] = np.asarray(val[i0:i1])
        dv = np.abs(np.diff(v))
        ok = np.isfinite(dv)
        if ok.sum() < 43200:  # need half a day of valid steps
            continue
        lab = (lo + 1 + np.arange(len(dv))) % P
        num = np.bincount(lab[ok], weights=dv[ok], minlength=P)
        den = np.bincount(lab[ok], minlength=P)
        out[d] = (num, den)
    return out


def circ_shift(prof: np.ndarray, tmpl: np.ndarray) -> tuple[int, float]:
    """Shift k (s) maximising corr(prof[t], tmpl[t - k]), in (-450, 450]."""
    a = prof - prof.mean()
    b = tmpl - tmpl.mean()
    c = np.fft.irfft(np.fft.rfft(a) * np.conj(np.fft.rfft(b)), P)
    k = int(np.argmax(c))
    r = float(c[k] / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-30))
    if k > P // 2:
        k -= P
    return k, r


def weekly(days: dict, width: int = 7):
    """Pool consecutive daily (num, den) folds into windows of `width` days."""
    keys = sorted(days)
    out = {}
    for j in range(0, len(keys), width):
        ks = [k for k in keys[j:j + width] if k - keys[j] < width]
        num = sum(days[k][0] for k in ks)
        den = sum(days[k][1] for k in ks)
        out[ks[0] + (ks[-1] - ks[0]) / 2] = num / np.maximum(den, 1)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--derived-dir", type=Path, required=True)
    ap.add_argument("--results-dir", type=Path, default=REPO / "results")
    ap.add_argument("--window-days", type=int, default=7)
    args = ap.parse_args(argv)

    folds = {}
    starts = {}
    for name in FILES:
        st, sec, val = load_parsed(args.derived_dir, name)
        folds[name] = daily_folds(sec, val)
        starts[name] = int(sec[0]) / 86400
        del sec, val
    if not all(folds.values()):
        raise SystemExit("not enough full days in every file for the block check")
    prof = {name: weekly(d, args.window_days) for name, d in folds.items()}
    # template: first window of every file (least drift if labels start right), then one refinement
    tmpl = np.mean([prof[n][min(prof[n])] for n in FILES], axis=0)
    for _ in range(2):
        aligned = []
        for n in FILES:
            for d, pr in prof[n].items():
                k, r = circ_shift(pr, tmpl)
                aligned.append(np.roll(pr, -k))
        new = np.mean(aligned, axis=0)
        # keep the template's phase anchored to the first windows
        k0 = int(np.median([circ_shift(prof[n][min(prof[n])], new)[0] for n in FILES]))
        tmpl = np.roll(new, k0)
    rows = []
    for n in FILES:
        for d, pr in sorted(prof[n].items()):
            k, r = circ_shift(pr, tmpl)
            rows.append({"file": n, "window_mid_ist": str(pd.Timestamp(d * 86400, unit="s").date()),
                         "days_since_file_start": round(d - starts[n], 2), "shift_s": k, "r": round(r, 4)})
    df = pd.DataFrame(rows)
    summary = {}
    for n, g in df.groupby("file", sort=False):
        summary[n] = {"windows": int(len(g)), "windows_r_ge_0p5": int((g["r"] >= 0.5).sum()),
                      "median_r": float(g["r"].median()),
                      "first_window_shift_s": int(g["shift_s"].iloc[0]),
                      "last_window_shift_s": int(g["shift_s"].iloc[-1]),
                      "min_shift_s": int(g["shift_s"].min()), "max_shift_s": int(g["shift_s"].max())}
    # cross-check: in each overlap, the difference of the two files' block shifts should equal the
    # label offset measured exactly by build_reference.py (lag_search)
    checks = []
    bs = args.results_dir / "reference_build_stats.json"
    if bs.exists():
        build = json.loads(bs.read_text(encoding="utf-8"))
        for o in build["overlaps"]:
            if "lag_search" not in o:
                continue
            lo, hi = pd.Timestamp(o["window_start_ist"]), pd.Timestamp(o["window_end_ist"])
            mids = pd.to_datetime(df["window_mid_ist"])
            inside = (mids >= lo.normalize()) & (mids <= hi)
            ga, gb = df[inside & (df["file"] == o["a"])], df[inside & (df["file"] == o["b"])]
            if len(ga) and len(gb):
                checks.append({"a": o["a"], "b": o["b"], "label_offset_from_overlap_s": o["lag_search"]["best_lag_s"],
                               "block_shift_a_minus_b_s": float(ga["shift_s"].median() - gb["shift_s"].median()),
                               "windows_a": int(len(ga)), "windows_b": int(len(gb))})
    out = {
        "method": ("For each file and each window of %d days: mean |f(t)-f(t-1)| folded on the label t mod 900 s "
                   "(the 15-minute scheduling block). Circular cross-correlation with a template (mean of aligned "
                   "windows, phase anchored to the first window of each file) gives each window's shift, in "
                   "seconds; r is the profile correlation at that shift. Positive shift = the block features "
                   "sit later on that file's labels. The check is relative: it assumes each file's first window "
                   "is on time." % args.window_days),
        "template_peak_phase_s": int(np.argmax(tmpl)),
        "per_file": summary, "overlap_cross_check": checks, "per_window": rows,
    }
    args.results_dir.mkdir(parents=True, exist_ok=True)
    (args.results_dir / "time_axis_check.json").write_text(json.dumps(out, indent=1), encoding="utf-8")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(11, 3.6), dpi=150)
    fig.patch.set_facecolor("#fcfcfb"); ax.set_facecolor("#fcfcfb")
    colors = ["#2a78d6", "#eb6834", "#1baf7a", "#6250d6", "#e34948"]
    ax.axhline(0, color="#c3c2b7", linewidth=0.8)
    for c, (name, g) in zip(colors, df.groupby("file", sort=False)):
        x = pd.to_datetime(g["window_mid_ist"])
        ax.plot(x, g["shift_s"], "-", lw=0.8, color=c, label=name)
        good = g["r"] >= 0.3
        ax.plot(x[good], g["shift_s"][good], "o", ms=3, color=c)
        ax.plot(x[~good], g["shift_s"][~good], "o", ms=3, mfc="none", mec=c, mew=0.8)
    ax.set_ylabel("shift of 15-min block features (s)", fontsize=8, color="#52514e")
    ax.tick_params(labelsize=7, colors="#52514e")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.grid(axis="y", color="#e6e5e0", linewidth=0.6)
    ax.legend(fontsize=7, frameon=False, ncol=5, loc="upper left")
    ax.set_title("Where the grid's 15-minute schedule changes fall on each file's time labels (7-day windows)",
                 fontsize=9, loc="left", color="#0b0b0b")
    fig.text(0.01, 0.01, "Shift 0 = where the features sit in each file's first week. Open circles: profile "
             "correlation r < 0.3, less reliable. Source data: Mendeley bvwh9d6227, CC BY 4.0.",
             fontsize=6.5, color="#52514e")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(args.results_dir / "time_axis_check.png", facecolor=fig.get_facecolor())
    print(json.dumps({k: out[k] for k in ("template_peak_phase_s", "per_file", "overlap_cross_check")}, indent=1))


if __name__ == "__main__":
    main()
