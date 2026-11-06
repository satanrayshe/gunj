"""Extractor tests on SYNTHETIC signals only.

The hum here is generated: its frequency follows a SYNTHETIC random walk
around 50 Hz, with harmonics and white noise added.
"""
import numpy as np
import pytest

from gunj.extract import extract_enf, extract_enf_naive
from gunj.synthetic import hann_truth, synthetic_enf_walk, synthetic_hum

AMPS = {1: 0.1, 2: 0.3, 3: 0.1}   # relative to unit white noise; SYNTHETIC


@pytest.fixture(scope="module")
def synth_case():
    f = synthetic_enf_walk(400, seed=1)                 # SYNTHETIC trace
    audio = synthetic_hum(f, sr=1000, amps=AMPS, seed=2)  # SYNTHETIC audio
    return f, audio


def rms(a, b):
    return float(np.sqrt(np.mean((np.asarray(a) - np.asarray(b)) ** 2)))


def test_synthetic_hum_trace_recovered(synth_case):
    """(a) SYNTHETIC hum: the recovered trace follows the known random walk."""
    f, audio = synth_case
    tr = extract_enf(audio, 1000)
    assert len(tr) == (len(audio) - 16000) // 1000 + 1
    assert tr.offset_s == 8.0
    truth = hann_truth(f, 1000, len(tr))
    err = rms(tr.freq, truth)
    assert err < 0.005, f"RMS error {err:.5f} Hz"
    # error is far below the spread of the SYNTHETIC walk itself
    assert err < 0.25 * np.std(truth)
    assert tr.hum_present
    assert tr.harmonic_median_snr[2] > 3
    assert tr.weights[2] == pytest.approx(tr.harmonic_median_snr[2] - 2)
    assert set(tr.weights) == {1, 2, 3, 4, 5, 6}
    assert tr.snr.shape == tr.freq.shape


def test_naive_baseline_is_coarser(synth_case):
    """The naive single-bin baseline is quantised to 1/16 Hz and less accurate."""
    f, audio = synth_case
    v1 = extract_enf(audio, 1000)
    nv = extract_enf_naive(audio, 1000)
    truth = hann_truth(f, 1000, len(v1))
    assert nv.harmonics == (1,)
    steps = nv.freq / (1.0 / 16.0)
    assert np.allclose(steps, np.round(steps))
    assert rms(nv.freq, truth) > rms(v1.freq, truth)


def test_resampled_input():
    """SYNTHETIC 8 kHz input is resampled to the 1 kHz analysis rate."""
    f = synthetic_enf_walk(140, seed=5)
    audio = synthetic_hum(f, sr=8000, amps=AMPS, seed=6)
    tr = extract_enf(audio, 8000)
    assert tr.sr == 1000
    assert rms(tr.freq, hann_truth(f, 1000, len(tr))) < 0.01


def test_pure_noise_has_no_hum():
    """(d, part 1) SYNTHETIC white noise: no harmonic reaches peak-to-floor 3."""
    noise = np.random.default_rng(5).normal(size=300 * 1000).astype(np.float32)
    tr = extract_enf(noise, 1000)
    assert not tr.hum_present
    assert max(tr.harmonic_median_snr.values()) < 3


def test_too_short_audio_raises():
    with pytest.raises(ValueError):
        extract_enf(np.zeros(10_000, dtype=np.float32), 1000)
