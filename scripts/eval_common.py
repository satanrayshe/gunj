"""Shared code for scripts/set_threshold.py and scripts/evaluate.py.

- Search: every excerpt trace of a stream is searched in every scope with
  calibrate_latency.run_search (the same code that estimates L), so the
  threshold, the latency and the Test results all come from one search path.
- Cache: one JSON file per stream, <cache_dir>/<split>/<video_id>.json,
  written to .tmp and renamed. A cached stream is skipped unless the SHA-256
  of its trace file, the reference (t0, length), the matcher code or
  CACHE_VERSION changed. The cache holds raw search output only (positions,
  r, confidence); tau, L, D, hits and verdicts are applied afterwards, so the
  cache never depends on them.
- Pool: searches run in a spawn pool of max(1, min(4, cpu_count - 2))
  workers, one task per (extractor, excerpt length, scope). Each worker
  loads the reference once with np.load(mmap_mode='r') (gunj.reference.load).
  A stream's cache is written as soon as its last task finishes, so a
  shutdown loses at most the streams in flight.
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")   # before numpy is imported

import csv
import datetime as dt
import hashlib
import io
import json
import logging
import math
import multiprocessing as mp
import re
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
for _p in (str(REPO), str(SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np                       # noqa: E402

import calibrate_latency as cl           # noqa: E402
import harvest as hv                     # noqa: E402
from gunj import reference as gref       # noqa: E402

EXTRACTORS = tuple(cl.EXTRACTORS)        # ("v2", "v1", "naive")
PRIMARY = "v2"                           # PROTOCOL section 7: v2 was frozen in time (tag extractor-v2)
LENGTHS = (10, 20, 40, 60)               # PROTOCOL section 8
SCOPES = ("full", "90d", "1d")
SCOPE_LABEL = {"full": "full", "90d": "+-90 days", "1d": "+-1 day"}
FRAME0_S = cl.FRAME0_S
TOP_K = 5
CACHE_VERSION = 1
TAU_EPS = 0.0001                          # PROTOCOL section 10
HIT_S = 30                                # PROTOCOL section 9
COARSE_S = (120, 600)
Z95 = 1.959964                            # PROTOCOL section 13
MATCHER_FILES = ("gunj/match.py", "gunj/match_v2.py")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NEGATIVE_SPLITS = ("negative", "negative_synthetic")


# ------------------------------------------------------------------ basics

def now_ist() -> str:
    return dt.datetime.now(IST).isoformat(timespec="seconds")


def default_workers() -> int:
    return max(1, min(4, (os.cpu_count() or 1) - 2))


def rel(path, repo: Path = REPO) -> str:
    return hv.rel(Path(path), repo)


def resolve(path_str: str, repo: Path = REPO) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else repo / p


def read_splits(path: Path) -> list:
    with open(path, encoding="utf-8", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def read_manifest(path: Path) -> dict:
    return hv.load_manifest(path)


def excerpt_lengths(row: dict) -> list:
    return hv.excerpt_lengths(row)


def is_synthetic(row: dict) -> bool:
    return hv.is_synthetic_row(row)


def parse_utc(s: str | None):
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _abs_path_markers(repo: Path) -> list:
    out = set()
    for base in (repo.resolve(), Path.home()):
        s = str(base)
        out.update({s, s.replace("\\", "/"), s.replace("\\", "\\\\")})
    return [m for m in out if len(m) > 3]


def check_no_abs_paths(text: str, repo: Path = REPO) -> None:
    """Refuse to write a file under results/ that holds an absolute local path."""
    low = text.lower()
    for m in _abs_path_markers(repo):
        if m.lower() in low:
            raise ValueError(f"refusing to write an absolute local path into results/ ({m!r})")


def is_under(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def write_text_atomic(path: Path, text: str, repo: Path = REPO) -> None:
    path = Path(path)
    if is_under(path, repo / "results"):
        check_no_abs_paths(text, repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    hv.replace_retry(tmp, path)


def write_json_atomic(path: Path, obj, repo: Path = REPO, indent=1) -> None:
    write_text_atomic(path, json.dumps(obj, indent=indent, ensure_ascii=False, default=_json_default) + "\n", repo)


def _json_default(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def setup_logging(path: Path, name: str) -> logging.Logger:
    path.parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger(name)
    log.setLevel(logging.INFO)
    for h in list(log.handlers):
        h.close()
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S%z")
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    log.propagate = False
    return log


# ------------------------------------------------------------- statistics

def tau_rule(values: list) -> dict:
    """PROTOCOL section 10. ``values``: one confidence per trial, None for
    "no verdict" (no runner-up, or the search could not run).

    m = floor(0.01 n); tau = (m+1)-th largest confidence + 0.0001. Trials
    without a confidence count in n and rank below every confidence.
    """
    n = len(values)
    m = n // 100                                   # floor(0.01 n), exact for integers
    finite = sorted((float(v) for v in values if v is not None and math.isfinite(float(v))), reverse=True)
    out = {"n": n, "m": m, "rank": m + 1, "n_no_verdict": n - len(finite)}
    if n == 0:
        out.update(tau=None, value_at_rank=None, status="no threshold-half trials in this cell")
    elif len(finite) < m + 1:
        out.update(tau=None, value_at_rank=None,
                   status="undefined: fewer than m+1 trials have a confidence")
    else:
        v = finite[m]
        out.update(value_at_rank=v, tau=v + TAU_EPS, status="set")
    out["n_reaching_tau"] = None if out["tau"] is None else sum(1 for v in finite if v >= out["tau"])
    return out


def wilson(k: int, n: int, z: float = Z95):
    """Wilson 95% interval (PROTOCOL section 13); (None, None) if n == 0."""
    if n <= 0:
        return None, None
    p = k / n
    den = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def rate_dict(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": None if n == 0 else k / n,
            "wilson95": None if lo is None else [lo, hi]}


def fmt_rate(d: dict) -> str:
    if not d or not d["n"]:
        return "n/a"
    lo, hi = d["wilson95"]
    return f"{d['k']}/{d['n']} ({100 * d['rate']:.1f}%, {100 * lo:.1f} to {100 * hi:.1f}%)"


# ------------------------------------------------------------------ cache

@dataclass
class Job:
    """One stream to search."""
    split: str
    video_id: str
    trace_path: str                 # as in the manifest (relative to the repo)
    release_ts: int                 # scope centre (pseudo claimed time for SYNTHETIC)
    lengths: list                   # pre-registered excerpt lengths (data/splits.csv)
    extractors: tuple = EXTRACTORS
    meta: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.split}/{self.video_id}"


def cache_path(cache_dir: Path, split: str, video_id: str) -> Path:
    return Path(cache_dir) / split / f"{video_id}.json"


def matcher_sha(repo: Path = REPO) -> dict:
    """SHA-256 of the matcher code that is actually imported (gunj/match.py, gunj/match_v2.py)."""
    import gunj.match
    import gunj.match_v2
    files = dict(zip(MATCHER_FILES, (gunj.match.__file__, gunj.match_v2.__file__)))
    return {k: hashlib.sha256(Path(p).read_bytes()).hexdigest() for k, p in files.items()}


def ref_identity(ref) -> dict:
    return {"t0_ist": str(ref.t0), "length": int(len(ref))}


def load_cache(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def cache_is_valid(rec, trace_sha: str, ref_id: dict, msha: dict, job: Job | None = None) -> bool:
    if not rec or rec.get("cache_version") != CACHE_VERSION:
        return False
    if rec.get("trace_sha256") != trace_sha or rec.get("reference") != ref_id:
        return False
    if rec.get("matcher_sha256") != msha:
        return False
    if job is not None:
        if int(rec.get("release_timestamp", -1)) != int(job.release_ts):
            return False
        if list(rec.get("lengths_listed", [])) != list(job.lengths):
            return False
    return True


def clean_tmp(cache_dir: Path) -> int:
    n = 0
    if Path(cache_dir).exists():
        for p in Path(cache_dir).rglob("*.tmp"):
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
    return n


# ------------------------------------------------------------------ worker

_REF = None


def _init_worker(ref_dir):
    global _REF
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    _REF = gref.load(ref_dir)            # np.load(..., mmap_mode="r")


def _cand(ref, c):
    if c is None:
        return None
    s = int(c.index) - FRAME0_S
    return {"index": int(c.index), "start_index": s, "start_ist": str(ref.time_of(s)), "r": float(c.r)}


def search_task(t: dict) -> dict:
    """One search: (stream, extractor, excerpt length, scope). Runs in a worker."""
    ref = _REF
    t0 = time.perf_counter()
    out = {"key": t["key"], "ext": t["ext"], "length_min": int(t["length_min"]), "scope": t["scope"]}
    try:
        data = Path(t["trace_abs"]).read_bytes()
        out["trace_sha256"] = hashlib.sha256(data).hexdigest()
        with np.load(io.BytesIO(data), allow_pickle=False) as z:
            got = cl.run_search(z, t["ext"], ref, int(t["release_ts"]), int(t["length_min"]), t["scope"], k=TOP_K)
    except (ValueError, OSError, KeyError) as e:
        out.update(ok=False, error=f"{type(e).__name__}: {str(e)[:200]}")
        got = None
    if got is None and "error" not in out:
        out.update(ok=False, error="no such excerpt in the trace file")
    if got is not None:
        res, n_frames, weighted = got
        out.update(ok=True, error=None, n_frames=n_frames, weighted=bool(weighted),
                   positions=[int(res.scope[0]), int(res.scope[1])], n_positions=int(res.n_positions),
                   candidates=[_cand(ref, c) for c in res.candidates],
                   runner_up=_cand(ref, res.runner_up),
                   margin=None if res.margin is None else float(res.margin),
                   confidence=None if res.confidence is None else float(res.confidence))
    out["seconds"] = round(time.perf_counter() - t0, 3)
    return out


def _trace_info(path: Path) -> tuple:
    """(files, meta) of a trace .npz."""
    with np.load(path, allow_pickle=False) as z:
        files = set(z.files)
        meta = json.loads(str(z["meta_json"][()])) if "meta_json" in files else {}
    return files, meta


def _hum_summary(meta: dict) -> dict:
    out = {}
    for key, tm in (meta.get("traces") or {}).items():
        h = tm.get("hum") or {}
        meds = h.get("median_peak_to_floor") or {}
        out[key] = {"present": bool(h.get("present")), "median_peak_to_floor": meds,
                    "max_median_peak_to_floor": max(meds.values()) if meds else None}
    return out


SCOPE_COST = {"full": 0, "90d": 1, "1d": 2}
EXT_COST = {"v2": 0, "v1": 1, "naive": 2}


def plan_tasks(job: Job, files: set, trace_abs: Path) -> tuple:
    """Tasks for one stream, plus the listed lengths that have no trace."""
    tasks, missing = [], []
    for m in job.lengths:
        have_any = False
        for ext in job.extractors:
            if f"{ext}__x{m}__freq" not in files:
                continue
            have_any = True
            for scope in SCOPES:
                tasks.append({"key": job.key, "ext": ext, "length_min": int(m), "scope": scope,
                              "trace_abs": str(trace_abs), "release_ts": int(job.release_ts)})
        if not have_any:
            missing.append(int(m))
    # longest searches first, for a better spread over the workers
    tasks.sort(key=lambda t: (SCOPE_COST[t["scope"]], EXT_COST.get(t["ext"], 9), -t["length_min"]))
    return tasks, missing


def _order_key(s: dict):
    return (EXTRACTORS.index(s["ext"]) if s["ext"] in EXTRACTORS else 9, s["length_min"], SCOPES.index(s["scope"]))


def run_searches(jobs: list, cache_dir: Path, ref, ref_dir=None, workers: int | None = None,
                 log: logging.Logger | None = None, repo: Path = REPO, search: bool = True) -> tuple:
    """Search every job not already cached. Returns (records by job key, timing).

    With search=False nothing is searched: only the valid cached records are returned.
    """
    log = log or logging.getLogger("eval")
    workers = default_workers() if workers is None else max(1, int(workers))
    cache_dir = Path(cache_dir)
    n_tmp = clean_tmp(cache_dir)
    if n_tmp:
        log.info("deleted %d leftover .tmp cache file(s)", n_tmp)
    ref_id, msha = ref_identity(ref), matcher_sha(repo)
    records, pending, all_tasks = {}, {}, []
    n_cached = 0
    n_unreadable = 0
    for job in jobs:
        tabs = resolve(job.trace_path, repo)
        try:
            sha = hv.sha256_file(tabs)
        except OSError as e:
            log.error("%s: cannot read %s (%s); skipped this run", job.key, rel(tabs, repo), e)
            n_unreadable += 1
            continue
        cp = cache_path(cache_dir, job.split, job.video_id)
        rec = load_cache(cp)
        if cache_is_valid(rec, sha, ref_id, msha, job):
            records[job.key] = rec
            n_cached += 1
            continue
        if not search:
            continue
        try:
            files, meta = _trace_info(tabs)
        except Exception as e:                 # a damaged file must not stop every other stream
            log.error("%s: cannot open %s (%s: %s); skipped this run", job.key, rel(tabs, repo),
                      type(e).__name__, e)
            n_unreadable += 1
            continue
        tasks, missing = plan_tasks(job, files, tabs)
        pending[job.key] = {"job": job, "sha": sha, "meta": meta, "missing": missing,
                            "n_tasks": len(tasks), "results": [], "t_first": None}
        all_tasks.extend(tasks)
    timing = {"workers": workers, "streams_cached": n_cached, "streams_to_search": len(pending),
              "streams_unreadable": n_unreadable,
              "tasks": len(all_tasks), "wall_seconds": 0.0, "streams_searched": 0,
              "per_stream_search_seconds": {}}
    if not search:
        timing["streams_not_cached"] = len(jobs) - n_cached
        return records, timing
    log.info("%d stream(s) cached, %d to search (%d searches), %d worker(s)",
             n_cached, len(pending), len(all_tasks), workers)
    if not pending:
        return records, timing

    t_start = time.time()

    def finish(key: str) -> None:
        p = pending[key]
        job = p["job"]
        shas = {r.get("trace_sha256") for r in p["results"] if r.get("trace_sha256")}
        if shas and shas != {p["sha"]}:
            log.warning("%s: trace file changed during the search; not cached, will be searched again", key)
            return
        searches = sorted((_strip(r) for r in p["results"]), key=_order_key)
        secs = round(sum(r["seconds"] for r in searches), 3)
        rec = {
            "cache_version": CACHE_VERSION, "video_id": job.video_id, "split": job.split,
            "trace_path": rel(resolve(job.trace_path, repo), repo), "trace_sha256": p["sha"],
            "reference": ref_id, "matcher_sha256": msha, "release_timestamp": int(job.release_ts),
            "scope_centre_ist": str(gref.unix_to_ist(int(job.release_ts))),
            "SYNTHETIC": bool(p["meta"].get("SYNTHETIC", False)),
            "lengths_listed": list(job.lengths), "lengths_missing": p["missing"],
            "hum": _hum_summary(p["meta"]), "searches": searches,
            "search_seconds_sum": secs, "written_ist": now_ist(),
        }
        write_json_atomic(cache_path(cache_dir, job.split, job.video_id), rec, repo)
        records[key] = rec
        timing["streams_searched"] += 1
        timing["per_stream_search_seconds"][key] = secs
        log.info("%s: %d searches done, %.1f s of search time, %.0f s since the run started",
                 key, len(searches), secs, time.time() - t_start)

    def handle(r: dict) -> None:
        p = pending[r["key"]]
        p["results"].append(r)
        if len(p["results"]) == p["n_tasks"]:
            finish(r["key"])

    for key, p in list(pending.items()):
        if p["n_tasks"] == 0:
            finish(key)
    if workers <= 1:
        global _REF
        _REF = ref
        for t in all_tasks:
            handle(search_task(t))
    else:
        ctx = mp.get_context("spawn")
        with ctx.Pool(workers, initializer=_init_worker, initargs=(None if ref_dir is None else str(ref_dir),)) as pool:
            for r in pool.imap_unordered(search_task, all_tasks, chunksize=1):
                handle(r)
    timing["wall_seconds"] = round(time.time() - t_start, 1)
    secs = list(timing["per_stream_search_seconds"].values())
    if timing["streams_searched"]:
        timing["wall_seconds_per_stream"] = round(timing["wall_seconds"] / timing["streams_searched"], 1)
        timing["search_seconds_per_stream_mean"] = round(statistics.mean(secs), 1)
        timing["search_seconds_per_stream_max"] = round(max(secs), 1)
    return records, timing


def _strip(r: dict) -> dict:
    return {k: v for k, v in r.items() if k not in ("key", "trace_sha256")}


# ---------------------------------------------------------------- lookups

def search_of(rec: dict, ext: str, length_min: int, scope: str):
    for s in rec.get("searches", []):
        if s["ext"] == ext and s["length_min"] == int(length_min) and s["scope"] == scope:
            return s
    return None


def trace_hum(rec: dict, ext: str, part: str) -> dict:
    return (rec.get("hum") or {}).get(f"{ext}__{part}") or {}


def load_tau_table(path: Path) -> dict:
    """{ext: {length: {scope: tau or None}}} from results/threshold.json."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for ext, by_len in (d.get("cells") or {}).items():
        for m, by_scope in by_len.items():
            for scope, cell in by_scope.items():
                tau = cell.get("tau") if isinstance(cell, dict) else None
                out.setdefault(ext, {}).setdefault(int(m), {})[scope] = None if tau is None else float(tau)
    return out


def tau_of(table: dict, ext: str, length_min: int, scope: str):
    return ((table or {}).get(ext) or {}).get(int(length_min), {}).get(scope)


# ------------------------------------------------------------------- git

def _git(args: list, repo: Path = REPO):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)


def committed_unmodified(paths: list, repo: Path = REPO) -> list:
    """Problems that stop a blind run: each path must be in the HEAD commit
    and identical to it (nothing staged, nothing changed). Checked with git."""
    head = _git(["rev-parse", "--verify", "-q", "HEAD"], repo)
    if head.returncode != 0:
        return ["the repository has no commit (HEAD)"]
    problems = []
    for p in paths:
        if _git(["cat-file", "-e", f"HEAD:{p}"], repo).returncode != 0:
            problems.append(f"{p}: not committed (not in HEAD)")
            continue
        d = _git(["diff", "--quiet", "HEAD", "--", p], repo)
        if d.returncode != 0:
            problems.append(f"{p}: differs from HEAD (modified or staged, not committed)")
    return problems


def last_commit(path: str, repo: Path = REPO) -> dict:
    r = _git(["log", "-1", "--format=%H|%cI", "--", path], repo)
    if r.returncode != 0 or "|" not in r.stdout:
        return {"commit": None, "committed": None}
    h, t = r.stdout.strip().split("|", 1)
    return {"commit": h, "committed": t}


# ---------------------------------------------------------- harvest health

LOG_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})([+-]\d{4})?\s+(\w+)?\s*(.*)$")


def _parse_log_ts(date: str, tz: str | None):
    try:
        t = dt.datetime.strptime(date + (tz or ""), "%Y-%m-%dT%H:%M:%S" + ("%z" if tz else ""))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=IST)       # run_stage3.log: local time (IST)


def harvest_health(manifest: dict, logs_dir: Path, decision_utc=None, repo: Path = REPO) -> dict:
    """PROTOCOL section 15 condition 3, from the manifest and the harvest logs.

    - No manifest row is "blocked" (the harvester's status for a bot check,
      sign-in wall, CAPTCHA or HTTP 429).
    - No harvest log line says BLOCKED, and no WARNING or ERROR line matches
      the harvester's own block pattern (harvest.BLOCK_RE); run_stage3.log
      shows no harvester exit code 3.
    - The most recent YouTube download attempt (latest finished_utc among
      non-SYNTHETIC rows) has status "done".
    Only rows and log lines up to ``decision_utc`` count, if it is given.
    """
    def in_time(t):
        return t is not None and (decision_utc is None or t <= decision_utc)

    rows = list(manifest.values())
    blocked = [r["video_id"] for r in rows if r.get("status") == "blocked"]
    real = [r for r in rows if not is_synthetic(r) and r.get("source_format") != "SYNTHETIC"
            and r.get("status") in ("done", "failed", "blocked") and in_time(parse_utc(r.get("finished_utc")))]
    latest = max(real, key=lambda r: parse_utc(r["finished_utc"])) if real else None
    hits = []
    logs_dir = Path(logs_dir)
    files = sorted(logs_dir.glob("harvest_*.log")) + ([logs_dir / "run_stage3.log"] if (logs_dir / "run_stage3.log").exists() else [])
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        stage = f.name == "run_stage3.log"
        for i, ln in enumerate(lines, 1):
            m = LOG_TS_RE.match(ln)
            ts = _parse_log_ts(m.group(1), m.group(2)) if m else None
            if decision_utc is not None and ts is not None and ts > decision_utc:
                continue
            if stage:
                bad = re.search(r"\bexit 3\b", ln) is not None
            else:
                level = (m.group(3) if m else "") or ""
                bad = "BLOCKED" in ln or (level in ("WARNING", "ERROR") and hv.BLOCK_RE.search(ln) is not None)
            if bad:
                hits.append({"file": rel(f, repo), "line": i, "text": hv.sanitize(ln)[:240]})
    latest_d = None if latest is None else {
        "video_id": latest["video_id"], "split": latest.get("split"), "status": latest.get("status"),
        "reason": latest.get("reason"), "finished_utc": latest.get("finished_utc")}
    ok_latest = latest is not None and latest.get("status") == "done"
    return {"blocked_rows": blocked, "log_matches": hits, "most_recent_download": latest_d,
            "most_recent_download_succeeded": ok_latest,
            "n_real_download_rows": len(real),
            "log_files_scanned": [rel(f, repo) for f in files],
            "ok": (not blocked) and (not hits) and ok_latest}
