"""Draw Gunj's pre-registered splits (PROTOCOL.md) from metadata only.

No audio exists when this runs, and none is used. Inputs:
  - data/candidates_india.csv and data/candidates_negatives.csv
    (scripts/harvest_metadata.py; yt-dlp metadata, no media);
  - the reference validity mask and source-file arrays
    (<derived>/grid_1s_valid_u8.npy, grid_1s_src_u8.npy, grid_1s_meta.json);
  - results/time_axis_check.json (label drift of each reference file,
    scripts/check_time_axis.py).

Outputs:
  - data/splits.csv: one row per selected stream (and per SYNTHETIC negative);
  - results/splits_summary.json: counts, exclusions, shortfalls, the
    analytic random-guess rates, and the SHA-256 of splits.csv.

Everything is drawn with one numpy Generator seeded with SEED, in a fixed
order (dev, calibration, test, real negatives, SYNTHETIC negatives), so a
re-run on the same inputs writes a byte-identical splits.csv.

Rules (PROTOCOL.md section 4 explains them in prose)
----------------------------------------------------
Indian candidates are eligible for any split only if all of these hold:
  - the title does not look like a court proceeding (COURT_RE);
  - the start time is yt-dlp's release_timestamp, the stream was live,
    and the video is public;
  - duration >= 20 min and < 11.5 h (streams near YouTube's 12 h archive
    length may not start at release_timestamp);
  - the whole stream [start, start + duration) lies inside the reference
    series and >= 90% of its seconds are valid;
  - the analysed window [start, start + min(duration, 65 min)) is >= 90% valid;
  - every valid reference second in that window +- 10 min comes from one
    source file (no file handover inside it; at handovers 64-319 s of real
    time are silently missing, see grid_1s_meta.json "junctions").
No two selected Indian streams may overlap in time over their analysed
windows (so they are independent recordings).

Label drift. For each stream, D = the label drift (s) of the reference file
in use at its start: the 3-window running median of that file's weekly
shift_s in time_axis_check.json, taken at the window nearest the start.
label_stable = the nearest window's profile r >= 0.5.

Order of drawing:
  1. Dev: one stream per venue type, distinct channels, label_stable,
     duration >= 62 min, verified channels only; Creative Commons preferred
     for the lecture slot. Rules relax step by step if a type has none.
  2. Calibration: 20 streams, label_stable, duration >= 62 min, verified
     channels only, at most 1 per channel (then 2 if short).
  3. Test: up to 150 of the remaining eligible streams, in seeded random
     order, at most 10 per channel.
  4. Negatives: up to 50 foreign 50 Hz streams, spread over channels by
     round-robin quotas, streams >= 62 min preferred, then split
     alternately into a threshold half and a report half.
  5. SYNTHETIC negatives: 20 white-noise and 20 synthetic-walk hum items,
     each with its own seed and a random pseudo claimed time; alternate
     halves.

Usage (from the repo root):
  .venv\\Scripts\\python.exe scripts\\make_splits.py --derived-dir ..\\data\\derived
"""
from __future__ import annotations

import gunj  # noqa: F401  (first, so its OPENBLAS_NUM_THREADS default applies)

import argparse
import csv
import hashlib
import io
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from gunj import reference as gref

REPO = Path(__file__).resolve().parents[1]

SEED = 2026110701              # the split seed (PROTOCOL.md section 4)
MIN_DURATION_S = 20 * 60
ARCHIVE_CAP_S = int(11.5 * 3600)
DOWNLOAD_S = 65 * 60            # audio harvested per stream (first 65 min)
EXCERPT_START_S = 120           # excerpts start at audio second 120
EXCERPT_LENGTHS_MIN = (10, 20, 40, 60)
LONG_S = EXCERPT_START_S + 60 * max(EXCERPT_LENGTHS_MIN)   # 3,720 s
MIN_VALID = 0.9
JUNCTION_MARGIN_S = 600
HIT_TOL_S = 30
SCOPE_HALF_DAYS = {"full": None, "90d": 90, "1d": 1}

DEV_TARGET = 5
CAL_TARGET = 20
TEST_TARGET = 150
TEST_PER_CHANNEL = 10
NEG_TARGET = 50
SYN_PER_KIND = 20
LABEL_STABLE_R = 0.5
DRIFT_MEDIAN_WINDOWS = 3

VENUE_ORDER = ("legislature", "lecture", "religious", "government event", "conference")
# Channels whose official status the metadata step could not verify
# (results/metadata_summary.md), plus one whose streams may come from outside
# India. Kept out of Dev and Calibration; allowed in Test and flagged.
UNVERIFIED = {
    "shirdi_sansthan": "official status not verified",
    "maharashtra_assembly": "official status not verified",
    "sansad_tv_lok_sabha": "official status not verified",
    "prd_live_kerala": "picked from search results; official status not verified",
    "kerala_iprd": "picked from search results; official status not verified",
    "mayapur_tv": "some streams may come from outside Mayapur (location uncertain)",
}
EXCLUDED_NEG_COUNTRIES = {"NP", "BT"}   # may be synchronous with India
COURT_RE = re.compile(
    r"\bcourts?\b|high\s*court|supreme\s*court|judicial|judiciary|judge|tribunal|"
    r"\blitigation\b|e-?court|न्यायालय|अदालत|न्यायिक",
    re.IGNORECASE)

# Code frozen by the pre-registration; its SHA-256 goes into splits_summary.json
# and PROTOCOL.md (extractor v1, matcher, SYNTHETIC generators, reference loader).
FROZEN_CODE = ("gunj/extract.py", "gunj/match.py", "gunj/synthetic.py", "gunj/reference.py")

SPLIT_ORDER = {"dev": 0, "calibration": 1, "test": 2, "negative": 3, "negative_synthetic": 4}
COLUMNS = [
    "video_id", "split", "half", "channel", "channel_id", "source_key", "venue_type",
    "country", "release_timestamp", "release_time_ist", "duration_s", "licence",
    "license_cc", "official_status", "ref_src_file", "ref_valid_frac_span",
    "ref_valid_frac_window", "label_drift_s", "drift_window_r", "label_stable",
    "excerpt_lengths_min", "select_rule", "synthetic_seed", "title",
]


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def sha256_file(path: Path, chunk: int = 1 << 22) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """Wilson score 95% interval for k successes in n trials."""
    if n <= 0:
        return (float("nan"), float("nan"))
    p = k / n
    den = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def round_robin_quotas(avail: dict, target: int, order: list) -> dict:
    """Max-min fair quotas: one per channel per round, in ``order``, until
    ``target`` is reached or every channel is exhausted."""
    q = {c: 0 for c in avail}
    total = 0
    while total < target:
        moved = False
        for c in order:
            if total >= target:
                break
            if q[c] < avail[c]:
                q[c] += 1
                total += 1
                moved = True
        if not moved:
            break
    return q


def overlaps(a0: int, a1: int, taken: list) -> bool:
    return any(a0 < b1 and b0 < a1 for b0, b1 in taken)


def fits_lengths(duration_s: int) -> str:
    """Pre-registered excerpt lengths that fit in the downloaded audio."""
    avail = min(int(duration_s), DOWNLOAD_S)
    return ";".join(str(m) for m in EXCERPT_LENGTHS_MIN if EXCERPT_START_S + 60 * m <= avail)


# --------------------------------------------------------------------------
# label drift table
# --------------------------------------------------------------------------

def drift_table(time_axis: dict) -> dict:
    """file code (1..5) -> DataFrame of windows with centre time, shift, r, D."""
    names = {f"freq_{r}.csv": i + 1 for i, r in enumerate(("I", "II", "III", "IV", "V"))}
    df = pd.DataFrame(time_axis["per_window"])
    out = {}
    for name, g in df.groupby("file", sort=False):
        g = g.copy()
        # window_mid_ist is the (date of the) middle day; take its noon as the centre
        g["centre"] = pd.to_datetime(g["window_mid_ist"]) + pd.Timedelta(hours=12)
        g = g.sort_values("centre").reset_index(drop=True)
        s = g["shift_s"].astype(float).to_numpy()
        h = DRIFT_MEDIAN_WINDOWS // 2
        g["D"] = [float(np.median(s[max(0, i - h):i + h + 1])) for i in range(len(s))]
        out[names[name]] = g
    return out


def drift_at(table: dict, code: int, t: pd.Timestamp):
    g = table.get(int(code))
    if g is None or g.empty:
        return None, None
    j = int(np.argmin(np.abs((g["centre"] - t).dt.total_seconds().to_numpy())))
    return float(g["D"].iloc[j]), float(g["r"].iloc[j])


# --------------------------------------------------------------------------
# candidate annotation
# --------------------------------------------------------------------------

def annotate(df: pd.DataFrame, ref, src, drift: dict, group: str) -> pd.DataFrame:
    """Add reference coverage, drift and the first failing eligibility reason."""
    n_ref = len(ref)
    rows = []
    for rec in df.to_dict("records"):
        start = gref.unix_to_ist(int(rec["release_timestamp"]))
        dur = int(rec["duration_s"])
        i0 = ref.index_of(start, check=False)
        i1 = i0 + dur
        w1 = i0 + min(dur, DOWNLOAD_S)
        a = dict(rec)
        a["start_ist"] = start
        a["i0"], a["w1"] = i0, w1
        a["ref_valid_frac_span"] = float("nan")
        a["ref_valid_frac_window"] = float("nan")
        a["ref_src_code"] = 0
        a["label_drift_s"] = float("nan")
        a["drift_window_r"] = float("nan")
        a["n_src_files"] = 0
        in_series = (i0 >= 0) and (i1 <= n_ref)
        if 0 <= i0 < n_ref:
            j1 = min(i1, n_ref)
            a["ref_valid_frac_span"] = float(np.count_nonzero(ref.valid[i0:j1])) / dur
            k1 = min(w1, n_ref)
            a["ref_valid_frac_window"] = float(np.count_nonzero(ref.valid[i0:k1])) / (w1 - i0)
            m0, m1 = max(0, i0 - JUNCTION_MARGIN_S), min(n_ref, w1 + JUNCTION_MARGIN_S)
            sv = np.asarray(src[m0:m1])
            vals, counts = np.unique(sv[sv > 0], return_counts=True)
            a["n_src_files"] = int(vals.size)
            if vals.size:
                code = int(vals[np.argmax(counts)])
                a["ref_src_code"] = code
                d, r = drift_at(drift, code, start)
                if d is not None:
                    a["label_drift_s"], a["drift_window_r"] = d, r
        title = str(rec.get("title", ""))
        reason = ""
        if COURT_RE.search(title):
            reason = "court_like_title"
        elif group == "negative" and str(rec.get("country", "")) in EXCLUDED_NEG_COUNTRIES:
            reason = "nepal_or_bhutan"
        elif rec.get("start_ts_used") != "release_timestamp":
            reason = "start_not_release_timestamp"
        elif rec.get("live_status") != "was_live" or rec.get("availability") != "public":
            reason = "not_public_past_live"
        elif dur < MIN_DURATION_S:
            reason = "shorter_than_20_min"
        elif group == "india" and dur >= ARCHIVE_CAP_S:
            reason = "possible_12h_archive_cap"
        elif not in_series:
            reason = "outside_reference_series"
        elif group == "india" and a["ref_valid_frac_span"] < MIN_VALID:
            reason = "span_valid_below_90pct"
        elif group == "india" and a["ref_valid_frac_window"] < MIN_VALID:
            reason = "window_valid_below_90pct"
        elif group == "india" and a["n_src_files"] != 1:
            reason = "crosses_reference_file_handover"
        a["exclude_reason"] = reason
        a["label_stable"] = bool(np.isfinite(a["drift_window_r"]) and
                                 a["drift_window_r"] >= LABEL_STABLE_R)
        a["official_status"] = UNVERIFIED.get(rec["source_key"], "verified by metadata step")
        a["verified"] = rec["source_key"] not in UNVERIFIED
        rows.append(a)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# drawing
# --------------------------------------------------------------------------

def draw(india: pd.DataFrame, neg: pd.DataFrame, n_ref: int, t0: pd.Timestamp,
         rng: np.random.Generator):
    picked: dict = {}        # video_id -> (split, half, rule)
    taken: list = []         # analysed windows already used (India)
    notes: list = []

    elig = india[india["exclude_reason"] == ""].copy()
    # one fixed random order for the whole India pool
    perm = rng.permutation(len(elig))
    elig = elig.iloc[perm].reset_index(drop=True)

    def take(row, split, rule, half=""):
        picked[row["video_id"]] = (split, half, rule)
        taken.append((int(row["i0"]), int(row["w1"])))

    def free(row):
        return row["video_id"] not in picked and not overlaps(int(row["i0"]), int(row["w1"]), taken)

    # ---- 1. Dev ----------------------------------------------------------
    dev_channels: set = set()
    ladder = [
        ("strict", lambda r: r["label_stable"] and r["duration_s"] >= LONG_S and r["verified"]),
        ("relaxed:label_stable", lambda r: r["duration_s"] >= LONG_S and r["verified"]),
        ("relaxed:label_stable+duration", lambda r: r["verified"]),
    ]
    for venue in VENUE_ORDER:
        done = False
        for rule_name, ok in ladder:
            pool = [r for r in elig.to_dict("records")
                    if r["venue_type"] == venue and ok(r) and r["channel_id"] not in dev_channels
                    and free(r)]
            if venue == "lecture":
                cc = [r for r in pool if bool(r["license_cc"])]
                if cc:
                    pool, rule_name = cc, rule_name + "+creative_commons"
            if pool:
                r = pool[0]                      # elig is already in random order
                take(r, "dev", f"dev:{venue}:{rule_name}")
                dev_channels.add(r["channel_id"])
                done = True
                break
        if not done:
            notes.append(f"dev: no eligible stream for venue type '{venue}'")

    # ---- 2. Calibration --------------------------------------------------
    cal_rows = []
    for cap in (1, 2):
        per_ch: dict = {}
        for r in cal_rows:
            per_ch[r["channel_id"]] = per_ch.get(r["channel_id"], 0) + 1
        for r in elig.to_dict("records"):
            if len(cal_rows) >= CAL_TARGET:
                break
            if not (r["label_stable"] and r["duration_s"] >= LONG_S and r["verified"]):
                continue
            if per_ch.get(r["channel_id"], 0) >= cap or not free(r):
                continue
            take(r, "calibration", f"calibration:label_stable:cap{cap}")
            per_ch[r["channel_id"]] = per_ch.get(r["channel_id"], 0) + 1
            cal_rows.append(r)
    if len(cal_rows) < CAL_TARGET:
        notes.append(f"calibration: {len(cal_rows)} of {CAL_TARGET} under the strict rule")

    # ---- 3. Test ---------------------------------------------------------
    per_ch = {}
    n_test = 0
    skipped_overlap = 0
    skipped_cap = 0
    for r in elig.to_dict("records"):
        if n_test >= TEST_TARGET:
            break
        if r["video_id"] in picked:
            continue
        if per_ch.get(r["channel_id"], 0) >= TEST_PER_CHANNEL:
            skipped_cap += 1
            continue
        if overlaps(int(r["i0"]), int(r["w1"]), taken):
            skipped_overlap += 1
            continue
        take(r, "test", "test:random_order:cap10_per_channel")
        per_ch[r["channel_id"]] = per_ch.get(r["channel_id"], 0) + 1
        n_test += 1
    if n_test < TEST_TARGET:
        notes.append(f"test: {n_test} of {TEST_TARGET} (pool exhausted)")

    # ---- 4. Real negatives ----------------------------------------------
    nel = neg[neg["exclude_reason"] == ""].copy()
    nel = nel.iloc[rng.permutation(len(nel))].reset_index(drop=True)
    chans = sorted(nel["channel_id"].unique())
    avail = {c: int((nel["channel_id"] == c).sum()) for c in chans}
    order = [chans[i] for i in rng.permutation(len(chans))]
    quotas = round_robin_quotas(avail, NEG_TARGET, order)
    neg_sel = []
    for c in chans:
        g = nel[nel["channel_id"] == c]
        longf = g[g["duration_s"] >= LONG_S]
        short = g[g["duration_s"] < LONG_S]
        chosen = pd.concat([longf, short]).head(quotas[c])
        chosen = chosen.iloc[rng.permutation(len(chosen))]
        neg_sel.extend(chosen.to_dict("records"))
    for k, r in enumerate(neg_sel):
        half = "threshold" if k % 2 == 0 else "report"
        picked[r["video_id"]] = ("negative", half, "negative:round_robin_quota:long_first")
    if len(neg_sel) < NEG_TARGET:
        notes.append(f"negatives: {len(neg_sel)} of {NEG_TARGET} (pool exhausted)")

    # ---- 5. SYNTHETIC negatives -----------------------------------------
    syn = []
    for kind in ("noise", "walk"):
        for j in range(SYN_PER_KIND):
            seed_j = int(rng.integers(1, 2**31 - 1))
            pos = int(rng.integers(0, n_ref - DOWNLOAD_S))
            claimed = t0 + pd.Timedelta(seconds=pos)
            unix = int(gref.ist_to_unix(claimed))
            syn.append({
                "video_id": f"SYN-{kind.upper()}-{j + 1:02d}",
                "split": "negative_synthetic",
                "half": "threshold" if j % 2 == 0 else "report",
                "channel": "SYNTHETIC",
                "channel_id": "SYNTHETIC",
                "source_key": f"synthetic_{kind}",
                "venue_type": "synthetic",
                "country": "SYNTHETIC",
                "release_timestamp": unix,
                "release_time_ist": claimed.strftime("%Y-%m-%dT%H:%M:%S+05:30"),
                "duration_s": DOWNLOAD_S,
                "licence": "SYNTHETIC (generated by gunj/synthetic.py; no licence needed)",
                "license_cc": False,
                "official_status": "SYNTHETIC",
                "ref_src_file": "", "ref_valid_frac_span": "", "ref_valid_frac_window": "",
                "label_drift_s": "", "drift_window_r": "", "label_stable": "",
                "excerpt_lengths_min": fits_lengths(DOWNLOAD_S),
                "select_rule": ("synthetic:white_noise_audio" if kind == "noise"
                                else "synthetic:hum_following_synthetic_OU_walk"),
                "synthetic_seed": seed_j,
                "title": ("SYNTHETIC white noise, 65 min at 1 kHz" if kind == "noise" else
                          "SYNTHETIC hum following a SYNTHETIC random walk, 65 min at 1 kHz"),
            })
    stats = {"test_skipped_channel_cap": skipped_cap, "test_skipped_time_overlap": skipped_overlap,
             "negative_quotas": {str(c): int(q) for c, q in quotas.items()}}
    return picked, syn, notes, stats


def to_rows(df: pd.DataFrame, picked: dict) -> list:
    code_name = {1: "freq_I.csv", 2: "freq_II.csv", 3: "freq_III.csv", 4: "freq_IV.csv",
                 5: "freq_V.csv"}
    out = []
    for r in df.to_dict("records"):
        if r["video_id"] not in picked:
            continue
        split, half, rule = picked[r["video_id"]]
        is_neg = split == "negative"
        out.append({
            "video_id": r["video_id"], "split": split, "half": half,
            "channel": r["channel"], "channel_id": r["channel_id"], "source_key": r["source_key"],
            "venue_type": r["venue_type"], "country": r["country"],
            "release_timestamp": int(r["release_timestamp"]),
            "release_time_ist": r["start_ist"].strftime("%Y-%m-%dT%H:%M:%S+05:30"),
            "duration_s": int(r["duration_s"]), "licence": r["license"],
            "license_cc": bool(r["license_cc"]), "official_status": r["official_status"],
            "ref_src_file": "" if is_neg else code_name.get(int(r["ref_src_code"]), ""),
            "ref_valid_frac_span": "" if is_neg else f"{r['ref_valid_frac_span']:.4f}",
            "ref_valid_frac_window": "" if is_neg else f"{r['ref_valid_frac_window']:.4f}",
            "label_drift_s": "" if is_neg else f"{r['label_drift_s']:.1f}",
            "drift_window_r": "" if is_neg else f"{r['drift_window_r']:.4f}",
            "label_stable": "" if is_neg else bool(r["label_stable"]),
            "excerpt_lengths_min": fits_lengths(r["duration_s"]),
            "select_rule": rule, "synthetic_seed": "", "title": r["title"],
        })
    return out


def csv_bytes(rows: list) -> bytes:
    rows = sorted(rows, key=lambda r: (SPLIT_ORDER[r["split"]], r["half"],
                                       int(r["release_timestamp"]), r["video_id"]))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in COLUMNS})
    return buf.getvalue().encode("utf-8")


def counts(rows: list, key: str) -> dict:
    out: dict = {}
    for r in rows:
        out[str(r[key])] = out.get(str(r[key]), 0) + 1
    return dict(sorted(out.items()))


def random_guess(n_ref: int) -> dict:
    """Analytic chance that a uniform random start lands within +-30 s of truth."""
    out = {}
    for scope, days in SCOPE_HALF_DAYS.items():
        for m in EXCERPT_LENGTHS_MIN:
            n_frames = 60 * m - 15          # 16 s window, 1 s hop
            if days is None:
                npos = n_ref - n_frames + 1
            else:
                npos = 2 * days * 86400 + 1  # unclipped scope
            p = (2 * HIT_TOL_S + 1) / npos
            out[f"{scope}_{m}min"] = {"positions": int(npos), "p_hit": p, "one_in": round(1 / p, 1)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--india", type=Path, default=REPO / "data" / "candidates_india.csv")
    ap.add_argument("--negatives", type=Path, default=REPO / "data" / "candidates_negatives.csv")
    ap.add_argument("--derived-dir", type=Path, required=True)
    ap.add_argument("--time-axis", type=Path, default=REPO / "results" / "time_axis_check.json")
    ap.add_argument("--out-csv", type=Path, default=REPO / "data" / "splits.csv")
    ap.add_argument("--summary", type=Path, default=REPO / "results" / "splits_summary.json")
    ap.add_argument("--verify-protocol", type=Path, default=None,
                    help="after writing, check that PROTOCOL.md quotes the current SHA-256 of "
                         "splits.csv and of every frozen code file; exit 1 if not")
    args = ap.parse_args(argv)

    ref = gref.load(args.derived_dir, mmap=True, with_source=True)
    if ref.source is None:
        raise SystemExit("grid_1s_src_u8.npy is needed for the file-handover rule")
    time_axis = json.loads(args.time_axis.read_text(encoding="utf-8"))
    drift = drift_table(time_axis)

    india_raw = pd.read_csv(args.india, dtype={"video_id": str})
    neg_raw = pd.read_csv(args.negatives, dtype={"video_id": str})
    # the CSV's own IST strings must agree with the conversion used here
    for d in (india_raw, neg_raw):
        conv = d["release_timestamp"].map(
            lambda u: gref.unix_to_ist(int(u)).strftime("%Y-%m-%dT%H:%M:%S+05:30"))
        bad = int((conv != d["release_time_ist"]).sum())
        if bad:
            raise SystemExit(f"{bad} rows: release_time_ist disagrees with release_timestamp")

    india = annotate(india_raw, ref, ref.source, drift, "india")
    neg = annotate(neg_raw, ref, ref.source, drift, "negative")
    rng = np.random.default_rng(SEED)
    picked, syn, notes, stats = draw(india, neg, len(ref), ref.t0, rng)

    rows = to_rows(india, picked) + to_rows(neg, picked) + syn
    data = csv_bytes(rows)
    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out_csv.with_suffix(".csv.tmp")
    tmp.write_bytes(data)
    tmp.replace(args.out_csv)
    digest = hashlib.sha256(data).hexdigest()

    by_split = {s: [r for r in rows if r["split"] == s] for s in SPLIT_ORDER}
    india_elig = india[india["exclude_reason"] == ""]
    summary = {
        "what": "Pre-registered splits drawn from metadata only (no audio existed). "
                "Written by scripts/make_splits.py.",
        "seed": SEED,
        "splits_csv": str(args.out_csv.relative_to(REPO)).replace("\\", "/")
        if args.out_csv.is_relative_to(REPO) else str(args.out_csv),
        "splits_csv_sha256": digest,
        "splits_csv_rows": len(rows),
        "inputs_sha256": {
            "candidates_india.csv": sha256_file(args.india),
            "candidates_negatives.csv": sha256_file(args.negatives),
            "time_axis_check.json": sha256_file(args.time_axis),
            "grid_1s_meta.json": sha256_file(args.derived_dir / gref.META_NAME),
            "grid_1s_valid_u8.npy": sha256_file(args.derived_dir / gref.VALID_NAME),
            "grid_1s_src_u8.npy": sha256_file(args.derived_dir / gref.SRC_NAME),
        },
        "frozen_code_sha256": {f: sha256_file(REPO / f) for f in FROZEN_CODE},
        "rules": {
            "min_duration_s": MIN_DURATION_S, "archive_cap_exclusion_s": ARCHIVE_CAP_S,
            "download_s": DOWNLOAD_S, "excerpt_start_s": EXCERPT_START_S,
            "excerpt_lengths_min": list(EXCERPT_LENGTHS_MIN), "long_stream_s": LONG_S,
            "min_valid_fraction": MIN_VALID, "junction_margin_s": JUNCTION_MARGIN_S,
            "label_stable_min_r": LABEL_STABLE_R, "drift_running_median_windows": DRIFT_MEDIAN_WINDOWS,
            "targets": {"dev": DEV_TARGET, "calibration": CAL_TARGET, "test": TEST_TARGET,
                        "test_per_channel_cap": TEST_PER_CHANNEL, "negatives": NEG_TARGET,
                        "synthetic_per_kind": SYN_PER_KIND},
            "unverified_channels": UNVERIFIED,
            "court_title_regex": COURT_RE.pattern,
        },
        "reference": {"t0_ist": str(ref.t0), "length_s": len(ref)},
        "india_candidates": {
            "total": int(len(india)),
            "excluded_by_reason": {k: int(v) for k, v in
                                   india["exclude_reason"].replace("", "eligible")
                                   .value_counts().sort_index().items()},
            "eligible": int(len(india_elig)),
            "eligible_label_stable": int(india_elig["label_stable"].sum()),
            "eligible_label_stable_long_verified": int(
                (india_elig["label_stable"] & (india_elig["duration_s"] >= LONG_S)
                 & india_elig["verified"]).sum()),
            "eligible_channels": int(india_elig["channel_id"].nunique()),
        },
        "negative_candidates": {
            "total": int(len(neg)),
            "excluded_by_reason": {k: int(v) for k, v in
                                   neg["exclude_reason"].replace("", "eligible")
                                   .value_counts().sort_index().items()},
        },
        "drawing_stats": stats,
        "shortfalls_and_notes": notes,
        "splits": {},
        "analytic_random_guess": random_guess(len(ref)),
        "wilson_upper_95_if_zero_false_matches": {},
    }
    for s, rs in by_split.items():
        info = {"n": len(rs), "by_channel": counts(rs, "channel"),
                "by_venue_type": counts(rs, "venue_type"),
                "by_licence": counts(rs, "licence"),
                "by_half": counts(rs, "half") if s.startswith("negative") else None,
                "by_country": counts(rs, "country") if s.startswith("negative") else None,
                "by_excerpt_lengths": counts(rs, "excerpt_lengths_min"),
                "total_hours": round(sum(int(r["duration_s"]) for r in rs) / 3600, 1)}
        if s in ("dev", "calibration", "test"):
            info["label_stable"] = sum(1 for r in rs if r["label_stable"] is True)
            info["unverified_channel_streams"] = sum(
                1 for r in rs if r["official_status"] != "verified by metadata step")
            info["by_ref_file"] = counts(rs, "ref_src_file")
            dr = [abs(float(r["label_drift_s"])) for r in rs]
            info["abs_label_drift_s"] = ({"median": float(np.median(dr)), "max": float(max(dr))}
                                         if dr else None)
            info["abs_label_drift_gt_30s"] = sum(1 for d in dr if d > HIT_TOL_S)
            info["min_ref_valid_frac_span"] = (min(float(r["ref_valid_frac_span"]) for r in rs)
                                               if rs else None)
            info["n_channels"] = len({r["channel_id"] for r in rs})
            info["max_per_channel"] = max(counts(rs, "channel_id").values()) if rs else 0
            info["n_long_ge_62min"] = sum(1 for r in rs if int(r["duration_s"]) >= LONG_S)
        if s == "dev":
            info["streams"] = [{"video_id": r["video_id"], "channel": r["channel"],
                                "venue_type": r["venue_type"], "licence": r["licence"],
                                "release_time_ist": r["release_time_ist"],
                                "select_rule": r["select_rule"]} for r in rs]
        summary["splits"][s] = info
    # per excerpt length: how many report-half negatives can give a trial
    for s in ("negative", "negative_synthetic"):
        rep = [r for r in by_split[s] if r["half"] == "report"]
        for m in EXCERPT_LENGTHS_MIN:
            n = sum(1 for r in rep if str(m) in r["excerpt_lengths_min"].split(";"))
            summary["wilson_upper_95_if_zero_false_matches"][f"{s}_report_{m}min"] = {
                "n": n, "upper": round(wilson(0, n)[1], 4) if n else None}
    rep_all = [r for s in ("negative", "negative_synthetic") for r in by_split[s]
               if r["half"] == "report"]
    for m in EXCERPT_LENGTHS_MIN:
        n = sum(1 for r in rep_all if str(m) in r["excerpt_lengths_min"].split(";"))
        summary["wilson_upper_95_if_zero_false_matches"][f"all_report_{m}min"] = {
            "n": n, "upper": round(wilson(0, n)[1], 4) if n else None}
    # trials available per half and excerpt length (one excerpt per stream per length)
    summary["negative_trials_per_half_and_length"] = {
        f"{s}_{h}_{m}min": sum(1 for r in by_split[s] if r["half"] == h
                               and str(m) in r["excerpt_lengths_min"].split(";"))
        for s in ("negative", "negative_synthetic") for h in ("threshold", "report")
        for m in EXCERPT_LENGTHS_MIN}
    summary["test_trials_per_length"] = {
        f"{m}min": sum(1 for r in by_split["test"] if str(m) in r["excerpt_lengths_min"].split(";"))
        for m in EXCERPT_LENGTHS_MIN}
    summary["test_expected_chance_hits_full_scope_60min"] = round(
        summary["splits"]["test"]["n"] * summary["analytic_random_guess"]["full_60min"]["p_hit"], 6)

    args.summary.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.summary.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(summary, indent=1, ensure_ascii=False, default=str) + "\n",
                   encoding="utf-8")
    tmp.replace(args.summary)
    print(f"wrote {args.out_csv} ({len(rows)} rows), sha256 {digest}")
    for s, rs in by_split.items():
        print(f"  {s}: {len(rs)}")
    for n in notes:
        print("  note:", n)
    if args.verify_protocol is not None:
        text = args.verify_protocol.read_text(encoding="utf-8")
        want = {"data/splits.csv": digest, **summary["frozen_code_sha256"]}
        missing = {k: v for k, v in want.items() if v not in text}
        for k, v in want.items():
            print(f"  {'ok     ' if k not in missing else 'MISSING'} {k} {v}")
        if missing:
            print(f"{args.verify_protocol} does not quote the current hash of: "
                  + ", ".join(missing))
            return 1
        print(f"{args.verify_protocol} quotes every current hash")
    return 0


if __name__ == "__main__":
    sys.exit(main())
