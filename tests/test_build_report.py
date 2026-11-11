"""Tests for scripts/build_report.py (the paper-style report).

The PDF step needs Playwright and PyMuPDF, which are not in the project venv,
so these tests cover the HTML step only: the guards on the template (no
number typed by hand, protocol phrases that must exist, citations), the
toy scoring files of tests/test_fill_results.py (toy streams T1 to T7, toy
negatives; no real stream) and one check that docs/report/report.html is up
to date with the repo's results.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "tests"))

import build_report as br  # noqa: E402
import test_fill_results as tfr  # noqa: E402  (toy scoring files)

DASHES = {chr(0x2014), chr(0x2013)}


@pytest.fixture(scope="module")
def book():
    return br.Book(REPO)


def test_a_number_typed_into_the_template_stops_the_build(book):
    with pytest.raises(br.ReportError, match="number outside a placeholder"):
        br.template_digits("<p>The endpoint is 25 of 90.</p>", book)
    with pytest.raises(br.ReportError, match="section 99"):
        br.template_digits("<p>See PROTOCOL section 99.</p>", book)


def test_labels_and_placeholders_may_hold_digits(book):
    br.template_digits('<p class="w80">M1 to M6, S1, v2, r<sub>1</sub>, top-1, SHA-256, PROTOCOL section 19, '
                       '{{p:30 s}}, {{n_test}} and <img src="figures/m3_v2_60min.png"></p><!-- 42 -->', book)


def test_protocol_and_readme_phrases_must_exist(book):
    assert book.phrase("p", "16 s Hann windows with a 1 s hop") == "16 s Hann windows with a 1 s hop"
    assert book.phrase("p", "SEED 2026110701")             # case and markdown emphasis are ignored
    with pytest.raises(br.ReportError, match="PROTOCOL.md does not contain"):
        book.phrase("p", "a 17 s window that the plan never had")
    with pytest.raises(br.ReportError, match="README.md does not contain"):
        book.phrase("r", "a sentence the README never had")


def test_every_deviation_of_section_19_is_found(book):
    stamps = book.deviation_stamps()
    assert stamps[0] == "7 Nov 2026, 08:30 IST"
    assert len(stamps) == len(set(stamps)) >= 10


def test_citations_are_numbered_by_first_use_and_all_used(book, monkeypatch):
    monkeypatch.setattr(book, "deviation_stamps", lambda: [])
    refs = {"a": {"key": "a", "text": "Author A: Title", "doi": "10.1/a", "confirmed": "toy"},
            "b": {"key": "b", "text": "Author B: Title", "url": "https://example.org/b", "accessed": "toy date",
                  "confirmed": "toy"}}
    f = {"_tables": {}, "_claims": [], "x": "a value"}
    out = br.render("<h2 id=\"sec-one\">One</h2><p>{{x}} {{cite:b}} then {{cite:a}}, see Section {{sec:one}}.</p>"
                    "{{refs}}", f, book, refs)
    assert '<span class="num">1</span>One' in out
    assert '[<a href="#ref-b">1</a>] then [<a href="#ref-a">2</a>]' in out
    assert out.index('id="ref-b"') < out.index('id="ref-a"')
    assert "accessed on toy date" in out and "https://doi.org/10.1/a" in out
    with pytest.raises(br.ReportError, match="never cited"):
        br.render("<p>{{cite:a}}</p>", f, book, refs)
    with pytest.raises(br.ReportError, match="has no value"):
        br.render("<p>{{missing}} {{cite:a,b}}</p>", f, book, refs)


def test_negative_numbers_get_a_minus_sign():
    assert br.MINUS_RE.sub("−", "from -4 s to 5 s (J -0.012)") == "from −4 s to 5 s (J −0.012)"
    for keep in ("top-1", "2020-12-20 17:21:01", "SHA-256", "k-1", "v2-x"):
        assert br.MINUS_RE.sub("−", keep) == keep


def _toy_dir(tmp_path: Path) -> Path:
    """The toy scoring files of test_fill_results, plus the few fields the report also reads."""
    d = tfr._toy_eval_dir(tmp_path / "toy")
    m5 = json.loads((d / "m5.json").read_text(encoding="utf-8"))
    m5["method"] = {"find_edits_defaults": {"min_jump": 5, "min_seg": 180}}
    (d / "m5.json").write_text(json.dumps(m5), encoding="utf-8")
    for name in ("evaluation.json", "gate_frozen.json"):
        obj = json.loads((d / name).read_text(encoding="utf-8"))
        obj["gate"]["condition_1_2_met"] = True
        if name == "evaluation.json":
            obj["blinding"] = "passed: toy"
        (d / name).write_text(json.dumps(obj), encoding="utf-8")
    return d


def test_toy_results_fill_the_whole_report(tmp_path):
    out, figs = br.build_html(REPO, _toy_dir(tmp_path))
    text = re.sub(r"<[^>]+>", " ", out)
    assert "{{" not in out and not DASHES & set(out)
    assert not br.fr.ABS_PATH_RE.search(out)
    assert "for 2 of 7 streams (28.6%, 95% CI" in text           # toy primary endpoint: T1 and T2 hit
    assert "25 of 90" not in text                                  # no real result leaks into a toy build
    assert "Gunj gave a confident verdict for 5 excerpts and no verdict for 2" in text
    assert "1 is off by 5,299 s (88 minutes)" in text
    assert "τ was reached by 1 of 3 real negatives" in text
    assert set(figs) == {"time_axis_check.png", "m3_reliability_v2_60min_full.png"}
    for blk in re.findall(r"<(p|li|figcaption|h3)\b[^>]*>(.*?)</\1>", out, flags=re.S):
        if "M5" in blk[1]:
            assert "SYNTHETIC" in blk[1]


def test_a_claim_the_results_contradict_stops_the_build(tmp_path):
    d = _toy_dir(tmp_path)
    for name in ("evaluation.json", "gate_frozen.json"):
        obj = json.loads((d / name).read_text(encoding="utf-8"))
        obj["gate"]["condition_1_2_met"] = False
        (d / name).write_text(json.dumps(obj), encoding="utf-8")
    with pytest.raises(br.ReportError, match="lock conditions of the gate were met"):
        br.build_html(REPO, d)


def test_references_have_a_link_and_a_confirmation():
    data = json.loads((REPO / br.REFERENCES).read_text(encoding="utf-8"))
    template = (REPO / br.TEMPLATE).read_text(encoding="utf-8")
    cited = {k.strip() for m in re.findall(r"\{\{cite:([^}]*)\}\}", template) for k in m.split(",")}
    keys = [r["key"] for r in data["references"]]
    assert set(keys) == cited and len(keys) == len(set(keys))
    for r in data["references"]:
        assert r.get("confirmed")
        assert (r.get("doi", "").startswith("10.") or r.get("url", "").startswith("https://"))


def test_repo_report_is_up_to_date(capsys):
    if not (REPO / br.HTML_OUT).exists():
        pytest.skip("docs/report/report.html has not been built")
    assert br.main(["--check"]) == 0, "run: python scripts/build_report.py"
    capsys.readouterr()
