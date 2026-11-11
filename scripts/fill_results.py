#!/usr/bin/env python
"""Fill the results blocks of README.md, submission/UNSTOP-ANSWERS.md,
video/NARRATION.md and video/SCRIPT.md.

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

Results that later scripts computed from the scoring outputs are read from
their own files: results/error_breakdown.json, results/m4_measured.json,
results/exploratory_offset_verdicts.json and results/figures/ (written by
scripts/make_figures.py) and results/m5.json (written by scripts/m5_edits.py).
Each is checked against results/test_results.csv, results/evaluation.json or
results/gate_frozen.json before it is used, and the run stops (exit 2) if it
disagrees. If one of these files is missing, its place in the text says "not
computed" and names the script to run.

The narration of the demo video is checked too. The spoken lines in
video/NARRATION.md are written by hand. The block narration-sources lists
where each spoken number comes from, and the run stops (exit 2) if a spoken
number differs from its source file. The blocks vo-1, vo-2, ... copy the
spoken lines into video/SCRIPT.md.

The script reads only small JSON, CSV, Markdown and Python text files. It
never loads the reference series or any trace, and it starts no worker
processes.

Usage (from the repo root):
  .venv\\Scripts\\python.exe scripts\\fill_results.py              fill the four files
  .venv\\Scripts\\python.exe scripts\\fill_results.py --dry-run    report what would change
  .venv\\Scripts\\python.exe scripts\\fill_results.py --check      exit 1 if a file is out of date
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
DEFAULT_TARGETS = ("README.md", "submission/UNSTOP-ANSWERS.md", "video/NARRATION.md", "video/SCRIPT.md")
MARKER_RE = re.compile(r"<!--\s*([a-z0-9][a-z0-9-]*):start\s*-->")
ABS_PATH_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|Users[\\/]|AppData")
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
FILE_ROMAN = ("I", "II", "III", "IV", "V")
CLASS_KEYS = ("within_30", "from_30_to_120", "from_120_to_600", "beyond_600", "no_candidate")   # scripts/make_figures.py
FIG_DIR = "results/figures"
NARRATION = "video/NARRATION.md"
NARR_LINE_RE = re.compile(r"^\s*(\d+)\.\s+\*\*(\d+):(\d{2})\*\*\s+(.+?)\s*$", re.M)    # as video/make_video.py
NARR_END_RE = re.compile(r"video ends at \*\*(\d+):(\d{2})\*\*", re.I)
VO_RE = re.compile(r"vo-(\d+)")
ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
        "sixteen seventeen eighteen nineteen").split()
TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()


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


def prose_rate(d: dict | None, noun: str = "") -> str:
    """The same rate for a sentence: '25 of 90 streams (27.8%, 95% CI 19.6 to 37.8%)'."""
    if not d or not d.get("n"):
        return "n/a (N = 0)"
    k, n = int(d["k"]), int(d["n"])
    lo, hi = d.get("wilson95") or wilson(k, n)
    return f"{k} of {n}{' ' + noun if noun else ''} ({100 * k / n:.1f}%, 95% CI {100 * lo:.1f} to {100 * hi:.1f}%)"


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


def dur_text(s: float) -> str:
    """A length of time for a sentence: 45 s, 88 minutes, 3.2 hours, 33.3 days."""
    s = abs(float(s))
    if s < 120:
        return secs_text(s)
    if s < 2 * 3600:
        return f"{round(s / 60)} minutes"
    if s < 2 * 86400:
        return f"{s / 3600:.1f} hours"
    return f"{s / 86400:.1f} days"


def words(n) -> str:
    """0 to 999 as the narration says it: 126 -> 'a hundred and twenty-six'."""
    n = int(n)
    if not 0 <= n <= 999:
        raise FillError(f"no spoken form for {n}")
    if n < 20:
        return ONES[n]
    if n < 100:
        t, o = divmod(n, 10)
        return TENS[t - 2] + ("-" + ONES[o] if o else "")
    h, r = divmod(n, 100)
    return ("a" if h == 1 else ONES[h]) + " hundred" + (" and " + words(r) if r else "")


def mhz(x_hz: float) -> str:
    return f"{1000 * float(x_hz):.1f}"


def plural(n: int, one: str, many: str | None = None) -> str:
    return one if int(n) == 1 else (many or one + "s")


# ----------------------------------------------------------------- sources

class Sources:
    """Reads repo files once; Test files come from --eval-dir (default results/)."""

    def __init__(self, repo: Path, eval_dir: Path, interim: bool, allow_practice: bool):
        self.repo = repo
        self.results = repo / "results"
        self.eval_dir = eval_dir
        self.interim = interim
        self.allow_practice = allow_practice
        self.narration = repo / NARRATION
        self.protocol = repo / "PROTOCOL.md"
        self.make_video = repo / "video" / "make_video.py"
        self.site_overview = repo / "site" / "data" / "overview.json"
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
        elif kind == "text":
            val = path.read_text(encoding="utf-8")
        else:
            with path.open(encoding="utf-8", newline="") as fh:
                val = list(csv.DictReader(fh))
        self._cache[key] = val
        return val

    def json(self, rel: str):
        return self._read(self.repo / rel, "json")

    def csv(self, rel: str):
        return self._read(self.repo / rel, "csv")

    def text(self, path: Path):
        return self._read(path, "text")

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

    def health(self) -> dict:
        """Harvest health as the gate saw it."""
        if self.frozen is not None:
            return self.frozen.get("health") or {}
        return (self.ev or {}).get("health") or {}

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


def ref_months(src: Sources) -> int:
    """Whole months from the first to the last second of the reference."""
    f = ref_facts(src)
    y0, m0, d0 = (int(x) for x in f["t0"][:10].split("-"))
    y1, m1, d1 = (int(x) for x in f["t1"][:10].split("-"))
    return (y1 - y0) * 12 + (m1 - m0) - (1 if d1 < d0 else 0)


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


def report_half(src: Sources) -> tuple[int, int]:
    """Report-half negatives selected: (real, SYNTHETIC)."""
    sp = src.need("results/splits_summary.json")["splits"]
    return sp["negative"]["by_half"]["report"], sp["negative_synthetic"]["by_half"]["report"]


def other_results(src: Sources, words_: tuple) -> list[str]:
    return sorted(p.name for p in src.results.glob("*") if p.is_file() and any(w in p.name.lower() for w in words_))


def protocol_says(src: Sources, *phrases: str) -> bool:
    text = src.text(src.protocol) or ""
    return all(p in text for p in phrases)


def libhum_status(src: Sources) -> str:
    """The libhum baseline: a result file if one exists, otherwise 'not run' with the reason PROTOCOL.md records."""
    lh = other_results(src, ("libhum",))
    if lh:
        return f"see `results/{lh[0]}`"
    if protocol_says(src, "The libhum baseline of section 12 has not been run", "libhum was never installed"):
        return ("not run: libhum was never installed, and the time went to the harvest problems "
                "(PROTOCOL section 19, 11 Nov 2026)")
    return "not run: no libhum result is in `results/` (PROTOCOL section 12)"


# ----------------------------------------------------------------- facts computed after the Test searches

def classes(rows) -> dict:
    """Distance classes of the top-1 candidate from T*, from the pre-registered hit columns
    (PROTOCOL section 9), exactly as scripts/make_figures.py counts them."""
    out = dict.fromkeys(CLASS_KEYS, 0)
    n = 0
    for r in rows:
        n += 1
        if r.get("error_s", "") == "":
            out["no_candidate"] += 1
        elif r.get("hit_30s") == "True":
            out["within_30"] += 1
        elif r.get("hit_120s") == "True":
            out["from_30_to_120"] += 1
        elif r.get("hit_600s") == "True":
            out["from_120_to_600"] += 1
        else:
            out["beyond_600"] += 1
    out["n"] = n
    return out


def breakdown_facts(src: Sources, ts: TestState):
    """results/error_breakdown.json, after every cell has been recounted from results/test_results.csv."""
    eb = src.eval_json("error_breakdown.json")
    if eb is None:
        return None
    bad = []
    for ext in EXTRACTORS:
        for m in LENGTHS:
            for s in SCOPES:
                try:
                    cell = eb["cells"][ext][str(m)][s]
                    parts = {"confident": cell["confident"], "top1": cell["top1"]}
                except (KeyError, TypeError) as e:
                    raise FillError(f"error_breakdown.json has no cell {ext}/{m}/{s}; re-run scripts/make_figures.py") from e
                rows = list(ts.cells.get((ext, m, s), {}).values())
                mine = {"confident": classes([r for r in rows if r.get("verdict") == "match"]), "top1": classes(rows)}
                for part in ("confident", "top1"):
                    if any(parts[part].get(k) != v for k, v in mine[part].items()):
                        bad.append(f"{ext}/{m}/{s} {part}")
    if bad:
        raise FillError("error_breakdown.json disagrees with test_results.csv in " + ", ".join(bad[:4])
                        + "; re-run scripts/make_figures.py")
    return eb


def far_verdicts(ts: TestState, ext=PRIMARY, m=60, scope="full") -> list:
    """Confident verdicts more than 600 s from T*: [(abs error in s, video id)], nearest first."""
    out = []
    for r in ts.cells.get((ext, m, scope), {}).values():
        if r.get("verdict") == "match" and r.get("error_s", "") != "" and r.get("hit_600s") != "True":
            out.append((abs(int(float(r["error_s"]))), r["video_id"]))
    return sorted(out)


def m5_facts(src: Sources, ts: TestState):
    """results/m5.json, after checking that it scored the confident hits of results/test_results.csv."""
    m5 = src.eval_json("m5.json")
    if m5 is None:
        return None
    pre = m5.get("preregistered") or {}
    want = sorted(v for v, r in ts.cells.get((PRIMARY, 60, "full"), {}).items() if r.get("outcome") == "confident hit")
    if sorted(pre.get("stream_ids") or []) != want:
        raise FillError("m5.json scored other streams than the confident hits (v2, 60 min, full span) of "
                        "test_results.csv; re-run scripts/m5_edits.py")
    if m5.get("SYNTHETIC") is not True:
        raise FillError("m5.json does not mark its cuts SYNTHETIC")
    cuts = [c for s in pre.get("streams") or [] for c in s.get("cuts") or []]
    tot = pre["totals"]["all"]["detected"]
    if cuts and (len(cuts), sum(1 for c in cuts if c.get("detected"))) != (tot["n"], tot["k"]):
        raise FillError("the totals of m5.json do not match its own list of cuts; re-run scripts/m5_edits.py")
    return m5


def m4m_facts(src: Sources, ts: TestState):
    """results/m4_measured.json, after checking that it measured the streams the gate counts as locks."""
    d = src.eval_json("m4_measured.json")
    if d is None:
        return None
    gate, _, _ = ts.gate()
    locked = sorted(((gate or {}).get("by_extractor") or {}).get(PRIMARY, {}).get("locked_streams") or [])
    if sorted(d["streams_that_lock"]["video_ids"]) != locked:
        raise FillError("m4_measured.json measured other streams than the gate locks; re-run scripts/make_figures.py")
    return d


def reliability_facts(src: Sources, ts: TestState):
    """The M3 reliability bins of the primary cell from results/figures/figures.json, checked against m2."""
    f = src.eval_json("figures/figures.json")
    if f is None:
        return None
    p = f["m3_primary_cell"]
    t, m2 = p["test"], ts.m2()
    if (sum(b["n"] for b in t["bins"]) + t["n_without_confidence"] != m2["n"]
            or sum(b["k"] for b in t["bins"]) != m2["top1_hit"]["k"]):
        raise FillError("figures/figures.json disagrees with evaluation.json (m2); re-run scripts/make_figures.py")
    real = ts.m3()["real"]["false_match"]
    if p["negatives"]["real"]["n"] != real["n"] or p["negatives"]["real"]["n_at_or_above_tau"] != real["k"]:
        raise FillError("figures/figures.json disagrees with evaluation.json (m3); re-run scripts/make_figures.py")
    return p


def exploratory_facts(src: Sources, ts: TestState, bd: dict | None):
    """results/exploratory_offset_verdicts.json, checked against the breakdown of the primary cell."""
    ex = src.eval_json("exploratory_offset_verdicts.json")
    if ex is None or bd is None:
        return None
    c = bd["cells"][PRIMARY]["60"]["full"]["confident"]
    pc = ex["primary_cell"]
    if (pc["within_30"]["n_verdicts"] != c["within_30"]
            or pc["offset_30_600"]["n_verdicts"] != c["from_30_to_120"] + c["from_120_to_600"]):
        raise FillError("exploratory_offset_verdicts.json disagrees with error_breakdown.json; "
                        "re-run scripts/make_figures.py")
    return ex


def harvest_facts(ts: TestState) -> dict:
    """What happened to the selected Test streams, from the flow of results/evaluation.json."""
    ev = ts.ev
    out = {"selected": ev["n_selected"], "with_trace": ev["n_with_trace"],
           "unavailable": 0, "failed": 0, "blocked": 0, "not_attempted": 0, "other": 0, "failed_ids": []}
    for d in ev["flow"]["drops"]:
        if d.get("to") not in ("download attempted", "audio obtained"):
            continue
        for reason, ids in d["reasons"].items():
            low = reason.lower()
            key = ("not_attempted" if low.startswith("not attempted") else
                   "blocked" if low.startswith("blocked") else
                   "unavailable" if "not available" in low else
                   "failed" if low.startswith("failed") else "other")
            out[key] += len(ids)
            if key == "failed":
                out["failed_ids"] += list(ids)
    out["lost"] = out["selected"] - out["with_trace"]
    if out["lost"] != sum(out[k] for k in ("unavailable", "failed", "blocked", "not_attempted", "other")):
        raise FillError("the flow drops in evaluation.json do not add up to the streams without a trace")
    return out


def block_facts(ts: TestState) -> dict:
    """The bot check that stopped the harvest: blocked streams and the time of the first log line."""
    h = ts.health()
    when = None
    for lm in h.get("log_matches") or []:
        m = re.match(r"(\d{4}-\d\d-\d\d)T(\d\d:\d\d):\d\d\+0530", lm.get("text", ""))
        if m:
            when = f"{day_text(m.group(1))}, {m.group(2)} IST"
            break
    return {"rows": list(h.get("blocked_rows") or []), "when": when}


# ----------------------------------------------------------------- narration

def narration(src: Sources) -> dict:
    """The timed lines of video/NARRATION.md: {'lines': [(number, start s, text)], 'end': s}."""
    text = src.text(src.narration)
    if text is None:
        raise FillError(f"{NARRATION} is missing")
    lines = [(int(m.group(1)), int(m.group(2)) * 60 + int(m.group(3)), m.group(4).strip())
             for m in NARR_LINE_RE.finditer(text)]
    end = NARR_END_RE.search(text)
    if not lines or not end:
        raise FillError(f"{NARRATION}: no timed lines or no end time found")
    if [n for n, _, _ in lines] != list(range(1, len(lines) + 1)):
        raise FillError(f"{NARRATION}: the timed lines are not numbered 1, 2, 3, ...")
    return {"lines": lines, "end": int(end.group(1)) * 60 + int(end.group(2))}


def video_ids(src: Sources) -> dict:
    """The two Test streams the video shows, as video/make_video.py names them."""
    text = src.text(src.make_video) or ""
    out = {}
    for key in ("LOCK_ID", "NV_ID"):
        m = re.search(rf'^{key}\s*=\s*"([^"]+)"', text, re.M)
        if not m:
            raise FillError(f"cannot find {key} in video/make_video.py")
        out[key] = m.group(1)
    return out


def narration_rows(src: Sources, ts: TestState) -> list[tuple]:
    """One row per spoken number: (line, phrase the line must contain, what is said, value, source).

    The phrase is built from the value in the results file, so a line that says another number fails the check.
    """
    rf, dr = ref_facts(src), drift_facts(src)
    R: list[tuple] = []
    months = ref_months(src)
    R.append((3, f"{words(months)} months", f"{words(months)} months",
              f"{day_text(rf['t0'])} to {day_text(rf['t1'])}", "`results/reference_stats.json`"))
    million = int(rf["seconds"]) // 1_000_000
    R.append((3, f"about {words(million)} million seconds", f"about {words(million)} million seconds",
              f"{num(rf['seconds'])} s", "`results/reference_stats.json`"))
    try:
        hero = src._read(src.site_overview, "json")["hero"]
        full = hero["searches"]["full"]
        val = (f"r {full['candidates'][0]['r']:.4f} against {full['runner_up']['r']:.4f}, confidence "
               f"{full['confidence']:.3f} against tau {full['tau']:.4f}; {hero['split_label']} stream "
               f"{hero['video_id']}, shown as an example only")
        R.append((4, f"a {hero['split']} stream", f"well clear of the next best; a {hero['split']} stream", val,
                  "`site/data/overview.json`, `hero`"))
    except (KeyError, TypeError, IndexError):
        R.append((None, None, "well clear of the next best (line 4)", "the example stream of the explorer",
                  "`site/data/overview.json`, `hero`"))
    if not ts.show:
        R.append((None, None, "every Test number in lines 5 to 10", PENDING, "`results/evaluation.json`"))
        return R

    ev, m2 = ts.ev, ts.m2()
    R.append((5, f"{words(ev['n_with_trace'])} of {words(ev['n_selected'])} streams",
              f"{words(ev['n_with_trace'])} of {words(ev['n_selected'])}",
              f"{ev['n_with_trace']} of {ev['n_selected']} selected Test streams had audio when the bot check "
              f"stopped the harvest", "`results/evaluation.json`; PROTOCOL section 19"))
    R.append((6, f"{words(m2['n']).capitalize()} had an hour of audio", f"{words(m2['n'])} had an hour of audio",
              f"n = {m2['n']} Test streams with a 60-min excerpt", "`results/evaluation.json`"))
    top = m2["top1_hit"]
    R.append((6, f"For {words(top['k'])}, Gunj's best guess was within thirty seconds",
              f"for {words(top['k'])}, the best guess was within thirty seconds",
              f"{rate_of(top)}: top-1 start within 30 s of T*, v2, 60 min, full span", "`results/evaluation.json`"))
    bd = breakdown_facts(src, ts)
    if bd is None:
        raise FillError("results/error_breakdown.json is missing, so line 6 of the narration cannot be checked; "
                        "run scripts/make_figures.py")
    c = bd["cells"][PRIMARY]["60"]["full"]["confident"]
    R.append((6, f"{words(c['n']).capitalize()} verdicts were confident", f"{words(c['n'])} verdicts were confident",
              f"{c['n']} of the {m2['n']} excerpts have the verdict \"match\" (hum flag set, confidence at or above tau)",
              "`results/error_breakdown.json`"))
    far = far_verdicts(ts)
    far_txt = " and ".join(f"{num(e)} s ({dur_text(e)})" for e, _ in far)
    R.append((6, f"{words(c['within_30']).capitalize()} within thirty seconds, {words(c['from_30_to_120'])} more "
                 f"within two minutes, {words(c['from_120_to_600'])} within ten, {words(c['beyond_600'])} plainly wrong",
              f"{words(c['within_30'])} within thirty seconds, {words(c['from_30_to_120'])} more within two minutes, "
              f"{words(c['from_120_to_600'])} within ten, {words(c['beyond_600'])} plainly wrong",
              f"{c['within_30']} within 30 s, {c['from_30_to_120']} from 30 to 120 s, {c['from_120_to_600']} from 120 "
              f"to 600 s, {c['beyond_600']} beyond 600 s" + (f": off by {far_txt}" if far else ""),
              "`results/error_breakdown.json`; `results/test_results.csv`, `error_s`"))
    R.append((7, f"about {words(round(dr['max_last'] / 60))} minutes",
              f"up to about {words(round(dr['max_last'] / 60))} minutes",
              f"largest label drift by the end of a file: {secs_text(dr['max_last'])}",
              "`results/time_axis_check.json`"))
    gate, gsrc, _ = ts.gate()
    p = gate["by_extractor"][PRIMARY]
    R.append((7, f"{words(p['n_streams_with_lock'])} test streams lock with it, {words(p['n_streams_with_lock_D0'])} without",
              f"{words(p['n_streams_with_lock'])} with the correction, {words(p['n_streams_with_lock_D0'])} without",
              f"gate count: {p['n_streams_with_lock']} of {gate['N_processed']} streams lock at some excerpt length "
              f"(full span or ±90 days), {p['n_streams_with_lock_D0']} with D = 0", f"`{gsrc}`"))
    mm = m4m_facts(src, ts)
    if mm is None:
        raise FillError("results/m4_measured.json is missing, so line 8 of the narration cannot be checked; "
                        "run scripts/make_figures.py")
    ra, rb = (mm["readings"][k]["median"]["hann16"]["full"] for k in ("A", "B"))
    if ra["status"] != "read" or rb["status"] != "read":
        raise FillError("m4_measured.json has no clip length at one of the two median noise levels")
    lo_min, hi_min = sorted((ra["shortest_length_min"], rb["shortest_length_min"]))
    R.append((8, f"{words(lo_min)} to {words(hi_min)} minutes", f"{words(lo_min)} to {words(hi_min)} minutes",
              f"shortest tested length with at least 90% unique locks over the full span, SYNTHETIC hann16 noise at "
              f"the median noise of the {mm['streams_that_lock']['n']} Test streams that lock: "
              f"{ra['shortest_length_min']} min at {ra['sigma_mhz']:.1f} mHz (r as scored), "
              f"{rb['shortest_length_min']} min at {rb['sigma_mhz']:.1f} mHz (unweighted r)",
              "`results/m4_measured.json`"))
    ids = video_ids(src)
    lock = ts.cells.get((PRIMARY, 60, "full"), {}).get(ids["LOCK_ID"])
    if lock is None or lock.get("outcome") != "confident hit":
        raise FillError(f"the lock example {ids['LOCK_ID']} of video/make_video.py is not a confident hit "
                        f"(v2, 60 min, full span) in test_results.csv")
    e, D = abs(int(float(lock["error_s"]))), float(lock["label_drift_s"])
    dmin = round(abs(D) / 60)
    almost = "almost " if abs(D) < 60 * dmin else ""
    R.append((9, f"{words(e)} seconds from its expected start", f"{words(e)} seconds",
              f"Test stream {ids['LOCK_ID']}: e = {int(float(lock['error_s'])):+d} s (v2, 60 min, full span)",
              "`results/test_results.csv`"))
    R.append((9, f"{almost}{words(dmin)} minutes of drift correction", f"{almost}{words(dmin)} minutes",
              f"the same stream: D = {D:+g} s", "`results/test_results.csv`, `label_drift_s`"))
    nv = [r for (ext, _, _), rows in ts.cells.items() if ext == PRIMARY for v, r in rows.items() if v == ids["NV_ID"]]
    if not nv or nv[0].get("hum_present_v1_full") != "False" or any(r.get("verdict") != "no verdict" for r in nv):
        raise FillError(f"the no-hum example {ids['NV_ID']} of video/make_video.py has a hum flag or a verdict "
                        f"in test_results.csv")
    R.append((10, "no hum, so Gunj gives no verdict", "no hum, no verdict",
              f"Test stream {ids['NV_ID']}: M1 hum flag off, and no verdict in any of its {len(nv)} v2 searches",
              "`results/test_results.csv`"))
    m3 = ts.m3()
    real = m3["real"]["false_match"]
    mc = ev["m3_counts"]
    n_real, _ = report_half(src)
    R.append((10, f"On {words(real['n'])} hour-long recordings from other grids, it gave {words(real['k'])} false "
                  f"{plural(real['k'], 'match', 'matches')}",
              f"{words(real['n'])} recordings from other grids, {words(real['k'])} false "
              f"{plural(real['k'], 'match', 'matches')}",
              f"{rate_of(real)} real foreign 50 Hz streams at or above tau (v2, 60 min, full span); SYNTHETIC "
              f"negatives {rate_of(m3['SYNTHETIC']['false_match'])}; {mc['real']} of {n_real} real report-half "
              f"negatives were harvested", "`results/evaluation.json` (`m3`, `m3_counts`)"))
    return R


def b_narration_sources(src, ts) -> str:
    lines = {n: text for n, _, text in narration(src)["lines"]}
    rows = narration_rows(src, ts)
    for line, phrase, _, _, source in rows:
        if line is None:
            continue
        if line not in lines:
            raise FillError(f"{NARRATION} has no line {line}")
        if phrase not in lines[line]:
            raise FillError(f"{NARRATION} line {line} should say \"{phrase}\" (from {source.replace('`', '')}); "
                            f"it says: {lines[line]}")
    L = ["| line | said | value | source |", "|---|---|---|---|"]
    for line, _, said, value, source in rows:
        L.append(f"| {line if line is not None else ''} | {said} | {value} | {source} |")
    return "\n".join(L)


def b_narration_words(src, ts) -> str:
    return str(sum(len(text.split()) for _, _, text in narration(src)["lines"]))


def b_vo(n: int):
    def block(src, ts) -> str:
        for k, _, text in narration(src)["lines"]:
            if k == n:
                return text
        raise FillError(f"{NARRATION} has no line {n}")
    return block


# ----------------------------------------------------------------- small blocks

def b_span(src, ts) -> str:
    f = ref_facts(src)
    return f"{day_text(f['t0'])} to {day_text(f['t1'])}"


def b_span_end(src, ts) -> str:
    return day_text(ref_facts(src)["t1"])


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


def b_gate_count(src, ts) -> str:
    gate, _, _ = ts.gate()
    if gate is None:
        return PENDING
    p = gate["by_extractor"][PRIMARY]
    return (f"{p['n_streams_with_lock']} of N = {gate['N_processed']} Test streams locking blind "
            f"({p['n_streams_with_lock_D0']} with D = 0)")


def b_harvest_count(src, ts) -> str:
    if not ts.show:
        return PENDING
    h = harvest_facts(ts)
    return f"{h['with_trace']} of the {h['selected']}"


def b_harvest_losses(src, ts) -> str:
    if not ts.show:
        return PENDING
    h = harvest_facts(ts)
    man = {r["video_id"]: r for r in (src.csv("data/harvest_manifest.csv") or []) if r.get("split") == "test"}
    open_ = bool(h["failed_ids"]) and all(man.get(v, {}).get("retryable") == "True" for v in h["failed_ids"])
    retry = " and could still have been retried when the harvest stopped (`data/harvest_manifest.csv`)" if open_ else ""
    parts = [(h["unavailable"], "{n} {were} no longer available on YouTube"),
             (h["failed"], "{n} failed in download or conversion" + retry),
             (h["not_attempted"], "{n} had not been attempted"),
             (h["blocked"], "{n} met the bot check"),
             (h["other"], "{n} {were} dropped for another reason")]
    said = [t.format(n=n, were=plural(n, "was", "were")) for n, t in parts if n]
    body = said[0] if len(said) == 1 else ", ".join(said[:-1]) + " and " + said[-1]
    return f"Of the other {h['lost']}, {body} (`results/flow_table.md`)"


def b_bot_check(src, ts) -> str:
    if not ts.show:
        return PENDING
    b = block_facts(ts)
    n = len(b["rows"])
    return (f"{n} bot check" + ("" if n == 1 else "s") + (f" ({b['when']})" if b["when"] and n else ""))


def b_neg_retry(src, ts) -> str:
    if not ts.show:
        return PENDING
    n_real, _ = report_half(src)
    return f"{ts.ev['m3_counts']['real']} of the {n_real}"


def b_lim_hum(src, ts) -> str:
    if not ts.show:
        return PENDING
    return fmt_rate(*ts.m1())


def b_lim_confident(src, ts) -> str:
    if not ts.show:
        return PENDING
    m2 = ts.m2()
    bd = breakdown_facts(src, ts)
    cw = m2["confident_wrong"]
    share = (f"{100 * cw['rate']:.1f}% of the {cw['n']} excerpts (95% CI {100 * cw['wilson95'][0]:.1f} to "
             f"{100 * cw['wilson95'][1]:.1f}%)")
    if bd is None:
        return f"In the primary cell {cw['k']} confident verdicts are more than 30 s from T*, which is {share}"
    c = bd["cells"][PRIMARY]["60"]["full"]["confident"]
    return (f"In the primary cell {c['n'] - c['within_30']} of the {c['n']} confident verdicts are more than 30 s "
            f"from T*, which is {share}: {c['from_30_to_120']} by 30 to 120 s, {c['from_120_to_600']} by 120 to "
            f"600 s and {c['beyond_600']} by more than 600 s")


def b_lim_false_match(src, ts) -> str:
    if not ts.show:
        return PENDING
    m3 = ts.m3()
    real, tog = m3["real"]["false_match"], m3["together"]["false_match"]
    syn = m3["SYNTHETIC"]["false_match"]
    if not real["n"]:
        return f"No real report-half negative was scored; the SYNTHETIC ones give {rate_of(syn)}"
    target = ("above the 1% target" if real["k"] / real["n"] > 0.01 else
              "which cannot show a rate below the 1% target with so few negatives")
    return (f"In the primary cell {real['k']} of the {real['n']} real foreign 60-min excerpts reached tau "
            f"({100 * real['rate']:.1f}%, 95% CI {100 * real['wilson95'][0]:.1f} to {100 * real['wilson95'][1]:.1f}%), "
            f"{target}; with the {syn['n']} SYNTHETIC ones added the count is {rate_of(tog)}")


def b_lim_m5(src, ts) -> str:
    if not ts.show:
        return PENDING
    m5 = m5_facts(src, ts)
    if m5 is None:
        return "not computed (run `scripts/m5_edits.py`)"
    by = m5["preregistered"]["totals"]["by_cut_length_s"]
    tot = m5["preregistered"]["totals"]["all"]
    shortest = min(by, key=int)
    d = by[shortest]["detected"]
    return (f"{rate_of(tot['detected'])} of the SYNTHETIC cuts were detected, and "
            f"{'none' if d['k'] == 0 else d['k']} of the {d['n']} SYNTHETIC cuts of {shortest} s")


def b_lim_m4(src, ts) -> str:
    if not ts.show:
        return PENDING
    mm = m4m_facts(src, ts)
    if mm is None:
        return "not computed (run `scripts/make_figures.py`)"
    ra, rb = (mm["readings"][k]["median"]["hann16"]["full"] for k in ("A", "B"))
    return (f"{ra['shortest_length_min']} min at {ra['sigma_mhz']:.1f} mHz (the correlation as scored) and "
            f"{rb['shortest_length_min']} min at {rb['sigma_mhz']:.1f} mHz (the unweighted correlation)")


# ----------------------------------------------------------------- headline and status

def b_headline(src, ts) -> str:
    if not ts.show:
        return f"- Test results: {PENDING}."
    m2 = ts.m2()
    gate, _, _ = ts.gate()
    h = harvest_facts(ts)
    dr = drift_facts(src)
    g = src.need("results/splits_summary.json")["analytic_random_guess"]["full_60min"]
    k1, n1 = ts.m1()
    L = []
    if ts.tag:
        L.append(f"- **{ts.tag}: for checking only.**")
    stop = (" before a YouTube bot check stopped the downloads" if block_facts(ts)["rows"] else "")
    L.append(f"- **Blind, pre-registered test.** The plan was committed on 7 Nov 2026, before any Test audio existed. "
             f"{h['with_trace']} of the {h['selected']} selected public Indian livestreams were harvested{stop}, "
             f"and every one of them is reported.")
    L.append(f"- **Primary endpoint** (extractor v2, 60-min excerpts, search over the whole {ref_months(src)}-month "
             f"log): the top candidate starts within 30 s of the expected time for "
             f"{prose_rate(m2['top1_hit'], 'streams')}.")
    hp = m2["top1_hit_hum_present"]
    L.append(f"- **Where hum is present** ({k1} of {n1} streams, {100 * k1 / n1:.1f}%), the same endpoint is "
             f"{hp['k']} of the {hp['n']} such streams with a 60-min excerpt ({100 * hp['rate']:.1f}%, 95% CI "
             f"{100 * hp['wilson95'][0]:.1f} to {100 * hp['wilson95'][1]:.1f}%).")
    p = gate["by_extractor"][PRIMARY]
    L.append(f"- **Label-drift correction.** The grid log's own time labels run late by up to "
             f"{secs_text(dr['max_last'])}. Without Gunj's per-stream correction the primary endpoint falls to "
             f"{prose_rate(m2['top1_hit_D0'])}, and the streams that lock blind at the gate fall from "
             f"{p['n_streams_with_lock']} to {p['n_streams_with_lock_D0']} of {gate['N_processed']}.")
    bd = breakdown_facts(src, ts)
    nv = m2["no_verdict"]["k"]
    if bd is None:
        L.append(f"- **Confident verdicts.** {m2['confident_hit']['k']} are within 30 s and "
                 f"{m2['confident_wrong']['k']} are further off; the other {nv} excerpts get no verdict.")
    else:
        c = bd["cells"][PRIMARY]["60"]["full"]["confident"]
        far = far_verdicts(ts)
        far_txt = (" (" + " and ".join(dur_text(e) for e, _ in far) + ")") if far else ""
        be = lambda n: plural(n, "is", "are")
        L.append(f"- **Confident verdicts.** Gunj gives a verdict for {c['n']} of the {m2['n']} and none for the "
                 f"other {nv}. Of the {c['n']}, {c['within_30']} {be(c['within_30'])} within 30 s, "
                 f"{c['from_30_to_120']} {be(c['from_30_to_120'])} 30 to 120 s off, {c['from_120_to_600']} "
                 f"{be(c['from_120_to_600'])} 120 to 600 s off, and {c['beyond_600']} {be(c['beyond_600'])} wrong by "
                 f"more than 10 minutes{far_txt}.")
    m3 = ts.m3()
    real, syn = m3["real"]["false_match"], m3["SYNTHETIC"]["false_match"]
    if real["n"]:
        verdict = ("The pre-registered target of at most 1% is not met." if real["k"] / real["n"] > 0.01 else
                   "So few negatives cannot show the pre-registered target of at most 1%.")
        L.append(f"- **False matches.** Searched the same way, {real['k']} of {real['n']} real foreign 50 Hz "
                 f"recordings {plural(real['k'], 'reaches', 'reach')} the threshold ({100 * real['rate']:.1f}%, "
                 f"95% CI {100 * real['wilson95'][0]:.1f} to {100 * real['wilson95'][1]:.1f}%), and {syn['k']} of "
                 f"{syn['n']} SYNTHETIC negatives. {verdict}")
    else:
        L.append(f"- **False matches.** No real foreign recording was scored; {syn['k']} of {syn['n']} SYNTHETIC "
                 f"negatives reach the threshold.")
    v1, nv_ = ts.m2("v1")["top1_hit"], ts.m2("naive")["top1_hit"]
    L.append(f"- **Chance and baselines.** A random guess lands within 30 s with probability {sci(g['p_hit'])} per "
             f"stream (1 in {num(round(g['one_in']))}). On the same {m2['n']} streams the frozen v1 extractor puts "
             f"{v1['k']} within 30 s and a naive single-bin 50 Hz extractor {nv_['k']}.")
    L += ["", "Each number above is written by `scripts/fill_results.py`; Results names the file behind it."]
    return "\n".join(L)


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
        line = (f"- Test numbers are from `results/evaluation.json` and `results/test_results.csv`, last written by "
                f"`scripts/evaluate.py` at {ist_text(ev.get('written_ist'))}: {ev['n_with_trace']} of "
                f"{ev['n_selected']} selected streams have a trace.")
        t = ev.get("timing") or {}
        neg = t.get("negatives") or {}
        if "streams_cached" in t and "streams_to_search" in t and "streams_to_search" in neg:
            again = (f"and searched {t['streams_to_search']} more" if t["streams_to_search"] else
                     "and searched none of them again")
            line += (f" That run took {t['streams_cached']} Test streams from the search cache {again}, and it "
                     f"searched {neg['streams_to_search']} negative {plural(neg['streams_to_search'], 'stream')} "
                     f"(`timing` in the same file).")
        if protocol_says(src, "No further YouTube download is planned"):
            line += " No further download is planned (PROTOCOL section 19), so these numbers are final."
        else:
            line += " The harvest can still add streams; `scripts/fill_results.py` re-reads the files each time it runs."
        L.append(line)
        m5 = src.eval_json("m5.json")
        if m5 is not None or src.eval_json("error_breakdown.json") is not None:
            when5 = f" (run of {ist_text(m5.get('written_ist'))})" if m5 else ""
            L.append(f"- M5 (on SYNTHETIC cuts), the confident-verdict breakdown, M4 at the measured noise level and "
                     f"the figures were computed after the Test results were known, by `scripts/m5_edits.py`{when5} and "
                     f"`scripts/make_figures.py`. They read the scoring outputs and change none of them. Anything "
                     f"PROTOCOL.md did not pre-register is labelled EXPLORATORY (post hoc).")
    L.append("- The reference, label-drift, latency, threshold, Dev and uniqueness results were measured before "
             "any Test search and do not change.")
    return "\n".join(L)


# ----------------------------------------------------------------- the results block

def figure(src, name: str, alt: str, caption: str) -> list[str]:
    """Markdown for one figure of scripts/make_figures.py with its caption, or a note that it is not made yet."""
    if not (src.eval_dir / "figures" / name).exists():
        return [f"Figure `{FIG_DIR}/{name}` is not made yet: run `scripts/make_figures.py`.", ""]
    return [f"![{alt}]({FIG_DIR}/{name})", "", f"Figure: {caption}", ""]


def details(summary: str, body: list[str]) -> list[str]:
    return ["<details>", f"<summary>{summary}</summary>", ""] + body + ["</details>", ""]


def _m2_grid(ts, key: str) -> list[str]:
    L = ["| excerpt | " + " | ".join(SCOPE_LABEL[s] for s in SCOPES) + " |", "|---|---|---|---|"]
    for m in LENGTHS:
        L.append(f"| {m} min | " + " | ".join(rate_of(ts.m2(PRIMARY, m, s)[key]) for s in SCOPES) + " |")
    return L


def _sec_gate(src, ts) -> list[str]:
    gate, gsrc, when = ts.gate()
    L = ["### Gate (PROTOCOL section 15)", ""]
    if gate is None:
        L.append(f"- {PENDING}. `scripts/evaluate.py` writes `results/gate_frozen.json` on its first run that starts "
                 "after the decision time with every Test stream harvested by then searched.")
        return L + [""]
    p = gate["by_extractor"][PRIMARY]
    if ts.tag:
        L.append(f"- **{ts.tag}**")
    L.append(f"- **{'Gate met' if gate['gate_met'] else 'Gate not met'}** ({when}, `{gsrc}`).")
    L.append(f"- Conditions 1 and 2: {p['n_streams_with_lock']} of N = {gate['N_processed']} Test streams "
             f"harvested by the decision time locked blind (5 needed): extractor v2, top candidate in the full "
             f"span or ±90 days within 30 s of T* for at least one excerpt length, with confidence at or above "
             f"tau of that cell. {'Met' if gate['condition_1_2_met'] else 'Not met'}.")
    L.append(f"- The same count without the label-drift correction (S1, D = 0): {p['n_streams_with_lock_D0']}.")
    c3 = f"- Condition 3, harvest health: {'met' if gate['condition_3_met'] else 'not met'}."
    b = block_facts(ts)
    if not gate["condition_3_met"] and b["rows"]:
        c3 += (f" YouTube answered {len(b['rows'])} {plural(len(b['rows']), 'stream')} ({', '.join(b['rows'])}) "
               f"with a bot check" + (f" on {b['when']}" if b["when"] else "")
               + ", and the harvester stopped as PROTOCOL section 17 requires.")
    c3 += f" tau and L committed by the decision time: {'yes' if gate['tau_L_committed_in_time'] else 'no'}."
    L.append(c3)
    others = "; ".join(f"{e} {gate['by_extractor'][e]['n_streams_with_lock']}" for e in EXTRACTORS if e != PRIMARY)
    L.append(f"- Locks with the baselines on the same streams (description only): {others}.")
    if not gate["gate_met"] and protocol_says(src, "the decision to stay with Gunj"):
        L.append("- PROTOCOL section 15 says to switch projects when the gate is not met. Shrey decided to stay with "
                 "Gunj; PROTOCOL section 19 (8 Nov 2026) and Deviations below give the reasons.")
    L.append("- `results/gate.md` lists every processed stream, hits and misses.")
    return L + [""]


def _sec_table(src, ts, m5) -> list[str]:
    P, show = PENDING, ts.show
    L = ["### Test split, pre-registered and blind", ""]
    if ts.tag and show:
        L += [f"**{ts.tag}**", ""]
    L += ["| result | value | source |", "|---|---|---|"]
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
    if m5 is not None:
        pre = m5["preregistered"]
        lens = sorted(int(x) for x in pre["totals"]["by_cut_length_s"])
        rows.append((f"M5 edit detection on SYNTHETIC cuts of {lens[0]} to {lens[-1]} s: detected within 30 s of "
                     f"the true position", rate_of(pre["totals"]["all"]["detected"]), "`results/m5.json`"))
        rows.append(("M5 false alarms (the cuts are SYNTHETIC): uncut excerpts of the same streams with an edit "
                     "reported", rate_of(pre["false_alarms"]["excerpts_with_a_reported_edit"]), "`results/m5.json`"))
    else:
        rows.append(("M5 edit detection on SYNTHETIC cuts",
                     "not computed: run `scripts/m5_edits.py`" if show else P, "PROTOCOL section 11"))
    st = other_results(src, ("station",))
    rows.append(("M6 live station (optional)", f"see `results/{st[0]}`" if st else "not run",
                 "PROTOCOL section 11"))
    for a, b, c in rows:
        L.append(f"| {a} | {b} | {c} |")
    return L + [""]


def _sec_m2(src, ts, bd) -> list[str]:
    L = ["### M2 top-1 within 30 s, extractor v2 (Wilson 95%; `results/evaluation.json`)", ""]
    L += _m2_grid(ts, "top1_hit") + [""]
    L += ["Confident wrong (verdict match, more than 30 s off), same cells:", ""]
    L += _m2_grid(ts, "confident_wrong") + [""]
    m2 = ts.m2()
    errs = [abs(int(float(r["error_s"]))) for r in ts.cells.get((PRIMARY, 60, "full"), {}).values()
            if r.get("error_s", "") != ""]
    L += figure(src, f"m2_signed_error_{PRIMARY}.png",
                "Signed error of the top candidate for every excerpt length and scope, extractor v2",
                f"look at the bottom-left panel, the primary cell (60 min, full span): the green bars inside the "
                f"±30 s band are its {m2['top1_hit']['k']} hits, and {sum(e > 86400 for e in errs)} of the other top "
                f"candidates are more than a day from T* (`results/test_results.csv`, `error_s`).")
    body = []
    for ext, name in (("v1", "the frozen extractor v1"), ("naive", "the naive single-bin 50 Hz extractor")):
        d = ts.m2(ext)
        body += figure(src, f"m2_signed_error_{ext}.png", f"Signed error of the top candidate, {name}",
                       f"look at the same bottom-left panel for {name}: {d['top1_hit']['k']} of {d['n']} top "
                       f"candidates fall in the green ±30 s band.")
    L += details("The same plot for the two baseline extractors", body)
    return L


def _sec_breakdown(src, ts, bd) -> list[str]:
    L = ["### How far off the top candidate is (coarse hits, PROTOCOL section 9)", ""]
    if bd is None:
        return L + ["Not computed: `results/error_breakdown.json` is missing. Run `scripts/make_figures.py`.", ""]
    cell = bd["cells"][PRIMARY]["60"]["full"]
    c, t = cell["confident"], cell["top1"]
    far = far_verdicts(ts)
    if len(far) != c["beyond_600"]:
        raise FillError("the verdicts beyond 600 s in test_results.csv do not match error_breakdown.json")
    L += ["A hit is a top candidate within 30 s of T*, and nothing wider is ever counted as a hit. PROTOCOL section 9 "
          "also pre-registered two coarser distances, 120 s and 600 s, to describe the label problem. The table "
          "splits the primary cell (v2, 60 min, full span) by them. Source: `results/error_breakdown.json`, written "
          "by `scripts/make_figures.py` from `results/test_results.csv`; this script recounts every cell from the "
          "CSV before it copies a number.", ""]
    L += ["| excerpts | n | within 30 s (hit) | 30 to 120 s | 120 to 600 s | beyond 600 s | no candidate |",
          "|---|---|---|---|---|---|---|"]
    for lab, d in (('verdict "match" (confident)', c), ("all, whatever the verdict", t)):
        L.append(f"| {lab} | {d['n']} | {d['within_30']} | {d['from_30_to_120']} | {d['from_120_to_600']} | "
                 f"{d['beyond_600']} | {d['no_candidate']} |")
    L.append("")
    L.append(f"- Confident verdicts, cumulative: within 30 s {rate_of(c['within_30_share'])}; within 120 s "
             f"{rate_of(c['within_120_share'])}; within 600 s {rate_of(c['within_600_share'])}.")
    if far:
        L.append(f"- The {len(far)} confident {plural(len(far), 'verdict')} beyond 600 s "
                 f"{plural(len(far), 'is', 'are')} off by "
                 + " and ".join(f"{num(e)} s ({dur_text(e)}, stream {v})" for e, v in far)
                 + " (`results/test_results.csv`, `error_s`).")
    L.append(f"- Every confident verdict more than 30 s from T* counts as confident wrong, whatever its class: "
             f"{c['n'] - c['within_30']} of the {c['n']}.")
    L.append(f"- All top candidates, cumulative: within 30 s {rate_of(t['within_30_share'])}; within 120 s "
             f"{rate_of(t['within_120_share'])}; within 600 s {rate_of(t['within_600_share'])}. \"No candidate\" "
             f"means the search could not run (a constant trace); such an excerpt stays in the denominator.")
    L += ["", "Confident verdicts by excerpt length over the full span. tau differs by cell (PROTOCOL section 10), "
              "so a longer excerpt is also judged against another threshold.", "",
          "| excerpt | tau | confident verdicts | within 30 s | 30 to 120 s | 120 to 600 s | beyond 600 s |",
          "|---|---|---|---|---|---|---|"]
    for m in LENGTHS:
        d = bd["cells"][PRIMARY][str(m)]["full"]
        x = d["confident"]
        L.append(f"| {m} min | {ts.m2(PRIMARY, m, 'full')['tau']:.4f} | {x['n']} | {x['within_30']} | "
                 f"{x['from_30_to_120']} | {x['from_120_to_600']} | {x['beyond_600']} |")
    L += ["", "Every cell of every extractor is in `results/error_breakdown.md`.", ""]
    L += figure(src, f"confident_verdict_breakdown_{PRIMARY}.png",
                "Confident verdicts and all top candidates by distance from T*, extractor v2",
                f"look at the 60-min, full-span bar of the top panel: of its {c['n']} confident verdicts "
                f"{c['within_30']} {plural(c['within_30'], 'is', 'are')} green (within 30 s), {c['from_30_to_120']} "
                f"{plural(c['from_30_to_120'], 'is', 'are')} 30 to 120 s off, {c['from_120_to_600']} "
                f"{plural(c['from_120_to_600'], 'is', 'are')} 120 to 600 s off and {c['beyond_600']} "
                f"{plural(c['beyond_600'], 'is', 'are')} beyond 600 s.")
    return L


def _sec_baselines(src, ts) -> list[str]:
    L = ["### Baselines on the same streams, 60 min, full span", ""]
    L += ["| method | top-1 within 30 s | confident hit | confident wrong | paired with v2 on the same streams (both hit / v2 only / baseline only / neither) |",
          "|---|---|---|---|---|"]
    for ext in EXTRACTORS:
        c = ts.m2(ext)
        pr = "" if ext == PRIMARY else (lambda q: f"{q['both']} / {q['v2_only']} / {q['base_only']} / "
                                                  f"{q['neither']} (n = {q['n']})")(ts.paired(ext))
        name = {"v2": "v2 (primary)", "v1": "v1 (frozen extractor)", "naive": "naive single-bin 50 Hz"}[ext]
        L.append(f"| {name} | {rate_of(c['top1_hit'])} | {rate_of(c['confident_hit'])} | "
                 f"{rate_of(c['confident_wrong'])} | {pr} |")
    L.append(f"| libhum | {libhum_status(src)} | | | |")
    g = src.need("results/splits_summary.json")["analytic_random_guess"]["full_60min"]
    n60 = ts.m2()["n"]
    L.append(f"| random guess (analytic) | p = {sci(g['p_hit'])} per stream; expected chance hits over these "
             f"{n60} streams: {sci(n60 * g['p_hit'], 2)} | | | |")
    L += ["", "Sources: `results/evaluation.json` (rates), `results/test_results.csv` (pairs), "
              "`results/splits_summary.json` (random guess). No significance test is run (PROTOCOL section 13).", ""]
    return L


def _sec_sensitivity(src, ts) -> list[str]:
    L = ["### Sensitivity analyses (PROTOCOL section 13), v2, 60 min, full span", ""]
    k2, n2 = ts.hits(keep=lambda r: r.get("label_stable") == "True")
    k3, n3 = ts.hits(keep=lambda r: r.get("channel_status") == VERIFIED)
    L += ["| analysis | top-1 within 30 s | source |", "|---|---|---|",
          f"| as pre-registered (T* = release time + L + D) | {rate_of(ts.m2()['top1_hit'])} | `results/evaluation.json` |",
          f"| S1: no label-drift correction (D = 0) | {rate_of(ts.m2()['top1_hit_D0'])} | `results/evaluation.json` |",
          f"| S2: label-stable streams only | {fmt_rate(k2, n2)} | `results/test_results.csv`, `label_stable` |",
          f"| S3: verified channels only | {fmt_rate(k3, n3)} | `results/test_results.csv`, `channel_status` |",
          "| S4: v1 against v2 | see the paired column above | `results/test_results.csv` |", ""]
    return L


def _sec_m1(src, ts) -> list[str]:
    L = ["### M1 hum presence by venue type (`results/test_results.csv`)", ""]
    venues = sorted({r.get("venue_type") or "unknown" for r in ts.streams.values()})
    L += ["| venue type | hum present |", "|---|---|"]
    for v in venues:
        kv, nv = ts.m1(keep=lambda r, v=v: (r.get("venue_type") or "unknown") == v)
        if nv:
            L.append(f"| {v} | {fmt_rate(kv, nv)} |")
    k, n = ts.m1()
    L += [f"| all | {fmt_rate(k, n)} |", ""]
    return L


def _sec_m3(src, ts) -> list[str]:
    mc = ts.ev["m3_counts"]
    n_real, n_syn = report_half(src)
    L = ["### M3: false matches on the report-half negatives (PROTOCOL section 11)", ""]
    L += [f"A false match is a report-half negative excerpt whose confidence reaches tau for its cell. "
          f"{mc['real']} of the {n_real} real foreign 50 Hz streams and {mc['SYNTHETIC']} of the {n_syn} SYNTHETIC "
          f"items were searched (`results/evaluation.json`, `m3_counts`). A negative has an excerpt in a cell only "
          f"if its stream is long enough. tau was set on the threshold half, which shares no stream with the report "
          f"half. Extractor v2, Wilson 95% (`results/evaluation.json`, `m3`):", ""]
    L += ["| excerpt | scope | tau | real foreign 50 Hz streams | SYNTHETIC noise and hum | real and SYNTHETIC together |",
          "|---|---|---|---|---|---|"]
    hits = {"real": {}, "SYNTHETIC": {}}
    cells_hit = {"real": 0, "SYNTHETIC": 0}
    same_with_flag, uppers = True, []
    for m in LENGTHS:
        for s in SCOPES:
            c = ts.m3(PRIMARY, m, s)
            L.append(f"| {m} min | {SCOPE_LABEL[s]} | {c['tau']:.4f} | {rate_of(c['real']['false_match'])} | "
                     f"{rate_of(c['SYNTHETIC']['false_match'])} | {rate_of(c['together']['false_match'])} |")
            for kind in ("real", "SYNTHETIC", "together"):
                if c[kind]["false_match"]["k"] != c[kind]["false_match_with_hum_flag"]["k"]:
                    same_with_flag = False
            for kind in ("real", "SYNTHETIC"):
                cells_hit[kind] += 1 if c[kind]["false_match"]["k"] else 0
            if c["together"]["false_match"]["n"]:
                uppers.append(c["together"]["false_match"]["wilson95"][1])
            for t in c["trials"]:
                if t.get("false_match") and t.get("kind") in hits:
                    hits[t["kind"]].setdefault(t["video_id"], []).append(f"{m} min {SCOPE_LABEL[s]}")
    L.append("")
    real = ts.m3()["real"]["false_match"]
    if real["n"]:
        met = "not met" if real["k"] / real["n"] > 0.01 else "not contradicted"
        L.append(f"- Target: at most 1% (PROTOCOL section 11). In the primary cell it is {met}: {rate_of(real)} real "
                 f"negatives at or above tau." + (f" No cell has enough negatives to show a rate below 1%: the "
                 f"smallest Wilson upper bound, real and SYNTHETIC together, is {100 * min(uppers):.1f}%." if uppers else ""))
    for kind, lab in (("real", "Real negatives"), ("SYNTHETIC", "SYNTHETIC negatives")):
        if hits[kind]:
            L.append(f"- {lab} at or above tau in some v2 cell: "
                     + "; ".join(f"{v} ({', '.join(where)})" for v, where in hits[kind].items()) + ".")
        else:
            L.append(f"- {lab} at or above tau in some v2 cell: none.")
    L.append("- With the hum-flag condition added, the v2 counts are "
             + ("the same in every cell." if same_with_flag else "lower in some cells; see `results/false_matches.md`.")
             + " v1 and the naive extractor are in `results/false_matches.md`.")
    L.append("")
    rel = reliability_facts(src, ts)
    if rel is None:
        return L + ["Reliability table not computed: run `scripts/make_figures.py`.", ""]
    t, ng = rel["test"], rel["negatives"]
    L += ["Reliability of the confidence score in the primary cell: Test top-1 hit rate by the pre-registered "
          "confidence bins (`results/figures/figures.json`, written by `scripts/make_figures.py`).", "",
          "| confidence bin | Test excerpts | top-1 within 30 s |", "|---|---|---|"]
    for b in t["bins"]:
        L.append(f"| {b['bin']} | {b['n']} | {rate_of(b)} |")
    hi = [b for b in t["bins"] if b["lo"] >= 0.1]
    L += ["", f"{t['n_without_confidence']} of the {t['n_excerpts']} excerpts "
              f"{plural(t['n_without_confidence'], 'has', 'have')} no confidence because the search could not run. "
              f"In this cell the largest confidence of a real negative is {max(ng['real']['confidence']):.4f} and of "
              f"a SYNTHETIC negative {max(ng['SYNTHETIC']['confidence']):.4f}; tau is {ng['tau']:.4f}.", ""]
    L += figure(src, "m3_reliability_v2_60min_full.png",
                "M3 reliability plot: Test top-1 hit rate by confidence bin, with the negatives on the same axis",
                f"look at the dashed line at tau: Test excerpts with confidence below {t['bins'][0]['hi']:g} hit in "
                f"{t['bins'][0]['k']} of {t['bins'][0]['n']} cases, those at {hi[0]['lo']:g} or above hit in "
                f"{sum(b['k'] for b in hi)} of {sum(b['n'] for b in hi)}, and {ng['real']['n_at_or_above_tau']} of "
                f"the {ng['real']['n']} real negatives (filled grey dots) "
                f"{plural(ng['real']['n_at_or_above_tau'], 'lies', 'lie')} to the right of it.")
    L += details("The reliability plot for all 12 cells of v2",
                 figure(src, "m3_reliability_v2_all_cells.png", "M3 reliability plot for every cell of extractor v2",
                        f"look at the grey dots under each panel against the dashed tau line: a real negative "
                        f"(filled) reaches tau in {cells_hit['real']} of the {len(LENGTHS) * len(SCOPES)} cells and "
                        f"a SYNTHETIC negative (open) in {cells_hit['SYNTHETIC']}."))
    return L


def _sec_m5(src, ts, m5) -> list[str]:
    L = ["### M5: edit detection on SYNTHETIC cuts (PROTOCOL section 11)", ""]
    if m5 is None:
        return L + ["Not computed: `results/m5.json` is missing. Run `scripts/m5_edits.py`; the SYNTHETIC cut traces "
                    "are in `traces/test`.", ""]
    pre = m5["preregistered"]
    by, tot, fa = pre["totals"]["by_cut_length_s"], pre["totals"]["all"], pre["false_alarms"]
    lens = sorted(by, key=int)
    per = pre["n_cuts"] / pre["n_streams"] if pre["n_streams"] else 0
    each = f", {int(per)} SYNTHETIC cuts each" if per == int(per) and per else f", {pre['n_cuts']} SYNTHETIC cuts in all"
    L += [f"Every cut is SYNTHETIC. At harvest time a stretch of {lens[0]} to {lens[-1]} s was removed from the "
          f"60-min excerpt audio, the cut audio was extracted with v2, and the audio was deleted. No recording with "
          f"a real edit was tested. M5 uses the {pre['n_streams']} Test streams whose uncut 60-min excerpt is a "
          f"confident hit over the full span{each}. Each SYNTHETIC cut trace is located in the ±1-day scope and "
          f"passed to `find_edits` with its frozen defaults. A detection is a reported cut within 30 s of the true "
          f"position of the SYNTHETIC cut. Source: `results/m5.json`, written by `scripts/m5_edits.py` at "
          f"{ist_text(m5.get('written_ist'))}.", ""]
    L += ["| SYNTHETIC cut length | SYNTHETIC cuts | detected | duration error of the detected SYNTHETIC cuts, median / max |",
          "|---|---|---|---|"]

    def err(d):
        e = d["duration_error_s"]
        return "n/a (none detected)" if not e["n"] else f"{secs_text(e['median'])} / {secs_text(e['max'])}"
    for s in lens:
        L.append(f"| SYNTHETIC, {s} s | {by[s]['n_cuts']} | {rate_of(by[s]['detected'])} | {err(by[s])} |")
    L.append(f"| all SYNTHETIC cuts | {tot['n_cuts']} | {rate_of(tot['detected'])} | {err(tot)} |")
    L += ["", f"- False alarms (SYNTHETIC test): `find_edits` reported an edit on "
              f"{rate_of(fa['excerpts_with_a_reported_edit'])} of the uncut excerpts of the same streams, "
              f"{fa['edits_reported']} {plural(fa['edits_reported'], 'edit')} in all.",
          "- Every SYNTHETIC cut with its reported position and jump, and the readings chosen where PROTOCOL "
          "section 11 leaves room, are in `results/m5.md`.", ""]
    return L


def _sec_flow(src, ts) -> list[str]:
    L = ["### Flow, Test streams (`results/evaluation.json`; drop reasons in `results/flow_table.md`)", ""]
    L += ["| step | streams |", "|---|---|"]
    for s in ts.ev["flow"]["steps"]:
        L.append(f"| {s['step']} | {s['n']} |")
    return L + [""]


def _sec_pretest(src) -> list[str]:
    rf, dr, lf, tf, dv = ref_facts(src), drift_facts(src), latency_facts(src), tau_facts(src), dev_facts(src)
    L = ["### Measured before any Test search", ""]
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
    return L + [""]


def _m4_table(src) -> list[str]:
    f = m4_facts(src)
    L = [f"Excerpts of the real reference, with SYNTHETIC noise added and no audio, searched like a recording. "
         f"The table below was read before any Test search, at {f['level']:g} mHz, the tested level nearest the "
         f"5 Nov probe's median equivalent trace noise of {f['probe_mhz']:.1f} mHz (`results/probe_noise.json`). "
         f"`white` is independent each second; `hann16` is smoothed over 16 s like real extractor error, so it is "
         f"the closer stand-in. Source: `results/uniqueness_table.csv`; all levels are plotted in "
         f"`results/uniqueness_curve.png`.", "",
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
            per = ((", ".join(r["hits"] for r in c["rows"]) + f" of {ns[0]}") if len(ns) == 1 else
                   ", ".join(f"{r['hits']}/{r['N']}" for r in c["rows"]))
            L.append(f"| {SCOPE_LABEL[scope]} | {kind} | {shortest} | {rate} | {per} |")
    return L


def _m4_measured(src, ts) -> list[str]:
    if not ts.show:
        return []
    mm = m4m_facts(src, ts)
    if mm is None:
        return ["", "M4 at the measured noise level is not computed: run `scripts/make_figures.py` "
                    "(PROTOCOL section 11).", ""]
    dist = mm["distribution_hz"]

    def entry(reading, scope):
        r = mm["readings"][reading]["median"]["hann16"][scope]
        if r["status"] != "read":
            return r["status"]
        m = r["shortest_length_min"]
        return f"{m} min ({100 * r['rates_by_length_min'][str(m)]['rate']:.1f}%)"
    labels = {"A": "A: r as scored (v2's weighted correlation)", "B": "B: unweighted r of the same traces"}
    L = ["", f"**M4 at the measured noise level.** PROTOCOL section 11 reads the curve at the equivalent trace noise "
             f"of the Test streams that lock, computed as in `scripts/probe_noise.py`. For the "
             f"{mm['streams_that_lock']['n']} streams the gate counts as locks ({mm['streams_that_lock']['n_excerpts']} "
             f"locking excerpts, one median per stream) the noise depends on which correlation r is used. The "
             f"protocol does not say which, and the simulation's matcher is unweighted, so both readings are "
             f"reported and neither is preferred. Source: `results/m4_measured.json` (`scripts/make_figures.py`); "
             f"SYNTHETIC `hann16` noise, rates interpolated linearly between the tested noise levels.", "",
         "| reading | median noise over streams (Q1 to Q3) | full span | ±90 days | ±1 day |", "|---|---|---|---|---|"]
    for k in ("A", "B"):
        d = dist[k]
        L.append(f"| {labels[k]} | {mhz(d['median'])} mHz ({mhz(d['q1'])} to {mhz(d['q3'])}) | "
                 + " | ".join(entry(k, s) for s in SCOPES) + " |")
    tested = ", ".join(str(x) for x in mm["uniqueness_lengths_min"])
    L += ["", f"Each entry is the shortest tested length ({tested} min) whose interpolated share of unique locks is "
              f"at least 90%, with that share. The readings at the quartiles and for other definitions of \"streams "
              f"that lock\" are in `results/m4_measured.md`.", ""]
    ra, rb = (mm["readings"][k]["median"]["hann16"]["full"] for k in ("A", "B"))
    cap = (f"look at the two medians in the top panel, {ra['sigma_mhz']:.1f} mHz with r as scored and "
           f"{rb['sigma_mhz']:.1f} mHz with the unweighted r")
    if ra["status"] == "read" and rb["status"] == "read":
        cap += (f": in the bottom-left panel (full span) their curves are first at or above the 90% line at "
                f"{ra['shortest_length_min']} min and at {rb['shortest_length_min']} min")
    L += figure(src, "m4_measured_noise.png",
                "Equivalent trace noise of the Test streams that lock, and the uniqueness table read at the two medians",
                cap + ".")
    return L


def _sec_exploratory(src, ts, bd, m5) -> list[str]:
    ex = exploratory_facts(src, ts, bd)
    if ex is None and m5 is None:
        return []
    L = ["### EXPLORATORY (post hoc) analyses", "",
         "Nothing in this subsection is in PROTOCOL.md. Both analyses were run after the Test results were known. "
         "They describe the pre-registered results and change none of them.", ""]
    if ex is not None:
        a, b = ex["primary_cell"]["within_30"], ex["primary_cell"]["offset_30_600"]
        ds = ex["distinct_streams"]["at_least_one_offset_30_600"]
        L.append(f"- **EXPLORATORY (post hoc): the confident verdicts 30 to 600 s from T*** "
                 f"(`results/exploratory_offset_verdicts.md`). In the primary cell the {a['n_verdicts']} confident "
                 f"verdicts within 30 s have a median |D| of {secs_text(a['abs_D_s']['median'])}, and "
                 f"{a['label_stable']['True']} of them are on label-stable streams. The {b['n_verdicts']} verdicts "
                 f"30 to 600 s off have a median |D| of {secs_text(b['abs_D_s']['median'])}, and "
                 f"{b['label_stable']['True']} are on label-stable streams. Over the full span, {ds['n_streams']} "
                 f"streams have such an offset verdict at some excerpt length, and "
                 f"{ex['offset_streams_with_a_hit_at_another_length']} of them have a top candidate within 30 s at "
                 f"another length. These are counts and medians, with no test and no new threshold. T* rests on "
                 f"YouTube's start time, L and D, so the table cannot say which of them an offset comes from.")
    if m5 is not None and (m5.get("exploratory") or {}).get("gate_locks"):
        g = m5["exploratory"]["gate_locks"]
        add = g["additional_streams_only"]
        oc = (add.get("uncut_60min_excerpt_in_results_table") or {}).get("full_scope_outcome") or {}
        why = ("" if not oc else " The additional streams are gate locks through another excerpt length or scope; "
               "the 60-min excerpt of " + " and of ".join(f"{n} is \"{k}\"" for k, n in sorted(oc.items()))
               + " over the full span.")
        L.append(f"- **EXPLORATORY (post hoc): M5 on every gate lock that has SYNTHETIC cut traces** "
                 f"(`results/m5.md`). Of the {g['n_gate_locks']} gate locks, {g['n_streams']} have SYNTHETIC cut "
                 f"traces: the pre-registered streams and {add['n_streams']} more. Over all of them "
                 f"{rate_of(g['totals']['all']['detected'])} SYNTHETIC cuts were detected, and on the "
                 f"{add['n_streams']} additional streams alone {rate_of(add['totals']['all']['detected'])}.{why}")
    L.append("")
    if ex is not None:
        a, b = ex["primary_cell"]["within_30"], ex["primary_cell"]["offset_30_600"]
        da, db = a["abs_D_s"], b["abs_D_s"]
        how = "overlap" if max(da["min"], db["min"]) <= min(da["max"], db["max"]) else "do not overlap"
        L += figure(src, "exploratory_offset_verdicts_v2.png",
                    "EXPLORATORY (post hoc): confident verdicts within 30 s of T* beside those 30 to 600 s from it",
                    f"EXPLORATORY (post hoc): look at panel (a), where the drift corrections |D| of the "
                    f"{a['n_verdicts']} confident verdicts within 30 s (median {secs_text(da['median'])}, from "
                    f"{secs_text(da['min'])} to {secs_text(da['max'])}) and of the {b['n_verdicts']} verdicts 30 to "
                    f"600 s off (median {secs_text(db['median'])}, from {secs_text(db['min'])} to "
                    f"{secs_text(db['max'])}) {how}.")
    return L


def b_results(src, ts) -> str:
    L: list[str] = []
    show = ts.show
    bd = breakdown_facts(src, ts) if show else None
    m5 = m5_facts(src, ts) if show else None
    L += _sec_gate(src, ts)
    L += _sec_table(src, ts, m5)
    if show:
        mc = ts.ev["m3_counts"]
        n_real, n_syn = report_half(src)
        L += [f"M3 used {mc['real']} of {n_real} real and {mc['SYNTHETIC']} of {n_syn} SYNTHETIC report-half negatives "
              f"(`results/evaluation.json`, `m3_counts`). Every cell is in the M3 subsection below.", ""]
    if not show:
        L += [f"The M2 grid by excerpt length and scope, the baselines on the same streams, the sensitivity "
              f"analyses S1 to S4, M1 by venue type and the flow table appear here once the gate is frozen. {PENDING}", ""]
    else:
        L += _sec_m2(src, ts, bd)
        L += _sec_breakdown(src, ts, bd)
        L += _sec_baselines(src, ts)
        L += _sec_sensitivity(src, ts)
        L += _sec_m1(src, ts)
        L += _sec_m3(src, ts)
        L += _sec_m5(src, ts, m5)
        L += _sec_flow(src, ts)
    L += _sec_pretest(src)
    L += ["### M4: how much audio a unique lock needs (reference only, SYNTHETIC noise)", ""]
    L += _m4_table(src)
    L += _m4_measured(src, ts)
    if show:
        ex = _sec_exploratory(src, ts, bd, m5)
        if ex:
            if L[-1] != "":
                L.append("")
            L += ex
    while L and L[-1] == "":
        L.pop()
    return "\n".join(L)


# ----------------------------------------------------------------- Unstop form text

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
    if not ts.show:
        f4 = m4_facts(src)
        full = f4["cells"][("full", "hann16")]["first"]
        m4 = ("" if full is None else
              f" Simulation with SYNTHETIC noise like the probe's: {full['length_min']} min of audio gave unique "
              f"full-span locks in {100 * int(full['hits']) / int(full['N']):.1f}% of {num(full['N'])} trials.")
        lf = latency_facts(src)
        return (f"Test results: {PENDING}. Already measured: start latency L = {lf['L']} s from {lf['n']} of "
                f"{lf['harvested']} Calibration streams (offsets {secs_text(lf['range'][0])} to "
                f"{secs_text(lf['range'][1])}).{m4}")
    k, n = ts.m1()
    head = f"{ts.tag}: " if ts.tag else ""
    m2, m3 = ts.m2(), ts.m3()
    d0 = m2["top1_hit_D0"]
    bd = breakdown_facts(src, ts)
    if bd is None:
        conf = f" Confident verdicts: {m2['confident_hit']['k']} within 30 s, {m2['confident_wrong']['k']} further off."
    else:
        c = bd["cells"][PRIMARY]["60"]["full"]["confident"]
        conf = (f" Confident verdicts: {c['n']} ({c['within_30']} within 30 s, {c['within_120_share']['k']} within "
                f"120 s, {c['beyond_600']} beyond 600 s).")
    m5 = m5_facts(src, ts)
    cuts = "" if m5 is None else f" SYNTHETIC cuts detected: {tiny(m5['preregistered']['totals']['all']['detected'])}."
    return (f"{head}Results: hum in {fmt_tiny(k, n)} of streams. Primary endpoint (60-min excerpts, whole log): top "
            f"candidate within 30 s for {tiny(m2['top1_hit'])}; {tiny(m2['top1_hit_hum_present'])} where hum is "
            f"present; {d0['k']}/{d0['n']} without the drift correction.{conf} False matches: real foreign streams "
            f"{tiny(m3['real']['false_match'])}, SYNTHETIC {tiny(m3['SYNTHETIC']['false_match'])}.{cuts}")


def b_unstop_baselines(src, ts) -> str:
    if not ts.show:
        return PENDING
    v1, nv = ts.m2("v1")["top1_hit"], ts.m2("naive")["top1_hit"]
    return f"v1 {v1['k']}/{v1['n']}, naive {nv['k']}/{nv['n']}" + (f" ({ts.tag})" if ts.tag else "")


def b_unstop_limits(src, ts) -> str:
    """The numbers of the Limits sentence of the Unstop form."""
    if not ts.show:
        return PENDING
    m2 = ts.m2()
    conf = m2["confident_hit"]["k"] + m2["confident_wrong"]["k"]
    return f"{m2['confident_wrong']['k']} of {conf} confident verdicts are off by more than 30 s"


def b_unstop_harvest(src, ts) -> str:
    if not ts.show:
        return PENDING
    h = harvest_facts(ts)
    return f"{h['with_trace']} harvested"


BLOCKS = {
    "span": b_span,
    "span-end": b_span_end,
    "ref-size": b_ref_size,
    "splits": b_splits,
    "test-licences": b_test_licences,
    "drift-max": b_drift_max,
    "test-selected": b_test_selected,
    "random-guess": b_random_guess,
    "latency": b_latency,
    "cli-threshold": b_cli_threshold,
    "gate-count": b_gate_count,
    "harvest-count": b_harvest_count,
    "harvest-losses": b_harvest_losses,
    "bot-check": b_bot_check,
    "neg-retry": b_neg_retry,
    "lim-hum": b_lim_hum,
    "lim-confident": b_lim_confident,
    "lim-false-match": b_lim_false_match,
    "lim-m5": b_lim_m5,
    "lim-m4": b_lim_m4,
    "headline": b_headline,
    "status": b_status,
    "results": b_results,
    "unstop-results": b_unstop_results,
    "unstop-baselines": b_unstop_baselines,
    "unstop-limits": b_unstop_limits,
    "unstop-harvest": b_unstop_harvest,
    "narration-words": b_narration_words,
    "narration-sources": b_narration_sources,
}


def block_fn(name: str):
    """The function that writes a block: one of BLOCKS, or vo-N for line N of the narration."""
    if name in BLOCKS:
        return BLOCKS[name]
    m = VO_RE.fullmatch(name)
    if m:
        return b_vo(int(m.group(1)))
    raise FillError(f"unknown block {name!r}; known: {', '.join(sorted(BLOCKS))}, vo-N")


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
        fn = block_fn(name)
        end_re = re.compile(r"<!--\s*" + re.escape(name) + r":end\s*-->")
        e = end_re.search(text, m.end())
        if e is None:
            raise FillError(f"block {name!r} has no end marker")
        inner = text[m.end():e.start()]
        if MARKER_RE.search(inner):
            raise FillError(f"block {name!r} contains another start marker")
        if name not in cache:
            cache[name] = fn(src, ts)
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
    ap = argparse.ArgumentParser(description="Fill the results blocks of the README, the Unstop answers and the "
                                             "video script from results/")
    ap.add_argument("targets", nargs="*", help=f"files to fill (default: {' '.join(DEFAULT_TARGETS)})")
    ap.add_argument("--eval-dir", default=None,
                    help="folder with scripts/evaluate.py outputs (default results/; for testing only)")
    ap.add_argument("--interim", action="store_true",
                    help="show Test numbers before the gate is frozen, labelled INTERIM (never publish)")
    ap.add_argument("--allow-practice", action="store_true",
                    help="accept a practice evaluation.json, labelled PRACTICE (testing only)")
    ap.add_argument("--dry-run", action="store_true", help="report what would change; write nothing")
    ap.add_argument("--check", action="store_true",
                    help="write nothing; exit 1 if any file would change (for use before a commit)")
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
            sys.stdout.buffer.write(f"\n----- {name}\n{body}\n".encode("utf-8"))
        sys.stdout.flush()
    stale = 0
    for t, old, new, found in plans:
        rel = src.label(t)
        if new == old:
            print(f"  {rel}: unchanged ({len(found)} blocks)")
            continue
        stale += 1
        if args.dry_run or args.check:
            print(f"  {rel}: would change ({len(found)} blocks: {', '.join(sorted(set(found)))})")
            continue
        write_atomic(t, new)
        print(f"  {rel}: written ({len(found)} blocks: {', '.join(sorted(set(found)))})")
    if args.check and stale:
        print(f"fill_results: {stale} {plural(stale, 'file')} out of date; run scripts/fill_results.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
