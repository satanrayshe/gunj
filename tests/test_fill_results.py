"""Tests for scripts/fill_results.py (marker filling, pending state, Wilson intervals,
the blocks added for the final results, and the checks on the narration).

The pre-Test results files in results/ are read as they are in the repo. Test
outputs are pointed at an empty folder or at a folder of toy scoring files that
this module writes (toy streams T1 to T7, toy negatives), so these tests never
depend on a scoring run. The last tests read three text files of the repo
(the narration, the shot list and the Unstop answers) and check their shape.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import fill_results as fr  # noqa: E402

DASHES = {chr(0x2014), chr(0x2013)}        # em dash, en dash


def _state(eval_dir: Path, interim=False, allow_practice=False):
    src = fr.Sources(REPO, eval_dir, interim, allow_practice)
    return src, fr.TestState(src)


def test_wilson_matches_protocol_numbers():
    # results/splits_summary.json: 0 false matches in 25 trials -> upper bound 0.1332
    lo, hi = fr.wilson(0, 25)
    assert lo == 0.0 and round(hi, 4) == 0.1332
    lo, hi = fr.wilson(7, 19)
    assert round(lo, 3) == 0.191 and round(hi, 3) == 0.590
    assert fr.wilson(0, 0) == (None, None)
    assert fr.fmt_rate(0, 0) == "n/a (N = 0)"


def test_pending_fill_is_idempotent(tmp_path):
    src, ts = _state(tmp_path)
    assert not ts.show
    doc = ("x <!-- span:start -->old<!-- span:end --> y\n"
           "<!-- results:start -->\nold\n<!-- results:end -->\n"
           "<!-- unstop-baselines:start --><!-- unstop-baselines:end -->\n")
    once, found = fr.fill_text(doc, src, ts, {})
    assert found == ["span", "results", "unstop-baselines"]
    assert "x <!-- span:start -->20 Dec 2020 to 20 Aug 2023<!-- span:end --> y" in once
    assert fr.PENDING in once
    assert "<!-- results:start -->\n### Gate" in once and once.count("<!-- results:end -->") == 1
    twice, _ = fr.fill_text(once, src, ts, {})
    assert twice == once


def test_bad_markers_stop_the_run(tmp_path):
    src, ts = _state(tmp_path)
    with pytest.raises(fr.FillError):
        fr.fill_text("<!-- nosuchblock:start -->x<!-- nosuchblock:end -->", src, ts, {})
    with pytest.raises(fr.FillError):
        fr.fill_text("<!-- span:start -->x", src, ts, {})


def test_main_writes_atomically_and_reports_unchanged(tmp_path, capsys):
    target = tmp_path / "doc.md"
    target.write_text("<!-- drift-max:start --><!-- drift-max:end -->\n", encoding="utf-8")
    assert fr.main([str(target), "--eval-dir", str(tmp_path / "empty")]) == 0
    assert target.read_text(encoding="utf-8").startswith("<!-- drift-max:start -->319 s<!-- drift-max:end -->")
    assert not list(tmp_path.glob("*.tmp"))
    assert fr.main([str(target), "--eval-dir", str(tmp_path / "empty")]) == 0
    assert "unchanged" in capsys.readouterr().out


def test_practice_output_stays_pending_unless_allowed(tmp_path):
    (tmp_path / "evaluation.json").write_text(json.dumps({"mode": "practice"}), encoding="utf-8")
    src, ts = _state(tmp_path, interim=True)
    assert not ts.show
    assert any("practice" in w for w in src.warnings)


def test_interim_needs_the_flag(tmp_path):
    (tmp_path / "evaluation.json").write_text(json.dumps({"mode": "real"}), encoding="utf-8")
    _, ts = _state(tmp_path)
    assert not ts.show            # no gate_frozen.json and no --interim: pending


def test_check_flag_reports_a_stale_file(tmp_path, capsys):
    target = tmp_path / "doc.md"
    target.write_text("<!-- drift-max:start -->old<!-- drift-max:end -->\n", encoding="utf-8")
    empty = str(tmp_path / "empty")
    assert fr.main([str(target), "--eval-dir", empty, "--check"]) == 1
    assert "old" in target.read_text(encoding="utf-8")            # --check writes nothing
    assert fr.main([str(target), "--eval-dir", empty]) == 0
    assert fr.main([str(target), "--eval-dir", empty, "--check"]) == 0
    capsys.readouterr()


# ----------------------------------------------------------------- small helpers

def test_words_are_the_spoken_forms():
    assert [fr.words(n) for n in (0, 9, 14, 20, 25, 90)] == ["zero", "nine", "fourteen", "twenty", "twenty-five", "ninety"]
    assert fr.words(126) == "a hundred and twenty-six"
    assert fr.words(150) == "a hundred and fifty"
    assert fr.words(300) == "three hundred"
    with pytest.raises(fr.FillError):
        fr.words(1000)


def test_durations_for_a_sentence():
    assert fr.dur_text(45) == "45 s"
    assert fr.dur_text(-5299) == "88 minutes"
    assert fr.dur_text(3 * 3600) == "3.0 hours"
    assert fr.dur_text(2876519) == "33.3 days"


def test_classes_use_the_preregistered_hit_columns():
    rows = [{"error_s": "2", "hit_30s": "True", "hit_120s": "True", "hit_600s": "True"},
            {"error_s": "-44", "hit_30s": "False", "hit_120s": "True", "hit_600s": "True"},
            {"error_s": "571", "hit_30s": "False", "hit_120s": "False", "hit_600s": "True"},
            {"error_s": "-5299", "hit_30s": "False", "hit_120s": "False", "hit_600s": "False"},
            {"error_s": "", "hit_30s": "", "hit_120s": "", "hit_600s": ""}]
    assert fr.classes(rows) == {"within_30": 1, "from_30_to_120": 1, "from_120_to_600": 1, "beyond_600": 1,
                                "no_candidate": 1, "n": 5}


# ----------------------------------------------------------------- toy scoring files

TOY = [  # video id, error_s, verdict, M1 hum flag, label_drift_s, label-stable
    ("T1", "2", "match", "True", "10.0", "True"),
    ("T2", "-8", "match", "True", "293.0", "False"),
    ("T3", "44", "match", "True", "60.0", "False"),
    ("T4", "571", "match", "True", "60.0", "False"),
    ("T5", "-5299", "match", "True", "-8.0", "True"),
    ("T6", "999999", "no verdict", "False", "0.0", "True"),
    ("T7", "", "no verdict", "False", "0.0", "True"),
]
V1_HITS, NAIVE_HITS = {"T1"}, set()
COLS = ["mode", "video_id", "venue_type", "label_drift_s", "label_stable", "channel_status", "harvest_status",
        "hum_present_v1_full", "extractor", "excerpt_min", "scope", "verdict", "error_s", "hit_30s", "hit_120s",
        "hit_600s", "outcome"]


def _rate(k, n):
    lo, hi = fr.wilson(k, n) if n else (None, None)
    return {"k": k, "n": n, "rate": (k / n if n else None), "wilson95": [lo, hi]}


def _toy_rows():
    rows = []
    for ext in fr.EXTRACTORS:
        for m in fr.LENGTHS:
            for scope in fr.SCOPES:
                for vid, err, verdict, hum, drift, stable in TOY:
                    if ext == "v1":
                        err, verdict = ("2", "match") if vid in V1_HITS else (("999999", "no verdict") if err else ("", "no verdict"))
                    if ext == "naive":
                        err, verdict = ("999999", "no verdict") if err else ("", "no verdict")
                    e = abs(int(err)) if err else None
                    hit = (lambda lim: "" if e is None else str(e <= lim))
                    outcome = ("no verdict" if verdict != "match" else
                               "confident hit" if e is not None and e <= 30 else "confident wrong")
                    rows.append({"mode": "real", "video_id": vid, "venue_type": "legislature" if hum == "True" else "lecture",
                                 "label_drift_s": drift, "label_stable": stable, "channel_status": fr.VERIFIED,
                                 "harvest_status": "done", "hum_present_v1_full": hum, "extractor": ext,
                                 "excerpt_min": str(m), "scope": scope, "verdict": verdict, "error_s": err,
                                 "hit_30s": hit(30), "hit_120s": hit(120), "hit_600s": hit(600), "outcome": outcome})
    return rows


def _split(rows):
    c = fr.classes(rows)
    n = c["n"]
    w30 = c["within_30"]
    w120 = w30 + c["from_30_to_120"]
    return {**c, "within_30_share": _rate(w30, n), "within_120_share": _rate(w120, n),
            "within_600_share": _rate(w120 + c["from_120_to_600"], n)}


def _toy_eval_dir(d: Path) -> Path:
    """Write a small, self-consistent set of scoring files (toy numbers, no real stream)."""
    d.mkdir(parents=True, exist_ok=True)
    rows = _toy_rows()
    with (d / "test_results.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    m2, cells = {}, {}
    for ext in fr.EXTRACTORS:
        for m in fr.LENGTHS:
            for scope in fr.SCOPES:
                cr = [r for r in rows if r["extractor"] == ext and r["excerpt_min"] == str(m) and r["scope"] == scope]
                n = len(cr)
                hits = [r for r in cr if r["hit_30s"] == "True"]
                hum = [r for r in cr if r["hum_present_v1_full"] == "True"]
                m2.setdefault(ext, {}).setdefault(str(m), {})[scope] = {
                    "n": n, "tau": 0.1, "top1_hit": _rate(len(hits), n), "top1_hit_D0": _rate(min(1, len(hits)), n),
                    "top1_hit_hum_present": _rate(sum(r["hit_30s"] == "True" for r in hum), len(hum)),
                    "confident_hit": _rate(sum(r["outcome"] == "confident hit" for r in cr), n),
                    "confident_wrong": _rate(sum(r["outcome"] == "confident wrong" for r in cr), n),
                    "no_verdict": _rate(sum(r["outcome"] == "no verdict" for r in cr), n)}
                cells.setdefault(ext, {}).setdefault(str(m), {})[scope] = {
                    "tau": 0.1, "confident": _split([r for r in cr if r["verdict"] == "match"]), "top1": _split(cr)}
    trials = [{"video_id": "NEG-1", "kind": "real", "confidence": 0.2, "hum_flag": True, "false_match": True},
              {"video_id": "NEG-2", "kind": "real", "confidence": 0.02, "hum_flag": True, "false_match": False},
              {"video_id": "NEG-3", "kind": "real", "confidence": 0.01, "hum_flag": False, "false_match": False},
              {"video_id": "SYN-TOY-1", "kind": "SYNTHETIC", "confidence": 0.02, "hum_flag": True, "false_match": False},
              {"video_id": "SYN-TOY-2", "kind": "SYNTHETIC", "confidence": 0.01, "hum_flag": False, "false_match": False}]
    fm = lambda k, n: {"false_match": _rate(k, n), "false_match_with_hum_flag": _rate(k, n)}
    m3cell = {"tau": 0.1, "trials": trials, "real": fm(1, 3), "SYNTHETIC": fm(0, 2), "together": fm(1, 5)}
    m3 = {ext: {str(m): {s: m3cell for s in fr.SCOPES} for m in fr.LENGTHS} for ext in fr.EXTRACTORS}
    gate = {"N_processed": 7, "condition_1_2_met": False, "condition_3_met": False, "tau_L_committed_in_time": True,
            "gate_met": False,
            "by_extractor": {"v2": {"n_streams_with_lock": 2, "n_streams_with_lock_D0": 1, "locked_streams": ["T1", "T2"]},
                             "v1": {"n_streams_with_lock": 1}, "naive": {"n_streams_with_lock": 0}}}
    health = {"blocked_rows": ["B1"], "log_matches": [{"text": "2026-11-08T05:30:42+0530 ERROR toy bot check"}]}
    ev = {"mode": "real", "written_ist": "2026-11-11T19:16:31+05:30", "n_selected": 10, "n_with_trace": 7,
          "m2": m2, "m3": m3, "m3_counts": {"real": 3, "SYNTHETIC": 2, "harvested_not_yet_searched": 0},
          "gate": gate, "health": health,
          "timing": {"streams_cached": 7, "streams_to_search": 0, "negatives": {"streams_to_search": 3}},
          "flow": {"steps": [{"step": "selected", "n": 10}, {"step": "audio obtained", "n": 7}],
                   "drops": [{"from": "selected", "to": "download attempted",
                              "reasons": {"not attempted yet (toy)": ["N1"]}},
                             {"from": "download attempted", "to": "audio obtained",
                              "reasons": {"failed: ERROR: toy: This video is not available": ["U1"],
                                          "blocked: toy bot check": ["B1"]}}]}}
    cut = lambda k, n, errs: {"SYNTHETIC": True, "n_cuts": n, "detected": _rate(k, n),
                              "duration_error_s": {"n": len(errs), "median": (sorted(errs)[len(errs) // 2] if errs else None),
                                                   "max": (max(errs) if errs else None), "values": errs}}
    m5 = {"written_ist": "2026-11-11T18:59:58+05:30", "SYNTHETIC": True,
          "preregistered": {"n_streams": 2, "n_cuts": 4, "stream_ids": ["T1", "T2"],
                            "totals": {"by_cut_length_s": {"10": cut(0, 2, []), "60": cut(1, 2, [3.0])},
                                       "all": cut(1, 4, [3.0])},
                            "false_alarms": {"n_uncut_excerpts": 2, "excerpts_with_a_reported_edit": _rate(0, 2),
                                             "edits_reported": 0}},
          "exploratory": {"gate_locks": {"n_gate_locks": 2, "n_streams": 2, "totals": {"all": cut(1, 4, [3.0])},
                                         "additional_streams_only": {"n_streams": 0, "totals": {"all": cut(0, 0, [])}}}}}
    reading = lambda sig, mins: {"median": {"hann16": {s: {"sigma_mhz": sig, "status": "read", "shortest_length_min": mins,
                                                           "rates_by_length_min": {str(mins): {"rate": 0.93}}}
                                                       for s in fr.SCOPES}}}
    m4 = {"streams_that_lock": {"n": 2, "n_excerpts": 5, "video_ids": ["T2", "T1"]},
          "distribution_hz": {"A": {"median": 0.009, "q1": 0.004, "q3": 0.017},
                              "B": {"median": 0.037, "q1": 0.013, "q3": 0.071}},
          "uniqueness_lengths_min": [5, 10, 20, 40, 60], "readings": {"A": reading(9.0, 10), "B": reading(37.0, 40)}}
    bins = [{"bin": "[0, 0.025)", "lo": 0.0, "hi": 0.025, **_rate(0, 1)},
            {"bin": "[0.1, 0.2)", "lo": 0.1, "hi": 0.2, **_rate(2, 5)}]
    figs = {"m3_primary_cell": {"test": {"n_excerpts": 7, "n_without_confidence": 1, "bins": bins},
                                "negatives": {"tau": 0.1,
                                              "real": {"n": 3, "confidence": [0.01, 0.02, 0.2], "n_at_or_above_tau": 1},
                                              "SYNTHETIC": {"n": 2, "confidence": [0.01, 0.02], "n_at_or_above_tau": 0}}}}
    group = lambda n, lo, med, hi, st: {"n_verdicts": n, "abs_D_s": {"min": lo, "median": med, "max": hi},
                                        "label_stable": {"True": st, "False": n - st}}
    ex = {"primary_cell": {"within_30": group(2, 10.0, 151.5, 293.0, 1), "offset_30_600": group(2, 60.0, 60.0, 60.0, 0)},
          "distinct_streams": {"at_least_one_offset_30_600": {"n_streams": 2}},
          "offset_streams_with_a_hit_at_another_length": 0}
    (d / "figures").mkdir(exist_ok=True)
    (d / "figures" / "m2_signed_error_v2.png").write_bytes(b"")        # a figure that "exists"
    for name, obj in (("evaluation.json", ev), ("error_breakdown.json", {"cells": cells}), ("m5.json", m5),
                      ("gate_frozen.json", {"frozen_ist": "2026-11-08T17:04:35+05:30", "gate": gate, "health": health}),
                      ("m4_measured.json", m4), ("figures/figures.json", figs),
                      ("exploratory_offset_verdicts.json", ex)):
        (d / name).write_text(json.dumps(obj), encoding="utf-8")
    return d


def _toy_state(tmp_path: Path):
    src, ts = _state(_toy_eval_dir(tmp_path / "toy"))
    mv = tmp_path / "toy_make_video.py"
    mv.write_text('LOCK_ID = "T2"  # toy\nNV_ID = "T6"  # toy\n', encoding="utf-8")
    src.make_video = mv
    src.site_overview = tmp_path / "no_overview.json"
    return src, ts


DOC = "\n".join(f"<!-- {name}:start -->\n<!-- {name}:end -->" for name in ("headline", "status", "results")) + "\n" + \
      " ".join(f"<!-- {name}:start --><!-- {name}:end -->" for name in (
          "gate-count", "harvest-count", "harvest-losses", "bot-check", "neg-retry", "lim-hum", "lim-confident",
          "lim-false-match", "lim-m5", "lim-m4", "unstop-results", "unstop-baselines", "unstop-limits",
          "unstop-harvest")) + "\n"


def test_toy_fill_carries_every_final_result(tmp_path):
    src, ts = _toy_state(tmp_path)
    assert ts.show and ts.frozen is not None
    out, found = fr.fill_text(DOC, src, ts, {})
    again, _ = fr.fill_text(out, src, ts, {})
    assert again == out
    assert "[pending" not in out and "INTERIM" not in out
    assert not DASHES & set(out)                            # no em or en dash in generated text
    assert not fr.ABS_PATH_RE.search(out)
    # headline: primary endpoint, breakdown with the verdict beyond 10 minutes, real false match
    assert "the top candidate starts within 30 s of the expected time for 2 of 7 streams" in out
    assert "Of the 5, 2 are within 30 s, 1 is 30 to 120 s off, 1 is 120 to 600 s off, and 1 is wrong by more " \
           "than 10 minutes (88 minutes)" in out
    assert "1 of 3 real foreign 50 Hz recordings reaches the threshold" in out
    # breakdown table, M3 real beside SYNTHETIC, M4 at the measured level, libhum
    assert '| verdict "match" (confident) | 5 | 2 | 1 | 1 | 1 | 0 |' in out
    assert "| all, whatever the verdict | 7 | 2 | 1 | 1 | 2 | 1 |" in out
    assert "3 of the 25 real foreign 50 Hz streams and 2 of the 20 SYNTHETIC items were searched" in out
    assert "| A: r as scored (v2's weighted correlation) | 9.0 mHz (4.0 to 17.0) | 10 min (93.0%) |" in out
    assert "| libhum | not run: libhum was never installed" in out
    assert "1 bot check (8 Nov 2026, 05:30 IST)" in out
    assert "Of the other 3, 1 was no longer available on YouTube, 1 had not been attempted and 1 met the bot check" in out
    # a figure that exists is embedded with a caption; one that does not is named as not made
    assert "![Signed error of the top candidate for every excerpt length and scope, extractor v2]" \
           "(results/figures/m2_signed_error_v2.png)" in out
    assert "Figure `results/figures/m4_measured_noise.png` is not made yet" in out


def test_every_m5_line_says_synthetic(tmp_path):
    src, ts = _toy_state(tmp_path)
    out, _ = fr.fill_text(DOC, src, ts, {})
    m5_lines = [ln for ln in out.splitlines() if "M5" in ln]
    assert len(m5_lines) >= 4
    assert all("SYNTHETIC" in ln for ln in m5_lines)
    section = out.split("### M5: edit detection on SYNTHETIC cuts")[1].split("\n### ")[0]
    body = [ln for ln in section.splitlines() if ln.strip() and not ln.startswith("|---") and "PROTOCOL section 11)" not in ln]
    assert body and all("SYNTHETIC" in ln for ln in body)
    assert "| all SYNTHETIC cuts | 4 | 1/4 (25.0%" in section
    assert "EXPLORATORY (post hoc): M5 on every gate lock that has SYNTHETIC cut traces" in out


def test_a_breakdown_that_disagrees_with_the_csv_stops_the_run(tmp_path):
    src, ts = _toy_state(tmp_path)
    p = src.eval_dir / "error_breakdown.json"
    eb = json.loads(p.read_text(encoding="utf-8"))
    eb["cells"]["v2"]["60"]["full"]["confident"]["within_30"] += 1
    p.write_text(json.dumps(eb), encoding="utf-8")
    src2, ts2 = _state(src.eval_dir)
    with pytest.raises(fr.FillError, match="error_breakdown.json disagrees"):
        fr.fill_text("<!-- headline:start -->\n<!-- headline:end -->\n", src2, ts2, {})


def test_m5_scored_on_other_streams_stops_the_run(tmp_path):
    src, ts = _toy_state(tmp_path)
    p = src.eval_dir / "m5.json"
    m5 = json.loads(p.read_text(encoding="utf-8"))
    m5["preregistered"]["stream_ids"] = ["T1", "T3"]
    p.write_text(json.dumps(m5), encoding="utf-8")
    src2, ts2 = _state(src.eval_dir)
    with pytest.raises(fr.FillError, match="m5.json scored other streams"):
        fr.fill_text("<!-- results:start -->\n<!-- results:end -->\n", src2, ts2, {})


def test_missing_later_files_say_not_computed(tmp_path):
    src, ts = _toy_state(tmp_path)
    for name in ("error_breakdown.json", "m5.json", "m4_measured.json", "exploratory_offset_verdicts.json"):
        (src.eval_dir / name).unlink()
    src2, ts2 = _state(src.eval_dir)
    out, _ = fr.fill_text("<!-- results:start -->\n<!-- results:end -->\n", src2, ts2, {})
    assert "not computed: run `scripts/m5_edits.py`" in out
    assert "Not computed: `results/error_breakdown.json` is missing" in out
    assert "M4 at the measured noise level is not computed" in out
    assert "[pending" not in out


# ----------------------------------------------------------------- narration checks

def _narration_from(rows, tmp_path: Path, swap=None) -> Path:
    """A narration whose line N says every phrase the toy results require of line N."""
    by_line = {}
    for line, phrase, *_ in rows:
        if line is not None:
            by_line.setdefault(line, []).append(phrase)
    text = []
    for n in range(1, 12):
        said = ". ".join(by_line.get(n, ["Nothing to check here"])) + "."
        if swap and n == swap[0]:
            said = said.replace(swap[1], swap[2])
        text.append(f"{n}. **0:{n:02d}** {said}")
    p = tmp_path / "NARRATION.md"
    p.write_text("\n".join(text) + "\n\nThe video ends at **1:54**.\n", encoding="utf-8")
    return p


def test_narration_numbers_are_checked_against_the_results(tmp_path):
    src, ts = _toy_state(tmp_path)
    rows = fr.narration_rows(src, ts)
    assert {line for line, *_ in rows if line} >= {3, 5, 6, 7, 8, 9, 10}
    said = {phrase for _, phrase, *_ in rows if phrase}
    assert "Seven had an hour of audio" in said
    assert "Two within thirty seconds, one more within two minutes, one within ten, one plainly wrong" in said
    assert "eight seconds from its expected start" in said and "almost five minutes of drift correction" in said
    assert "On three hour-long recordings from other grids, it gave one false match" in said
    src.narration = _narration_from(rows, tmp_path)
    table = fr.b_narration_sources(src, ts)
    assert table.startswith("| line | said | value | source |") and "`results/m4_measured.json`" in table
    assert fr.b_vo(6)(src, ts).startswith("Seven had an hour of audio")
    assert int(fr.b_narration_words(src, ts)) > 50
    # one wrong spoken number: the run stops and names the line
    src2, ts2 = _toy_state(tmp_path / "again")
    src2.narration = _narration_from(rows, tmp_path / "again", swap=(6, "Seven had", "Eight had"))
    with pytest.raises(fr.FillError, match="line 6 should say"):
        fr.b_narration_sources(src2, ts2)


def test_narration_examples_must_fit_the_results(tmp_path):
    src, ts = _toy_state(tmp_path)
    src.make_video.write_text('LOCK_ID = "T3"\nNV_ID = "T6"\n', encoding="utf-8")       # T3 is a confident wrong verdict
    with pytest.raises(fr.FillError, match="lock example T3"):
        fr.narration_rows(src, ts)


# ----------------------------------------------------------------- the text files of the repo

def _repo_narration():
    src = fr.Sources(REPO, REPO / "results", False, False)
    return fr.narration(src)


def test_repo_narration_keeps_its_timeline():
    narr = _repo_narration()
    lines = narr["lines"]
    assert [t for _, t, _ in lines] == [1, 6, 16, 26, 36, 48, 63, 76, 85, 94, 107]      # the 11 fixed start times
    assert narr["end"] == 114                                                          # 1:54
    counts = [len(text.split()) for _, _, text in lines]
    assert 225 <= sum(counts) <= 245
    ends = [t for _, t, _ in lines[1:]] + [narr["end"]]
    for (n, start, _), end, words in zip(lines, ends, counts):
        assert words / (end - start) <= 2.4, f"line {n} is too long for its slot"
    text = (REPO / "video" / "NARRATION.md").read_text(encoding="utf-8")
    m = re.search(r"<!-- narration-words:start -->(\d+)<!-- narration-words:end -->", text)
    assert m and int(m.group(1)) == sum(counts)


def test_repo_shot_list_follows_the_narration():
    narr = _repo_narration()
    lines = narr["lines"]
    script = (REPO / "video" / "SCRIPT.md").read_text(encoding="utf-8")
    ends = [t for _, t, _ in lines[1:]] + [narr["end"]]
    clock = lambda s: f"{s // 60}:{s % 60:02d}"
    for (n, start, text), end in zip(lines, ends):
        assert f"<!-- vo-{n}:start -->{text}<!-- vo-{n}:end -->" in script, f"run scripts/fill_results.py (line {n})"
        assert f"**Line {n}, {clock(start)} to {clock(end)}." in script


def test_repo_readme_shape():
    text = (REPO / "README.md").read_text(encoding="utf-8")
    assert text.count("[pending") == 1 and "[pending: video link]" in text      # the only placeholder left
    assert not DASHES & set(text)                                               # no em or en dash
    assert not fr.ABS_PATH_RE.search(text)
    head = text.split("<!-- headline:start -->")[1].split("<!-- headline:end -->")[0]
    assert 5 <= sum(1 for ln in head.splitlines() if ln.startswith("- ")) <= 7
    assert text.index("<!-- headline:start -->") < text.index("docs/screens/")  # the result comes before the picture
    for ln in text.splitlines():
        if "M5" in ln:
            assert "SYNTHETIC" in ln, ln[:80]
    for name in re.findall(r"\]\((results/[^)]+\.png)\)", text):
        assert (REPO / name).exists(), name


def test_repo_unstop_answers_fit_the_form():
    text = (REPO / "submission" / "UNSTOP-ANSWERS.md").read_text(encoding="utf-8")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    fields = {}
    for part in re.split(r"^## ", text, flags=re.M)[1:]:
        head, _, body = part.partition("\n")
        fields[head.strip()] = len(body.split())
    assert 110 <= fields["3. Problem Statement"] <= 130
    assert fields["4. Proposed Solution"] <= 260
    assert "https://github.com/satanrayshe/gunj" in text and "https://satanrayshe.github.io/gunj/" in text
