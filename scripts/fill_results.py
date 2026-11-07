#!/usr/bin/env python
"""Fill the results blocks of README.md and submission/UNSTOP-ANSWERS.md.

Every number this script writes is read at run time from a file in the repo,
and that file is named next to the number. No number is typed in by hand.

A block is the text between two markers:

    <!-- results:start -->  ...  <!-- results:end -->

A block may stand on its own lines or sit inside one line (an inline value).
Only the text between the markers is replaced, so the script can be re-run at
any time. With the same inputs it writes the same text, and it leaves a file
untouched if no block in it changed. Each file is written to <name>.tmp and
then renamed. An unknown block name, a start marker without its end marker,
or a local absolute path in the generated text stops the run (exit 2) before
anything is written.

Test numbers come from scripts/evaluate.py: results/evaluation.json,
results/test_results.csv and results/gate_frozen.json (results/gate.md,
results/flow_table.md and results/false_matches.md hold the same numbers for
people). They are shown only once results/gate_frozen.json exists, that is,
once the PROTOCOL section 15 gate of 8 Nov 2026 14:00 IST has been frozen.
Until then every Test value reads "[pending: gate 8 Nov 14:00]".
--interim shows the current Test numbers labelled INTERIM, for checking only;
an INTERIM fill must not be committed or published.

The script reads only small JSON, CSV and Markdown files. It never loads the
reference series or any trace, and it starts no worker processes.

Usage (from the repo root):
  .venv\\Scripts\\python.exe scripts\\fill_results.py              fill both files
  .venv\\Scripts\\python.exe scripts\\fill_results.py --dry-run    report what would change
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import argparse
import csv
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
Z95 = 1.959964                                    # PROTOCOL section 13
PENDING = "[pending: gate 8 Nov 14:00]"
EXTRACTORS = ("v2", "v1", "naive")                # scripts/eval_common.py
PRIMARY = "v2"
LENGTHS = (10, 20, 40, 60)
SCOPES = ("full", "90d", "1d")
SCOPE_LABEL = {"full": "full span", "90d": "±90 days", "1d": "±1 day"}
VERIFIED = "verified by metadata step"            # data/splits.csv official_status (scripts/make_splits.py)
DEFAULT_TARGETS = ("README.md", "submission/UNSTOP-ANSWERS.md")
MARKER_RE = re.compile(r"<!--\s*([a-z0-9][a-z0-9-]*):start\s*-->")
ABS_PATH_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|Users[\\/]|AppData")
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
FILE_ROMAN = ("I", "II", "III", "IV", "V")


class FillError(Exception):
    """Stops the run before any file is written."""


# ----------------------------------------------------------------- numbers

def wilson(k: int, n: int, z: float = Z95):
    """Wilson 95% interval, the formula of PROTOCOL section 13."""
    if n <= 0:
        return None, None
    p = k / n
    den = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def fmt_rate(k: int, n: int, ci=None) -> str:
    if not n:
        return "n/a (N = 0)"
    lo, hi = ci if ci is not None else wilson(k, n)
    return f"{k}/{n} ({100 * k / n:.1f}%, 95% CI {100 * lo:.1f} to {100 * hi:.1f}%)"


def fmt_rate_short(k: int, n: int, ci=None) -> str:
    if not n:
        return "n/a (N = 0)"
    lo, hi = ci if ci is not None else wilson(k, n)
    return f"{k}/{n} ({100 * k / n:.1f}%; CI {100 * lo:.1f} to {100 * hi:.1f}%)"


def rate_of(d: dict | None, short: bool = False) -> str:
    """Format a rate dict written by scripts/evaluate.py (k, n, rate, wilson95)."""
    if not d or not d.get("n"):
        return "n/a (N = 0)"
    f = fmt_rate_short if short else fmt_rate
    return f(int(d["k"]), int(d["n"]), d.get("wilson95"))


def num(x) -> str:
    return f"{int(x):,}"


def sci(x: float, digits: int = 4) -> str:
    """7.258e-07 -> '7.258 × 10^-7'."""
    if x == 0:
        return "0"
    e = math.floor(math.log10(abs(x)))
    return f"{x / 10 ** e:.{digits - 1}f} × 10^{e}"


def day_text(s: str) -> str:
    """'2020-12-20 17:21:01' or an ISO time -> '20 Dec 2020'."""
    y, m, d = int(s[0:4]), int(s[5:7]), int(s[8:10])
    return f"{d} {MONTHS[m - 1]} {y}"


def ist_text(iso: str | None) -> str:
    """'2026-11-08T14:12:03+05:30' -> '8 Nov 2026, 14:12 IST'."""
    if not iso:
        return "time not recorded"
    if not iso.endswith("+05:30"):
        return iso
    return f"{day_text(iso)}, {iso[11:16]} IST"


def secs_text(s: float) -> str:
    s = float(s)
    return f"{int(s)} s" if s == int(s) else f"{s:g} s"


# ----------------------------------------------------------------- sources

class Sources:
    """Reads repo files once; Test files come from --eval-dir (default results/)."""

    def __init__(self, repo: Path, eval_dir: Path, interim: bool, allow_practice: bool):
        self.repo = repo
        self.results = repo / "results"
        self.eval_dir = eval_dir
        self.interim = interim
        self.allow_practice = allow_practice
        self._cache: dict = {}
        self.warnings: list[str] = []

    def label(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.repo.resolve()).as_posix()
        except ValueError:
            return f"<outside the repo>/{path.name}"

    def _read(self, path: Path, kind: str):
        key = (str(path), kind)
        if key in self._cache:
            return self._cache[key]
        if not path.exists():
            val = None
        elif kind == "json":
            try:
                val = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as e:
                raise FillError(f"cannot parse {self.label(path)}: {e}") from e
        else:
            with path.open(encoding="utf-8", newline="") as fh:
                val = list(csv.DictReader(fh))
        self._cache[key] = val
        return val

    def json(self, rel: str):
        return self._read(self.repo / rel, "json")

    def csv(self, rel: str):
        return self._read(self.repo / rel, "csv")

    def eval_json(self, name: str):
        return self._read(self.eval_dir / name, "json")

    def eval_csv(self, name: str):
        return self._read(self.eval_dir / name, "csv")

    def need(self, rel: str):
        v = self.json(rel)
        if v is None:
            raise FillError(f"{rel} is missing; it was written before any Test search and should be in the repo")
        return v


class TestState:
    """What scripts/evaluate.py has written, and whether it may be shown."""

    def __init__(self, src: Sources):
        self.src = src
        self.ev = src.eval_json("evaluation.json")
        self.frozen = src.eval_json("gate_frozen.json")
        self.rows = src.eval_csv("test_results.csv")
        self.mode = None
        if self.ev is not None:
            self.mode = self.ev.get("mode")
            if self.mode != "real" and not src.allow_practice:
                src.warnings.append(f"evaluation.json has mode {self.mode!r}, not 'real'; Test values stay pending")
                self.ev = None
        if self.frozen is not None and self.mode == "practice":
            raise FillError("gate_frozen.json next to a practice evaluation.json; refusing")
        self.practice = self.mode == "practice"
        self.show = self.ev is not None and (self.frozen is not None or src.interim)
        if self.show and self.rows is None:
            raise FillError("evaluation.json exists but test_results.csv does not; re-run when scripts/evaluate.py has finished")
        if self.frozen is not None:
            self.tag = ""
        elif self.practice:
            self.tag = "PRACTICE (Calibration split, not Test)"
        else:
            self.tag = "INTERIM"
        self.streams: dict = {}
        self.cells: dict = defaultdict(dict)
        if self.show:
            self._index_rows()
            self._check_consistency()

    def _index_rows(self) -> None:
        for r in self.rows:
            if r.get("mode") != self.mode:
                continue
            vid = r["video_id"]
            self.streams.setdefault(vid, r)
            if r.get("extractor") in EXTRACTORS and r.get("excerpt_min") and r.get("scope") in SCOPES:
                self.cells[(r["extractor"], int(r["excerpt_min"]), r["scope"])][vid] = r

    def _check_consistency(self) -> None:
        """test_results.csv and evaluation.json must come from the same evaluate.py run."""
        bad = []
        for ext in EXTRACTORS:
            for m in LENGTHS:
                for s in SCOPES:
                    d = self.ev["m2"][ext][str(m)][s]
                    rows = self.cells.get((ext, m, s), {})
                    k = sum(1 for r in rows.values() if r.get("hit_30s") == "True")
                    if d["n"] != len(rows) or d["top1_hit"]["k"] != k:
                        bad.append(f"{ext}/{m}/{s}: json {d['top1_hit']['k']}/{d['n']}, csv {k}/{len(rows)}")
        if bad:
            raise FillError("test_results.csv and evaluation.json disagree (written by different evaluate.py runs?): "
                            + "; ".join(bad[:4]) + ". Re-run when scripts/evaluate.py has finished.")

    # --- views
    def m2(self, ext=PRIMARY, m=60, scope="full") -> dict:
        return self.ev["m2"][ext][str(m)][scope]

    def m3(self, ext=PRIMARY, m=60, scope="full") -> dict:
        return self.ev["m3"][ext][str(m)][scope]

    def gate(self):
        if self.frozen is not None:
            return self.frozen["gate"], "results/gate_frozen.json", "frozen " + ist_text(self.frozen.get("frozen_ist"))
        if self.show:
            return self.ev["gate"], "results/evaluation.json", f"{self.tag}, not frozen"
        return None, None, None

    def m1(self, keep=lambda r: True):
        done = [r for r in self.streams.values()
                if r.get("harvest_status") == "done" and r.get("hum_present_v1_full") in ("True", "False") and keep(r)]
        return sum(1 for r in done if r["hum_present_v1_full"] == "True"), len(done)

    def hits(self, ext=PRIMARY, m=60, scope="full", keep=lambda r: True):
        rows = [r for r in self.cells.get((ext, m, scope), {}).values() if keep(r)]
        return sum(1 for r in rows if r.get("hit_30s") == "True"), len(rows)

    def paired(self, base: str, m=60, scope="full"):
        a, b = self.cells.get((PRIMARY, m, scope), {}), self.cells.get((base, m, scope), {})
        both = sorted(set(a) & set(b))
        hit = lambda r: r.get("hit_30s") == "True"
        out = {"n": len(both), "both": 0, "v2_only": 0, "base_only": 0, "neither": 0}
        for v in both:
            key = {(True, True): "both", (True, False): "v2_only", (False, True): "base_only",
                   (False, False): "neither"}[(hit(a[v]), hit(b[v]))]
            out[key] += 1
        return out


# ----------------------------------------------------------------- pre-Test facts

def ref_facts(src: Sources) -> dict:
    st = src.need("results/reference_stats.json")
    return {"t0": st["span"]["t0_ist"], "t1": st["span"]["t_end_ist"], "seconds": st["span"]["seconds"],
            "days": st["span"]["days"], "valid": st["valid_seconds"], "valid_pct": st["valid_pct"],
            "gaps10": st["gaps_gt10s"], "missing10": st["missing_in_gaps_gt10s_s"],
            "longest": st["longest_gaps"][0]}


def drift_facts(src: Sources) -> dict:
    ta = src.need("results/time_axis_check.json")
    per = ta["per_file"]
    names = [f"freq_{r}.csv" for r in FILE_ROMAN]
    last = [per[n]["last_window_shift_s"] for n in names]
    overl = [x["label_offset_from_overlap_s"] for x in ta["overlap_cross_check"]]
    agree = all(abs(float(x["block_shift_a_minus_b_s"]) - float(x["label_offset_from_overlap_s"])) <= 1
                for x in ta["overlap_cross_check"])
    ss = src.need("results/splits_summary.json")["splits"]["test"]
    return {"last": last, "max_last": max(last), "max_last_file": FILE_ROMAN[last.index(max(last))],
            "overlaps": overl, "agree": agree, "test_n": ss["n"], "gt30": ss["abs_label_drift_gt_30s"],
            "median_abs": ss["abs_label_drift_s"]["median"], "max_abs": ss["abs_label_drift_s"]["max"],
            "stable": ss["label_stable"]}


def latency_facts(src: Sources) -> dict:
    lat = src.need("results/latency.json")
    cal = src.need("results/calibration.json")
    by_ext = {e: cal["latency"][e]["counted"]["n"] for e in EXTRACTORS if e in cal["latency"]}
    hum = [s for s in cal["streams"] if s.get("harvest_status") == "done"]
    return {"L": lat["L_s"], "status": lat["status"], "n": lat["counted"]["n"],
            "range": lat["counted"]["range_s"], "iqr": lat["counted"]["iqr_s"],
            "holds": lat["counted"]["constant_latency_holds"], "harvested": cal["n_harvested"],
            "selected": cal["n_calibration_streams"], "by_ext": by_ext,
            "hum_k": sum(1 for s in hum if s.get("hum_present_v1_full") is True), "hum_n": len(hum)}


def tau_facts(src: Sources) -> dict:
    th = src.need("results/threshold.json")
    cells = th["cells"]
    best = None
    for m in LENGTHS:
        for s in SCOPES:
            for t in cells[PRIMARY][str(m)][s]["trials"]:
                if t.get("kind") == "real" and t.get("r1") is not None and (best is None or t["r1"] > best[0]):
                    best = (t["r1"], t.get("confidence"), m, s)
    return {"cells": cells, "real": th["n_negatives"]["real_done"], "syn": th["n_negatives"]["synthetic_done"],
            "excluded": len(th.get("excluded") or []), "best_real_r1": best}


def m4_facts(src: Sources) -> dict:
    rows = src.csv("results/uniqueness_table.csv")
    if rows is None:
        raise FillError("results/uniqueness_table.csv is missing")
    pn = src.need("results/probe_noise.json")["summary"]
    probe_mhz = 1000 * pn["sigma_eff_all_median_hz"]
    levels = sorted({float(r["sigma_mhz"]) for r in rows if float(r["sigma_mhz"]) > 0})
    level = min(levels, key=lambda x: abs(x - probe_mhz))
    out = {}
    for scope in SCOPES:
        for kind in ("white", "hann16"):
            sel = sorted((r for r in rows if r["scope"] == scope and r["noise_kind"] == kind
                          and float(r["sigma_mhz"]) == level), key=lambda r: int(r["length_min"]))
            first = next((r for r in sel if float(r["hit_rate"]) >= 0.9), None)
            out[(scope, kind)] = {"rows": sel, "first": first}
    return {"probe_mhz": probe_mhz, "level": level, "cells": out}


def dev_facts(src: Sources) -> dict:
    d = src.need("results/v2_tuning.json")
    chosen = next(c for c in d["candidates"] if c["id"] == d["chosen"])
    return {"n": len(d["dev_streams"]), "chosen": d["chosen"], "J": chosen["J"],
            "J_v1": d["baselines"]["v1"]["J"], "J_naive": d["baselines"]["naive"]["J"]}


def other_results(src: Sources, words: tuple) -> list[str]:
    return sorted(p.name for p in src.results.glob("*") if p.is_file() and any(w in p.name.lower() for w in words))


# ----------------------------------------------------------------- blocks

def b_span(src, ts) -> str:
    f = ref_facts(src)
    return f"{day_text(f['t0'])} to {day_text(f['t1'])}"


def b_span_end(src, ts) -> str:
    return day_text(ref_facts(src)["t1"])


def b_fm_bound(src, ts) -> str:
    w = src.need("results/splits_summary.json")["wilson_upper_95_if_zero_false_matches"]
    r, a = w["negative_report_60min"], w["all_report_60min"]
    return (f"{100 * r['upper']:.1f}% with the {r['n']} real report-half negatives at 60 min, and "
            f"{100 * a['upper']:.1f}% with the SYNTHETIC ones added (n = {a['n']})")


def b_splits(src, ts) -> str:
    ss = src.need("results/splits_summary.json")
    n = {k: v["n"] for k, v in ss["splits"].items()}
    return (f"seed {ss['seed']} into Dev ({n['dev']}), Calibration ({n['calibration']}) and Test ({n['test']}) "
            f"splits, plus {n['negative']} foreign 50 Hz streams and {n['negative_synthetic']} SYNTHETIC items")


def b_test_licences(src, ts) -> str:
    t = src.need("results/splits_summary.json")["splits"]["test"]
    lic = t["by_licence"]
    cc = sum(v for k, v in lic.items() if k.startswith("Creative Commons"))
    return (f"Of the {t['n']} Test streams, {cc} carry a Creative Commons BY licence and {t['n'] - cc} "
            f"the standard YouTube licence")


def b_ref_size(src, ts) -> str:
    f = ref_facts(src)
    return f"{num(f['seconds'])} seconds, {num(f['valid'])} of them valid ({f['valid_pct']:.2f}%)"


def b_drift_max(src, ts) -> str:
    return secs_text(drift_facts(src)["max_last"])


def b_test_selected(src, ts) -> str:
    return num(src.need("results/splits_summary.json")["splits"]["test"]["n"])


def b_random_guess(src, ts) -> str:
    g = src.need("results/splits_summary.json")["analytic_random_guess"]["full_60min"]
    return f"1 in {num(round(g['one_in']))}"


def b_latency(src, ts) -> str:
    f = latency_facts(src)
    return (f"L = {f['L']} s, from {f['n']} of {f['harvested']} Calibration streams "
            f"(o from {secs_text(f['range'][0])} to {secs_text(f['range'][1])})")


def b_cli_threshold(src, ts) -> str:
    cfg = src.need("gunj/config.json")
    return f"{cfg['confidence_threshold']:.4f}"


def b_status(src, ts) -> str:
    L = []
    gate, gsrc, when = ts.gate()
    if ts.frozen is not None:
        L.append(f"- Gate (PROTOCOL section 15, decision time 8 Nov 2026 14:00 IST): "
                 f"**{'met' if gate['gate_met'] else 'not met'}**, {when} (`{gsrc}`).")
    elif ts.show:
        L.append(f"- **{ts.tag}: the gate is not frozen yet. These Test numbers are for checking only; "
                 f"do not commit or publish this fill.**")
    else:
        L.append(f"- Test results: {PENDING}. The gate is decided on 8 Nov 2026 at 14:00 IST "
                 f"(moved from 7 Nov 22:00; see Deviations). Until `results/gate_frozen.json` exists, "
                 f"every Test value in this README reads {PENDING}.")
    if ts.show:
        ev = ts.ev
        L.append(f"- Test numbers are from `results/evaluation.json` and `results/test_results.csv`, written by "
                 f"`scripts/evaluate.py` at {ist_text(ev.get('written_ist'))}: {ev['n_with_trace']} of "
                 f"{ev['n_selected']} selected streams had a trace then. The harvest can still add streams; "
                 f"`scripts/fill_results.py` re-reads the files each time it runs.")
    L.append("- The reference, label-drift, latency, threshold, Dev and uniqueness results were measured before "
             "any Test search and do not change.")
    return "\n".join(L)


def _m2_grid(ts, key: str) -> list[str]:
    L = ["| excerpt | " + " | ".join(SCOPE_LABEL[s] for s in SCOPES) + " |", "|---|---|---|---|"]
    for m in LENGTHS:
        L.append(f"| {m} min | " + " | ".join(rate_of(ts.m2(PRIMARY, m, s)[key]) for s in SCOPES) + " |")
    return L


def _m4_table(src) -> list[str]:
    f = m4_facts(src)
    L = [f"Excerpts of the real reference, with SYNTHETIC noise added and no audio, searched like a recording. "
         f"Noise level {f['level']:g} mHz, the tested level nearest the 5 Nov probe's median equivalent "
         f"trace noise of {f['probe_mhz']:.1f} mHz (`results/probe_noise.json`). `white` is independent each second; "
         f"`hann16` is smoothed over 16 s like real extractor error, so it is the closer stand-in. Source: "
         f"`results/uniqueness_table.csv`.", "",
         "| scope | SYNTHETIC noise | shortest tested length with at least 90% unique locks | hit rate there | hits at 5, 10, 20, 40 and 60 min |",
         "|---|---|---|---|---|"]
    for scope in SCOPES:
        for kind in ("white", "hann16"):
            c = f["cells"][(scope, kind)]
            fr = c["first"]
            shortest = "none of the tested lengths" if fr is None else f"{fr['length_min']} min"
            rate = "n/a" if fr is None else fmt_rate(int(fr["hits"]), int(fr["N"]),
                                                     (float(fr["wilson95_lo"]), float(fr["wilson95_hi"])))
            ns = sorted({r["N"] for r in c["rows"]})
            per = (", ".join(r["hits"] for r in c["rows"]) + f" of {ns[0]}") if len(ns) == 1 else                 ", ".join(f"{r['hits']}/{r['N']}" for r in c["rows"])
            L.append(f"| {SCOPE_LABEL[scope]} | {kind} | {shortest} | {rate} | {per} |")
    return L


def b_results(src, ts) -> str:
    L: list[str] = []
    P = PENDING
    gate, gsrc, when = ts.gate()

    # ---- gate
    L += ["### Gate (PROTOCOL section 15)", ""]
    if gate is None:
        L.append(f"- {P}. `scripts/evaluate.py` writes `results/gate_frozen.json` on its first run that starts "
                 "after the decision time with every Test stream harvested by then searched.")
    else:
        p = gate["by_extractor"][PRIMARY]
        if ts.tag:
            L.append(f"- **{ts.tag}**")
        L.append(f"- **{'Gate met' if gate['gate_met'] else 'Gate not met'}** ({when}, `{gsrc}`).")
        L.append(f"- Conditions 1 and 2: {p['n_streams_with_lock']} of N = {gate['N_processed']} Test streams "
                 f"harvested by the decision time locked blind (5 needed): extractor v2, top candidate in the full "
                 f"span or ±90 days within 30 s of T* for at least one excerpt length, with confidence at or above "
                 f"tau of that cell. {'Met' if gate['condition_1_2_met'] else 'Not met'}.")
        L.append(f"- The same count without the label-drift correction (S1, D = 0): {p['n_streams_with_lock_D0']}.")
        L.append(f"- Condition 3, harvest health: {'met' if gate['condition_3_met'] else 'not met'}. "
                 f"tau and L committed by the decision time: {'yes' if gate['tau_L_committed_in_time'] else 'no'}.")
        others = "; ".join(f"{e} {gate['by_extractor'][e]['n_streams_with_lock']}" for e in EXTRACTORS if e != PRIMARY)
        L.append(f"- Locks with the baselines on the same streams (description only): {others}.")
        L.append("- `results/gate.md` lists every processed stream, hits and misses.")
    L.append("")

    # ---- headline Test table
    L += ["### Test split, pre-registered and blind", ""]
    if ts.tag and ts.show:
        L += [f"**{ts.tag}**", ""]
    L += ["| result | value | source |", "|---|---|---|"]
    show = ts.show
    if show:
        k, n = ts.m1()
    rows = [
        ("M1 hum present (extractor v1, all downloaded audio; Test streams with audio)",
         fmt_rate(k, n) if show else P, "`results/test_results.csv`"),
        ("M2 primary endpoint: top-1 start within 30 s of T*, v2, 60-min excerpts, full span",
         rate_of(ts.m2()["top1_hit"]) if show else P, "`results/evaluation.json`"),
        ("Same cell: confident hit (hum flag set and confidence at or above tau, within 30 s)",
         rate_of(ts.m2()["confident_hit"]) if show else P, "`results/evaluation.json`"),
        ("Same cell: confident wrong (verdict match, more than 30 s off)",
         rate_of(ts.m2()["confident_wrong"]) if show else P, "`results/evaluation.json`"),
        ("Same cell: no verdict",
         rate_of(ts.m2()["no_verdict"]) if show else P, "`results/evaluation.json`"),
        ("Same cell, M1 hum-present streams only: top-1 within 30 s",
         rate_of(ts.m2()["top1_hit_hum_present"]) if show else P, "`results/evaluation.json`"),
        ("v2, 60 min, ±90 days: top-1 within 30 s",
         rate_of(ts.m2(PRIMARY, 60, "90d")["top1_hit"]) if show else P, "`results/evaluation.json`"),
    ]
    for kind in ("real", "SYNTHETIC", "together"):
        lab = {"real": "real foreign 50 Hz streams", "SYNTHETIC": "SYNTHETIC noise and hum",
               "together": "real and SYNTHETIC together"}[kind]
        rows.append((f"M3 false matches at tau, report-half negatives, v2, 60 min, full span: {lab}",
                     rate_of(ts.m3()[kind]["false_match"]) if show else P,
                     "`results/evaluation.json` (`m3`; also `results/false_matches.md`)"))
    m5 = other_results(src, ("edit", "m5", "cut"))
    rows.append(("M5 edit detection on SYNTHETIC cuts",
                 f"computed in `results/{m5[0]}` (not read by this script yet)" if m5 else
                 "[pending: M5 not computed yet; the SYNTHETIC cut traces exist]", "PROTOCOL section 11"))
    st = other_results(src, ("station",))
    rows.append(("M6 live station (optional)", f"see `results/{st[0]}`" if st else "not run",
                 "PROTOCOL section 11"))
    for a, b, c in rows:
        L.append(f"| {a} | {b} | {c} |")
    if show:
        mc = ts.ev["m3_counts"]
        L += ["", f"M3 used {mc['real']} of 25 real and {mc['SYNTHETIC']} of 20 SYNTHETIC report-half negatives "
                  f"(`results/evaluation.json`, `m3_counts`). Every cell, with and without the hum-flag condition, "
                  f"is in `results/false_matches.md`."]
    L.append("")

    # ---- details
    if not show:
        L += [f"The M2 grid by excerpt length and scope, the baselines on the same streams, the sensitivity "
              f"analyses S1 to S4, M1 by venue type and the flow table appear here once the gate is frozen. {P}", ""]
    else:
        L += ["### M2 top-1 within 30 s, extractor v2 (Wilson 95%; `results/evaluation.json`)", ""]
        L += _m2_grid(ts, "top1_hit") + [""]
        L += ["Confident wrong (verdict match, more than 30 s off), same cells:", ""]
        L += _m2_grid(ts, "confident_wrong") + [""]

        L += ["### Baselines on the same streams, 60 min, full span", ""]
        L += ["| method | top-1 within 30 s | confident hit | confident wrong | paired with v2 on the same streams (both hit / v2 only / baseline only / neither) |",
              "|---|---|---|---|---|"]
        for ext in EXTRACTORS:
            c = ts.m2(ext)
            pr = "" if ext == PRIMARY else (lambda q: f"{q['both']} / {q['v2_only']} / {q['base_only']} / "
                                                      f"{q['neither']} (n = {q['n']})")(ts.paired(ext))
            name = {"v2": "v2 (primary)", "v1": "v1 (frozen extractor)", "naive": "naive single-bin 50 Hz"}[ext]
            L.append(f"| {name} | {rate_of(c['top1_hit'])} | {rate_of(c['confident_hit'])} | "
                     f"{rate_of(c['confident_wrong'])} | {pr} |")
        lh = other_results(src, ("libhum",))
        L.append(f"| libhum | " + (f"see `results/{lh[0]}`" if lh else
                                    "[pending: libhum baseline not run yet; PROTOCOL section 12]") + " | | | |")
        g = src.need("results/splits_summary.json")["analytic_random_guess"]["full_60min"]
        n60 = ts.m2()["n"]
        L.append(f"| random guess (analytic) | p = {sci(g['p_hit'])} per stream; expected chance hits over these "
                 f"{n60} streams: {sci(n60 * g['p_hit'], 2)} | | | |")
        L += ["", "Sources: `results/evaluation.json` (rates), `results/test_results.csv` (pairs), "
                  "`results/splits_summary.json` (random guess).", ""]

        L += ["### Sensitivity analyses (PROTOCOL section 13), v2, 60 min, full span", ""]
        k2, n2 = ts.hits(keep=lambda r: r.get("label_stable") == "True")
        k3, n3 = ts.hits(keep=lambda r: r.get("channel_status") == VERIFIED)
        L += ["| analysis | top-1 within 30 s | source |", "|---|---|---|",
              f"| as pre-registered (T* = release time + L + D) | {rate_of(ts.m2()['top1_hit'])} | `results/evaluation.json` |",
              f"| S1: no label-drift correction (D = 0) | {rate_of(ts.m2()['top1_hit_D0'])} | `results/evaluation.json` |",
              f"| S2: label-stable streams only | {fmt_rate(k2, n2)} | `results/test_results.csv`, `label_stable` |",
              f"| S3: verified channels only | {fmt_rate(k3, n3)} | `results/test_results.csv`, `channel_status` |",
              "| S4: v1 against v2 | see the paired column above | `results/test_results.csv` |", ""]

        L += ["### M1 hum presence by venue type (`results/test_results.csv`)", ""]
        venues = sorted({r.get("venue_type") or "unknown" for r in ts.streams.values()})
        L += ["| venue type | hum present |", "|---|---|"]
        for v in venues:
            kv, nv = ts.m1(keep=lambda r, v=v: (r.get("venue_type") or "unknown") == v)
            if nv:
                L.append(f"| {v} | {fmt_rate(kv, nv)} |")
        k, n = ts.m1()
        L += [f"| all | {fmt_rate(k, n)} |", ""]

        L += ["### Flow, Test streams (`results/evaluation.json`; drop reasons in `results/flow_table.md`)", ""]
        L += ["| step | streams |", "|---|---|"]
        for s in ts.ev["flow"]["steps"]:
            L.append(f"| {s['step']} | {s['n']} |")
        L.append("")

    # ---- final pre-Test results
    rf, dr, lf, tf, dv = ref_facts(src), drift_facts(src), latency_facts(src), tau_facts(src), dev_facts(src)
    L += ["### Measured before any Test search", ""]
    lg = rf["longest"]
    L.append(f"- **Reference** (`results/reference_stats.json`): {rf['t0']} to {rf['t1']} IST, {num(rf['seconds'])} "
             f"seconds ({rf['days']} days), {num(rf['valid'])} valid ({rf['valid_pct']:.2f}%). {rf['gaps10']} gaps "
             f"longer than 10 s hold {num(rf['missing10'])} missing seconds; the longest runs from {lg['start_ist']} "
             f"to {lg['end_ist']} ({num(lg['length_s'])} s).")
    L.append(f"- **The reference's time labels drift** (`results/time_axis_check.json`). Checked against the grid's "
             f"15-minute scheduling clock, each file's labels run late by the end of the file: "
             + ", ".join(f"{FILE_ROMAN[i]} {secs_text(v)}" for i, v in enumerate(dr["last"]))
             + ". Where two files overlap they label the same values "
             + ", ".join(secs_text(v) for v in dr["overlaps"])
             + " apart (I-II, II-III, III-IV, IV-V)"
             + (", and the clock check gives the same four offsets within 1 s. " if dr["agree"] else
                "; the clock check does not reproduce all four offsets. ")
             + f"Gunj therefore corrects every stream by its own drift estimate D. For {dr['gt30']} of the "
             f"{dr['test_n']} Test streams |D| is over 30 s, wider than the hit window itself (median |D| "
             f"{secs_text(dr['median_abs'])}, largest {secs_text(dr['max_abs'])}; `results/splits_summary.json`). "
             f"{dr['stable']} Test streams are label-stable (S2).")
    rr, iq = lf["range"], lf["iqr"]
    L.append(f"- **Latency L** (`results/latency.json`, Calibration split only): L = {lf['L']} s ({lf['status']}), "
             f"the median offset o of the {lf['n']} Calibration streams of {lf['harvested']} harvested "
             f"({lf['selected']} selected) that locked with confidence at or above tau and |o| ≤ 600 s. Their o "
             f"runs from {secs_text(rr[0])} to {secs_text(rr[1])} (IQR {secs_text(iq[0])} to {secs_text(iq[1])}). "
             + ("The range is within the 60 s that PROTOCOL section 6 allows for one constant latency. " if lf["holds"]
                else "The range is wider than 60 s, so by PROTOCOL section 6 one constant latency does not hold. ")
             + f"Streams that count, by extractor (`results/calibration.json`): "
             + ", ".join(f"{e} {lf['by_ext'][e]}" for e in EXTRACTORS if e in lf["by_ext"]) + f" of {lf['harvested']}.")
    L.append(f"- **Hum on the Calibration split** (description only, `results/calibration.json`): v1 hum flag on "
             f"{fmt_rate(lf['hum_k'], lf['hum_n'])} harvested streams.")
    c = tf["cells"][PRIMARY]
    L.append(f"- **Threshold tau** (`results/threshold.json`): set per extractor, excerpt length and scope on the "
             f"threshold half of the negatives, {tf['real']} real foreign 50 Hz streams and {tf['syn']} SYNTHETIC "
             f"items ({tf['excluded']} real ones could not be downloaded). v2, 60 min: full span "
             f"{c['60']['full']['tau']:.4f}, ±90 days {c['60']['90d']['tau']:.4f}, ±1 day {c['60']['1d']['tau']:.4f}.")
    if tf["best_real_r1"]:
        r1, conf, m, s = tf["best_real_r1"]
        ctxt = "" if conf is None else f", but its confidence was only {conf:.4f} because its runner-up scored almost as high"
        L.append(f"  - The highest raw correlation of any real negative was r1 = {r1:.4f} (v2, {m} min, "
                 f"{SCOPE_LABEL[s]}){ctxt}. Foreign recordings can correlate strongly with some stretch of the "
                 f"Indian log, which is why the confidence is the margin atanh(r1) - atanh(r2) over the best "
                 f"candidate at least 60 s away.")
    L.append(f"- **Extractor v2, tuned on the {dv['n']} Dev streams only** (`results/v2_tuning.json`; Dev is never "
             f"pooled with Test): objective J = {dv['J']:.3f} for the chosen candidate {dv['chosen']}, against "
             f"{dv['J_v1']:.3f} for v1 and {dv['J_naive']:.3f} for the naive extractor.")
    L += ["", "### M4: how much audio a unique lock needs (reference only, SYNTHETIC noise)", ""]
    L += _m4_table(src)
    return "\n".join(L)


def fmt_tiny(k: int, n: int, ci=None) -> str:
    """Shortest form for the Unstop text: 'k/n (p%, CI lo-hi%)'."""
    if not n:
        return "none yet"
    lo, hi = ci if ci is not None else wilson(k, n)
    return f"{k}/{n} ({100 * k / n:.1f}%, CI {100 * lo:.1f}-{100 * hi:.1f}%)"


def tiny(d: dict | None) -> str:
    if not d or not d.get("n"):
        return "none yet"
    return fmt_tiny(int(d["k"]), int(d["n"]), d.get("wilson95"))


def b_unstop_results(src, ts) -> str:
    f4 = m4_facts(src)
    full = f4["cells"][("full", "hann16")]["first"]
    m4 = ("" if full is None else
          f" Simulation with SYNTHETIC noise like the probe's: {full['length_min']} min of audio gave unique "
          f"full-span locks in {100 * int(full['hits']) / int(full['N']):.1f}% of {num(full['N'])} trials.")
    if not ts.show:
        lf = latency_facts(src)
        return (f"Test results: {PENDING}. Already measured: start latency L = {lf['L']} s from {lf['n']} of "
                f"{lf['harvested']} Calibration streams (offsets {secs_text(lf['range'][0])} to "
                f"{secs_text(lf['range'][1])}).{m4}")
    gate, _, _ = ts.gate()
    k, n = ts.m1()
    head = f"{ts.tag}: " if ts.tag else ""
    gtxt = ""
    if gate is not None:
        p = gate["by_extractor"][PRIMARY]
        why = [t for ok, t in ((gate["condition_1_2_met"], "fewer than 5 locks"),
                                (gate["condition_3_met"], "harvest health failed"),
                                (gate["tau_L_committed_in_time"], "tau or L not committed in time")) if not ok]
        verdict = "met" if gate["gate_met"] else "not met (" + "; ".join(why or ["see results/gate.md"]) + ")"
        gtxt = f" Gate {verdict}: {p['n_streams_with_lock']} of {gate['N_processed']} streams locked blind."
    m2 = ts.m2()
    return (f"{head}Results (N = {n} streams with audio): hum in {fmt_tiny(k, n)}. 60-min excerpts, full span: "
            f"top-1 within 30 s {tiny(m2['top1_hit'])}, confidently wrong {m2['confident_wrong']['k']}/"
            f"{m2['confident_wrong']['n']}. False matches at the threshold: real {tiny(ts.m3()['real']['false_match'])}, "
            f"SYNTHETIC {tiny(ts.m3()['SYNTHETIC']['false_match'])}.{gtxt}")


def b_unstop_baselines(src, ts) -> str:
    if not ts.show:
        return PENDING
    v1, nv = ts.m2("v1")["top1_hit"], ts.m2("naive")["top1_hit"]
    return f"v1 {v1['k']}/{v1['n']}, naive {nv['k']}/{nv['n']}" + (f" ({ts.tag})" if ts.tag else "")


BLOCKS = {
    "span": b_span,
    "span-end": b_span_end,
    "fm-bound": b_fm_bound,
    "ref-size": b_ref_size,
    "splits": b_splits,
    "test-licences": b_test_licences,
    "drift-max": b_drift_max,
    "test-selected": b_test_selected,
    "random-guess": b_random_guess,
    "latency": b_latency,
    "cli-threshold": b_cli_threshold,
    "status": b_status,
    "results": b_results,
    "unstop-results": b_unstop_results,
    "unstop-baselines": b_unstop_baselines,
}


# ----------------------------------------------------------------- filling

def fill_text(text: str, src: Sources, ts: TestState, cache: dict) -> tuple[str, list]:
    """Replace every block in text. Returns (new text, names of the blocks found)."""
    nl = "\r\n" if "\r\n" in text else "\n"
    out, pos, found = [], 0, []
    while True:
        m = MARKER_RE.search(text, pos)
        if m is None:
            out.append(text[pos:])
            break
        name = m.group(1)
        if name not in BLOCKS:
            raise FillError(f"unknown block {name!r}; known: {', '.join(sorted(BLOCKS))}")
        end_re = re.compile(r"<!--\s*" + re.escape(name) + r":end\s*-->")
        e = end_re.search(text, m.end())
        if e is None:
            raise FillError(f"block {name!r} has no end marker")
        inner = text[m.end():e.start()]
        if MARKER_RE.search(inner):
            raise FillError(f"block {name!r} contains another start marker")
        if name not in cache:
            cache[name] = BLOCKS[name](src, ts)
        body = cache[name]
        if ABS_PATH_RE.search(body):
            raise FillError(f"block {name!r} would contain a local absolute path")
        if inner.startswith(("\n", "\r\n")):            # a block on its own lines
            body = nl + body.replace("\n", nl) + nl
        out.append(text[pos:m.end()] + body + e.group(0))
        pos = e.end()
        found.append(name)
    return "".join(out), found


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Fill README and Unstop results blocks from results/")
    ap.add_argument("targets", nargs="*", help=f"files to fill (default: {' '.join(DEFAULT_TARGETS)})")
    ap.add_argument("--eval-dir", default=None,
                    help="folder with scripts/evaluate.py outputs (default results/; for testing only)")
    ap.add_argument("--interim", action="store_true",
                    help="show Test numbers before the gate is frozen, labelled INTERIM (never publish)")
    ap.add_argument("--allow-practice", action="store_true",
                    help="accept a practice evaluation.json, labelled PRACTICE (testing only)")
    ap.add_argument("--dry-run", action="store_true", help="report what would change; write nothing")
    ap.add_argument("--print", dest="print_blocks", action="store_true", help="print every generated block")
    return ap


def main(argv=None, repo: Path = REPO) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(repo)
    eval_dir = Path(args.eval_dir) if args.eval_dir else repo / "results"
    if not eval_dir.is_absolute():
        eval_dir = repo / eval_dir
    targets = [Path(t) for t in (args.targets or DEFAULT_TARGETS)]
    targets = [t if t.is_absolute() else repo / t for t in targets]
    src = Sources(repo, eval_dir, args.interim, args.allow_practice)
    try:
        ts = TestState(src)
        cache: dict = {}
        plans = []
        for t in targets:
            if not t.exists():
                raise FillError(f"target {t.name} does not exist")
            with t.open(encoding="utf-8", newline="") as fh:
                old = fh.read()
            new, found = fill_text(old, src, ts, cache)
            if not found:
                src.warnings.append(f"{t.name}: no blocks found")
            plans.append((t, old, new, found))
    except FillError as e:
        print(f"fill_results: {e}. Nothing was written.", file=sys.stderr)
        return 2
    for w in src.warnings:
        print(f"warning: {w}", file=sys.stderr)
    state = ("Test values shown, gate frozen" if ts.frozen is not None else
             f"Test values shown, {ts.tag}" if ts.show else f"Test values pending ({PENDING})")
    print(f"fill_results: {state}")
    if args.print_blocks:
        for name, body in cache.items():
            print(f"\n----- {name}\n{body}")
    for t, old, new, found in plans:
        rel = src.label(t)
        if new == old:
            print(f"  {rel}: unchanged ({len(found)} blocks)")
            continue
        if args.dry_run:
            print(f"  {rel}: would change ({len(found)} blocks: {', '.join(sorted(set(found)))})")
            continue
        write_atomic(t, new)
        print(f"  {rel}: written ({len(found)} blocks: {', '.join(sorted(set(found)))})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
