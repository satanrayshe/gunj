"""SYNTHETIC checks of the v1 extractor, the naive baseline and the edit helper.

Everything here is SYNTHETIC: random-walk "grid" traces, generated hum with
white noise, and edits made by deleting samples. Nothing is real audio or
real grid data, and none of it measures performance on real recordings.

Usage (from the repo root):
  .venv\\Scripts\\python.exe scripts\\synthetic_checks.py --out results\\synthetic_checks.json
"""
from __future__ import annotations

import gunj  # noqa: F401  (first, so its OPENBLAS_NUM_THREADS default applies)
import argparse
import json
import time
from pathlib import Path

import numpy as np

from gunj.extract import extract_enf, extract_enf_naive
from gunj.match import find_edits, ncc_search
from gunj.synthetic import cut_audio, hann_truth, synthetic_enf_walk, synthetic_hum

LEVELS = {
    "weak": {1: 0.02, 2: 0.06, 3: 0.015},
    "medium": {1: 0.05, 2: 0.2, 3: 0.05},
    "strong": {1: 0.1, 2: 0.3, 3: 0.1},
}


def rms(a, b):
    return float(np.sqrt(np.mean((np.asarray(a) - np.asarray(b)) ** 2)))


def extraction(seeds, seconds):
    rows = []
    for level, amps in LEVELS.items():
        for seed in seeds:
            f = synthetic_enf_walk(seconds + 1, seed=100 + seed)
            audio = synthetic_hum(f, sr=1000, amps=amps, seed=200 + seed)
            v1 = extract_enf(audio, 1000)
            nv = extract_enf_naive(audio, 1000)
            truth = hann_truth(f, 1000, len(v1))
            k2 = v1.harmonic_freq[list(v1.harmonics).index(2)]
            rows.append({
                "level": level, "seed": seed,
                "rms_v1_hz": round(rms(v1.freq, truth), 6),
                "rms_k2_only_hz": round(rms(k2, truth), 6),
                "rms_naive_hz": round(rms(nv.freq, truth), 6),
                "truth_std_hz": round(float(np.std(truth)), 6),
                "hum_present": v1.hum_present,
                "median_peak_to_floor": {k: round(v, 3) for k, v in v1.harmonic_median_snr.items()},
                "weights": {k: round(v, 3) for k, v in v1.weights.items()},
            })
    summary = {}
    for level in LEVELS:
        sub = [r for r in rows if r["level"] == level]
        summary[level] = {key: round(float(np.median([r[key] for r in sub])), 6)
                          for key in ("rms_v1_hz", "rms_k2_only_hz", "rms_naive_hz")}
        summary[level]["hum_present_share"] = float(np.mean([r["hum_present"] for r in sub]))
    return {"rows": rows, "median_by_level": summary}


def noise_only(seeds, seconds):
    rows = []
    for seed in seeds:
        x = np.random.default_rng(300 + seed).normal(size=seconds * 1000).astype(np.float32)
        tr = extract_enf(x, 1000)
        rows.append({"seed": seed, "hum_present": tr.hum_present,
                     "median_peak_to_floor": {k: round(v, 3) for k, v in tr.harmonic_median_snr.items()},
                     "weights": {k: round(v, 3) for k, v in tr.weights.items()}})
    meds = np.array([[r["median_peak_to_floor"][k] for k in range(1, 7)] for r in rows])
    return {"rows": rows,
            "max_median_peak_to_floor_by_harmonic": {k: float(meds[:, k - 1].max()) for k in range(1, 7)},
            "hum_present_share": float(np.mean([r["hum_present"] for r in rows]))}


def edits(levels=("weak", "medium", "strong"), cut_at=(400, 600, 800), cut_len=(10, 20, 40, 80, 120)):
    ref = synthetic_enf_walk(200_000, seed=11, dtype=np.float32)
    valid = np.ones(ref.size, dtype=np.uint8)
    l0 = 70_000
    seg = ref[l0:l0 + 1301].astype(np.float64)
    rows = []
    for level in levels:
        audio = synthetic_hum(seg, sr=1000, amps=LEVELS[level], seed=21)
        clean = extract_enf(audio, 1000)
        res = ncc_search(clean.freq, ref, valid)
        out = find_edits(clean.freq, ref, valid, res.top.index)
        rows.append({"level": level, "cut_at_s": None, "cut_len_s": 0,
                     "lock_offset_s": int(res.top.index - l0 - 8),
                     "edits_found": len(out["edits"]), "false_alarm": len(out["edits"]) > 0})
        for at in cut_at:
            for ln in cut_len:
                tr = extract_enf(cut_audio(audio, 1000, at, ln), 1000)
                res = ncc_search(tr.freq, ref, valid)
                out = find_edits(tr.freq, ref, valid, res.top.index)
                e = out["edits"]
                hit = (len(e) == 1 and abs(e[0]["jump_s"] - ln) <= 2
                       and abs(e[0]["clip_time_s"] - at) <= 10)
                rows.append({
                    "level": level, "cut_at_s": at, "cut_len_s": ln,
                    "lock_offset_s": int(res.top.index - l0 - 8),
                    "edits_found": len(e), "detected": bool(hit),
                    "jump_s": e[0]["jump_s"] if e else None,
                    "clip_time_s": e[0]["clip_time_s"] if e else None,
                })
    cuts = [r for r in rows if r["cut_len_s"]]
    by_level = {lv: {"detected": int(sum(r["detected"] for r in cuts if r["level"] == lv)),
                     "cuts": int(sum(r["level"] == lv for r in cuts)),
                     "whole_trace_lock_off_by_more_than_cut": int(sum(
                         abs(r["lock_offset_s"]) > r["cut_len_s"] for r in cuts if r["level"] == lv))}
                for lv in levels}
    return {"clip_seconds": 1300, "rows": rows, "by_level": by_level,
            "detected": int(sum(r["detected"] for r in cuts)), "cuts": len(cuts),
            "false_alarms_on_clean": int(sum(r.get("false_alarm", False) for r in rows)),
            "clean_clips": len(rows) - len(cuts),
            "hit_rule": "exactly one edit, |jump - true| <= 2 s, |time - true| <= 10 s"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    t = time.time()
    out = {
        "label": "SYNTHETIC: generated signals only; not a result on real recordings",
        "extraction_600s": extraction(range(5), 600),
        "white_noise_300s": noise_only(range(5), 300),
        "edits": edits(),
    }
    out["runtime_s"] = round(time.time() - t, 1)
    tmp = Path(args.out + ".tmp")
    tmp.write_text(json.dumps(out, indent=1), encoding="utf-8")
    tmp.replace(args.out)          # never leave a half-written result
    print(json.dumps({k: out[k] for k in ("runtime_s",)}),
          json.dumps(out["extraction_600s"]["median_by_level"]),
          json.dumps(out["white_noise_300s"]["max_median_peak_to_floor_by_harmonic"]),
          json.dumps({k: out["edits"][k] for k in ("detected", "cuts", "false_alarms_on_clean", "clean_clips", "by_level")}),
          sep="\n")


if __name__ == "__main__":
    main()
