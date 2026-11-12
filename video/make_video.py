"""Build the Gunj v1 demo video (silent, captions burned in) and re-capture docs/screens.

    python video/make_video.py frames     capture the explorer frame by frame (Playwright)
    python video/make_video.py assemble   crossfades, captions from video/NARRATION.md, ffmpeg encode
    python video/make_video.py screens    re-capture the explorer screenshots in docs/screens
    python video/make_video.py all        frames, then assemble
    python video/make_video.py check      name any narrated cut or narration file older than video/NARRATION.md

Needs a Python with playwright (and its Chromium) and Pillow, and ffmpeg on PATH. The script
serves site/ itself on a free local port. Everything the recording changes on the page is
injected from here, and no site file is edited: channel names are hidden, the drift section is
moved above the uniqueness section to follow the narration, and a highlight box is drawn. Every
number on screen is the explorer's own, read from site/data. Line start times come from
video/NARRATION.md, so the pictures, the captions and the narration share one timeline. Inside a
line, a caption starts when its first word is spoken in the recorded narration, as measured in
video/caption_onsets.json; without that file, captions are timed by word count. Work files go to
a folder in the system temp directory.
"""
import functools
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parent.parent
SITE = REPO / "site"
VIDEO = REPO / "video"
SCREENS = REPO / "docs" / "screens"
NARRATION = VIDEO / "NARRATION.md"
ONSETS = VIDEO / "caption_onsets.json"
SILENT = VIDEO / "gunj-v1-silent.mp4"
WORK = Path(tempfile.gettempdir()) / "gunj-v1-video-build"

FPS = 30
VIEW = {"width": 1280, "height": 720}  # CSS px; x1.5 gives 1920x1080 frames
DPR = 1.5
LOCK_ID = "5LW0lKI1jx8"  # Test stream, MATCH within 30 s of T* on its clip page (60 min, full span)
NV_ID = "eEJdGpEayes"  # Test stream, no hum flag, no verdict
REPO_URL = "https://github.com/satanrayshe/gunj"


# ------------------------------------------------------------------ narration and captions

def parse_narration():
    text = NARRATION.read_text(encoding="utf-8")
    lines = [(int(m.group(1)) * 60 + int(m.group(2)), m.group(3).strip())
             for m in re.finditer(r"^\s*\d+\.\s+\*\*(\d+):(\d{2})\*\*\s+(.+?)\s*$", text, re.M)]
    m = re.search(r"video ends at \*\*(\d+):(\d{2})\*\*", text, re.I)
    if not lines or not m:
        raise SystemExit("NARRATION.md: no timed lines or no end time found")
    return lines, int(m.group(1)) * 60 + int(m.group(2))


def _split_long(s, maxc=100):
    if len(s) <= maxc:
        return [s]
    for sep in ("; ", ", ", ": "):
        idx = [m.start() for m in re.finditer(re.escape(sep), s)]
        if idx:
            i = min(idx, key=lambda k: abs(k - len(s) / 2))
            return _split_long(s[:i + 1], maxc) + _split_long(s[i + len(sep):], maxc)
    return [s]


def measured_onsets():
    """{(line, chunk): (start s, text)} from video/caption_onsets.json, or {} if there is none.

    The file records when the first word of each caption after a line's first is spoken in the
    recorded narration. Captions then follow the voice instead of an even word-count pace."""
    if not ONSETS.exists():
        return {}
    data = json.loads(ONSETS.read_text(encoding="utf-8"))
    lead = float(data.get("lead_in_s", 0.0))
    return {(c["line"], c["chunk"]): (float(c["voice_s"]) - lead, c["text"]) for c in data["chunks"]}


def caption_chunks():
    """Per narration line, a list of (start s, end s, text), split at sentences.

    Timed by word count, except where video/caption_onsets.json gives the measured start of a caption."""
    lines, end = parse_narration()
    onsets = measured_onsets()
    out = []
    for i, (t0, txt) in enumerate(lines):
        t1 = (lines[i + 1][0] if i + 1 < len(lines) else end) - 0.25
        pieces = []
        for s in re.split(r"(?<=[.?!])\s+", txt):
            pieces += _split_long(s)
        merged = []
        for p in pieces:
            if merged and len(merged[-1]) + 1 + len(p) <= 64:
                merged[-1] += " " + p
            else:
                merged.append(p)
        words = [len(p.split()) for p in merged]
        starts, t = [], t0
        for w in words:
            starts.append(t)
            t += (t1 - t0) * w / sum(words)
        for k in range(1, len(merged)):
            got = onsets.get((i + 1, k + 1))
            if got is None:
                continue
            if got[1] != merged[k]:
                raise SystemExit(f"{ONSETS.name}: line {i + 1} caption {k + 1} text differs from NARRATION.md; "
                                 "measure the onsets again or delete the file")
            starts[k] = got[0]
        for k in range(1, len(starts)):
            if not starts[k - 1] + 0.6 <= starts[k] <= t1 - 0.6:
                raise SystemExit(f"line {i + 1} caption {k + 1}: start {starts[k]:.2f} s is out of order")
        out.append([(s, (starts[k + 1] if k + 1 < len(starts) else t1), p) for k, (s, p) in enumerate(zip(starts, merged))])
    return out, end


def ease(x):
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


# ------------------------------------------------------------------ local server and page tweaks

class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve_site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(SITE)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/" % srv.server_address[1]


def channel_names():
    clips = json.loads((SITE / "data" / "clips.json").read_text(encoding="utf-8"))["clips"]
    ov = json.loads((SITE / "data" / "overview.json").read_text(encoding="utf-8"))
    names = {c.get("channel") or "" for c in clips}
    names.add((ov.get("hero") or {}).get("channel") or "")
    return sorted(n for n in names if n.strip())


INIT_JS = r"""
(() => {
  // requestAnimationFrame under manual control, so the slide-and-lock can be stepped frame by frame
  const nRAF = window.requestAnimationFrame.bind(window);
  let manual = false, q = [], vt = 0;
  window.requestAnimationFrame = (cb) => { if (manual) { q.push(cb); return q.length; } return nRAF(cb); };
  window.__manual = (on) => { manual = on; if (on) vt = performance.now(); else { const qq = q; q = []; qq.forEach((cb) => nRAF(cb)); } };
  window.__tick = (dt) => { vt += dt; const qq = q; q = []; qq.forEach((cb) => cb(vt)); return q.length; };

  // hide channel names
  const NAMES = (__NAMES__).map((s) => s.trim()).filter((s) => s.length >= 4).sort((a, b) => b.length - a.length);
  const fix = (s) => { let o = s; NAMES.forEach((n) => { o = o.split(n + ", ").join("").split(n).join(""); }); return o; };
  const scrubNode = (n) => { const s = n.nodeValue; if (s && s.length >= 4) { const o = fix(s); if (o !== s) n.nodeValue = o; } };
  const scrub = (root) => {
    if (root.nodeType === 3) { scrubNode(root); return; }
    if (root.nodeType !== 1) return;
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT); let n;
    while ((n = w.nextNode())) scrubNode(n);
  };
  new MutationObserver((ms) => ms.forEach((m) => {
    if (m.type === "characterData") scrubNode(m.target); else m.addedNodes.forEach(scrub);
  })).observe(document, { childList: true, subtree: true, characterData: true });

  const css = `h1:focus, h1:focus-visible, main:focus { outline: none !important; }
    html { scrollbar-width: none; } ::-webkit-scrollbar { display: none; }
    #__spot { position: fixed; pointer-events: none; z-index: 60; border-radius: 4px; display: none;
      border: 2px solid rgba(240, 162, 58, 0.95); box-shadow: 0 0 0 200vmax rgba(5, 7, 9, 0.64); }`;
  const addCss = () => { const s = document.createElement("style"); s.textContent = css; (document.head || document.documentElement).appendChild(s); };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", addCss); else addCss();

  // highlight box
  window.__spot = (r, op) => {
    let e = document.getElementById("__spot");
    if (!e) { e = document.createElement("div"); e.id = "__spot"; document.body.appendChild(e); }
    if (!r || op <= 0) { e.style.display = "none"; return; }
    e.style.display = "block"; e.style.opacity = String(Math.min(1, op));
    e.style.left = r.x + "px"; e.style.top = r.y + "px"; e.style.width = r.w + "px"; e.style.height = r.h + "px";
  };
  const findDt = (scope, t) => Array.from(scope.querySelectorAll("dt")).find((d) => d.textContent.trim() === t);
  window.__els = (p) => {
    const scope = p.within ? document.querySelector(p.within) : document;
    if (p.dt) { const a = findDt(scope, p.dt), b = findDt(scope, p.dtTo || p.dt); return [a, a.nextElementSibling, b, b.nextElementSibling]; }
    let els = Array.from(scope.querySelectorAll(p.css));
    if (p.text) els = els.filter((e) => e.textContent.trim() === p.text);
    if (!p.all) els = els.slice(0, 1);
    if (p.closest) els = els.map((e) => e.closest(p.closest));
    if (p.cells) els = els.flatMap((e) => Array.from(e.children).slice(p.cells[0], p.cells[1] + 1));
    return els;
  };
  window.__hdr = () => { const h = document.querySelector(".bar"); return h ? h.getBoundingClientRect().bottom : 0; };
  window.__docTop = (p) => window.__els(p)[0].getBoundingClientRect().top + window.scrollY;
  window.__rect = (parts, pad) => {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    parts.forEach((p) => window.__els(p).forEach((e) => {
      if (!e) throw new Error("missing element for " + JSON.stringify(p));
      const r = e.getBoundingClientRect();
      x0 = Math.min(x0, r.left); y0 = Math.min(y0, r.top); x1 = Math.max(x1, r.right); y1 = Math.max(y1, r.bottom);
    }));
    const r = { x: x0 - pad, y: y0 - pad, w: x1 - x0 + 2 * pad, h: y1 - y0 + 2 * pad };
    const top = window.__hdr() + 4, bot = window.innerHeight - 4;
    if (r.y < top) { r.h -= top - r.y; r.y = top; }
    if (r.y + r.h > bot) r.h = bot - r.y;
    return r;
  };

  // overview for the video: the drift section above the uniqueness section, as the narration runs
  window.__prepOverview = () => {
    const d = document.querySelector('section[aria-labelledby="d-h"]'), u = document.querySelector('section[aria-labelledby="u-h"]');
    u.parentNode.insertBefore(d, u);
  };
})();
"""


def init_js():
    return INIT_JS.replace("__NAMES__", json.dumps(channel_names()))


def endcard_html(base):
    return f"""<!doctype html><html lang="en" data-theme="dark"><head><meta charset="utf-8">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<link rel="stylesheet" href="{base}style.css">
<style>
  body {{ margin: 0; }}
  .end {{ height: 100vh; box-sizing: border-box; padding: 0 0 150px 140px; display: flex; flex-direction: column; justify-content: center; }}
  .end .brand {{ gap: 30px; }}
  .end .brand-mark {{ transform: scale(2.4); transform-origin: left center; margin-right: 26px; }}
  .end .brand-name {{ font-size: 64px; }}
  .end .tag {{ color: var(--muted); font-size: 22px; margin: 8px 0 36px; }}
  .end .url {{ font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: 30px; color: var(--trace); }}
  .end .who {{ font-size: 24px; margin-top: 14px; }}
  .end .fine {{ color: var(--muted); font-size: 14px; margin-top: 42px; line-height: 1.75; max-width: 980px; }}
</style></head><body>
<div class="end">
  <div class="brand"><span class="brand-mark"></span><span class="brand-name">Gunj</span></div>
  <div class="tag">Dating Indian recordings from mains hum</div>
  <div class="url">{REPO_URL}</div>
  <div class="who">Shrey Sati, Threws Odyssey 2026</div>
  <div class="fine">Grid data: Mukherjee, Kalita and Kumar (2024), PMU-Frequency Data at 1-second Interval, Mendeley Data bvwh9d6227 v4, CC BY 4.0.<br>
  No audio or video from any stream is used in this video. Simulated results are labelled SYNTHETIC.</div>
</div></body></html>"""


# ------------------------------------------------------------------ frame capture

class Rec:
    """Screenshots with a frame count each; [None, n] marks an n-frame crossfade into the next shot."""

    def __init__(self, page, out):
        self.page, self.out = page, out
        self.items, self.t, self.n, self.spot = [], 0, 0, None

    def js(self, expr, arg=None):
        return self.page.evaluate(expr, arg)

    def snap(self, frames=1):
        self.n += 1
        name = "f%05d.png" % self.n
        self.page.screenshot(path=str(self.out / name))
        self.items.append([name, frames])
        self.t += frames

    def hold_until(self, sec):
        n = round(sec * FPS) - self.t
        if n < 0:
            raise SystemExit("timeline overrun: wanted %.2f s, already at %.2f s" % (sec, self.t / FPS))
        if n:
            self.snap(n)

    def xfade(self, frames=12):
        self.items.append([None, frames])

    def rect(self, parts, pad=8):
        return self.js("([p, pad]) => __rect(p, pad)", [parts, pad])

    def set_spot(self, r, op):
        self.js("([r, o]) => __spot(r, o)", [r, op])

    def spot_to(self, r, dur=0.33):
        n = max(1, round(dur * FPS))
        for i in range(1, n + 1):
            e = ease(i / n)
            if self.spot is None:
                self.set_spot(r, e)
            else:
                self.set_spot({k: self.spot[k] + (r[k] - self.spot[k]) * e for k in r}, 1)
            self.snap(1)
        self.spot = r

    def unspot(self, dur=0.27):
        if self.spot is None:
            return
        n = max(1, round(dur * FPS))
        for i in range(1, n + 1):
            self.set_spot(self.spot, 1 - ease(i / n))
            self.snap(1)
        self.set_spot(None, 0)
        self.spot = None

    def top(self, part, gap=16):
        return self.js("(p) => __docTop(p)", part) - self.js("__hdr()") - gap

    def scroll_to(self, y, dur):
        y0 = self.js("window.scrollY")
        n = max(1, round(dur * FPS))
        for i in range(1, n + 1):
            self.js("(y) => window.scrollTo(0, y)", y0 + (y - y0) * ease(i / n))
            self.snap(1)

    def jump(self, y):
        self.js("(y) => window.scrollTo(0, y)", y)
        self.page.wait_for_timeout(250)

    def goto(self, hash_, ready):
        self.set_spot(None, 0)
        self.spot = None
        self.js("(h) => { location.hash = h; }", hash_)
        self.page.wait_for_selector(ready)
        self.page.wait_for_timeout(1200)
        self.js("document.activeElement && document.activeElement.blur()")


def step_frames():
    from playwright.sync_api import sync_playwright

    chunks, end = caption_chunks()
    C = [c[0][0] for c in chunks]  # start of each narration line, s

    def ch(line, k):  # start of caption chunk k of narration line `line` (both 1-based), s
        return chunks[line - 1][k - 1][0]

    frames = WORK / "frames"
    shutil.rmtree(frames, ignore_errors=True)
    frames.mkdir(parents=True)
    srv, url = serve_site()
    TEST = "#test-panel"                       # the Test results panel of the overview
    TABLE = TEST + " table.res-tbl"            # its primary-cell table (v2 is the first row)
    TRACE = {"css": 'svg[aria-label^="Trace of"]', "closest": ".panel"}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--disable-gpu", "--disable-extensions", "--mute-audio"])
            ctx = browser.new_context(viewport=VIEW, device_scale_factor=DPR, color_scheme="dark", reduced_motion="no-preference")
            ctx.add_init_script(init_js())
            page = ctx.new_page()
            page.goto(url + "index.html#/", wait_until="networkidle")
            page.wait_for_selector(".lockcard")
            page.wait_for_timeout(3000)  # the hero's own slide-and-lock plays once on load
            page.evaluate("__prepOverview()")
            page.evaluate("__manual(true); document.querySelector('.lockcard .btn').click();")  # back to the start state
            page.wait_for_timeout(400)
            R = Rec(page, frames)

            # 1. title, the hero waiting in its start state
            R.hold_until(C[1])
            # 2. the hum: the recording's trace against the reference
            R.scroll_to(R.top({"css": "#hero-h"}), 1.2)
            R.spot_to(R.rect([{"css": ".hero-plots > .panel:nth-child(2)"}], 6))
            R.hold_until(C[2] - 0.6)
            R.unspot()
            R.hold_until(C[2] + 0.7)
            # 3. slide and lock, stepped at 2x the site's 1.2 s
            for _ in range(400):
                left = R.js("__tick(1200 / 72)")
                R.snap(1)
                if not left:
                    break
            R.js("__manual(false)")
            R.hold_until(C[3])
            # 4. the lock card, then the Calibration badge
            R.spot_to(R.rect([{"css": ".lockcard .lc-sec:nth-child(1)"}, {"css": ".lockcard .lc-sec:nth-child(2)"}]))
            R.hold_until(ch(4, 2))
            R.spot_to(R.rect([{"css": "#hero-h"}, {"css": 'section[aria-labelledby="hero-h"] .section-head .badge'}]), 0.4)
            R.hold_until(C[4] - 0.5)
            R.unspot()
            R.hold_until(C[4])
            # 5. the Test panel: blind, pre-registered; 126 of 150 streams processed
            R.scroll_to(R.top({"css": "#res-h"}), 1.5)
            R.spot_to(R.rect([{"css": TEST + " .panel-h"}, {"css": TEST + " .lead-note"}]))
            R.hold_until(ch(5, 2))
            R.spot_to(R.rect([{"dt": "streams processed", "within": TEST}]), 0.4)
            R.hold_until(C[5])
            # 6. ninety with an hour of audio; the primary endpoint; then the confident verdicts by distance
            R.spot_to(R.rect([{"css": TABLE + " caption"}]), 0.4)
            R.hold_until(ch(6, 2))
            R.spot_to(R.rect([{"css": TABLE + " thead tr", "cells": [0, 1]}, {"css": TABLE + " tbody tr", "cells": [0, 1]}]), 0.4)
            R.hold_until(ch(6, 3) - 1.3)
            R.unspot()
            R.scroll_to(R.top({"css": "#breakdown"}, 150), 0.8)
            R.spot_to(R.rect([{"css": "#bd-confident"}]), 0.33)
            R.hold_until(ch(6, 4) + 2.6)
            R.spot_to(R.rect([{"css": "#bd-confident"}, {"css": "#breakdown > .figure-note"}]), 0.4)
            R.hold_until(C[6] - 0.4)
            R.unspot()
            R.hold_until(C[6])
            # 7. the reference's label drift, then locks with and without the correction
            R.scroll_to(R.top({"css": "#d-h"}), 1.5)
            R.spot_to(R.rect([{"css": 'section[aria-labelledby="d-h"] .panel svg'}], 6))
            R.hold_until(ch(7, 2))
            R.spot_to(R.rect([{"css": "#drift-callout"}], 6), 0.4)
            R.hold_until(C[7] - 0.4)
            R.unspot()
            R.hold_until(C[7])
            # 8. uniqueness curve (SYNTHETIC noise), then its reading at the measured noise level
            R.scroll_to(R.top({"css": "#u-panel"}, 70), 1.2)
            R.spot_to(R.rect([{"css": "#u-panel svg"}, {"css": "#u-panel .controls"}], 6))
            R.hold_until(ch(8, 2))
            R.spot_to(R.rect([{"css": "#u-panel .u-summary"}, {"css": "#u-panel table.res-tbl"}], 6), 0.4)
            R.hold_until(C[8])
            # 9. a blind Test lock
            R.xfade(12)
            R.goto("#/clip/test/" + LOCK_ID, 'svg[aria-label^="Trace of"]')
            R.jump(R.top({"css": ".verdict-line"}, 20))
            R.snap(1)
            R.spot_to(R.rect([{"css": ".verdict-line .big"}, {"dt": "public start", "dtTo": "label drift D", "within": "main"}]))
            R.hold_until(ch(9, 2) - 0.3)
            R.unspot()
            R.scroll_to(R.top(TRACE), 1.2)
            R.hold_until(C[9])
            # 10. no hum, no verdict; then the false-match rows
            R.xfade(12)
            R.goto("#/clip/test/" + NV_ID, 'svg[aria-label^="Trace of"]')
            R.jump(R.top({"css": ".verdict-line"}, 20))
            R.snap(1)
            R.spot_to(R.rect([{"css": ".verdict-line > *", "all": True}]))
            R.hold_until(ch(10, 2) - 1.0)
            R.unspot()
            hum = {"css": "h3", "text": "Hum", "closest": ".panel"}
            R.scroll_to(R.js("(p) => __docTop(p)", hum) - 330, 1.2)
            R.spot_to(R.rect([{"css": "h3", "text": "Hum", "closest": ".panel-h"},
                              {"css": 'svg[aria-label^="Hum, this excerpt"]'}], 8))
            R.hold_until(ch(10, 3) + 0.4)  # the crossfade needs a shot of at least 12 frames before it
            R.xfade(12)
            R.goto("#/", "#m3-panel table.fm-tbl")
            R.jump(R.top({"css": "#m3-panel"}))
            r = R.rect([{"css": "#m3-panel table.fm-tbl caption"}, {"css": "#fm-real"}, {"css": "#fm-syn"}, {"css": "#fm-target"}])
            R.set_spot(r, 1)
            R.spot = r
            R.snap(1)
            R.hold_until(C[10])
            # 11. end card
            R.xfade(15)
            R.spot = None
            page.set_content(endcard_html(url), wait_until="networkidle")
            page.evaluate("document.fonts.ready.then(() => true)")
            page.wait_for_timeout(500)
            R.snap(1)
            R.hold_until(end)
            browser.close()
    finally:
        srv.shutdown()
    (WORK / "timeline.json").write_text(json.dumps(R.items), encoding="utf-8")
    print("frames: %d screenshots, %d video frames (%.2f s)" % (R.n, R.t, R.t / FPS))


# ------------------------------------------------------------------ assembly

def caption_font(fdir):
    """IBM Plex Sans (the site's font) for libass, else Segoe UI. Returns the family name."""
    fdir.mkdir(parents=True, exist_ok=True)
    have = sorted(fdir.glob("*.ttf"))
    if not have:
        try:
            req = Request("https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@500", headers={"User-Agent": "Wget/1.21"})
            css = urlopen(req, timeout=20).read().decode("utf-8")
            src = re.findall(r"url\((https://[^)]+\.ttf)\)", css)[0]
            (fdir / "IBMPlexSans-Medium.ttf").write_bytes(urlopen(src, timeout=30).read())
        except Exception as e:  # offline: fall back to a Windows font
            print("caption font download failed (%s); using Segoe UI" % e)
            win = Path(os.environ.get("WINDIR", "")) / "Fonts" / "segoeui.ttf"
            if win.is_file():
                shutil.copy(win, fdir / "segoeui.ttf")
        have = sorted(fdir.glob("*.ttf"))
    if not have:
        return "Arial"
    return _family_name(have[0])


def _family_name(path):
    """Font family (name ID 1, Windows platform) from a TrueType name table; libass matches on it."""
    data = path.read_bytes()
    for i in range(struct.unpack(">H", data[4:6])[0]):
        tag, _, off, _ = struct.unpack(">4sIII", data[12 + 16 * i:28 + 16 * i])
        if tag != b"name":
            continue
        count, strings = struct.unpack(">HH", data[off + 2:off + 6])
        for j in range(count):
            pid, _, _, nid, ln, o = struct.unpack(">6H", data[off + 6 + 12 * j:off + 18 + 12 * j])
            if pid == 3 and nid == 1:
                return data[off + strings + o:off + strings + o + ln].decode("utf-16-be")
    return path.stem


def ass_time(t):
    cs = int(round(t * 100))
    return "%d:%02d:%02d.%02d" % (cs // 360000, cs // 6000 % 60, cs // 100 % 60, cs % 100)


def write_captions(path, family):
    chunks, _ = caption_chunks()
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{family},42,&H00F2F0EE,&H000000FF,&H24101214,&H24101214,0,0,0,0,100,100,0,0,3,14,0,2,250,250,46,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    for line in chunks:
        for i, (t0, t1, txt) in enumerate(line):
            fade = "{\\fad(%d,%d)}" % (150 if i == 0 else 0, 150 if i == len(line) - 1 else 0)
            ev.append("Dialogue: 0,%s,%s,Cap,,0,0,0,,%s%s" % (ass_time(t0), ass_time(t1), fade, txt))
    path.write_text(head + "\n".join(ev) + "\n", encoding="utf-8")


def step_assemble():
    from PIL import Image

    frames = WORK / "frames"
    items = json.loads((WORK / "timeline.json").read_text(encoding="utf-8"))
    seq = []
    for i, (name, n) in enumerate(items):
        if name is not None:
            seq.append([name, n])
            continue
        prev, nxt = seq[-1], items[i + 1][0]
        if prev[1] <= n:
            raise SystemExit("crossfade longer than the shot before it")
        prev[1] -= n
        a = Image.open(frames / prev[0]).convert("RGB")
        b = Image.open(frames / nxt).convert("RGB")
        for j in range(n):
            fn = "x%03d_%02d.png" % (i, j)
            Image.blend(a, b, ease((j + 1) / (n + 1))).save(frames / fn, compress_level=1)
            seq.append([fn, 1])
    total = sum(n for _, n in seq)
    with open(frames / "list.ffconcat", "w", encoding="utf-8") as f:
        f.write("ffconcat version 1.0\n")
        for name, n in seq:
            f.write("file '%s'\nduration %.6f\n" % (name, n / FPS))
        f.write("file '%s'\n" % seq[-1][0])
    family = caption_font(WORK / "fonts")
    write_captions(frames / "captions.ass", family)
    print("caption font:", family, "| frames:", total, "(%.2f s)" % (total / FPS))
    vf = ("fps=%d,subtitles=captions.ass:fontsdir=../fonts,"
          "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p") % FPS
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostats",
           "-f", "concat", "-safe", "0", "-i", "list.ffconcat", "-vf", vf, "-frames:v", str(total), "-r", str(FPS),
           "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
           "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
           "-threads", "2", "-filter_threads", "1", "-an", "-movflags", "+faststart", "out.mp4"]
    subprocess.run(cmd, cwd=frames, check=True)
    shutil.move(str(frames / "out.mp4"), str(SILENT))
    print("wrote", SILENT.relative_to(REPO))
    warn_stale_narration()


def warn_stale_narration():
    """A narrated cut or a narration file older than the narration text (or than this silent cut) speaks old
    lines under new captions. Name them, so that they are not uploaded by mistake; nothing is deleted."""
    newest = max(NARRATION.stat().st_mtime, SILENT.stat().st_mtime if SILENT.exists() else 0)
    old = [p for p in [VIDEO / "gunj-v1.mp4"] + sorted(VIDEO.glob("narration*.wav")) + sorted(VIDEO.glob("narration*.m4a"))
           if p.exists() and p.stat().st_mtime < (NARRATION.stat().st_mtime if p.suffix != ".mp4" else newest)]
    for p in old:
        print("WARNING: %s is older than %s; it speaks an earlier narration. Do not upload it. Make the narration "
              "again from video/NARRATION.md and run video/mux.ps1." % (p.relative_to(REPO).as_posix(),
                                                                      "the narration text" if p.suffix != ".mp4" else
                                                                      "the narration text or the silent cut"))


# ------------------------------------------------------------------ docs screenshots

def step_screens():
    from playwright.sync_api import sync_playwright

    shots = [("overview", "#/", ".lockcard", 1440, 900), ("overview", "#/", ".lockcard", 390, 844),
             ("clip-" + LOCK_ID, "#/clip/test/" + LOCK_ID, 'svg[aria-label^="Trace of"]', 1440, 900),
             ("clip-" + LOCK_ID, "#/clip/test/" + LOCK_ID, 'svg[aria-label^="Trace of"]', 390, 844)]
    srv, url = serve_site()
    try:
        for name, hash_, ready, w, h in shots:
            with sync_playwright() as p:  # one browser per capture, closed after it
                browser = p.chromium.launch(args=["--disable-gpu", "--disable-extensions", "--mute-audio"])
                ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1, color_scheme="dark")
                ctx.add_init_script(init_js())
                page = ctx.new_page()
                page.goto(url + "index.html" + hash_, wait_until="networkidle")
                page.wait_for_selector(ready)
                page.wait_for_timeout(3000)
                page.evaluate("document.activeElement && document.activeElement.blur()")
                out = SCREENS / ("%s-%dx%d.png" % (name, w, h))
                page.screenshot(path=str(out))
                browser.close()
                print("wrote", out.relative_to(REPO))
    finally:
        srv.shutdown()


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    if step in ("frames", "all"):
        step_frames()
    if step in ("assemble", "all"):
        step_assemble()
    if step == "screens":
        step_screens()
    if step == "check":
        warn_stale_narration()
    if step not in ("frames", "assemble", "all", "screens", "check"):
        raise SystemExit(__doc__)
