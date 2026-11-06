"""Command line: ``gunj date`` and ``gunj extract``.

    gunj date  <audio> [--scope full|90d|1d] [--claimed YYYY-MM-DD[THH:MM[:SS]]]
    gunj extract <audio> [--out trace.csv]

``gunj date`` prints JSON: the top-5 candidate recording start times in IST,
their r, the margin and confidence, the hum statistics, and a verdict. The
verdict is "no verdict" when there is no hum or when the confidence is below
the threshold in config.json. That threshold is a placeholder until it is
calibrated (the JSON says so).

The reference folder is, in order: --ref, $GUNJ_REF, $GUNJ_DERIVED, then
<threws>/data/derived. $GUNJ_REF may also point at grid_1s_f32.npy itself.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import __version__
from . import reference as gref
from .audio import load_audio
from .extract import ENFTrace, extract_enf, extract_enf_naive
from .match import RUNNER_UP_SEP_S, find_edits, ncc_search

CONFIG_PATH = Path(__file__).with_name("config.json")
SCOPES = {"full": None, "90d": 90.0, "1d": 1.0}
TIME_FMT = "%Y-%m-%d %H:%M:%S"


def load_config(path=None) -> dict:
    p = Path(path) if path else Path(os.environ.get("GUNJ_CONFIG", CONFIG_PATH))
    cfg = json.loads(p.read_text(encoding="utf-8"))
    cfg["_path"] = str(p)
    return cfg


def resolve_ref_dir(arg=None) -> Path:
    raw = arg or os.environ.get("GUNJ_REF")
    if raw:
        p = Path(raw)
        return p.parent if p.suffix == ".npy" else p
    return gref.default_derived_dir()


def _fmt(ts: pd.Timestamp) -> str:
    return pd.Timestamp(ts).round("s").strftime(TIME_FMT)


def parse_claimed(text: str):
    """(start, end) of the claimed time: a whole IST day for YYYY-MM-DD,
    a single instant when a time of day is given."""
    t = pd.Timestamp(text)
    if t.tzinfo is not None:
        t = gref.to_ist(t)
    date_only = len(text.strip()) <= 10
    if date_only:
        return t.normalize(), t.normalize() + pd.Timedelta(days=1), True
    return t, t, False


def scope_positions(ref, trace: ENFTrace, mode: str, claimed: str | None,
                    start_shift_s: float = 0.0):
    """Candidate positions [p0, p1) for the scope, plus a description.

    The scope bounds the recording START time. Position p aligns trace frame
    0 with reference second p, so the start is p - offset_s - start_shift_s.
    """
    days = SCOPES[mode]
    off = pd.Timedelta(seconds=trace.offset_s + start_shift_s)
    if days is None:
        p0, p1 = 0, len(ref)
        desc = {"mode": "full", "claimed": claimed}
    else:
        if not claimed:
            raise ValueError(f"--scope {mode} needs --claimed")
        c0, c1, date_only = parse_claimed(claimed)
        half = pd.Timedelta(days=days)
        start_lo, start_hi = c0 - half, c1 + half
        p0, _ = ref.window(start_lo + off, None)
        p1, _ = ref.window(start_hi + off, None)
        p1 = min(p1 + 1, len(ref))
        desc = {"mode": mode, "claimed": claimed,
                "claimed_kind": "whole IST day" if date_only else "instant",
                "plus_minus_days": days}
        if p1 <= p0:
            raise ValueError(f"claimed time {claimed} +- {days:g} d is outside the reference "
                             f"({_fmt(ref.t0)} .. {_fmt(ref.t_end)} IST)")
    desc["recording_start_from_ist"] = _fmt(ref.time_of(p0) - off)
    desc["recording_start_to_ist"] = _fmt(ref.time_of(max(p1 - 1, p0)) - off)
    return p0, p1, desc


def decide(trace: ENFTrace, res, threshold: float, calibrated: bool) -> tuple[str, str]:
    tag = "" if calibrated else " (placeholder threshold, not calibrated)"
    if not trace.hum_present:
        return "no verdict", (f"no mains hum: no harmonic has a median peak-to-floor ratio "
                              f">= {trace.hum_ratio:g}")
    if res is None or not res.candidates:
        return "no verdict", "no admissible reference window in the search scope"
    if res.confidence is None:
        return "no verdict", "no runner-up candidate to measure a margin against"
    if res.confidence < threshold:
        return "no verdict", f"confidence {res.confidence:.4f} is below threshold {threshold:g}{tag}"
    return "match", f"confidence {res.confidence:.4f} >= threshold {threshold:g}{tag}"


def date_trace(trace: ENFTrace, ref, scope: str = "full", claimed: str | None = None,
               config: dict | None = None, top_k: int | None = None,
               force_search: bool = False, edits: bool = False,
               start_shift_s: float = 0.0) -> dict:
    """Search ``trace`` against ``ref`` and build the JSON-ready result.

    ``start_shift_s`` is subtracted from every reported start time; use it
    when the trace was cut from an excerpt starting that many seconds into
    the recording, so times refer to the recording's own start.
    """
    cfg = config if config is not None else load_config()
    k = int(top_k or cfg.get("top_k", 5))
    thr = float(cfg["confidence_threshold"])
    calibrated = bool(cfg.get("confidence_threshold_calibrated", False))
    p0, p1, sdesc = scope_positions(ref, trace, scope, claimed, start_shift_s)
    off = pd.Timedelta(seconds=trace.offset_s + start_shift_s)

    res = None
    t_search = None
    if trace.hum_present or force_search:
        t = time.time()
        res = ncc_search(trace.freq, ref.freq, ref.valid, start=p0, stop=p1, k=k)
        t_search = round(time.time() - t, 2)
        sdesc["positions_with_valid_window"] = res.n_positions
    verdict, reason = decide(trace, res, thr, calibrated)

    cands = []
    if res is not None:
        for i, c in enumerate(res.candidates):
            cands.append({"rank": i + 1, "recording_start_ist": _fmt(ref.time_of(c.index) - off),
                          "r": round(c.r, 4), "ref_index": int(c.index)})
    out = {
        "tool": "gunj", "version": __version__,
        "extractor": trace.method,
        "trace_seconds": len(trace),
        "hum": trace.summary(),
        "reference": {"t0_ist": _fmt(ref.t0), "t_end_ist": _fmt(ref.t_end), "seconds": len(ref)},
        "scope": sdesc,
        "candidates": cands,
        "runner_up_min_separation_s": RUNNER_UP_SEP_S,
        "margin": None if res is None or res.margin is None else round(res.margin, 4),
        "confidence": None if res is None or res.confidence is None else round(res.confidence, 4),
        "confidence_definition": cfg.get("confidence_definition"),
        "threshold": {"value": thr, "calibrated": calibrated, "config": cfg.get("_path"),
                      "note": None if calibrated else "PLACEHOLDER, NOT CALIBRATED"},
        "verdict": verdict,
        "verdict_reason": reason,
        "search_seconds": t_search,
    }
    if verdict == "match":
        out["recording_start_ist"] = cands[0]["recording_start_ist"]
    if edits and res is not None and res.candidates:
        ed = find_edits(trace.freq, ref.freq, ref.valid, res.candidates[0].index,
                        offset_s=trace.offset_s, hop_s=trace.hop_s)
        an = ed["anchor"]
        out["edits"] = {
            "method": "basic: binary segmentation of the trace into constant-offset pieces",
            "anchor": an.get("chosen", "given"),
            "anchor_start_ist": _fmt(ref.time_of(an["used"]) - off),
            "found": [dict(e, clip_time=_mmss(e["clip_time_s"] + start_shift_s),
                           clip_time_s=round(e["clip_time_s"] + start_shift_s, 1))
                      for e in ed["edits"]],
            "params": ed["params"],
        }
    return out


def _mmss(s: float) -> str:
    s = int(round(s))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _extract(audio, sr, naive: bool) -> ENFTrace:
    return extract_enf_naive(audio, sr) if naive else extract_enf(audio, sr)


def _excerpt(audio, sr, start_s, length_s):
    a = int(round((start_s or 0) * sr))
    b = None if length_s is None else a + int(round(length_s * sr))
    return audio[a:b]


def cmd_date(args) -> int:
    cfg = load_config(args.config)
    t = time.time()
    audio, sr = load_audio(args.audio)
    dur = audio.shape[0] / sr
    audio = _excerpt(audio, sr, args.excerpt_start, args.excerpt_length)
    trace = _extract(audio, sr, args.naive)
    t_extract = round(time.time() - t, 2)
    ref_dir = resolve_ref_dir(args.ref)
    ref = gref.load(ref_dir)
    out = date_trace(trace, ref, scope=args.scope, claimed=args.claimed, config=cfg,
                     top_k=args.top, force_search=args.force_search, edits=args.edits,
                     start_shift_s=args.excerpt_start or 0.0)
    out = {"audio": {"path": str(args.audio), "duration_s": round(dur, 2), "analysis_sr": sr,
                     "excerpt_start_s": args.excerpt_start, "excerpt_length_s": args.excerpt_length},
           **out, "reference_dir": str(ref_dir), "extract_seconds": t_extract}
    _emit(out, args.json_out)
    return 0


def cmd_extract(args) -> int:
    audio, sr = load_audio(args.audio)
    audio = _excerpt(audio, sr, args.excerpt_start, args.excerpt_length)
    trace = _extract(audio, sr, args.naive)
    if args.out:
        df = pd.DataFrame({"t_s": trace.times(), "freq_hz": trace.freq,
                           "freq_raw_hz": trace.freq_raw, "snr": trace.snr})
        for i, k in enumerate(trace.harmonics):
            df[f"snr_k{k}"] = trace.harmonic_snr[i]
        tmp = Path(str(args.out) + ".tmp")
        df.to_csv(tmp, index=False, float_format="%.6f")
        tmp.replace(args.out)
    _emit({"audio": str(args.audio), "analysis_sr": sr, "hum": trace.summary(),
           "trace_seconds": len(trace), "offset_s": trace.offset_s,
           "out": args.out}, args.json_out)
    return 0


def _emit(obj, path=None):
    text = json.dumps(obj, indent=2, default=_json_default)
    if path:
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(text + "\n", encoding="utf-8")
        tmp.replace(path)                # never leave a half-written file
    sys.stdout.write(text + "\n")


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="gunj", description="Date Indian recordings from grid hum (ENF).")
    ap.add_argument("--version", action="version", version=f"gunj {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("audio", help="audio file (WAV/FLAC via soundfile, anything else via ffmpeg)")
        p.add_argument("--naive", action="store_true", help="use the naive single-bin 50 Hz baseline extractor")
        p.add_argument("--excerpt-start", type=float, default=None, metavar="S",
                       help="use audio from S seconds in")
        p.add_argument("--excerpt-length", type=float, default=None, metavar="S",
                       help="use only S seconds of audio")
        p.add_argument("--json-out", default=None, help="also write the JSON to this file")

    d = sub.add_parser("date", help="find when a recording was made")
    common(d)
    d.add_argument("--scope", choices=sorted(SCOPES), default="full")
    d.add_argument("--claimed", default=None, help="claimed date YYYY-MM-DD (IST) or date and time")
    d.add_argument("--ref", default=None, help="reference folder (default $GUNJ_REF, then data/derived)")
    d.add_argument("--config", default=None, help="config JSON (default gunj/config.json)")
    d.add_argument("--top", type=int, default=None, help="number of candidates (default from config)")
    d.add_argument("--force-search", action="store_true", help="search even when no hum is found")
    d.add_argument("--edits", action="store_true", help="also look for cuts relative to the rank-1 lock")
    d.set_defaults(func=cmd_date)

    e = sub.add_parser("extract", help="write the 1-Hz ENF trace to CSV")
    common(e)
    e.add_argument("--out", default=None, help="CSV path for the trace")
    e.set_defaults(func=cmd_extract)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError, RuntimeError, IndexError, KeyError) as exc:
        sys.stdout.write(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, indent=2) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
