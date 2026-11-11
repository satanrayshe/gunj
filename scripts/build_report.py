#!/usr/bin/env python
"""Build the paper-style report: docs/report/report.html and docs/gunj-report.pdf.

The prose of the report lives in docs/report/template.html. Every number in
it is a placeholder that this script fills at build time:

  {{name}}          a value computed here from the results files, mostly
                    through the helpers of scripts/fill_results.py, which
                    recount the later result files against
                    results/test_results.csv and stop on any disagreement
  {{p:phrase}}      a phrase copied from PROTOCOL.md; the build stops if
                    PROTOCOL.md does not contain it (case and markdown
                    emphasis ignored)
  {{r:phrase}}      the same for README.md
  {{cite:a,b}}      numbered citations of docs/report/references.json, in
                    the order of first use
  {{sec:x}}, {{fig:x}}, {{tab:x}}
                    the number of section sec-x, figure fig-x or table tab-x,
                    counted in document order
  {{table:x}}       a results table written here
  {{refs}}          the reference list

The template itself may hold no digit outside a placeholder, apart from the
labels M1 to M6, S1 to S4, v1, v2, r1, r2, top-1, SHA-256 and "PROTOCOL
section N" (N must be a section of PROTOCOL.md). So a number typed into the
prose by hand stops the build. Qualitative statements of the template that
depend on the results (the gate was not met, the false-match target is not
met, libhum and M6 were not run, ...) are checked in claims() and stop the
build if the results say otherwise. The finished HTML is checked for left
placeholders, en and em dashes, local absolute paths, an M5 statement
without the word SYNTHETIC, and a PROTOCOL section 19 entry that the
report does not cover.

The figures are copied byte for byte from results/figures/ or results/ into
docs/report/figures/. The HTML is then printed to docs/gunj-report.pdf with
headless Chromium through Playwright (A4, page numbers in the footer), and
the PDF is checked: page count, embedded fonts, size, the size of the body
text (Chromium shrinks the whole layout if anything is wider than the page),
and no placeholder or local path in its text. Playwright, pypdf and PyMuPDF
are imported only for the PDF step, so the HTML step and the tests run in
the project venv.

Usage (from the repo root; Playwright lives in the system Python):
  python scripts\\build_report.py               HTML, then PDF
  python scripts\\build_report.py --html-only   HTML and figures only
  python scripts\\build_report.py --check       write nothing; exit 1 if report.html or a figure is out of date
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import argparse
import html
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import fill_results as fr  # noqa: E402

TEMPLATE = "docs/report/template.html"
REFERENCES = "docs/report/references.json"
HTML_OUT = "docs/report/report.html"
FIG_DIR = "docs/report/figures"
PDF_OUT = "docs/gunj-report.pdf"
FIG_SOURCES = ("results/figures", "results")
BODY_PT_MIN = 9.0            # docs/report/report.css sets the body at 9.5 pt
PAGES_OK = (4, 6)
MAX_PDF_BYTES = 3 * 1024 * 1024
PRIMARY = fr.PRIMARY
COUNTRY = {"GB": "the United Kingdom", "IT": "Italy", "LK": "Sri Lanka", "PK": "Pakistan", "VA": "Vatican City"}

PLACEHOLDER_RE = re.compile(r"\{\{\s*(?:([a-z]+):)?\s*([^{}]*?)\s*\}\}")
KINDS = {"p", "r", "cite", "sec", "fig", "tab", "table"}
HEAD_RE = re.compile(r'<(h[23])\s+id="sec-([a-z0-9-]+)"\s*>')
NUMBERED_RE = re.compile(r'<(figure|table)\b[^>]*?\bid="(fig|tab)-([a-z0-9-]+)"')
IMG_RE = re.compile(r'src="figures/([A-Za-z0-9_.-]+\.png)"')
ALLOWED_LABELS_RE = re.compile(r"\bM[1-6]\b|\bS[1-4]\b|\bv[12]\b|\br\s?[12]\b|\b[Tt]op-1\b|SHA-256|"
                               r"PROTOCOL(?:\.md)? sections? (\d+)(?:(?:, | and | to )\d+)*")
MINUS_RE = re.compile(r"(?<![\w.-])-(?=\d)")
DASHES = ("\u2013", "\u2014")
FOOTER = ('<div style="width:100%; box-sizing:border-box; padding:0 16mm; font-family:Cambria, Georgia, serif; '
          'font-size:7.5pt; color:#555; display:flex; justify-content:space-between;">'
          '<span>Sati. Gunj: dating Indian recordings from mains hum</span>'
          '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>')


class ReportError(Exception):
    """Stops the build before anything is written."""


# ----------------------------------------------------------------- small formatting helpers

def kn(k, n) -> str:
    return f"{int(k)} of {int(n)}"


def pct(k, n, d: dict | None = None) -> str:
    """'27.8%, 95% CI 19.6 to 37.8%' from a rate dict of evaluate.py, or from k and n (Wilson, PROTOCOL section 13)."""
    lo, hi = (d or {}).get("wilson95") or fr.wilson(int(k), int(n))
    return f"{100 * int(k) / int(n):.1f}%, 95% CI {100 * lo:.1f} to {100 * hi:.1f}%"


def prose(d: dict, noun: str = "") -> str:
    """'25 of 90 streams (27.8%, 95% CI 19.6 to 37.8%)'."""
    k, n = int(d["k"]), int(d["n"])
    if not n:
        return "n/a (N = 0)"
    return f"{k} of {n}{' ' + noun if noun else ''} ({pct(k, n, d)})"


def cell_html(k, n, d: dict | None = None) -> str:
    """A table cell: 'k/n · p% [lo, hi]'."""
    k, n = int(k), int(n)
    if not n:
        return "n/a"
    lo, hi = (d or {}).get("wilson95") or fr.wilson(k, n)
    return (f'<span class="nw">{k}/{n} &middot; {100 * k / n:.1f}%</span> '
            f'<span class="ci nw">[{100 * lo:.1f}, {100 * hi:.1f}]</span>')


def sci_html(x: float, digits: int = 4) -> str:
    """7.258e-07 -> '7.258 × 10<sup>−7</sup>'."""
    s = fr.sci(x, digits)                     # '7.258 × 10^-7'
    base, _, exp = s.partition("^")
    return f"{html.escape(base)}<sup>{exp.replace('-', chr(0x2212))}</sup>" if exp else html.escape(s)


def be(n, one="is", many="are") -> str:
    return fr.plural(n, one, many)


def join_and(parts: list[str]) -> str:
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("**", "").replace("`", "")).strip().lower()


# ----------------------------------------------------------------- sources

class Book:
    """The texts a phrase may be copied from, normalised once."""

    def __init__(self, repo: Path):
        self.repo = repo
        self.protocol = (repo / "PROTOCOL.md").read_text(encoding="utf-8")
        self.readme = (repo / "README.md").read_text(encoding="utf-8")
        self._n = {"p": norm(self.protocol), "r": norm(self.readme)}
        self.sections = set(re.findall(r"^## (\d+)\.", self.protocol, flags=re.M))

    def phrase(self, kind: str, phrase: str) -> str:
        if norm(phrase) not in self._n[kind]:
            where = "PROTOCOL.md" if kind == "p" else "README.md"
            raise ReportError(f"{where} does not contain the phrase {phrase!r}")
        return phrase

    def deviation_stamps(self) -> list[str]:
        """'7 Nov 2026, 08:30 IST' for every entry of PROTOCOL section 19."""
        parts = re.split(r"^## 19\. .*$", self.protocol, maxsplit=1, flags=re.M)
        if len(parts) != 2:
            raise ReportError("PROTOCOL.md has no section 19")
        sec = re.split(r"^## ", parts[1], maxsplit=1, flags=re.M)[0]
        return re.findall(r"^- \*\*(\d{1,2} [A-Z][a-z]{2} \d{4}, \d\d:\d\d IST)", sec, flags=re.M)


def load_state(repo: Path, eval_dir: Path | None = None):
    eval_dir = eval_dir or repo / "results"
    src = fr.Sources(repo, eval_dir, False, False)
    try:
        ts = fr.TestState(src)
    except fr.FillError as e:
        raise ReportError(str(e)) from e
    if not ts.show or ts.frozen is None:
        raise ReportError("the Test results are not final (no frozen gate or no real evaluation.json); "
                          "the report is built only from final results")
    return src, ts


def metadata_channels(src) -> list[tuple[int, int]]:
    """(candidates kept, channels) per candidate list, from results/metadata_summary.md."""
    text = src.text(src.results / "metadata_summary.md") or ""
    return [(int(a), int(b)) for a, b in re.findall(r"Kept: (\d+) from (\d+) channels", text)]


# ----------------------------------------------------------------- the numbers of the report

def facts(src, ts, book: Book) -> dict:
    """Every value of the template, as plain text (keys ending in _html are HTML)."""
    P = lambda s: book.phrase("p", s)        # a protocol constant used inside a generated sentence
    f: dict = {}
    m2 = ts.m2()
    n90 = m2["n"]
    try:
        bd = fr.breakdown_facts(src, ts)
        m5 = fr.m5_facts(src, ts)
        mm = fr.m4m_facts(src, ts)
        rel = fr.reliability_facts(src, ts)
        ex = fr.exploratory_facts(src, ts, bd)
        h = fr.harvest_facts(ts)
        rf, dr, lf, tf, dv, f4 = (fr.ref_facts(src), fr.drift_facts(src), fr.latency_facts(src),
                                  fr.tau_facts(src), fr.dev_facts(src), fr.m4_facts(src))
    except fr.FillError as e:
        raise ReportError(str(e)) from e
    for name, obj, script in (("results/error_breakdown.json", bd, "scripts/make_figures.py"),
                              ("results/m5.json", m5, "scripts/m5_edits.py"),
                              ("results/m4_measured.json", mm, "scripts/make_figures.py"),
                              ("results/figures/figures.json", rel, "scripts/make_figures.py"),
                              ("results/exploratory_offset_verdicts.json", ex, "scripts/make_figures.py")):
        if obj is None:
            raise ReportError(f"{name} is missing; run {script} first")
    ss = src.need("results/splits_summary.json")
    sp = ss["splits"]
    gate, _, _ = ts.gate()
    block = fr.block_facts(ts)

    # --- run and reference
    f["eval_written"] = fr.ist_text(ts.ev.get("written_ist"))
    f["ref_span"] = f"{fr.day_text(rf['t0'])} to {fr.day_text(rf['t1'])}"
    f["ref_t0"], f["ref_t1"], f["ref_t1_day"] = rf["t0"], rf["t1"], fr.day_text(rf["t1"])
    f["ref_seconds"] = f"{fr.num(rf['seconds'])} seconds"
    f["ref_days"] = f"{rf['days']} days"
    f["ref_valid_pct"] = f"{rf['valid_pct']:.2f}%"
    lg = rf["longest"]
    f["ref_gaps"] = (f"The series has {rf['gaps10']} gaps longer than 10 s, holding {fr.num(rf['missing10'])} missing "
                     f"seconds; the longest lasts {fr.num(lg['length_s'])} s, from {lg['start_ist']} to "
                     f"{lg['end_ist']} IST.")
    stats = src.need("results/reference_stats.json")
    f["ref_std_mhz"] = f"{1000 * stats['freq']['std_hz']:.1f} mHz"
    ta = src.need("results/time_axis_check.json")
    f["n_ref_files"] = str(len(ta["per_file"]))
    f["drift_max"] = fr.secs_text(dr["max_last"])
    f["drift_last"] = join_and([str(int(v)) for v in dr["last"]]) + " s"
    f["overlap_offsets"] = join_and([str(int(v)) for v in dr["overlaps"]]) + " s"
    f["drift_agree"] = ("at the overlaps the clock check gives the same four offsets within 1 s" if dr["agree"] else
                        "at the overlaps the clock check does not reproduce every offset")

    # --- candidates and splits
    kept = metadata_channels(src)
    ic, nc = ss["india_candidates"], ss["negative_candidates"]
    if len(kept) < 2 or kept[0][0] != ic["total"] or kept[1][0] != nc["total"]:
        raise ReportError("results/metadata_summary.md and results/splits_summary.json disagree on the candidates")
    f["cand_india"] = f"{ic['total']} Indian livestreams from {kept[0][1]} channels"
    f["cand_foreign"] = f"{nc['total']} foreign livestreams from {kept[1][1]} channels"
    exc = ic["excluded_by_reason"]
    f["excl_archive"] = str(exc.get("possible_12h_archive_cap", 0))
    f["excl_coverage"] = str(exc.get("span_valid_below_90pct", 0))
    f["n_eligible"] = f"{ic['eligible']} eligible Indian streams of {ic['eligible_channels']} channels"
    f["seed"] = str(ss["seed"])
    f["n_dev"], f["n_cal"], f["n_test"] = str(sp["dev"]["n"]), str(sp["calibration"]["n"]), str(sp["test"]["n"])
    f["n_dev_streams"] = f"{sp['dev']['n']} Dev streams"
    f["test_channels"] = str(sp["test"]["n_channels"])
    f["max_per_channel"] = str(sp["test"]["max_per_channel"])
    venues = sorted(sp["test"]["by_venue_type"].items(), key=lambda kv: (-kv[1], kv[0]))
    f["test_venues"] = join_and([f"{n} {v}" for v, n in venues]) + " streams"
    f["n_unverified"] = str(sp["test"]["unverified_channel_streams"])
    f["n_neg"] = str(sp["negative"]["n"])
    ctry = sorted(sp["negative"]["by_country"].items(), key=lambda kv: (-kv[1], kv[0]))
    f["neg_countries"] = "from " + join_and([f"{COUNTRY.get(c, c)} ({n})" for c, n in ctry])
    syn = sp["negative_synthetic"]
    per_kind = ss["rules"]["targets"]["synthetic_per_kind"]
    if 2 * per_kind != syn["n"]:
        raise ReportError("results/splits_summary.json: the SYNTHETIC negatives are not two kinds of equal size")
    f["syn_items"] = (f"{syn['n']} SYNTHETIC items ({per_kind} of SYNTHETIC white noise and {per_kind} of SYNTHETIC hum "
                      f"that follows a SYNTHETIC random walk)")

    # --- harvest
    f["harvest_count"] = f"{h['with_trace']} of the {h['selected']}"
    stop = " before a bot check stopped the downloads" if block["rows"] else ""
    f["harvest_abstract"] = f"{h['with_trace']} of {h['selected']} selected Test streams were harvested{stop}"
    lost = [(h["unavailable"], "{n} {were} no longer available on YouTube"),
            (h["failed"], "{n} failed in download or conversion"),
            (h["not_attempted"], "{n} had not been attempted"),
            (h["blocked"], "{n} met the bot check that stopped the harvest"),
            (h["other"], "{n} {were} dropped for another reason")]
    said = [t.format(n=n, were=be(n, "was", "were")) for n, t in lost if n]
    f["harvest_sentence"] = (f"Of the {h['selected']} Test streams, {h['with_trace']} have a trace"
                             + (f"; of the other {h['lost']}, {join_and(said)}." if h["lost"] else "."))

    # --- extractor v2
    tun = src.need("results/v2_tuning.json")
    cp = tun["chosen_params"]
    f["v2_gate"] = f"{cp['gate']:g}"
    # the frame weight of gunj/extract_v2.py: max(snr_k(t) - frame_offset, 0) ** frame_power
    f["v2_weight_html"] = (f"max(SNR {chr(0x2212)} {cp['frame_offset']:g}, 0)"
                           + (f"<sup>{cp['frame_power']:g}</sup>" if cp["frame_power"] != 1 else ""))
    f["v2_tuning"] = (f"v2 was chosen from {len(tun['candidates'])} candidate settings on the Dev streams only, by the "
                      f"mean clipped Fisher-z margin J of the true start over the best other candidate ({dv['J']:.3f}, "
                      f"against {dv['J_v1']:.3f} for v1 and {dv['J_naive']:.3f} for a naive single-bin "
                      f"{P('50 Hz')} extractor)")
    cfg = src.need("gunj/config.json")
    f["top_k"] = str(cfg["top_k"])
    r1, conf, m, s = tf["best_real_r1"]
    f["best_neg"] = (f"the best real threshold-half negative reached a top correlation of {r1:.4f} (v2, {m} min, "
                     f"{fr.SCOPE_LABEL[s]}) with a confidence of only {conf:.4f}")
    fe = m5["method"]["find_edits_defaults"]
    try:
        er = fr.edit_reach(src)                    # read from the frozen gunj/match.py
    except fr.FillError as e:
        raise ReportError(str(e)) from e
    if (er["min_seg"], er["min_jump"]) != (fe["min_seg"], fe["min_jump"]):
        raise ReportError("the find_edits defaults in results/m5.json differ from gunj/match.py")
    f["edit_min_jump"], f["edit_min_seg"] = f"{fe['min_jump']} s", f"{fe['min_seg']} s"
    f["edit_refine"], f["edit_closest"] = f"{er['refine']} s", f"{er['closest']} s"

    # --- drift D and latency L
    f["d_stats"] = (f"For {dr['gt30']} of the {dr['test_n']} Test streams |D| exceeds {P('30 s')}, more than the "
                    f"tolerance of a hit ({P('|e| ≤ 30 s')}; median |D| {fr.secs_text(dr['median_abs'])}, largest "
                    f"{fr.secs_text(dr['max_abs'])}), and {dr['stable']} are label-stable.")
    rr, iq = lf["range"], lf["iqr"]
    f["l_stats"] = (f"It came out as L = {lf['L']} s, from {lf['n']} of the {lf['harvested']} harvested Calibration "
                    f"streams, whose offsets run from {fr.secs_text(rr[0])} to {fr.secs_text(rr[1])} (interquartile "
                    f"range {fr.secs_text(iq[0])} to {fr.secs_text(iq[1])}).")
    f["L_n_streams"] = f"{lf['n']} Calibration streams"

    # --- threshold
    f["tau_basis"] = (f"τ was set on {tf['real']} real and {tf['syn']} SYNTHETIC threshold-half negatives "
                      f"({tf['excluded']} real {be(tf['excluded'], 'one', 'ones')} could not be downloaded).")
    f["tau_primary"] = f"{m2['tau']:.4f}"

    # --- gate and M1
    p2 = gate["by_extractor"][PRIMARY]
    met = "met" if gate["condition_1_2_met"] else "not met"
    g = (f"The frozen gate run ({fr.ist_text(ts.frozen.get('frozen_ist'))}) found {p2['n_streams_with_lock']} of "
         f"{gate['N_processed']} Test streams locking blind ({p2['n_streams_with_lock_D0']} with D set to 0), against "
         f"{gate['by_extractor']['v1']['n_streams_with_lock']} for v1 and "
         f"{gate['by_extractor']['naive']['n_streams_with_lock']} for the naive extractor, so the lock conditions were "
         f"{met}.")
    if not gate["condition_3_met"]:
        nb = len(block["rows"])
        when = f" on {block['when']}" if block["when"] else ""
        g += (f" The harvest-health condition failed: YouTube answered {nb} {fr.plural(nb, 'stream')} with a bot "
              f"check{when}, and the harvester stopped.")
    g += " The gate was therefore " + ("met." if gate["gate_met"] else "not met.")
    f["gate_sentence"] = g
    k1, n1 = ts.m1()
    vparts = []
    for v in sorted({r.get("venue_type") or "unknown" for r in ts.streams.values()}):
        kv, nv = ts.m1(keep=lambda r, v=v: (r.get("venue_type") or "unknown") == v)
        if nv:
            vparts.append((nv, v, kv))
    vparts.sort(key=lambda x: (-x[0], x[1]))
    f["m1_sentence"] = (f"The M1 hum flag was set for {kn(k1, n1)} Test streams with audio ({pct(k1, n1)}); by venue "
                        f"type, " + join_and([f"{v} {kn(kv, nv)}" for nv, v, kv in vparts]) + ".")
    neg = fr.m1_negatives(src)
    if neg is None:
        f["m1_sentence"] += (" M1 for the real negatives, which the plan reports as description only, was not computed: "
                             "data/harvest_manifest.csv or data/splits.csv is missing.")
    else:
        kn_, nn_ = neg["all"]
        f["m1_sentence"] += (f" As description only, the flag was set for {kn(kn_, nn_)} harvested real foreign negatives "
                             f"({pct(kn_, nn_)}).")

    # --- M2
    f["n_primary"] = str(n90)
    f["primary_prose"] = prose(m2["top1_hit"], "streams")
    f["primary_short"] = kn(m2["top1_hit"]["k"], n90)
    f["hum_present_prose"] = prose(m2["top1_hit_hum_present"])
    f["hum_present_short"] = kn(m2["top1_hit_hum_present"]["k"], m2["top1_hit_hum_present"]["n"])
    f["d0_prose"] = prose(m2["top1_hit_D0"])
    f["d0_short"] = kn(m2["top1_hit_D0"]["k"], m2["top1_hit_D0"]["n"])
    cell = bd["cells"][PRIMARY]["60"]["full"]
    c, t = cell["confident"], cell["top1"]
    errs = [abs(int(float(r["error_s"]))) for r in ts.cells.get((PRIMARY, 60, "full"), {}).values()
            if r.get("error_s", "") != ""]
    gt_day = sum(e > 86400 for e in errs)
    top = (f"Of the {t['n']} excerpts, the top candidate is within 30 s for {t['within_30']}, 30 to 120 s off for "
           f"{t['from_30_to_120']}, 120 to 600 s off for {t['from_120_to_600']} and further off for {t['beyond_600']} "
           f"({gt_day} by more than a day)")
    if t["no_candidate"]:
        top += (f"; {t['no_candidate']} {fr.plural(t['no_candidate'], 'excerpt')} "
                f"{be(t['no_candidate'], 'has', 'have')} no candidate, because the trace was constant")
    f["top1_classes"] = top + "."
    far = fr.far_verdicts(ts)
    if len(far) != c["beyond_600"]:
        raise ReportError("the confident verdicts beyond 600 s in test_results.csv do not match error_breakdown.json")
    far_txt = (f"{c['beyond_600']} {be(c['beyond_600'])} off by " + join_and(
        [f"{fr.num(e)} s ({fr.dur_text(e)})" for e, _ in far])) if far else f"{c['beyond_600']} are more than 600 s off"
    nv_k = m2["no_verdict"]["k"]
    f["conf_n"], f["conf_w30"] = str(c["n"]), str(c["within_30"])
    f["conf_wrong"] = str(m2["confident_wrong"]["k"])
    f["conf_w30_of"] = kn(c["within_30"], c["n"])
    f["conf_wrong_of"] = kn(c["n"] - c["within_30"], c["n"])
    f["conf_sentence"] = (f"Gunj gave a confident verdict for {c['n']} excerpts and no verdict for {nv_k}; of the "
                          f"{c['n']}, {c['within_30']} {be(c['within_30'])} within 30 s, {c['from_30_to_120']} "
                          f"{be(c['from_30_to_120'])} 30 to 120 s off, {c['from_120_to_600']} "
                          f"{be(c['from_120_to_600'])} 120 to 600 s off and {far_txt}")
    f["conf_rates"] = (f"Confident hits are thus {prose(m2['confident_hit'])} and confident wrong verdicts "
                       f"{prose(m2['confident_wrong'])}.")

    # --- baselines and sensitivity
    pv1, pnv = ts.paired("v1"), ts.paired("naive")
    v1h, nvh = ts.m2("v1")["top1_hit"]["k"], ts.m2("naive")["top1_hit"]["k"]
    if pv1["base_only"] == 0 and pnv["base_only"] == 0:
        f["paired_sentence"] = (f"Every stream that v1 ({v1h} hits) or the naive extractor ({nvh}) puts within 30 s is "
                                f"also a hit of v2.")
    else:
        f["paired_sentence"] = (f"v1 hits {v1h} of these streams and the naive extractor {nvh}; v2 misses "
                                f"{pv1['base_only']} that v1 hits and {pnv['base_only']} that the naive extractor hits.")
    gr = ss["analytic_random_guess"]["full_60min"]
    f["rg_sentence_html"] = (f"A uniform random guess would hit with probability {sci_html(gr['p_hit'])} per stream, "
                             f"or {sci_html(n90 * gr['p_hit'], 2)} times over these {n90} streams.")

    # --- M3
    mc = ts.ev["m3_counts"]
    n_real, n_syn = fr.report_half(src)
    f["n_real_report"] = str(n_real)
    m3 = ts.m3()
    real, syn_, tog = m3["real"]["false_match"], m3["SYNTHETIC"]["false_match"], m3["together"]["false_match"]
    cells_hit, uppers, same_flag = {"real": 0, "SYNTHETIC": 0}, [], True
    for mm_ in fr.LENGTHS:
        for sc in fr.SCOPES:
            cc = ts.m3(PRIMARY, mm_, sc)
            for kind in ("real", "SYNTHETIC"):
                cells_hit[kind] += 1 if cc[kind]["false_match"]["k"] else 0
            for kind in ("real", "SYNTHETIC", "together"):
                same_flag &= cc[kind]["false_match"]["k"] == cc[kind]["false_match_with_hum_flag"]["k"]
            if cc["together"]["false_match"]["n"]:
                uppers.append(cc["together"]["false_match"]["wilson95"][1])
    target = (f"so the pre-registered target of {P('at most 1%')} is not met" if real["n"] and real["k"] / real["n"] > 0.01
              else "which cannot show a rate below the pre-registered target with so few negatives")
    all_syn = "all " if mc["SYNTHETIC"] == n_syn else f"{mc['SYNTHETIC']} of the "
    f["m3_sentence"] = (f"The report half was searched with {mc['real']} of its {n_real} real foreign streams and "
                        f"{all_syn}{n_syn} SYNTHETIC items. In the primary cell τ was reached by "
                        f"{prose(real, 'real negatives')}, {prose(syn_, 'SYNTHETIC ones')} and "
                        f"{kn(tog['k'], tog['n'])} together, {target}. "
                        f"Across the "
                        f"{len(fr.LENGTHS) * len(fr.SCOPES)} cells of v2 a real negative reached τ in "
                        f"{cells_hit['real']} cells and a SYNTHETIC one in {cells_hit['SYNTHETIC']}; adding the "
                        f"hum-flag condition " + ("changes no count." if same_flag else "lowers some counts.")
                        + (f" No cell has enough negatives to show a rate below 1%: the smallest Wilson upper bound, "
                           f"real and SYNTHETIC together, is {100 * min(uppers):.1f}%." if uppers else ""))
    f["fm_real_short"] = kn(real["k"], real["n"])
    tb, ng = rel["test"]["bins"], rel["negatives"]
    hi = [b for b in tb if b["lo"] >= 0.1]
    f["rel_sentence"] = (f"Test excerpts with c below {tb[0]['hi']:g} hit in {kn(tb[0]['k'], tb[0]['n'])} cases and "
                         f"those with c of {hi[0]['lo']:g} or more in {kn(sum(b['k'] for b in hi), sum(b['n'] for b in hi))}. "
                         f"In the primary cell the largest confidence of a real negative was "
                         f"{max(ng['real']['confidence']):.4f} and of a SYNTHETIC one "
                         f"{max(ng['SYNTHETIC']['confidence']):.4f}, against τ = {ng['tau']:.4f}.")

    # --- M4
    first = f4["cells"][("full", "hann16")]["first"]
    kinds = {c["kind"] for c in src.need("results/uniqueness_run.json")["conditions"]}
    if "hann16" not in kinds:
        raise ReportError("results/uniqueness_run.json has no hann16 noise condition")
    smooth = re.sub(r"\D", "", "hann16")                # SYNTHETIC noise smoothed by a 16 s Hann window
    if first is None:
        pre = "none of the tested lengths reached 90% unique locks over the full span"
    else:
        pre = (f"90% unique locks over the full span needed {first['length_min']} min of audio "
               f"({kn(first['hits'], first['N'])} trials, {100 * int(first['hits']) / int(first['N']):.1f}%)")
    f["m4_pre"] = (f"Before any Test search the simulation was read at {f4['level']:g} mHz, the tested level nearest "
                   f"the {f4['probe_mhz']:.1f} mHz equivalent trace noise of the feasibility probe; with SYNTHETIC "
                   f"noise smoothed over {smooth} s, {pre}.")
    dist = mm["distribution_hz"]
    ra, rb = (mm["readings"][k]["median"]["hann16"]["full"] for k in ("A", "B"))

    def reading(r):
        if r["status"] != "read":
            return f"not read ({r['status']})"
        mins = r["shortest_length_min"]
        out = f"{mins} min ({100 * r['rates_by_length_min'][str(mins)]['rate']:.1f}%"
        prev = r.get("previous_tested_min")
        if prev is not None and str(prev) in r["rates_by_length_min"]:
            out += f"; {prev} min gives {100 * r['rates_by_length_min'][str(prev)]['rate']:.1f}%"
        return out + ")"
    if mm.get("preregistered_reading", "B") != "B":
        raise ReportError("results/m4_measured.json names another pre-registered reading than B; the template "
                          "describes reading B as the computation of scripts/probe_noise.py")
    st = mm["streams_that_lock"]
    f["m4_readings"] = (f"Read that way (reading B), the median noise over the {st['n']} streams "
                        f"({st['n_excerpts']} locking excerpts) is {fr.mhz(dist['B']['median'])} mHz (quartiles "
                        f"{fr.mhz(dist['B']['q1'])} to {fr.mhz(dist['B']['q3'])}), and the shortest tested length with "
                        f"at least 90% unique locks over the full span is {reading(rb)}. EXPLORATORY (post hoc): with "
                        f"v2's weighted correlation in the same formula (reading A), the median is "
                        f"{fr.mhz(dist['A']['median'])} mHz and the length {reading(ra)}.")

    # --- M5 (SYNTHETIC cuts only)
    pre5 = m5["preregistered"]
    by, tot, fa = pre5["totals"]["by_cut_length_s"], pre5["totals"]["all"], pre5["false_alarms"]
    lens = sorted(by, key=int)
    shortest = by[lens[0]]["detected"]
    err = tot["duration_error_s"]
    errs_txt = (f" For the detected SYNTHETIC cuts the missing duration was off by a median of "
                f"{fr.secs_text(err['median'])} and at most {fr.secs_text(err['max'])}." if err["n"] else "")
    none_short = ("none" if shortest["k"] == 0 else str(shortest["k"]))
    f["m5_sentence"] = (f"Every cut is SYNTHETIC, a stretch of {lens[0]} to {lens[-1]} s removed from the 60-min "
                        f"excerpt audio at harvest time. On the {pre5['n_streams']} Test streams whose uncut 60-min "
                        f"excerpt is a confident hit over the full span, {prose(tot['detected'], 'SYNTHETIC cuts')} "
                        f"were detected within 30 s "
                        f"of their true position; {none_short} of the {shortest['n']} SYNTHETIC cuts of {lens[0]} s "
                        f"{be(shortest['k'] if shortest['k'] else 1, 'was', 'were')} detected.{errs_txt} As M5 false alarms, find_edits reported an edit on "
                        f"{prose(fa['excerpts_with_a_reported_edit'], 'uncut excerpts')} of the same streams. No "
                        f"recording with a real edit was tested.")
    f["m5_cut_range"] = f"{lens[0]} to {lens[-1]} s"

    # --- EXPLORATORY (post hoc)
    a, b = ex["primary_cell"]["within_30"], ex["primary_cell"]["offset_30_600"]
    da, db = a["abs_D_s"], b["abs_D_s"]
    how = "overlap" if max(da["min"], db["min"]) <= min(da["max"], db["max"]) else "do not overlap"
    gl = (m5.get("exploratory") or {}).get("gate_locks")
    gl_txt = ""
    if gl:
        gl_txt = (f" Over all {gl['n_streams']} gate locks with SYNTHETIC cut traces, M5 detects "
                  f"{kn(gl['totals']['all']['detected']['k'], gl['totals']['all']['detected']['n'])} SYNTHETIC cuts.")
    f["exploratory_sentence"] = (f"In the primary cell the drift corrections |D| of the {a['n_verdicts']} confident "
                                 f"verdicts within 30 s (median {fr.secs_text(da['median'])}, range "
                                 f"{fr.secs_text(da['min'])} to {fr.secs_text(da['max'])}) and of the "
                                 f"{b['n_verdicts']} that are 30 to 600 s off (median {fr.secs_text(db['median'])}, "
                                 f"range {fr.secs_text(db['min'])} to {fr.secs_text(db['max'])}) {how}; these are counts "
                                 f"with no test, and they cannot show whether an offset comes from YouTube's start time, "
                                 f"L, D or the lock.{gl_txt}")

    # --- deviations
    f["n_dev_entries"] = str(len(book.deviation_stamps()))
    gone = n_real - mc["real"]
    f["retry_counts"] = (f"{mc['real']} of the {n_real} streams" +
                         (f" (the remaining {gone} {be(gone, 'was', 'were')} no longer available)" if gone else ""))

    # --- captions
    f["cap_drift"] = (f"Label drift of the reference against the grid's 15-minute scheduling clock, in weekly windows. "
                      f"Each series is one source file, zero at its first week; open circles mark windows with a weak "
                      f"profile correlation. At the end of each file the labels run late by {f['drift_last']} (files "
                      f"I to V). Source: results/time_axis_check.json.")
    f["cap_m2"] = ("M2 of extractor v2: top-1 candidates within 30 s of T*, as hits, share of N and Wilson 95% "
                   "interval. N counts the Test streams with a trace at that length; the primary cell is in bold. The "
                   "last three columns split the confident verdicts (match) of the full span into hits (within 30 s) "
                   "and wrong verdicts. Sources: results/evaluation.json, results/error_breakdown.json.")
    f["cap_base"] = ("Baselines and pre-registered sensitivity analyses in the primary cell (60-min excerpts, full "
                     "span). The paired column compares each baseline with v2 on the same streams. Sources: "
                     "results/evaluation.json, results/test_results.csv, results/splits_summary.json.")
    nr_above = ng["real"]["n_at_or_above_tau"]
    f["cap_reliability"] = (f"M3 reliability plot for the primary cell. Top: Test top-1 hit rate by pre-registered "
                            f"confidence bin, with Wilson 95% intervals. Bottom: the confidence of every Test excerpt "
                            f"and of the report-half negatives, {ng['real']['n']} real (filled) and "
                            f"{ng['SYNTHETIC']['n']} SYNTHETIC (open). Dashed line: τ = {ng['tau']:.4f}, with "
                            f"{nr_above} real {fr.plural(nr_above, 'negative')} to its right. Source: "
                            f"results/figures/figures.json.")

    f["_tables"] = {"m2": table_m2(ts, bd), "base": table_base(src, ts)}
    lo_bin, hi_bins = tb[0], hi
    f["_claims"] = claims(src, ts, gate, real) + [
        ("the M2 count over 90 days equals the full span",
         ts.m2(PRIMARY, 60, "90d")["top1_hit"] == m2["top1_hit"] or
         (ts.m2(PRIMARY, 60, "90d")["top1_hit"]["k"], ts.m2(PRIMARY, 60, "90d")["n"]) == (m2["top1_hit"]["k"], n90)),
        ("the confidence separates Test hits from misses (lowest bin below the bins at 0.1 or more)",
         lo_bin["n"] > 0 and sum(b["n"] for b in hi_bins) > 0 and
         lo_bin["k"] / lo_bin["n"] < sum(b["k"] for b in hi_bins) / sum(b["n"] for b in hi_bins)),
    ]
    return f


def claims(src, ts, gate, real) -> list[tuple[str, bool]]:
    """Statements the template makes in words, checked against the results."""
    station = fr.other_results(src, ("station",))
    return [
        ("the gate was not met, on harvest health", not gate["gate_met"] and not gate["condition_3_met"]),
        ("the lock conditions of the gate were met", bool(gate["condition_1_2_met"])),
        ("tau and L were committed by the decision time", bool(gate["tau_L_committed_in_time"])),
        ("the M3 target of at most 1% is not met in the primary cell", bool(real["n"]) and real["k"] / real["n"] > 0.01),
        ("libhum was not run", fr.libhum_status(src).startswith("not run: libhum was never installed")),
        ("M6 was not run", not station),
        ("no further download is planned", fr.protocol_says(src, "No further YouTube download is planned")),
        ("the retry met no block", fr.protocol_says(src, "single retry of 10 Nov", "No bot check, CAPTCHA, sign-in wall or HTTP 429 was met")),
        ("the author decided to stay with Gunj", fr.protocol_says(src, "decided at about 17:08 IST to stay with Gunj")),
        ("the extractor v2 was frozen before Calibration audio was extracted",
         fr.protocol_says(src, "frozen at 05:55 IST, before any Calibration audio was extracted")),
        ("tau, L and evaluate.py were committed before any Test search",
         bool(re.search(r"^passed", str(ts.ev.get("blinding", ""))))),
    ]


# ----------------------------------------------------------------- tables

def table_m2(ts, bd) -> str:
    rows = []
    for m in fr.LENGTHS:
        d = {s: ts.m2(PRIMARY, m, s) for s in fr.SCOPES}
        c = bd["cells"][PRIMARY][str(m)]["full"]["confident"]
        cells = []
        for s in fr.SCOPES:
            h = d[s]["top1_hit"]
            if h["n"] != d["full"]["n"]:
                raise ReportError(f"M2 cells of {m} min have different N across scopes")
            lo, hi_ = h.get("wilson95") or fr.wilson(h["k"], h["n"])
            txt = (f'<span class="nw">{h["k"]} &middot; {100 * h["k"] / h["n"]:.1f}%</span> '
                   f'<span class="ci nw">[{100 * lo:.1f}, {100 * hi_:.1f}]</span>')
            cells.append(f"<td><b>{txt}</b></td>" if (m, s) == (60, "full") else f"<td>{txt}</td>")
        rows.append(f'<tr><td class="l">{m} min</td><td>{d["full"]["n"]}</td>{"".join(cells)}'
                    f'<td>{c["n"]}</td><td>{c["within_30"]}</td><td>{c["n"] - c["within_30"]}</td></tr>')
    head = ('<colgroup><col style="width:9%"><col style="width:6%"><col style="width:21%"><col style="width:21%">'
            '<col style="width:21%"><col style="width:7%"><col style="width:7%"><col style="width:8%"></colgroup>'
            '<thead><tr><th class="l" rowspan="2">Excerpt</th><th rowspan="2">N</th>'
            '<th colspan="3">Top-1 within 30 s of T*: hits &middot; share [Wilson 95%]</th>'
            '<th colspan="3">Confident, full span</th></tr>'
            '<tr><th>Full span</th><th>&plusmn;90 days</th><th>&plusmn;1 day</th>'
            '<th>Match</th><th>Hit</th><th>Wrong</th></tr></thead>')
    return f'<table class="m2">{head}<tbody>{"".join(rows)}</tbody></table>'


def table_base(src, ts) -> str:
    def counts(d):
        return f"{d['k']} ({100 * d['rate']:.1f}%)" if d.get("n") else "n/a"
    name = {"v2": "v2, primary", "v1": "v1, frozen extractor", "naive": "naive single-bin 50 Hz"}
    rows = ['<tr class="group first"><td class="l" colspan="5">Extractors on the same streams</td></tr>']
    for ext in fr.EXTRACTORS:
        c = ts.m2(ext)
        pr = "" if ext == PRIMARY else (lambda q: f"{q['both']} / {q['v2_only']} / {q['base_only']} / {q['neither']}")(
            ts.paired(ext))
        rows.append(f'<tr><td class="l">{name[ext]}</td><td>{cell_html(c["top1_hit"]["k"], c["top1_hit"]["n"], c["top1_hit"])}'
                    f'</td><td>{counts(c["confident_hit"])}</td><td>{counts(c["confident_wrong"])}</td>'
                    f'<td class="c">{pr}</td></tr>')
    lh = fr.libhum_status(src)
    lh = re.sub(r"\s*\(PROTOCOL section \d+, [^)]*\)", "", lh).replace("`", "")
    rows.append(f'<tr><td class="l">libhum</td><td class="l" colspan="4">{html.escape(lh)}</td></tr>')
    g = src.need("results/splits_summary.json")["analytic_random_guess"]["full_60min"]
    n = ts.m2()["n"]
    rows.append(f'<tr><td class="l">random guess, analytic</td><td class="l" colspan="4">p = {sci_html(g["p_hit"])} per '
                f'stream; {sci_html(n * g["p_hit"], 2)} expected hits over these {n} streams</td></tr>')
    rows.append('<tr class="group"><td class="l" colspan="5">Sensitivity analyses, extractor v2</td></tr>')
    d0 = ts.m2()["top1_hit_D0"]
    k2, n2 = ts.hits(keep=lambda r: r.get("label_stable") == "True")
    k3, n3 = ts.hits(keep=lambda r: r.get("channel_status") == fr.VERIFIED)
    for lab, txt in (("S1: no drift correction (D = 0)", cell_html(d0["k"], d0["n"], d0)),
                     ("S2: label-stable streams only", cell_html(k2, n2)),
                     ("S3: verified channels only", cell_html(k3, n3))):
        rows.append(f'<tr><td class="l">{lab}</td><td>{txt}</td><td></td><td></td><td></td></tr>')
    rows.append('<tr><td class="l">S4: v1 against v2</td><td class="l" colspan="4">the paired counts above</td></tr>')
    head = ('<colgroup><col style="width:27%"><col style="width:25%"><col style="width:11%">'
            '<col style="width:11%"><col style="width:26%"></colgroup>'
            '<thead><tr><th class="l">Method or analysis</th><th>Top-1 within 30 s: hits/N &middot; share [95% CI]</th>'
            '<th>Confident hit</th><th>Confident wrong</th>'
            '<th>Paired with v2: both / v2 only / other only / neither</th></tr></thead>')
    return f'<table class="base">{head}<tbody>{"".join(rows)}</tbody></table>'


# ----------------------------------------------------------------- the template

def template_digits(template: str, book: Book) -> None:
    """Stop if the template holds a digit outside a placeholder or an allowed label."""
    t = re.sub(r"<!--.*?-->", " ", template, flags=re.S)
    t = PLACEHOLDER_RE.sub(" ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    for m in ALLOWED_LABELS_RE.finditer(t):
        if m.group(1) and m.group(1) not in book.sections:
            raise ReportError(f"template cites PROTOCOL section {m.group(1)}, which PROTOCOL.md does not have")
    rest = ALLOWED_LABELS_RE.sub(" ", t)
    m = re.search(r"\d", rest)
    if m:
        ctx = re.sub(r"\s+", " ", rest[max(0, m.start() - 50):m.end() + 30])
        raise ReportError(f"template: a number outside a placeholder, near '...{ctx}...'; use a placeholder")


def numbering(template: str) -> dict:
    """{'sec': {key: '3'}, 'fig': {...}, 'tab': {...}} in document order."""
    out = {"sec": {}, "fig": {}, "tab": {}}
    major = minor = 0
    for m in HEAD_RE.finditer(template):
        if m.group(1) == "h2":
            major, minor = major + 1, 0
            num = str(major)
        else:
            minor += 1
            num = f"{major}.{minor}"
        out["sec"][m.group(2)] = num
    for kind in ("fig", "tab"):
        keys = [m.group(3) for m in NUMBERED_RE.finditer(template) if m.group(2) == kind]
        out[kind] = {k: str(i + 1) for i, k in enumerate(keys)}
    for kind, d in out.items():
        if len(d) != len(set(d)):
            raise ReportError(f"template: duplicate {kind} ids")
    return out


def load_references(repo: Path) -> dict:
    data = json.loads((repo / REFERENCES).read_text(encoding="utf-8"))
    refs = {}
    for r in data["references"]:
        if r["key"] in refs:
            raise ReportError(f"references.json: duplicate key {r['key']}")
        if not (r.get("doi") or r.get("url")) or not r.get("confirmed"):
            raise ReportError(f"references.json: {r['key']} needs a DOI or URL and a 'confirmed' note")
        refs[r["key"]] = r
    return refs


def reference_html(r: dict, show_accessed: bool = True) -> str:
    link = f"https://doi.org/{r['doi']}" if r.get("doi") else r["url"]
    acc = f" (accessed {html.escape(r['accessed'])})" if r.get("accessed") and show_accessed else ""
    return (f'<li id="ref-{r["key"]}">{html.escape(r["text"])}. '
            f'<a href="{html.escape(link)}">{html.escape(link)}</a>{acc}</li>')


def references_html(refs: dict, order: list[str]) -> str:
    """The numbered list; one note instead of a date on every entry when all web pages share one access date."""
    dates = {refs[k]["accessed"] for k in order if refs[k].get("accessed")}
    note = ""
    if len(dates) == 1:
        note = f'<p class="refnote">Web pages without a DOI were accessed on {html.escape(dates.pop())}.</p>'
    return note + '<ol class="refs">' + "".join(reference_html(refs[k], not note) for k in order) + "</ol>"


def render(template: str, f: dict, book: Book, refs: dict) -> str:
    """Fill the template. Raises ReportError on any unknown or unverifiable placeholder."""
    template_digits(template, book)
    nums = numbering(template)
    order: list[str] = []
    for m in PLACEHOLDER_RE.finditer(template):
        if m.group(1) == "cite":
            for key in (k.strip() for k in m.group(2).split(",")):
                if key not in refs:
                    raise ReportError(f"template cites {key!r}, which references.json does not have")
                if key not in order:
                    order.append(key)
    unused = sorted(set(refs) - set(order))
    if unused:
        raise ReportError(f"references.json entries never cited: {', '.join(unused)}")
    cite_no = {k: i + 1 for i, k in enumerate(order)}

    def sub(m):
        kind, arg = m.group(1), m.group(2)
        if kind is None:
            if arg == "refs":
                return references_html(refs, order)
            if arg not in f or arg.startswith("_"):
                raise ReportError(f"template placeholder {{{{{arg}}}}} has no value")
            val = str(f[arg])
            val = MINUS_RE.sub("−", val)          # a negative number gets a minus sign, not a hyphen
            return val if arg.endswith("_html") else html.escape(val, quote=False)
        if kind not in KINDS:
            raise ReportError(f"unknown placeholder kind {kind!r}")
        if kind in ("p", "r"):
            text = html.escape(book.phrase(kind, arg), quote=False)
            return re.sub(r"(\d)\^(\d+)", r"\1<sup>\2</sup>", text)
        if kind == "cite":
            keys = [k.strip() for k in arg.split(",")]
            return "[" + ", ".join(f'<a href="#ref-{k}">{cite_no[k]}</a>' for k in keys) + "]"
        if kind in ("sec", "fig", "tab"):
            if arg not in nums[kind]:
                raise ReportError(f"template refers to {kind} {arg!r}, which has no id {kind}-{arg}")
            return nums[kind][arg]
        if arg not in f["_tables"]:
            raise ReportError(f"no table {arg!r}")
        return f["_tables"][arg]

    out = PLACEHOLDER_RE.sub(sub, template)

    def head(m):
        return f'{m.group(0)}<span class="num">{nums["sec"][m.group(2)]}</span>'
    out = HEAD_RE.sub(head, out)
    check_html(out, book, f)
    return out


def check_html(out: str, book: Book, f: dict) -> None:
    bad = []
    if "{{" in out or "}}" in out:
        bad.append("a placeholder was left unfilled")
    if any(d in out for d in DASHES):
        bad.append("an en or em dash")
    if fr.ABS_PATH_RE.search(out):
        bad.append("a local absolute path")
    for blk in re.findall(r"<(?:p|li|figcaption|h[23])\b[^>]*>(.*?)</(?:p|li|figcaption|h[23])>", out, flags=re.S):
        if "M5" in blk and "SYNTHETIC" not in blk:
            bad.append("an M5 statement without the word SYNTHETIC: " + re.sub(r"<[^>]+>", "", blk)[:80])
    dev = out.split('id="sec-deviations"', 1)
    dev = dev[1].split("<h2", 1)[0] if len(dev) == 2 else ""
    for stamp in book.deviation_stamps():
        if stamp not in dev:
            bad.append(f"PROTOCOL section 19 entry {stamp} is not in the deviations section")
    for what, ok in f["_claims"]:
        if not ok:
            bad.append(f"the template says {what}, and the results do not")
    if bad:
        raise ReportError("the report fails its checks: " + "; ".join(bad))


def build_html(repo: Path = REPO, eval_dir: Path | None = None) -> tuple[str, list[str]]:
    """The finished HTML and the figure files it uses."""
    book = Book(repo)
    src, ts = load_state(repo, eval_dir)
    template = (repo / TEMPLATE).read_text(encoding="utf-8")
    f = facts(src, ts, book)
    out = render(template, f, book, load_references(repo))
    return out, sorted(set(IMG_RE.findall(out)))


def figure_source(repo: Path, name: str) -> Path:
    for d in FIG_SOURCES:
        p = repo / d / name
        if p.exists():
            return p
    raise ReportError(f"figure {name} is in neither {' nor '.join(FIG_SOURCES)}")


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


# ----------------------------------------------------------------- PDF

def print_pdf(html_path: Path, pdf_path: Path) -> None:
    """Print the HTML with headless Chromium (Playwright); one browser, closed after use."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ReportError("Playwright is not installed in this Python; run the PDF step with the system Python "
                          "(python scripts\\build_report.py)") from e
    tmp = pdf_path.with_name(pdf_path.name + ".tmp")
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-gpu", "--disable-extensions", "--mute-audio"])
        try:
            page = browser.new_page()
            page.goto(html_path.resolve().as_uri(), wait_until="load")
            page.evaluate("document.fonts.ready.then(() => true)")
            page.emulate_media(media="print")
            page.pdf(path=str(tmp), format="A4", print_background=True, prefer_css_page_size=True,
                     display_header_footer=True, header_template="<span></span>", footer_template=FOOTER,
                     outline=True, tagged=True)
        finally:
            browser.close()
    try:
        import logging
        from pypdf import PdfWriter
        logging.getLogger("pypdf").setLevel(logging.ERROR)     # Chromium's trailer /Size is one short; harmless
        w = PdfWriter(clone_from=str(tmp))
        w.add_metadata({"/Title": "Gunj: dating Indian recordings from mains hum against an open grid-frequency record",
                        "/Author": "Shrey Sati",
                        "/Subject": "ENF dating of Indian recordings: method and pre-registered evaluation",
                        "/Keywords": "electric network frequency; audio forensics; timestamp verification; "
                                     "pre-registration; India"})
        with open(tmp, "wb") as fh:
            w.write(fh)
    except ImportError:
        print("note: pypdf is not installed; the PDF keeps Chromium's metadata", file=sys.stderr)
    os.replace(tmp, pdf_path)


def check_pdf(pdf_path: Path) -> list[str]:
    """Page count, embedded fonts, size and text. Returns the problems found (empty if none)."""
    problems, info = [], []
    size = pdf_path.stat().st_size
    info.append(f"{size / 1024 / 1024:.2f} MB")
    if size > MAX_PDF_BYTES:
        problems.append(f"the PDF is {size / 1024 / 1024:.2f} MB, over {MAX_PDF_BYTES // 1024 // 1024} MB")
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz  # older PyMuPDF
        except ImportError:
            fitz = None
    if fitz is None:
        print("note: PyMuPDF is not installed; page count, fonts and text of the PDF were not checked", file=sys.stderr)
        return problems
    doc = fitz.open(str(pdf_path))
    try:
        pages = doc.page_count
        info.append(f"{pages} pages")
        if not PAGES_OK[0] <= pages <= PAGES_OK[1]:
            problems.append(f"{pages} pages, outside {PAGES_OK[0]} to {PAGES_OK[1]}")
        fonts, not_embedded = set(), set()
        text, sizes = [], {}
        for page in doc:
            for fnt in page.get_fonts(full=True):
                fonts.add(fnt[3])
                if fnt[1] in ("n/a", "") and fnt[2] != "Type3":
                    not_embedded.add(fnt[3])
            text.append(page.get_text())
            for blk in page.get_text("dict")["blocks"]:
                for line in blk.get("lines", []):
                    for span in line["spans"]:
                        key = round(span["size"], 1)
                        sizes[key] = sizes.get(key, 0) + len(span["text"])
        info.append(f"fonts: {', '.join(sorted(fonts))}")
        if not_embedded:
            problems.append("fonts not embedded: " + ", ".join(sorted(not_embedded)))
        # Chromium shrinks every page when something is wider than the page; the body size shows it
        body = max(sizes, key=sizes.get) if sizes else 0
        info.append(f"body text {body} pt")
        if body < BODY_PT_MIN:
            problems.append(f"the body text prints at {body} pt, below {BODY_PT_MIN} pt: something is wider than "
                            f"the page and Chromium shrank the layout")
        alltext = "\n".join(text)
        if "{{" in alltext or fr.ABS_PATH_RE.search(alltext):
            problems.append("a placeholder or a local path in the PDF text")
    finally:
        doc.close()
    print("pdf: " + "; ".join(info))
    return problems


# ----------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Build docs/report/report.html and docs/gunj-report.pdf from the results")
    ap.add_argument("--html-only", action="store_true", help="write the HTML and figures; skip the PDF")
    ap.add_argument("--check", action="store_true", help="write nothing; exit 1 if report.html or a figure is out of date")
    ap.add_argument("--eval-dir", default=None, help="folder with the scoring outputs (default results/; for tests)")
    return ap


def main(argv=None, repo: Path = REPO) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(repo)
    eval_dir = Path(args.eval_dir) if args.eval_dir else None
    try:
        out, figs = build_html(repo, eval_dir)
        sources = {name: figure_source(repo, name) for name in figs}
    except ReportError as e:
        print(f"build_report: {e}. Nothing was written.", file=sys.stderr)
        return 2
    html_path = repo / HTML_OUT
    data = out.encode("utf-8")
    stale = []
    if not html_path.exists() or html_path.read_bytes() != data:
        stale.append(HTML_OUT)
    for name, p in sources.items():
        dst = repo / FIG_DIR / name
        if not dst.exists() or dst.read_bytes() != p.read_bytes():
            stale.append(f"{FIG_DIR}/{name}")
    unused = sorted(q for q in (repo / FIG_DIR).glob("*.png") if q.name not in sources) if (repo / FIG_DIR).is_dir() else []
    if args.check:
        stale += [f"{FIG_DIR}/{q.name} (no longer used)" for q in unused]
        if stale:
            print("build_report: out of date: " + ", ".join(stale), file=sys.stderr)
            return 1
        print("build_report: report.html and its figures are up to date")
        return 0
    for name, p in sources.items():
        dst = repo / FIG_DIR / name
        if f"{FIG_DIR}/{name}" in stale:
            write_atomic(dst, p.read_bytes())
    for q in unused:                     # copies this script made for figures the report no longer shows
        q.unlink()
    if HTML_OUT in stale:
        write_atomic(html_path, data)
    print(f"build_report: {HTML_OUT} {'written' if HTML_OUT in stale else 'unchanged'}; "
          f"{len(sources)} figures in {FIG_DIR}")
    if args.html_only:
        return 0
    try:
        print_pdf(html_path, repo / PDF_OUT)
    except ReportError as e:
        print(f"build_report: {e}", file=sys.stderr)
        return 2
    problems = check_pdf(repo / PDF_OUT)
    if problems:
        print("build_report: the PDF fails its checks: " + "; ".join(problems), file=sys.stderr)
        return 3
    print(f"build_report: {PDF_OUT} written and checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
