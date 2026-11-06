"""Tests for scripts/uniqueness.py on a SYNTHETIC random-walk reference.

Every reference and query here is SYNTHETIC (gunj.synthetic.synthetic_enf_walk
plus generated noise); none of it is real grid data.
"""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gunj.match import ncc_search
from gunj.synthetic import synthetic_enf_walk

_SPEC = importlib.util.spec_from_file_location(
    "uniqueness", Path(__file__).resolve().parents[1] / "scripts" / "uniqueness.py")
U = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(U)


@pytest.fixture(scope="module")
def synth_ref_gappy():
    """SYNTHETIC 600,000-second reference with a few invalid stretches."""
    f = synthetic_enf_walk(600_000, seed=21, dtype=np.float32)
    v = np.ones(f.size, dtype=np.uint8)
    for a, n in ((50_000, 400), (210_000, 5_000), (400_123, 37)):
        v[a:a + n] = 0
        f[a:a + n] = np.nan
    return f, v


@pytest.mark.parametrize("length,scope", [(300, (0, None)), (1200, (100_000, 350_000)),
                                          (3600, (0, None))])
def test_batched_equals_ncc_search(synth_ref_gappy, length, scope):
    f, v = synth_ref_gappy
    rng = np.random.default_rng(length)
    starts = [int(rng.integers(scope[0], (scope[1] or f.size) - length)) for _ in range(3)]
    queries, truths = [], []
    for a in starts:
        x, _ = U.excerpt_values(type("R", (), {"freq": f, "valid": v})(), a, length)
        z = U.base_noise(5, length, a)
        for kind, sig in (("white", 0.0), ("white", 0.004), ("hann16", 0.008)):
            queries.append(x + U.make_noise(z, length, kind, sig))
            truths.append(a)
    got = U.batched_search(queries, truths, f, v, scope[0], scope[1], nfft=1 << 14, group=5, threads=3)
    for q, g in zip(queries, got):
        want = ncc_search(q, f, v, start=scope[0], stop=scope[1], k=U.K_SELECT, nfft=1 << 14)
        assert g["top_index"] == want.top.index
        assert g["r1"] == want.top.r
        assert g["runner_up_index"] == want.runner_up.index
        assert g["r2"] == want.runner_up.r
        assert g["n_positions"] == want.n_positions
    # noise-free queries lock exactly on their own start
    for i in range(0, len(queries), 3):
        assert got[i]["top_index"] == truths[i]
        assert got[i]["r_at_truth"] == pytest.approx(1.0, abs=1e-9)


def test_noise_kinds_have_requested_std():
    z = U.base_noise(1, 200_000, 0)
    w = U.make_noise(z, 200_000, "white", 0.02)
    h = U.make_noise(z, 200_000, "hann16", 0.02)
    assert np.std(w) == pytest.approx(0.02, rel=0.02)
    assert np.std(h) == pytest.approx(0.02, rel=0.05)
    # hann16 is smoothed: neighbouring seconds are strongly correlated, white ones are not
    assert np.corrcoef(h[:-1], h[1:])[0, 1] > 0.8
    assert abs(np.corrcoef(w[:-1], w[1:])[0, 1]) < 0.02
    assert not U.make_noise(z, 100, "white", 0.0).any()


def test_wilson_interval():
    lo, hi = U.wilson(0, 10)
    assert lo == 0.0 and hi == pytest.approx(0.2775, abs=1e-4)
    lo, hi = U.wilson(10, 10)
    assert lo == pytest.approx(0.7225, abs=1e-4) and hi == 1.0
    lo, hi = U.wilson(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-4) and hi == pytest.approx(0.5962, abs=1e-4)


def test_excerpt_starts_nested_and_valid(synth_ref_gappy):
    f, v = synth_ref_gappy
    c = U.ValidCounter(v)
    a5, _ = U.excerpt_starts(c, f.size, 3600, 40, seed=3)
    a3, _ = U.excerpt_starts(c, f.size, 3600, 25, seed=3)
    assert a5[:25] == a3
    assert all(c.frac(a, 3600) >= 0.9 for a in a5)


def test_load_done_drops_incomplete_groups(tmp_path):
    p = tmp_path / "t.csv"
    rows = []
    for j, nrows in ((0, 2), (1, 1)):
        for i in range(nrows):
            rows.append({c: 0 for c in U.TRIAL_COLS} | dict(scope="1d", length_s=300, excerpt_j=j,
                                                          noise_kind="white", sigma_hz=float(i)))
    U.append_rows(p, rows)
    done = U.load_done(p, n_cond=2)
    assert done == {("1d", 300, 0)}
    assert len(pd.read_csv(p)) == 2
