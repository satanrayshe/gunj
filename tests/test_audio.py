"""Audio loader tests on SYNTHETIC files written to a temp folder."""
import shutil

import numpy as np
import pytest
import soundfile as sf
from scipy.signal import resample_poly

from gunj.audio import load_audio


def synth_file(path, sr, seconds=25, channels=2, seed=0):
    """SYNTHETIC multi-channel test tone plus noise."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(sr * seconds)) / sr
    base = 0.2 * np.sin(2 * np.pi * 100.0 * t) + 0.05 * rng.normal(size=t.size)
    data = np.stack([base * (1 + 0.1 * c) for c in range(channels)], axis=1)
    sf.write(str(path), data.astype(np.float32), sr, subtype="FLOAT")
    return data.mean(axis=1)


@pytest.mark.parametrize("sr,up,down", [(44_100, 10, 441), (48_000, 1, 48), (16_000, 1, 16)])
def test_block_resampling_matches_one_shot(tmp_path, sr, up, down):
    p = tmp_path / f"synthetic_{sr}.wav"
    mono = synth_file(p, sr)
    got, out_sr = load_audio(p, block_s=3.0)
    want = resample_poly(mono.astype(np.float32).astype(np.float64), up, down)
    assert out_sr == 1000
    assert got.shape == want.shape
    assert np.max(np.abs(got - want)) < 1e-5


def test_low_rate_file_kept_as_is(tmp_path):
    p = tmp_path / "synthetic_800.wav"
    mono = synth_file(p, 800, channels=1)
    got, out_sr = load_audio(p)
    assert out_sr == 800
    assert np.allclose(got, mono, atol=1e-6)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_ffmpeg_path(tmp_path):
    p = tmp_path / "synthetic_ff.wav"
    synth_file(p, 48_000, seconds=10)
    a, sr_a = load_audio(p)
    b, sr_b = load_audio(p, prefer_ffmpeg=True)
    assert sr_a == sr_b == 1000
    assert abs(a.size - b.size) <= 2
    n = min(a.size, b.size) - 200
    c = np.corrcoef(a[100:n], b[100:n])[0, 1]
    assert c > 0.98
