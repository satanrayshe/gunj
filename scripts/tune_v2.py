#!/usr/bin/env python
"""Tune extractor v2 on the 5 Dev streams only.

The objective and the candidates were fixed in results/v2_tuning_plan.md
before any Dev result existed. Only Dev audio (kept 1 kHz FLAC under
media/keep) and the reference are read. No Calibration, Test or negative
data is touched.

  tune_v2.py run      score every candidate, write results/v2_tuning.json
  tune_v2.py report   write results/v2_tuning.md from the JSON, with the
                      SHA-256 of the v2 code and a check that V2_FROZEN in
                      gunj/extract_v2.py equals the chosen candidate

Run "report" again after editing V2_FROZEN, before the freeze commit.
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import argparse
import csv
import datetime as dt
import hashlib
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np                                   # noqa: E402
import pandas as pd                                  # noqa: E402
import soundfile as sf                               # noqa: E402
from scipy.ndimage import maximum_filter1d           # noqa: E402

from gunj import reference as gref                   # noqa: E402
from gunj.extract_v2 import V2_FROZEN, V2Params, combine, harmonic_tracks   # noqa: E402
from gunj.match import fisher_margin, ncc_search     # noqa: E402
from gunj.match_v2 import wncc_search                # noqa: E402

LENGTHS_MIN = (10, 20, 40, 60)
EXCERPT_START_S = 120
FRAME0_S = 128            # frame 0 is centred 128 s into the recording (PROTOCOL section 8)
TRUE_TOL_S = 600          # true region: E +- 600 s (PROTOCOL section 6, L step 4)
SEP_S = 60
CLIP = 1.0
TIE = 0.01
SR = 1000
JSON_PATH = REPO / "results" / "v2_tuning.json"
MD_PATH = REPO / "results" / "v2_tuning.md"
CACHE = REPO / "media" / "keep" / "tune_cache"
V2_FILES = ("gunj/extract_v2.py", "gunj/match_v2.py")

B0 = V2Params(band_half=0.15, gate=3.0, frame_offset=1.0, frame_power=1.0, consensus_hz=None,
              smooth=5, corr_weights=False)
STAGE_A = [
    ("A1", "base B0", B0),
    ("A2", "B0 + correlation weights", replace(B0, corr_weights=True)),
    ("A3", "B0 + band 0.30 Hz", replace(B0, band_half=0.30)),
    ("A4", "B0 + correlation weights + band 0.30 Hz", replace(B0, corr_weights=True, band_half=0.30)),
]


def stage_b(w: V2Params) -> list:
    band_alt = 0.45 if abs(w.band_half - 0.30) < 1e-9 else 0.22
    return [
        ("B1", "frame weight power 2", replace(w, frame_power=2.0)),
        ("B2", "running median 1 point (none)", replace(w, smooth=1)),
        ("B3", "running median 9 points", replace(w, smooth=9)),
        ("B4", "harmonic consensus 0.03 Hz", replace(w, consensus_hz=0.03)),
        ("B5", "harmonic gate 2", replace(w, gate=2.0)),
        ("B6", f"band {band_alt:.2f} Hz", replace(w, band_half=band_alt)),
    ]


def now_ist() -> str:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).isoformat(timespec="seconds")


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_dev(manifest: Path, splits: Path) -> list:
    with open(splits, encoding="utf-8", newline="") as f:
        meta = {r["video_id"]: r for r in csv.DictReader(f) if r["split"] == "dev"}
    with open(manifest, encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["split"] == "dev"]
    out = []
    for r in rows:
        if r["status"] != "done" or not r.get("flac_path"):
            continue
        s = meta[r["video_id"]]
        out.append({"video_id": r["video_id"], "flac": REPO / r["flac_path"],
                    "trace": REPO / r["trace_path"], "audio_sha256": r["audio_sha256"],
                    "release_timestamp": int(s["release_timestamp"]), "D": float(s["label_drift_s"]),
                    "venue": s["venue_type"], "channel": s["channel"],
                    "excerpts": [int(x) for x in s["excerpt_lengths_min"].split(";") if x]})
    missing = sorted(set(meta) - {d["video_id"] for d in out})
    return out, missing


def read_flac(path: Path, want_sha: str) -> np.ndarray:
    pcm, sr = sf.read(str(path), dtype="int16", always_2d=True)
    pcm = np.ascontiguousarray(pcm[:, 0])
    if sr != SR:
        raise ValueError(f"{path.name}: {sr} Hz")
    if hashlib.sha256(pcm.astype("<i2").tobytes()).hexdigest() != want_sha:
        raise ValueError(f"{path.name}: SHA-256 differs from the manifest")
    return pcm.astype(np.float32) / 32768.0          # exactly what the harvester extracted from


def tracks(audio, vid, m, band, mem: dict):
    key = (vid, m, round(band, 4))
    if key in mem:
        return mem[key]
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{vid}_x{m}_b{band:.2f}.npz"
    if f.exists():
        with np.load(f) as z:
            val = (tuple(int(k) for k in z["used"]), z["hf"], z["hs"], int(z["nfft"]))
    else:
        i0 = EXCERPT_START_S * SR
        seg = audio[i0:i0 + 60 * m * SR]
        used, hf, hs, nfft, _ = harmonic_tracks(seg, SR, band_half=band)
        tmp = f.with_name(f.name + ".tmp")
        with open(tmp, "wb") as fh:
            np.savez(fh, used=np.array(used), hf=hf, hs=hs, nfft=np.array(nfft))
        os.replace(tmp, f)
        val = (tuple(used), hf, hs, nfft)
    mem[key] = val
    return val


def scope_1d(ref, release_ts: int):
    i0, i1 = ref.scope(gref.unix_to_ist(release_ts), days=1)    # bounds on the implied start
    return i0 + FRAME0_S, min(i1 + FRAME0_S, len(ref))


def expected_index(ref, release_ts: int, D: float) -> int:
    t = gref.unix_to_ist(release_ts) + pd.Timedelta(seconds=float(D))
    return ref.index_of(t, check=False)


def score_cell(freq, fw, ref, release_ts, D) -> dict:
    p0, p1 = scope_1d(ref, release_ts)
    r = np.full(p1 - p0, -np.inf)
    if fw is None:
        res = ncc_search(freq, ref.freq, ref.valid, start=p0, stop=p1, k=5, r_out=r)
    else:
        res = wncc_search(freq, fw, ref.freq, ref.valid, start=p0, stop=p1, k=5, r_out=r)
    e = expected_index(ref, release_ts, D)
    pos = np.arange(p0, p1)
    start = pos - FRAME0_S
    fin = np.isfinite(r)
    true = (np.abs(start - e) <= TRUE_TOL_S) & fin
    out = {"top_r": None, "top_offset_s": None, "top_in_true": False, "confidence": res.confidence,
           "r_true": None, "true_offset_s": None, "r_other": None, "margin": None}
    if res.top is not None:
        out["top_r"] = round(res.top.r, 5)
        out["top_offset_s"] = int(res.top.index - FRAME0_S - e)
        out["top_in_true"] = bool(abs(res.top.index - FRAME0_S - e) <= TRUE_TOL_S)
    if res.confidence is not None:
        out["confidence"] = round(res.confidence, 5)
    if not true.any():
        return out
    jt = int(np.flatnonzero(true)[np.argmax(r[true])])
    rt = float(r[jt])
    mx = maximum_filter1d(r, size=2 * SEP_S + 1, mode="constant", cval=-np.inf)
    peak = (r == mx) & fin
    other = peak & (np.abs(pos - pos[jt]) >= SEP_S)
    if not other.any():
        return out
    ro = float(r[other].max())
    out.update(r_true=round(rt, 5), true_offset_s=int(start[jt] - e), r_other=round(ro, 5),
               margin=round(fisher_margin(rt, ro), 5))
    return out


def objective(cells: list) -> float:
    vals = [np.clip(c["margin"], -CLIP, CLIP) if c["margin"] is not None else -CLIP for c in cells]
    return float(np.mean(vals))


def score_candidate(params: V2Params, dev, audio_cache, mem, ref, log) -> dict:
    cells = []
    for d in dev:
        audio = audio_cache[d["video_id"]]
        for m in LENGTHS_MIN:
            if m not in d["excerpts"]:
                continue
            used, hf, hs, nfft = tracks(audio, d["video_id"], m, params.band_half, mem)
            tr = combine(used, hf, hs, params, sr=SR, nfft=nfft)
            c = score_cell(tr.freq, tr.extra.get("frame_weight"), ref, d["release_timestamp"], d["D"])
            c.update(video_id=d["video_id"], length_min=m, hum_present=bool(tr.hum_present))
            cells.append(c)
    return {"J": round(objective(cells), 5), "cells": cells,
            "top_in_true": sum(c["top_in_true"] for c in cells), "n_cells": len(cells)}


def score_stored(dev, ref, ext: str) -> dict:
    """Score a frozen extractor's harvested traces (v1, naive) the same way."""
    cells = []
    for d in dev:
        with np.load(d["trace"]) as z:
            for m in LENGTHS_MIN:
                k = f"{ext}__x{m}__freq"
                if k not in z.files:
                    continue
                c = score_cell(z[k], None, ref, d["release_timestamp"], d["D"])
                c.update(video_id=d["video_id"], length_min=m)
                cells.append(c)
    return {"J": round(objective(cells), 5), "cells": cells,
            "top_in_true": sum(c["top_in_true"] for c in cells), "n_cells": len(cells)}


def full_scope_60(freq, fw, ref, release_ts, D) -> dict:
    t = time.time()
    if fw is None:
        res = ncc_search(freq, ref.freq, ref.valid, k=5)
    else:
        res = wncc_search(freq, fw, ref.freq, ref.valid, k=5)
    e = expected_index(ref, release_ts, D)
    top = res.top
    off = int(top.index - FRAME0_S - e)
    return {"top_r": round(top.r, 5), "confidence": None if res.confidence is None else round(res.confidence, 5),
            "top_offset_s": off, "top_start_ist": str(ref.time_of(top.index - FRAME0_S)),
            "top_in_true": abs(off) <= TRUE_TOL_S, "seconds": round(time.time() - t, 1)}


def choose(cands: list) -> dict:
    best = max(c["J"] for c in cands)
    for c in cands:                      # listed order: first within TIE of the best wins
        if c["J"] >= best - TIE:
            return c
    raise AssertionError


def cmd_run(args) -> int:
    ref = gref.load(args.ref)
    dev, missing = load_dev(REPO / args.manifest, REPO / args.splits_csv)
    if missing:
        print(f"Dev streams without kept audio: {missing}", flush=True)
    if not dev:
        print("no Dev audio; run the Dev harvest first", flush=True)
        return 1
    audio = {d["video_id"]: read_flac(d["flac"], d["audio_sha256"]) for d in dev}
    mem: dict = {}
    t0 = time.time()
    out = {"written_ist": now_ist(), "plan": "results/v2_tuning_plan.md",
           "dev_streams": [{k: (str(v) if isinstance(v, Path) else v) for k, v in d.items()} for d in dev],
           "missing_dev": missing, "objective": {
               "J": "mean over Dev stream x length cells of clip(atanh(r_true) - atanh(r_other), -1, 1)",
               "true_region_s": TRUE_TOL_S, "scope": "+-1 day around release time (IST)",
               "lengths_min": list(LENGTHS_MIN), "L_assumed_s": 0, "tie": TIE},
           "baselines": {}, "candidates": []}
    for ext in ("v1", "naive"):
        out["baselines"][ext] = score_stored(dev, ref, ext)
        print(f"baseline {ext}: J={out['baselines'][ext]['J']}", flush=True)
    for cid, desc, p in STAGE_A:
        s = score_candidate(p, dev, audio, mem, ref, None)
        out["candidates"].append({"id": cid, "stage": "A", "desc": desc, "params": p.to_dict(), **s})
        print(f"{cid} {desc}: J={s['J']} top_in_true={s['top_in_true']}/{s['n_cells']} "
              f"({time.time() - t0:.0f} s)", flush=True)
    win_a = choose([c for c in out["candidates"] if c["stage"] == "A"])
    out["stage_a_winner"] = win_a["id"]
    wp = V2Params(**{**win_a["params"], "harmonics": tuple(win_a["params"]["harmonics"])})
    for cid, desc, p in stage_b(wp):
        s = score_candidate(p, dev, audio, mem, ref, None)
        out["candidates"].append({"id": cid, "stage": "B", "desc": f"{win_a['id']} + {desc}",
                                  "params": p.to_dict(), **s})
        print(f"{cid} {desc}: J={s['J']} top_in_true={s['top_in_true']}/{s['n_cells']} "
              f"({time.time() - t0:.0f} s)", flush=True)
    chosen = choose(out["candidates"])
    out["chosen"] = chosen["id"]
    out["chosen_params"] = chosen["params"]
    out["chosen_beats_v1"] = bool(chosen["J"] > out["baselines"]["v1"]["J"])
    # diagnostics only (not used to choose): 60-min excerpt in the full scope
    cp = V2Params(**{**chosen["params"], "harmonics": tuple(chosen["params"]["harmonics"])})
    diag = []
    for d in dev:
        if 60 not in d["excerpts"]:
            continue
        with np.load(d["trace"]) as z:
            v1 = full_scope_60(z["v1__x60__freq"], None, ref, d["release_timestamp"], d["D"])
        used, hf, hs, nfft = tracks(audio[d["video_id"]], d["video_id"], 60, cp.band_half, mem)
        tr = combine(used, hf, hs, cp, sr=SR, nfft=nfft)
        v2 = full_scope_60(tr.freq, tr.extra.get("frame_weight"), ref, d["release_timestamp"], d["D"])
        diag.append({"video_id": d["video_id"], "v1": v1, "v2": v2})
        print(f"full scope 60 min {d['video_id']}: v1 {v1} | v2 {v2}", flush=True)
    out["full_scope_60_diagnostic"] = diag
    out["elapsed_s"] = round(time.time() - t0, 1)
    tmp = JSON_PATH.with_name(JSON_PATH.name + ".tmp")
    tmp.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, JSON_PATH)
    print(f"chosen {chosen['id']} J={chosen['J']} (v1 J={out['baselines']['v1']['J']}); wrote {JSON_PATH}",
          flush=True)
    return 0


def _fmt(v, nd=3):
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def cmd_report(args) -> int:
    d = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    chosen = next(c for c in d["candidates"] if c["id"] == d["chosen"])
    frozen = V2_FROZEN.to_dict()
    match = frozen == chosen["params"]
    shas = {f: sha256_file(REPO / f) for f in V2_FILES}
    combined = hashlib.sha256(b"".join((REPO / f).read_bytes() for f in V2_FILES)).hexdigest()
    vids = [s["video_id"] for s in d["dev_streams"]]
    L = []
    L.append("# Extractor v2 tuning (Dev only)\n")
    L.append(f"Generated by `scripts/tune_v2.py report` at {now_ist()} from `results/v2_tuning.json` "
             f"(written {d['written_ist']} by `scripts/tune_v2.py run`). Every number below is copied from that "
             "file. The objective and the candidate list were fixed in `results/v2_tuning_plan.md` before any "
             "Dev result existed.\n")
    L.append("## Data\n")
    L.append(f"- Dev streams used: {len(vids)} ({', '.join(vids)}). Their 1 kHz audio is the kept FLAC "
             "under `media/keep` (not in git); its SHA-256 matches `data/harvest_manifest.csv`.")
    if d["missing_dev"]:
        L.append(f"- Dev streams without audio: {', '.join(d['missing_dev'])}.")
    L.append("- No Calibration, Test or negative audio was used.\n")
    L.append("## Objective\n")
    o = d["objective"]
    L.append(f"- J = {o['J']}.")
    L.append(f"- Scope: {o['scope']}. True region: implied start within E ± {o['true_region_s']} s, "
             f"E = release time + D (L assumed {o['L_assumed_s']} s).")
    L.append(f"- Cells: Dev stream × excerpt length {o['lengths_min']} min. Tie rule: within {o['tie']} of the "
             "best J, the candidate listed first wins.\n")
    L.append("## Results\n")
    L.append("| id | stage | candidate | J | cells with top candidate in true region |")
    L.append("|---|---|---|---|---|")
    for ext, b in d["baselines"].items():
        L.append(f"| {ext} | frozen | {ext} as harvested | {b['J']:.3f} | {b['top_in_true']} / {b['n_cells']} |")
    for c in d["candidates"]:
        L.append(f"| {c['id']} | {c['stage']} | {c['desc']} | {c['J']:.3f} | {c['top_in_true']} / {c['n_cells']} |")
    L.append("")
    L.append(f"Stage A winner: {d['stage_a_winner']}. Chosen: **{d['chosen']}** ({chosen['desc']}), "
             f"J = {chosen['J']:.3f}, against v1 J = {d['baselines']['v1']['J']:.3f}.")
    if not d["chosen_beats_v1"]:
        L.append("\n**The chosen v2 does not beat v1 on J.** PROTOCOL makes v2 the primary extractor if it is "
                 "frozen in time. Whether to keep v2 as primary is Shrey's decision.")
    hum = sorted({c["video_id"] for c in chosen["cells"] if c["hum_present"] and c["length_min"] == 60})
    lk2 = sorted({c["video_id"] for c in chosen["cells"] if c["top_in_true"] and c["length_min"] == 60})
    lk1 = sorted({c["video_id"] for c in d["baselines"]["v1"]["cells"]
                  if c["top_in_true"] and c["length_min"] == 60})
    L.append(f"\nIn plain words: {len(hum)} of {len(vids)} Dev streams have a hum flag on the 60-min excerpt "
             f"with the chosen v2 ({', '.join(hum) or 'none'}). At 60 min in the ±1-day scope the top candidate "
             f"is in the true region for {len(lk2)} streams with v2 and {len(lk1)} with v1. The other streams "
             "show no usable hum with either extractor, so their margins are negative for every candidate.")
    L.append("\nChosen parameters:\n")
    L.append("```")
    L.append(json.dumps(chosen["params"], indent=1))
    L.append("```\n")
    L.append("### Per cell, chosen v2 and v1\n")
    L.append("margin = atanh(r_true) − atanh(r_other); offsets are implied start − E, in seconds.\n")
    L.append("| stream | min | v2 margin | v2 top offset | v2 conf | v1 margin | v1 top offset | v1 conf | hum (v2) |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    v1c = {(c["video_id"], c["length_min"]): c for c in d["baselines"]["v1"]["cells"]}
    for c in chosen["cells"]:
        b = v1c.get((c["video_id"], c["length_min"]), {})
        L.append(f"| {c['video_id']} | {c['length_min']} | {_fmt(c['margin'])} | {_fmt(c['top_offset_s'])} | "
                 f"{_fmt(c['confidence'])} | {_fmt(b.get('margin'))} | {_fmt(b.get('top_offset_s'))} | "
                 f"{_fmt(b.get('confidence'))} | {c['hum_present']} |")
    L.append("\n### Diagnostic: 60-min excerpt in the full scope (not used to choose)\n")
    L.append("| stream | v1 top offset (s) | v1 conf | v2 top offset (s) | v2 conf |")
    L.append("|---|---|---|---|---|")
    for g in d["full_scope_60_diagnostic"]:
        L.append(f"| {g['video_id']} | {g['v1']['top_offset_s']} | {_fmt(g['v1']['confidence'])} | "
                 f"{g['v2']['top_offset_s']} | {_fmt(g['v2']['confidence'])} |")
    L.append("")
    L.append("## Freeze\n")
    L.append(f"- `V2_FROZEN` in `gunj/extract_v2.py` equals the chosen parameters: **{match}**.")
    for f, s in shas.items():
        L.append(f"- SHA-256 of `{f}`: `{s}`")
    L.append(f"- SHA-256 of the two files concatenated in that order (the harvester's `v2_code_sha256`): "
             f"`{combined}`")
    L.append("- Frozen by the annotated git tag `extractor-v2` on the commit that adds these files. "
             "`scripts/harvest.py` refuses to extract v2 if either file differs from the tag.")
    L.append("- v1 (`gunj/extract.py`) and the frozen matcher (`gunj/match.py`) are unchanged; "
             "`tests/test_v2.py` checks their PROTOCOL hashes.")
    text = "\n".join(L) + "\n"
    tmp = MD_PATH.with_name(MD_PATH.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, MD_PATH)
    print(f"wrote {MD_PATH} (V2_FROZEN equals the chosen candidate: {match})")
    return 0 if match else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tune extractor v2 on Dev only")
    ap.add_argument("cmd", choices=["run", "report"])
    ap.add_argument("--manifest", default="data/harvest_manifest.csv")
    ap.add_argument("--splits-csv", default="data/splits.csv")
    ap.add_argument("--ref", default=None, help="reference folder (default ../data/derived)")
    args = ap.parse_args(argv)
    os.chdir(REPO)
    return cmd_run(args) if args.cmd == "run" else cmd_report(args)


if __name__ == "__main__":
    raise SystemExit(main())
