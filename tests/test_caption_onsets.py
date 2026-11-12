"""Caption timing in video/make_video.py: measured onsets from video/caption_onsets.json."""
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def load_make_video():
    spec = importlib.util.spec_from_file_location("make_video_under_test", REPO / "video" / "make_video.py")
    mv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mv)
    return mv


FIRST = "The first sentence is long enough that it cannot share a caption."
SECOND = "The second sentence also needs a caption of its own on screen."


def toy(tmp_path, onsets=None):
    mv = load_make_video()
    narration = tmp_path / "NARRATION.md"
    narration.write_text(f"1. **0:01** {FIRST} {SECOND}\n2. **0:12** Done.\n\nThe video ends at **0:15**.\n",
                         encoding="utf-8")
    mv.NARRATION = narration
    mv.ONSETS = tmp_path / "caption_onsets.json"
    if onsets is not None:
        mv.ONSETS.write_text(json.dumps(onsets), encoding="utf-8")
    return mv


def test_word_count_timing_without_onsets(tmp_path):
    chunks, end = toy(tmp_path).caption_chunks()
    assert end == 15
    (a0, a1, t0), (b0, b1, t1) = chunks[0]
    assert (t0, t1) == (FIRST, SECOND)
    assert a0 == 1 and b1 == pytest.approx(11.75) and a1 == pytest.approx(b0)


def test_measured_onset_moves_the_caption(tmp_path):
    mv = toy(tmp_path, {"lead_in_s": 0.1, "chunks": [{"line": 1, "chunk": 2, "text": SECOND, "voice_s": 4.0}]})
    (a0, a1, _), (b0, b1, _) = mv.caption_chunks()[0][0]
    assert a0 == 1 and a1 == pytest.approx(3.9) and b0 == pytest.approx(3.9) and b1 == pytest.approx(11.75)


def test_stale_onsets_are_refused(tmp_path):
    mv = toy(tmp_path, {"lead_in_s": 0.1, "chunks": [{"line": 1, "chunk": 2, "text": "Old words.", "voice_s": 4.0}]})
    with pytest.raises(SystemExit, match="differs"):
        mv.caption_chunks()


def test_out_of_order_onset_is_refused(tmp_path):
    mv = toy(tmp_path, {"lead_in_s": 0.0, "chunks": [{"line": 1, "chunk": 2, "text": SECOND, "voice_s": 1.2}]})
    with pytest.raises(SystemExit, match="out of order"):
        mv.caption_chunks()


def test_repo_onsets_fit_the_narration():
    mv = load_make_video()
    if not mv.ONSETS.exists():
        pytest.skip("no video/caption_onsets.json")
    chunks, _ = mv.caption_chunks()
    measured = mv.measured_onsets()
    assert measured, "the onsets file lists no captions"
    for (line, k), (start, _text) in measured.items():
        assert chunks[line - 1][k - 1][0] == pytest.approx(start)
