"""scripts/m5_edits.py (PROTOCOL M5, edit detection) on toy data.

Everything here is SYNTHETIC: a generated random-walk reference, toy traces read
from it with generated noise, and SYNTHETIC cuts made by skipping a stretch of
the walk. No real audio, trace or grid data is read, and the toy stream IDs
(SYN-A ...) are made up.
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
import m5_edits as m5           # noqa: E402
from conftest import SYNTH_T0, write_synth_ref_dir  # noqa: E402
from gunj import reference as gref                 # noqa: E402

T0 = pd.Timestamp(SYNTH_T0)
N = 1500                 # frames in a toy "60-min" trace (short, to keep the test quick)
LATENCY = 5              # each toy recording starts 5 s after its release time
RESULT_COLS = ["mode", "video_id", "split", "extractor", "excerpt_min", "scope", "outcome",
               "hit_30s", "confidence_ge_tau", "error_s"]


def _edit(t, jump):
    return {"clip_time_s": float(t), "jump_s": float(jump), "kind": "cut" if jump > 0 else "insertion"}


# ------------------------------------------------------------ the M5 rules

def test_detection_window_is_30_s_inclusive():
    assert m5.score_cut([_edit(630.0, 40)], 600.0, 40)["detected"]          # exactly 30 s late
    assert m5.score_cut([_edit(570.0, 40)], 600.0, 40)["detected"]          # exactly 30 s early
    assert not m5.score_cut([_edit(630.5, 40)], 600.0, 40)["detected"]
    none = m5.score_cut([], 600.0, 40)
    assert not none["detected"] and none["duration_error_s"] is None and none["other_edits"] == 0


def test_an_insertion_near_the_cut_is_not_a_reported_cut():
    s = m5.score_cut([_edit(601.0, -40)], 600.0, 40)
    assert not s["detected"] and s["either_kind_within_window"]
    assert s["duration_error_s"] is None and s["other_edits"] == 1


def test_duration_error_uses_the_nearest_reported_cut():
    s = m5.score_cut([_edit(200.0, 15), _edit(590.0, 37), _edit(612.0, 44)], 600.632, 40)
    assert s["detected"] and s["detecting_edit"] == 1
    assert s["reported_jump_s"] == 37 and s["duration_error_s"] == 3
    assert s["position_error_s"] == -10.632
    assert s["other_edits"] == 2


def test_every_edit_on_an_uncut_excerpt_is_a_false_alarm():
    assert m5.false_alarm_count([]) == 0
    assert m5.false_alarm_count([_edit(500.0, 20), _edit(900.0, -12)]) == 2


def test_totals_by_cut_length_with_the_protocol_wilson_interval():
    def cut(length, detected, err=None):
        return {"true_length_s": length, "detected": detected, "duration_error_s": err}
    t = m5.cut_totals([cut(10, True, 1.0), cut(10, False), cut(10, True, 4.0), cut(120, True, 2.0)])
    assert list(t["by_cut_length_s"]) == ["10", "20", "40", "60", "90", "120"]
    ten = t["by_cut_length_s"]["10"]
    assert ten["n_cuts"] == 3 and ten["detected"]["k"] == 2
    assert ten["duration_error_s"] == {"n": 2, "median": 2.5, "max": 4.0, "values": [1.0, 4.0]}
    k, n, z = 2, 3, 1.959964                                   # PROTOCOL section 13, written out
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    assert ten["detected"]["wilson95"] == pytest.approx([centre - half, centre + half])
    empty = t["by_cut_length_s"]["40"]
    assert empty["n_cuts"] == 0 and empty["detected"]["rate"] is None
    assert empty["duration_error_s"]["median"] is None
    assert t["all"]["n_cuts"] == 4 and t["all"]["detected"]["k"] == 3
    assert t["all"]["duration_error_s"]["median"] == 2.0 and t["all"]["duration_error_s"]["max"] == 4.0


def test_stream_rules_read_only_the_primary_cells():
    def row(vid, ext, m, scope, outcome, hit, ge):
        return {"video_id": vid, "split": "test", "extractor": ext, "excerpt_min": str(m), "scope": scope,
                "outcome": outcome, "hit_30s": str(hit), "confidence_ge_tau": str(ge)}
    rows = [row("a", "v2", 60, "full", "confident hit", True, True),
            row("b", "v2", 60, "90d", "confident hit", True, True),      # wrong scope for M5, a gate lock
            row("c", "v1", 60, "full", "confident hit", True, True),     # not the primary extractor
            row("d", "v2", 40, "full", "confident hit", True, True),     # wrong length for M5, a gate lock
            row("e", "v2", 60, "full", "confident wrong", False, True),
            row("f", "v2", 20, "1d", "confident hit", True, True),       # +-1 day is not a gate scope
            row("g", "v2", 10, "full", "no verdict", True, True)]        # a lock without the hum flag
    assert m5.confident_hit_streams(rows) == ["a"]
    assert m5.gate_locks_from_rows(rows) == ["a", "b", "d", "g"]


def test_find_edits_defaults_are_the_frozen_ones():
    assert m5.find_edits_defaults() == {
        "max_shift": 300, "min_seg": 180, "step": 10, "min_jump": 5, "r_min": 0.6, "min_gain": 0.15,
        "max_edits": 3, "relock_radius": 86_400, "win": 120, "offset_s": 8.0, "hop_s": 1.0}


def test_signed_table_values_never_show_minus_zero():
    assert m5._sgn(-0.04) == "+0.0" and m5._sgn(-0.06) == "-0.1" and m5._sgn(6.368) == "+6.4"
    assert m5._sgn(None) == "n/a" and m5._f(None) == "n/a" and m5._f(1.5) == "1.5"


def test_part_as_excerpt_maps_names_only():
    class Z:
        files = ["v2__cut1__freq", "v2__cut1__fw", "v2__x60__freq", "v1__x60__freq", "meta_json"]

        def __getitem__(self, name):
            return name
    view = m5.PartAsExcerpt(Z(), "v2", "cut1")
    assert sorted(view.files) == ["v2__x60__freq", "v2__x60__fw"]
    assert view["v2__x60__freq"] == "v2__cut1__freq" and view["v2__x60__fw"] == "v2__cut1__fw"
    with pytest.raises(KeyError):
        view["v1__x60__freq"]


# ------------------------------------------------- toy SYNTHETIC streams

def toy_trace(ref, lock, n, jumps=(), seed=0, sigma=0.002):
    """SYNTHETIC trace: n frames read from the SYNTHETIC reference from index ``lock`` on.
    At each (frame, jump) the read position skips ``jump`` seconds (a negative jump goes
    back, as a repeated stretch would). Gaussian noise is added."""
    idx = lock + np.arange(n)
    for frame, jump in jumps:
        idx[frame:] += jump
    return ref[idx].astype(np.float64) + np.random.default_rng(seed).normal(0.0, sigma, n)


def write_trace(path: Path, parts: dict, cuts: list) -> None:
    arrays, traces = {}, {}
    for key, freq in parts.items():
        arrays[f"{key}__freq"] = freq
        arrays[f"{key}__snr"] = np.full(freq.size, 10.0, dtype=np.float32)
        arrays[f"{key}__fw"] = np.ones(freq.size, dtype=np.float32)
        traces[key] = {"offset_s": 8.0, "hop_s": 1.0, "n_frames": int(freq.size),
                       "hum": {"present": True, "median_peak_to_floor": {"50 Hz": 10.0}}}
    meta = {"SYNTHETIC": True, "traces": traces, "cuts": cuts}
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, meta_json=np.array(json.dumps(meta)), **arrays)


def _cut(j, at_s, length_s):
    return {"cut": j, "at_s": float(at_s), "length_s": int(length_s), "at_recording_s": 120.0 + at_s,
            "label": "SYNTHETIC cut (PROTOCOL M5)"}


def make_toy(repo: Path, ref_arrays) -> Path:
    """A toy repo with SYNTHETIC traces, a hand-written results table and gate list, and a
    search cache written by the evaluation's own eval_common.run_searches.

    A cut at excerpt second at_s shows in the trace at frame at_s - 8 (frame i is centred
    8 + i s into the excerpt)."""
    f, v = ref_arrays
    ref_dir = write_synth_ref_dir(repo / "ref", f, v)
    rel_index = {"SYN-A": 60_000, "SYN-B": 90_000, "SYN-C": 120_000, "SYN-D": 140_000, "SYN-E": 150_000}
    lock = {vid: i + LATENCY + 128 for vid, i in rel_index.items()}       # frame 0 <-> start + 128 s
    tdir = repo / "traces" / "test"
    # SYN-A: clean uncut trace; a 40 s cut at 600 s and a 90 s cut at 1100 s.
    write_trace(tdir / "SYN-A.npz", {
        "v2__x60": toy_trace(f, lock["SYN-A"], N, seed=1),
        "v2__cut1": toy_trace(f, lock["SYN-A"], N - 40, [(592, 40)], seed=2),
        "v2__cut2": toy_trace(f, lock["SYN-A"], N - 90, [(1092, 90)], seed=3)},
        [_cut(1, 600.0, 40), _cut(2, 1100.0, 90)])
    # SYN-B: the "uncut" trace itself jumps by 20 s (one false alarm); cut1 is a 10 s cut at
    # 500 s; cut2 claims a 60 s cut at 1000 s, but the trace jumps 100 s later.
    write_trace(tdir / "SYN-B.npz", {
        "v2__x60": toy_trace(f, lock["SYN-B"], N, [(700, 20)], seed=4),
        "v2__cut1": toy_trace(f, lock["SYN-B"], N - 10, [(492, 10)], seed=5),
        "v2__cut2": toy_trace(f, lock["SYN-B"], N - 60, [(1092, 60)], seed=6)},
        [_cut(1, 500.0, 10), _cut(2, 1000.0, 60)])
    # SYN-C (a gate lock outside the M5 set): cut1 repeats 40 s at 700 s (an insertion);
    # cut2 is a 120 s cut at 900 s.
    write_trace(tdir / "SYN-C.npz", {
        "v2__x60": toy_trace(f, lock["SYN-C"], N, seed=7),
        "v2__cut1": toy_trace(f, lock["SYN-C"], N, [(692, -40)], seed=8),
        "v2__cut2": toy_trace(f, lock["SYN-C"], N - 120, [(892, 120)], seed=9)},
        [_cut(1, 700.0, 40), _cut(2, 900.0, 120)])
    # SYN-E: a gate lock with a 10-min trace only, so no cut traces. SYN-D has no trace file:
    # it is neither a confident hit nor a gate lock, so the script must not open it.
    write_trace(tdir / "SYN-E.npz", {"v2__x10": toy_trace(f, lock["SYN-E"], 585, seed=10)}, [])

    ts = {vid: int(gref.ist_to_unix(T0 + pd.Timedelta(seconds=i))) for vid, i in rel_index.items()}
    (repo / "data").mkdir(parents=True, exist_ok=True)
    with open(repo / "data" / "splits.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["video_id", "split", "release_timestamp", "excerpt_lengths_min"])
        w.writeheader()
        for vid in rel_index:
            w.writerow({"video_id": vid, "split": "test", "release_timestamp": ts[vid],
                        "excerpt_lengths_min": "10" if vid == "SYN-E" else "60"})

    def row(vid, ext, m, scope, outcome, hit, ge):
        return {"mode": "real", "video_id": vid, "split": "test", "extractor": ext, "excerpt_min": m,
                "scope": scope, "outcome": outcome, "hit_30s": str(hit), "confidence_ge_tau": str(ge),
                "error_s": "3"}
    rows = [row("SYN-A", "v2", 60, "full", "confident hit", True, True),
            row("SYN-A", "v2", 60, "1d", "confident hit", True, True),
            row("SYN-B", "v2", 60, "full", "confident hit", True, True),
            row("SYN-B", "v2", 60, "1d", "confident hit", True, True),
            row("SYN-C", "v2", 60, "full", "no verdict", True, False),
            row("SYN-C", "v2", 60, "90d", "confident hit", True, True),
            row("SYN-C", "v2", 60, "1d", "no verdict", True, False),
            row("SYN-D", "v2", 60, "full", "confident wrong", False, True),
            row("SYN-D", "v1", 60, "full", "confident hit", True, True),
            row("SYN-E", "v2", 10, "full", "confident hit", True, True)]
    (repo / "results").mkdir(parents=True, exist_ok=True)
    with open(repo / "results" / "test_results.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=RESULT_COLS)
        w.writeheader()
        w.writerows(rows)
    gate = {"gate": {"by_extractor": {"v2": {"locked_streams": ["SYN-A", "SYN-B", "SYN-C", "SYN-E"]}}}}
    (repo / "results" / "gate_frozen.json").write_text(json.dumps(gate), encoding="utf-8")

    ref = gref.load(ref_dir)
    jobs = [ec.Job(split="test", video_id=vid, trace_path=f"traces/test/{vid}.npz", release_ts=ts[vid],
                   lengths=[10] if vid == "SYN-E" else [60], extractors=("v2",))
            for vid in ("SYN-A", "SYN-B", "SYN-C", "SYN-E")]
    records, _ = ec.run_searches(jobs, repo / "results" / "search_cache", ref, workers=1, repo=repo)
    assert len(records) == 4
    return ref_dir


def _tables_have_captions(md: str) -> list:
    """The caption line above each markdown table (the nearest non-empty line)."""
    lines = md.splitlines()
    caps = []
    for i, ln in enumerate(lines[:-1]):
        if ln.startswith("|") and lines[i + 1].startswith("|---"):
            j = i - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            caps.append(lines[j])
    return caps


def test_end_to_end_on_toy_synthetic_streams(tmp_path, synth_ref_small):
    repo = tmp_path / "toy"
    ref_dir = make_toy(repo, synth_ref_small)
    assert m5.main(["--ref", str(ref_dir)], repo=repo) == 0
    text = (repo / "results" / "m5.json").read_text(encoding="utf-8")
    d = json.loads(text)
    assert d["SYNTHETIC"] is True and d["inputs"]["reference"]["memory_mapped"] is True

    # pre-registered block: the two confident hits at (v2, 60 min, full) and nothing else
    pre = d["preregistered"]
    assert pre["stream_ids"] == ["SYN-A", "SYN-B"] and pre["n_streams"] == 2 and pre["n_cuts"] == 4
    by = {s["video_id"]: s for s in pre["streams"]}
    a1, a2 = by["SYN-A"]["cuts"]
    for c, length in ((a1, 40), (a2, 90)):
        assert c["SYNTHETIC"] is True and c["detected"] and c["true_length_s"] == length
        assert abs(c["position_error_s"]) <= 10 and c["duration_error_s"] <= 2
        assert c["reported_edits"][c["detecting_edit"]]["kind"] == "cut"
    assert a1["n_frames"] == N - 40 and a2["n_frames"] == N - 90        # the cut trace was searched
    assert by["SYN-A"]["uncut"]["false_alarms"] == 0 and by["SYN-A"]["uncut"]["reported_edits"] == []
    b1, b2 = by["SYN-B"]["cuts"]
    assert b1["detected"] and b1["duration_error_s"] <= 2
    assert not b2["detected"] and b2["duration_error_s"] is None        # reported 100 s from the truth
    assert b2["other_edits"] == 1 and not b2["either_kind_within_window"]
    assert by["SYN-B"]["uncut"]["false_alarms"] == 1
    fa = pre["false_alarms"]
    assert fa["n_uncut_excerpts"] == 2 and fa["edits_reported"] == 1
    assert fa["excerpts_with_a_reported_edit"] == ec.rate_dict(1, 2)
    assert fa["stream_ids_with_a_reported_edit"] == ["SYN-B"]
    t = pre["totals"]
    assert t["all"]["detected"] == ec.rate_dict(3, 4)
    assert t["all"]["detected"]["wilson95"] == list(ec.wilson(3, 4))
    assert t["by_cut_length_s"]["40"]["detected"] == ec.rate_dict(1, 1)
    assert t["by_cut_length_s"]["60"]["detected"] == ec.rate_dict(0, 1)
    assert t["by_cut_length_s"]["20"]["n_cuts"] == 0
    assert t["all"]["duration_error_s"]["n"] == 3
    for s in pre["streams"]:
        assert s["checks"]["uncut_1d_search_reproduces_cache"] and s["checks"]["uncut_lock_equals_full_scope_lock"]
        assert s["trace_path"] == f"traces/test/{s['video_id']}.npz"

    # EXPLORATORY block: kept apart, covers the gate locks with cut traces
    exp = d["exploratory"]
    assert "EXPLORATORY" in exp["label"]
    g = exp["gate_locks"]
    assert g["stream_ids"] == ["SYN-A", "SYN-B", "SYN-C"] and g["n_cuts"] == 6
    assert g["gate_locks_without_cut_traces"] == ["SYN-E"]
    assert g["gate_locks_recomputed_from_results_table_equal"] is True
    extra = g["additional_streams_only"]
    assert extra["stream_ids"] == ["SYN-C"] and "EXPLORATORY" in extra["label"]
    c1, c2 = extra["streams"][0]["cuts"]
    assert not c1["detected"] and c1["either_kind_within_window"]      # reported as an insertion
    assert any(e["kind"] == "insertion" for e in c1["reported_edits"])
    assert c2["detected"] and c2["duration_error_s"] <= 2
    assert g["totals"]["all"]["detected"] == ec.rate_dict(4, 6)
    assert pre["totals"]["all"]["detected"]["n"] == 4                  # unchanged by the EXPLORATORY set
    rc = exp["reading_checks_on_preregistered_streams"]
    assert rc["either_kind_within_30s"] == ec.rate_dict(3, 4)
    assert (rc["not_detected"], rc["not_detected_no_edit_reported"],
            rc["not_detected_edits_reported_elsewhere"]) == (1, 0, 1)
    assert all("SYN-D" not in s for s in (pre["stream_ids"], g["stream_ids"]))

    # the markdown: SYNTHETIC in the title and in every table caption, plain ASCII, no local paths
    md = (repo / "results" / "m5.md").read_text(encoding="utf-8")
    assert md.splitlines()[0].startswith("# ") and "SYNTHETIC" in md.splitlines()[0]
    caps = _tables_have_captions(md)
    assert len(caps) == 6 and all(c.startswith("Table ") and "SYNTHETIC" in c for c in caps)
    assert "## EXPLORATORY (post hoc)" in md and all("EXPLORATORY" in c for c in caps[3:])
    assert md.index("## Pre-registered result") < md.index("## EXPLORATORY (post hoc)")
    assert md.isascii()
    for out in (text, md):
        assert str(tmp_path) not in out and str(tmp_path).replace("\\", "/") not in out
        assert str(tmp_path).replace("\\", "\\\\") not in out
    assert not list((repo / "results").glob("*.tmp"))


def test_find_edits_is_called_without_keywords(tmp_path, synth_ref_small, monkeypatch):
    repo = tmp_path / "toy"
    ref_dir = make_toy(repo, synth_ref_small)
    calls = []

    def spy(*args, **kwargs):
        calls.append((len(args), dict(kwargs)))
        return {"edits": [], "anchor": {"given": int(args[3]), "used": int(args[3])}}
    monkeypatch.setattr(m5, "find_edits", spy)
    assert m5.main(["--ref", str(ref_dir), "--no-exploratory"], repo=repo) == 0
    assert len(calls) == 6 and all(c == (4, {}) for c in calls)       # 2 streams x (uncut + 2 cuts)
    d = json.loads((repo / "results" / "m5.json").read_text(encoding="utf-8"))
    assert d["preregistered"]["totals"]["all"]["detected"] == ec.rate_dict(0, 4)
    assert d["exploratory"]["status"].startswith("not computed")
    assert d["timing"]["find_edits_calls"] == 6


def test_refuses_when_a_trace_differs_from_the_searched_one(tmp_path, synth_ref_small, capsys):
    repo = tmp_path / "toy"
    ref_dir = make_toy(repo, synth_ref_small)
    f, _ = synth_ref_small
    write_trace(repo / "traces" / "test" / "SYN-A.npz",
                {"v2__x60": toy_trace(f, 60_000 + LATENCY + 128, N, seed=99)}, [])   # changed after the search
    assert m5.main(["--ref", str(ref_dir)], repo=repo) == 2
    assert "refusing to write M5" in capsys.readouterr().err
    assert not (repo / "results" / "m5.json").exists() and not (repo / "results" / "m5.md").exists()
