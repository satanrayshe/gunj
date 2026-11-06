"""Offline tests for scripts/make_splits.py helpers. No real data is read;
the drift table below is made up for the test (SYNTHETIC)."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import make_splits as ms  # noqa: E402


def test_wilson_known_values():
    lo, hi = ms.wilson(0, 25)
    assert lo == pytest.approx(0.0, abs=1e-12)
    assert hi == pytest.approx(0.1332, abs=1e-4)
    lo, hi = ms.wilson(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-4)
    assert hi == pytest.approx(0.7634, abs=1e-4)


def test_round_robin_quotas_fill_small_channels_first():
    q = ms.round_robin_quotas({"a": 3, "b": 5, "c": 30, "d": 30}, 20, ["a", "b", "c", "d"])
    assert sum(q.values()) == 20
    assert q["a"] == 3 and q["b"] == 5 and abs(q["c"] - q["d"]) <= 1


def test_round_robin_quotas_short_pool():
    q = ms.round_robin_quotas({"a": 2, "b": 1}, 10, ["a", "b"])
    assert q == {"a": 2, "b": 1}


def test_fits_lengths():
    assert ms.fits_lengths(1200) == "10"          # 120 s + 10 min fits, 20 min does not
    assert ms.fits_lengths(1320) == "10;20"
    assert ms.fits_lengths(3719) == "10;20;40"
    assert ms.fits_lengths(3720) == "10;20;40;60"
    assert ms.fits_lengths(40000) == "10;20;40;60"  # download is capped at 65 min


def test_overlaps():
    assert ms.overlaps(10, 20, [(15, 30)])
    assert not ms.overlaps(10, 20, [(20, 30)])
    assert not ms.overlaps(10, 20, [])


def test_drift_running_median_and_nearest_window():
    # SYNTHETIC drift table in the layout of results/time_axis_check.json
    per_window = [
        {"file": "freq_V.csv", "window_mid_ist": "2023-01-04", "shift_s": 0, "r": 0.7},
        {"file": "freq_V.csv", "window_mid_ist": "2023-01-11", "shift_s": 100, "r": 0.3},
        {"file": "freq_V.csv", "window_mid_ist": "2023-01-18", "shift_s": 2, "r": 0.6},
        {"file": "freq_V.csv", "window_mid_ist": "2023-01-25", "shift_s": 4, "r": 0.6},
    ]
    tab = ms.drift_table({"per_window": per_window})
    # the isolated outlier (100) is removed by the 3-window running median
    d, r = ms.drift_at(tab, 5, pd.Timestamp("2023-01-11 10:00:00"))
    assert d == 2.0 and r == 0.3
    d, r = ms.drift_at(tab, 5, pd.Timestamp("2023-01-26 00:00:00"))
    assert d == 3.0 and r == 0.6                  # edge: median of the two available
    assert ms.drift_at(tab, 4, pd.Timestamp("2023-01-11")) == (None, None)


def test_court_regex():
    assert ms.COURT_RE.search("Live proceedings of the High Court of Gujarat")
    assert ms.COURT_RE.search("Supreme Court hearing")
    assert ms.COURT_RE.search("उच्च न्यायालय")
    assert not ms.COURT_RE.search("Budget session, Bihar Vidhan Sabha")


def test_csv_bytes_is_order_independent():
    a = {"video_id": "b", "split": "test", "half": "", "release_timestamp": 2}
    b = {"video_id": "a", "split": "dev", "half": "", "release_timestamp": 5}
    c = {"video_id": "c", "split": "test", "half": "", "release_timestamp": 1}
    assert ms.csv_bytes([a, b, c]) == ms.csv_bytes([c, a, b])
    lines = ms.csv_bytes([a, b, c]).decode().splitlines()
    assert [ln.split(",")[0] for ln in lines[1:]] == ["a", "c", "b"]
