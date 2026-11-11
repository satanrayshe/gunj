"""File matching in video/place_voice.py (no audio and no ffmpeg needed)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "video"))
import place_voice as pv  # noqa: E402


def test_names_map_to_their_line_numbers():
    names = ["01.m4a", "line 2.mp3", "Line03.wav", "notes.txt"]
    assert pv.match_files(names, 3) == {1: "01.m4a", 2: "line 2.mp3", 3: "Line03.wav"}


@pytest.mark.parametrize("names, problem", [
    (["01.m4a", "1 again.m4a", "02.m4a"], "line 1: more than one file"),
    (["01.m4a", "02.m4a", "03.m4a"], "there is no line 3"),
    (["01.m4a"], "line 2: no recording"),
    (["intro.m4a", "01.m4a", "02.m4a"], "no line number"),
])
def test_bad_folders_are_refused_with_the_reason(names, problem):
    with pytest.raises(SystemExit, match=problem):
        pv.match_files(names, 2)
