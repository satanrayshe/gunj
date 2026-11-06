"""Tests for scripts/build_reference.py and gunj.reference.

All CSVs here are SYNTHETIC: tiny made-up files in the five layouts of the real
Mendeley exports (quoted 8-column, 4-column, plain 2-column CRLF, and the
ComplexReportGroup header), with empty rows, a truncated last row followed by
a Java trace, overlaps, and gaps. No real grid data is used.
"""
import importlib.util
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gunj import reference as gref

REPO = Path(__file__).resolve().parents[1]
BASE = pd.Timestamp("2021-03-01 00:00:00")


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_reference", REPO / "scripts" / "build_reference.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def synth(k: int) -> float:
    """Synthetic frequency for second k (not real data)."""
    return round(50.0 + 0.01 * math.sin(k / 7.0) + 0.0001 * k, 6)


def ts(k: int) -> str:
    return str(BASE + pd.Timedelta(seconds=k))


TRACE = ("Failed to produce download file: com.example.ValidationException: Maximum data read count "
         "[20000000] exceeded. Use filters or a smaller period to reduce the dataset"
         "com.example.StreamExecutionException: boom\n\tat com.example.A.b(A.java:1)\n"
         "Caused by: com.example.ValidationException: Maximum data read count [20000000] exceeded.\n\t... 6 more\n")


def write_synthetic(ref: Path):
    ref.mkdir()
    h8 = '"STARTDATE","SubstationId","DeviceType","DeviceId","PointName","MeasurementId",'
    row8 = '"{t}","BALIP_PG","B","400BUS2______","HZ","BALIP_PG.B.400BUS2______.HZ",{v},"{q}"\n'
    # I: seconds 0..99, first five empty; then a truncated row and a Java trace
    lines = [h8 + '"MeasurementData1hz","MeasurementData1hz:Quality"\n']
    for k in range(100):
        v = "" if k < 5 else f'"{synth(k)}"'
        lines.append(row8.format(t=ts(k), v=v, q="2" if k < 5 else "0"))
    lines.append(f'"{ts(100)}","BALIP_PG","B","400BUS2______","HZ","BALIP_PG.B.40' + TRACE)
    (ref / "freq_I.csv").write_text("".join(lines), encoding="utf-8", newline="")
    # II: seconds 90..299; second 95 differs by +0.001; 150..169 empty; one quality-1 row
    lines = ['"STARTDATE","SubstationId","MeasurementData1hz","MeasurementData1hz:Quality"\n']
    for k in range(90, 300):
        if 150 <= k < 170:
            lines.append(f'"{ts(k)}","BALIP_PG",,"2"\n')
            continue
        v = synth(k) + (0.001 if k == 95 else 0.0)
        lines.append(f'"{ts(k)}","BALIP_PG","{v:.6f}","{1 if k == 200 else 0}"\n')
    (ref / "freq_II.csv").write_text("".join(lines), encoding="utf-8", newline="")
    # III: seconds 290..399, identical to II where they overlap
    lines = [h8 + '"MeasurementData1hz","MeasurementData1hz:Quality"\n']
    for k in range(290, 400):
        lines.append(row8.format(t=ts(k), v=f'"{synth(k)}"', q="0"))
    lines.append('"20' + TRACE)
    (ref / "freq_III.csv").write_text("".join(lines), encoding="utf-8", newline="")
    # IV: seconds 400..499 with rows 450..454 absent; plain CSV with CRLF
    lines = ["STARTDATE,MeasurementData1hz\r\n"]
    for k in range(400, 500):
        if 450 <= k < 455:
            continue
        lines.append(f"{ts(k)},{synth(k)}\r\n")
    (ref / "freq_IV.csv").write_text("".join(lines), encoding="utf-8", newline="")
    # V: seconds 480..599, ComplexReportGroup header, identical to IV on 480..499
    lines = [h8 + '"ComplexReportGroup:1hz","ComplexReportGroup:1hz:Quality"\n']
    for k in range(480, 600):
        lines.append(row8.format(t=ts(k), v=f'"{synth(k)}"', q="0"))
    (ref / "freq_V.csv").write_text("".join(lines), encoding="utf-8", newline="")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("synthetic_ref")
    ref, derived, results = root / "ref", root / "derived", root / "results"
    write_synthetic(ref)
    br = _load_builder()
    br.main(["--ref-dir", str(ref), "--derived-dir", str(derived), "--results-dir", str(results),
             "--chunk-rows", "37", "--block", "50"])
    return ref, derived, results


def test_series_span_and_mask(built):
    _, derived, _ = built
    r = gref.load(derived, with_source=True)
    assert r.t0 == BASE + pd.Timedelta(seconds=5)          # first five rows of I are empty
    assert r.t_end == BASE + pd.Timedelta(seconds=599)
    assert len(r) == 595
    missing = set(range(150, 170)) | set(range(450, 455))
    for k in range(5, 600):
        assert r.valid[k - 5] == (0 if k in missing else 1), k
    assert np.isnan(r.freq[150 - 5])
    assert r.freq.dtype == np.float32 and r.valid.dtype == np.uint8


def test_overlap_rule_later_file_wins(built):
    _, derived, results = built
    r = gref.load(derived, with_source=True)
    i95 = r.index_of(BASE + pd.Timedelta(seconds=95))
    assert r.freq[i95] == np.float32(float(f"{synth(95) + 0.001:.6f}"))   # file II's value wins over I's
    assert r.source[i95] == 2
    assert r.source[r.index_of(ts(80))] == 1                 # only file I there
    assert r.source[r.index_of(ts(100))] == 2                # truncated row in I dropped; II has it anyway
    assert r.source[r.index_of(ts(295))] == 3                # III after II
    assert r.source[r.index_of(ts(490))] == 5                # V after IV
    stats = json.loads((results / "reference_build_stats.json").read_text())
    ov = {(o["a"], o["b"]): o for o in stats["overlaps"]}
    o12 = ov[("freq_I.csv", "freq_II.csv")]
    assert o12["both_valid"] == 10 and o12["identical"] == 9
    assert o12["max_abs_diff_hz"] == pytest.approx(0.001, abs=1e-9)
    assert ov[("freq_IV.csv", "freq_V.csv")]["share_identical"] == 1.0
    assert ov[("freq_IV.csv", "freq_V.csv")]["mean_abs_diff_by_lag_s"]["0"] == 0.0
    p1 = stats["parse"]["freq_I.csv"]
    assert p1["rows_kept"] == 100 and p1["rows_empty_value"] == 5
    assert p1["truncated_row_prefix"].startswith(f'"{ts(100)}"')
    assert p1["trailing_lines_dropped"] == 4
    assert [j["to_file"] for j in stats["junctions"]] == ["freq_II.csv", "freq_III.csv", "freq_V.csv"]


def test_lag_search_sign():
    """Synthetic random walk: file b labels every moment 37 s earlier than file a."""
    br = _load_builder()
    rng = np.random.default_rng(0)
    x = np.cumsum(rng.normal(0, 1e-3, 30000)) + 50.0
    a = x[:20000].copy()
    b = x[37:20037].copy()          # b[t] = a[t + 37]
    a[500:520] = np.nan              # a synthetic gap
    out = br.best_lag(a, b, 3000)
    assert out["best_lag_s"] == 37
    assert out["r_at_best"] > 0.999999 and out["identical_at_best"] == out["pairs_at_best"]


def test_gap_map(built):
    _, _, results = built
    g = json.loads((results / "reference_gaps.json").read_text())
    assert g["n_gaps_all"] == 2 and g["n_gaps_gt10s"] == 1
    gap = g["gaps"][0]
    assert gap["start_ist"] == ts(150) and gap["end_ist"] == ts(169) and gap["length_s"] == 20
    assert g["by_month"][0]["month"] == "2021-03"
    assert g["by_month"][0]["missing_s"] == 25
    assert (results / "reference_summary.md").exists()
    assert (results / "reference_coverage.png").stat().st_size > 1000


def test_time_index_mapping(built):
    _, derived, _ = built
    r = gref.load(derived)
    t = BASE + pd.Timedelta(seconds=321)
    i = r.index_of(t)
    assert i == 316 and r.time_of(i) == t
    assert r.index_of("2021-03-01 00:05:21.900") == 316          # floors to the second
    aware = pd.Timestamp("2021-02-28 18:35:21", tz="UTC")        # = 00:05:21 IST
    assert r.index_of(aware) == 316
    with pytest.raises(IndexError):
        r.index_of(BASE)                                         # before t0
    i0, i1 = r.scope(t, seconds=10)
    assert (i0, i1) == (306, 327)
    assert r.scope(t, days=1) == (0, len(r))                     # clipped
    f = r.filled(140, 175)
    assert np.all(np.isfinite(f))
    assert gref.unix_to_ist(0) == pd.Timestamp("1970-01-01 05:30:00")
    assert gref.ist_to_unix("1970-01-01 05:30:00") == 0
