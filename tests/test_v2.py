"""Extractor v2 and weighted matcher tests on SYNTHETIC signals only.

The references and the hum here are SYNTHETIC random walks and generated
audio. The last test checks that v1 and the frozen matcher are unchanged.
"""
import hashlib
from pathlib import Path

import numpy as np
import pytest

from gunj.extract_v2 import V2Params, combine, extract_enf_v2, harmonic_tracks
from gunj.match import ncc_search
from gunj.match_v2 import search, wncc_search
from gunj.synthetic import hann_truth, synthetic_enf_walk, synthetic_hum

REPO = Path(__file__).resolve().parents[1]


def _query(ref, p0, n, seed=1, noise=0.002):
    rng = np.random.default_rng(seed)
    return ref[p0:p0 + n].astype(np.float64) + rng.normal(0, noise, n)


def test_unit_weights_equal_frozen_matcher(synth_ref_small):
    f, v = synth_ref_small
    q = _query(f, 123_456, 900)
    a = ncc_search(q, f, v, k=5)
    b = wncc_search(q, np.ones(q.size), f, v, k=5)
    assert [c.index for c in a.candidates] == [c.index for c in b.candidates]
    assert np.allclose([c.r for c in a.candidates], [c.r for c in b.candidates], atol=1e-9)
    assert b.confidence == pytest.approx(a.confidence, abs=1e-8)


def test_weighted_r_matches_brute_force(synth_ref_small):
    f, v = synth_ref_small
    n = 300
    q = _query(f, 50_000, n, seed=3)
    u = np.random.default_rng(4).uniform(0, 1, n)
    start, stop = 49_000, 51_000
    r_out = np.empty(stop - start)
    wncc_search(q, u, f, v, start=start, stop=stop, r_out=r_out)
    y = f.astype(np.float64) - 50.0
    W = u.sum()
    xc = q - (u * q).sum() / W
    for p in (49_000, 49_777, 50_000, 50_999):
        w = y[p:p + n]
        yc = w - (u * w).sum() / W
        r = (u * xc * yc).sum() / np.sqrt((u * xc * xc).sum() * (u * yc * yc).sum())
        assert r_out[p - start] == pytest.approx(r, abs=1e-9)


def test_zero_weight_on_corrupt_frames_helps(synth_ref_small):
    f, v = synth_ref_small
    n, p0 = 1200, 77_000
    q = _query(f, p0, n, seed=5)
    rng = np.random.default_rng(6)
    q[300:700] = 50.0 + rng.normal(0, 0.05, 400)          # SYNTHETIC garbage stretch
    u = np.ones(n)
    u[300:700] = 0.0
    plain = ncc_search(q, f, v)
    wtd = wncc_search(q, u, f, v)
    assert wtd.top.index == p0
    assert wtd.top.r > (plain.top.r if plain.top.index == p0 else 0.0)
    assert search(q, None, f, v).top.index == plain.top.index   # None -> frozen matcher


def test_v2_recovers_synthetic_trace_and_gates_noise_harmonics():
    walk = synthetic_enf_walk(600, seed=21)                               # SYNTHETIC
    audio = synthetic_hum(walk, sr=1000, amps={1: 0.05, 2: 0.2, 3: 0.05}, seed=22)
    tr = extract_enf_v2(audio, 1000, params=V2Params())
    truth = hann_truth(walk, 1000, len(tr))
    assert np.sqrt(np.mean((tr.freq - truth) ** 2)) < 0.01
    assert tr.hum_present
    # harmonics 4-6 carry no SYNTHETIC hum: noise only, weight 0
    assert all(tr.weights[k] == 0.0 for k in (4, 5, 6))
    assert tr.weights[2] == 1.0
    fw = tr.extra["frame_weight"]
    assert fw.shape == tr.freq.shape and fw.min() >= 0 and fw.max() <= 1
    off = extract_enf_v2(audio, 1000, params=V2Params(corr_weights=False))
    assert off.extra["frame_weight"] is None


def test_combine_consensus_and_fallback():
    rng = np.random.default_rng(0)
    n = 200
    base = 50 + 0.01 * np.sin(np.arange(n) / 10)
    hf = np.vstack([base, base, base + 0.2])                # third harmonic is off by 0.2 Hz
    hs = np.vstack([np.full(n, 6.0), np.full(n, 6.0), np.full(n, 6.0)])
    tr = combine((1, 2, 3), hf, hs + rng.uniform(0, 0.1, (3, n)), V2Params(consensus_hz=0.03, smooth=1))
    assert np.allclose(tr.freq, base, atol=1e-9)
    weak = combine((1, 2), hf[:2], np.full((2, n), 1.5), V2Params(smooth=1))
    assert weak.extra["gate_fallback"] is True and not weak.hum_present


def test_harmonic_tracks_band_parameter():
    walk = synthetic_enf_walk(120, seed=31) + 0.2                         # SYNTHETIC, offset to 50.2 Hz
    audio = synthetic_hum(walk, sr=1000, amps={1: 0.1, 2: 0.3}, seed=32)
    _, hf_n, _, _, _ = harmonic_tracks(audio, 1000, band_half=0.15, harmonics=(2,))
    _, hf_w, _, _, _ = harmonic_tracks(audio, 1000, band_half=0.30, harmonics=(2,))
    assert np.max(hf_n) <= 50.15 + 1e-6                     # narrow band clips at its edge
    assert abs(np.median(hf_w) - np.median(walk)) < 0.01     # wide band follows the hum


def test_v2_frozen_equals_dev_tuning_choice():
    """V2_FROZEN must be the candidate chosen in results/v2_tuning.json."""
    import json
    from gunj.extract_v2 import V2_FROZEN
    path = REPO / "results" / "v2_tuning.json"
    if not path.exists():
        pytest.skip("results/v2_tuning.json not present")
    d = json.loads(path.read_text(encoding="utf-8"))
    assert V2_FROZEN.to_dict() == d["chosen_params"]


def test_frozen_v1_files_unchanged():
    """PROTOCOL.md section 7 quotes these hashes; v2 must not touch v1."""
    want = {
        "gunj/extract.py": "ecca86510ed2dbcc7f8d71895949f3df09e0e80257e272d5537f8a806a6d998d",
        "gunj/match.py": "8810a350065256c2785720a1211fadca4c3bbe37b67a8cf44c465b9ed16f7343",
    }
    for rel, sha in want.items():
        assert hashlib.sha256((REPO / rel).read_bytes()).hexdigest() == sha, rel
