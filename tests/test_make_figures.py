"""scripts/make_figures.py on toy data.

Everything here is SYNTHETIC or made up: toy result rows with invented stream IDs
(SYN-A ...), a generated random-walk reference, traces cut from it with generated
noise, and a toy uniqueness table. No Test result, real trace or real grid data is
read, so these tests do not depend on a scoring run in progress.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import eval_common as ec        # noqa: E402
import make_figures as mf       # noqa: E402
from conftest import SYNTH_T0, write_synth_ref_dir  # noqa: E402
from gunj.match_v2 import wncc_search               # noqa: E402

T0 = pd.Timestamp(SYNTH_T0)
NF = {10: 585, 20: 1185, 40: 2385, 60: 3585}       # frames in an excerpt trace
COLS = ["mode", "video_id", "split", "venue_type", "label_drift_s", "label_stable", "harvested_by_decision_time",
        "extractor", "excerpt_min", "scope", "search_error", "top1_start_ist", "r1", "confidence", "tau",
        "confidence_ge_tau", "verdict", "error_s", "hit_30s", "hit_120s", "hit_600s", "outcome"]
TAU = 0.1


def _row(vid, ext, m, scope, error, conf, hum=True, **kw):
    """One toy row of test_results.csv, with the hit columns that evaluate.py would write."""
    r = {c: "" for c in COLS}
    r.update(mode="real", video_id=vid, split="test", venue_type="lecture", label_drift_s="40.0",
             label_stable="True", harvested_by_decision_time="True", extractor=ext, excerpt_min=str(m),
             scope=scope, tau=repr(TAU))
    if error is None:
        r.update(search_error="ValueError: trace is constant; nothing to correlate", verdict="no verdict",
                 outcome="no verdict", confidence_ge_tau="False")
    else:
        ge = conf >= TAU
        match = ge and hum
        hit = abs(error) <= 30
        r.update(error_s=str(error), confidence=repr(conf), confidence_ge_tau=str(ge),
                 hit_30s=str(hit), hit_120s=str(abs(error) <= 120), hit_600s=str(abs(error) <= 600),
                 verdict="match" if match else "no verdict",
                 outcome=("confident hit" if hit else "confident wrong") if match else "no verdict",
                 r1="0.500000", top1_start_ist="2022-03-01 00:00:00")
    r.update(kw)
    return r


def test_wilson_is_the_protocol_formula():
    assert mf.Z95 == ec.Z95
    for k, n in ((0, 25), (7, 19), (14, 23), (23, 23), (0, 0)):
        assert mf.wilson(k, n) == ec.wilson(k, n)
    lo, hi = mf.wilson(0, 25)                      # results/splits_summary.json: upper bound 13.32%
    assert lo == 0.0 and round(hi, 4) == 0.1332
    assert mf.fmt_rate(mf.rate(0, 0)) == "n/a (N = 0)"
    assert mf.fmt_rate(mf.rate(14, 23)).startswith("14/23 (60.9%, 95% CI ")


def test_error_classes_come_from_the_hit_columns():
    rows = [_row("SYN-A", "v2", 60, "full", e, 0.5) for e in (0, -30, 31, 120, -121, 600, 601, -5000)]
    rows.append(_row("SYN-B", "v2", 60, "full", None, 0.0))
    assert [mf.error_class(r) for r in rows] == [
        "within_30", "within_30", "from_30_to_120", "from_30_to_120", "from_120_to_600", "from_120_to_600",
        "beyond_600", "beyond_600", "no_candidate"]
    s = mf.split_counts(rows)
    assert (s["n"], s["within_30"], s["from_30_to_120"], s["from_120_to_600"], s["beyond_600"],
            s["no_candidate"]) == (9, 2, 2, 2, 2, 1)
    assert s["within_30_share"]["k"] == 2 and s["within_120_share"]["k"] == 4 and s["within_600_share"]["k"] == 6
    assert s["within_600_share"]["n"] == 9          # an excerpt with no candidate stays in the denominator
    assert mf.column_mismatches(rows) == 0
    rows[2]["hit_30s"] = "True"                     # |e| = 31 marked as a hit: caught
    assert mf.column_mismatches(rows) == 1


def test_breakdown_must_agree_with_evaluation_json():
    rows = [_row("SYN-A", ext, m, s, 5, 0.5) for ext in mf.EXTRACTORS for m in mf.LENGTHS for s in mf.SCOPES]
    rows += [_row("SYN-B", ext, m, s, 45, 0.5) for ext in mf.EXTRACTORS for m in mf.LENGTHS for s in mf.SCOPES]
    bd = mf.breakdown(rows)
    cell = bd["v2"]["60"]["full"]
    assert cell["tau"] == TAU and cell["confident"]["n"] == 2 and cell["confident"]["from_30_to_120"] == 1
    ev = {"m2": {e: {str(m): {s: {"n": 2, "top1_hit": {"k": 1}, "confident_hit": {"k": 1},
                                  "confident_wrong": {"k": 1}} for s in mf.SCOPES} for m in mf.LENGTHS}
                 for e in mf.EXTRACTORS}}
    assert mf.check_against_evaluation(bd, ev) == 36
    ev["m2"]["v1"]["20"]["90d"]["confident_wrong"]["k"] = 0
    with pytest.raises(mf.FigureError):
        mf.check_against_evaluation(bd, ev)


def test_confidence_bins_are_half_open_as_pre_registered():
    assert [mf.conf_bin(c) for c in (0.0, 0.0249, 0.025, 0.05, 0.0999, 0.1, 0.2, 0.3999, 0.4, 7.0)] == \
        [0, 0, 1, 2, 2, 3, 4, 4, 5, 5]
    assert mf.bin_label(0) == "[0, 0.025)" and mf.bin_label(5) == "[0.4, inf)"
    rows = [_row("SYN-A", "v2", 60, "full", 0, 0.03), _row("SYN-B", "v2", 60, "full", 900, 0.03),
            _row("SYN-C", "v2", 60, "full", -10, 0.5), _row("SYN-D", "v2", 60, "full", None, 0.0)]
    rel = mf.reliability(rows)
    assert rel["n_excerpts"] == 4 and rel["n_without_confidence"] == 1
    assert [(b["k"], b["n"]) for b in rel["bins"]] == [(0, 0), (1, 2), (0, 0), (0, 0), (0, 0), (1, 1)]
    assert rel["confidence_hits"] == [0.03, 0.5] and rel["confidence_misses"] == [0.03]
    x = mf.conf_pos(np.array([0.0, 0.0125, 0.025, 0.05, 0.4, 0.8]))
    assert np.allclose(x, [0, 0.5, 1, 2, 5, 6])    # equal width for every finite bin


def test_negatives_are_split_into_real_and_synthetic():
    fm = {"cells": {"v2": {"60": {"full": {"tau": 0.08, "trials": [
        {"video_id": "SYN-NOISE-01", "kind": "SYNTHETIC", "confidence": 0.02, "false_match": False,
         "false_match_hum": False},
        {"video_id": "toy-real-1", "kind": "real", "confidence": 0.09, "false_match": True, "false_match_hum": False},
        {"video_id": "toy-real-2", "kind": "real", "confidence": None, "false_match": False,
         "false_match_hum": False}]}}}}}
    neg = mf.negatives_of(fm, "v2", 60, "full")
    assert neg["tau"] == 0.08
    assert neg["real"] == {"n": 2, "n_without_confidence": 1, "confidence": [0.09], "n_at_or_above_tau": 1,
                           "n_at_or_above_tau_with_hum_flag": 0}
    assert neg["SYNTHETIC"]["n"] == 1 and neg["SYNTHETIC"]["confidence"] == [0.02]


def test_signed_error_axis_keeps_hits_inside_the_band():
    e = np.array([-84_000_000, -601, -31, -30, -7, 0, 6, 30, 31, 86_400, 84_000_000])
    t = mf.sym_pos(e)
    assert np.all(np.diff(t) > 0)                   # monotone
    assert np.allclose(mf.sym_pos([-30.5, 30.5]), [-mf.LIN_HALF, mf.LIN_HALF])
    edges = mf.sym_edges(84_046_325)
    assert np.allclose(np.diff(edges), mf.BIN_W) and edges[0] == -edges[-1] and edges[-1] >= t[-1]
    h = mf.error_histogram(e, edges)
    assert int(h["counts"].sum()) == e.size
    assert int(h["counts"][h["in_band"]].sum()) == int((np.abs(e) <= 30).sum()) == 5
    assert int(h["in_band"].sum()) == mf.N_LIN_BINS


def test_quartiles_match_numpy_and_rank_undefined_values_last():
    v = [3.0, 1.0, 4.0, 1.5, 9.0, 2.6]
    q = mf.quartiles(v)
    assert q["n"] == 6 and q["n_undefined"] == 0
    assert np.allclose([q["min"], q["q1"], q["median"], q["q3"], q["max"]], np.percentile(v, [0, 25, 50, 75, 100]))
    q = mf.quartiles([1.0, 2.0, None])
    assert q["median"] == 2.0 and q["max"] is None and q["q3"] is None and q["n_undefined"] == 1
    assert mf.quartiles([]) is None
    assert mf.per_stream([{"video_id": "SYN-A", "s": 1.0}, {"video_id": "SYN-A", "s": 3.0},
                          {"video_id": "SYN-B", "s": None}], "s") == {"SYN-A": 2.0, "SYN-B": None}


def test_sigma_eff_recovers_synthetic_noise(synth_ref_small):
    ref_f, ref_v = synth_ref_small                  # SYNTHETIC reference
    idx, n, sigma = 50_000, 3585, 0.02
    rng = np.random.default_rng(5)
    trace = ref_f[idx:idx + n].astype(float) + sigma * rng.standard_normal(n)      # SYNTHETIC white noise
    en = mf.excerpt_noise(ref_f, ref_v, idx, trace, np.ones(n), r_scored=0.9)
    assert en["n_frames"] == n and en["window_valid_share"] == 1.0
    assert abs(en["sigma_B_hz"] - sigma) < 0.1 * sigma          # the model of scripts/probe_noise.py
    assert abs(en["r_weighted_recomputed"] - en["r_unweighted"]) < 1e-12           # unit weights
    assert math.isclose(en["sigma_A_hz"], en["ref_window_std_hz"] * math.sqrt(1 / 0.81 - 1))
    assert mf.sigma_eff(0.05, 1.0) == 0.0 and mf.sigma_eff(0.05, 0.0) is None and mf.sigma_eff(0.05, -0.2) is None


TABLE = [  # toy uniqueness table: SYNTHETIC numbers made up for the test
    {"scope": "full", "length_min": "5", "noise_kind": "white", "sigma_mhz": "0.0", "N": "100", "hits": "100"},
    {"scope": "full", "length_min": "10", "noise_kind": "white", "sigma_mhz": "0.0", "N": "100", "hits": "100"},
    {"scope": "full", "length_min": "20", "noise_kind": "white", "sigma_mhz": "0.0", "N": "100", "hits": "100"},
    {"scope": "full", "length_min": "5", "noise_kind": "hann16", "sigma_mhz": "10.0", "N": "100", "hits": "20"},
    {"scope": "full", "length_min": "10", "noise_kind": "hann16", "sigma_mhz": "10.0", "N": "100", "hits": "80"},
    {"scope": "full", "length_min": "20", "noise_kind": "hann16", "sigma_mhz": "10.0", "N": "100", "hits": "100"},
    {"scope": "full", "length_min": "5", "noise_kind": "hann16", "sigma_mhz": "20.0", "N": "100", "hits": "0"},
    {"scope": "full", "length_min": "10", "noise_kind": "hann16", "sigma_mhz": "20.0", "N": "100", "hits": "40"},
    {"scope": "full", "length_min": "20", "noise_kind": "hann16", "sigma_mhz": "20.0", "N": "100", "hits": "90"},
]


def test_uniqueness_table_is_read_by_linear_interpolation():
    assert mf.table_levels(TABLE, "hann16") == [0.0, 10.0, 20.0]        # the 0 mHz control is shared
    rd = mf.read_uniqueness(TABLE, "hann16", "full", 5.0)
    assert rd["bracket_mhz"] == [0.0, 10.0]
    assert [round(rd["rates_by_length_min"][m]["rate"], 6) for m in ("5", "10", "20")] == [0.6, 0.9, 1.0]
    assert rd["shortest_length_min"] == 10 and rd["previous_tested_min"] == 5
    rd = mf.read_uniqueness(TABLE, "hann16", "full", 15.0)
    assert [round(rd["rates_by_length_min"][m]["rate"], 6) for m in ("5", "10", "20")] == [0.1, 0.6, 0.95]
    assert rd["shortest_length_min"] == 20 and rd["previous_tested_min"] == 10
    rd = mf.read_uniqueness(TABLE, "hann16", "full", 20.0)              # exactly on a pre-set level
    assert rd["bracket_mhz"] == [20.0, 20.0] and rd["rates_by_length_min"]["20"]["rate"] == 0.9
    rd = mf.read_uniqueness(TABLE, "hann16", "full", 25.0)              # above the largest level: not read
    assert rd["shortest_length_min"] is None and rd["rates_by_length_min"] == {} and "not read" in rd["status"]
    assert mf.read_uniqueness(TABLE, "hann16", "full", None)["shortest_length_min"] is None


def test_swarm_is_deterministic_and_keeps_markers_apart():
    x = np.array([0.0, 0.5, 1.0, 1.2, 30.0, 30.1, 30.2, 30.3])
    y = mf.swarm(x, 4.0)
    assert np.array_equal(y, mf.swarm(x, 4.0))
    for i in range(x.size):
        for j in range(i):
            assert (x[i] - x[j]) ** 2 + (y[i] - y[j]) ** 2 >= 16.0 - 1e-6
    assert y[4] == 0.0 and mf.swarm([], 4.0).size == 0


def test_abs_path_guard(tmp_path):
    mf.check_no_abs_paths("results/figures/x.png, tau = 0.08, A: r as scored, https://example.org/a")
    with pytest.raises(mf.FigureError):
        mf.check_no_abs_paths("see D:" + "\\" + "work" + "\\" + "x.png")
    with pytest.raises(mf.FigureError):
        mf.write_text(tmp_path / "x.md", f"written to {tmp_path}", repo=tmp_path)
    assert not list(tmp_path.glob("x.md*"))


# ------------------------------------------------------------- end to end

def _toy_repo(tmp_path, ref_f, ref_v):
    """A toy repo: six made-up streams, a SYNTHETIC reference, and every file make_figures reads."""
    repo = tmp_path / "repo"
    res = repo / "results"
    (res / "search_cache" / "test").mkdir(parents=True)
    (repo / "traces" / "test").mkdir(parents=True)
    (repo / "data").mkdir()
    ref_dir = write_synth_ref_dir(tmp_path / "ref", ref_f, ref_v)
    rng = np.random.default_rng(3)
    # SYN-A locks at every length: its v2 traces are the SYNTHETIC reference plus SYNTHETIC noise
    idx, arrays, searches, r_of = 60_000, {}, [], {}
    for m, n in NF.items():
        x = ref_f[idx:idx + n].astype(np.float64) + 0.01 * rng.standard_normal(n)
        u = (0.5 + rng.random(n)).astype(np.float32)
        top = wncc_search(x, u.astype(np.float64), ref_f, ref_v, start=idx - 200, stop=idx + 200).top
        assert top.index == idx
        arrays[f"v2__x{m}__freq"], arrays[f"v2__x{m}__fw"] = x, u
        r_of[m] = top.r
        for scope in ("full", "90d", "1d"):
            searches.append({"ext": "v2", "length_min": m, "scope": scope, "n_frames": n, "candidates": [
                {"index": idx, "start_index": idx - mf.FRAME0_S, "r": top.r,
                 "start_ist": str(T0 + pd.Timedelta(seconds=idx - mf.FRAME0_S))}]})
    np.savez(repo / "traces" / "test" / "SYN-A.npz", **arrays)
    (res / "search_cache" / "test" / "SYN-A.json").write_text(json.dumps({"video_id": "SYN-A", "searches": searches}),
                                                             encoding="utf-8")
    toy = {"SYN-A": (2, 0.9), "SYN-B": (45, 0.6), "SYN-C": (-300, 0.3), "SYN-D": (5000, 0.2),
           "SYN-E": (-70_000, 0.01), "SYN-F": (None, 0.0)}
    rows = []
    for vid, (err, conf) in toy.items():
        for ext in mf.EXTRACTORS:
            for m in mf.LENGTHS:
                for scope in mf.SCOPES:
                    kw = {}
                    if vid == "SYN-A" and ext == "v2":
                        kw = {"r1": f"{r_of[m]:.6f}",
                              "top1_start_ist": str(T0 + pd.Timedelta(seconds=idx - mf.FRAME0_S))}
                    rows.append(_row(vid, ext, m, scope, err, conf, **kw))
    with open(res / "test_results.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    bd = mf.breakdown(mf.searched_rows(rows))          # the toy m2 block, consistent with the toy rows
    m2 = {e: {str(m): {s: {"n": bd[e][str(m)][s]["top1"]["n"],
                           "top1_hit": {"k": bd[e][str(m)][s]["top1"]["within_30"]},
                           "confident_hit": {"k": bd[e][str(m)][s]["confident"]["within_30"]},
                           "confident_wrong": {"k": bd[e][str(m)][s]["confident"]["n"]
                                               - bd[e][str(m)][s]["confident"]["within_30"]}}
                       for s in mf.SCOPES} for m in mf.LENGTHS} for e in mf.EXTRACTORS}
    (res / "evaluation.json").write_text(json.dumps({
        "mode": "real", "written_ist": "2026-11-11T00:00:00+05:30", "m2": m2,
        "m3_counts": {"real": 2, "SYNTHETIC": 1, "harvested_not_yet_searched": 0}}), encoding="utf-8")
    trials = [{"video_id": "toy-real-1", "kind": "real", "confidence": 0.004, "hum_flag": False,
               "false_match": False, "false_match_hum": False},
              {"video_id": "toy-real-2", "kind": "real", "confidence": 0.03, "hum_flag": True,
               "false_match": False, "false_match_hum": False},
              {"video_id": "SYN-NOISE-01", "kind": "SYNTHETIC", "confidence": 0.012, "hum_flag": False,
               "false_match": False, "false_match_hum": False}]
    (res / "false_matches.json").write_text(json.dumps({
        "written_ist": "2026-11-11T00:00:00+05:30",
        "counts": {"real": 2, "SYNTHETIC": 1, "harvested_not_yet_searched": 0},
        "cells": {e: {str(m): {s: {"tau": TAU, "trials": trials} for s in mf.SCOPES} for m in mf.LENGTHS}
                  for e in mf.EXTRACTORS}}), encoding="utf-8")
    (res / "gate_frozen.json").write_text(json.dumps(
        {"gate": {"by_extractor": {"v2": {"locked_streams": ["SYN-A"]}}}}), encoding="utf-8")
    with open(res / "uniqueness_table.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(TABLE[0]), lineterminator="\n")
        w.writeheader()
        for scope in mf.SCOPES:
            w.writerows([dict(r, scope=scope) for r in TABLE])
    with open(repo / "data" / "splits.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["video_id", "ref_src_file"], lineterminator="\n")
        w.writeheader()
        w.writerows([{"video_id": v, "ref_src_file": "freq_II.csv" if v < "SYN-D" else "freq_V.csv"} for v in toy])
    return repo, ref_dir


def test_end_to_end_on_a_toy_repo(tmp_path, synth_ref_small, capsys):
    ref_f, ref_v = synth_ref_small
    repo, ref_dir = _toy_repo(tmp_path, ref_f, ref_v)
    assert mf.main(["--repo", str(repo), "--ref", str(ref_dir)]) == 0
    out = capsys.readouterr().out
    assert "primary cell: 4 verdicts match; 1 within 30 s, 1 from 30 to 120 s, 1 from 120 to 600 s, 1 beyond 600 s" in out
    res, figs = repo / "results", repo / "results" / "figures"
    names = ["m2_signed_error_v2.png", "m2_signed_error_v1.png", "m2_signed_error_naive.png",
             "m3_reliability_v2_60min_full.png", "m3_reliability_v2_all_cells.png",
             "confident_verdict_breakdown_v2.png", "m4_measured_noise.png", "exploratory_offset_verdicts_v2.png"]
    for name in names:
        data = (figs / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", name
        assert int.from_bytes(data[16:20], "big") == int(mf.FIG_W * mf.DPI), name      # 1800 px wide
    assert not list(res.rglob("*.tmp"))
    bd = json.loads((res / "error_breakdown.json").read_text(encoding="utf-8"))
    c = bd["cells"]["v2"]["60"]["full"]["confident"]
    assert (c["n"], c["within_30"], c["from_30_to_120"], c["from_120_to_600"], c["beyond_600"]) == (4, 1, 1, 1, 1)
    assert bd["cells"]["v2"]["60"]["full"]["top1"]["no_candidate"] == 1
    assert bd["checks"] == {"cells_equal_to_evaluation_m2": 36, "rows_with_inconsistent_hit_columns": 0}
    m4 = json.loads((res / "m4_measured.json").read_text(encoding="utf-8"))
    assert m4["streams_that_lock"]["n"] == 1 and m4["streams_that_lock"]["matches_recomputed_list"]
    assert m4["streams_that_lock"]["n_excerpts"] == 4
    a, b = m4["distribution_hz"]["A"]["median"], m4["distribution_hz"]["B"]["median"]
    assert 0.005 < a < 0.02 and 0.005 < b < 0.02        # the SYNTHETIC noise added above was 0.01 Hz
    assert m4["readings"]["A"]["median"]["hann16"]["full"]["bracket_mhz"] in ([0.0, 10.0], [10.0, 20.0])
    ex = json.loads((res / "exploratory_offset_verdicts.json").read_text(encoding="utf-8"))
    assert ex["label"].startswith("EXPLORATORY")
    assert ex["primary_cell"]["within_30"]["n_verdicts"] == 1 and ex["primary_cell"]["offset_30_600"]["n_verdicts"] == 2
    assert [s["video_id"] for s in ex["offset_streams"]] == ["SYN-B", "SYN-C"]
    texts = {p.name: p.read_text(encoding="utf-8") for p in
             (res / "error_breakdown.md", res / "m4_measured.md", res / "exploratory_offset_verdicts.md",
              figs / "README.md", figs / "figures.json", res / "m4_measured.json")}
    assert texts["exploratory_offset_verdicts.md"].startswith("# EXPLORATORY (post hoc)")
    assert "SYNTHETIC" in texts["m4_measured.md"] and "SYNTHETIC" in texts["README.md"]
    assert "EXPLORATORY" in texts["README.md"]
    for name, text in texts.items():
        assert str(tmp_path) not in text and str(tmp_path).replace("\\", "/") not in text, name
        assert chr(0x2014) not in text and chr(0x2013) not in text, name    # no em or en dashes
    first = {p: p.read_bytes() for p in (res / "error_breakdown.md", res / "m4_measured.json", figs / "figures.json")}
    assert mf.main(["--repo", str(repo), "--ref", str(ref_dir)]) == 0       # same inputs, same text
    assert all(p.read_bytes() == b for p, b in first.items())


def test_m4_is_reused_or_reported_as_not_computed_without_the_reference(tmp_path, synth_ref_small):
    ref_f, ref_v = synth_ref_small
    repo, ref_dir = _toy_repo(tmp_path, ref_f, ref_v)
    rows = mf.searched_rows(mf.read_csv(repo / "results" / "test_results.csv"))
    notes = []
    m4, note = mf.run_m4(rows, repo, None, skip=True)
    assert m4 is None and note.startswith("not computed") and "m4_measured.json does not exist" in note
    m4, note = mf.run_m4(rows, repo, tmp_path / "no_such_folder", skip=False, log=notes.append)
    assert m4 is None and note.startswith("not computed") and len(notes) == 1      # reported, not fatal
    m4, note = mf.run_m4(rows, repo, ref_dir, skip=False)
    assert note == "measured" and m4["streams_that_lock"]["video_ids"] == ["SYN-A"]
    mf.write_json(repo / "results" / "m4_measured.json", m4, repo)
    again, note = mf.run_m4(rows, repo, None, skip=True)             # the JSON is reused for text and figure
    assert note == "reused" and again["distribution_hz"] == m4["distribution_hz"]
    assert "SYNTHETIC" in mf.m4_md(again)


def test_refuses_without_a_real_scoring_run(tmp_path, capsys):
    res = tmp_path / "results"
    res.mkdir()
    assert mf.main(["--repo", str(tmp_path)]) == 2
    for name in ("test_results.csv", "false_matches.json"):
        (res / name).write_text("", encoding="utf-8")
    (res / "evaluation.json").write_text(json.dumps({"mode": "practice"}), encoding="utf-8")
    assert mf.main(["--repo", str(tmp_path)]) == 2
    assert "not from a real" in capsys.readouterr().err
    # evaluation.json and false_matches.json from two different runs (one is being rewritten): refused
    (res / "evaluation.json").write_text(json.dumps({"mode": "real", "written_ist": "t1"}), encoding="utf-8")
    (res / "false_matches.json").write_text(json.dumps({"written_ist": "t0", "cells": {}}), encoding="utf-8")
    assert mf.main(["--repo", str(tmp_path)]) == 2
    assert "not from the same scoring run" in capsys.readouterr().err
    assert not (res / "figures").exists()
