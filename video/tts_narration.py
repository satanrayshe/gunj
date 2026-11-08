"""Build the narration track for the v1 video from video/NARRATION.md with a synthetic voice.

Voice: Microsoft Edge online text-to-speech, en-IN-NeerjaNeural, through the edge-tts
command-line tool (run with uvx, no account or key). The voice is synthetic and is
credited as such in the README and the video description.

Each numbered line in NARRATION.md ("N. **m:ss** text") is spoken starting at its time.
If a line runs into the next one, it is re-spoken slightly faster (at most +25%).
The result is one WAV the length of the silent video, which video/mux.ps1 then puts
under the picture:

  python video/tts_narration.py            # writes video/narration-tts.wav
  powershell -ExecutionPolicy Bypass -File video\\mux.ps1 video\\narration-tts.wav
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
VOICE = "en-IN-NeerjaNeural"
GAP_S = 0.3                       # silence kept before the next line starts
MAX_RATE = 25                     # percent
LINE_RE = re.compile(r"^\s*\d+\.\s+\*\*(\d+):(\d\d)\*\*\s+(.+?)\s*$")
END_RE = re.compile(r"ends at \*\*(\d+):(\d\d)\*\*")
SAY = {"Gunj's": "Goonj's", "Gunj": "Goonj"}     # how the name is said; captions keep "Gunj"


def parse(md: str):
    lines = [(int(m) * 60 + int(s), text) for m, s, text in
             (mm.groups() for mm in map(LINE_RE.match, md.splitlines()) if mm)]
    end = END_RE.search(md)
    if not lines or not end:
        raise SystemExit("NARRATION.md: no numbered lines or no end time found")
    return lines, int(end.group(1)) * 60 + int(end.group(2))


def spoken(text: str) -> str:
    for a, b in SAY.items():
        text = re.sub(rf"\b{re.escape(a)}", b, text)
    return text


def duration(p: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(p)],
                         capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


def speak(text: str, out: Path, rate: int) -> float:
    cmd = ["uvx", "edge-tts", "--voice", VOICE, f"--rate={rate:+d}%", "--text", text, "--write-media", str(out)]
    subprocess.run(cmd, check=True, capture_output=True)
    return duration(out)


def main() -> int:
    lines, end_s = parse((HERE / "NARRATION.md").read_text(encoding="utf-8"))
    work = Path(tempfile.gettempdir()) / "gunj-tts"
    work.mkdir(exist_ok=True)
    clips = []
    for i, (start, text) in enumerate(lines):
        slot = (lines[i + 1][0] if i + 1 < len(lines) else end_s) - start - GAP_S
        out = work / f"line{i + 1:02d}.mp3"
        rate = 0
        d = speak(spoken(text), out, rate)
        if d > slot:
            rate = min(MAX_RATE, math.ceil((d / slot - 1) * 100) + 2)
            d = speak(spoken(text), out, rate)
        print(f"line {i + 1:2d} at {start // 60}:{start % 60:02d}  {d:5.2f} s of {slot:5.2f} s  rate {rate:+d}%"
              + ("  STILL TOO LONG" if d > slot + GAP_S else ""))
        clips.append((start, out))
    inputs, chains = [], []
    for k, (start, p) in enumerate(clips):
        inputs += ["-i", str(p)]
        chains.append(f"[{k}:a]aresample=48000,adelay={start * 1000}:all=1[a{k}]")
    mix = "".join(f"[a{k}]" for k in range(len(clips)))
    filt = ";".join(chains) + f";{mix}amix=inputs={len(clips)}:normalize=0,apad,atrim=0:{end_s}[out]"
    target = HERE / "narration-tts.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", filt, "-map", "[out]",
                    "-ac", "1", "-ar", "48000", str(target)], check=True)
    print(f"wrote {target.relative_to(HERE.parent)}  {duration(target):.2f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
