#!/usr/bin/env python
"""Estimate latency L from the Calibration split only (PROTOCOL.md section 6).

For each Calibration stream (and nothing else; no Dev or Test trace is read):
1. Take the frozen extractor's 60-min excerpt trace (traces/calibration/<id>.npz,
   part x60). The primary extractor is v2 if its traces exist, else v1;
   every extractor present is reported.
2. Search it in the +-1-day scope around the release time in IST.
3. o = (implied recording start on the reference labels) - (release time in
   IST + D), in seconds. The implied start is the top candidate's position
   minus 128 s (frame 0 is centred 128 s into the recording).
4. A stream counts if confidence >= tau(extractor, 60 min, +-1 day) and
   |o| <= 600 s.
5. If at least 3 streams count, L = median of their o, rounded to the
   nearest second; otherwise L = 0 and results are "latency not calibrated".
6. All o values, the IQR and the full range of the counted o are reported.
   If that range exceeds 60 s, a single constant latency does not hold.

tau comes from results/threshold.json (set on the threshold-half negatives,
PROTOCOL section 10). If that file does not exist yet, step 4 cannot be
applied: L stays PENDING and results/latency.json is not written. A
PROVISIONAL figure (median o of streams with |o| <= 600 s, no tau
condition) is reported for information only and must not be used as L.

Writes results/calibration.json and results/calibration.md, and
results/latency.json only when tau is available.
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np                       # noqa: E402
import pandas as pd                      # noqa: E402

from gunj import reference as gref       # noqa: E402
from gunj.match import ncc_search        # noqa: E402
from gunj.match_v2 import wncc_search    # noqa: E402

FRAME0_S = 128
MAX_ABS_O_S = 600
MIN_COUNTED = 3
RANGE_FLAG_S = 60
LENGTH_MIN = 60
EXTRACTORS = ("v2", "v1", "naive")


def now_ist() -> str:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).isoformat(timespec="seconds")


def load_rows(manifest: Path, splits: Path):
    with open(splits, encoding="utf-8", newline="") as f:
        meta = {r["video_id"]: r for r in csv.DictReader(f) if r["split"] == "calibration"}
    with open(manifest, encoding="utf-8", newline="") as f:
        man = {r["video_id"]: r for r in csv.DictReader(f) if r["split"] == "calibration"}
    return meta, man


def load_tau(path: Path):
    if not path.exists():
        return None
    d = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for ext in EXTRACTORS:
        try:
            out[ext] = float(d["cells"][ext][str(LENGTH_MIN)]["1d"]["tau"])
        except (KeyError, TypeError, ValueError):
            pass
    return out or None


SCOPE_DAYS = {"full": None, "90d": 90, "1d": 1}     # PROTOCOL section 8


def scope_positions(ref, rel_ist, scope: str) -> tuple:
    """Search positions [p0, p1) for a scope (PROTOCOL section 8).

    A scope bounds the implied recording start, which is position - 128 s.
    "full" is the whole series; "90d" and "1d" are the release time in IST
    +- 90 days or +- 1 day, clipped to the series.
    """
    days = SCOPE_DAYS[scope]
    i0, i1 = ref.scope_full() if days is None else ref.scope(rel_ist, days=days)
    return i0 + FRAME0_S, min(i1 + FRAME0_S, len(ref))


def run_search(z, ext, ref, release_ts, length_min: int = LENGTH_MIN, scope: str = "1d", k: int = 5):
    """Search one excerpt trace of an open trace file in one scope.

    Returns (SearchResult, n_frames, weighted) or None if the trace file has
    no such excerpt. v2 traces carry frame weights and use the weighted
    matcher (gunj/match_v2.py); v1 and naive use the frozen gunj/match.py.
    """
    key = f"{ext}__x{length_min}"
    if f"{key}__freq" not in z.files:
        return None
    freq = z[f"{key}__freq"]
    fw = z[f"{key}__fw"] if f"{key}__fw" in z.files else None
    p0, p1 = scope_positions(ref, gref.unix_to_ist(release_ts), scope)
    if fw is None:
        res = ncc_search(freq, ref.freq, ref.valid, start=p0, stop=p1, k=k)
    else:
        res = wncc_search(freq, fw.astype(np.float64), ref.freq, ref.valid, start=p0, stop=p1, k=k)
    return res, int(freq.size), fw is not None


def search_one(z, ext, ref, release_ts, D):
    out = run_search(z, ext, ref, release_ts, LENGTH_MIN, "1d")
    if out is None:
        return None
    res, n_frames, weighted = out
    rel_ist = gref.unix_to_ist(release_ts)
    e = ref.index_of(rel_ist + pd.Timedelta(seconds=float(D)), check=False)
    e0 = ref.index_of(rel_ist, check=False)
    top, ru = res.top, res.runner_up
    if top is None:
        return {"extractor": ext, "lock": None}
    start_idx = top.index - FRAME0_S
    return {
        "extractor": ext, "weighted_correlation": weighted, "n_frames": n_frames,
        "lock_start_ist": str(ref.time_of(start_idx)), "lock_index": int(top.index),
        "r1": round(top.r, 5), "r2": None if ru is None else round(ru.r, 5),
        "runner_up_start_ist": None if ru is None else str(ref.time_of(ru.index - FRAME0_S)),
        "confidence": None if res.confidence is None else round(res.confidence, 5),
        "o_s": int(start_idx - e), "o_without_D_s": int(start_idx - e0),
        "positions_searched": int(res.n_positions),
    }


def latency_rule(results: list, tau):
    """PROTOCOL section 6 steps 4-6 for one extractor."""
    os_all = [r["o_s"] for r in results if r.get("o_s") is not None]
    out = {"n_streams": len(results), "o_all_s": os_all}
    if tau is None:
        out["tau"] = None
        out["status"] = "PENDING: tau for (60 min, +-1 day) is not set yet (results/threshold.json missing)"
        out["L_s"] = None
        prov = [r["o_s"] for r in results if r.get("o_s") is not None and abs(r["o_s"]) <= MAX_ABS_O_S]
        out["provisional"] = summarise(prov, "PROVISIONAL, |o| <= 600 s only, no tau condition. Not L.")
        return out
    counted = [r["o_s"] for r in results if r.get("confidence") is not None and r["confidence"] >= tau
               and abs(r["o_s"]) <= MAX_ABS_O_S]
    out["tau"] = tau
    s = summarise(counted, "counted: confidence >= tau and |o| <= 600 s")
    out["counted"] = s
    if len(counted) >= MIN_COUNTED:
        out["L_s"] = int(np.round(np.median(counted)))
        out["status"] = "calibrated"
    else:
        out["L_s"] = 0
        out["status"] = "latency not calibrated (fewer than 3 streams count)"
    return out


def summarise(vals: list, what: str) -> dict:
    d = {"what": what, "n": len(vals), "values_s": sorted(vals)}
    if vals:
        v = np.asarray(vals, dtype=float)
        q1, q3 = np.percentile(v, [25, 75])
        d.update(median_s=float(np.median(v)), median_rounded_s=int(np.round(np.median(v))),
                 iqr_s=[float(q1), float(q3)], range_s=[float(v.min()), float(v.max())],
                 range_width_s=float(v.max() - v.min()))
        d["constant_latency_holds"] = bool(d["range_width_s"] <= RANGE_FLAG_S)
    return d


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Latency L from Calibration only")
    ap.add_argument("--manifest", default="data/harvest_manifest.csv")
    ap.add_argument("--splits-csv", default="data/splits.csv")
    ap.add_argument("--threshold", default="results/threshold.json")
    ap.add_argument("--ref", default=None)
    args = ap.parse_args(argv)
    os.chdir(REPO)
    meta, man = load_rows(REPO / args.manifest, REPO / args.splits_csv)
    tau = load_tau(REPO / args.threshold)
    ref = gref.load(args.ref)
    streams, per_ext = [], {e: [] for e in EXTRACTORS}
    for vid, s in meta.items():
        m = man.get(vid)
        row = {"video_id": vid, "channel": s["channel"], "venue_type": s["venue_type"],
               "release_time_ist": s["release_time_ist"], "D_s": float(s["label_drift_s"]),
               "label_stable": s["label_stable"] == "True", "drift_window_r": float(s["drift_window_r"]),
               "ref_src_file": s["ref_src_file"],
               "harvest_status": None if m is None else m["status"],
               "harvest_reason": None if m is None else m["reason"]}
        if m is None or m["status"] != "done":
            streams.append(row)
            continue
        path = REPO / m["trace_path"]
        if path.parent.name != "calibration":
            raise SystemExit(f"refusing to read a non-Calibration trace: {path}")
        with np.load(path, allow_pickle=False) as z:
            tmeta = json.loads(str(z["meta_json"][()]))
            row["seconds"] = float(m["seconds"])
            row["audio_sha256"] = m["audio_sha256"]
            hum = {}
            for key, tm in tmeta["traces"].items():
                ext, part = key.split("__")
                if part in ("full", f"x{LENGTH_MIN}"):
                    hum[key] = {"present": tm["hum"]["present"],
                                "median_peak_to_floor": tm["hum"]["median_peak_to_floor"]}
            row["hum"] = hum
            row["hum_present_v1_full"] = m["hum_present_v1"] == "True"
            row["locks"] = {}
            for ext in EXTRACTORS:
                r = search_one(z, ext, ref, int(s["release_timestamp"]), float(s["label_drift_s"]))
                if r is None:
                    continue
                r["trace_hum_flag"] = bool(tmeta["traces"][f"{ext}__x{LENGTH_MIN}"]["hum"]["present"])
                row["locks"][ext] = r
                per_ext[ext].append({**r, "video_id": vid})
        streams.append(row)
    present = [e for e in EXTRACTORS if per_ext[e]]
    primary = "v2" if "v2" in present else "v1"
    rules = {e: latency_rule(per_ext[e], None if tau is None else tau.get(e)) for e in present}
    out = {
        "written_ist": now_ist(), "script": "scripts/calibrate_latency.py",
        "protocol": "PROTOCOL.md section 6 (draft, not yet approved)",
        "scope": "+-1 day around release time in IST", "excerpt": "60 min, audio [120, 3720) s",
        "frame0_offset_s": FRAME0_S, "max_abs_o_s": MAX_ABS_O_S, "range_flag_s": RANGE_FLAG_S,
        "threshold_file": args.threshold, "tau_available": tau is not None,
        "primary_extractor": primary, "n_calibration_streams": len(meta),
        "n_harvested": sum(1 for s in streams if s.get("harvest_status") == "done"),
        "latency": rules, "streams": streams,
        "note": "Calibration only. No Dev or Test trace was read or searched.",
    }
    res_dir = REPO / "results"
    tmp = res_dir / "calibration.json.tmp"
    tmp.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, res_dir / "calibration.json")
    if tau is not None and primary in rules:
        lat = {"written_ist": out["written_ist"], "primary_extractor": primary,
               "L_s": rules[primary]["L_s"], "status": rules[primary]["status"],
               "counted": rules[primary].get("counted"), "tau": rules[primary]["tau"],
               "source": "results/calibration.json"}
        tmp = res_dir / "latency.json.tmp"
        tmp.write_text(json.dumps(lat, indent=1), encoding="utf-8")
        os.replace(tmp, res_dir / "latency.json")
    write_md(out, res_dir / "calibration.md")
    print(json.dumps({e: {k: v for k, v in r.items() if k != "o_all_s"} for e, r in rules.items()}, indent=1))
    return 0


def _f(v, nd=3):
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def write_md(d: dict, path: Path) -> None:
    L = ["# Latency calibration (Calibration split only)\n"]
    L.append(f"Generated by `scripts/calibrate_latency.py` at {d['written_ist']}. Every number below is "
             "copied from `results/calibration.json`, which the same run wrote. It follows PROTOCOL.md "
             "section 6, which is still a draft. No Dev or Test trace was read or searched.\n")
    L.append("## Set-up\n")
    L.append(f"- Calibration streams in `data/splits.csv`: {d['n_calibration_streams']}. Harvested: "
             f"{d['n_harvested']} (`data/harvest_manifest.csv`).")
    L.append(f"- Excerpt: {d['excerpt']}. Scope: {d['scope']}. Implied start = lock position − "
             f"{d['frame0_offset_s']} s.")
    L.append("- o = implied start on the reference labels − (release time in IST + D). D is "
             "`label_drift_s` from `data/splits.csv`.")
    L.append(f"- Primary extractor: {d['primary_extractor']}. v1 and the naive baseline are shown beside it.")
    if not d["tau_available"]:
        L.append(f"- **tau is not set yet** (`{d['threshold_file']}` does not exist; it needs the "
                 "threshold-half negatives, PROTOCOL section 10). So step 4 of the L rule cannot be applied. "
                 "**L is PENDING** and `results/latency.json` was not written. The PROVISIONAL figures below "
                 "drop the tau condition and keep only |o| ≤ 600 s. They are for information and are not L.")
    L.append("")
    L.append("## L by extractor\n")
    L.append("| extractor | status | streams | n used | median o (s) | IQR (s) | range (s) | range ≤ 60 s? |")
    L.append("|---|---|---|---|---|---|---|---|")
    for ext, r in d["latency"].items():
        s = r.get("counted") or r.get("provisional") or {}
        L.append(f"| {ext} | {r['status'] if r['L_s'] is not None else 'PENDING (PROVISIONAL shown)'} | "
                 f"{r['n_streams']} | {s.get('n', 0)} | {_f(s.get('median_s'), 1)} | "
                 f"{_f(s.get('iqr_s'))} | {_f(s.get('range_s'))} | {_f(s.get('constant_latency_holds'))} |")
    L.append("")
    done = [s for s in d["streams"] if s.get("harvest_status") == "done"]
    n_hum = sum(1 for s in done if s.get("hum_present_v1_full"))
    L.append(f"- v1 hum flag (PROTOCOL M1 rule, all downloaded audio): {n_hum} of {len(done)} harvested streams.")
    for ext in d["latency"]:
        near = [s["video_id"] for s in done if ext in s.get("locks", {}) and abs(s["locks"][ext]["o_s"]) <= 600]
        L.append(f"- {ext}: top candidate within 600 s of release time + D for {len(near)} of {len(done)} "
                 f"streams ({', '.join(near) or 'none'}).")
    for s in d["streams"]:
        if s.get("harvest_status") not in (None, "done"):
            L.append(f"- Not harvested: {s['video_id']} ({s['harvest_status']}: {s.get('harvest_reason')}).")
    L.append("")
    for ext, r in d["latency"].items():
        s = r.get("counted") or r.get("provisional") or {}
        if s.get("n") and s.get("range_width_s", 0) == RANGE_FLAG_S:
            L.append(f"- **Note ({ext}):** the range of the o values used is exactly {RANGE_FLAG_S} s, at the "
                     "limit. PROTOCOL flags a range that exceeds 60 s, so this one is not flagged, but one more "
                     "spread-out lock would flag it.")
        if s.get("n") and not s.get("constant_latency_holds", True):
            L.append(f"- **Flag ({ext}):** the range of the o values used is {s['range_width_s']:.0f} s, "
                     "more than 60 s. PROTOCOL says a single constant latency does not hold.")
    L.append("")
    L.append("## Per stream\n")
    L.append("Lock = implied recording start (IST, reference labels). conf = atanh(r1) − atanh(r2).\n")
    L.append("| stream | venue | D (s) | stable | hum v1 (max median) | extractor | lock (IST) | r1 | conf | o (s) | o, D = 0 (s) | abs(o) ≤ 600 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for s in d["streams"]:
        if s.get("harvest_status") != "done":
            L.append(f"| {s['video_id']} | {s['venue_type']} | {s['D_s']:.0f} | {s['label_stable']} | "
                     f"not harvested: {s.get('harvest_status')} {s.get('harvest_reason') or ''} | | | | | | | |")
            continue
        v1h = s["hum"].get("v1__full", {})
        mx = max(v1h.get("median_peak_to_floor", {"": 0}).values()) if v1h else None
        for i, (ext, lk) in enumerate(s["locks"].items()):
            head = (f"| {s['video_id']} | {s['venue_type']} | {s['D_s']:.0f} | {s['label_stable']} | "
                    f"{v1h.get('present')} ({_f(mx, 2)}) ") if i == 0 else "| | | | | "
            L.append(head + f"| {ext} | {lk['lock_start_ist']} | {_f(lk['r1'])} | {_f(lk['confidence'])} | "
                     f"{lk['o_s']} | {lk['o_without_D_s']} | {abs(lk['o_s']) <= 600} |")
    L.append("")
    L.append("## Notes\n")
    L.append("- M1 hum flag: any harmonic's median peak-to-floor ratio ≥ 3 with v1 on all downloaded audio.")
    L.append("- The o values with D = 0 are shown only to describe the label problem; L uses D.")
    path.with_name(path.name + ".tmp").write_text("\n".join(L) + "\n", encoding="utf-8")
    os.replace(path.with_name(path.name + ".tmp"), path)


if __name__ == "__main__":
    raise SystemExit(main())
