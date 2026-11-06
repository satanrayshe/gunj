"""Edit-localisation tests. The audio, the reference and the edit are all
SYNTHETIC: hum generated from a SYNTHETIC random walk, with a SYNTHETIC
40 s cut made by deleting samples."""
import numpy as np
import pytest

from gunj.extract import extract_enf
from gunj.match import find_edits, local_offsets, ncc_search
from gunj.synthetic import cut_audio, synthetic_hum

AMPS = {1: 0.1, 2: 0.3, 3: 0.1}
L0 = 70_000          # where the SYNTHETIC clip starts in the SYNTHETIC reference
CUT_AT, CUT_LEN = 600.0, 40.0


@pytest.fixture(scope="module")
def clips(synth_ref_small):
    ref, _ = synth_ref_small
    seg = ref[L0:L0 + 1301].astype(np.float64)            # 1300 s of SYNTHETIC hum
    audio = synthetic_hum(seg, sr=1000, amps=AMPS, seed=21)
    edited = cut_audio(audio, 1000, CUT_AT, CUT_LEN)      # SYNTHETIC edit
    return extract_enf(audio, 1000), extract_enf(edited, 1000)


def test_unedited_clip_locks_and_has_no_edits(clips, synth_ref_small):
    ref, valid = synth_ref_small
    clean, _ = clips
    res = ncc_search(clean.freq, ref, valid)
    assert res.top.index == L0 + 8          # frame 0 is centred 8 s into the audio
    out = find_edits(clean.freq, ref, valid, res.top.index)
    assert out["edits"] == []
    lo = local_offsets(clean.freq, ref, valid, res.top.index)
    good = lo["r"] > 0.5
    # short windows jitter by a second or two around the true offset of 0
    assert np.all(np.abs(lo["offset"][good]) <= 3)
    assert np.median(lo["offset"][good]) == 0


def test_finds_synthetic_40s_cut(clips, synth_ref_small):
    """(e) The helper finds the SYNTHETIC 40 s cut, its place and its size."""
    ref, valid = synth_ref_small
    _, edited = clips
    res = ncc_search(edited.freq, ref, valid)
    # a whole-trace lock on an edited clip can fall between the two sides
    assert L0 + 8 <= res.top.index <= L0 + 8 + int(CUT_LEN)
    out = find_edits(edited.freq, ref, valid, res.top.index)
    assert len(out["edits"]) == 1, out["edits"]
    e = out["edits"][0]
    assert e["kind"] == "cut"
    assert abs(e["jump_s"] - CUT_LEN) <= 2
    assert abs(e["clip_time_s"] - CUT_AT) <= 10
