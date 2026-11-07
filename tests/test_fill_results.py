"""Tests for scripts/fill_results.py (marker filling, pending state, Wilson intervals).

The pre-Test results files in results/ are read as they are in the repo. Test
outputs are pointed at an empty or fake folder, so these tests never depend on
a scoring run in progress.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import fill_results as fr  # noqa: E402


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
