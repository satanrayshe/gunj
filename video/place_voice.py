"""Build the narration track from lines recorded one at a time.

Record each numbered line of video/NARRATION.md as its own file, in any format ffmpeg reads
(m4a, mp3, wav, ...), and name each file by its line number: 01.m4a, 02.m4a, ... 11.m4a
("line 1.m4a" or "Line01.mp3" work too). Put the files in one folder, then from the repo root:

  python video/place_voice.py "path\\to\\folder"      # writes video/narration-voice.wav
  powershell -ExecutionPolicy Bypass -File video\\mux.ps1 video\\narration-voice.wav

Each recording is trimmed of the silence before and after the words, then starts when its caption
appears. A line that would run into the next caption is sped up without changing its pitch, by at
most 25%. If a line is still too long at that speed, the script names it, writes nothing and exits 1,
so that line can be recorded again a little faster.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from tts_narration import GAP_S, duration, parse

HERE = Path(__file__).resolve().parent
MAX_TEMPO = 1.25
AUDIO_EXT = {".m4a", ".mp3", ".wav", ".aac", ".ogg", ".opus", ".flac", ".webm", ".amr", ".3gp", ".mp4"}


def match_files(names: list[str], n_lines: int) -> dict[int, str]:
    """Map line number -> file name, using the first number in each audio file's name."""
    found: dict[int, list[str]] = {}
    for name in names:
        if Path(name).suffix.lower() not in AUDIO_EXT:
            continue
        m = re.search(r"\d+", Path(name).stem)
        if not m:
            raise SystemExit(f"{name}: no line number in the file name")
        found.setdefault(int(m.group()), []).append(name)
    problems = [f"line {k}: more than one file ({', '.join(sorted(v))})" for k, v in sorted(found.items()) if len(v) > 1]
    problems += [f"{v[0]}: there is no line {k} (lines are 1 to {n_lines})" for k, v in sorted(found.items())
                 if not 1 <= k <= n_lines]
    problems += [f"line {k}: no recording" for k in range(1, n_lines + 1) if k not in found]
    if problems:
        raise SystemExit("Recordings folder:\n  " + "\n  ".join(problems))
    return {k: v[0] for k, v in found.items()}


def trim(src: Path, out: Path, threshold_db: float) -> float:
    """Cut rumble below 80 Hz and the silence before and after the words; mono 48 kHz WAV."""
    edge = f"silenceremove=start_periods=1:start_threshold={threshold_db}dB:start_silence=0.08"
    af = f"highpass=f=80,{edge},areverse,{edge},areverse,aresample=48000"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af", af, "-ac", "1", str(out)], check=True)
    return duration(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("folder", type=Path, help="folder with one recording per narration line")
    ap.add_argument("--threshold", type=float, default=-45.0,
                    help="level in dB below which sound counts as silence when trimming (default -45)")
    args = ap.parse_args()
    if not args.folder.is_dir():
        raise SystemExit(f"Not a folder: {args.folder}")

    lines, end_s = parse((HERE / "NARRATION.md").read_text(encoding="utf-8"))
    files = match_files([p.name for p in args.folder.iterdir() if p.is_file()], len(lines))
    work = Path(tempfile.mkdtemp(prefix="gunj-voice-"))
    placed, too_long = [], []
    for i, (start, _text) in enumerate(lines):
        k = i + 1
        slot = (lines[i + 1][0] if k < len(lines) else end_s) - start - GAP_S
        clip = work / f"line{k:02d}.wav"
        d = trim(args.folder / files[k], clip, args.threshold)
        if d < 0.3:
            raise SystemExit(f"line {k} ({files[k]}): nothing louder than {args.threshold} dB; "
                             "check the recording or lower --threshold")
        tempo = max(1.0, d / slot)
        note = ""
        if tempo > MAX_TEMPO:
            too_long.append((k, d - slot * MAX_TEMPO))
            note = "  TOO LONG"
        elif tempo > 1.0:
            fast = work / f"line{k:02d}-fast.wav"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-af", f"atempo={tempo:.4f}", str(fast)],
                           check=True)
            clip = fast
            note = f"  sped up {100 * (tempo - 1):.0f}%"
        print(f"line {k:2d} at {start // 60}:{start % 60:02d}  {d:5.2f} s spoken, {slot:5.2f} s free{note}")
        placed.append((start, clip))
    if too_long:
        for k, over in too_long:
            print(f"Line {k} is {over:.1f} s too long even at {MAX_TEMPO:g}x. Record it again a little faster.")
        print("Nothing written.")
        return 1

    inputs, chains = [], []
    for j, (start, p) in enumerate(placed):
        inputs += ["-i", str(p)]
        chains.append(f"[{j}:a]adelay={start * 1000}:all=1[a{j}]")
    mix = "".join(f"[a{j}]" for j in range(len(placed)))
    filt = ";".join(chains) + f";{mix}amix=inputs={len(placed)}:normalize=0,apad,atrim=0:{end_s}[out]"
    target = HERE / "narration-voice.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", filt, "-map", "[out]",
                    "-ac", "1", "-ar", "48000", str(target)], check=True)
    print(f"wrote {target.relative_to(HERE.parent).as_posix()}  {duration(target):.2f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
