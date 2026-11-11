#!/usr/bin/env python
"""M5: edit detection on SYNTHETIC cuts (PROTOCOL.md section 11).

Every cut scored here is SYNTHETIC. At harvest time scripts/harvest.py removed
a stretch of 10 to 120 s from the 60-min excerpt audio of each Test stream with
gunj.synthetic.cut_audio, extracted the cut audio with the primary extractor
(v2) and deleted the audio. The cut traces are the arrays v2__cut1__* and
v2__cut2__* in traces/test/<video_id>.npz, and each cut's position and length
are in that file's meta_json under "cuts". This script reads traces only. It
reads no audio and downloads nothing.

How each sentence of PROTOCOL section 11 (M5) is applied
- "M5 uses only streams whose uncut 60-min excerpt is a confident hit in the
  full scope." The streams are the rows of results/test_results.csv with
  split test, extractor v2, excerpt 60 min, scope full and outcome
  "confident hit".
- "Each cut trace is located in the +-1-day scope". The cut trace is searched
  with calibrate_latency.run_search, the call scripts/eval_common.py makes for
  every Test search: the weighted v2 matcher (gunj.match_v2.wncc_search) with
  the cut trace's own frame weights, over calibrate_latency.scope_positions
  for the release time in IST +- 1 day. The top candidate is the lock.
- "then find_edits runs with its frozen defaults." gunj.match.find_edits is
  called with the trace, the reference, its validity mask and the lock, and
  with no keyword argument.
- "A detection is a reported cut within +-30 s of the true cut position."
- "The duration error is |reported jump - true cut length|."
- "A false alarm is any edit reported on the uncut excerpt of the same
  streams."
- "Results are given by cut length, and every one is labelled SYNTHETIC."

Readings chosen where the text leaves room
1. Stream rule. "Confident hit" is the outcome written by the frozen
   scripts/evaluate.py: the 60-min trace's hum flag is set, its confidence is
   at or above tau for (v2, 60 min, full), and its top candidate is within
   30 s of T* (with the label-drift correction D). The D = 0 variant (S1) is
   not used.
2. Positions. find_edits reports clip_time_s = 8 + trace_frame: the centre of
   the first frame that follows the new offset, in seconds of the cut excerpt
   audio. The true cut position is at_s, the excerpt second at which the
   removed stretch began, which is also the splice point in the cut audio.
   Both count from the start of the excerpt (120 s into the recording). The
   script refuses a trace whose offset_s or hop_s differs from find_edits'
   defaults (8.0 and 1.0).
3. "Reported cut" means an edit that find_edits reports with kind "cut"
   (jump_s > 0, audio missing). An edit of kind "insertion" near the true
   position is not a detection. The count with either kind is written beside
   it and labelled EXPLORATORY.
4. "Within +-30 s" is |clip_time_s - at_s| <= 30, inclusive, as in the hit
   rule of section 9.
5. If more than one reported cut is within 30 s, the one nearest the true
   position is used (the earlier one on a tie).
6. The duration error is |jump_s - length_s| of the detecting edit. It exists
   only for detected cuts, so its median and maximum are over detected cuts.
7. No threshold or hit condition is applied to the lock of a cut trace,
   because the text sets none. Whatever the top candidate in the +-1-day
   scope is, it is passed to find_edits. If the search returns no candidate
   the cut counts as not detected.
8. False alarms. The uncut 60-min trace of the same stream goes through the
   same two steps: located in the +-1-day scope, then find_edits with its
   defaults. Every edit it reports, of either kind, is a false alarm. Two
   numbers are written: the uncut excerpts with at least one reported edit
   (with a Wilson interval) and the total number of edits reported on them.
   The +-1-day lock is compared with the full-scope lock in the search cache
   and the comparison is recorded for every stream.
9. Edits reported on a cut trace other than the detecting one are listed for
   each cut. PROTOCOL defines false alarms on uncut excerpts only, so their
   count is labelled EXPLORATORY.
10. Wilson 95% intervals come from eval_common.wilson (PROTOCOL section 13,
   z = 1.959964).
11. EXPLORATORY (post hoc, not in PROTOCOL.md): the same procedure over every
   gate lock (results/gate_frozen.json, extractor v2, any excerpt length)
   that has SYNTHETIC cut traces. It is written under the key "exploratory"
   and is never added to the pre-registered numbers.

Checks before anything is written (exit status 2 and no output if one fails
for a stream that is scored)
- the SHA-256 of the trace file equals the one in the stream's search-cache
  record, so these are the traces the evaluation searched;
- the SHA-256 of gunj/match.py and gunj/match_v2.py equals the cache record's;
- the release timestamp in data/splits.csv equals the cache record's;
- the +-1-day search of the uncut trace run here gives the cached search's
  top position and r, so the search call is the evaluation's.

The run is a single process. The reference is opened with
np.load(..., mmap_mode="r") (gunj.reference.load) and only the seconds near
each stream are read. Outputs are written to <name>.tmp and renamed. The run
takes about a minute and can be repeated at any time: apart from the time
stamp and the timing block it writes the same files.

Outputs
  results/m5.json   every stream and cut, the totals by cut length, the
                    false alarms, and the EXPLORATORY block
  results/m5.md     a short table of the same numbers

Usage (from the repo root):
  .venv\\Scripts\\python.exe scripts\\m5_edits.py
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")   # before numpy is imported

import argparse                           # noqa: E402
import csv                                # noqa: E402
import hashlib                            # noqa: E402
import inspect                            # noqa: E402
import io                                 # noqa: E402
import json                               # noqa: E402
import statistics                         # noqa: E402
import sys                                # noqa: E402
import time                               # noqa: E402
from pathlib import Path                  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np                        # noqa: E402

import eval_common as ec                  # noqa: E402  (frozen; puts the repo on sys.path)
import calibrate_latency as cl            # noqa: E402  (frozen)
import harvest as hv                      # noqa: E402  (frozen)
from gunj import match as gmatch          # noqa: E402  (frozen)
from gunj import reference as gref        # noqa: E402
from gunj.match import find_edits         # noqa: E402

REPO = ec.REPO
PRIMARY = ec.PRIMARY                      # "v2" (PROTOCOL section 7)
LENGTH_MIN = hv.CUT_EXCERPT_MIN           # the cut traces come from the 60-min excerpt
EXCERPT_PART = f"x{LENGTH_MIN}"
SCOPE = "1d"                              # "Each cut trace is located in the +-1-day scope"
FULL_SCOPE = "full"
DETECT_S = 30.0                           # "within +-30 s of the true cut position"
EXCERPT_START_S = hv.EXCERPT_START_S      # the excerpt starts 120 s into the recording
CUT_LENGTHS_S = tuple(hv.CUT_LENGTHS_S)   # (10, 20, 40, 60, 90, 120), PROTOCOL M5
FRAME0_S = ec.FRAME0_S                    # trace frame 0 is centred 128 s into the recording
GATE_SCOPES = ("full", "90d")             # PROTOCOL section 15
R_TOL = 1e-9
SYN_LABEL = "SYNTHETIC cut (PROTOCOL M5)"


class IntegrityError(RuntimeError):
    """An input differs from what the frozen evaluation searched."""


# ------------------------------------------------------------------ inputs

def read_results(path: Path) -> list:
    with open(path, encoding="utf-8", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def _is_primary_test_row(r: dict) -> bool:
    return r.get("split") == "test" and r.get("extractor") == PRIMARY


def confident_hit_streams(rows: list) -> list:
    """The M5 streams: Test streams whose uncut 60-min excerpt is a confident hit in the
    full scope with the primary extractor (reading 1). Order as in the results table."""
    out = []
    for r in rows:
        if (_is_primary_test_row(r) and str(r.get("excerpt_min")) == str(LENGTH_MIN)
                and r.get("scope") == FULL_SCOPE and r.get("outcome") == "confident hit"
                and r["video_id"] not in out):
            out.append(r["video_id"])
    return out


def gate_locks_from_rows(rows: list) -> list:
    """Gate locks recomputed from the results table (PROTOCOL section 15, conditions 1 and 2):
    primary extractor, any excerpt length, full or +-90-day scope, top candidate within 30 s
    of T* and confidence at or above tau of that cell. Used only to cross-check the frozen list."""
    out = []
    for r in rows:
        if (_is_primary_test_row(r) and r.get("scope") in GATE_SCOPES and r.get("hit_30s") == "True"
                and r.get("confidence_ge_tau") == "True" and r["video_id"] not in out):
            out.append(r["video_id"])
    return out


def load_gate_locks(path: Path) -> list:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return list(d["gate"]["by_extractor"][PRIMARY]["locked_streams"])


def uncut_cells(rows: list, vid: str) -> dict:
    """The stream's (v2, 60 min) rows of the results table, copied for context."""
    out = {}
    for r in rows:
        if (_is_primary_test_row(r) and r["video_id"] == vid
                and str(r.get("excerpt_min")) == str(LENGTH_MIN) and r.get("scope") in (FULL_SCOPE, SCOPE)):
            out[r["scope"]] = {"outcome": r.get("outcome"), "hit_30s": r.get("hit_30s") == "True",
                               "error_s": None if r.get("error_s") in (None, "") else int(float(r["error_s"])),
                               "confidence_ge_tau": r.get("confidence_ge_tau") == "True"}
    return out


def find_edits_defaults() -> dict:
    """Keyword defaults of the frozen gunj.match.find_edits, read from its signature."""
    return {k: p.default for k, p in inspect.signature(gmatch.find_edits).parameters.items()
            if p.default is not inspect.Parameter.empty}


# ------------------------------------------------------------------ search

class PartAsExcerpt:
    """One part of an open trace file (for example "cut1") shown under the 60-min excerpt
    names, so that calibrate_latency.run_search runs on it unchanged. Only names are mapped;
    the arrays are the part's own."""

    def __init__(self, z, ext: str, part: str, length_min: int = LENGTH_MIN):
        self._z = z
        self._src = f"{ext}__{part}__"
        self._dst = f"{ext}__x{length_min}__"
        self.files = [self._dst + f[len(self._src):] for f in z.files if f.startswith(self._src)]

    def __getitem__(self, name: str):
        if not name.startswith(self._dst):
            raise KeyError(name)
        return self._z[self._src + name[len(self._dst):]]


def locate(z, ext: str, part: str, ref, release_ts: int):
    """Search one trace part in the +-1-day scope with the evaluation's own search call.

    Returns calibrate_latency.run_search's (SearchResult, n_frames, weighted), or None if
    the trace file has no such part."""
    view = z if part == EXCERPT_PART else PartAsExcerpt(z, ext, part)
    return cl.run_search(view, ext, ref, int(release_ts), LENGTH_MIN, SCOPE, k=ec.TOP_K)


def _plain(v):
    """numpy scalars and containers to plain JSON types."""
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    return v


def analyse_trace(z, meta: dict, ext: str, part: str, ref, release_ts: int) -> dict:
    """Locate one trace in the +-1-day scope, then run find_edits with its frozen defaults."""
    key = f"{ext}__{part}"
    out = {"trace_key": key, "lock": None, "anchor": None, "reported_edits": [],
           "find_edits_ran": False, "note": None}
    tm = (meta.get("traces") or {}).get(key) or {}
    dflt = find_edits_defaults()
    for name in ("offset_s", "hop_s"):
        if name in tm and float(tm[name]) != float(dflt[name]):
            raise IntegrityError(f"{key}: {name} = {tm[name]} differs from find_edits' default {dflt[name]}")
    try:
        got = locate(z, ext, part, ref, release_ts)
    except ValueError as e:                      # a constant or non-finite trace
        out["note"] = f"search could not run: {e}"
        return out
    if got is None:
        raise KeyError(f"no trace {key}")
    res, n_frames, weighted = got
    out.update(n_frames=int(n_frames), weighted_search=bool(weighted),
               scope_positions=[int(res.scope[0]), int(res.scope[1])], n_positions=int(res.n_positions))
    top = res.top
    if top is None:
        out["note"] = "no admissible reference window in the +-1-day scope"
        return out
    out["lock"] = {"index": int(top.index),
                   "recording_start_ist": str(ref.time_of(int(top.index) - FRAME0_S)),
                   "r": float(top.r),
                   "confidence": None if res.confidence is None else float(res.confidence)}
    freq = np.asarray(z[f"{key}__freq"], dtype=np.float64)
    fe = find_edits(freq, ref.freq, ref.valid, int(top.index))       # no keyword: frozen defaults
    out["find_edits_ran"] = True
    out["anchor"] = _plain(fe["anchor"])
    out["reported_edits"] = [dict(_plain(e), recording_time_s=float(EXCERPT_START_S + e["clip_time_s"]))
                             for e in fe["edits"]]
    return out


# ----------------------------------------------------------------- scoring

def score_cut(edits: list, at_s: float, length_s: float, window_s: float = DETECT_S) -> dict:
    """Apply the M5 rules to the edits reported on one SYNTHETIC cut trace.

    A detection is an edit of kind "cut" with |clip_time_s - at_s| <= window_s (readings 3
    and 4); the nearest one is used (reading 5). The duration error is |jump_s - length_s|
    of that edit (reading 6)."""
    near = [(abs(float(e["clip_time_s"]) - float(at_s)), i) for i, e in enumerate(edits)
            if abs(float(e["clip_time_s"]) - float(at_s)) <= window_s]
    cuts = sorted(x for x in near if edits[x[1]]["kind"] == "cut")
    out = {"detected": bool(cuts), "detecting_edit": None, "reported_position_s": None,
           "position_error_s": None, "reported_jump_s": None, "duration_error_s": None,
           "either_kind_within_window": bool(near)}
    if cuts:
        e = edits[cuts[0][1]]
        out.update(detecting_edit=cuts[0][1], reported_position_s=float(e["clip_time_s"]),
                   position_error_s=round(float(e["clip_time_s"]) - float(at_s), 3),
                   reported_jump_s=float(e["jump_s"]),
                   duration_error_s=round(abs(float(e["jump_s"]) - float(length_s)), 3))
    out["other_edits"] = len(edits) - (1 if cuts else 0)
    return out


def false_alarm_count(edits: list) -> int:
    """Every edit reported on an uncut excerpt, of either kind, is a false alarm (reading 8)."""
    return len(edits)


def _cut_group(cuts: list) -> dict:
    det = [c for c in cuts if c["detected"]]
    errs = sorted(float(c["duration_error_s"]) for c in det)
    return {"SYNTHETIC": True, "n_cuts": len(cuts), "detected": ec.rate_dict(len(det), len(cuts)),
            "duration_error_s": {"n": len(errs), "median": statistics.median(errs) if errs else None,
                                 "max": max(errs) if errs else None, "values": errs}}


def cut_totals(cuts: list) -> dict:
    """Detection and duration error by cut length and for all lengths together."""
    lengths = sorted(set(CUT_LENGTHS_S) | {int(c["true_length_s"]) for c in cuts})
    return {"by_cut_length_s": {str(n): _cut_group([c for c in cuts if int(c["true_length_s"]) == n])
                                for n in lengths},
            "all": _cut_group(cuts)}


def false_alarm_totals(streams: list) -> dict:
    """False alarms on the uncut excerpts of the given streams."""
    hit = [s["video_id"] for s in streams if s["uncut"]["false_alarms"] > 0]
    return {"n_uncut_excerpts": len(streams),
            "excerpts_with_a_reported_edit": ec.rate_dict(len(hit), len(streams)),
            "edits_reported": sum(s["uncut"]["false_alarms"] for s in streams),
            "stream_ids_with_a_reported_edit": hit,
            "find_edits_did_not_run": [s["video_id"] for s in streams if not s["uncut"]["find_edits_ran"]]}


def reading_checks(cuts: list) -> dict:
    """EXPLORATORY counts: what readings 3 and 9 leave out, and how the misses split."""
    missed = [c for c in cuts if not c["detected"]]
    return {"either_kind_within_30s": ec.rate_dict(sum(c["either_kind_within_window"] for c in cuts), len(cuts)),
            "other_edits_on_cut_traces": sum(c["other_edits"] for c in cuts),
            "cut_traces_with_another_edit": sum(1 for c in cuts if c["other_edits"] > 0),
            "not_detected": len(missed),
            "not_detected_no_edit_reported": sum(1 for c in missed if not c["reported_edits"]),
            "not_detected_edits_reported_elsewhere": sum(1 for c in missed if c["reported_edits"])}


def summarise(streams: list) -> dict:
    cuts = [c for s in streams for c in s["cuts"]]
    return {"n_streams": len(streams), "n_cuts": len(cuts),
            "stream_ids": [s["video_id"] for s in streams],
            "totals": cut_totals(cuts), "false_alarms": false_alarm_totals(streams)}


# ------------------------------------------------------------- one stream

def _cached_top(rec: dict, scope: str):
    s = ec.search_of(rec, PRIMARY, LENGTH_MIN, scope) if rec else None
    cands = (s or {}).get("candidates") or []
    return cands[0] if cands else None


def check_against_cache(vid: str, rec, trace_sha: str, msha: dict, release_ts: int, uncut: dict) -> dict:
    """The checks listed in the module docstring. Raises IntegrityError on a mismatch."""
    if not rec:
        raise IntegrityError(f"{vid}: no search-cache record")
    if rec.get("trace_sha256") != trace_sha:
        raise IntegrityError(f"{vid}: the trace file differs from the one the evaluation searched")
    if rec.get("matcher_sha256") != msha:
        raise IntegrityError(f"{vid}: the matcher code differs from the one the evaluation used")
    if int(rec.get("release_timestamp", -1)) != int(release_ts):
        raise IntegrityError(f"{vid}: the release timestamp differs from the cache record's")
    c1, cf = _cached_top(rec, SCOPE), _cached_top(rec, FULL_SCOPE)
    lock = uncut.get("lock")
    same = (c1 is None and lock is None) or (
        c1 is not None and lock is not None and int(c1["index"]) == lock["index"]
        and abs(float(c1["r"]) - lock["r"]) <= R_TOL)
    if not same:
        raise IntegrityError(f"{vid}: the +-1-day search of the uncut trace does not reproduce the cached one")
    return {"trace_sha256_equals_cache": True, "matcher_sha256_equals_cache": True,
            "release_timestamp_equals_cache": True, "uncut_1d_search_reproduces_cache": True,
            "uncut_lock_equals_full_scope_lock": bool(lock is not None and cf is not None
                                                      and int(cf["index"]) == lock["index"])}


def process_stream(vid: str, split_row: dict, rows: list, traces_dir: Path, cache_dir: Path, ref,
                   msha: dict, repo: Path = REPO) -> dict | None:
    """Locate and score the uncut trace and every SYNTHETIC cut trace of one stream.

    Returns None if the stream has no 60-min trace (a stream shorter than 62 min has
    neither a 60-min excerpt nor SYNTHETIC cut traces)."""
    trace_path = Path(traces_dir) / f"{vid}.npz"
    data = trace_path.read_bytes()
    trace_sha = hashlib.sha256(data).hexdigest()
    release_ts = int(split_row["release_timestamp"])
    with np.load(io.BytesIO(data), allow_pickle=False) as z:
        meta = json.loads(str(z["meta_json"][()])) if "meta_json" in z.files else {}
        if f"{PRIMARY}__{EXCERPT_PART}__freq" not in z.files:
            return None
        uncut = analyse_trace(z, meta, PRIMARY, EXCERPT_PART, ref, release_ts)
        uncut["false_alarms"] = false_alarm_count(uncut["reported_edits"])
        rec = ec.load_cache(ec.cache_path(cache_dir, "test", vid))
        checks = check_against_cache(vid, rec, trace_sha, msha, release_ts, uncut)
        plan = meta.get("cuts") or []
        expected = hv.cut_plan(vid)
        checks["cuts_equal_harvest_cut_plan"] = (
            len(plan) == len(expected)
            and all(float(a["at_s"]) == float(b["at_s"]) and int(a["length_s"]) == int(b["length_s"])
                    for a, b in zip(plan, expected)))
        cuts, missing = [], []
        for c in plan:
            part = f"cut{int(c['cut'])}"
            if f"{PRIMARY}__{part}__freq" not in z.files:
                missing.append(int(c["cut"]))
                continue
            a = analyse_trace(z, meta, PRIMARY, part, ref, release_ts)
            sc = score_cut(a["reported_edits"], float(c["at_s"]), float(c["length_s"]))
            lock, ulock = a.get("lock"), uncut.get("lock")
            cuts.append({"cut": int(c["cut"]), "SYNTHETIC": True, "label": c.get("label") or SYN_LABEL,
                         "true_position_s": float(c["at_s"]),
                         "true_position_recording_s": float(c.get("at_recording_s", EXCERPT_START_S + c["at_s"])),
                         "true_length_s": int(c["length_s"]),
                         **sc, **a,
                         "lock_minus_uncut_lock_s": (None if lock is None or ulock is None
                                                     else lock["index"] - ulock["index"])})
    return {"video_id": vid, "trace_path": ec.rel(trace_path, repo), "release_timestamp": release_ts,
            "results_table": uncut_cells(rows, vid), "checks": checks, "uncut": uncut, "cuts": cuts,
            "cuts_listed_without_a_trace": missing}


# ------------------------------------------------------------------ report

def uncut_outcomes(streams: list) -> dict:
    """What the results table says about the uncut 60-min excerpt of the given streams."""
    full = [(s["results_table"].get(FULL_SCOPE) or {}).get("outcome") or "no row" for s in streams]
    return {"full_scope_outcome": {k: full.count(k) for k in sorted(set(full))},
            "top1_within_30s_in_1d_scope": sum(bool((s["results_table"].get(SCOPE) or {}).get("hit_30s"))
                                               for s in streams)}


def build(rows: list, splits: dict, gate_ids, traces_dir: Path, cache_dir: Path, ref, msha: dict,
          exploratory: bool = True, repo: Path = REPO, log=print) -> dict:
    """Score the pre-registered streams and, separately, the EXPLORATORY gate-lock set."""
    prereg_ids = confident_hit_streams(rows)
    order = []
    for r in rows:
        if r.get("split") == "test" and r["video_id"] not in order:
            order.append(r["video_id"])
    extra_ids = []
    if exploratory and gate_ids is not None:
        known = set(gate_ids)
        extra_ids = [v for v in order if v in known and v not in prereg_ids]
        extra_ids += sorted(v for v in known if v not in order and v not in prereg_ids)
    records, no_cuts = {}, []
    for vid in prereg_ids + extra_ids:
        if vid not in splits:
            raise IntegrityError(f"{vid}: not a Test row of the splits file")
        t = time.perf_counter()
        rec = process_stream(vid, splits[vid], rows, traces_dir, cache_dir, ref, msha, repo)
        if rec is None:
            no_cuts.append(vid)
            log(f"{vid}: no 60-min trace, so no SYNTHETIC cut traces")
            continue
        if not rec["cuts"]:
            no_cuts.append(vid)
        records[vid] = rec
        log(f"{vid}: {len(rec['cuts'])} SYNTHETIC cut(s), detected "
            f"{sum(c['detected'] for c in rec['cuts'])}, edits on the uncut excerpt "
            f"{rec['uncut']['false_alarms']}, {time.perf_counter() - t:.1f} s")
    pre = [records[v] for v in prereg_ids if v in records]
    out = {"preregistered": {
        "label": "PRE-REGISTERED: PROTOCOL.md section 11, M5. Every cut is SYNTHETIC.",
        "stream_rule": "Test streams whose uncut 60-min excerpt is a confident hit in the full scope "
                       "(extractor v2; results table)",
        **summarise(pre),
        "streams_without_cut_traces": [v for v in prereg_ids if v in no_cuts],
        "streams": pre}}
    if not exploratory:
        out["exploratory"] = {"label": "EXPLORATORY (post hoc)", "status": "not computed: --no-exploratory"}
        return out
    pre_cuts = [c for s in pre for c in s["cuts"]]
    exp = {"label": "EXPLORATORY (post hoc). Nothing under this key is in PROTOCOL.md, and none of it is "
                    "part of the pre-registered M5 result. Every cut is SYNTHETIC.",
           "reading_checks_on_preregistered_streams": reading_checks(pre_cuts)}
    if gate_ids is None:
        exp["gate_locks"] = {"status": "not computed: the frozen gate file is missing"}
    else:
        extra = [records[v] for v in extra_ids if v in records and records[v]["cuts"]]
        both = pre + extra
        recomputed = gate_locks_from_rows(rows)
        exp["gate_locks"] = {
            "label": "EXPLORATORY (post hoc): every gate lock (extractor v2, any excerpt length, full or "
                     "+-90-day scope) that has SYNTHETIC cut traces. Includes the pre-registered streams.",
            "n_gate_locks": len(gate_ids),
            "gate_locks_recomputed_from_results_table_equal": sorted(recomputed) == sorted(gate_ids),
            "gate_locks_without_cut_traces": sorted(v for v in gate_ids if v in no_cuts),
            **summarise(both),
            "reading_checks": reading_checks([c for s in both for c in s["cuts"]]),
            "additional_streams_only": {
                "label": "EXPLORATORY (post hoc): gate locks that are not in the pre-registered M5 set",
                **summarise(extra),
                "uncut_60min_excerpt_in_results_table": uncut_outcomes(extra),
                "reading_checks": reading_checks([c for s in extra for c in s["cuts"]]),
                "streams": extra}}
    out["exploratory"] = exp
    return out


def _f(v, nd: int = 1) -> str:
    return "n/a" if v is None else f"{float(v):.{nd}f}"


def _sgn(v, nd: int = 1) -> str:
    if v is None:
        return "n/a"
    return f"{round(float(v), nd) + 0.0:+.{nd}f}"        # + 0.0 turns -0.0 into 0.0


def _length_table(totals: dict) -> list:
    L = ["| cut length (s) | SYNTHETIC cuts | detected (Wilson 95%) | median duration error (s) | "
         "max duration error (s) |", "|---|---|---|---|---|"]
    groups = list(totals["by_cut_length_s"].items()) + [("all", totals["all"])]
    for name, g in groups:
        L.append(f"| {name} | {g['n_cuts']} | {ec.fmt_rate(g['detected'])} | "
                 f"{_f(g['duration_error_s']['median'])} | {_f(g['duration_error_s']['max'])} |")
    return L


def _fa_table(fa: dict) -> list:
    return ["| uncut excerpts | with at least one reported edit (Wilson 95%) | edits reported |", "|---|---|---|",
            f"| {fa['n_uncut_excerpts']} | {ec.fmt_rate(fa['excerpts_with_a_reported_edit'])} | "
            f"{fa['edits_reported']} |"]


def _edits_cell(edits: list) -> str:
    if not edits:
        return "none"
    return "; ".join(f"{e['clip_time_s']:.0f} s, {e['jump_s']:+.0f} s ({e['kind']})" for e in edits)


def render_md(d: dict) -> str:
    pre, exp = d["preregistered"], d["exploratory"]
    fa = pre["false_alarms"]
    L = ["# M5: edit detection on SYNTHETIC cuts", ""]
    L.append(f"Generated by `scripts/m5_edits.py` at {d['written_ist']}. Every number below is copied from "
             "`results/m5.json`, which the same run wrote. PROTOCOL.md section 11 (M5).")
    L.append("")
    L.append("Every cut here is SYNTHETIC. At harvest time a stretch of 10 to 120 s was removed from the 60-min "
             "excerpt audio of each Test stream with `gunj.synthetic.cut_audio`, the cut audio was extracted "
             "with extractor v2, and the audio was deleted. The test contains no recording with a real edit.")
    L.append("")
    L.append("## Pre-registered result (PROTOCOL section 11)")
    L.append("")
    L.append(f"- Streams: {pre['n_streams']} Test streams whose uncut 60-min excerpt is a confident hit in the "
             f"full scope (extractor v2; `{d['inputs']['results_table']['path']}`). SYNTHETIC cuts scored: "
             f"{pre['n_cuts']}.")
    L.append("- Each cut trace was located in the +-1-day scope with the evaluation's search call "
             "(`calibrate_latency.run_search`). Then `gunj.match.find_edits` ran with its frozen defaults.")
    L.append("- Detection: a reported cut within +-30 s of the true cut position. Duration error: "
             "|reported jump - true cut length|, over detected cuts. False alarm: any edit reported on the "
             "uncut excerpt of the same streams.")
    if pre["streams_without_cut_traces"]:
        L.append("- Streams of this set without SYNTHETIC cut traces: "
                 + ", ".join(pre["streams_without_cut_traces"]) + ".")
    L.append("")
    L.append("Table 1. Pre-registered M5 on SYNTHETIC cuts: detection and duration error by cut length.")
    L.append("")
    L += _length_table(pre["totals"])
    L.append("")
    L.append("Table 2. Pre-registered M5: false alarms on the uncut excerpts of the same streams (the same "
             "clips without a SYNTHETIC cut).")
    L.append("")
    L += _fa_table(fa)
    L.append("")
    if fa["stream_ids_with_a_reported_edit"]:
        L.append("Uncut excerpts with a reported edit: " + ", ".join(fa["stream_ids_with_a_reported_edit"]) + ".")
        L.append("")
    if fa["find_edits_did_not_run"]:
        L.append("Uncut excerpts where `find_edits` could not run (no lock): "
                 + ", ".join(fa["find_edits_did_not_run"]) + ".")
        L.append("")
    L.append("Table 3. Pre-registered M5: every SYNTHETIC cut. Positions are seconds into the 60-min excerpt, "
             f"which starts {EXCERPT_START_S} s into the recording. A reported edit is shown as its position "
             "and its jump.")
    L.append("")
    L.append("| stream | SYNTHETIC cut | true position (s) | true length (s) | edits reported on the cut trace | "
             "detected | position error (s) | duration error (s) | edits on the uncut excerpt |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for s in pre["streams"]:
        for i, c in enumerate(s["cuts"]):
            L.append(f"| {s['video_id'] if i == 0 else ''} | {c['cut']} | {c['true_position_s']:.1f} | "
                     f"{c['true_length_s']} | {_edits_cell(c['reported_edits'])} | "
                     f"{'yes' if c['detected'] else 'no'} | {_sgn(c['position_error_s'])} | "
                     f"{_f(c['duration_error_s'])} | {s['uncut']['false_alarms'] if i == 0 else ''} |")
    L.append("")
    L.append("## EXPLORATORY (post hoc)")
    L.append("")
    L.append("Nothing in this section is in PROTOCOL.md, and none of it is part of the pre-registered result "
             "above. Every cut is SYNTHETIC.")
    L.append("")
    if "reading_checks_on_preregistered_streams" in exp:
        rc = exp["reading_checks_on_preregistered_streams"]
        L.append(f"- EXPLORATORY reading check on the pre-registered streams: counting an edit of either kind "
                 f"(cut or insertion) within +-30 s gives {ec.fmt_rate(rc['either_kind_within_30s'])}. "
                 f"Edits reported on cut traces besides the detecting one: {rc['other_edits_on_cut_traces']} "
                 f"(cut traces with such an edit: {rc['cut_traces_with_another_edit']}).")
        L.append(f"- EXPLORATORY description of the misses on the pre-registered streams: of {rc['not_detected']} "
                 f"cuts that were not detected, {rc['not_detected_no_edit_reported']} had no edit reported on "
                 f"the cut trace and {rc['not_detected_edits_reported_elsewhere']} had edits reported, none of "
                 "them a cut within +-30 s of the true position.")
    g = exp.get("gate_locks")
    if g is None or "status" in g:
        L.append(f"- EXPLORATORY gate-lock set: {(g or exp).get('status', 'not computed')}.")
        L.append("")
    else:
        a = g["additional_streams_only"]
        L.append(f"- EXPLORATORY gate-lock set: the same procedure over every gate lock that has SYNTHETIC cut "
                 f"traces. Gate locks: {g['n_gate_locks']} (`{d['inputs']['gate_frozen']['path']}`); with "
                 f"SYNTHETIC cut traces: {g['n_streams']} (pre-registered streams: {pre['n_streams']}; "
                 f"additional streams: {a['n_streams']}). SYNTHETIC cuts: {g['n_cuts']}.")
        uo = a["uncut_60min_excerpt_in_results_table"]
        L.append("- The additional streams are gate locks through another excerpt length, the +-90-day scope, "
                 "or a lock without the hum flag. Outcome of their 60-min excerpt in the full scope: "
                 + ", ".join(f"{k} {v}" for k, v in uo["full_scope_outcome"].items())
                 + f". Their 60-min excerpt's top candidate in the +-1-day scope is within 30 s of T* for "
                 f"{uo['top1_within_30s_in_1d_scope']} of {a['n_streams']}. `find_edits` starts from whatever "
                 "that +-1-day search returns.")
        L.append("")
        L.append("Table 4. EXPLORATORY (post hoc) on SYNTHETIC cuts: detection and duration error by cut "
                 "length, all gate locks with cut traces.")
        L.append("")
        L += _length_table(g["totals"])
        L.append("")
        L.append("Table 5. EXPLORATORY (post hoc): false alarms on the uncut excerpts of all gate locks with "
                 "SYNTHETIC cut traces.")
        L.append("")
        L += _fa_table(g["false_alarms"])
        L.append("")
        L.append("Table 6. EXPLORATORY (post hoc) on SYNTHETIC cuts: the additional streams only (gate locks "
                 "outside the pre-registered set).")
        L.append("")
        L += _length_table(a["totals"])
        L.append("")
        fa2 = a["false_alarms"]
        L.append(f"EXPLORATORY false alarms on the additional streams' uncut excerpts: "
                 f"{ec.fmt_rate(fa2['excerpts_with_a_reported_edit'])} excerpts with a reported edit, "
                 f"{fa2['edits_reported']} edits reported.")
        L.append("")
    L.append("## Readings and checks")
    L.append("")
    ck = d["checks_summary"]
    L.append("- A reported cut is an edit that `find_edits` gives the kind \"cut\" (positive jump). Its position "
             "is `clip_time_s`, the centre of the first frame after the offset change. \"Within +-30 s\" "
             "includes 30 s exactly. The duration error exists only for detected cuts.")
    L.append("- No threshold is applied to the lock of a cut trace. The uncut excerpt goes through the same two "
             "steps as a cut trace, and every edit reported on it counts as a false alarm.")
    L.append("- `find_edits` has no frame-weight argument: its fits use the unweighted correlation of "
             "`gunj/match.py` on the v2 trace. The search that locates each trace is the weighted one "
             "(`gunj/match_v2.py`).")
    L.append(f"- Checks passed for all {ck['n_streams_scored']} streams scored: the trace file and the matcher "
             "code have the SHA-256 recorded in the search cache, and the +-1-day search of the uncut trace "
             "reproduces the cached search.")
    L.append(f"- The uncut +-1-day lock is the same reference position as the full-scope lock for "
             f"{ck['preregistered_uncut_lock_equals_full_scope_lock']} of {pre['n_streams']} pre-registered "
             "streams.")
    L.append(f"- The cut positions and lengths in the trace files equal `harvest.cut_plan(video_id)` (seeded "
             f"by the split seed and the video ID) for {ck['cuts_equal_harvest_cut_plan']} of "
             f"{ck['n_streams_scored']} streams scored.")
    L.append(f"- `find_edits` defaults used: {json.dumps(d['method']['find_edits_defaults'], sort_keys=True)}.")
    L.append("- The script docstring lists every reading chosen where PROTOCOL section 11 leaves room.")
    return "\n".join(L) + "\n"


# -------------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="M5: edit detection on SYNTHETIC cuts (PROTOCOL section 11)")
    ap.add_argument("--results-csv", default="results/test_results.csv")
    ap.add_argument("--gate-frozen", default="results/gate_frozen.json")
    ap.add_argument("--splits-csv", default="data/splits.csv")
    ap.add_argument("--traces-dir", default="traces/test")
    ap.add_argument("--cache-dir", default="results/search_cache")
    ap.add_argument("--ref", default=None, help="reference folder (default: gunj.reference default)")
    ap.add_argument("--out-json", default="results/m5.json")
    ap.add_argument("--out-md", default="results/m5.md")
    ap.add_argument("--no-exploratory", action="store_true",
                    help="skip the EXPLORATORY gate-lock block")
    return ap


def _file_info(path: Path, repo: Path) -> dict:
    return {"path": ec.rel(path, repo), "sha256": hv.sha256_file(path)}


def main(argv=None, repo: Path = REPO) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(repo)
    t_run = time.time()
    results_csv = ec.resolve(args.results_csv, repo)
    gate_path = ec.resolve(args.gate_frozen, repo)
    splits_csv = ec.resolve(args.splits_csv, repo)
    traces_dir = ec.resolve(args.traces_dir, repo)
    cache_dir = ec.resolve(args.cache_dir, repo)
    rows = read_results(results_csv)
    splits = {r["video_id"]: r for r in ec.read_splits(splits_csv) if r["split"] == "test"}
    gate_ids = load_gate_locks(gate_path) if gate_path.exists() else None
    ref = gref.load(args.ref)                 # np.load(..., mmap_mode="r")
    msha = ec.matcher_sha(repo)
    try:
        blocks = build(rows, splits, gate_ids, traces_dir, cache_dir, ref, msha,
                       exploratory=not args.no_exploratory, repo=repo)
    except IntegrityError as e:
        print(f"refusing to write M5: {e}", file=sys.stderr)
        return 2
    pre = blocks["preregistered"]
    scored = list(pre["streams"])
    g = blocks["exploratory"].get("gate_locks") or {}
    scored += (g.get("additional_streams_only") or {}).get("streams", [])
    inputs = {"results_table": _file_info(results_csv, repo), "splits": _file_info(splits_csv, repo),
              "traces_dir": ec.rel(traces_dir, repo), "search_cache": ec.rel(cache_dir / "test", repo),
              "reference": {**ec.ref_identity(ref), "memory_mapped": isinstance(ref.freq, np.memmap)},
              "matcher_sha256": msha}
    if gate_ids is not None:
        inputs["gate_frozen"] = _file_info(gate_path, repo)
    out = {
        "written_ist": ec.now_ist(), "script": "scripts/m5_edits.py",
        "protocol": "PROTOCOL.md section 11, M5 (edit detection on SYNTHETIC cuts)",
        "SYNTHETIC": True,
        "note": "Every cut is SYNTHETIC: a stretch removed from the 60-min excerpt audio with "
                "gunj.synthetic.cut_audio at harvest time. Positions are seconds into the excerpt, which "
                f"starts {EXCERPT_START_S} s into the recording.",
        "inputs": inputs,
        "method": {
            "extractor": PRIMARY, "excerpt_min": LENGTH_MIN, "locate_scope": SCOPE,
            "locate_call": "calibrate_latency.run_search (weighted v2 matcher, +-1 day around the release "
                           "time in IST), top candidate",
            "find_edits_call": "gunj.match.find_edits(trace, reference, validity mask, lock) with no keyword",
            "find_edits_defaults": find_edits_defaults(),
            "detection": "an edit of kind \"cut\" with |clip_time_s - true position| <= 30 s; the nearest one",
            "detection_window_s": DETECT_S,
            "duration_error": "|jump_s of the detecting edit - true cut length|, detected cuts only",
            "false_alarm": "any edit reported on the uncut 60-min excerpt of the same streams",
            "intervals": "Wilson 95%, z = 1.959964 (PROTOCOL section 13; eval_common.wilson)",
            "per_cut_description_fields": "either_kind_within_window, other_edits and lock_minus_uncut_lock_s "
                                          "describe a cut trace and are not part of the metric; their totals "
                                          "are under \"exploratory\" (EXPLORATORY, post hoc)",
            "readings": "see the docstring of scripts/m5_edits.py"},
        "checks_summary": {
            "n_streams_scored": len(scored),
            "all_cache_checks_passed": True,
            "preregistered_uncut_lock_equals_full_scope_lock":
                sum(s["checks"]["uncut_lock_equals_full_scope_lock"] for s in pre["streams"]),
            "cuts_equal_harvest_cut_plan": sum(s["checks"]["cuts_equal_harvest_cut_plan"] for s in scored)},
        **blocks,
        "timing": {"wall_seconds": round(time.time() - t_run, 1), "processes": 1,
                   "find_edits_calls": sum(int(s["uncut"]["find_edits_ran"])
                                           + sum(int(c["find_edits_ran"]) for c in s["cuts"]) for s in scored)},
    }
    ec.write_json_atomic(ec.resolve(args.out_json, repo), out, repo)
    ec.write_text_atomic(ec.resolve(args.out_md, repo), render_md(out), repo)
    t = pre["totals"]["all"]
    print(json.dumps({"n_streams": pre["n_streams"], "n_cuts": pre["n_cuts"],
                      "detected": ec.fmt_rate(t["detected"]),
                      "duration_error_median_s": t["duration_error_s"]["median"],
                      "duration_error_max_s": t["duration_error_s"]["max"],
                      "uncut_excerpts_with_an_edit": ec.fmt_rate(pre["false_alarms"]["excerpts_with_a_reported_edit"]),
                      "edits_on_uncut_excerpts": pre["false_alarms"]["edits_reported"],
                      "wall_seconds": out["timing"]["wall_seconds"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
