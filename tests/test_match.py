"""Matcher tests on a SYNTHETIC random-walk reference.

The "reference" is a SYNTHETIC mean-reverting random walk, and the queries
are segments cut from it with SYNTHETIC noise added.
"""
import numpy as np
import pytest

from gunj.match import fisher_margin, ncc_search
from gunj.synthetic import synthetic_enf_walk


def brute_ncc(q, ref, min_ref_std=0.003):
    x = q - q.mean()
    x = x / np.linalg.norm(x)
    y = ref.astype(np.float64) - 50.0
    w = np.lib.stride_tricks.sliding_window_view(y, q.size)
    wc = w - w.mean(axis=1, keepdims=True)
    den = np.sqrt((wc ** 2).sum(axis=1))
    r = (wc @ x) / den
    r[den / np.sqrt(q.size) < min_ref_std] = -np.inf
    return r


def query(ref, p0, n, seed=1, noise=0.002):
    rng = np.random.default_rng(seed)
    return ref[p0:p0 + n].astype(np.float64) + rng.normal(0, noise, n)


def test_blocked_fft_equals_brute_force():
    """Overlap-save blocks give the same r as a direct sliding computation."""
    ref = synthetic_enf_walk(30_000, seed=3, dtype=np.float32)
    q = query(ref, 12_345, 300)
    r_out = np.empty(ref.size - q.size + 1)
    ncc_search(q, ref, nfft=1 << 12, r_out=r_out)     # many small blocks
    r = brute_ncc(q, ref)
    fin = np.isfinite(r)
    assert np.array_equal(fin, np.isfinite(r_out))
    assert np.max(np.abs(r_out[fin] - r[fin])) < 1e-9


def test_embedded_segment_found_exactly(synth_ref_large):
    """(b) A known segment of the SYNTHETIC reference locks at the exact offset."""
    ref, valid = synth_ref_large
    p0 = 1_234_567
    res = ncc_search(query(ref, p0, 600), ref, valid, k=5)
    assert res.top.index == p0
    assert res.margin > 0 and res.confidence > 0
    assert res.runner_up is res.candidates[1]
    assert abs(res.runner_up.index - p0) >= 60
    rs = [c.r for c in res.candidates]
    assert rs == sorted(rs, reverse=True)
    idx = [c.index for c in res.candidates]
    assert all(abs(a - b) >= 60 for i, a in enumerate(idx) for b in idx[i + 1:])
    assert res.confidence == pytest.approx(fisher_margin(rs[0], rs[1]))
    assert res.n_positions == ref.size - 600 + 1


def test_block_size_does_not_change_answer(synth_ref_large):
    ref, valid = synth_ref_large
    q = query(ref, 777_777, 900, seed=4)
    a = ncc_search(q, ref, valid, nfft=1 << 16)
    b = ncc_search(q, ref, valid, nfft=1 << 21)
    assert [c.index for c in a.candidates] == [c.index for c in b.candidates]
    assert np.allclose([c.r for c in a.candidates], [c.r for c in b.candidates], atol=1e-9)


def test_scope_limits_candidates(synth_ref_small):
    """(c) Candidates come only from the requested scope."""
    ref, valid = synth_ref_small
    p0 = 100_000
    q = query(ref, p0, 600)
    inside = ncc_search(q, ref, valid, start=p0 - 86_400, stop=p0 + 86_400)
    assert inside.top.index == p0
    outside = ncc_search(q, ref, valid, start=p0 + 1_000, stop=p0 + 50_000)
    assert all(p0 + 1_000 <= c.index < p0 + 50_000 for c in outside.candidates)
    assert outside.scope == (p0 + 1_000, p0 + 50_000)
    empty = ncc_search(q, ref, valid, start=ref.size, stop=ref.size + 10)
    assert empty.candidates == [] and empty.confidence is None


def test_validity_mask_excludes_windows(synth_ref_small):
    """(c) Windows under 90% valid are excluded; small gaps are tolerated."""
    ref, valid = synth_ref_small
    p0 = 50_000
    q = query(ref, p0, 600)

    v = valid.copy()
    v[p0 + 100:p0 + 200] = 0                 # 100 of 600 s invalid -> 83% valid
    res = ncc_search(q, ref, v, k=20)
    assert all(abs(c.index - p0) > 0 for c in res.candidates)
    for c in res.candidates:
        assert v[c.index:c.index + 600].mean() >= 0.9

    v = valid.copy()
    v[p0 + 100:p0 + 130] = 0                 # 30 of 600 s invalid -> 95% valid
    assert ncc_search(q, ref, v).top.index == p0


def test_nan_values_are_invalid(synth_ref_small):
    """(c) NaN reference seconds count as invalid even without a mask."""
    ref, _ = synth_ref_small
    p0 = 50_000
    q = query(ref, p0, 600)
    f = ref.copy()
    f[p0 + 100:p0 + 200] = np.nan
    res = ncc_search(q, f, None, k=20)
    assert all(c.index != p0 for c in res.candidates)
    f = ref.copy()
    f[p0 + 100:p0 + 130] = np.nan
    assert ncc_search(q, f, None).top.index == p0


def test_strip_holds_block_maxima(synth_ref_small):
    ref, valid = synth_ref_small
    q = query(ref, 60_000, 600)
    r_out = np.empty(ref.size - 600 + 1)
    res = ncc_search(q, ref, valid, strip_bin=3600, r_out=r_out, nfft=1 << 15)
    assert res.strip.size == int(np.ceil(r_out.size / 3600))
    assert np.nanmax(res.strip) == pytest.approx(res.top.r, abs=1e-6)
    assert res.strip[0] == pytest.approx(np.max(r_out[:3600]), abs=1e-6)


def test_constant_trace_rejected(synth_ref_small):
    ref, valid = synth_ref_small
    with pytest.raises(ValueError):
        ncc_search(np.full(600, 50.0), ref, valid)
