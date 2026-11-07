#!/usr/bin/env python
"""Blind Test searches, results table, flow table, gate and false matches.

PROTOCOL.md sections 6 (truth T*), 8 (excerpts, scopes), 9 (hit,
confidence, verdict), 11 (M2, M3), 14 (blinding), 15 (gate) and 16
(reporting).

Modes
- Real (default): the Test split. Before any Test trace is touched, git must
  show that results/threshold.json, results/latency.json, this script and
  the code it runs (BLIND_FILES) are in the HEAD commit and unchanged
  (checked with git, not timestamps). Otherwise it exits 2 and writes no
  result. Outputs go to results/.
- --practice: the identical pipeline with the Calibration split in place of
  Test, written to a scratch folder (--out, default <temp>/gunj_practice),
  never results/. No Test trace is read. tau and L are read from
  --threshold / --latency (default results/threshold.json and
  results/latency.json) if those files exist; without them every verdict
  is "no verdict (tau not set)" and L = 0 ("latency not set").

Each run searches only streams that are not cached yet (or whose trace
SHA-256 changed), so it can be re-run every hour while Test traces arrive
and after a shutdown. Searches: every extractor (v2 primary, v1, naive), every
pre-registered excerpt length the stream has, every scope (full, +-90 days,
+-1 day), whatever the hum flag (PROTOCOL 8: "Every excerpt is searched in
every scope"). The verdict rule is applied afterwards. Report-half negatives
(real and SYNTHETIC) are searched the same way for M3.

Outputs (in results/, or the --out folder for --practice):
  test_results.csv   one row per stream x excerpt length x extractor x scope
  flow_table.md      the flow table (section 16) and the M2 cell counts
  gate.md            section 15 gate
  gate_frozen.json   the gate as frozen by the first complete run after the
                     decision time (reading 10)
  false_matches.md / false_matches.json   M3 on the report-half negatives
  evaluation.json    everything above in machine-readable form, plus timing
  search_cache/<split>/<video_id>.json    raw search output per stream

Readings chosen where PROTOCOL leaves room:
1. test_results.csv is in long form: "one row per Test stream and excerpt"
   is split further by extractor and scope, so each row has exactly one set
   of top-5 candidates, r, confidence, verdict and error. A stream whose
   download failed has one row with its harvest status and empty search
   fields; streams not yet attempted appear only in the flow table. A stream
   whose last try failed because the laptop's network was down
   (eval_common.network_down_row: failed, retryable, reason matching
   harvest.NETWORK_DOWN_RE) counts as not attempted yet (PROTOCOL section 19,
   11:50 IST: network loss is not a failed attempt): it has no row in
   test_results.csv and the flow table lists it under "not attempted yet
   (network down at last try; will be retried)".
2. Gate extractor: the gate (section 15) counts locks of the primary
   extractor (v2) only; v1 and the naive baseline are shown beside it as
   description. A stream counts if, for at least one excerpt length, its top
   candidate in the full or the +-90-day scope is within 30 s of T* AND that
   search's confidence is >= tau of its own cell. The hum flag is not part
   of the gate's text, so it is not required; confident hits (hum flag set)
   are shown beside the count.
3. "Processed by the decision time" (section 15) means the stream's harvest
   finished (manifest finished_utc) at or before --decision-time (default
   2026-11-07T22:00:00+05:30). Searches may run later; they do not depend on
   the clock. Streams harvested later are in the results but not in the gate.
4. Harvest health (section 15, condition 3), judged as of the decision
   time: no manifest row with finished_utc up to the decision time is
   "blocked"; no harvest log line (logs/harvest_*.log, logs/run_stage3.log)
   up to the decision time shows BLOCKED, a harvester exit code 3, or a
   WARNING/ERROR matching the harvester's own block pattern; and the most
   recent YouTube download attempt up to the decision time (any split,
   SYNTHETIC streams excluded) has status "done". The attempts are the
   manifest rows plus the timestamped outcome lines of the append-only
   harvest logs, so a retry after the decision time that overwrites a
   manifest row does not change the answer. A failed attempt for any reason
   (even "video not available") makes "its most recent download succeeded"
   false, except a failure caused by the laptop's network being down
   (harvest.NETWORK_DOWN_RE, PROTOCOL section 19, 11:50 IST): it never
   reached YouTube, is not a download attempt, and is skipped when finding
   the most recent download. gate.md lists those skipped rows.
5. One L for all extractors: T* = release time in IST + L + D, with L from
   results/latency.json (the primary extractor's L, section 6). With
   status "latency not calibrated" L is 0 and every result says so.
6. "tau or L committed by 22:00": the commit time of results/threshold.json
   and results/latency.json (git log) must be at or before the decision
   time.
7. A cell whose tau is undefined (null in threshold.json) gives "no
   verdict" and cannot count for the gate.
8. Flow table "hum present" uses the M1 flag (v1 on all downloaded audio).
   Its "match" and "hit" steps use the gate's cells (primary extractor, any
   length, full or +-90 days); verdict "match" needs the excerpt trace's own
   hum flag (section 9).
9. M3 counts a false match when a report-half negative excerpt's confidence
   is >= tau for its cell, with and without the hum-flag condition; real and
   SYNTHETIC separately and together. M5 (edit detection) and the M3
   reliability plot are not computed here.
10. The gate is frozen once. The first real run that starts at least 10 min
   after the decision time (FREEZE_GRACE: the harvester writes a manifest
   row seconds after its finished_utc) and finds every Test stream harvested
   by the decision time searched writes gate.md and results/gate_frozen.json
   (the gate and health dicts and the gate.md text). Every later run (hourly, and the full results) keeps
   gate.md and the gate and health sections of evaluation.json as frozen and
   only updates the other outputs. A run with a different --decision-time
   than the frozen one refuses (exit 2). Runs before then write an INTERIM or
   "not frozen yet" gate.md.
11. Harvest reasons, log lines and search errors are passed through
   eval_common.redact_paths (<repo>, <home>) before they reach any output,
   so a local path in a failure message cannot stop the run;
   check_no_abs_paths stays as the last guard.
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import argparse
import csv
import datetime as dt
import io
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd                       # noqa: E402

import eval_common as ec                  # noqa: E402
from eval_common import EXTRACTORS, LENGTHS, PRIMARY, SCOPES, SCOPE_LABEL, HIT_S, COARSE_S  # noqa: E402
from gunj import reference as gref        # noqa: E402

DECISION_TIME = "2026-11-07T22:00:00+05:30"
GATE_SCOPES = ("full", "90d")
GATE_MIN_STREAMS = 5
BLIND_FILES = [
    "results/threshold.json", "results/latency.json",
    "scripts/evaluate.py", "scripts/eval_common.py", "scripts/calibrate_latency.py", "scripts/harvest.py",
    "gunj/config.json", "gunj/match.py", "gunj/match_v2.py", "gunj/reference.py", "data/splits.csv",
]
TEST_LENGTH_TOTAL = 533       # excerpts per extractor over the 150 Test streams (108x4 + 17x3 + 25x2)
GATE_FROZEN_NAME = "gate_frozen.json"
# The harvester writes a stream's manifest row a few seconds after its finished_utc, so the
# freezing run must start a little after the decision time (reading 10).
FREEZE_GRACE = dt.timedelta(minutes=10)
NETWORK_DOWN_BUCKET = "not attempted yet (network down at last try; will be retried)"


def _now() -> dt.datetime:
    return dt.datetime.now(ec.IST)


def _utc_iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds")

COLUMNS = [
    "mode", "video_id", "split", "channel", "venue_type", "release_time_ist", "label_drift_s",
    "label_stable", "channel_status", "harvest_status", "harvest_reason", "harvested_by_decision_time",
    "hum_present_v1_full", "hum_max_median_ratio_v1_full", "extractor", "is_primary", "excerpt_min",
    "scope", "trace_hum_present", "trace_hum_max_median_ratio", "trace_hum_median_peak_to_floor",
    "n_frames", "weighted_correlation", "positions_searched", "search_error", "top1_start_ist", "r1",
    "runner_up_start_ist", "r2", "margin", "confidence", "tau", "confidence_ge_tau", "verdict",
    "verdict_reason", "L_s", "latency_status", "true_start_ist", "error_s", "hit_30s", "hit_120s",
    "hit_600s", "outcome", "error_D0_s", "hit_30s_D0", "top5_start_ist", "top5_r", "top5_error_s",
]


# ------------------------------------------------------------------ inputs

def load_latency(path: Path) -> dict:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if d.get("L_s") is None:
        raise ValueError(f"{path.name} has no L_s")
    return {"L_s": int(d["L_s"]), "status": d.get("status") or "", "source": path.name}


def truth_index(ref, release_ts: int, L: int, D: float) -> int:
    rel_ist = gref.unix_to_ist(int(release_ts))
    return ref.index_of(rel_ist + pd.Timedelta(seconds=float(L) + float(D)), check=False)


def evaluate_search(s: dict, rec: dict, tstar: int, tstar0: int, tau, ref, repo: Path = ec.REPO) -> dict:
    """Apply sections 9 and 15 to one cached search."""
    ext, m = s["ext"], s["length_min"]
    hum = ec.trace_hum(rec, ext, f"x{m}")
    hum_flag = bool(hum.get("present", False))
    cands = s.get("candidates") or []
    top = cands[0] if cands else None
    conf = s.get("confidence")
    conf_ge = conf is not None and tau is not None and conf >= tau
    if s.get("error"):
        reason = "search could not run: " + ec.redact_paths(s["error"], repo)
    elif top is None:
        reason = "no admissible reference window in the scope"
    elif not hum_flag:
        reason = "no hum flag on this trace"
    elif conf is None:
        reason = "no runner-up candidate"
    elif tau is None:
        reason = "tau not set for this cell"
    elif not conf_ge:
        reason = "confidence below tau"
    else:
        reason = "hum flag set and confidence >= tau"
    verdict = "match" if (hum_flag and conf_ge) else "no verdict"
    err = None if top is None else int(top["start_index"] - tstar)
    err0 = None if top is None else int(top["start_index"] - tstar0)
    hit = err is not None and abs(err) <= HIT_S
    outcome = ("confident hit" if hit else "confident wrong") if verdict == "match" else "no verdict"
    return {"ext": ext, "length_min": m, "scope": s["scope"], "hum_flag": hum_flag,
            "hum_max": hum.get("max_median_peak_to_floor"), "hum_meds": hum.get("median_peak_to_floor"),
            "top": top, "runner_up": s.get("runner_up"), "conf": conf, "tau": tau, "conf_ge_tau": conf_ge,
            "verdict": verdict, "verdict_reason": reason, "error_s": err, "hit": hit,
            "hit_120": err is not None and abs(err) <= COARSE_S[0],
            "hit_600": err is not None and abs(err) <= COARSE_S[1],
            "outcome": outcome, "error_D0_s": err0, "hit_D0": err0 is not None and abs(err0) <= HIT_S,
            "lock": hit and conf_ge, "lock_D0": (err0 is not None and abs(err0) <= HIT_S) and conf_ge,
            "search": s, "true_start_ist": str(ref.time_of(tstar)), "cands": cands}


def _b(v):
    return "" if v is None else ("True" if v else "False")


def _n(v, nd=None):
    if v is None:
        return ""
    if nd is None:
        return repr(float(v)) if isinstance(v, float) else str(v)
    return f"{float(v):.{nd}f}"


# ----------------------------------------------------------------- tables

def stream_state(r: dict, m: dict | None, rec: dict | None, decision_utc, repo: Path = ec.REPO) -> dict:
    st = None if m is None else m.get("status")
    fin = None if m is None else ec.parse_utc(m.get("finished_utc"))
    return {"video_id": r["video_id"], "status": st or "not attempted",
            "reason": "" if m is None else ec.redact_paths(m.get("reason") or "", repo),
            "by_decision": fin is not None and fin <= decision_utc,
            "hum_m1": None if m is None or m.get("hum_present_v1") in (None, "") else m.get("hum_present_v1") == "True",
            "hum_m1_max": None if m is None else (m.get("hum_max_median_ratio_v1") or None),
            "record": rec}


def build_rows(mode: str, stream_rows: list, manifest: dict, records: dict, tau_table, lat: dict,
               ref, decision_utc, repo: Path = ec.REPO) -> tuple:
    """CSV rows and per-stream evaluated searches."""
    csv_rows, per_stream = [], {}
    L = lat["L_s"]
    for r in stream_rows:
        vid = r["video_id"]
        m = manifest.get(vid)
        rec = records.get(f"{r['split']}/{vid}")
        stt = stream_state(r, m, rec, decision_utc, repo)
        base = {"mode": mode, "video_id": vid, "split": r["split"], "channel": r.get("channel"),
                "venue_type": r.get("venue_type"), "release_time_ist": r.get("release_time_ist"),
                "label_drift_s": r.get("label_drift_s"), "label_stable": r.get("label_stable"),
                "channel_status": r.get("official_status"), "harvest_status": stt["status"],
                "harvest_reason": stt["reason"], "harvested_by_decision_time": _b(stt["by_decision"]),
                "hum_present_v1_full": _b(stt["hum_m1"]), "hum_max_median_ratio_v1_full": stt["hum_m1_max"] or "",
                "L_s": L, "latency_status": lat["status"]}
        if m is None or ec.network_down_row(m):        # not attempted yet (reading 1)
            continue
        if rec is None:
            csv_rows.append(base)
            continue
        D = float(r.get("label_drift_s") or 0.0)
        tstar = truth_index(ref, int(r["release_timestamp"]), L, D)
        tstar0 = truth_index(ref, int(r["release_timestamp"]), L, 0.0)
        evs = []
        for s in rec["searches"]:
            ev = evaluate_search(s, rec, tstar, tstar0, ec.tau_of(tau_table, s["ext"], s["length_min"], s["scope"]),
                                 ref, repo)
            evs.append(ev)
            top, ru, srch = ev["top"], ev["runner_up"], ev["search"]
            csv_rows.append({**base,
                "extractor": ev["ext"], "is_primary": _b(ev["ext"] == PRIMARY), "excerpt_min": ev["length_min"],
                "scope": ev["scope"], "trace_hum_present": _b(ev["hum_flag"]),
                "trace_hum_max_median_ratio": _n(ev["hum_max"]),
                "trace_hum_median_peak_to_floor": json.dumps(ev["hum_meds"] or {}, separators=(",", ":")),
                "n_frames": srch.get("n_frames", ""), "weighted_correlation": _b(srch.get("weighted")),
                "positions_searched": srch.get("n_positions", ""), "search_error": ec.redact_paths(srch.get("error") or "", repo),
                "top1_start_ist": "" if top is None else top["start_ist"], "r1": "" if top is None else _n(top["r"], 6),
                "runner_up_start_ist": "" if ru is None else ru["start_ist"], "r2": "" if ru is None else _n(ru["r"], 6),
                "margin": _n(srch.get("margin"), 6), "confidence": _n(ev["conf"]), "tau": _n(ev["tau"]),
                "confidence_ge_tau": _b(ev["conf_ge_tau"]), "verdict": ev["verdict"],
                "verdict_reason": ev["verdict_reason"], "true_start_ist": ev["true_start_ist"],
                "error_s": _n(ev["error_s"]), "hit_30s": _b(ev["hit"] if top else None),
                "hit_120s": _b(ev["hit_120"] if top else None), "hit_600s": _b(ev["hit_600"] if top else None),
                "outcome": ev["outcome"], "error_D0_s": _n(ev["error_D0_s"]),
                "hit_30s_D0": _b(ev["hit_D0"] if top else None),
                "top5_start_ist": ";".join(c["start_ist"] for c in ev["cands"]),
                "top5_r": ";".join(f"{c['r']:.6f}" for c in ev["cands"]),
                "top5_error_s": ";".join(str(int(c["start_index"] - tstar)) for c in ev["cands"])})
        for mm in rec.get("lengths_missing", []):
            csv_rows.append({**base, "excerpt_min": mm, "search_error": "listed excerpt length has no trace"})
        per_stream[vid] = {"row": r, "state": stt, "evs": evs}
    return csv_rows, per_stream


def gate_cells(evs: list, ext: str = PRIMARY) -> list:
    return [e for e in evs if e["ext"] == ext and e["scope"] in GATE_SCOPES]


def flow_table(stream_rows: list, manifest: dict, per_stream: dict, repo: Path = ec.REPO) -> dict:
    """Section 16 flow: selected -> attempted -> audio -> trace -> hum -> match -> hit."""
    steps, drops = [], []
    sel = [r["video_id"] for r in stream_rows]
    steps.append(("selected", sel))
    att, d = [], {}
    for v in sel:
        m = manifest.get(v)
        st = None if m is None else m.get("status")
        if ec.network_down_row(m):                     # reading 1: not a download attempt
            d.setdefault(NETWORK_DOWN_BUCKET, []).append(v)
        elif st in ("done", "failed", "blocked"):
            att.append(v)
        else:
            d.setdefault("not attempted yet" if m is None
                         else f"{st}: {ec.redact_paths(m.get('reason') or '', repo)}", []).append(v)
    steps.append(("download attempted", att)); drops.append(d)
    aud, d = [], {}
    for v in att:
        m = manifest[v]
        if m["status"] == "done":
            aud.append(v)
        else:
            d.setdefault(f"{m['status']}: {ec.redact_paths(m.get('reason') or '', repo)}", []).append(v)
    steps.append(("audio obtained", aud)); drops.append(d)
    tr, d = [], {}
    for v in aud:
        ps = per_stream.get(v)
        if ps and any(e["ext"] == PRIMARY for e in ps["evs"]):
            tr.append(v)
        else:
            d.setdefault("not searched yet" if ps is None else f"no {PRIMARY} excerpt trace", []).append(v)
    steps.append((f"trace extracted ({PRIMARY}) and searched", tr)); drops.append(d)
    hum, d = [], {}
    for v in tr:
        if per_stream[v]["state"]["hum_m1"]:
            hum.append(v)
        else:
            d.setdefault("no hum (M1: no v1 harmonic median peak-to-floor >= 3 on all audio)", []).append(v)
    steps.append(("hum present (M1)", hum)); drops.append(d)
    mat, d = [], {}
    for v in hum:
        cells = gate_cells(per_stream[v]["evs"])
        if any(e["verdict"] == "match" for e in cells):
            mat.append(v)
        else:
            why = sorted({e["verdict_reason"] for e in cells}) or ["no searches"]
            d.setdefault("no match in any length, full or +-90 d (" + "; ".join(why) + ")", []).append(v)
    steps.append((f"match ({PRIMARY}, any length, full or +-90 d)", mat)); drops.append(d)
    hit, d = [], {}
    for v in mat:
        if any(e["outcome"] == "confident hit" for e in gate_cells(per_stream[v]["evs"])):
            hit.append(v)
        else:
            d.setdefault("every match is a wrong lock (|e| > 30 s)", []).append(v)
    steps.append(("hit (confident hit)", hit)); drops.append(d)
    extra = [v for v in tr if v not in hum and any(e["verdict"] == "match" for e in gate_cells(per_stream[v]["evs"]))]
    return {"steps": [{"step": s, "n": len(v), "video_ids": v} for s, v in steps],
            "drops": [{"from": steps[i][0], "to": steps[i + 1][0], "reasons": d} for i, d in enumerate(drops)],
            "note_match_without_m1_hum": extra}


def m2_cells(per_stream: dict) -> dict:
    out = {}
    for ext in EXTRACTORS:
        for m in LENGTHS:
            for scope in SCOPES:
                evs = [e for ps in per_stream.values() for e in ps["evs"]
                       if e["ext"] == ext and e["length_min"] == m and e["scope"] == scope]
                humv = [e for ps in per_stream.values() if ps["state"]["hum_m1"] for e in ps["evs"]
                        if e["ext"] == ext and e["length_min"] == m and e["scope"] == scope]
                n = len(evs)
                out.setdefault(ext, {}).setdefault(str(m), {})[scope] = {
                    "n": n, "top1_hit": ec.rate_dict(sum(e["hit"] for e in evs), n),
                    "top1_hit_D0": ec.rate_dict(sum(e["hit_D0"] for e in evs), n),
                    "top1_hit_hum_present": ec.rate_dict(sum(e["hit"] for e in humv), len(humv)),
                    "confident_hit": ec.rate_dict(sum(e["outcome"] == "confident hit" for e in evs), n),
                    "confident_wrong": ec.rate_dict(sum(e["outcome"] == "confident wrong" for e in evs), n),
                    "no_verdict": ec.rate_dict(sum(e["outcome"] == "no verdict" for e in evs), n),
                    "tau": evs[0]["tau"] if evs else None}
    return out


def gate_eval(per_stream: dict, health: dict, commits: dict, decision_utc, mode: str) -> dict:
    proc = {v: ps for v, ps in per_stream.items() if ps["state"]["by_decision"]}
    by_ext = {}
    for ext in EXTRACTORS:
        locks = {v: [e for e in gate_cells(ps["evs"], ext) if e["lock"]] for v, ps in proc.items()}
        by_ext[ext] = {
            "n_streams_with_lock": sum(1 for v in locks if locks[v]),
            "n_streams_with_hit": sum(1 for ps in proc.values() if any(e["hit"] for e in gate_cells(ps["evs"], ext))),
            "n_streams_with_lock_D0": sum(1 for ps in proc.values() if any(e["lock_D0"] for e in gate_cells(ps["evs"], ext))),
            "n_streams_with_confident_hit": sum(1 for ps in proc.values()
                                                if any(e["outcome"] == "confident hit" for e in gate_cells(ps["evs"], ext))),
            "locked_streams": sorted(v for v in locks if locks[v]),
        }
    g = by_ext[PRIMARY]
    c12 = g["n_streams_with_lock"] >= GATE_MIN_STREAMS
    if mode == "practice":
        committed_ok, committed_note = False, "not applicable: practice run (Calibration traces, nothing committed)"
    else:
        bad = [f for f, c in commits.items() if f in ("results/threshold.json", "results/latency.json")
               and (c.get("committed") is None or ec.parse_utc(c["committed"]) > decision_utc)]
        committed_ok = not bad
        committed_note = "both committed at or before the decision time" if committed_ok else \
            "committed after the decision time or not at all: " + ", ".join(bad)
    met = c12 and health["ok"] and committed_ok and mode == "real"
    return {"N_processed": len(proc), "by_extractor": by_ext, "condition_1_2_met": c12,
            "condition_3_met": health["ok"], "tau_L_committed_in_time": committed_ok,
            "tau_L_committed_note": committed_note, "gate_met": met}


def m3_cells(neg_records: list, tau_table) -> dict:
    out = {}
    for ext in EXTRACTORS:
        for m in LENGTHS:
            for scope in SCOPES:
                tau = ec.tau_of(tau_table, ext, m, scope)
                trials = []
                for rec in neg_records:
                    s = ec.search_of(rec, ext, m, scope)
                    if s is None:
                        continue
                    conf = s.get("confidence")
                    hum = bool(ec.trace_hum(rec, ext, f"x{m}").get("present", False))
                    fm = conf is not None and tau is not None and conf >= tau
                    trials.append({"video_id": rec["video_id"], "kind": "SYNTHETIC" if rec["split"] == "negative_synthetic" else "real",
                                   "confidence": conf, "hum_flag": hum, "false_match": fm, "false_match_hum": fm and hum})
                cell = {"tau": tau, "trials": sorted(trials, key=lambda t: (t["confidence"] is None, -(t["confidence"] or 0)))}
                for kind, sel in (("real", lambda t: t["kind"] == "real"), ("SYNTHETIC", lambda t: t["kind"] == "SYNTHETIC"),
                                  ("together", lambda t: True)):
                    ts = [t for t in trials if sel(t)]
                    cell[kind] = {"false_match": ec.rate_dict(sum(t["false_match"] for t in ts), len(ts)),
                                  "false_match_with_hum_flag": ec.rate_dict(sum(t["false_match_hum"] for t in ts), len(ts))}
                out.setdefault(ext, {}).setdefault(str(m), {})[scope] = cell
    return out


# ---------------------------------------------------------------- writers

def write_csv(path: Path, rows: list, repo: Path) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in COLUMNS})
    ec.write_text_atomic(path, buf.getvalue(), repo)


def _e(v):
    return "n/a" if v is None else f"{v:+d}"


def _c(v, nd=3):
    return "n/a" if v is None else f"{v:.{nd}f}"


def header_lines(d: dict) -> list:
    if d["mode"] == "practice":
        return ["**PRACTICE RUN on Calibration traces, not Test. Not a gate result.** "
                "It exercises the identical pipeline; its numbers must not be reported as Test results.", ""]
    return []


def write_flow_md(path: Path, d: dict, repo: Path) -> None:
    L = [f"# Flow table and M2 cell counts ({d['split_label']})\n"] + header_lines(d)
    L.append(f"Generated by `scripts/evaluate.py` at {d['written_ist']} from `{d['files']['csv']}` "
             f"(same run). PROTOCOL.md sections 11 and 16. {d['latency_line']}\n")
    L.append("## Flow (streams)\n")
    L.append("| step | streams | dropped from the step before, with reasons |")
    L.append("|---|---|---|")
    f = d["flow"]
    for i, s in enumerate(f["steps"]):
        if i == 0:
            L.append(f"| {s['step']} | {s['n']} | |")
            continue
        reasons = f["drops"][i - 1]["reasons"]
        txt = "; ".join(f"{k}: {len(v)} ({', '.join(v[:12])}{', ...' if len(v) > 12 else ''})"
                        for k, v in reasons.items())
        L.append(f"| {s['step']} | {s['n']} | {txt} |")
    if f["note_match_without_m1_hum"]:
        L.append(f"\nAlso matched although the M1 flag is off (excerpt trace's own hum flag set): "
                 f"{', '.join(f['note_match_without_m1_hum'])}.")
    L.append("\n## M2 cells (top-1 hit rate ignores tau; Wilson 95%)\n")
    for ext in EXTRACTORS:
        L.append(f"### {ext}{' (primary)' if ext == PRIMARY else ''}\n")
        L.append("| excerpt | scope | n | tau | top-1 hit | top-1 hit, D = 0 | top-1 hit, M1 hum present | "
                 "confident hit | confident wrong | no verdict |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for m in LENGTHS:
            for s in SCOPES:
                c = d["m2"][ext][str(m)][s]
                L.append(f"| {m} min | {SCOPE_LABEL[s]} | {c['n']} | {_c(c['tau'], 4)} | {ec.fmt_rate(c['top1_hit'])} | "
                         f"{ec.fmt_rate(c['top1_hit_D0'])} | {ec.fmt_rate(c['top1_hit_hum_present'])} | "
                         f"{c['confident_hit']['k']} | {c['confident_wrong']['k']} | {c['no_verdict']['k']} |")
        L.append("")
    ec.write_text_atomic(path, "\n".join(L) + "\n", repo)


def gate_md_text(d: dict, per_stream: dict) -> str:
    g, h = d["gate"], d["health"]
    p = g["by_extractor"][PRIMARY]
    final = d["after_decision_time"]
    fz = d.get("gate_freeze") or {}
    L = ["# Gate, Saturday 7 November 2026, 22:00 IST (PROTOCOL.md section 15)\n"] + header_lines(d)
    L.append(f"Generated by `scripts/evaluate.py` at {d['written_ist']}. Decision time: {d['decision_time']}. "
             + ("This run is after the decision time: only streams harvested by then are counted."
                if final else "**INTERIM**: this run is before the decision time; streams harvested later "
                              "are not in it yet.") + "\n")
    if fz.get("status") == "frozen this run":
        L.append(f"**FROZEN**: this run started at {fz['run_started_ist']}, after the decision time and its "
                 f"{int(FREEZE_GRACE.total_seconds() // 60)}-min grace, and every "
                 f"Test stream harvested by then was searched. Later runs keep this file and the gate in "
                 f"`evaluation.json` unchanged (`{fz['file']}`).\n")
    elif final and fz.get("status"):
        L.append(f"Not frozen yet: {fz['status']}. A later run freezes the gate.\n")
    L.append(f"## Verdict: {'GATE MET' if g['gate_met'] else 'GATE NOT MET'}\n")
    L.append(f"1 and 2. Test streams that lock blind (primary extractor {PRIMARY}; top candidate in the full or "
             f"+-90-day scope within {HIT_S} s of T* for at least one excerpt length, with confidence >= tau of "
             f"that cell): **{p['n_streams_with_lock']}** of N = {g['N_processed']} processed "
             f"(need {GATE_MIN_STREAMS}): {'met' if g['condition_1_2_met'] else 'not met'}.")
    L.append(f"   - Locked streams: {', '.join(p['locked_streams']) or 'none'}.")
    L.append(f"   - S1 (D = 0, no label-drift correction): {p['n_streams_with_lock_D0']} streams.")
    L.append(f"   - Hit regardless of tau (condition 1 alone): {p['n_streams_with_hit']}; confident hits "
             f"(also hum flag set): {p['n_streams_with_confident_hit']}.")
    for ext in EXTRACTORS:
        if ext != PRIMARY:
            e = g["by_extractor"][ext]
            L.append(f"   - {ext} (description only, not the gate): {e['n_streams_with_lock']} lock, "
                     f"{e['n_streams_with_lock_D0']} with D = 0.")
    L.append(f"3. Harvest health: {'met' if h['ok'] else 'NOT met'}. Blocked manifest rows: "
             f"{', '.join(h['blocked_rows']) or 'none'}. Log lines showing a block: {len(h['log_matches'])}. "
             f"Most recent download: " + ("none" if not h["most_recent_download"] else
             f"{h['most_recent_download']['video_id']} ({h['most_recent_download']['split']}) "
             f"{h['most_recent_download']['status']} at {h['most_recent_download']['finished_utc']}"
             + (f", {h['most_recent_download']['reason']}" if h["most_recent_download"].get("reason") else "")) + ".")
    nd = h.get("network_down_rows") or []
    nd_txt = ", ".join(f"{x['video_id']} at {x['finished_utc']}" for x in nd[-12:])
    L.append(f"   - Failures caused by the laptop's network being down, skipped as not download attempts "
             f"(PROTOCOL section 19, 11:50 IST): {len(nd)}"
             + (f" ({nd_txt}{'; the latest 12 shown' if len(nd) > 12 else ''})" if nd else "") + ".")
    for x in h["log_matches"][:20]:
        L.append(f"   - `{x['file']}` line {x['line']}: {x['text']}")
    L.append(f"- tau and L committed by the decision time: {g['tau_L_committed_note']}.")
    L.append("")
    L.append("## Inputs\n")
    L.append(f"- {d['latency_line']}")
    L.append(f"- tau: `{d['files']['threshold']}`" + ("" if d["tau_available"] else " (not set: every verdict is no verdict)"))
    for f, c in d["commits"].items():
        L.append(f"- `{f}`: commit {c.get('commit') or 'none'} at {c.get('committed') or 'n/a'}")
    if d["blinding"]:
        L.append(f"- Blinding check (git): {d['blinding']}")
    L.append("")
    L.append(f"## Every processed stream (N = {g['N_processed']}), hits and misses\n")
    L.append(f"Primary extractor {PRIMARY}. e = implied start of the top candidate − T* (s); conf = atanh(r1) − "
             "atanh(r2); LOCK = |e| ≤ 30 s and conf ≥ tau. e(D=0) is S1.\n")
    L.append("| stream | venue | D (s) | stable | M1 hum | min | full: e / conf / tau | full e(D=0) | "
             "+-90 d: e / conf / tau | +-90 d e(D=0) | result |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    proc = [(v, ps) for v, ps in per_stream.items() if ps["state"]["by_decision"]]
    for v, ps in proc:
        r = ps["row"]
        evs = {(e["length_min"], e["scope"]): e for e in ps["evs"] if e["ext"] == PRIMARY}
        lens = sorted({e["length_min"] for e in ps["evs"] if e["ext"] == PRIMARY})
        if not lens:
            L.append(f"| {v} | {r.get('venue_type')} | {r.get('label_drift_s')} | {r.get('label_stable')} | "
                     f"{ps['state']['hum_m1']} | | no {PRIMARY} trace | | | | |")
        for i, m in enumerate(lens):
            cells = []
            for s in GATE_SCOPES:
                e = evs.get((m, s))
                if e is None:
                    cells += ["n/a", "n/a"]
                else:
                    cells += [f"{_e(e['error_s'])} / {_c(e['conf'])} / {_c(e['tau'])}", _e(e["error_D0_s"])]
            res = [s for s in GATE_SCOPES if evs.get((m, s)) and evs[(m, s)]["lock"]]
            hits = [s for s in GATE_SCOPES if evs.get((m, s)) and evs[(m, s)]["hit"]]
            txt = ("LOCK " + ",".join(res)) if res else ("hit below tau " + ",".join(hits) if hits else "miss")
            head = (f"| {v} | {r.get('venue_type')} | {r.get('label_drift_s')} | {r.get('label_stable')} | "
                    f"{ps['state']['hum_m1']} ") if i == 0 else "| | | | | "
            L.append(head + f"| {m} | {cells[0]} | {cells[1]} | {cells[2]} | {cells[3]} | {txt} |")
    late = [v for v, ps in per_stream.items() if not ps["state"]["by_decision"]]
    if late:
        L.append(f"\nSearched but harvested after the decision time (not counted): {', '.join(late)}.")
    fl = [s for s in d["flow"]["drops"][1]["reasons"].items()] if len(d["flow"]["drops"]) > 1 else []
    if fl:
        L.append("\nAttempted but no audio (not in N): " + "; ".join(f"{k}: {', '.join(v)}" for k, v in fl) + ".")
    return "\n".join(L) + "\n"


def write_gate_md(path: Path, d: dict, per_stream: dict, repo: Path) -> str:
    text = gate_md_text(d, per_stream)
    ec.write_text_atomic(path, text, repo)
    return text


def gate_unsearched(stream_rows: list, manifest: dict, per_stream: dict, decision_utc) -> list:
    """Streams harvested (done) by the decision time that have no search record yet."""
    out = []
    for r in stream_rows:
        m = manifest.get(r["video_id"]) or {}
        fin = ec.parse_utc(m.get("finished_utc"))
        if m.get("status") == "done" and fin is not None and fin <= decision_utc and r["video_id"] not in per_stream:
            out.append(r["video_id"])
    return out


def load_frozen_gate(path: Path):
    """The frozen gate (reading 10), or None if there is none. Raises ValueError if unreadable."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        fz = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"cannot read {path.name}: {e}") from e
    if not isinstance(fz, dict) or not all(k in fz for k in ("decision_utc", "gate", "health", "gate_md")):
        raise ValueError(f"{path.name} lacks decision_utc, gate, health or gate_md")
    return fz


def write_m3_md(path: Path, d: dict, repo: Path) -> None:
    m3 = d["m3"]
    L = ["# M3: false matches on the report-half negatives\n"] + header_lines(d)
    L.append(f"Generated by `scripts/evaluate.py` at {d['written_ist']}. PROTOCOL.md section 11 (M3). A false match "
             "is a report-half negative excerpt whose confidence reaches tau for its cell. Real and SYNTHETIC "
             "negatives are shown separately and together; SYNTHETIC negatives are generated by gunj/synthetic.py.\n")
    L.append(f"- Report-half negatives searched: {d['m3_counts']['real']} real, {d['m3_counts']['SYNTHETIC']} SYNTHETIC "
             f"(of 25 real and 20 SYNTHETIC)."
             + (f" {d['m3_counts']['harvested_not_yet_searched']} harvested negatives are still being searched; "
                "this file is rewritten when they are done." if d["m3_counts"]["harvested_not_yet_searched"] else "")
             + "\n")
    for ext in EXTRACTORS:
        L.append(f"## {ext}{' (primary)' if ext == PRIMARY else ''}\n")
        L.append("| excerpt | scope | tau | real | real, hum flag | SYNTHETIC | SYNTHETIC, hum flag | together | together, hum flag |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for m in LENGTHS:
            for s in SCOPES:
                c = m3[ext][str(m)][s]
                L.append(f"| {m} min | {SCOPE_LABEL[s]} | {_c(c['tau'], 4)} | " + " | ".join(
                    ec.fmt_rate(c[k][f]) for k in ("real", "SYNTHETIC", "together")
                    for f in ("false_match", "false_match_with_hum_flag")) + " |")
        L.append("")
    ec.write_text_atomic(path, "\n".join(L) + "\n", repo)


# ------------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Blind Test evaluation and gate (PROTOCOL sections 14-16)")
    ap.add_argument("--practice", action="store_true",
                    help="run the identical pipeline on Calibration traces into a scratch folder")
    ap.add_argument("--out", default=None, help="practice output folder (default <temp>/gunj_practice)")
    ap.add_argument("--threshold", default=None, help="practice only: tau file (default results/threshold.json)")
    ap.add_argument("--latency", default=None, help="practice only: L file (default results/latency.json)")
    ap.add_argument("--manifest", default="data/harvest_manifest.csv")
    ap.add_argument("--splits-csv", default="data/splits.csv")
    ap.add_argument("--ref", default=None, help="reference folder (default: gunj.reference default)")
    ap.add_argument("--workers", type=int, default=None, help="default max(1, min(4, cpu_count - 2))")
    ap.add_argument("--decision-time", default=DECISION_TIME)
    ap.add_argument("--no-negatives", action="store_true", help="skip the M3 report-half negative searches")
    return ap


def main(argv=None, repo: Path = ec.REPO) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(repo)
    if args.practice:
        out_dir = Path(args.out) if args.out else Path(tempfile.gettempdir()) / "gunj_practice"
        out_dir = out_dir if out_dir.is_absolute() else repo / out_dir
        if ec.is_under(out_dir, repo / "results"):
            print("refusing: --practice output must not be under results/", file=sys.stderr)
            return 2
        log_path, lock_path = out_dir / "evaluate.log", out_dir / "evaluate.lock"
    else:
        if args.out or args.threshold or args.latency:
            print("refusing: --out, --threshold and --latency are for --practice only", file=sys.stderr)
            return 2
        out_dir = repo / "results"
        log_path, lock_path = repo / "logs" / "evaluate.log", repo / "logs" / "evaluate.lock"
    out_dir.mkdir(parents=True, exist_ok=True)
    log = ec.setup_logging(log_path, "evaluate")
    lock = ec.hv.RunLock(lock_path)
    if not lock.acquire():
        log.error("another evaluate.py run holds %s; exiting", ec.rel(lock_path, repo))
        return 4
    try:
        return _run(args, repo, out_dir, log)
    finally:
        lock.release()


def _run(args, repo: Path, out_dir: Path, log) -> int:
    t_run = time.time()
    started = _now()
    mode = "practice" if args.practice else "real"
    split = "calibration" if args.practice else "test"
    decision_utc = dt.datetime.fromisoformat(args.decision_time)
    if decision_utc.tzinfo is None:
        decision_utc = decision_utc.replace(tzinfo=ec.IST)              # a naive time is IST
    frozen_path = out_dir / GATE_FROZEN_NAME
    frozen = None
    if mode == "real":                                                   # reading 10
        try:
            frozen = load_frozen_gate(frozen_path)
        except ValueError as e:
            log.error("REFUSING: %s; the frozen gate must not be recomputed", e)
            return 2
        if frozen is not None and frozen["decision_utc"] != _utc_iso(decision_utc):
            log.error("REFUSING: %s froze the gate for decision time %s; this run asks for %s",
                      ec.rel(frozen_path, repo), frozen.get("decision_time"), args.decision_time)
            return 2
    splits = ec.read_splits(ec.resolve(args.splits_csv, repo))
    manifest = ec.read_manifest(ec.resolve(args.manifest, repo))
    blinding = None
    commits = {}
    if mode == "real":
        problems = ec.committed_unmodified(BLIND_FILES, repo)
        if problems:
            log.error("REFUSING to touch any Test trace: these files must be committed and unmodified first:")
            for p in problems:
                log.error("  %s", p)
            return 2
        blinding = f"passed: {len(BLIND_FILES)} files committed and unmodified ({', '.join(BLIND_FILES)})"
        commits = {f: ec.last_commit(f, repo) for f in ("results/threshold.json", "results/latency.json",
                                                         "scripts/evaluate.py")}
        thr_path, lat_path = repo / "results" / "threshold.json", repo / "results" / "latency.json"
    else:
        thr_path = ec.resolve(args.threshold or "results/threshold.json", repo)
        lat_path = ec.resolve(args.latency or "results/latency.json", repo)
    try:
        tau_table = ec.load_tau_table(thr_path) if thr_path.exists() else None
        lat = load_latency(lat_path) if lat_path.exists() else None
    except (ValueError, KeyError, AttributeError) as e:
        log.error("REFUSING: cannot read tau or L: %s", e)
        return 2
    if mode == "real" and (tau_table is None or lat is None):
        log.error("REFUSING: results/threshold.json or results/latency.json is missing")
        return 2
    if lat is None:
        lat = {"L_s": 0, "status": "latency not set (practice run, no latency file)", "source": None}

    ref = gref.load(args.ref)
    stream_rows = [r for r in splits if r["split"] == split]
    jobs = []
    for r in stream_rows:
        m = manifest.get(r["video_id"])
        if not m or m.get("status") != "done" or not m.get("trace_path"):
            continue
        tp = ec.resolve(m["trace_path"], repo)
        if tp.parent.name != split:
            raise SystemExit(f"refusing to read a trace outside traces/{split}/: {ec.rel(tp, repo)}")
        if not tp.exists():
            log.warning("%s: manifest says done but %s is missing", r["video_id"], ec.rel(tp, repo))
            continue
        jobs.append(ec.Job(split=split, video_id=r["video_id"], trace_path=m["trace_path"],
                           release_ts=int(r["release_timestamp"]), lengths=ec.excerpt_lengths(r)))
    neg_rows = [r for r in splits if r["split"] in ec.NEGATIVE_SPLITS and r.get("half") == "report"]
    neg_jobs = []
    if not args.no_negatives:
        for r in neg_rows:
            m = manifest.get(r["video_id"])
            if m and m.get("status") == "done" and m.get("trace_path") and ec.resolve(m["trace_path"], repo).exists():
                tp = ec.resolve(m["trace_path"], repo)
                if tp.parent.name == "test":
                    raise SystemExit(f"refusing: negative trace under traces/test: {ec.rel(tp, repo)}")
                neg_jobs.append(ec.Job(split=r["split"], video_id=r["video_id"], trace_path=m["trace_path"],
                                       release_ts=int(r["release_timestamp"]), lengths=ec.excerpt_lengths(r)))
    if mode == "practice" and any(j.split == "test" for j in jobs + neg_jobs):
        raise SystemExit("refusing: practice mode must not read Test traces")
    log.info("%s run: %d %s streams with a trace (of %d selected), %d report-half negatives",
             mode, len(jobs), split, len(stream_rows), len(neg_jobs))
    cache_dir = out_dir / "search_cache"
    ctx = dict(args=args, repo=repo, out_dir=out_dir, log=log, mode=mode, split=split, stream_rows=stream_rows,
               manifest=manifest, tau_table=tau_table, lat=lat, ref=ref, decision_utc=decision_utc,
               blinding=blinding, commits=commits, thr_path=thr_path, lat_path=lat_path, t_run=t_run,
               jobs=jobs, neg_jobs=neg_jobs, started=started, frozen=frozen, frozen_path=frozen_path)
    # Phase 1: the Test (or practice) streams, with whatever negatives are cached already, so a
    # slow M3 search never delays gate.md. Phase 2: the remaining negatives, then write again.
    records, timing = ec.run_searches(jobs, cache_dir, ref, args.ref, args.workers, log, repo)
    neg_records, _ = ec.run_searches(neg_jobs, cache_dir, ref, args.ref, args.workers, log, repo, search=False)
    n_left = len(neg_jobs) - len(neg_records)
    write_report(ctx, records, neg_records, timing, None, n_left)
    if n_left:
        log.info("searching %d report-half negative(s) for M3", n_left)
        neg_records, timing_neg = ec.run_searches(neg_jobs, cache_dir, ref, args.ref, args.workers, log, repo)
        write_report(ctx, records, neg_records, timing, timing_neg, len(neg_jobs) - len(neg_records))
    return 0


def write_report(ctx: dict, records: dict, neg_recs: dict, timing: dict, timing_neg, m3_pending: int) -> None:
    args, repo, out_dir, log, mode, split = (ctx[k] for k in ("args", "repo", "out_dir", "log", "mode", "split"))
    stream_rows, manifest, tau_table, lat, ref = (ctx[k] for k in ("stream_rows", "manifest", "tau_table", "lat", "ref"))
    decision_utc, blinding, commits, thr_path, lat_path = (ctx[k] for k in ("decision_utc", "blinding", "commits",
                                                                            "thr_path", "lat_path"))
    jobs, neg_jobs, t_run = ctx["jobs"], ctx["neg_jobs"], ctx["t_run"]
    csv_rows, per_stream = build_rows(mode, stream_rows, manifest, records, tau_table, lat, ref, decision_utc, repo)
    flow = flow_table(stream_rows, manifest, per_stream, repo)
    m2 = m2_cells(per_stream)
    frozen, frozen_path, started = ctx.get("frozen"), ctx.get("frozen_path"), ctx.get("started")
    freeze_now = False
    if frozen is not None:                                   # reading 10: never recomputed
        health, gate = frozen["health"], frozen["gate"]
        gate_freeze = {"status": "frozen", "file": ec.rel(frozen_path, repo),
                       "frozen_ist": frozen.get("frozen_ist"), "run_started_ist": frozen.get("run_started_ist")}
    else:
        health = ec.harvest_health(manifest, repo / "logs", decision_utc, repo)
        gate = gate_eval(per_stream, health, commits, decision_utc, mode)
        gate_freeze = None
        if mode == "real":
            unsearched = gate_unsearched(stream_rows, manifest, per_stream, decision_utc)
            freeze_now = started is not None and started >= decision_utc + FREEZE_GRACE and not unsearched
            if freeze_now:
                status = "frozen this run"
            elif started is None or started < decision_utc:
                status = "this run started before the decision time"
            elif started < decision_utc + FREEZE_GRACE:
                status = (f"this run started less than {int(FREEZE_GRACE.total_seconds() // 60)} min after "
                          "the decision time")
            else:
                status = (f"{len(unsearched)} stream(s) harvested by the decision time have no search yet: "
                          + ", ".join(unsearched))
            gate_freeze = {"status": status, "file": ec.rel(frozen_path, repo),
                           "run_started_ist": None if started is None else started.isoformat(timespec="seconds")}
    neg_records = [neg_recs[j.key] for j in neg_jobs if j.key in neg_recs]
    m3 = m3_cells(neg_records, tau_table)

    all_recs = [records[j.key] for j in jobs if j.key in records]
    secs = [rec["search_seconds_sum"] for rec in all_recs]
    n_len = sum(len({s["length_min"] for s in rec["searches"]}) for rec in all_recs)
    per_len = (sum(secs) / n_len) if n_len else None
    w = timing["workers"]
    eff = None
    if timing.get("streams_searched") and timing["wall_seconds"] > 0:
        run_secs = sum(timing["per_stream_search_seconds"].values())
        eff = run_secs / (timing["wall_seconds"] * w)
    timing_out = {
        **{k: v for k, v in timing.items() if k != "per_stream_search_seconds"},
        "search_seconds_per_stream_all_cached": None if not secs else {
            "n": len(secs), "mean": round(sum(secs) / len(secs), 1), "min": round(min(secs), 1), "max": round(max(secs), 1)},
        "search_seconds_per_excerpt_length": None if per_len is None else round(per_len, 1),
        "pool_efficiency_this_run": None if eff is None else round(eff, 3),
        "estimate_150_test_streams": None if per_len is None else {
            "excerpt_lengths": TEST_LENGTH_TOTAL,
            "single_worker_hours": round(per_len * TEST_LENGTH_TOTAL / 3600, 2),
            "wall_hours_with_workers": round(per_len * TEST_LENGTH_TOTAL / (w * (eff or 1.0)) / 3600, 2),
            "workers": w},
        "negatives": None if timing_neg is None else {k: v for k, v in timing_neg.items()
                                                      if k != "per_stream_search_seconds"},
        "run_seconds": round(time.time() - t_run, 1),
    }
    now = _now()
    files = {k: ec.rel(out_dir / v, repo) for k, v in (("csv", "test_results.csv"), ("flow", "flow_table.md"),
             ("gate", "gate.md"), ("m3_md", "false_matches.md"), ("m3_json", "false_matches.json"),
             ("json", "evaluation.json"))}
    files["threshold"] = ec.rel(thr_path, repo)
    files["latency"] = ec.rel(lat_path, repo) if lat_path.exists() else None
    latency_line = (f"L = {lat['L_s']} s ({lat['status']}; `{files['latency']}`). T* = release time in IST + L + D."
                    if files["latency"] else f"L = 0 s ({lat['status']}). T* = release time in IST + L + D.")
    d = {"written_ist": ec.now_ist(), "mode": mode, "split": split,
         "split_label": "PRACTICE, Calibration split" if mode == "practice" else "Test split",
         "decision_time": args.decision_time, "after_decision_time": now >= decision_utc,
         "protocol": "PROTOCOL.md sections 6, 8, 9, 11, 14, 15, 16", "primary_extractor": PRIMARY,
         "latency": lat, "latency_line": latency_line, "tau_available": tau_table is not None,
         "blinding": blinding, "commits": commits, "files": files,
         "n_selected": len(stream_rows), "n_with_trace": len(jobs),
         "flow": flow, "m2": m2, "gate": gate, "health": health, "gate_freeze": gate_freeze,
         "m3_counts": {"real": sum(1 for r in neg_records if r["split"] == "negative"),
                       "SYNTHETIC": sum(1 for r in neg_records if r["split"] == "negative_synthetic"),
                       "harvested_not_yet_searched": m3_pending},
         "m3": m3, "timing": timing_out}
    write_csv(out_dir / "test_results.csv", csv_rows, repo)
    write_flow_md(out_dir / "flow_table.md", d, repo)
    if frozen is not None:
        gate_path = out_dir / "gate.md"
        try:
            same = gate_path.read_text(encoding="utf-8") == frozen["gate_md"]
        except OSError:
            same = False
        if not same:
            log.warning("gate.md differs from the frozen gate; restoring it from %s", ec.rel(frozen_path, repo))
            ec.write_text_atomic(gate_path, frozen["gate_md"], repo)
    else:
        gate_text = write_gate_md(out_dir / "gate.md", d, per_stream, repo)
        if freeze_now:
            fz = {"note": "PROTOCOL.md section 15 gate, frozen by the first real evaluate.py run that started after "
                          "the decision time with every Test stream harvested by then searched. Later runs reuse "
                          "it and never recompute or overwrite gate.md (evaluate.py reading 10).",
                  "decision_time": args.decision_time, "decision_utc": _utc_iso(decision_utc),
                  "run_started_ist": gate_freeze["run_started_ist"], "frozen_ist": d["written_ist"],
                  "gate": gate, "health": health, "gate_md": gate_text}
            ec.write_json_atomic(frozen_path, fz, repo)
            ctx["frozen"] = fz
            log.info("gate frozen: %s", ec.rel(frozen_path, repo))
    write_m3_md(out_dir / "false_matches.md", d, repo)
    ec.write_json_atomic(out_dir / "false_matches.json", {"written_ist": d["written_ist"], "mode": mode,
                                                         "counts": d["m3_counts"], "cells": m3}, repo)
    ec.write_json_atomic(out_dir / "evaluation.json", d, repo)
    p = gate["by_extractor"][PRIMARY]
    log.info("%s: N=%d processed, %d lock (S1 D=0: %d), health %s, gate %s; wrote %s",
             mode, gate["N_processed"], p["n_streams_with_lock"], p["n_streams_with_lock_D0"],
             "ok" if health["ok"] else "NOT ok", "MET" if gate["gate_met"] else "not met", ", ".join(
                 v for k, v in files.items() if k not in ("threshold", "latency") and v))
    log.info("timing: %s", json.dumps(timing_out))


if __name__ == "__main__":
    raise SystemExit(main())
