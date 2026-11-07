#!/usr/bin/env python
"""Resumable audio harvester for Gunj: audio only, first 65 min, 1-Hz traces.

Approval on record (STATE.md): on 7 Nov 2026 at about 01:20 IST Shrey
approved downloading AUDIO ONLY from the public YouTube streams in
data/splits.csv, with these limits, which this script enforces:

- lowest-bitrate audio-only format (yt-dlp "worstaudio"), first 65 min only
  (download_ranges 0-3900 s); converted to 1 kHz mono 16-bit;
- every trace is extracted, then the audio is deleted. Only video IDs,
  hashes and 1-Hz traces are kept, except the 1 kHz audio of the splits in
  --keep-audio-splits (Dev only), kept as FLAC under media/keep;
- no cookies, no sign-in, no proxies; at least 2.5 s between HTTP requests
  made by yt-dlp, and a random 10-20 s sleep between downloads;
- at the first bot check, sign-in wall, CAPTCHA or HTTP 429 the run STOPS
  (exit code 3), the row is marked "blocked" and nothing tries to get
  around it. A later run refuses to start until --clear-block is given,
  which only Shrey should decide.

Resume and kill safety
- One harvester at a time (an OS file lock on logs/harvest.lock).
- On start: leftover partial media under media/tmp and *.tmp files under
  traces/ and media/keep are deleted.
- Every output is written to name.tmp and then renamed, so a half-written
  file never looks finished. Order per video: FLAC (kept splits only), the
  trace file, then the manifest row.
- Video IDs whose manifest row is "done" (and whose files exist) are
  skipped. A trace file whose embedded record is complete is recovered into
  the manifest without downloading again (a kill between the two writes).
- Retryable failures (network, short audio) are retried up to 3 attempts
  on later runs; permanent ones (private, removed, no audio-only format)
  are not.
- On Windows, ffmpeg and other children are placed in a job object that is
  killed with this process, so a killed run leaves no orphan downloading.

Traces (traces/<split>/<id>.npz). Keys are "<extractor>__<part>__<array>".
Parts: "full" (all downloaded audio; v1 full is the PROTOCOL M1 hum flag),
"x10", "x20", "x40", "x60" (audio seconds [120, 120 + 60 m), each extracted
on its own; PROTOCOL section 8), and with --cuts "cut1", "cut2" (SYNTHETIC
cut versions of the 60-min excerpt, primary extractor only; PROTOCOL M5).
Arrays: freq (float64 Hz), snr (float32), fw (v2 correlation frame weights),
and for "full" the per-harmonic hfreq and hsnr (float32). "meta_json" holds
the manifest row, code hashes and per-trace metadata.

No trace is searched here. Test search results stay blind (PROTOCOL 14).

SYNTHETIC negatives (split negative_synthetic, IDs SYN-NOISE-xx and
SYN-WALK-xx) are generated with gunj.synthetic from each row's
synthetic_seed (PROTOCOL section 5). They are never downloaded, and the
downloader refuses anything that is not an 11-character YouTube ID.

Examples:
  Dev:   .venv\\Scripts\\python.exe scripts\\harvest.py --splits dev
             --extractors v1,naive --keep-audio-splits dev
  Test + negatives (only after Shrey approves PROTOCOL.md):
         .venv\\Scripts\\python.exe scripts\\harvest.py
             --splits test,negative,negative_synthetic --extractors v2,v1,naive
             --keep-audio-splits dev --cuts
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")   # before numpy is imported

import argparse
import csv
import datetime as dt
import hashlib
import json
import logging
import random
import re
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np            # noqa: E402
import soundfile as sf        # noqa: E402

from gunj.synthetic import cut_audio   # noqa: E402

SPLIT_SEED = 2026110701          # PROTOCOL.md section 5; scripts/make_splits.py SEED
DOWNLOAD_END_S = 3900            # first 65 min
EXCERPT_START_S = 120            # PROTOCOL section 8
SR = 1000
CUT_LENGTHS_S = (10, 20, 40, 60, 90, 120)   # PROTOCOL M5
CUT_MARGIN_S = 300
CUT_EXCERPT_MIN = 60
N_CUTS = 2
MIN_GAP_S = 2.5                  # between yt-dlp HTTP requests
SLEEP_RANGE_S = (10.0, 20.0)     # between downloads
MAX_ATTEMPTS = 3
MIN_AUDIO_FRACTION = 0.98

EXIT_OK, EXIT_ERROR, EXIT_BLOCKED, EXIT_LOCKED = 0, 1, 3, 4

# Same title screen as scripts/make_splits.py (COURT_RE).
COURT_RE = re.compile(
    r"\bcourts?\b|high\s*court|supreme\s*court|judicial|judiciary|judge|tribunal|"
    r"\blitigation\b|e-?court|न्यायालय|अदालत|न्यायिक", re.I)
BLOCK_RE = re.compile(
    r"not a bot|confirm you.?re not|captcha|unusual traffic|too many requests|"
    r"http error 429|\b429\b|sign.?in|log.?in to|/sorry/|consent\.youtube", re.I)
UNAVAILABLE_RE = re.compile(
    r"private video|members.only|join this channel|video unavailable|has been removed|"
    r"terminated|copyright|is not available|no longer available|requested format is not available|"
    r"no video formats|does not exist", re.I)
URL_RE = re.compile(r"https?://\S+")

MANIFEST_FIELDS = [
    "video_id", "split", "status", "reason", "retryable", "seconds", "audio_sha256",
    "source_sha256", "source_format", "source_ext", "source_abr_kbps", "source_protocol",
    "extractors", "primary", "cuts", "trace_path", "flac_path", "hum_present_v1",
    "hum_max_median_ratio_v1", "hum_json", "n_requests", "attempts", "started_utc",
    "finished_utc", "harvester_sha256", "v2_code_sha256",
]


class Blocked(RuntimeError):
    """YouTube showed a bot check, sign-in wall, CAPTCHA or HTTP 429."""


class Unavailable(RuntimeError):
    """The video cannot be fetched (private, removed, no audio-only format)."""


class DownloadFailed(RuntimeError):
    """A failure that may be transient (network, short audio)."""


# ---------------------------------------------------------------- utilities

def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sanitize(msg) -> str:
    """Drop URLs (they carry the client IP) and squash whitespace."""
    s = URL_RE.sub("<url>", str(msg))
    return re.sub(r"\s+", " ", s).strip()[:400]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_pcm(pcm: np.ndarray) -> str:
    """SHA-256 of the 1 kHz audio: its int16 little-endian samples."""
    return hashlib.sha256(np.ascontiguousarray(pcm, dtype="<i2").tobytes()).hexdigest()


def code_sha256(paths) -> str:
    h = hashlib.sha256()
    for p in paths:
        h.update(Path(p).read_bytes())
    return h.hexdigest()


def replace_retry(src: Path, dst: Path, tries: int = 20) -> None:
    """os.replace, retried briefly (Windows refuses while a reader has dst open)."""
    for i in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.25)


def rel(path: Path, root: Path = REPO) -> str:
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


# ----------------------------------------------------------------- manifest

def load_manifest(path: Path) -> dict:
    """video_id -> row. A leftover manifest .tmp is ignored (never trusted)."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8", newline="") as f:
        return {r["video_id"]: dict(r) for r in csv.DictReader(f)}


def write_manifest(path: Path, rows: dict) -> None:
    """Atomic write: path.tmp, flush, fsync, rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS, extrasaction="ignore")
        w.writeheader()
        for vid in sorted(rows, key=lambda v: (rows[v].get("split", ""), v)):
            w.writerow({k: rows[vid].get(k, "") for k in MANIFEST_FIELDS})
        f.flush()
        os.fsync(f.fileno())
    replace_retry(tmp, path)


def read_splits(path: Path, splits) -> list:
    with open(path, encoding="utf-8", newline="") as f:
        rows = [dict(r) for r in csv.DictReader(f)]
    want = list(splits)
    out = [r for r in rows if r["split"] in want]
    order = {s: i for i, s in enumerate(want)}
    return sorted(out, key=lambda r: order[r["split"]])   # stable: file order within a split


def excerpt_lengths(row: dict) -> list:
    raw = (row.get("excerpt_lengths_min") or "").strip()
    return [int(x) for x in raw.split(";") if x.strip()]


# --------------------------------------------------------------------- cuts

def cut_seed(video_id: str, split_seed: int = SPLIT_SEED) -> np.random.SeedSequence:
    """Seed for a stream's SYNTHETIC cuts, from the split seed and the video ID."""
    vid_int = int.from_bytes(hashlib.sha256(video_id.encode("utf-8")).digest()[:8], "little")
    return np.random.SeedSequence([int(split_seed), vid_int])


def cut_plan(video_id: str, split_seed: int = SPLIT_SEED, n_cuts: int = N_CUTS,
             excerpt_s: float = CUT_EXCERPT_MIN * 60, margin_s: float = CUT_MARGIN_S,
             lengths=CUT_LENGTHS_S) -> list:
    """SYNTHETIC cut positions for one stream (PROTOCOL M5).

    Each cut removes length_s seconds starting at at_s seconds into the
    60-min excerpt. length_s is drawn from ``lengths``; at_s is uniform with
    at least margin_s seconds of untouched excerpt on either side.
    """
    rng = np.random.default_rng(cut_seed(video_id, split_seed))
    out = []
    for j in range(n_cuts):
        length = int(rng.choice(np.asarray(lengths)))
        at = float(rng.uniform(margin_s, excerpt_s - margin_s - length))
        at = round(at, 3)                       # whole samples at 1 kHz
        out.append({"cut": j + 1, "at_s": at, "length_s": length,
                    "at_recording_s": round(EXCERPT_START_S + at, 3),
                    "excerpt_s": float(excerpt_s), "margin_s": float(margin_s),
                    "label": "SYNTHETIC cut (PROTOCOL M5)"})
    return out


# --------------------------------------------------------------- extraction

def get_extractor(name: str):
    if name == "v1":
        from gunj.extract import extract_enf
        return extract_enf
    if name == "naive":
        from gunj.extract import extract_enf_naive
        return extract_enf_naive
    if name == "v2":
        from gunj.extract_v2 import extract_enf_v2
        return extract_enf_v2
    raise ValueError(f"unknown extractor {name!r} (use v1, v2, naive)")


def _trace_meta(tr, audio_start_s: float, audio_seconds: float) -> dict:
    fw = tr.extra.get("frame_weight") if tr.extra else None
    m = {
        "method": tr.method, "n_frames": len(tr), "offset_s": tr.offset_s, "hop_s": tr.hop_s,
        "audio_start_s": float(audio_start_s), "audio_seconds": round(float(audio_seconds), 3),
        "frame0_recording_s": float(audio_start_s + tr.offset_s),
        "hum": tr.summary(), "harmonics": list(tr.harmonics),
        "weights": {str(k): float(v) for k, v in tr.weights.items()},
        "has_frame_weight": fw is not None,
    }
    if tr.extra and "params" in tr.extra:
        m["params"] = tr.extra["params"]
    return m


def _pack(arrays: dict, key: str, tr, full: bool) -> None:
    arrays[f"{key}__freq"] = np.asarray(tr.freq, dtype=np.float64)
    arrays[f"{key}__snr"] = np.asarray(tr.snr, dtype=np.float32)
    fw = tr.extra.get("frame_weight") if tr.extra else None
    if fw is not None:
        arrays[f"{key}__fw"] = np.asarray(fw, dtype=np.float32)
    if full:
        arrays[f"{key}__hfreq"] = np.asarray(tr.harmonic_freq, dtype=np.float32)
        arrays[f"{key}__hsnr"] = np.asarray(tr.harmonic_snr, dtype=np.float32)


def build_traces(audio: np.ndarray, sr: int, video_id: str, lengths_min, extractors,
                 primary: str, cuts: bool, extractor_fns: dict | None = None,
                 log=None) -> tuple:
    """Extract every trace for one stream. Returns (arrays, meta).

    ``audio`` is float32 mono at ``sr`` (1 kHz), starting at the recording's
    first sample. Nothing is searched.
    """
    fns = extractor_fns or {e: get_extractor(e) for e in extractors}
    n_s = audio.shape[0] / sr
    arrays, meta = {}, {"traces": {}, "cuts": []}
    for ext in extractors:
        fn = fns[ext]
        parts = [("full", 0.0, None)]
        for m in lengths_min:
            if EXCERPT_START_S + 60 * m <= n_s + 1e-9:
                parts.append((f"x{m}", float(EXCERPT_START_S), float(60 * m)))
        for part, a, length in parts:
            i0 = int(round(a * sr))
            i1 = None if length is None else i0 + int(round(length * sr))
            seg = audio[i0:i1]
            t = time.time()
            tr = fn(seg, sr)
            key = f"{ext}__{part}"
            _pack(arrays, key, tr, full=(part == "full"))
            meta["traces"][key] = _trace_meta(tr, a, seg.shape[0] / sr)
            if log:
                log.info("  %s: %d frames, hum=%s, %.1f s", key, len(tr), tr.hum_present,
                         time.time() - t)
    if cuts and CUT_EXCERPT_MIN in lengths_min and n_s + 1e-9 >= EXCERPT_START_S + 60 * CUT_EXCERPT_MIN:
        fn = fns[primary]
        i0 = EXCERPT_START_S * sr
        excerpt = audio[i0:i0 + CUT_EXCERPT_MIN * 60 * sr]
        for c in cut_plan(video_id):
            edited = cut_audio(excerpt, sr, c["at_s"], c["length_s"])     # SYNTHETIC edit
            tr = fn(edited, sr)
            key = f"{primary}__cut{c['cut']}"
            _pack(arrays, key, tr, full=False)
            tm = _trace_meta(tr, float(EXCERPT_START_S), edited.shape[0] / sr)
            tm["synthetic_cut"] = c
            tm["SYNTHETIC"] = True
            meta["traces"][key] = tm
            meta["cuts"].append(c)
            if log:
                log.info("  %s: SYNTHETIC cut %.0f s at %.3f s, %d frames", key,
                         c["length_s"], c["at_s"], len(tr))
    return arrays, meta


def save_npz_atomic(path: Path, arrays: dict, meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    payload = dict(arrays)
    payload["meta_json"] = np.array(json.dumps(meta, ensure_ascii=False, default=_json_default))
    with open(tmp, "wb") as f:
        np.savez_compressed(f, **payload)
        f.flush()
        os.fsync(f.fileno())
    replace_retry(tmp, path)


def read_npz_meta(path: Path) -> dict | None:
    try:
        with np.load(path, allow_pickle=False) as z:
            return json.loads(str(z["meta_json"][()]))
    except Exception:
        return None


def _json_default(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def hum_fields(meta: dict) -> dict:
    out = {}
    summary = {}
    for key, tm in meta["traces"].items():
        ext, part = key.split("__")
        if part != "full":
            continue
        h = tm["hum"]
        summary[ext] = {"present": h["present"], "median_peak_to_floor": h["median_peak_to_floor"]}
    v1 = meta["traces"].get("v1__full")
    if v1:
        meds = v1["hum"]["median_peak_to_floor"]
        out["hum_present_v1"] = str(bool(v1["hum"]["present"]))
        out["hum_max_median_ratio_v1"] = f"{max(meds.values()):.3f}" if meds else ""
    out["hum_json"] = json.dumps(summary, separators=(",", ":"), sort_keys=True)
    return out


# -------------------------------------------------------------------- audio

def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg is not on PATH")
    return exe


def is_synthetic_row(r: dict) -> bool:
    return r.get("split") == "negative_synthetic" or (r.get("video_id") or "").startswith("SYN-")


def synthetic_negative_audio(video_id: str, seed: int) -> np.ndarray:
    """SYNTHETIC negative audio, 3,900 s at 1 kHz (PROTOCOL section 5).

    SYN-NOISE-xx: SYNTHETIC white Gaussian noise, standard deviation 1.
    SYN-WALK-xx: synthetic_hum(synthetic_enf_walk(3901, seed), sr=1000,
    seed=seed): SYNTHETIC hum following a SYNTHETIC grid that is not India's.
    """
    from gunj.synthetic import synthetic_enf_walk, synthetic_hum
    n_s = DOWNLOAD_END_S
    if video_id.startswith("SYN-NOISE-"):
        return np.random.default_rng(int(seed)).standard_normal(n_s * SR).astype(np.float32)
    if video_id.startswith("SYN-WALK-"):
        return synthetic_hum(synthetic_enf_walk(n_s + 1, seed=int(seed)), sr=SR, seed=int(seed))
    raise ValueError(f"unknown SYNTHETIC negative id {video_id!r}")


def to_1k_wav(src: Path, dst: Path, max_s: float = DOWNLOAD_END_S) -> None:
    """Decode src to 1 kHz mono 16-bit PCM WAV (first max_s seconds)."""
    tmp = dst.with_name(dst.name + ".tmp.wav")
    cmd = [ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(src), "-vn", "-sn", "-dn", "-t", str(max_s), "-ac", "1", "-ar", str(SR),
           "-c:a", "pcm_s16le", "-map_metadata", "-1", "-bitexact", str(tmp)]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if p.returncode != 0 or not tmp.exists():
        raise DownloadFailed("ffmpeg conversion failed: " + sanitize(p.stderr.decode(errors="replace")[-300:]))
    replace_retry(tmp, dst)


def read_pcm16(path: Path) -> np.ndarray:
    data, sr = sf.read(str(path), dtype="int16", always_2d=True)
    if sr != SR:
        raise DownloadFailed(f"expected {SR} Hz audio, got {sr}")
    return np.ascontiguousarray(data[:, 0] if data.shape[1] == 1 else data.mean(axis=1).astype(np.int16))


def write_flac_atomic(pcm: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    sf.write(str(tmp), pcm, SR, format="FLAC", subtype="PCM_16")
    back = read_pcm16_any(tmp)
    if sha256_pcm(back) != sha256_pcm(pcm):
        tmp.unlink(missing_ok=True)
        raise RuntimeError("FLAC round trip changed the samples")
    replace_retry(tmp, path)


def read_pcm16_any(path: Path) -> np.ndarray:
    with sf.SoundFile(str(path)) as f:
        if f.samplerate != SR:
            raise DownloadFailed(f"expected {SR} Hz audio, got {f.samplerate}")
        data = f.read(dtype="int16", always_2d=True)
    return np.ascontiguousarray(data[:, 0])


# --------------------------------------------------------------- downloader

class YtDlpDownloader:
    """yt-dlp Python API: worst audio-only format, 0-3900 s, no cookies.

    Every HTTP request yt-dlp itself makes is spaced by at least MIN_GAP_S
    and counted (media segments are fetched by ffmpeg, not counted here).
    """

    def __init__(self, log: logging.Logger, min_gap: float = MIN_GAP_S):
        import yt_dlp                                    # noqa: F401
        self.log = log
        self.min_gap = float(min_gap)
        self._last = 0.0
        self.total_requests = 0

    def _make_ydl(self, params):
        import yt_dlp
        from yt_dlp.networking.exceptions import HTTPError
        outer = self

        class GapYDL(yt_dlp.YoutubeDL):
            n_requests = 0

            def urlopen(self, req):
                gap = time.monotonic() - outer._last
                if gap < outer.min_gap:
                    time.sleep(outer.min_gap - gap)
                self.n_requests += 1
                outer.total_requests += 1
                try:
                    resp = super().urlopen(req)
                    final = getattr(resp, "url", "") or ""
                    if "/sorry/" in final or "consent.youtube" in final or "accounts.google" in final:
                        raise Blocked("redirected to a bot check or sign-in page")
                    return resp
                except HTTPError as e:
                    if e.status == 429:
                        raise Blocked("HTTP 429 Too Many Requests") from e
                    raise
                finally:
                    outer._last = time.monotonic()

        return GapYDL(params)

    def fetch(self, video_id: str, outdir: Path) -> dict:
        from yt_dlp.utils import DownloadError, ExtractorError, download_range_func
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
            raise Unavailable(f"{video_id!r} is not a YouTube video ID; nothing was requested")
        outdir.mkdir(parents=True, exist_ok=True)
        report = outdir / "ffreport.log"
        log = self.log

        class YLog:
            def debug(self, m):
                if not m.startswith("[debug]"):
                    log.debug("yt-dlp: %s", sanitize(m))

            def info(self, m):
                log.debug("yt-dlp: %s", sanitize(m))

            def warning(self, m):
                log.warning("yt-dlp: %s", sanitize(m))

            def error(self, m):
                log.error("yt-dlp: %s", sanitize(m))

        params = {
            "format": "worstaudio",                  # lowest-bitrate audio-only format
            "download_ranges": download_range_func(None, [(0, DOWNLOAD_END_S)]),
            "outtmpl": {"default": "%(id)s.src.%(ext)s"},
            "paths": {"home": str(outdir), "temp": str(outdir)},
            "quiet": True, "no_warnings": False, "noprogress": True, "logger": YLog(),
            "noplaylist": True, "cachedir": False, "overwrites": True, "continuedl": False,
            "sleep_interval_requests": MIN_GAP_S,
            "retries": 3, "fragment_retries": 3, "extractor_retries": 1, "socket_timeout": 30,
            "writeinfojson": False, "writethumbnail": False, "writesubtitles": False,
            "writeautomaticsub": False, "getcomments": False, "postprocessors": [],
            "js_runtimes": {},                       # no JS runtime is needed for HLS audio
        }
        # ffmpeg (run by yt-dlp for download_ranges) writes its errors here.
        try:
            rep = os.path.relpath(report, os.getcwd()).replace("\\", "/")
        except ValueError:
            rep = None
        old_rep = os.environ.get("FFREPORT")
        if rep and ":" not in rep:
            os.environ["FFREPORT"] = f"file={rep}:level=16"
        ydl = self._make_ydl(params)
        try:
            with ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
        except Blocked:
            raise
        except (DownloadError, ExtractorError) as e:
            msg = sanitize(e)
            ff = _ffreport_errors(report)
            if BLOCK_RE.search(msg) or BLOCK_RE.search(ff):
                raise Blocked(msg + (" | ffmpeg: " + ff if ff else "")) from e
            if UNAVAILABLE_RE.search(msg):
                raise Unavailable(msg) from e
            raise DownloadFailed(msg + (" | ffmpeg: " + ff if ff else "")) from e
        except Exception as e:                       # network and other errors
            raise DownloadFailed(f"{type(e).__name__}: {sanitize(e)}") from e
        finally:
            if old_rep is None:
                os.environ.pop("FFREPORT", None)
            else:
                os.environ["FFREPORT"] = old_rep
        ff = _ffreport_errors(report)
        if BLOCK_RE.search(ff):
            raise Blocked("ffmpeg: " + ff)
        reqs = info.get("requested_downloads") or []
        path = Path(reqs[0]["filepath"]) if reqs and reqs[0].get("filepath") else None
        if path is None or not path.exists():
            raise DownloadFailed("yt-dlp reported success but no file was written"
                                 + (" | ffmpeg: " + ff if ff else ""))
        vcodec = info.get("vcodec")
        if vcodec not in (None, "none"):
            path.unlink(missing_ok=True)
            raise Unavailable(f"selected format {info.get('format_id')} is not audio-only")
        abr = info.get("abr") or info.get("tbr")
        return {"path": path, "format_id": str(info.get("format_id") or ""),
                "ext": str(info.get("ext") or ""), "abr": "" if abr is None else f"{float(abr):.1f}",
                "protocol": str(info.get("protocol") or ""), "n_requests": ydl.n_requests,
                "title": info.get("title") or "", "duration": info.get("duration")}


def _ffreport_errors(report: Path) -> str:
    """Error lines from an ffmpeg report, without the command line (it holds URLs)."""
    if not report.exists():
        return ""
    try:
        lines = report.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    keep = [ln for ln in lines if ln.strip() and not ln.startswith(("Command line", '"', "ffmpeg started"))]
    return sanitize(" ".join(keep[-6:]))


# ---------------------------------------------------------------- run state

class RunLock:
    """Exclusive OS file lock, released by the OS if the process dies."""

    def __init__(self, path: Path):
        self.path = path
        self.f = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.f.close()
            self.f = None
            return False
        return True

    def release(self) -> None:
        if self.f is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self.f.close()
        self.f = None


_JOB = None


def kill_children_with_me(log=None) -> bool:
    """Windows: put this process in a job that kills its children when it dies."""
    global _JOB
    if os.name != "nt" or _JOB is not None:
        return _JOB is not None
    try:
        import ctypes
        from ctypes import wintypes

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IOC(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class EXT(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IOC),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return False
        info = EXT()
        info.BasicLimitInformation.LimitFlags = 0x2000      # KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info)):
            return False
        if not k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(k32.GetCurrentProcess())):
            return False
        _JOB = job
        return True
    except Exception as e:                                   # best effort only
        if log:
            log.warning("could not create a kill-on-close job object: %s", e)
        return False


def cleanup_partials(media_tmp: Path, trace_root: Path, keep_root: Path, log=None) -> int:
    """Delete leftover partial media and *.tmp outputs. Returns items removed."""
    n = 0
    if media_tmp.exists():
        for p in media_tmp.iterdir():
            try:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
                n += 1
            except OSError as e:
                if log:
                    log.warning("could not delete %s: %s", rel(p), e)
    for root in (trace_root, keep_root):
        if root.exists():
            for p in root.rglob("*.tmp"):
                try:
                    p.unlink()
                    n += 1
                except OSError:
                    pass
    return n


def row_is_complete(m: dict, extractors, trace_root: Path, keep_root: Path, keep_splits,
                    cuts_needed: bool, repo: Path = REPO) -> bool:
    if not m or m.get("status") != "done":
        return False
    tp = m.get("trace_path") or ""
    if not tp or not (repo / tp).exists():
        return False
    have = set(filter(None, (m.get("extractors") or "").split(",")))
    if not set(extractors) <= have:
        return False
    if cuts_needed and (m.get("cuts") or "0") in ("", "0"):
        return False
    if m.get("split") in keep_splits:
        fp = m.get("flac_path") or ""
        if not fp or not (repo / fp).exists():
            return False
    return True


def plan(split_rows, manifest: dict, extractors, trace_root: Path, keep_root: Path,
         keep_splits, cuts: bool, cut_splits, repo: Path = REPO) -> list:
    """(row, action, why) per stream; action is "process" or "skip"."""
    out = []
    for r in split_rows:
        vid = r["video_id"]
        m = manifest.get(vid)
        cuts_needed = bool(cuts and r["split"] in cut_splits and CUT_EXCERPT_MIN in excerpt_lengths(r))
        if COURT_RE.search(r.get("title") or ""):
            out.append((r, "skip", "court-like title"))
        elif m is None:
            out.append((r, "process", "new"))
        elif m.get("status") == "done":
            if row_is_complete(m, extractors, trace_root, keep_root, keep_splits, cuts_needed, repo):
                out.append((r, "skip", "done"))
            else:
                out.append((r, "process", "done row incomplete for this request"))
        elif m.get("status") == "failed":
            if m.get("retryable") == "True" and int(m.get("attempts") or 0) < MAX_ATTEMPTS:
                out.append((r, "process", "retry"))
            else:
                out.append((r, "skip", "failed (not retried)"))
        elif m.get("status") == "skipped":
            out.append((r, "skip", "skipped earlier"))
        else:                                   # blocked: only reached with --clear-block
            out.append((r, "process", f"previous status {m.get('status')}"))
    return out


def recover_from_trace(path: Path, extractors, keep_splits, cuts_needed: bool,
                       repo: Path = REPO) -> dict | None:
    """Manifest row stored in a finished trace file, if it covers this request."""
    if not path.exists():
        return None
    meta = read_npz_meta(path)
    if not meta or not meta.get("complete"):
        return None
    row = dict(meta.get("manifest_row") or {})
    if not row:
        return None
    row["status"] = "done"
    row["reason"] = "recovered from trace file"
    if row_is_complete(row, extractors, path.parent, path.parent, keep_splits, cuts_needed, repo):
        return row
    return None


# ---------------------------------------------------------------------- run

class Ctx:
    def __init__(self, args, log, downloader, sleep=time.sleep, repo: Path = REPO):
        self.args = args
        self.log = log
        self.downloader = downloader
        self.sleep = sleep
        self.repo = repo
        self.run_id = uuid.uuid4().hex[:8]
        self.media_tmp = repo / "media" / "tmp"
        self.keep_root = repo / "media" / "keep"
        self.trace_root = repo / "traces"
        self.manifest_path = repo / args.manifest
        self.splits_path = repo / args.splits_csv
        self.downloads_done = 0
        self.extractors = [e for e in args.extractors.split(",") if e]
        self.primary = args.primary or self.extractors[0]
        self.keep_splits = set(filter(None, (args.keep_audio_splits or "").split(",")))
        self.cut_splits = set(filter(None, (args.cut_splits or "").split(",")))
        self.harvester_sha = code_sha256([Path(__file__).resolve()])
        v2_files = [repo / "gunj" / "extract_v2.py", repo / "gunj" / "match_v2.py"]
        self.v2_sha = code_sha256(v2_files) if "v2" in self.extractors and all(p.exists() for p in v2_files) else ""


def v2_frozen_check(repo: Path, log) -> tuple:
    """(ok, message): working-tree v2 code equals the code at tag extractor-v2."""
    files = ["gunj/extract_v2.py", "gunj/match_v2.py"]
    try:
        tag = subprocess.run(["git", "-C", str(repo), "rev-parse", "-q", "--verify", "refs/tags/extractor-v2"],
                             capture_output=True, text=True, check=False)
        if tag.returncode != 0:
            return False, "git tag extractor-v2 does not exist (v2 is not frozen)"
        for f in files:
            blob = subprocess.run(["git", "-C", str(repo), "show", f"extractor-v2:{f}"],
                                  capture_output=True, check=False)
            if blob.returncode != 0:
                return False, f"{f} is not in tag extractor-v2"
            if hashlib.sha256(blob.stdout).hexdigest() != hashlib.sha256((repo / f).read_bytes()).hexdigest():
                return False, f"{f} differs from tag extractor-v2"
        return True, "v2 code matches tag extractor-v2"
    except OSError as e:
        return False, f"git not available: {e}"


def process_video(r: dict, prev: dict | None, ctx: Ctx) -> dict:
    """Harvest one stream. Returns its manifest row. Raises Blocked to stop."""
    vid, split = r["video_id"], r["split"]
    log = ctx.log
    lengths = excerpt_lengths(r)
    cuts_needed = bool(ctx.args.cuts and split in ctx.cut_splits and CUT_EXCERPT_MIN in lengths)
    trace_path = ctx.trace_root / split / f"{vid}.npz"
    flac_path = ctx.keep_root / f"{vid}.flac"
    keep = split in ctx.keep_splits
    attempts = int((prev or {}).get("attempts") or 0) + 1
    row = {"video_id": vid, "split": split, "status": "", "reason": "", "retryable": "",
           "extractors": ",".join(ctx.extractors), "primary": ctx.primary,
           "attempts": str(attempts), "started_utc": now_utc(),
           "harvester_sha256": ctx.harvester_sha, "v2_code_sha256": ctx.v2_sha, "cuts": "0"}
    work = ctx.media_tmp / f"{vid}.{ctx.run_id}"
    t0 = time.time()
    try:
        if is_synthetic_row(r):
            # SYNTHETIC negative: generated here, never downloaded, never kept.
            audio = synthetic_negative_audio(vid, int(r["synthetic_seed"]))
            row.update(source_sha256="", source_format="SYNTHETIC", source_ext="", source_abr_kbps="",
                       source_protocol="generated (gunj.synthetic)", n_requests="0",
                       seconds=f"{audio.shape[0] / SR:.3f}",
                       audio_sha256=hashlib.sha256(np.ascontiguousarray(audio, dtype="<f4").tobytes()).hexdigest())
            arrays, tmeta = build_traces(audio, SR, vid, lengths, ctx.extractors, ctx.primary, False, log=log)
            del audio
            row["cuts"] = "0"
            row.update(hum_fields(tmeta))
            row["trace_path"] = rel(trace_path, ctx.repo)
            row["status"], row["reason"], row["retryable"] = "done", "SYNTHETIC negative (float32 audio hash)", ""
            row["finished_utc"] = now_utc()
            meta = {"video_id": vid, "split": split, "complete": True, "manifest_row": row, "sr": SR,
                    "SYNTHETIC": True, "synthetic_seed": int(r["synthetic_seed"]),
                    "excerpt_start_s": EXCERPT_START_S, "excerpt_lengths_min": lengths,
                    "release_timestamp": r.get("release_timestamp"),
                    "note": "SYNTHETIC negative generated with gunj.synthetic (PROTOCOL section 5).", **tmeta}
            save_npz_atomic(trace_path, arrays, meta)
            log.info("%s: SYNTHETIC negative done, trace %s", vid, rel(trace_path, ctx.repo))
            return row
        pcm = None
        src_info = {}
        if (prev and prev.get("audio_sha256") and flac_path.exists()):
            back = read_pcm16_any(flac_path)
            if sha256_pcm(back) == prev["audio_sha256"]:
                pcm = back
                src_info = {k: prev.get(k, "") for k in ("source_sha256", "source_format", "source_ext",
                                                         "source_abr_kbps", "source_protocol", "n_requests")}
                log.info("%s: using kept FLAC (no download)", vid)
        if pcm is None:
            if ctx.downloads_done > 0:
                s = random.uniform(*SLEEP_RANGE_S)
                log.info("sleeping %.1f s before the next download", s)
                ctx.sleep(s)
            work.mkdir(parents=True, exist_ok=True)
            ctx.downloads_done += 1
            d = ctx.downloader.fetch(vid, work)
            log.info("%s: downloaded format %s (%s, %s kbps, %s) in %.0f s, %d yt-dlp requests",
                     vid, d["format_id"], d["ext"], d["abr"], d["protocol"], time.time() - t0, d["n_requests"])
            if COURT_RE.search(d.get("title") or ""):
                d["path"].unlink(missing_ok=True)
                raise Unavailable("court-like title on the live page (PROTOCOL section 4); audio deleted")
            src_info = {"source_sha256": sha256_file(d["path"]), "source_format": d["format_id"],
                        "source_ext": d["ext"], "source_abr_kbps": d["abr"],
                        "source_protocol": d["protocol"], "n_requests": str(d["n_requests"])}
            wav = work / f"{vid}.1k.wav"
            to_1k_wav(d["path"], wav)
            d["path"].unlink(missing_ok=True)               # compressed source gone first
            pcm = read_pcm16(wav)
        row.update(src_info)
        seconds = pcm.shape[0] / SR
        expected = min(float(r.get("duration_s") or DOWNLOAD_END_S), DOWNLOAD_END_S)
        if seconds < MIN_AUDIO_FRACTION * expected - 2:
            raise DownloadFailed(f"short audio: {seconds:.1f} s of {expected:.0f} s expected")
        row["seconds"] = f"{seconds:.3f}"
        row["audio_sha256"] = sha256_pcm(pcm)
        audio = pcm.astype(np.float32) / 32768.0
        t1 = time.time()
        arrays, tmeta = build_traces(audio, SR, vid, lengths, ctx.extractors, ctx.primary,
                                     cuts_needed, log=log)
        del audio
        row["cuts"] = str(len(tmeta["cuts"]))
        row.update(hum_fields(tmeta))
        if keep:
            write_flac_atomic(pcm, flac_path)
            row["flac_path"] = rel(flac_path, ctx.repo)
        row["trace_path"] = rel(trace_path, ctx.repo)
        row["status"], row["reason"], row["retryable"] = "done", "", ""
        row["finished_utc"] = now_utc()
        meta = {"video_id": vid, "split": split, "complete": True, "manifest_row": row,
                "sr": SR, "download_end_s": DOWNLOAD_END_S, "excerpt_start_s": EXCERPT_START_S,
                "excerpt_lengths_min": lengths, "split_seed": SPLIT_SEED,
                "release_timestamp": r.get("release_timestamp"), "label_drift_s": r.get("label_drift_s"),
                "note": "1-Hz ENF traces only. No audio is stored here. Cut traces are SYNTHETIC.",
                **tmeta}
        save_npz_atomic(trace_path, arrays, meta)
        log.info("%s: done, %.1f s audio, extraction %.0f s, trace %s", vid, seconds,
                 time.time() - t1, rel(trace_path, ctx.repo))
    except Blocked as e:
        row.update(status="blocked", reason=sanitize(e), retryable="False", finished_utc=now_utc())
        raise_after = e
        _rm(work)
        row["_blocked"] = raise_after
        return row
    except Unavailable as e:
        row.update(status="failed", reason=sanitize(e), retryable="False", finished_utc=now_utc())
        log.warning("%s: unavailable: %s", vid, row["reason"])
    except DownloadFailed as e:
        row.update(status="failed", reason=sanitize(e), retryable="True", finished_utc=now_utc())
        log.warning("%s: failed (retryable): %s", vid, row["reason"])
    except Exception as e:                                 # a bug or a bad file
        row.update(status="failed", reason=f"{type(e).__name__}: {sanitize(e)}", retryable="True",
                   finished_utc=now_utc())
        log.error("%s: unexpected error: %s\n%s", vid, row["reason"], sanitize(traceback.format_exc()))
    finally:
        _rm(work)
    return row


def _rm(p: Path) -> None:
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)


def run(args, downloader=None, sleep=time.sleep, repo: Path = REPO, log=None) -> int:
    log = log or logging.getLogger("harvest")
    ctx = Ctx(args, log, downloader, sleep=sleep, repo=repo)
    for e in ctx.extractors:
        get_extractor(e)                                    # fail early on a bad name
    if ctx.primary not in ctx.extractors:
        log.error("primary extractor %s is not in --extractors", ctx.primary)
        return EXIT_ERROR
    if "v2" in ctx.extractors and not args.allow_unfrozen_v2:
        ok, msg = v2_frozen_check(repo, log)
        log.info("v2 freeze check: %s", msg)
        if not ok:
            log.error("refusing to extract v2: %s (PROTOCOL section 7)", msg)
            return EXIT_ERROR
    lock = RunLock(repo / "logs" / "harvest.lock")
    if not lock.acquire():
        log.error("another harvester holds logs/harvest.lock; exiting")
        return EXIT_LOCKED
    try:
        kill_children_with_me(log)
        n = cleanup_partials(ctx.media_tmp, ctx.trace_root, ctx.keep_root, log)
        if n:
            log.info("deleted %d leftover partial item(s)", n)
        manifest = load_manifest(ctx.manifest_path)
        blocked = [v for v, m in manifest.items() if m.get("status") == "blocked"]
        if blocked and not args.clear_block:
            log.error("manifest has blocked row(s) %s; YouTube showed a bot check, sign-in wall, "
                      "CAPTCHA or 429 earlier. Not starting. Only Shrey can decide to use --clear-block.",
                      ",".join(blocked))
            return EXIT_BLOCKED
        splits = [s for s in args.splits.split(",") if s]
        rows = read_splits(ctx.splits_path, splits)
        if args.only:
            only = set(args.only.split(","))
            rows = [r for r in rows if r["video_id"] in only]
        if args.limit:
            rows = rows[:args.limit]
        actions = plan(rows, manifest, ctx.extractors, ctx.trace_root, ctx.keep_root,
                       ctx.keep_splits, args.cuts, ctx.cut_splits, repo)
        todo = [a for a in actions if a[1] == "process"]
        log.info("run %s: splits=%s extractors=%s primary=%s cuts=%s keep=%s: %d streams, %d to process",
                 ctx.run_id, splits, ctx.extractors, ctx.primary, args.cuts, sorted(ctx.keep_splits),
                 len(actions), len(todo))
        for r, act, why in actions:
            vid = r["video_id"]
            if act == "skip":
                if why == "court-like title" and vid not in manifest:
                    manifest[vid] = {"video_id": vid, "split": r["split"], "status": "skipped",
                                     "reason": "court-like title (PROTOCOL section 4)", "retryable": "False",
                                     "finished_utc": now_utc(), "attempts": "0"}
                    write_manifest(ctx.manifest_path, manifest)
                    log.warning("%s: skipped, court-like title", vid)
                continue
            cuts_needed = bool(args.cuts and r["split"] in ctx.cut_splits
                               and CUT_EXCERPT_MIN in excerpt_lengths(r))
            rec = recover_from_trace(ctx.trace_root / r["split"] / f"{vid}.npz", ctx.extractors,
                                     ctx.keep_splits, cuts_needed, repo)
            if rec is not None:
                manifest[vid] = rec
                write_manifest(ctx.manifest_path, manifest)
                log.info("%s: recovered a finished trace into the manifest", vid)
                continue
            log.info("%s [%s]: %s (%s)", vid, r["split"], (r.get("title") or "")[:70], why)
            row = process_video(r, manifest.get(vid), ctx)
            blocked_exc = row.pop("_blocked", None)
            manifest[vid] = row
            write_manifest(ctx.manifest_path, manifest)
            if blocked_exc is not None:
                log.error("%s: BLOCKED: %s. Stopping the run (no retry, no workaround).", vid, row["reason"])
                return EXIT_BLOCKED
        n_done = sum(1 for r, _, _ in actions if manifest.get(r["video_id"], {}).get("status") == "done")
        log.info("run %s finished: %d of %d streams done; yt-dlp requests this run: %s",
                 ctx.run_id, n_done, len(actions), getattr(downloader, "total_requests", "n/a"))
        return EXIT_OK
    finally:
        lock.release()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--splits", required=True, help="comma list, e.g. dev or calibration")
    ap.add_argument("--manifest", default="data/harvest_manifest.csv")
    ap.add_argument("--splits-csv", default="data/splits.csv")
    ap.add_argument("--extractors", default="v1,naive", help="comma list of v1, v2, naive")
    ap.add_argument("--primary", default=None, help="extractor for cut traces (default: first)")
    ap.add_argument("--keep-audio-splits", default="", help="splits whose 1 kHz FLAC is kept (dev only)")
    ap.add_argument("--cuts", action="store_true", help="make the SYNTHETIC cut traces (PROTOCOL M5)")
    ap.add_argument("--cut-splits", default="test", help="splits that get cut traces with --cuts")
    ap.add_argument("--only", default=None, help="comma list of video IDs (debugging)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--allow-unfrozen-v2", action="store_true", help="tests only")
    ap.add_argument("--clear-block", action="store_true",
                    help="start even though a row is blocked (Shrey's decision only)")
    ap.add_argument("--log", default=None, help="log file (default logs/harvest_<splits>.log)")
    return ap


def setup_logging(path: Path) -> logging.Logger:
    path.parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("harvest")
    log.setLevel(logging.DEBUG)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S%z")
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    return log


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    os.chdir(REPO)
    name = args.splits.replace(",", "+")
    log = setup_logging(REPO / (args.log or f"logs/harvest_{name}.log"))
    log.info("harvest start pid=%d argv=%s", os.getpid(), " ".join(sys.argv[1:]))
    try:
        dl = YtDlpDownloader(log)
    except ImportError as e:
        log.error("yt-dlp is not installed: %s", e)
        return EXIT_ERROR
    return run(args, downloader=dl, log=log)


if __name__ == "__main__":
    raise SystemExit(main())
