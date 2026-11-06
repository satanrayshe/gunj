"""End-to-end wiring check of ``gunj date`` on the real reference.

The audio is SYNTHETIC: generated hum whose frequency follows a stretch of
the real reference, plus white noise. The right answer is therefore known.
This checks file loading, extraction, the full-reference search, time
conversion and the JSON output. It is not an accuracy result on real
recordings.

Usage (from the repo root):
  .venv\\Scripts\\python.exe scripts\\cli_smoke_real_ref.py --ref-dir "..\\data\\derived" ^
      --work-dir <temp folder> --out results\\cli_smoke_real_ref.json
"""
from __future__ import annotations

import gunj  # noqa: F401  (first, so its OPENBLAS_NUM_THREADS default applies)
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from gunj import reference as gref
from gunj.synthetic import synthetic_hum

AMPS = {1: 0.05, 2: 0.2, 3: 0.05}   # the "medium" SYNTHETIC level


def run_cli(args):
    t = time.time()
    p = subprocess.run([sys.executable, "-m", "gunj", *args], capture_output=True, text=True)
    return json.loads(p.stdout), round(time.time() - t, 1), p.returncode


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref-dir", required=True)
    ap.add_argument("--work-dir", required=True, help="folder for the temporary SYNTHETIC WAV")
    ap.add_argument("--out", required=True)
    ap.add_argument("--minutes", type=int, default=30)
    ap.add_argument("--seed", type=int, default=3)
    args = ap.parse_args(argv)

    ref = gref.load(args.ref_dir, mmap=True)
    n = args.minutes * 60 + 1
    rng = np.random.default_rng(args.seed)
    i = None
    for cand in range(int(rng.integers(0, len(ref) - n)), len(ref) - n, 3600):
        if ref.valid[cand:cand + n].all():
            i = cand
            break
    if i is None:
        raise SystemExit("no fully valid stretch found")
    truth = ref.time_of(i)
    audio = synthetic_hum(np.asarray(ref.freq[i:i + n], dtype=np.float64), sr=1000,
                          amps=AMPS, seed=args.seed)
    wav = Path(args.work_dir) / "synthetic_hum_from_reference.wav"
    wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(wav), (0.05 * audio).astype(np.float32), 1000, subtype="FLOAT")

    runs = {}
    try:
        for label, extra in (("full", []),
                             ("1d", ["--scope", "1d", "--claimed", truth.strftime("%Y-%m-%d")])):
            out, secs, code = run_cli(["date", str(wav), "--ref", args.ref_dir, *extra])
            top = out["candidates"][0] if out.get("candidates") else None
            runs[label] = {
                "exit_code": code, "wall_seconds": secs,
                "top1_start_ist": top["recording_start_ist"] if top else None,
                "top1_correct": bool(top and top["recording_start_ist"] == truth.strftime("%Y-%m-%d %H:%M:%S")),
                "output": out,
            }
            print(label, code, secs, runs[label]["top1_correct"], flush=True)
    finally:
        wav.unlink(missing_ok=True)
    res = {
        "what": ("SYNTHETIC audio: generated hum following a stretch of the real reference, "
                 "plus white noise. Wiring check only; not an accuracy result."),
        "synthetic_amplitudes": AMPS, "minutes": args.minutes,
        "true_start_ist": truth.strftime("%Y-%m-%d %H:%M:%S"), "true_start_index": i,
        "runs": runs,
    }
    tmp = Path(args.out + ".tmp")
    tmp.write_text(json.dumps(res, indent=1), encoding="utf-8")
    tmp.replace(args.out)          # never leave a half-written result


if __name__ == "__main__":
    main()
