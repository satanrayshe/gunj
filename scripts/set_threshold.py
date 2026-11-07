#!/usr/bin/env python
"""Set the confidence threshold tau from the threshold-half negatives (PROTOCOL.md section 10).

For every threshold-half negative (split "negative", half "threshold": 25
real foreign streams; split "negative_synthetic", half "threshold": 20
SYNTHETIC items), every extractor (v2, v1, naive), every pre-registered
excerpt length the stream is long enough for (10, 20, 40, 60 min;
excerpt_lengths_min in data/splits.csv) and every scope (full, +-90 days,
+-1 day), the excerpt trace is searched whatever its hum flag (a forced
search). For each cell (extractor, length, scope) with n trials:

    m = floor(0.01 n);  tau = (m+1)-th largest confidence + 0.0001

so at most 1% of the threshold-half trials reach tau (with n < 100, tau sits
just above the largest negative confidence).

Writes results/threshold.json (every threshold-half confidence and tau per
cell) and results/threshold.md, and puts the primary extractor's (v2) tau
into gunj/config.json with confidence_threshold_calibrated: true.

Refuses to run (exit 2, listing them) if any threshold-half row of
data/harvest_manifest.csv is not "done" or "failed", or a done row's trace
file is missing. --cache-only searches whatever threshold-half traces exist
and fills the search cache without setting tau, so the slow searches can run
while the harvester is still working; --wait does that every --poll-seconds
until every row is final and then sets tau (exit 3 if a manifest row is
"blocked", since the harvest has then stopped). Searches are cached per stream in
results/search_cache/<split>/<video_id>.json (scripts/eval_common.py) and
skipped on a re-run unless the trace file's SHA-256 changed.

Readings chosen where PROTOCOL leaves room:
1. Real and SYNTHETIC threshold-half trials are pooled into one n per cell,
   as section 10 counts them ("25 real negatives ... plus 20 SYNTHETIC").
2. Scope centre: the release time in IST (data/splits.csv
   release_timestamp). For SYNTHETIC negatives that column holds the pseudo
   claimed time drawn by scripts/make_splits.py (section 5).
3. "A negative with no runner-up counts as no verdict": such a trial (and
   one whose search cannot run, e.g. a constant trace) counts in n with no
   confidence and ranks below every confidence. If fewer than m+1 trials in
   a cell have a confidence, tau for that cell is null (undefined), and
   evaluate.py then gives no verdict in that cell.
4. A failed download gives no trial. A "skipped" row (court-like title;
   the harvester never retries it) is final like "failed" and is treated
   the same way; it is listed.
5. A length listed in excerpt_lengths_min whose trace is absent from the
   trace file gives no trial; it is listed.
6. gunj/config.json: PROTOCOL says the primary extractor's tau goes there
   but not in what form, and 'gunj date' reads a single number. The 12 v2
   cells go in confidence_threshold_cells, and confidence_threshold is the
   largest of them, so whichever cell a CLI query falls in, its threshold is
   at least that cell's tau. ('gunj date' itself extracts with v1; that is
   unchanged here.)
7. Once results/threshold.json is in a git commit, the script refuses to
   overwrite it (the value is pre-registered); --force overrides that and
   any such re-run is a deviation for PROTOCOL section 19.

Usage (from the repo root):
  .venv\\Scripts\\python.exe scripts\\set_threshold.py --cache-only   (any time)
  .venv\\Scripts\\python.exe scripts\\set_threshold.py --wait         (start now; sets tau when ready)
  .venv\\Scripts\\python.exe scripts\\set_threshold.py                (all 45 rows done/failed)
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import eval_common as ec                 # noqa: E402
from eval_common import EXTRACTORS, LENGTHS, PRIMARY, SCOPES, SCOPE_LABEL  # noqa: E402
from gunj import reference as gref       # noqa: E402

RULE = ("m = floor(0.01 n); tau = (m+1)-th largest threshold-half confidence + 0.0001 "
        "(PROTOCOL.md section 10). Trials with no runner-up count in n as no verdict.")
FINAL = ("done", "failed", "skipped")


def failed_is_final(m: dict) -> bool:
    """A failed row is final only if the harvester will never retry it: not retryable, or
    retryable with MAX_ATTEMPTS used up for a reason other than the laptop's network being
    down (harvest.plan() retries network-down rows without limit)."""
    if m.get("retryable") != "True":
        return True
    if ec.hv.NETWORK_DOWN_RE.search(m.get("reason") or ""):
        return False
    return int(m.get("attempts") or 0) >= ec.hv.MAX_ATTEMPTS


def threshold_rows(splits: list) -> list:
    return [r for r in splits if r["split"] in ec.NEGATIVE_SPLITS and r.get("half") == "threshold"]


def readiness(rows: list, manifest: dict, repo: Path) -> tuple:
    """(not_ready, done_rows, excluded) for the threshold-half rows."""
    not_ready, done, excluded = [], [], []
    for r in rows:
        m = manifest.get(r["video_id"])
        st = None if m is None else m.get("status")
        if st not in FINAL or (st == "failed" and not failed_is_final(m)):
            not_ready.append({"video_id": r["video_id"], "split": r["split"],
                              "status": st or "not in manifest"})
        elif st == "done":
            tp = m.get("trace_path") or ""
            if not tp or not ec.resolve(tp, repo).exists():
                not_ready.append({"video_id": r["video_id"], "split": r["split"],
                                  "status": f"done but trace file missing ({tp or 'no trace_path'})"})
            else:
                done.append((r, m))
        else:
            excluded.append({"video_id": r["video_id"], "split": r["split"], "status": st,
                             "retryable": m.get("retryable", ""),
                             "reason": m.get("reason", "")})
    return not_ready, done, excluded


def kind_of(r: dict) -> str:
    return "SYNTHETIC" if r["split"] == "negative_synthetic" else "real"


def build_cells(done: list, records: dict) -> tuple:
    cells, missing = {}, []
    for ext in EXTRACTORS:
        for m in LENGTHS:
            for scope in SCOPES:
                trials = []
                for r, _ in done:
                    if m not in ec.excerpt_lengths(r):
                        continue
                    rec = records[f"{r['split']}/{r['video_id']}"]
                    s = ec.search_of(rec, ext, m, scope)
                    if s is None:
                        if scope == "full":
                            missing.append({"video_id": r["video_id"], "extractor": ext, "length_min": m})
                        continue
                    top = s["candidates"][0] if s.get("candidates") else None
                    ru = s.get("runner_up")
                    trials.append({
                        "video_id": r["video_id"], "kind": kind_of(r),
                        "confidence": s.get("confidence"),
                        "r1": None if top is None else top["r"], "r2": None if ru is None else ru["r"],
                        "top_start_ist": None if top is None else top["start_ist"],
                        "hum_flag": bool(ec.trace_hum(rec, ext, f"x{m}").get("present", False)),
                        "n_positions": s.get("n_positions"), "search_error": s.get("error")})
                rule = ec.tau_rule([t["confidence"] for t in trials])
                trials.sort(key=lambda t: (t["confidence"] is None, -(t["confidence"] or 0.0), t["video_id"]))
                cell = {**rule,
                        "n_real": sum(1 for t in trials if t["kind"] == "real"),
                        "n_synthetic": sum(1 for t in trials if t["kind"] == "SYNTHETIC"),
                        "trials": trials}
                cells.setdefault(ext, {}).setdefault(str(m), {})[scope] = cell
    return cells, missing


def config_update(path: Path, cells: dict, repo: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    taus = {str(m): {s: cells[PRIMARY][str(m)][s]["tau"] for s in SCOPES} for m in LENGTHS}
    finite = [t for by in taus.values() for t in by.values() if t is not None]
    if not finite:
        return {"updated": False, "reason": f"no {PRIMARY} cell has a tau"}
    new = {
        "_note": ("Settings read by 'gunj date'. confidence_threshold is CALIBRATED on the threshold half "
                  "of the negative set (PROTOCOL.md section 10; results/threshold.json, written by "
                  f"scripts/set_threshold.py). The {PRIMARY} (primary extractor) tau of each cell is in "
                  "confidence_threshold_cells (excerpt minutes -> scope); confidence_threshold is the largest "
                  "of them. The former placeholder 0.1 is not used for any result."),
        "confidence_threshold": max(finite),
        "confidence_threshold_calibrated": True,
        "confidence_threshold_extractor": PRIMARY,
        "confidence_threshold_cells": taus,
        "confidence_threshold_rule": RULE,
        "confidence_threshold_source": "results/threshold.json",
    }
    for k, v in cfg.items():
        if k not in new and not k.startswith("_"):
            new[k] = v
    ec.write_json_atomic(path, new, repo, indent=2)
    return {"updated": True, "path": ec.rel(path, repo), "confidence_threshold": new["confidence_threshold"],
            "cells": taus}


def write_md(d: dict, path: Path, repo: Path) -> None:
    L = ["# Confidence threshold tau (threshold-half negatives)\n",
         f"Generated by `scripts/set_threshold.py` at {d['written_ist']}. Every number is copied from "
         "`results/threshold.json`, written by the same run. PROTOCOL.md section 10.\n",
         f"- Rule: {RULE}",
         f"- Threshold-half negatives with a trace: {d['n_negatives']['real_done']} real, "
         f"{d['n_negatives']['synthetic_done']} SYNTHETIC. Excluded (no trace): {len(d['excluded'])}.",
         f"- Primary extractor: {PRIMARY}. Search forced on every negative, whatever its hum flag.", ""]
    for ext in EXTRACTORS:
        L.append(f"## {ext}{' (primary)' if ext == PRIMARY else ''}\n")
        L.append("| excerpt | " + " | ".join(f"{SCOPE_LABEL[s]}: tau (n, m, largest)" for s in SCOPES) + " |")
        L.append("|---|" + "---|" * len(SCOPES))
        for m in LENGTHS:
            cells = []
            for s in SCOPES:
                c = d["cells"][ext][str(m)][s]
                if c["tau"] is None:
                    cells.append(f"undefined (n {c['n']})")
                else:
                    top = c["trials"][0]["confidence"] if c["trials"] else None
                    cells.append(f"{c['tau']:.4f} ({c['n']}, {c['m']}, {top:.4f})")
            L.append(f"| {m} min | " + " | ".join(cells) + " |")
        L.append("")
    if d["excluded"]:
        L.append("## Excluded threshold-half rows\n")
        for e in d["excluded"]:
            L.append(f"- {e['video_id']} ({e['split']}): {e['status']}, {e.get('reason') or ''}")
        L.append("")
    if d["missing_traces"]:
        L.append("## Listed lengths with no trace\n")
        for e in d["missing_traces"]:
            L.append(f"- {e['video_id']} {e['extractor']} {e['length_min']} min")
        L.append("")
    L.append("SYNTHETIC negatives are generated by gunj/synthetic.py (PROTOCOL section 5); they are labelled "
             "SYNTHETIC in `results/threshold.json`.")
    ec.write_text_atomic(path, "\n".join(L) + "\n", repo)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Set tau from the threshold-half negatives (PROTOCOL section 10)")
    ap.add_argument("--manifest", default="data/harvest_manifest.csv")
    ap.add_argument("--splits-csv", default="data/splits.csv")
    ap.add_argument("--out", default="results/threshold.json")
    ap.add_argument("--config", default="gunj/config.json")
    ap.add_argument("--no-config", action="store_true", help="do not update gunj/config.json")
    ap.add_argument("--cache-dir", default="results/search_cache")
    ap.add_argument("--ref", default=None, help="reference folder (default: gunj.reference default)")
    ap.add_argument("--workers", type=int, default=None, help="default max(1, min(4, cpu_count - 2))")
    ap.add_argument("--cache-only", action="store_true",
                    help="search the threshold-half traces that exist; do not set tau")
    ap.add_argument("--wait", action="store_true",
                    help="keep caching new threshold-half traces until every row is done/failed, then set tau "
                         "(exit 3 if the harvest is blocked)")
    ap.add_argument("--poll-seconds", type=int, default=120)
    ap.add_argument("--force", action="store_true", help="overwrite a committed threshold.json (a deviation)")
    ap.add_argument("--accept-excluded", action="store_true",
                    help="set tau even though some real threshold-half rows used up their retries "
                         "(record the decision in PROTOCOL section 19)")
    ap.add_argument("--log", default="logs/set_threshold.log")
    return ap


def main(argv=None, repo: Path = ec.REPO) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(repo)
    log = ec.setup_logging(ec.resolve(args.log, repo), "set_threshold")
    lock = ec.hv.RunLock(ec.resolve(args.log, repo).with_suffix(".lock"))
    if not lock.acquire():
        log.error("another set_threshold run holds the lock; exiting")
        return 4
    try:
        return _run(args, repo, log)
    finally:
        lock.release()


def _jobs(done: list) -> list:
    return [ec.Job(split=r["split"], video_id=r["video_id"], trace_path=m["trace_path"],
                   release_ts=int(r["release_timestamp"]), lengths=ec.excerpt_lengths(r)) for r, m in done]


def _run(args, repo: Path, log) -> int:
    t0 = time.time()
    splits_path = ec.resolve(args.splits_csv, repo)
    rows = threshold_rows(ec.read_splits(splits_path))
    out_path = ec.resolve(args.out, repo)
    ref = None
    while True:
        manifest = ec.read_manifest(ec.resolve(args.manifest, repo))
        not_ready, done, excluded = readiness(rows, manifest, repo)
        log.info("threshold-half rows: %d (%d real, %d SYNTHETIC); done with trace %d, excluded %d, not ready %d",
                 len(rows), sum(1 for r in rows if r["split"] == "negative"),
                 sum(1 for r in rows if r["split"] == "negative_synthetic"), len(done), len(excluded),
                 len(not_ready))
        if not (args.wait and not_ready):
            break
        blocked = [v for v, m in manifest.items() if m.get("status") == "blocked"]
        if blocked:
            log.error("the harvest is blocked (%s); the remaining threshold-half rows will not finish. "
                      "Not setting tau.", ", ".join(blocked))
            return 3
        ref = ref or gref.load(args.ref)
        ec.run_searches(_jobs(done), ec.resolve(args.cache_dir, repo), ref, args.ref, args.workers, log, repo)
        log.info("--wait: %d row(s) not ready yet (%s); checking again in %d s", len(not_ready),
                 ", ".join(x["video_id"] for x in not_ready[:8]) + (", ..." if len(not_ready) > 8 else ""),
                 args.poll_seconds)
        time.sleep(args.poll_seconds)
    if not args.cache_only:
        if not_ready:
            log.error("REFUSING to set tau: %d threshold-half row(s) are not done or failed:", len(not_ready))
            for x in not_ready:
                log.error("  %s (%s): %s", x["video_id"], x["split"], x["status"])
            return 2
        worn_out = [x for x in excluded if x["split"] == "negative" and x.get("retryable") == "True"]
        if worn_out and not args.accept_excluded:
            log.error("REFUSING to set tau: %d real threshold-half negative(s) used up their retries "
                      "(not a permanent failure). Retry them, or rerun with --accept-excluded and record "
                      "the decision in PROTOCOL section 19:", len(worn_out))
            for x in worn_out:
                log.error("  %s: %s", x["video_id"], x.get("reason", ""))
            return 2
        if not args.force and ec.is_under(out_path, repo):
            relp = ec.rel(out_path, repo)
            if ec._git(["cat-file", "-e", f"HEAD:{relp}"], repo).returncode == 0:
                log.error("REFUSING: %s is already committed (pre-registered). Use --force only for a "
                          "recorded deviation (PROTOCOL section 19).", relp)
                return 2
    ref = ref or gref.load(args.ref)
    lost = []
    for attempt in range(3):
        records, timing = ec.run_searches(_jobs(done), ec.resolve(args.cache_dir, repo), ref, args.ref,
                                          args.workers, log, repo)
        lost = [r["video_id"] for r, _ in done if f"{r['split']}/{r['video_id']}" not in records]
        if not lost or args.cache_only:
            break
        log.warning("no search record yet for %s (a transient read error is not cached); retrying in 30 s",
                    ", ".join(lost))
        time.sleep(30)
    if args.cache_only:
        log.info("cache-only run: %d of %d threshold-half streams searched and cached; %d not ready. tau not set.",
                 len(records), len(rows), len(not_ready))
        return 0
    if lost:
        log.error("REFUSING to set tau: still no search record for %s after 3 tries.", ", ".join(lost))
        return 2
    cells, missing = build_cells(done, records)
    negs = []
    for r in rows:
        m = manifest.get(r["video_id"], {})
        rec = records.get(f"{r['split']}/{r['video_id']}")
        negs.append({"video_id": r["video_id"], "split": r["split"], "kind": kind_of(r),
                     "country": r.get("country"), "status": m.get("status"), "reason": m.get("reason") or "",
                     "trace_path": m.get("trace_path") or None,
                     "trace_sha256": None if rec is None else rec["trace_sha256"],
                     "scope_centre_ist": str(gref.unix_to_ist(int(r["release_timestamp"]))),
                     "excerpt_lengths_min": ec.excerpt_lengths(r),
                     "synthetic_seed": r.get("synthetic_seed") or None})
    out = {
        "written_ist": ec.now_ist(), "script": "scripts/set_threshold.py", "protocol": "PROTOCOL.md section 10",
        "rule": RULE, "primary_extractor": PRIMARY, "extractors": list(EXTRACTORS),
        "lengths_min": list(LENGTHS), "scopes": {s: SCOPE_LABEL[s] for s in SCOPES},
        "scope_centre": "release time in IST (pseudo claimed time for SYNTHETIC negatives), data/splits.csv",
        "inputs": {"splits_csv": ec.rel(splits_path, repo),
                   "splits_csv_sha256": hashlib.sha256(splits_path.read_bytes()).hexdigest(),
                   "manifest": ec.rel(ec.resolve(args.manifest, repo), repo),
                   "reference": ec.ref_identity(ref), "matcher_sha256": ec.matcher_sha(repo)},
        "n_negatives": {"rows": len(rows),
                        "real_done": sum(1 for r, _ in done if r["split"] == "negative"),
                        "synthetic_done": sum(1 for r, _ in done if r["split"] == "negative_synthetic")},
        "negatives": negs, "excluded": excluded, "missing_traces": missing,
        "cells": cells,
        "note": "SYNTHETIC negatives are generated (gunj/synthetic.py) and labelled SYNTHETIC. No Test or "
                "Calibration trace was read.",
    }
    if args.no_config:
        out["config_json"] = {"updated": False, "reason": "--no-config"}
    else:
        out["config_json"] = config_update(ec.resolve(args.config, repo), cells, repo)
    out["timing"] = {**{k: v for k, v in timing.items() if k != "per_stream_search_seconds"},
                     "run_seconds": round(time.time() - t0, 1)}
    ec.write_json_atomic(out_path, out, repo)
    write_md(out, out_path.with_suffix(".md"), repo)
    for ext in EXTRACTORS:
        log.info("%s tau: %s", ext, json.dumps({m: {s: (None if c["tau"] is None else round(c["tau"], 5))
                                                   for s, c in by.items()} for m, by in cells[ext].items()}))
    log.info("wrote %s and %s; config: %s", ec.rel(out_path, repo), ec.rel(out_path.with_suffix(".md"), repo),
             out["config_json"].get("updated"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
