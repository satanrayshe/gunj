"""Effective trace noise implied by the 5 Nov feasibility probe.

The probe's audio is not used (and is not on disk). This script reads only
the probe's printed outputs in docs/probe and the built reference series.

Model. Suppose the extracted trace is the true grid frequency y plus white
noise of standard deviation sigma, independent of y. Over an excerpt whose
reference window has standard deviation s (mean removed), the expected
correlation at the true position is

    r = s / sqrt(s**2 + sigma**2)     so     sigma = s * sqrt(1 / r**2 - 1).

The probe printed r at the true lock for several excerpts. This script finds
each excerpt's window in the series, measures s there, and turns the printed r
into sigma_eff. sigma_eff lumps together everything that lowers r: extractor
noise, the extractor's 16 s smoothing and 5-point median, and r being printed
to 2-3 decimals. It is an "equivalent white noise", not a measured noise
spectrum. Real extraction noise is smoothed over about 16 s, so white noise of
the same size is probably easier for the matcher than the real thing.

Alignment. The probe searched file IV alone, whose labels in January 2023 are
198 s later than file V's (results/reference_build_stats.json, junctions). The
series uses file V there. So a file-IV label L is the series label L - 198 s.
In the probe, trace frame i (centred at audio second i + 8) matched reference
second rec_start + i + 8, where rec_start is the printed lock time.

Sources of r (all for the 100 Hz harmonic, k = 2):
  - enf_margin_out.txt: top r for 30/40/60-min excerpts from minute 2, which
    is the true lock (raw reference).
  - enf_hp_out.txt, "k2 raw": rank-1 excerpts only (their top r is the
    true r; raw reference, r printed to 2 decimals).
  - enf_mf_out.txt, "smoothed ref raw": the true r of six excerpts against a
    reference smoothed like the extractor (16 s Hann, then 5-point median).
    For these s is measured on the same smoothed reference.

Usage (from the repo root):
  .venv\\Scripts\\python.exe scripts\\probe_noise.py --ref-dir ..\\data\\derived ^
      --probe-dir docs\\probe --build-stats results\\reference_build_stats.json ^
      --out results\\probe_noise.json
"""
from __future__ import annotations

import gunj  # noqa: F401  (first, so its OPENBLAS_NUM_THREADS default applies)
import argparse
import json
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

from gunj import reference as gref
from gunj.match import ref_segment

EXTRACTOR_OFFSET_S = 8      # trace frame i is centred at audio second i + 8
SMOOTH_WIN = 16             # probe's Hann window length in seconds
MEDIAN_WIN = 5


def iv_to_v_offset(build_stats: dict) -> int:
    for j in build_stats["junctions"]:
        if j["from_file"] == "freq_IV.csv" and j["to_file"] == "freq_V.csv":
            return int(j["label_offset_s"])
    raise SystemExit("IV -> V junction not found in build stats")


def parse_margin(text: str):
    """enf_margin_out.txt lines -> (length, lock label, offset vs YT, r)."""
    pat = re.compile(r"len=(\d+)s: top r=([\d.]+) at (\S+ \S+) \(off vs YT ([+-]\d+)s\)")
    rows = []
    for m in pat.finditer(text):
        rows.append(dict(length=int(m.group(1)), start=120, r=float(m.group(2)),
                         lock_iv=pd.Timestamp(m.group(3)), off_vs_yt=int(m.group(4)),
                         source="enf_margin_out.txt", reference="raw"))
    return rows


def parse_hp_k2raw(text: str):
    """enf_hp_out.txt 'k2 raw' entries with rank 1 -> true r (2 decimals)."""
    rows = []
    for line in text.splitlines():
        if not line.startswith("k2 raw"):
            continue
        length = int(re.search(r"len=(\d+)", line).group(1))
        for m in re.finditer(r"(\d+):rank(\d+)\(top r ([\d.]+), 2nd ([\d.]+), off ([+-]\d+)s\)", line):
            if int(m.group(2)) == 1:
                rows.append(dict(length=length, start=int(m.group(1)), r=float(m.group(3)),
                                 off_vs_yt=int(m.group(5)), source="enf_hp_out.txt (k2 raw)",
                                 reference="raw"))
    return rows


def parse_mf_smoothed(text: str):
    """enf_mf_out.txt 'smoothed ref raw' -> true r against the smoothed reference."""
    rows = []
    for line in text.splitlines():
        if not line.startswith("smoothed ref raw:"):
            continue
        for m in re.finditer(r"(\d+)m@(\d+): rank (\d+), true r ([\d.]+)", line):
            rows.append(dict(length=int(m.group(1)) * 60, start=int(m.group(2)),
                             r=float(m.group(4)), rank=int(m.group(3)), off_vs_yt=189,
                             source="enf_mf_out.txt (smoothed ref raw)", reference="smoothed"))
    return rows


def parse_outcomes(pdir: Path):
    """Real-clip outcomes printed by the probe (one stream), for comparison only.

    - enf_hp_out.txt 'k2 raw': rank of the true start per excerpt, search over
      file IV only. Counted as a hit when rank == 1.
    - enf_margin_out.txt: the top lock of each excerpt and its offset from
      YouTube's start; a hit when the lock agrees with the other excerpts'
      locks (all within +-30 s of +189 s).
    - enf_claimed_out.txt: +-1 day search, hits within 30 s as printed.
    """
    out = []
    for line in (pdir / "enf_hp_out.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("k2 raw"):
            length = int(re.search(r"len=(\d+)", line).group(1))
            ranks = [int(m.group(1)) for m in re.finditer(r"\d+:rank(\d+)\(", line)]
            out.append(dict(source="enf_hp_out.txt (k2 raw)", scope="file IV only", length_s=length,
                            hits=sum(r == 1 for r in ranks), n=len(ranks), ranks=ranks))
    for m in re.finditer(r"len=(\d+)s: top r=[\d.]+ at \S+ \S+ \(off vs YT ([+-]\d+)s\)",
                         (pdir / "enf_margin_out.txt").read_text(encoding="utf-8")):
        off = int(m.group(2))
        out.append(dict(source="enf_margin_out.txt", scope="file IV only", length_s=int(m.group(1)),
                        hits=int(abs(off - 189) <= 30), n=1, offset_vs_youtube_s=off))
    for m in re.finditer(r"claimed\+-1day len=(\d+)s: (\d+)/(\d+) excerpts within 30 s \(search space (\d+) s\)",
                         (pdir / "enf_claimed_out.txt").read_text(encoding="utf-8")):
        out.append(dict(source="enf_claimed_out.txt", scope="+-1 day", length_s=int(m.group(1)),
                        hits=int(m.group(2)), n=int(m.group(3)), search_space_s=int(m.group(4))))
    snr = [dict(minute=int(m.group(1)), median_peak_to_floor=float(m.group(2))) for m in
           re.finditer(r"minute\s+(\d+): median snr ([\d.]+)",
                       (pdir / "enf_margin_out.txt").read_text(encoding="utf-8"))]
    return out, snr


def smooth_like_probe(y: np.ndarray) -> np.ndarray:
    """ys[i] = Hann-weighted mean of y[i:i+16], then a centred 5-point median."""
    h = np.hanning(SMOOTH_WIN)
    h /= h.sum()
    ys = np.convolve(y, h, mode="valid")          # ys[i] covers y[i:i+16]
    return pd.Series(ys).rolling(MEDIAN_WIN, center=True, min_periods=1).median().to_numpy()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref-dir", required=True)
    ap.add_argument("--probe-dir", required=True)
    ap.add_argument("--build-stats", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    ref = gref.load(args.ref_dir, mmap=True)
    pdir = Path(args.probe_dir)
    stats = json.loads(Path(args.build_stats).read_text(encoding="utf-8"))
    shift = iv_to_v_offset(stats)

    margin_rows = parse_margin((pdir / "enf_margin_out.txt").read_text(encoding="utf-8"))
    if not margin_rows:
        raise SystemExit("could not parse enf_margin_out.txt")
    # YouTube start (IST) as the probe saw it: lock minus offset.
    yt = {(r["lock_iv"] - pd.Timedelta(seconds=r["off_vs_yt"])) for r in margin_rows}
    if len(yt) != 1:
        raise SystemExit(f"inconsistent YouTube start in probe output: {yt}")
    yt_start = yt.pop()

    rows = margin_rows + parse_hp_k2raw((pdir / "enf_hp_out.txt").read_text(encoding="utf-8")) \
        + parse_mf_smoothed((pdir / "enf_mf_out.txt").read_text(encoding="utf-8"))

    out_rows = []
    for r in rows:
        lock_iv = yt_start + pd.Timedelta(seconds=r["off_vs_yt"])
        lock_series = lock_iv - pd.Timedelta(seconds=shift)
        i_rec = ref.index_of(lock_series)
        L, s0 = r["length"], r["start"]
        a = i_rec + s0 + EXTRACTOR_OFFSET_S           # raw second matched by frame s0
        if r["reference"] == "raw":
            y, ok = ref_segment(ref.freq, ref.valid, a, a + L)
        else:
            # smoothed frame j covers raw seconds [i_rec + j, i_rec + j + 16)
            b = i_rec + s0
            y_raw, ok_raw = ref_segment(ref.freq, ref.valid, b - MEDIAN_WIN, b + L + SMOOTH_WIN + MEDIAN_WIN)
            ys = smooth_like_probe(y_raw)
            y = ys[MEDIAN_WIN:MEDIAN_WIN + L]
            ok = ok_raw[MEDIAN_WIN:MEDIAN_WIN + L]
        win0 = a if r["reference"] == "raw" else i_rec + s0
        s = float(np.std(y))
        rv = r["r"]
        sigma = s * np.sqrt(1.0 / rv ** 2 - 1.0)
        # r is printed rounded; carry the rounding through to sigma
        dec = 2 if r["source"].startswith("enf_hp") else 3
        half = 0.5 * 10 ** (-dec)
        sig_lo = s * np.sqrt(1.0 / min(rv + half, 0.999999) ** 2 - 1.0)
        sig_hi = s * np.sqrt(1.0 / (rv - half) ** 2 - 1.0)
        out_rows.append({
            "source": r["source"], "reference": r["reference"],
            "excerpt_length_s": L, "excerpt_start_audio_s": s0,
            "r_printed": rv, "r_decimals": dec,
            "lock_label_file_iv": str(lock_iv), "lock_label_series": str(lock_series),
            "series_window_start_index": int(win0),
            "series_window_start_ist": str(ref.time_of(win0)),
            "window_valid_share": round(float(np.mean(ok)), 4),
            "ref_window_std_hz": round(s, 6),
            "sigma_eff_hz": round(float(sigma), 6),
            "sigma_eff_range_from_rounding_hz": [round(float(sig_lo), 6), round(float(sig_hi), 6)],
        })

    outcomes, snr = parse_outcomes(pdir)
    sig_raw = np.array([o["sigma_eff_hz"] for o in out_rows if o["reference"] == "raw"])
    sig_all = np.array([o["sigma_eff_hz"] for o in out_rows])
    res = {
        "what": "Equivalent white trace noise implied by the 5 Nov probe's printed r at the true lock. "
                "Derived from docs/probe/*_out.txt and the reference series; no audio used.",
        "model": "sigma_eff = s * sqrt(1/r^2 - 1), s = std of the reference over the excerpt's matched window",
        "alignment": {"youtube_start_ist_from_probe": str(yt_start),
                      "file_iv_minus_series_label_offset_s": shift,
                      "extractor_frame_offset_s": EXTRACTOR_OFFSET_S},
        "rows": out_rows,
        "probe_outcomes": outcomes,
        "probe_scope_note": "file IV only, 13,651,200 rows (results/reference_summary.md). That is between "
                            "the +-90 d (15,552,001 positions) and full scopes of M4.",
        "probe_median_peak_to_floor_100hz_by_10min": snr,
        "summary": {
            "n_raw_reference_points": int(sig_raw.size),
            "sigma_eff_raw_min_hz": round(float(sig_raw.min()), 6),
            "sigma_eff_raw_median_hz": round(float(np.median(sig_raw)), 6),
            "sigma_eff_raw_max_hz": round(float(sig_raw.max()), 6),
            "n_all_points": int(sig_all.size),
            "sigma_eff_all_min_hz": round(float(sig_all.min()), 6),
            "sigma_eff_all_median_hz": round(float(np.median(sig_all)), 6),
            "sigma_eff_all_max_hz": round(float(sig_all.max()), 6),
        },
        "caveats": [
            "One stream only (the probe clip, 11 Jan 2023). Its 100 Hz peak-to-floor ratio per 10 minutes is in probe_median_peak_to_floor_100hz_by_10min.",
            "sigma_eff is an equivalent white noise. The real trace error is smoothed over about 16 s, so it has fewer independent samples than white noise of the same size.",
            "r values from enf_hp_out.txt are printed to 2 decimals; the rounding range is given per row.",
        ],
    }
    tmp = Path(args.out).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(res, indent=1), encoding="utf-8")
    os.replace(tmp, args.out)
    for o in out_rows:
        print(f'{o["source"]:36s} L={o["excerpt_length_s"]:5d} start={o["excerpt_start_audio_s"]:5d} '
              f'r={o["r_printed"]:.3f} s={o["ref_window_std_hz"]:.5f} sigma={o["sigma_eff_hz"]:.5f} '
              f'{o["sigma_eff_range_from_rounding_hz"]} valid={o["window_valid_share"]}')
    print(json.dumps(res["summary"], indent=1))


if __name__ == "__main__":
    main()
