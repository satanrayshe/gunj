"""scripts/export_site_data.py: the blocks that copy the later result files into the explorer.

Everything here is toy data: invented stream IDs (TOY-...), made-up counts and hashes of
files written by the test itself. No Test result, trace or grid data is read. The one real
file read is PROTOCOL.md, for the libhum sentence, and the site's own code files.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import export_site_data as E    # noqa: E402

COLS = ["mode", "video_id", "split", "harvest_status", "hum_present_v1_full", "label_stable", "channel_status",
        "extractor", "excerpt_min", "scope", "confidence", "verdict", "outcome", "error_s", "hit_30s", "hit_120s",
        "hit_600s"]


def _row(vid, ext, m, scope, error, verdict="no verdict", conf="0.01"):
    r = {c: "" for c in COLS}
    r.update(mode="real", video_id=vid, split="test", harvest_status="done", hum_present_v1_full="True",
             label_stable="False", channel_status="verified by metadata step", extractor=ext, excerpt_min=str(m),
             scope=scope, confidence=conf, verdict=verdict)
    if error is not None:
        a = abs(error)
        r.update(error_s=str(error), hit_30s=str(a <= 30), hit_120s=str(a <= 120), hit_600s=str(a <= 600))
    if verdict == "match":
        r["outcome"] = "confident hit" if error is not None and abs(error) <= 30 else "confident wrong"
    return r


def _toy_rows():
    """Four toy streams in every cell: a confident hit, a confident wrong 90 s off, a confident wrong
    two days off, and a top candidate with no verdict and no candidate at all."""
    rows = []
    for ext in E.EXTRACTORS:
        for m in E.LENGTHS:
            for sc in E.SCOPES:
                rows += [_row("TOY-A", ext, m, sc, -8, "match", "0.5"),
                         _row("TOY-B", ext, m, sc, 90, "match", "0.3"),
                         _row("TOY-C", ext, m, sc, -172800, "match", "0.2"),
                         _row("TOY-D", ext, m, sc, None)]
    return rows


def _write_csv(p: Path, rows: list) -> None:
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _split(rows):
    out = {k: 0 for k in E.CLASS_KEYS}
    for r in rows:
        out[E.class_of(r)] += 1
    n = len(rows)
    w30 = out["within_30"]
    w120 = w30 + out["from_30_to_120"]
    w600 = w120 + out["from_120_to_600"]
    return {"n": n, **out, "within_30_share": E.rate_kn(w30, n), "within_120_share": E.rate_kn(w120, n),
            "within_600_share": E.rate_kn(w600, n)}


@pytest.fixture()
def toy_dir(tmp_path):
    """A toy results folder: test_results.csv, evaluation.json, false_matches.json, gate_frozen.json and an
    error_breakdown.json that records their hashes, as scripts/make_figures.py writes it."""
    d = tmp_path / "results"
    d.mkdir()
    rows = _toy_rows()
    _write_csv(d / "test_results.csv", rows)
    (d / "evaluation.json").write_text(json.dumps({"toy": True}), encoding="utf-8")
    (d / "false_matches.json").write_text(json.dumps({"toy": True}), encoding="utf-8")
    (d / "gate_frozen.json").write_text(json.dumps({"frozen_ist": "2026-11-08T17:04:35+05:30"}), encoding="utf-8")
    cells = {}
    for m in E.LENGTHS:
        for sc in E.SCOPES:
            cr = E.cell_rows(rows, "v2", m, sc)
            cells.setdefault(str(m), {})[sc] = {"tau": 0.1, "confident": _split([r for r in cr if r["verdict"] == "match"]),
                                                "top1": _split(cr)}
    bd = {"inputs": {"test_results": {"path": "results/test_results.csv", "sha256": _sha(d / "test_results.csv")},
                     "evaluation": {"path": "results/evaluation.json", "sha256": _sha(d / "evaluation.json")}},
          "classes": {"within_30": "toy"}, "cells": {"v2": cells}}
    (d / "error_breakdown.json").write_text(json.dumps(bd), encoding="utf-8")
    return d, rows


def test_class_of_follows_the_hit_columns():
    assert E.class_of(_row("TOY", "v2", 60, "full", 30)) == "within_30"
    assert E.class_of(_row("TOY", "v2", 60, "full", -31)) == "from_30_to_120"
    assert E.class_of(_row("TOY", "v2", 60, "full", 600)) == "from_120_to_600"
    assert E.class_of(_row("TOY", "v2", 60, "full", 601)) == "beyond_600"
    assert E.class_of(_row("TOY", "v2", 60, "full", None)) == "no_candidate"


def test_breakdown_is_copied_after_a_recount(toy_dir):
    d, rows = toy_dir
    b = E.build_breakdown(d, rows)
    c = b["cells"]["60"]["full"]["confident"]
    assert (c["n"], c["within_30"], c["from_30_to_120"], c["beyond_600"]) == (3, 1, 1, 1)
    assert b["cells"]["60"]["full"]["top1"]["no_candidate"] == 1
    assert b["primary"]["beyond_600"] == [{"video_id": "TOY-C", "e_s": -172800, "words": "2.0 days"}]


def test_breakdown_stops_when_a_count_differs(toy_dir):
    d, rows = toy_dir
    bd = json.loads((d / "error_breakdown.json").read_text(encoding="utf-8"))
    bd["cells"]["v2"]["60"]["full"]["confident"]["within_30"] = 2
    (d / "error_breakdown.json").write_text(json.dumps(bd), encoding="utf-8")
    with pytest.raises(E.StaleInput, match="re-run scripts/make_figures.py"):
        E.build_breakdown(d, rows)


def test_breakdown_stops_when_the_table_changed(toy_dir):
    d, rows = toy_dir
    _write_csv(d / "test_results.csv", rows[:-1])
    with pytest.raises(E.StaleInput, match="another version"):
        E.build_breakdown(d, rows)


def test_missing_later_files_give_null_blocks(tmp_path):
    rows = _toy_rows()
    assert E.build_breakdown(tmp_path, rows) is None
    assert E.build_m5(tmp_path, rows) is None
    assert E.build_m4_measured(tmp_path) is None
    assert E.build_figures(tmp_path, rows) is None


def _toy_m5(d: Path, streams: list, synthetic=True) -> None:
    det = {"k": 1, "n": 2, "rate": 0.5, "wilson95": [0.09, 0.91]}
    m5 = {"SYNTHETIC": synthetic,
          "inputs": {"results_table": {"sha256": _sha(d / "test_results.csv")},
                     "gate_frozen": {"sha256": _sha(d / "gate_frozen.json")}},
          "method": {"detection_window_s": 30.0, "locate_scope": "1d"},
          "preregistered": {"stream_rule": "toy", "n_streams": len(streams), "n_cuts": 2, "stream_ids": streams,
                            "totals": {"by_cut_length_s": {"20": {"n_cuts": 2, "detected": det,
                                                                  "duration_error_s": {"n": 1, "median": 1.0, "max": 1.0}}},
                                       "all": {"n_cuts": 2, "detected": det,
                                               "duration_error_s": {"n": 1, "median": 1.0, "max": 1.0}}},
                            "false_alarms": {"excerpts_with_a_reported_edit": {"k": 0, "n": 1, "rate": 0.0,
                                                                               "wilson95": [0.0, 0.79]},
                                             "edits_reported": 0}}}
    (d / "m5.json").write_text(json.dumps(m5), encoding="utf-8")


def test_m5_is_copied_with_its_synthetic_label(toy_dir):
    d, rows = toy_dir
    _toy_m5(d, ["TOY-A"])
    b = E.build_m5(d, rows)
    assert b["label"] == "SYNTHETIC"
    assert b["detected"]["k"] == 1 and b["by_cut_length"][0]["cut_s"] == 20


def test_m5_stops_on_other_streams_or_a_missing_label(toy_dir):
    d, rows = toy_dir
    _toy_m5(d, ["TOY-B"])
    with pytest.raises(E.StaleInput, match="confident hits"):
        E.build_m5(d, rows)
    _toy_m5(d, ["TOY-A"], synthetic=False)
    with pytest.raises(E.StaleInput, match="SYNTHETIC"):
        E.build_m5(d, rows)


def test_table_counts_and_pairs():
    rows = _toy_rows()
    c = E.table_counts(rows)
    assert (c["m1_hum_present"]["k"], c["m1_hum_present"]["n"]) == (4, 4)
    assert c["paired_60_full"]["v1"] == {"n": 4, "both": 1, "v2_only": 0, "baseline_only": 0, "neither": 3}
    assert c["s3_verified_channels"]["k"] == 1


def test_libhum_reason_comes_from_protocol():
    b = E.build_libhum()
    assert b["status"] == "not run"
    assert b["reason"] and "never installed" in b["reason"]
    assert b["source"].startswith("PROTOCOL.md section 19")


def test_writer_refuses_stale_words_and_local_paths(tmp_path):
    with pytest.raises(ValueError, match="interim"):
        E.write_json_atomic(tmp_path / "x.json", {"note": "an interim number"})
    fake = "\\".join(["C:", "Us" + "ers", "someone", "file"])        # built from parts: no path literal here
    with pytest.raises(ValueError, match="absolute local path"):
        E.write_json_atomic(tmp_path / "y.json", {"p": fake})
    assert E.write_json_atomic(tmp_path / "z.json", {"ok": "final"}) > 0


def test_dates_and_durations_in_words():
    assert E.fmt_day_ist("2026-11-08T17:04:35+05:30") == "8 Nov 2026, 17:04 IST"
    assert E.dur_text(-5299) == "88 minutes"
    assert E.dur_text(2876519) == "33.3 days"


def test_never_attempted_test_streams_are_listed(tmp_path):
    d = tmp_path / "results"
    d.mkdir()
    ev = {"flow": {"drops": [{"from": "selected", "to": "download attempted",
                              "reasons": {"not attempted yet (network down at last try; will be retried)": ["TOY-N", "TOY-A"]}},
                             {"from": "download attempted", "to": "audio obtained",
                              "reasons": {"failed: toy": ["TOY-F"]}}]}}
    (d / "evaluation.json").write_text(json.dumps(ev), encoding="utf-8")
    splits = {"TOY-N": {"video_id": "TOY-N", "split": "test", "release_time_ist": "2022-01-01 10:00:00"},
              "TOY-A": {"video_id": "TOY-A", "split": "test"}, "TOY-F": {"video_id": "TOY-F", "split": "test"}}
    out = E.never_attempted_clips(d, splits, listed={"TOY-A", "TOY-F"})
    assert [c["video_id"] for c in out] == ["TOY-N"]                  # rows already in the table are not repeated
    c = out[0]
    assert c["verdict"] == "not attempted" and c["harvest"]["status"] == "not attempted" and c["search"] is None
    assert "will be retried" not in json.dumps(c)                     # no longer true: no further download is planned
    row = E.summary_row(c)
    assert row["page"] and row["harvest"] == "not attempted"
    assert E.never_attempted_clips(tmp_path / "none", splits, set()) == []


def test_m1_of_the_real_negatives(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    with open(tmp_path / "data" / "harvest_manifest.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["video_id", "split", "status", "hum_present_v1"])
        w.writerows([("NEG-1", "negative", "done", "True"), ("NEG-2", "negative", "done", "False"),
                     ("NEG-3", "negative", "failed", ""), ("TOY-T", "test", "done", "True")])
    monkeypatch.setattr(E, "REPO", tmp_path)
    splits = {"NEG-1": {"split": "negative", "half": "threshold"}, "NEG-2": {"split": "negative", "half": "report"},
              "NEG-3": {"split": "negative", "half": "report"}, "TOY-T": {"split": "test"}}
    m = E.m1_negatives(splits)
    assert (m["all"]["k"], m["all"]["n"]) == (1, 2)
    assert (m["threshold"]["k"], m["threshold"]["n"], m["report"]["k"], m["report"]["n"]) == (1, 1, 0, 1)
    assert m["label"].startswith("description only")


def test_dataset_credit_comes_from_the_reference_list():
    c = E.dataset_credit()
    refs = {r["key"]: r for r in json.loads((REPO / "docs" / "report" / "references.json").read_text(encoding="utf-8"))["references"]}
    assert c["doi_url"] == "https://doi.org/" + refs["mendeley"]["doi"]
    assert c["licence"] == "CC BY 4.0" and c["licence_url"].startswith("https://creativecommons.org/licenses/by/4.0")
    assert "merged" in c["changes"] and "downsampled" in c["changes"]


def test_uniqueness_bounds_come_from_hits_and_n(tmp_path, monkeypatch):
    (tmp_path / "results").mkdir()
    cols = ["scope", "length_min", "noise_kind", "noise_label", "sigma_mhz", "N", "hits", "hit_rate", "wilson95_lo",
            "wilson95_hi", "random_guess_hit_rate"]
    with open(tmp_path / "results" / "uniqueness_table.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()          # toy row: the CSV's 4-decimal bound would round to 91.1 instead of 91.2
        w.writerow({"scope": "full", "length_min": "40", "noise_kind": "hann16", "noise_label": "toy", "sigma_mhz": "40",
                    "N": "450", "hits": "422", "hit_rate": "0.9378", "wilson95_lo": "0.9115", "wilson95_hi": "0.9566",
                    "random_guess_hit_rate": "0"})
    (tmp_path / "results" / "uniqueness_run.json").write_text(json.dumps({"seed": 1, "hit_tol_s": 30}), encoding="utf-8")
    (tmp_path / "results" / "probe_noise.json").write_text(json.dumps({"summary": {}}), encoding="utf-8")
    monkeypatch.setattr(E, "REPO", tmp_path)
    r = E.build_uniqueness()["rows"][0]
    assert (r["lo"], r["hi"]) == tuple(E.wilson(422, 450))
    assert f"{100 * r['lo']:.1f}" == "91.2"


def test_site_code_has_no_stale_wording():
    for name in ("index.html", "app.js", "style.css"):
        text = (REPO / "site" / name).read_text(encoding="utf-8")
        assert not re.search(r"\b(interim|pending)\b", text, re.I), name
        assert "\u2014" not in text and "\u2013" not in text, name
